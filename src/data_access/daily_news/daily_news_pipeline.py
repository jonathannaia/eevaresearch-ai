"""Daily News Slice 1's one orchestration entry point — connects
feed_registry (which official feeds to poll), rss_atom_client
(fetch+parse), canonical_url (the hard suppression gate),
summary_grounding (grounded/fallback/original-preserved summary), and
dedup (cross-source duplicate detection) into a single bounded
discovery run. Manual/on-demand only this slice — see
scripts/run_daily_news_discovery.py — no autonomous scheduling lives
here or anywhere else in this slice.

Zero imports from src.data_access.dart/edgar/edinet and zero reads/
writes of any Radar store — the only shared touch point is
tracked_companies.py's own read-only company registry, via
feed_registry.tracked_company_for().
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from src.data_access.daily_news import canonical_url, daily_news_store, dedup, rss_atom_client
from src.data_access.daily_news.feed_registry import DailyNewsFeedSource, PILOT_FEEDS, tracked_company_for
from src.data_access.daily_news.summary_grounding import generate_summary
from src.models.daily_news_models import (
    NewsSourceReference,
    NewsStateTransition,
    NewsStory,
    NewsStoryStatus,
    SourceClass,
)

if TYPE_CHECKING:  # avoids a hard import-time dependency on daily_news_backend.py's own connection-acquisition chain
    from src.data_access.daily_news.daily_news_backend import DailyNewsRepositoryProtocol


@dataclass(frozen=True)
class DailyNewsScanReport:
    """A structured, JSON-serializable-shaped summary of one discovery
    run — safe strings only, never a raw exception or secret. The
    public page never reads this; only the hidden admin page does."""

    scan_id: str
    started_at: str
    completed_at: str
    sources_polled: int
    items_discovered: int  # raw feed entries seen, across every source, before any gate
    items_suppressed_no_url: int
    items_deduplicated: int
    stories_published: int  # newly persisted this run
    source_failures: dict[str, str]  # company_name -> sanitized failure_code
    suppressed_items: tuple[tuple[str, str, str], ...]  # (company_name, title, reason) — admin view only
    warnings: tuple[str, ...]


def _story_id(company_name: str, canonical_link: str) -> str:
    digest = hashlib.sha256(f"{company_name}|{canonical_link}".encode("utf-8")).hexdigest()[:16]
    slug = company_name.lower().replace(" ", "-").replace(".", "")
    return f"newsitem-{slug}-{digest}"


def _transition(story: NewsStory, status: NewsStoryStatus, detail: str = "") -> NewsStory:
    story.status = status
    story.state_history.append(NewsStateTransition(status=status, at=datetime.now(timezone.utc).isoformat(), detail=detail))
    return story


def run_discovery(
    cache_dir: Path,
    feed_sources: tuple[DailyNewsFeedSource, ...] = PILOT_FEEDS,
    daily_news_repository: DailyNewsRepositoryProtocol | None = None,
) -> DailyNewsScanReport:
    """One bounded discovery run across every configured pilot feed.
    Never loops, never sleeps, never retries on its own — a single pass,
    same "one explicit call, one bounded amount of work" discipline as
    radar_pipeline.run_pipeline. One source's fetch failure is isolated
    (recorded in source_failures) and never blocks the others.

    `daily_news_repository` (Daily News durability workstream) is
    additive and optional, mirroring radar_pipeline.run_pipeline's own
    `candidate_repository` seam exactly. Omitted (every existing caller
    this workstream — scripts/run_daily_news_discovery.py's own default
    invocation and any test that doesn't pass one), every store touch
    below is exactly today's JSON behavior via daily_news_store.py.
    Supplied, every store touch in this one call routes through the
    given collaborator instead — see
    src.data_access.daily_news.daily_news_backend.get_daily_news_repository."""
    scan_id = f"daily-news-scan-{uuid.uuid4().hex[:12]}"
    started_at = datetime.now(timezone.utc).isoformat()

    if daily_news_repository is None:
        store = daily_news_store.load_stories(cache_dir)
    else:
        store = daily_news_repository.load_stories()
    existing_headlines = [(s.company_name, s.headline) for s in store.values()]

    items_discovered = 0
    suppressed_items: list[tuple[str, str, str]] = []
    items_deduplicated = 0
    newly_published: list[NewsStory] = []
    source_failures: dict[str, str] = {}
    warnings: list[str] = []

    for source in feed_sources:
        company = tracked_company_for(source.company_name)
        if company is None:
            warnings.append(f"{source.company_name}: not found in tracked_companies.py — source skipped.")
            continue

        fetch_result = rss_atom_client.fetch_entries(source.feed_url)
        if fetch_result.failure_code is not None:
            source_failures[source.company_name] = fetch_result.failure_code
            continue

        for entry in fetch_result.entries:
            items_discovered += 1
            if not entry.title:
                suppressed_items.append((source.company_name, "(no title)", "Missing title"))
                continue

            if not canonical_url.validate_canonical_url(entry.link, source.canonical_domains, source.feed_url):
                suppressed_items.append((source.company_name, entry.title, "No valid canonical source URL"))
                continue

            story_id = _story_id(source.company_name, entry.link)
            if story_id in store:
                continue  # already-seen item on a prior run — idempotent no-op

            if dedup.is_duplicate_title(existing_headlines, source.company_name, entry.title):
                items_deduplicated += 1
                suppressed_items.append((source.company_name, entry.title, "Duplicate of an existing story"))
                continue

            summary_result = generate_summary(entry.title, entry.summary, has_valid_source_url=True)
            retrieved_at = datetime.now(timezone.utc).isoformat()

            image_url = None
            image_alt = None
            if canonical_url.validate_image_url(entry.image_url, source.image_host):
                image_url = entry.image_url
                image_alt = entry.image_alt or entry.title

            source_reference = NewsSourceReference(
                publisher=source.company_name, source_class=SourceClass.OFFICIAL_COMPANY, url=entry.link,
                title=entry.title, published_at=entry.published_at or retrieved_at, retrieved_at=retrieved_at,
                original_language="Non-Latin script" if summary_result.translation_unavailable else "English",
                excerpt_original=entry.summary,
                image_url=image_url, image_alt=image_alt,
            )

            story = NewsStory(
                id=story_id, company_name=source.company_name, ticker=company.krx_code,
                theme_slug=company.themes[0] if company.themes else "",
                headline=entry.title, eeva_summary=summary_result.eeva_summary,
                is_fallback_summary=summary_result.is_fallback,
                translation_unavailable=summary_result.translation_unavailable,
                original_title=summary_result.original_title,
                sources=(source_reference,), status=NewsStoryStatus.DISCOVERED, state_history=[],
            )
            story = _transition(story, NewsStoryStatus.PUBLISHED, "Discovered from official company feed.")

            newly_published.append(story)
            existing_headlines.append((source.company_name, entry.title))

    if newly_published:
        if daily_news_repository is None:
            daily_news_store.upsert_new_stories(cache_dir, newly_published)
        else:
            daily_news_repository.upsert_new_stories(newly_published)

    completed_at = datetime.now(timezone.utc).isoformat()
    return DailyNewsScanReport(
        scan_id=scan_id, started_at=started_at, completed_at=completed_at, sources_polled=len(feed_sources),
        items_discovered=items_discovered, items_suppressed_no_url=sum(1 for *_, reason in suppressed_items if reason == "No valid canonical source URL"),
        items_deduplicated=items_deduplicated, stories_published=len(newly_published),
        source_failures=source_failures, suppressed_items=tuple(suppressed_items), warnings=tuple(warnings),
    )
