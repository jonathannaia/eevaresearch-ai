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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from src.data_access.daily_news import canonical_url, daily_news_store, dedup, localization_dedup, rss_atom_client
from src.data_access.daily_news.feed_registry import DailyNewsFeedSource, PILOT_FEEDS, tracked_company_for
from src.data_access.daily_news.materiality_classification import classify_issuer_story
from src.data_access.daily_news.summary_grounding import generate_summary
from src.data_access.translation import translation_service
from src.models.daily_news_models import (
    NewsSourceReference,
    NewsStateTransition,
    NewsStory,
    NewsStoryStatus,
    SourceClass,
)

if TYPE_CHECKING:  # avoids a hard import-time dependency on daily_news_backend.py's own connection-acquisition chain
    from src.data_access.daily_news.daily_news_backend import DailyNewsRepositoryProtocol
    from src.data_access.translation.interfaces import TranslationProvider

# Dashboard/Signals quality fix (design/DASHBOARD_SIGNAL_QUALITY_FIX_
# DESIGN.md) — DeepL source-language codes for every language a real
# Daily News source declares today (source_registry.DailyNewsSourceEntry.
# language). "English" is deliberately absent: it is never the text
# actually sent to the translation provider (see
# _evaluate_localization_match's own docstring — only the non-English
# side of a candidate/existing pair is ever translated). Kept as this
# module's own small, local copy rather than importing recently_updated.
# py's private constant — matches this codebase's own established
# precedent (see that module's own docstring on why it keeps its own
# copy rather than reaching across a module boundary for a private
# symbol).
_LANGUAGE_CODE_BY_NAME: dict[str, str] = {"Korean": "KO", "Japanese": "JA", "French": "FR"}


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
    # Observability fix (design/DECISIONS.md, Daily News operational-fix
    # workstream): items whose deterministic story_id (see _story_id)
    # already existed in the store — the idempotency path itself
    # (dedup.py's own docstring: "re-running discovery naturally upserts
    # the same id rather than creating a duplicate"), previously a silent
    # `continue` with no counter and no suppressed_items entry at all.
    # Distinct from items_deduplicated, which counts a DIFFERENT
    # already-published story with a matching normalized title (dedup.
    # is_duplicate_title) — this field counts the SAME story rediscovered
    # by its own id, the ordinary, expected steady-state outcome once a
    # feed's current items have already been published on an earlier run.
    items_already_seen: int
    # Issuer-ingestion freshness/cap policy (design/DECISIONS.md) — all
    # four report-only, never persisted (DailyNewsScanReport itself is
    # never written to any store; only daily_news_admin.py reads it
    # transiently). items_missing_published_at and items_invalid_
    # published_at are deliberately separate counters, not folded
    # together: an empty entry.published_at (the feed genuinely didn't
    # supply one) and a non-empty-but-unparsable one are different
    # failure modes worth distinguishing in a report, even though both
    # are handled the same way (suppressed, fail-closed).
    items_stale: int
    items_missing_published_at: int
    items_invalid_published_at: int
    items_capped: int
    stories_published: int  # newly persisted this run
    source_failures: dict[str, str]  # company_name -> sanitized failure_code
    suppressed_items: tuple[tuple[str, str, str], ...]  # (company_name, title, reason) — admin view only
    warnings: tuple[str, ...]
    # Daily News worker observability, Part A (design/DECISIONS.md) —
    # every source's own raw FeedFetchResult (entries excluded from this
    # dict's practical use, only duration_ms/http_status/failure_code
    # matter to the caller — but the whole result is kept, not just those
    # fields, so nothing here needs to change shape if a future
    # observability field is added to FeedFetchResult itself), keyed by
    # DailyNewsFeedSource.source_id. Purely additive: populated
    # unconditionally alongside the existing per-entry loop below, never
    # read by any gate/dedup/cap decision in this function.
    fetch_results: dict[str, "rss_atom_client.FeedFetchResult"] = field(default_factory=dict)


