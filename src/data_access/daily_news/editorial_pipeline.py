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
one).

Government / Public Sector Daily News lane (design/DECISIONS.md) — two
specific, hardcoded exceptions to the fail-closed company-or-theme
matching gate below, for exactly `_SPACEFORCE_SOURCE_ID` and
`_NIST_SOURCE_ID`. Deliberately NOT a category-based or generically-
extensible bypass (e.g. no `_ALWAYS_ELIGIBLE_SOURCE_IDS` frozenset) —
every other existing and future editorial source, including any other
entry that happens to use SourceCategory.GOVERNMENT_POLICY, still goes
through the exact same matched_companies_and_themes() gate unchanged.
`_SPACEFORCE_SOURCE_ID` may publish with zero company/theme matches
(every item in that feed is inherently in-scope). `_NIST_SOURCE_ID` may
publish only when `_matches_nist_allow_list()` returns True — a pure,
source-scoped, strict CHIPS/semiconductor allow-list, checked only
against the item's own real title/summary, never a fetched article
body. matched_companies/matched_themes are still computed and stored
for both sources (harmless, usually empty; correctly tags the rare item
that does name a tracked company)."""
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
from src.data_access.daily_news.editorial_admission import assess_admission
from src.data_access.daily_news.editorial_matching import matched_companies_and_themes
from src.data_access.daily_news.materiality_classification import classify_editorial_story
from src.data_access.daily_news.source_registry import EDITORIAL_SOURCE_REGISTRY, DailyNewsSourceEntry, normalize_source_url
from src.models.daily_news_models import EditorialStory

if TYPE_CHECKING:
    from src.data_access.daily_news.daily_news_backend import EditorialStoryRepositoryProtocol

_MAX_EXCERPT_CHARS = 400
_PER_SOURCE_CAP = 5  # enforced both at persistence time (run_editorial_discovery) and display time (select_visible_editorial_stories)
_TOTAL_DISPLAY_CAP = 20
_FRESHNESS_WINDOW_HOURS = 72
# Controlled dry-run harness (design/DECISIONS.md) — bounds
# EditorialScanReport.admitted_examples/rejected_examples; a reporting
# sample size only, never a gate/cap on what actually qualifies.
_DRY_RUN_SAMPLE_SIZE = 5

# Government / Public Sector Daily News lane (design/DECISIONS.md) — see
# this module's own docstring for why these are two named, hardcoded
# source_ids, not a generic/extensible mechanism.
_SPACEFORCE_SOURCE_ID = "spaceforce-news-rss"
_NIST_SOURCE_ID = "nist-news-rss"

# Strict, source-scoped allow-list for _NIST_SOURCE_ID only — approved
# terms exactly, never broadened without a separate proposal (same
# discipline editorial_matching.THEME_KEYWORDS' own docstring
# establishes for that separate, general-purpose table, which this list
# is deliberately kept apart from). Deliberately excludes "advanced
# manufacturing" (too broad — NIST publishes general-manufacturing
# content unrelated to semiconductors), bare "chip" (ambiguous), and
# bare "CHIPS" alone without "Act"/"for America" (collides with NIST's
# own generic navigation/boilerplate text).
_NIST_ALLOW_LIST_TERMS: tuple[str, ...] = (
    "CHIPS Act", "CHIPS for America", "semiconductor", "semiconductors",
    "semiconductor manufacturing", "semiconductor fabrication",
    "wafer fabrication", "microelectronics",
)
_NIST_ALLOW_LIST_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE) for term in _NIST_ALLOW_LIST_TERMS
)

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
    # Precision-first admission gate (design/DECISIONS.md, the Nintendo/
    # Amazon false-positive audit) — distinct from items_no_match: this
    # counts an item that DID match a company or theme keyword but was
    # rejected because that match wasn't admission-worthy (an incidental
    # mention, a consumer/editorial format, an ambiguous alias with no
    # supporting evidence, ...). See editorial_admission.py.
    items_not_subject_relevant: int
    items_duplicate: int
    items_already_seen: int  # idempotency: same story_id already in the store
    items_capped: int  # qualified (passed every gate) but excluded by the 5-per-feed persistence cap this run
    stories_published: int  # newly persisted this run
    source_failures: dict[str, str]  # source_id -> sanitized failure_code
    # Controlled dry-run harness (design/DECISIONS.md) — populated ONLY
    # when run_editorial_discovery(dry_run=True); empty tuples for every
    # normal (non-dry-run) call, including every existing production
    # worker tick, which never sets dry_run and never reads these two
    # fields. A small, bounded sample (see _DRY_RUN_SAMPLE_SIZE) of
    # admitted headlines and (rejected headline, reason) pairs — reuses
    # the exact same admission/materiality reasons this module already
    # computes for every item, never a separate/duplicated gate.
    admitted_examples: tuple[str, ...] = ()
    rejected_examples: tuple[tuple[str, str], ...] = ()


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


def _matches_nist_allow_list(title: str, summary: str | None) -> bool:
    """Pure, source-scoped (see _NIST_SOURCE_ID) allow-list check —
    word-boundary, case-insensitive, against only the item's own real
    title and summary, never a fetched article body. Fails closed: an
    empty/None title and summary, or any text matching none of
    _NIST_ALLOW_LIST_TERMS, returns False."""
    combined = title if not summary else f"{title}\n{summary}"
    if not combined.strip():
        return False
    return any(pattern.search(combined) for pattern in _NIST_ALLOW_LIST_PATTERNS)


def run_editorial_discovery(
    cache_dir: Path,
    source_entries: tuple[DailyNewsSourceEntry, ...] = EDITORIAL_SOURCE_REGISTRY,
    editorial_repository: "EditorialStoryRepositoryProtocol | None" = None,
    dry_run: bool = False,
) -> EditorialScanReport:
    """One bounded discovery run across every configured editorial feed.
    One source's fetch failure is isolated (recorded in source_failures)
    and never blocks the others — same discipline as
    daily_news_pipeline.run_discovery().

    `dry_run` (default False — controlled dry-run harness, design/
    DECISIONS.md): when True, every real fetch/match/materiality/
    admission step still runs exactly as normal — the only difference is
    that the final persistence call (editorial_story_store.
    upsert_new_stories()/editorial_repository.upsert_new_stories()) is
    skipped. The returned EditorialScanReport's stories_published still
    reports the count that WOULD have been persisted, for an accurate
    dry-run summary. Existing callers omitting this parameter are
    completely unaffected."""
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
    items_not_subject_relevant = 0
    items_duplicate = 0
    items_already_seen = 0
    items_capped = 0
    newly_published: list[EditorialStory] = []
    source_failures: dict[str, str] = {}
    admitted_examples: list[str] = []
    rejected_examples: list[tuple[str, str]] = []

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
            # Materiality classified here, once, right after matching —
            # needed by the admission gate below (a genuine material-
            # development signal can independently justify admission,
            # even for an ambiguous alias or a consumer-format-shaped
            # headline — see editorial_admission.py). Carried through to
            # construction below unchanged, never recomputed.
            materiality_tier, materiality_reasons = classify_editorial_story(
                entry.title, entry.summary, source.category,
            )
            # Government / Public Sector Daily News lane (design/DECISIONS.md):
            # two named, hardcoded source_id exceptions to the general
            # fail-closed gate below — see this module's own docstring
            # for why this is deliberately not a category-based or
            # generically-extensible mechanism. Every other source_id,
            # including any future one, falls through to the unchanged
            # company-or-theme check, followed by the precision-first
            # admission gate (design/DECISIONS.md, the Nintendo/Amazon
            # false-positive audit) — a company/theme keyword match is
            # necessary but no longer sufficient; see editorial_admission.
            # py's own docstring for the full rule. Deliberately not
            # applied to SpaceForce/NIST: an existing, narrower,
            # separately-approved bypass this fix does not touch.
            if source.source_id == _SPACEFORCE_SOURCE_ID:
                pass
            elif source.source_id == _NIST_SOURCE_ID:
                if not _matches_nist_allow_list(entry.title, entry.summary):
                    items_no_match += 1
                    continue
            elif not matched_companies and not matched_themes:
                items_no_match += 1
                if dry_run and len(rejected_examples) < _DRY_RUN_SAMPLE_SIZE:
                    rejected_examples.append((entry.title, "no_qualifying_company_or_theme_match"))
                continue
            else:
                admission = assess_admission(
                    entry.title, entry.summary, matched_companies, matched_themes, materiality_reasons,
                )
                if not admission.admitted:
                    items_not_subject_relevant += 1
                    if dry_run and len(rejected_examples) < _DRY_RUN_SAMPLE_SIZE:
                        rejected_examples.append((entry.title, admission.reason))
                    continue

            # Provisionally registered so a second duplicate of THIS SAME
            # entry later in this same source's own feed is still caught
            # — even though this entry might itself be dropped by the cap
            # below, the source's own feed practically never repeats an
            # already-capped item's exact URL/title, so this is a safe,
            # simple, single-pass dedup registration.
            existing_urls.add(normalized_url)
            existing_title_publisher.add((normalized_title, source.attribution_label))
            qualifying.append((entry, story_id, matched_companies, matched_themes, materiality_tier, materiality_reasons))

        qualifying.sort(key=lambda item: item[0].published_at, reverse=True)
        capped = qualifying[:_PER_SOURCE_CAP]
        items_capped += len(qualifying) - len(capped)

        for entry, story_id, matched_companies, matched_themes, materiality_tier, materiality_reasons in capped:
            retrieved_at = datetime.now(timezone.utc).isoformat()
            # Signals materiality classification (design/DECISIONS.md) —
            # computed once, above, in the qualifying loop (also used by
            # the precision-first admission gate there — see
            # editorial_admission.py); carried through here unchanged,
            # never recomputed and never reclassifies an already-
            # persisted story (see NewsMaterialityTier's own docstring).
            story = EditorialStory(
                id=story_id, headline=entry.title, publisher=source.attribution_label, source_url=entry.link,
                published_at=entry.published_at, retrieved_at=retrieved_at,
                excerpt=_extractive_excerpt(entry.summary),
                matched_companies=matched_companies, matched_themes=matched_themes,
                source_feed_id=source.source_id,
                materiality_tier=materiality_tier, materiality_reasons=materiality_reasons,
            )
            newly_published.append(story)
            if dry_run and len(admitted_examples) < _DRY_RUN_SAMPLE_SIZE:
                admitted_examples.append(story.headline)

    if newly_published and not dry_run:
        if editorial_repository is None:
            editorial_story_store.upsert_new_stories(cache_dir, newly_published)
        else:
            editorial_repository.upsert_new_stories(newly_published)

    completed_at = datetime.now(timezone.utc).isoformat()
    return EditorialScanReport(
        scan_id=scan_id, started_at=started_at, completed_at=completed_at, sources_polled=len(source_entries),
        items_fetched=items_fetched, items_no_valid_url=items_no_valid_url, items_stale=items_stale,
        items_no_match=items_no_match, items_not_subject_relevant=items_not_subject_relevant,
        items_duplicate=items_duplicate, items_already_seen=items_already_seen,
        items_capped=items_capped, stories_published=len(newly_published), source_failures=source_failures,
        admitted_examples=tuple(admitted_examples), rejected_examples=tuple(rejected_examples),
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


def select_visible_editorial_stories_for_company(
    stories: dict[str, EditorialStory], company_name: str, now: datetime | None = None,
) -> tuple[EditorialStory, ...]:
    """System-wide company-matched-news fix (design/DECISIONS.md) — the
    per-company counterpart to select_visible_editorial_stories() above.
    Same 72-hour freshness (re-checked here, not trusted from ingest
    time) and same _PER_SOURCE_CAP-per-source cap, applied against the
    FULL persisted story set — never against
    select_visible_editorial_stories()'s own already cross-company-
    capped output. Deliberately does NOT apply _TOTAL_DISPLAY_CAP: that
    cap exists only to bound the shared "All companies" list's length: a
    company already scoped to its own matched_companies membership has
    no cross-company list to bound, and applying that cap here would
    silently drop a company's own real, fresh, correctly-matched
    coverage whenever enough OTHER companies' stories happened to be
    newer — exactly the defect this function exists to fix. Generic:
    works identically for any company_name in the Daily News company
    universe (company_aliases.daily_news_company_names()) — tracked or
    Daily-News-only-stub — with no company-specific branch.

    Kept as its own separate, small function (rather than refactoring
    select_visible_editorial_stories() to take an optional company
    filter) specifically so the "All companies" path above stays
    provably byte-for-byte unchanged — see
    tests/test_editorial_pipeline.py's own regression coverage."""
    now = now or datetime.now(timezone.utc)
    matching = [s for s in stories.values() if company_name in s.matched_companies]
    fresh = [s for s in matching if _is_fresh(s.published_at, now)]
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
    return tuple(capped_per_source)
