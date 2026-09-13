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
  (title -> canonical URL -> already-seen-by-id -> duplicate-by-title
  against the persisted store -> duplicate-by-title within this same
  batch -> missing publication timestamp -> unparsable publication
  timestamp -> stale (older than the 7x24h freshness window) ->
  newest-first-with-original-index-tiebreak selection of at most 5
  per source) so its classifications are a faithful preview, never a
  guess — proven by tests/test_daily_news_diagnostics.py's own parity
  test against a real, isolated run_discovery() call for the same
  fixtures.

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
module's own parity test, not by import. The same duplication-over-
import choice applies to the issuer-ingestion freshness/cap policy
helpers below (`_FRESHNESS_WINDOW_SECONDS`, `_PER_SOURCE_CAP`,
`_parse_utc_datetime`, `_is_fresh`): byte-for-byte identical to daily_
news_pipeline.py's own private definitions of the same names, duplicated
rather than imported for the same reason — this module's own "never
calls a write-capable path" guarantee must stay independently verifiable
from its import list alone, not from trusting daily_news_pipeline.py's
internals never change to add one."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from src.data_access.daily_news import canonical_url, dedup, rss_atom_client
from src.data_access.daily_news.feed_registry import PILOT_FEEDS
from src.data_access.daily_news.rss_atom_client import RawFeedEntry
from src.models.daily_news_models import NewsStory

MAX_DIAGNOSTIC_ENTRIES = 20

# Issuer-ingestion freshness/cap policy — byte-for-byte identical to
# daily_news_pipeline.py's own private definitions of the same names
# (see this module's own docstring for why these are duplicated rather
# than imported). Verified identical by this module's own parity test.
_FRESHNESS_WINDOW_SECONDS = 7 * 24 * 3600
_PER_SOURCE_CAP = 5


def _parse_utc_datetime(published_at: str) -> datetime | None:
    """Byte-for-byte identical to daily_news_pipeline._parse_utc_datetime()."""
    try:
        dt = datetime.fromisoformat(published_at)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _is_fresh(published_dt: datetime, now: datetime) -> bool:
    """Byte-for-byte identical to daily_news_pipeline._is_fresh() —
    inclusive boundary, same `<=` convention."""
    return (now - published_dt).total_seconds() <= _FRESHNESS_WINDOW_SECONDS