def _story_id(company_name: str, canonical_link: str) -> str:
    digest = hashlib.sha256(f"{company_name}|{canonical_link}".encode("utf-8")).hexdigest()[:16]
    slug = company_name.lower().replace(" ", "-").replace(".", "")
    return f"newsitem-{slug}-{digest}"


# Issuer-ingestion freshness/cap policy (design/DECISIONS.md) — a uniform
# gate applied identically to every issuer source, existing and future;
# no source_id/company/category/jurisdiction branch anywhere near this.
# Deliberately a fixed second-count (7 * 24 * 3600), not a "calendar day"
# boundary — mirrors editorial_pipeline.py's own _is_fresh() convention
# exactly (elapsed-seconds-since-published, inclusive boundary), the
# only precedent this codebase has for a freshness window. This module
# never imports from editorial_pipeline.py (each pipeline stays fully
# independent, same discipline this file's own docstring already
# establishes for dart/edgar/edinet) — the helpers below are a
# deliberate, separate re-implementation, not a shared import.
_FRESHNESS_WINDOW_SECONDS = 7 * 24 * 3600
_PER_SOURCE_CAP = 5


def _parse_utc_datetime(published_at: str) -> datetime | None:
    """Returns None for anything that isn't a real, parseable timestamp
    — the caller is responsible for distinguishing an empty string
    (items_missing_published_at) from a non-empty-but-unparsable one
    (items_invalid_published_at) before calling this, since both look
    identical from inside this function. Defensive tzinfo-attachment
    only (rss_atom_client._parse_published_at() always attaches
    tzinfo=timezone.utc when it produces a value at all — real input is
    never naive) — mirrors editorial_pipeline._is_fresh()'s own
    defensive handling exactly."""
    try:
        dt = datetime.fromisoformat(published_at)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _is_fresh(published_dt: datetime, now: datetime) -> bool:
    """Inclusive boundary — an entry exactly 7*24*3600 seconds old is
    fresh, matching editorial_pipeline._is_fresh()'s own `<=` convention.
    A future-dated entry (negative elapsed time) is also fresh — never
    specifically rejected, since nothing in the approved policy asks for
    that check."""
    return (now - published_dt).total_seconds() <= _FRESHNESS_WINDOW_SECONDS


def _transition(story: NewsStory, status: NewsStoryStatus, detail: str = "") -> NewsStory:
    story.status = status
    story.state_history.append(NewsStateTransition(status=status, at=datetime.now(timezone.utc).isoformat(), detail=detail))
    return story


@dataclass(frozen=True)
class _IssuerCandidate:
    """One source's own in-run candidate awaiting the per-source cap —
    never persisted, never touches existing_headlines. `original_index`
    is this entry's position in `fetch_result.entries` as returned by
    the fetch, captured purely as a deterministic tie-breaker for
    candidates that share an identical published_dt (see the explicit
    sort key in run_discovery() below) — independent of feed-serving
    order guarantees, which this codebase makes none of."""

    entry: "rss_atom_client.RawFeedEntry"
    story_id: str
    published_dt: datetime
    original_index: int


def _find_localizable_existing_story(
    store: dict[str, NewsStory], company_name: str, candidate_language: str, published_dt: datetime,
) -> NewsStory | None:
    """Structural pre-filter only (same company, first-party
    OFFICIAL_COMPANY, a different declared language, publication within
    localization_dedup.MAX_PUBLISH_GAP_SECONDS) — returns the first
    already-persisted story that could plausibly be a cross-language
    localized duplicate, before any translation is ever attempted, so a
    candidate/company pair that cannot possibly match never spends a
    translation call. Real title-similarity is decided later, only for
    a match this function returns, by localization_dedup.is_localized_duplicate."""
    for story in store.values():
        if story.company_name != company_name or not story.sources:
            continue
        existing_ref = story.sources[0]
        if existing_ref.source_class != SourceClass.OFFICIAL_COMPANY:
            continue
        if existing_ref.original_language == candidate_language:
            continue
        existing_dt = _parse_utc_datetime(existing_ref.published_at)
        if existing_dt is None:
            continue
        if abs((published_dt - existing_dt).total_seconds()) > localization_dedup.MAX_PUBLISH_GAP_SECONDS:
            continue
        return story
    return None


