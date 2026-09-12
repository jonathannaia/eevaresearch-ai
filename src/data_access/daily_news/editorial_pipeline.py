"""Editorial Daily News v1 (design/DECISIONS.md) — one bounded discovery
pass across EDITORIAL_SOURCE_REGISTRY's issuer-agnostic feeds. Manual/
on-demand only, same "no autonomous scheduling lives here" discipline as
daily_news_pipeline.run_discovery() — see
scripts/run_daily_news_discovery.py's own --editorial-only flag.

Reuses, unmodified: rss_atom_client.fetch_entries() (fetch+parse),
canonical_url.validate_canonical_url() (the hard per-item URL gate,
using each entry's own domains — never a different allowlist),
source_registry.normalize_source_url() (URL-normalization for dedup),
dedup.normalize_title_unicode() (title-normalization for dedup).

Zero import of daily_news_pipeline.py, daily_news_store.py, or
NewsStory — this pipeline persists only EditorialStory, via
editorial_story_store.py's own separate cache file. The existing issuer
pipeline, store, and worker are completely untouched by this module.

Freshness (72h) IS applied here (an item older than the window is never
even a persistence candidate), and so is the per-source cap of 5 — see
_PER_SOURCE_CAP below: at most 5 NEW stories may be persisted per feed
in one discovery run, applied after the canonical-URL gate, freshness,
fail-closed matching, and dedup, and before persistence. This is a
per-RUN cap, not a per-source total-ever-stored cap — a feed's own
already-stored items from an earlier run are untouched and can still
accumulate past 5 in the store over multiple runs; see
select_visible_editorial_stories() below, which applies its own
separate, always-current per-source/total DISPLAY cap against the full
store contents for exactly that reason. The overall 20-card total cap
remains display-only (a cross-feed presentation limit, not an ingestion
one)."""
from __future__ import annotations

import hashlib
import html
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from src.data_access.daily_news import canonical_url, dedup, editorial_story_store, rss_atom_client
from src.data_access.daily_news.editorial_matching import matched_companies_and_themes
from src.data_access.daily_news.source_registry import EDITORIAL_SOURCE_REGISTRY, DailyNewsSourceEntry, normalize_source_url
from src.models.daily_news_models import EditorialStory

if TYPE_CHECKING:
    from src.data_access.daily_news.daily_news_backend import EditorialStoryRepositoryProtocol

_MAX_EXCERPT_CHARS = 400
_PER_SOURCE_CAP = 5  # enforced both at persistence time (run_editorial_discovery) and display time (select_visible_editorial_stories)
_TOTAL_DISPLAY_CAP = 20
_FRESHNESS_WINDOW_HOURS = 72

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class EditorialScanReport:
    """Mirrors DailyNewsScanReport's own shape and safe-strings-only
    discipline — never a raw exception, feed content, or credential."""

    scan_id: str
    started_at: str
    completed_at: str
    sources_polled: int
    items_fetched: int  # raw feed entries seen, across every source, before any gate
    items_no_valid_url: int
    items_stale: int
    items_no_match: int  # fail-closed: zero company and zero theme match
    items_duplicate: int
    items_already_seen: int  # idempotency: same story_id already in the store
    items_capped: int  # qualified (passed every gate) but excluded by the 5-per-feed persistence cap this run
    stories_published: int  # newly persisted this run
    source_failures: dict[str, str]  # source_id -> sanitized failure_code


def _story_id(canonical_link: str) -> str:
    digest = hashlib.sha256(normalize_source_url(canonical_link).encode("utf-8")).hexdigest()[:16]
    return f"editorial-{digest}"


def _strip_html(text: str) -> str:
    unescaped = html.unescape(text)
    no_tags = _HTML_TAG_RE.sub(" ", unescaped)
    return _WHITESPACE_RE.sub(" ", no_tags).strip()


def _extractive_excerpt(raw_description: str | None) -> str | None:
    """Mirrors summary_grounding.py's own extractive approach (HTML-strip,
    trim to a sentence boundary, bounded length) — a deliberate, small,
    self-contained copy rather than a cross-module import: that module's
    own generate_summary() has a fallback-sentence/translation-detection
    contract this project's issuer lane needs and Editorial Daily News v1
    explicitly does not (the approved requirement is "omit the excerpt
    entirely" when unusable, never a fallback sentence) — reusing it
    wholesale would risk pulling in behavior that doesn't fit, and this
    module must never touch summary_grounding.py or risk the issuer
    lane's own summary behavior. Returns None (never a fabricated
    sentence) when the description is empty or strips to nothing."""
    if not raw_description:
        return None
    cleaned = _strip_html(raw_description)
    if not cleaned:
        return None
    if len(cleaned) <= _MAX_EXCERPT_CHARS:
        return cleaned
    excerpt = ""
    for sentence in _SENTENCE_END_RE.split(cleaned):
        candidate = f"{excerpt} {sentence}".strip() if excerpt else sentence
        if len(candidate) > _MAX_EXCERPT_CHARS:
            break
        excerpt = candidate
    if not excerpt:
        excerpt = cleaned[:_MAX_EXCERPT_CHARS].rsplit(" ", 1)[0]
    return excerpt.strip() or None


def _is_fresh(published_at: str, now: datetime, window_hours: int = _FRESHNESS_WINDOW_HOURS) -> bool:
    try:
        dt = datetime.fromisoformat(published_at)
    except (ValueError, TypeError):
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds() <= window_hours * 3600