@dataclass(frozen=True)
class FeedEntryClassification:
    source_id: str
    feed_url: str
    title: str
    link: str
    normalized_title: str
    # "already_seen" | "would_deduplicate" | "would_publish" |
    # "would_be_capped" | "suppressed_no_title" | "suppressed_no_url" |
    # "suppressed_missing_published_at" | "suppressed_invalid_published_at" |
    # "suppressed_stale"
    classification: str
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
    run_discovery()'s own gate order exactly (see this module's own
    docstring), so it is a faithful preview of what a real discovery run
    would decide for these same entries — but only ever decides, never
    acts.

    Two-phase, exactly like run_discovery(): entries that survive every
    suppression gate (including freshness) are first collected as
    provisional "would_publish" candidates, in the SAME source-local,
    call-local `queued_titles_this_batch` scope run_discovery() uses for
    `queued_headlines_this_source` — so a later entry in this same batch
    that duplicates an earlier QUALIFYING one is still caught as
    would_deduplicate even if that earlier entry is itself later capped
    out, exactly mirroring run_discovery()'s own timing. Only once every
    entry has been classified are the qualifying candidates sorted
    newest-first (original feed index as the tie-breaker) and the ones
    beyond the first `_PER_SOURCE_CAP` are downgraded from would_publish
    to would_be_capped. A capped-out candidate never mutates `stories`
    or any cross-call state — this function loads nothing and persists
    nothing, so there is no cross-call dedupe state for it to poison in
    the first place; the in-batch mirroring above is the only place
    order/timing could otherwise diverge from run_discovery()."""
    existing_headlines = [(s.company_name, s.headline) for s in stories.values()]
    queued_titles_this_batch: list[tuple[str, str]] = []
    queued_entries_this_batch: list[RawFeedEntry] = []
    now = datetime.now(timezone.utc)

    results: list[FeedEntryClassification | None] = [None] * len(entries)
    qualifying: list[tuple[int, datetime]] = []  # (index into results/entries, published_dt)

    for index, entry in enumerate(entries):
        if not entry.title:
            results[index] = FeedEntryClassification(
                source_id=source_id, feed_url=feed_url, title="(no title)", link=entry.link or "",
                normalized_title="", classification="suppressed_no_title",
            )
            continue

        normalized_title = dedup.normalize_title_unicode(entry.title)

        if not canonical_url.validate_canonical_url(entry.link, canonical_domains, feed_url):
            results[index] = FeedEntryClassification(
                source_id=source_id, feed_url=feed_url, title=entry.title, link=entry.link or "",
                normalized_title=normalized_title, classification="suppressed_no_url",
            )
            continue

        story_id = _story_id(company_name, entry.link)
        if story_id in stories:
            results[index] = FeedEntryClassification(
                source_id=source_id, feed_url=feed_url, title=entry.title, link=entry.link,
                normalized_title=normalized_title, classification="already_seen",
            )
            continue

        match = next(
            (
                s for s in stories.values()
                if s.company_name == company_name and dedup.normalize_title_unicode(s.headline) == normalized_title
            ),
            None,
        )
        if match is not None:
            results[index] = FeedEntryClassification(
                source_id=source_id, feed_url=feed_url, title=entry.title, link=entry.link,
                normalized_title=normalized_title, classification="would_deduplicate",
                matched_story_title=match.headline,
                matched_story_url=match.sources[0].url if match.sources else None,
            )
            continue

        if dedup.is_duplicate_title(queued_titles_this_batch, company_name, entry.title):
            # Matches an earlier QUALIFYING entry in this same batch, not
            # a persisted story — matched_story_title/url here describe
            # that earlier batch entry (same normalized title, by
            # definition), not a store record. Reusing these two fields
            # rather than adding new ones: both describe "the thing this
            # would duplicate against," and adding a third, rarely-
            # distinguishable field for this one case isn't worth the
            # schema churn.
            matched_entry = next(
                e for e in queued_entries_this_batch
                if dedup.normalize_title_unicode(e.title) == normalized_title
            )
            results[index] = FeedEntryClassification(
                source_id=source_id, feed_url=feed_url, title=entry.title, link=entry.link,
                normalized_title=normalized_title, classification="would_deduplicate",
                matched_story_title=matched_entry.title, matched_story_url=matched_entry.link,
            )
            continue

        if not entry.published_at:
            results[index] = FeedEntryClassification(
                source_id=source_id, feed_url=feed_url, title=entry.title, link=entry.link,
                normalized_title=normalized_title, classification="suppressed_missing_published_at",
            )
            continue
        published_dt = _parse_utc_datetime(entry.published_at)
        if published_dt is None:
            results[index] = FeedEntryClassification(
                source_id=source_id, feed_url=feed_url, title=entry.title, link=entry.link,
                normalized_title=normalized_title, classification="suppressed_invalid_published_at",
            )
            continue
        if not _is_fresh(published_dt, now):
            results[index] = FeedEntryClassification(
                source_id=source_id, feed_url=feed_url, title=entry.title, link=entry.link,
                normalized_title=normalized_title, classification="suppressed_stale",
            )
            continue

        queued_titles_this_batch.append((company_name, entry.title))
        queued_entries_this_batch.append(entry)
        # Provisional — downgraded to would_be_capped below if it doesn't
        # make the newest-`_PER_SOURCE_CAP` cut.
        results[index] = FeedEntryClassification(
            source_id=source_id, feed_url=feed_url, title=entry.title, link=entry.link,
            normalized_title=normalized_title, classification="would_publish",
        )
        qualifying.append((index, published_dt))

    # Same single explicit sort key as run_discovery(): newest
    # published_dt first, original feed index as the deterministic
    # tie-breaker for an exact timestamp match.
    qualifying.sort(key=lambda pair: (-pair[1].timestamp(), pair[0]))
    for index, _published_dt in qualifying[_PER_SOURCE_CAP:]:
        capped_result = results[index]
        results[index] = FeedEntryClassification(
            source_id=capped_result.source_id, feed_url=capped_result.feed_url, title=capped_result.title,
            link=capped_result.link, normalized_title=capped_result.normalized_title, classification="would_be_capped",
        )

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