@dataclass(frozen=True)
class _LocalizationMatch:
    existing_story: NewsStory
    # True when the CANDIDATE (the newly-evaluated side) is the
    # English/global side and existing_story is the non-English side —
    # the "reverse discovery order" case, where the candidate is
    # preferred and the older story becomes the now-superseded
    # alternate, rather than the candidate itself being suppressed.
    candidate_is_preferred: bool


def _translate_and_compare(
    translation_provider: "TranslationProvider | None",
    cache_dir: Path,
    company_name: str, candidate_language: str, candidate_title: str, published_dt: datetime,
    existing_story: NewsStory,
) -> _LocalizationMatch | None:
    """Translation-assisted core of the localized-duplicate check, given
    an ALREADY-FOUND candidate existing_story (see
    _find_localizable_existing_story for discovery-time lookup, and
    select_canonical_stories for the read-time pairwise variant — both
    call this same function so the translate-and-compare logic itself
    never diverges between the two call sites). Returns None whenever no
    translation was attempted or available — "insufficient confidence"
    always means keeping both items, never comparing raw text in two
    different languages directly. Only ever translates the ONE
    non-English title in the pair (whichever side that is), reusing the
    exact same cached, generic translation_service.
    translate_cached_with_outcome() every other translation call site in
    this app already uses — never a new provider, never a new cache."""
    if translation_provider is None:
        return None
    existing_ref = existing_story.sources[0]
    existing_dt = _parse_utc_datetime(existing_ref.published_at)
    if existing_dt is None:
        return None

    if candidate_language == "English":
        non_english_title, non_english_lang = existing_story.headline, existing_ref.original_language
    else:
        non_english_title, non_english_lang = candidate_title, candidate_language
    lang_code = _LANGUAGE_CODE_BY_NAME.get(non_english_lang)
    if lang_code is None:
        return None

    attempt = translation_service.translate_cached_with_outcome(
        translation_provider, document_id=f"localization-dedup:{company_name}:{non_english_lang}",
        text=non_english_title, cache_dir=cache_dir, source_lang=lang_code,
    )
    if attempt.translation is None:
        return None
    translated = attempt.translation.translated_text

    candidate_translated_title = translated if candidate_language != "English" else candidate_title
    existing_title_for_compare = existing_story.headline if candidate_language != "English" else translated

    is_duplicate = localization_dedup.is_localized_duplicate(
        candidate_company=company_name, candidate_source_class=SourceClass.OFFICIAL_COMPANY,
        candidate_language=candidate_language, candidate_published_at_epoch=published_dt.timestamp(),
        candidate_translated_title=candidate_translated_title,
        existing_company=existing_story.company_name, existing_source_class=existing_ref.source_class,
        existing_language=existing_ref.original_language, existing_published_at_epoch=existing_dt.timestamp(),
        existing_title=existing_title_for_compare,
    )
    if not is_duplicate:
        return None
    candidate_is_preferred = candidate_language == "English" and existing_ref.original_language != "English"
    return _LocalizationMatch(existing_story=existing_story, candidate_is_preferred=candidate_is_preferred)


def _evaluate_localization_match(
    translation_provider: "TranslationProvider | None",
    cache_dir: Path,
    company_name: str, candidate_language: str, candidate_title: str, published_dt: datetime,
    store: dict[str, NewsStory],
) -> _LocalizationMatch | None:
    """Discovery-time convenience wrapper: finds a plausible existing
    match via the cheap, translation-free structural pre-filter
    (_find_localizable_existing_story), then delegates to
    _translate_and_compare only for that one candidate — never
    translates anything for a company/store with no plausible match at
    all."""
    if translation_provider is None:
        return None
    existing_story = _find_localizable_existing_story(store, company_name, candidate_language, published_dt)
    if existing_story is None:
        return None
    return _translate_and_compare(
        translation_provider, cache_dir, company_name, candidate_language, candidate_title, published_dt, existing_story,
    )


