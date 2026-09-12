"""Daily News worker observability, Part A (design/DECISIONS.md) — a
strictly read-only diagnostic for previewing how a single registered
feed's CURRENT entries would be classified by the real discovery
pipeline, without ever running that pipeline or writing anything.

Two functions, two different guarantees:

- `classify_feed_entries()` is genuinely pure: data in, classifications
  out. It has no HTTP, repository, store, backend, pipeline, database,
  file, or write-capable dependency of any kind — its only imports are
  `canonical_url` and `dedup`, both pure gate/normalize functions. It
  mirrors daily_news_pipeline.run_discovery()'s own gate order exactly
  (title -> canonical URL -> already-seen-by-id -> duplicate-by-title)
  so its classifications are a faithful preview, never a guess — proven
  by tests/test_daily_news_diagnostics.py's own parity test against a
  real, isolated run_discovery() call for the same fixtures.

- `diagnose_source_duplicates()` is the one-feed wrapper: it requires an
  explicit, already-registered `source_id` (never guesses or constructs
  a URL — raises ValueError for an unknown one), makes EXACTLY ONE
  ordinary `requests.get` to that source's own already-approved feed
  URL (via the existing, unmodified rss_atom_client.fetch_entries() —
  no retry, no second validation request, no article-page fetch, no
  redirect-behavior change), and returns at most 20 classifications.
  This function is storage-read-only and network-active — it is NOT
  universally non-mutating the way classify_feed_entries() is: it talks
  to the external source over the network (unavoidable to see the
  feed's current entries), but it never instantiates a repository, a DB
  connection, a store, a backend, or a pipeline, and it never writes
  anything locally or to production. It requires the caller to already
  have loaded `stories` (e.g. via `repository.load_stories()`) and pass
  them in — this module never loads or saves a store itself.

This module deliberately does NOT import daily_news_store,
daily_news_backend, or daily_news_pipeline — even though
daily_news_pipeline.run_discovery() is itself safe to read, importing it
here would make this module's own "never calls a write-capable path"
guarantee depend on trusting that module's internals never change to add
one. Duplicating the tiny, stable `_story_id()` computation locally
instead keeps this module's own write-incapability independently
verifiable from its import list alone — this mirrors the codebase's own
established convention of duplicating small private helpers across a
module boundary rather than importing them (see e.g.
recently_updated.py's own _LANGUAGE_CODE_BY_ORIGINAL_LANGUAGE comment).
`_story_id()` here must stay byte-for-byte identical to daily_news_
pipeline.py's own private function of the same name — verified by this
module's own parity test, not by import."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from src.data_access.daily_news import canonical_url, dedup, rss_atom_client
from src.data_access.daily_news.feed_registry import PILOT_FEEDS
from src.data_access.daily_news.rss_atom_client import RawFeedEntry
from src.models.daily_news_models import NewsStory

MAX_DIAGNOSTIC_ENTRIES = 20


@dataclass(frozen=True)
class FeedEntryClassification:
    source_id: str
    feed_url: str
    title: str
    link: str
    normalized_title: str
    classification: str  # "already_seen" | "would_deduplicate" | "would_publish" | "suppressed_no_title" | "suppressed_no_url"
    matched_story_title: str | None = None
    matched_story_url: str | None = None


def _story_id(company_name: str, canonical_link: str) -> str:
    """Byte-for-byte identical to daily_news_pipeline._story_id() —
    duplicated, not imported (see this module's own docstring for why).
    Verified identical by tests/test_daily_news_diagnostics.py's parity
    test."""
    digest = hashlib.sha256(f"{company_name}|{canonical_link}".encode("utf-8")).hexdigest()[:16]
    slug = company_name.lower().replace(" ", "-").replace(".", "")
    return f"newsitem-{slug}-{digest}"


def classify_feed_entries(
    source_id: str,
    feed_url: str,
    company_name: str,
    canonical_domains: tuple[str, ...],
    entries: tuple[RawFeedEntry, ...],
    stories: dict[str, NewsStory],
) -> tuple[FeedEntryClassification, ...]:
    """Pure function: takes already-fetched entries and an already-loaded
    stories dict, returns classifications. Never fetches anything, never
    loads or saves a store, never imports anything write-capable. Mirrors
    run_discovery()'s own gate order exactly, so it is a faithful
    preview of what a real discovery run would decide for these same
    entries — but only ever decides, never acts."""
    existing_headlines = [(s.company_name, s.headline) for s in stories.values()]
    results: list[FeedEntryClassification] = []

    for entry in entries:
        if not entry.title:
            results.append(FeedEntryClassification(
                source_id=source_id, feed_url=feed_url, title="(no title)", link=entry.link or "",
                normalized_title="", classification="suppressed_no_title",
            ))
            continue

        normalized_title = dedup.normalize_title_unicode(entry.title)

        if not canonical_url.validate_canonical_url(entry.link, canonical_domains, feed_url):
            results.append(FeedEntryClassification(
                source_id=source_id, feed_url=feed_url, title=entry.title, link=entry.link or "",
                normalized_title=normalized_title, classification="suppressed_no_url",
            ))
            continue

        story_id = _story_id(company_name, entry.link)
        if story_id in stories:
            results.append(FeedEntryClassification(
                source_id=source_id, feed_url=feed_url, title=entry.title, link=entry.link,
                normalized_title=normalized_title, classification="already_seen",
            ))
            continue

        match = next(
            (
                s for s in stories.values()
                if s.company_name == company_name and dedup.normalize_title_unicode(s.headline) == normalized_title
            ),
            None,
        )
        if match is not None:
            results.append(FeedEntryClassification(
                source_id=source_id, feed_url=feed_url, title=entry.title, link=entry.link,
                normalized_title=normalized_title, classification="would_deduplicate",
                matched_story_title=match.headline,
                matched_story_url=match.sources[0].url if match.sources else None,
            ))
            continue

        results.append(FeedEntryClassification(
            source_id=source_id, feed_url=feed_url, title=entry.title, link=entry.link,
            normalized_title=normalized_title, classification="would_publish",
        ))

    return tuple(results)


def diagnose_source_duplicates(source_id: str, stories: dict[str, NewsStory]) -> tuple[FeedEntryClassification, ...]:
    """Storage-read-only, network-active — NOT universally non-mutating.
    Makes exactly one `requests.get` (via the existing, unmodified
    rss_atom_client.fetch_entries()) to the exact already-registered feed
    URL for `source_id` — no retry, no second request of any kind, no
    write anywhere. Raises ValueError for an unknown source_id rather
    than guessing a URL. Returns at most MAX_DIAGNOSTIC_ENTRIES (20)
    classifications; an empty tuple if the fetch itself fails."""
    feed_source = next((f for f in PILOT_FEEDS if f.source_id == source_id), None)
    if feed_source is None:
        raise ValueError(f"No registered Daily News feed with source_id={source_id!r}.")

    fetch_result = rss_atom_client.fetch_entries(feed_source.feed_url)
    if fetch_result.failure_code is not None:
        return ()

    return classify_feed_entries(
        source_id=source_id, feed_url=feed_source.feed_url, company_name=feed_source.company_name,
        canonical_domains=feed_source.canonical_domains, entries=fetch_result.entries[:MAX_DIAGNOSTIC_ENTRIES],
        stories=stories,
    )