def run_editorial_discovery(
    cache_dir: Path,
    source_entries: tuple[DailyNewsSourceEntry, ...] = EDITORIAL_SOURCE_REGISTRY,
    editorial_repository: "EditorialStoryRepositoryProtocol | None" = None,
) -> EditorialScanReport:
    """One bounded discovery run across every configured editorial feed.
    One source's fetch failure is isolated (recorded in source_failures)
    and never blocks the others — same discipline as
    daily_news_pipeline.run_discovery()."""
    scan_id = f"editorial-scan-{uuid.uuid4().hex[:12]}"
    started_at = datetime.now(timezone.utc).isoformat()
    now = datetime.now(timezone.utc)

    if editorial_repository is None:
        store = editorial_story_store.load_stories(cache_dir)
    else:
        store = editorial_repository.load_stories()

    existing_urls = {normalize_source_url(s.source_url) for s in store.values()}
    existing_title_publisher = {
        (dedup.normalize_title_unicode(s.headline), s.publisher) for s in store.values()
    }

    items_fetched = 0
    items_no_valid_url = 0
    items_stale = 0
    items_no_match = 0
    items_duplicate = 0
    items_already_seen = 0
    items_capped = 0
    newly_published: list[EditorialStory] = []
    source_failures: dict[str, str] = {}

    for source in source_entries:
        fetch_result = rss_atom_client.fetch_entries(source.canonical_url)
        if fetch_result.failure_code is not None:
            source_failures[source.source_id] = fetch_result.failure_code
            continue

        # Collected first, capped after — the 5-per-feed persistence cap
        # (requirement: applied after the URL gate, freshness, matching,
        # and dedup, before persistence) needs every qualifying entry for
        # this source gathered before deciding which 5 (newest) survive.
        qualifying: list[tuple] = []

        for entry in fetch_result.entries:
            items_fetched += 1
            if not entry.title:
                items_no_valid_url += 1
                continue

            if not canonical_url.validate_canonical_url(entry.link, source.domains, source.canonical_url):
                items_no_valid_url += 1
                continue

            if not entry.published_at or not _is_fresh(entry.published_at, now):
                items_stale += 1
                continue

            story_id = _story_id(entry.link)
            if story_id in store:
                items_already_seen += 1
                continue

            normalized_url = normalize_source_url(entry.link)
            normalized_title = dedup.normalize_title_unicode(entry.title)
            if normalized_url in existing_urls or (normalized_title, source.attribution_label) in existing_title_publisher:
                items_duplicate += 1
                continue

            matched_companies, matched_themes = matched_companies_and_themes(entry.title, entry.summary)
            if not matched_companies and not matched_themes:
                items_no_match += 1
                continue

            # Provisionally registered so a second duplicate of THIS SAME
            # entry later in this same source's own feed is still caught
            # — even though this entry might itself be dropped by the cap
            # below, the source's own feed practically never repeats an
            # already-capped item's exact URL/title, so this is a safe,
            # simple, single-pass dedup registration.
            existing_urls.add(normalized_url)
            existing_title_publisher.add((normalized_title, source.attribution_label))
            qualifying.append((entry, story_id, matched_companies, matched_themes))

        qualifying.sort(key=lambda item: item[0].published_at, reverse=True)
        capped = qualifying[:_PER_SOURCE_CAP]
        items_capped += len(qualifying) - len(capped)

        for entry, story_id, matched_companies, matched_themes in capped:
            retrieved_at = datetime.now(timezone.utc).isoformat()
            story = EditorialStory(
                id=story_id, headline=entry.title, publisher=source.attribution_label, source_url=entry.link,
                published_at=entry.published_at, retrieved_at=retrieved_at,
                excerpt=_extractive_excerpt(entry.summary),
                matched_companies=matched_companies, matched_themes=matched_themes,
                source_feed_id=source.source_id,
            )
            newly_published.append(story)

    if newly_published:
        if editorial_repository is None:
            editorial_story_store.upsert_new_stories(cache_dir, newly_published)
        else:
            editorial_repository.upsert_new_stories(newly_published)

    completed_at = datetime.now(timezone.utc).isoformat()
    return EditorialScanReport(
        scan_id=scan_id, started_at=started_at, completed_at=completed_at, sources_polled=len(source_entries),
        items_fetched=items_fetched, items_no_valid_url=items_no_valid_url, items_stale=items_stale,
        items_no_match=items_no_match, items_duplicate=items_duplicate, items_already_seen=items_already_seen,
        items_capped=items_capped, stories_published=len(newly_published), source_failures=source_failures,
    )


def select_visible_editorial_stories(
    stories: dict[str, EditorialStory], now: datetime | None = None,
) -> tuple[EditorialStory, ...]:
    """Pure, display-time windowing — no I/O, no persistence. 72-hour
    freshness (re-checked here, not trusted from ingest time), then a
    per-source cap of 5 (newest first within each source), then an
    overall cap of 20 (newest first across sources) — mirrors
    src/ui/pages/daily_news.py's own existing "filter at render time"
    convention for the issuer lane exactly."""
    now = now or datetime.now(timezone.utc)
    fresh = [s for s in stories.values() if _is_fresh(s.published_at, now)]
    fresh.sort(key=lambda s: s.published_at, reverse=True)

    capped_per_source: list[EditorialStory] = []
    counts: dict[str, int] = {}
    for story in fresh:
        count = counts.get(story.source_feed_id, 0)
        if count >= _PER_SOURCE_CAP:
            continue
        counts[story.source_feed_id] = count + 1
        capped_per_source.append(story)

    capped_per_source.sort(key=lambda s: s.published_at, reverse=True)
    return tuple(capped_per_source[:_TOTAL_DISPLAY_CAP])