def _compare_using_cached_translation_only(
    cache_dir: Path,
    company_name: str, candidate_language: str, candidate_title: str, published_dt: datetime,
    existing_story: NewsStory,
) -> _LocalizationMatch | None:
    """Render-time-safe counterpart to _translate_and_compare — same
    gates/similarity logic (delegates to the identical localization_
    dedup.is_localized_duplicate call), but takes NO TranslationProvider
    at all, so it is structurally incapable of making a live translation
    request or any other network call. Reads only translation_service.
    get_cached_translation() — a pure cache lookup, never a write, never
    a call to _translate_with_retry. Returns None (keep both items:
    "insufficient confidence") whenever the one non-English title in the
    pair has no ALREADY-cached translation — a plausible cross-language
    pair discovered/persisted since the last time discovery actually
    translated it simply stays visible as two separate stories until a
    future discovery run (or an explicit user-triggered Translate
    action) populates the cache; render time never populates it itself."""
    existing_ref = existing_story.sources[0]
    existing_dt = _parse_utc_datetime(existing_ref.published_at)
    if existing_dt is None:
        return None

    if candidate_language == "English":
        non_english_title, non_english_lang = existing_story.headline, existing_ref.original_language
    else:
        non_english_title, non_english_lang = candidate_title, candidate_language
    if non_english_lang not in _LANGUAGE_CODE_BY_NAME:
        return None

    cached = translation_service.get_cached_translation(
        document_id=f"localization-dedup:{company_name}:{non_english_lang}",
        text=non_english_title, cache_dir=cache_dir,
    )
    if cached is None:
        return None
    translated = cached.translated_text

    candidate_translated_title = translated if candidate_language != "English" else candidate_title
    existing_title_for_compare = existing_story.headline if candidate_language != "English" else translated

    is_duplicate = localization_dedup.is_localized_duplicate(
        candidate_company=company_name, candidate_source_class=SourceClass.OFFICIAL_COMPANY,
        candidate_language=candidate_language, candidate_published_at_epoch=published_dt.timestamp(),
        candidate_translated_title=candidate_translated_title,
        existing_company=existing_story.company_name, existing_source_class=existing_ref.source_class,
        existing_language=existing_ref.original_language, existing_published_at_epoch=existing_dt.timestamp(),
        existing_title=existing_title_for_compare,
    )
    if not is_duplicate:
        return None
    candidate_is_preferred = candidate_language == "English" and existing_ref.original_language != "English"
    return _LocalizationMatch(existing_story=existing_story, candidate_is_preferred=candidate_is_preferred)


def select_canonical_stories(stories: dict[str, NewsStory], cache_dir: Path) -> dict[str, NewsStory]:
    """Read-time reconciliation (Dashboard/Signals quality fix, design/
    DASHBOARD_SIGNAL_QUALITY_FIX_DESIGN.md): given every currently-
    persisted NewsStory (as returned by a DailyNewsRepositoryProtocol's
    own load_stories()), returns the subset that should actually be
    DISPLAYED — collapsing a cross-language localized-duplicate pair
    (see localization_dedup.py) down to its one preferred story
    (English/global preferred over a non-English alternate, regardless
    of which side was discovered/published first) without ever
    mutating, deleting, or reordering the underlying store: every story
    this function excludes from its return value is still fully
    present, unmodified, and independently retrievable from `stories`
    itself — real traceability by construction, never a separate
    lossy record.

    Call this immediately after repository.load_stories() in any
    surface that renders issuer-lane NewsStory cards (see
    src/ui/pages/daily_news.py and src/ui/components/recently_updated.py)
    — never inside run_discovery() itself, which already makes its own
    write-time decision for the common (English-first) case via
    _evaluate_localization_match and only ever needs this function to
    additionally hide an already-persisted alternate for the reverse-
    order case.

    PURE/READ-ONLY with respect to external services, by construction:
    takes no TranslationProvider parameter at all (there is nothing here
    to pass one to), and its one collaborator,
    _compare_using_cached_translation_only, calls only translation_
    service.get_cached_translation() — a cache-file read, never
    translate_cached_with_outcome() and never anything that can reach a
    TranslationProvider.translate() call or the network. A plausible
    cross-language pair whose one non-English title has no already-
    cached translation is left as two separate, fully visible stories
    ("insufficient confidence" always means keep both) — discovery-time
    comparison/caching (_evaluate_localization_match, inside
    run_discovery) and the explicit, user-triggered Translate action in
    the UI remain the only two places this app ever calls a translation
    provider; this function is neither."""
    if len(stories) < 2:
        return stories
    superseded_ids: set[str] = set()
    story_list = list(stories.values())
    for i, candidate in enumerate(story_list):
        if candidate.id in superseded_ids or not candidate.sources:
            continue
        candidate_ref = candidate.sources[0]
        candidate_dt = _parse_utc_datetime(candidate_ref.published_at)
        if candidate_dt is None:
            continue
        for other in story_list[i + 1:]:
            if other.id in superseded_ids or not other.sources:
                continue
            match = _compare_using_cached_translation_only(
                cache_dir, candidate.company_name, candidate_ref.original_language,
                candidate.headline, candidate_dt, other,
            )
            if match is None:
                continue
            other_ref = other.sources[0]
            if candidate_ref.original_language == "English" and other_ref.original_language != "English":
                superseded_ids.add(other.id)
            elif other_ref.original_language == "English" and candidate_ref.original_language != "English":
                superseded_ids.add(candidate.id)
                break  # candidate itself is superseded; stop comparing it further
            else:
                # Neither side is English (outside this fix's named
                # fixtures) — deterministic tie-break only: keep
                # whichever published first, never an arbitrary pick.
                other_dt = _parse_utc_datetime(other_ref.published_at)
                if other_dt is not None and candidate_dt <= other_dt:
                    superseded_ids.add(other.id)
                else:
                    superseded_ids.add(candidate.id)
                    break
    if not superseded_ids:
        return stories
    return {story_id: story for story_id, story in stories.items() if story_id not in superseded_ids}


def run_discovery(
    cache_dir: Path,
    feed_sources: tuple[DailyNewsFeedSource, ...] = PILOT_FEEDS,
    daily_news_repository: DailyNewsRepositoryProtocol | None = None,
    translation_provider: "TranslationProvider | None" = None,
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
    src.data_access.daily_news.daily_news_backend.get_daily_news_repository.

    `translation_provider` (Dashboard/Signals quality fix, design/
    DASHBOARD_SIGNAL_QUALITY_FIX_DESIGN.md) is additive and optional,
    the same seam shape as EDGAR/DART/EDINET's own translation_provider
    parameter. Omitted, the localized-duplicate check below is a no-op
    for every entry (see _evaluate_localization_match: None provider ->
    None match -> both items always kept) — every existing caller that
    doesn't pass one behaves exactly as before this fix. Supplied, it is
    used ONLY to translate the one non-English title in a plausible
    cross-language duplicate pair before comparing — never for any other
    purpose in this pipeline."""
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
    items_already_seen = 0
    items_stale = 0
    items_missing_published_at = 0
    items_invalid_published_at = 0
    items_capped = 0
    newly_published: list[NewsStory] = []
    source_failures: dict[str, str] = {}
    warnings: list[str] = []
    fetch_results: dict[str, rss_atom_client.FeedFetchResult] = {}
    now = datetime.now(timezone.utc)

    for source in feed_sources:
        company = tracked_company_for(source.company_name)
        if company is None:
            warnings.append(f"{source.company_name}: not found in tracked_companies.py — source skipped.")
            continue

        fetch_result = rss_atom_client.fetch_entries(source.feed_url)
        fetch_results[source.source_id] = fetch_result
        if fetch_result.failure_code is not None:
            source_failures[source.company_name] = fetch_result.failure_code
            continue

        # Issuer-ingestion freshness/cap policy (design/DECISIONS.md) —
        # every entry that survives every existing gate below is
        # collected here first, never constructed or persisted yet.
        # `queued_headlines_this_source` is a deliberately SEPARATE,
        # source-local, run-local duplicate-title scope from the global
        # `existing_headlines` list: it exists only so two entries from
        # THIS source's OWN fetch that share a title are still caught
        # (first-in-feed-order wins, exactly mirroring the existing
        # cross-run duplicate semantics), without writing anything into
        # global state for a candidate that might still be dropped by
        # the cap below. `existing_headlines` itself is only ever
        # appended to once a candidate has survived capping — see the
        # `capped` loop further down, the one and only place either
        # `existing_headlines` or `store`-relevant persistence is
        # touched for this source's entries.
        qualifying: list[_IssuerCandidate] = []
        queued_headlines_this_source: list[tuple[str, str]] = []

        for original_index, entry in enumerate(fetch_result.entries):
            items_discovered += 1
            if not entry.title:
                suppressed_items.append((source.company_name, "(no title)", "Missing title"))
                continue

            if not canonical_url.validate_canonical_url(entry.link, source.canonical_domains, source.feed_url):
                suppressed_items.append((source.company_name, entry.title, "No valid canonical source URL"))
                continue

            story_id = _story_id(source.company_name, entry.link)
            if story_id in store:
                items_already_seen += 1
                continue  # already-seen item on a prior run — idempotent no-op

            if dedup.is_duplicate_title(existing_headlines, source.company_name, entry.title):
                items_deduplicated += 1
                suppressed_items.append((source.company_name, entry.title, "Duplicate of an existing story"))
                continue
            if dedup.is_duplicate_title(queued_headlines_this_source, source.company_name, entry.title):
                items_deduplicated += 1
                suppressed_items.append((source.company_name, entry.title, "Duplicate of an existing story"))
                continue

            if not entry.published_at:
                items_missing_published_at += 1
                suppressed_items.append((source.company_name, entry.title, "Missing publication timestamp"))
                continue
            published_dt = _parse_utc_datetime(entry.published_at)
            if published_dt is None:
                items_invalid_published_at += 1
                suppressed_items.append((source.company_name, entry.title, "Unparsable publication timestamp"))
                continue
            if not _is_fresh(published_dt, now):
                items_stale += 1
                suppressed_items.append((source.company_name, entry.title, "Older than the 7x24h freshness window"))
                continue

            # Dashboard/Signals quality fix (design/
            # DASHBOARD_SIGNAL_QUALITY_FIX_DESIGN.md) — cross-language
            # localized-duplicate check, run only after the existing
            # same-language exact-title dedup above already failed to
            # catch it. `store` is live and run-updated (see the
            # `store[story.id] = story` write below), so this also
            # catches a same-run pair discovered from two different
            # sources, not only a cross-run one. See
            # _evaluate_localization_match's own docstring for exactly
            # why an unavailable/failed translation always means keeping
            # both items, never comparing raw untranslated text.
            localization_match = _evaluate_localization_match(
                translation_provider, cache_dir, source.company_name, source.language, entry.title, published_dt, store,
            )
            if localization_match is not None:
                if localization_match.candidate_is_preferred:
                    # Reverse discovery order: this English/global entry
                    # arrived after a non-English original was already
                    # persisted. Prefer English — the candidate is NOT
                    # suppressed (falls through to admission below); the
                    # older story's own record is left fully intact,
                    # unmutated, in the store (real traceability, not
                    # just a text note) — this only adds a report-level
                    # entry naming it as the now-superseded alternate.
                    superseded = localization_match.existing_story
                    suppressed_items.append((
                        superseded.company_name, superseded.headline,
                        f"Superseded by English/global item {entry.link} ({source.language})",
                    ))
                else:
                    items_deduplicated += 1
                    superseded_ref = localization_match.existing_story.sources[0]
                    suppressed_items.append((
                        source.company_name, entry.title,
                        f"Localized duplicate of {superseded_ref.url} ({superseded_ref.original_language})",
                    ))
                    continue

            queued_headlines_this_source.append((source.company_name, entry.title))
            qualifying.append(_IssuerCandidate(entry=entry, story_id=story_id, published_dt=published_dt, original_index=original_index))

        # Deterministic selection: newest published_dt first; for an
        # exact timestamp tie, the entry that appeared earlier in the
        # feed wins (smallest original_index) — a single explicit sort
        # key, not incidental dict/list ordering.
        qualifying.sort(key=lambda candidate: (-candidate.published_dt.timestamp(), candidate.original_index))
        capped = qualifying[:_PER_SOURCE_CAP]
        items_capped += len(qualifying) - len(capped)

        for candidate in capped:
            entry = candidate.entry
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
                # Dashboard/Signals quality fix (design/
                # DASHBOARD_SIGNAL_QUALITY_FIX_DESIGN.md): the source's
                # own curated, declared language (source_registry.
                # DailyNewsSourceEntry.language, default "English") —
                # replaces the former "Non-Latin script"/"English"
                # heuristic, which could only ever detect CJK/Hangul
                # script and always mislabeled a Latin-script non-English
                # source (e.g. French) as "English". summary_result.
                # translation_unavailable/original_title (the separate,
                # unrelated non-Latin-script summary-extraction fallback)
                # are untouched by this change.
                original_language=source.language,
                excerpt_original=entry.summary,
                image_url=image_url, image_alt=image_alt,
                # Set once, at this exact construction site only — an
                # already-known story_id short-circuits above (`if
                # story_id in store: ... continue`) before this branch is
                # ever reached again for the same story, so this value is
                # structurally write-once. See NewsSourceReference's own
                # docstring for the exact distinction from published_at/
                # retrieved_at.
                first_discovered_at=retrieved_at,
            )

            # Signals materiality classification (design/DECISIONS.md) —
            # computed once, here, at construction time only; never
            # reclassifies an already-persisted story (see
            # NewsMaterialityTier's own docstring). Classification never
            # affects admission/inclusion above — every item that already
            # passed every existing gate is still published exactly as
            # before; this only adds a display-time tier label.
            materiality_tier, materiality_reasons = classify_issuer_story(
                entry.title, entry.summary, SourceClass.OFFICIAL_COMPANY,
            )

            story = NewsStory(
                id=candidate.story_id, company_name=source.company_name, ticker=company.krx_code,
                theme_slug=company.themes[0] if company.themes else "",
                headline=entry.title, eeva_summary=summary_result.eeva_summary,
                is_fallback_summary=summary_result.is_fallback,
                translation_unavailable=summary_result.translation_unavailable,
                original_title=summary_result.original_title,
                sources=(source_reference,), status=NewsStoryStatus.DISCOVERED, state_history=[],
                materiality_tier=materiality_tier, materiality_reasons=materiality_reasons,
            )
            story = _transition(story, NewsStoryStatus.PUBLISHED, "Discovered from official company feed.")

            newly_published.append(story)
            existing_headlines.append((source.company_name, entry.title))
            # Dashboard/Signals quality fix (design/
            # DASHBOARD_SIGNAL_QUALITY_FIX_DESIGN.md): keeps `store` live
            # and run-updated, mirroring existing_headlines' own
            # liveness above — so _evaluate_localization_match can catch
            # a same-run pair discovered from two different sources
            # (e.g. an English source processed before a French one in
            # this same feed_sources list), not only a cross-run one.
            store[story.id] = story

    if newly_published:
        if daily_news_repository is None:
            daily_news_store.upsert_new_stories(cache_dir, newly_published)
        else:
            daily_news_repository.upsert_new_stories(newly_published)

    completed_at = datetime.now(timezone.utc).isoformat()
    return DailyNewsScanReport(
        scan_id=scan_id, started_at=started_at, completed_at=completed_at, sources_polled=len(feed_sources),
        items_discovered=items_discovered, items_suppressed_no_url=sum(1 for *_, reason in suppressed_items if reason == "No valid canonical source URL"),
        items_deduplicated=items_deduplicated, items_already_seen=items_already_seen,
        items_stale=items_stale, items_missing_published_at=items_missing_published_at,
        items_invalid_published_at=items_invalid_published_at, items_capped=items_capped,
        stories_published=len(newly_published),
        source_failures=source_failures, suppressed_items=tuple(suppressed_items), warnings=tuple(warnings),
        fetch_results=fetch_results,
    )
