"""Recently Updated ("Latest") — Dashboard Batch 1 (design/DECISIONS.md),
beta-blocker data-integrity fix (design/DECISIONS.md). A single, compact,
reverse-chronological feed merging three already-existing, already-real
data sources — never a new one, never a seeded/demo one:

  1. Real Radar CandidateSignals for SEC EDGAR/DART/EDINET (via
     backend_factory.get_candidate_repository()) — never a raw
     FilingEvent, which may never have qualified as a candidate at all.
  2. Real, currently-PUBLISHED Daily News issuer stories (via
     daily_news_backend.get_daily_news_repository()).
  3. Real Daily News editorial stories that already passed the existing
     high-signal eligibility rules (via src.ui.components.
     editorial_coverage.get_visible_editorial_stories() — the exact same
     function the Daily News page itself calls; previously absent from
     this feed entirely).

No new source, no new network call, no new cache format — purely a
different read-time merge/sort/render over data every source already
produces through its own existing, unmodified pipeline.

Sort key, per row (approved rule, design/DECISIONS.md):
  Radar candidates: filing.filed_at when present and parseable; else
    filing.rcept_dt parsed as a source-claimed filing date; else filing.
    retrieved_at as the final fallback.
  Daily News (issuer and editorial): published_at exactly as stored —
    already resolved to publisher-claimed-or-retrieved at write time by
    daily_news_pipeline.py/editorial_pipeline.py, so no further fallback
    is applied here.
Every parsed instant with no explicit UTC offset is treated as UTC for
sorting purposes only (same "naive input assumed UTC" convention
src.logic.formatting already documents); a bare filing date (rcept_dt)
is combined with UTC midnight for sorting only — never displayed as a
fabricated time when the source didn't supply one (see
_filing_display_date, which falls back to a date-only label in that
case). Ties break on a content-derived identity key (_row_identity_key),
never on list-build/source order, so the shown order is fully
deterministic across reruns.

Eligibility (beta-blocker fix, design/DECISIONS.md) — a row is excluded,
never clamped or guessed, when: its own sort key is unparseable; its own
sort key is materially in the future (more than _FUTURE_TOLERANCE past
"now" — never trusted, since a genuine publication can't postdate the
moment this process is looking at it); a Radar candidate's own status
means archived/rejected (_ARCHIVED_OR_REJECTED_CANDIDATE_STATUSES); a
Radar filing is stale seed/demo data (filing.is_demo); or a Daily News
issuer story isn't currently PUBLISHED. A row that duplicates an
already-kept row's own content-derived identity key is dropped. If
nothing survives all of the above, the feed shows its own honest empty
state — never old/seed content standing in for a live result.

No claim-type, confidence, strength, importance, priority, score,
summary, "why it matters", or bullish/bearish language anywhere — this
is a factual, source-attributed feed only. Fails closed per source: one
source's repository error degrades that source to zero items, never a
raised exception reaching the Dashboard (same discipline
regional_brief.py's own _load_recent_filings already uses).

Company name, title, and source URL are real, external-sourced strings
(filer/publisher-supplied) — escaped via html.escape() before being
placed inside unsafe_allow_html, matching the discipline
src/ui/components/radar_card.py and src/ui/pages/themes_research.py
already establish for the same category of data.

On-demand title translation (Dashboard/Filings usability pass, design/
DECISIONS.md; extended to Daily News issuer rows by the Dashboard/
Signals quality fix, design/DASHBOARD_SIGNAL_QUALITY_FIX_DESIGN.md): a
filing OR Daily News issuer row whose own original_language is a
non-English language this app's existing translation_service.py already
supports ("Korean"/"Japanese"/"French") shows a `Translate to English`
action. The original title is always the initial/default display —
nothing is translated automatically on page load. Editorial rows
(_load_editorial_rows) are unaffected: every currently-registered
editorial source is English-language, so original_language/
translation_document_id stay unset (None) for those rows, same as
before this fix. On click, this calls the existing
translation_service.translate_cached_with_outcome() exactly once,
through the same DeepLProvider/cache_dir/cache-file convention
src.data_access.dart.radar_service._translation_provider and
src.data_access.edinet.edinet_service._translation_provider already use
(duplicated here rather than imported, matching this codebase's own
established precedent of each module keeping its own copy of that tiny
private helper) — no new provider, secret, dependency, worker, or
migration. A successful translation is cached in st.session_state (never
persisted to any store) and offers an `Original`/`English` toggle for
that row only; a failed/unavailable attempt shows one concise,
non-blocking status line and leaves the original title as the only
thing shown — never a raw error, category, or retry mechanics."""
from __future__ import annotations

import html
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone

import streamlit as st

from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.daily_news import daily_news_backend, daily_news_pipeline, dedup, editorial_pipeline
from src.data_access.translation import translation_service
from src.data_access.translation.deepl_provider import DeepLProvider
from src.logic import filing_display
from src.logic.formatting import fmt_date, fmt_datetime_local
from src.logic.market_map import REGION_SOURCE
from src.logic.source_link import public_source_url
from src.models.daily_news_models import EditorialStory, NewsMaterialityTier, NewsStoryStatus
from src.models.models import CandidateStatus, FilingEvent
from src.ui.components.editorial_coverage import get_visible_editorial_stories
from src.ui.components.primitives import cjk_html
from src.ui.ui import get_page

PREVIEW_COUNT = 5

# Beta-blocker fix (design/DECISIONS.md) — "Latest"/Recently Updated must
# be driven entirely by live, currently-eligible data: real Radar
# CandidateSignals (never a raw FilingEvent that never qualified as a
# candidate), real PUBLISHED Daily News issuer stories, and real,
# already-high-signal-gated Daily News editorial stories (the same
# fail-closed matching + freshness + per-source-cap rules
# get_visible_editorial_stories() already enforces for the Daily News
# page itself — reused here unchanged, never reimplemented). A status
# that means "a human/system already archived or rejected this" is
# never eligible to be the freshest thing shown on the dashboard.
_ARCHIVED_OR_REJECTED_CANDIDATE_STATUSES = frozenset({CandidateStatus.DISMISSED, CandidateStatus.NOT_MATERIAL})

# Same guard, same tolerance, and the same reasoning as
# rss_atom_client._FUTURE_TOLERANCE: a genuine publication/filing can
# never be timestamped later than the moment this process is looking at
# it. A handful of minutes of ordinary clock skew is tolerated; anything
# more is never trusted enough to win "Latest" — excluded outright here
# (never clamped to "now", which would let an untrustworthy timestamp
# win the very top spot it's least entitled to).
_FUTURE_TOLERANCE = timedelta(minutes=5)

_FILING_SOURCE_LABEL = {
    "SEC EDGAR": "SEC EDGAR",
    "OpenDART / DART": "Korea DART",
    "EDINET": "Japan EDINET",
}

# The same source-language-name -> DeepL source_lang mapping
# translation_service.py's own _LANGUAGE_CODE_BY_NAME already defines —
# duplicated (not imported) here since that name is module-private
# there, matching this file's own existing precedent of keeping its own
# copy of a small shared helper rather than reaching across a module
# boundary for a private symbol (see _parse_filing_date's own comment).
# Deliberately only the two languages FilingEvent.original_language ever
# actually carries for a non-English source (Korean for DART, Japanese
# for EDINET) — English/unmapped values never show a translate action.
# Dashboard/Signals quality fix (design/
# DASHBOARD_SIGNAL_QUALITY_FIX_DESIGN.md): French added — Daily News
# issuer rows now reach this same map (see _load_daily_news_rows below),
# and DeepL already supports FR natively; no provider change needed.
_LANGUAGE_CODE_BY_ORIGINAL_LANGUAGE = {"Korean": "KO", "Japanese": "JA", "French": "FR"}


@dataclass(frozen=True)
class _Row:
    sort_key: datetime
    company_name: str
    title: str
    source_label: str
    display_date: str
    source_url: str | None
    # None for every Daily News row (translation out of scope there) and
    # for any filing whose original_language isn't one of the two known,
    # supported values above — in both cases no translate action renders.
    original_language: str | None = None
    # Stable per-row cache/session key — the filing's own (source_name,
    # corp_code, rcept_no) dedup key for a filing row; None for Daily News.
    translation_document_id: str | None = None
    # EDINET filing-source usability fix (design/
    # EDINET_FILING_SOURCE_USABILITY_DESIGN.md) — additive, EDINET-only;
    # None for every non-EDINET filing row and every Daily News row. A
    # filing-specific EDINET-portal instruction (issuer, 4-digit
    # securities code, filing type/title, filed date, document ID),
    # shown next to "View source ↗" whenever that link resolves to the
    # bare, non-specific EDINET portal homepage rather than a real
    # per-document URL.
    edinet_instruction: str | None = None


def _esc(value: object) -> str:
    if value is None:
        return ""
    return html.escape(str(value))


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _is_materially_future(dt: datetime, now: datetime) -> bool:
    return dt > now + _FUTURE_TOLERANCE


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return _as_utc(datetime.fromisoformat(raw))
    except ValueError:
        return None


def _parse_filing_date(raw: str) -> date | None:
    """Same tolerant parser as regional_brief.py's/radar_inbox.py's own
    private _parse_rcept_date — duplicated here rather than imported,
    matching this codebase's own existing precedent of each page/
    component keeping its own copy rather than sharing a private symbol
    across modules. FilingEvent.rcept_dt is each source's own raw date
    string — DART's unconverted "YYYYMMDD", EDGAR/EDINET's dashed
    "YYYY-MM-DD"."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw.replace("-", ""), "%Y%m%d").date()
    except ValueError:
        return None


def _filing_sort_key(filing: FilingEvent) -> datetime | None:
    parsed = _parse_iso(filing.filed_at)
    if parsed is not None:
        return parsed
    filing_date = _parse_filing_date(filing.rcept_dt)
    if filing_date is not None:
        return _as_utc(datetime.combine(filing_date, datetime.min.time()))
    return _parse_iso(filing.retrieved_at)


def _filing_display_date(filing: FilingEvent) -> str:
    if filing.filed_at and _parse_iso(filing.filed_at) is not None:
        return fmt_datetime_local(filing.filed_at)
    filing_date = _parse_filing_date(filing.rcept_dt)
    if filing_date is not None:
        return fmt_date(filing_date.isoformat())
    if filing.retrieved_at and _parse_iso(filing.retrieved_at) is not None:
        return fmt_datetime_local(filing.retrieved_at)
    return ""


def _load_filing_rows(
    settings: Settings, now: datetime, preloaded_by_source: dict | None = None,
) -> list[_Row]:
    """Real Radar CandidateSignals only (design/DECISIONS.md beta-blocker
    fix) — never a raw FilingEvent, which may never have qualified as a
    candidate at all (routine/non-matching filings are never promoted;
    see edgar_rules.py/dart_rules.py/edinet_rules.py). A candidate whose
    own status means "archived"/"rejected" (_ARCHIVED_OR_REJECTED_
    CANDIDATE_STATUSES), a stale seed/demo filing (filing.is_demo), or a
    filing whose own sort key is unparseable or materially future is
    never eligible to appear here — excluded, never clamped.

    Phase 2B: `preloaded_by_source` maps a source name to the candidate
    objects the Dashboard already read for that source. Supplied, NO
    repository is constructed here and load_candidates() is never
    called. Omitted, every load happens exactly as before. The
    eligibility rules, ordering and rendered rows below are identical
    either way."""
    rows: list[_Row] = []
    for source in REGION_SOURCE.values():
        if preloaded_by_source is not None:
            source_candidates = preloaded_by_source.get(source)
            if source_candidates is None:
                continue
        else:
            try:
                source_candidates = list(
                    backend_factory.get_candidate_repository(settings, source).load_candidates().values()
                )
            except Exception:  # noqa: BLE001 — fail closed; one source's error must never take down the feed
                continue
        for candidate in source_candidates:
            if candidate.status in _ARCHIVED_OR_REJECTED_CANDIDATE_STATUSES:
                continue
            filing = candidate.filing
            if filing.is_demo:
                continue
            sort_key = _filing_sort_key(filing)
            if sort_key is None or _is_materially_future(sort_key, now):
                continue
            rows.append(_Row(
                sort_key=sort_key,
                company_name=filing.corp_name,
                title=filing.report_nm,
                source_label=_FILING_SOURCE_LABEL.get(filing.source_name, filing.source_name),
                display_date=_filing_display_date(filing),
                # EDINET-safety fix (design/DECISIONS.md): filing.source_url
                # is always the raw, key-required EDINET API endpoint for an
                # EDINET filing — public_source_url() rewrites it to the
                # public disclosure portal root; every other source's URL
                # passes through unchanged.
                source_url=public_source_url(filing.source_url) or None,
                original_language=filing.original_language,
                translation_document_id=f"recently-updated:{filing.source_name}:{filing.corp_code}:{filing.rcept_no}",
                edinet_instruction=(
                    filing_display.edinet_source_instruction(filing, candidate, _filing_display_date(filing))
                    if filing.source_name == filing_display.EDINET_SOURCE_NAME else None
                ),
            ))
    return rows


def _load_daily_news_rows(settings: Settings, now: datetime) -> list[_Row]:
    """Real, currently-PUBLISHED Daily News issuer stories only (beta-
    blocker fix, design/DECISIONS.md) — a DISCOVERED/SUMMARIZED story
    (still in progress) or a SUPPRESSED one (no valid canonical URL) was
    previously shown here unfiltered; both are now excluded, matching
    the exact status gate src.ui.pages.daily_news._published_stories()
    already applies to the Daily News page itself. A story whose own
    published_at is unparseable or materially future is excluded, never
    clamped — same reasoning as _load_filing_rows above."""
    rows: list[_Row] = []
    try:
        stories = daily_news_backend.get_daily_news_repository(settings).load_stories()
        # Dashboard/Signals quality fix (design/
        # DASHBOARD_SIGNAL_QUALITY_FIX_DESIGN.md): read-time
        # reconciliation — collapses a cross-language localized-
        # duplicate pair (e.g. an English/French pair for the same
        # company event) down to its one preferred (English/global)
        # row, the same call src.ui.pages.daily_news._published_stories
        # already makes. Every story this excludes stays fully intact
        # in the underlying store; only what this row list shows
        # changes. A no-op whenever no such pair exists.
        # select_canonical_stories() takes no TranslationProvider — it
        # only ever reads an already-cached translation, never triggers
        # a live translation request during render.
        stories = daily_news_pipeline.select_canonical_stories(stories, settings.cache_dir)
    except Exception:  # noqa: BLE001 — fail closed; Daily News unavailability must never take down the feed
        return rows
    for story in stories.values():
        if story.status != NewsStoryStatus.PUBLISHED:
            continue
        if not story.sources:
            continue
        # Signals quality pass: a Background issuer story never reaches
        # this default preview — the same rule _load_editorial_rows()
        # below already applies to the editorial lane. A legacy story
        # (no stored tier) is tiered at read time, never written back.
        if daily_news_pipeline.effective_issuer_tier(story) == NewsMaterialityTier.BACKGROUND:
            continue
        source_ref = story.sources[0]
        sort_key = _parse_iso(source_ref.published_at)
        if sort_key is None or _is_materially_future(sort_key, now):
            continue
        rows.append(_Row(
            sort_key=sort_key,
            company_name=story.company_name,
            title=story.headline,
            source_label="Signals",
            display_date=fmt_datetime_local(source_ref.published_at),
            source_url=source_ref.url or None,
            # Dashboard/Signals quality fix (design/
            # DASHBOARD_SIGNAL_QUALITY_FIX_DESIGN.md): wires Daily News
            # issuer rows into this component's existing, already-
            # tested on-demand translate control (previously
            # deliberately None for every Daily News row — see this
            # module's own docstring) — the same shared, non-filing
            # translation component filing rows above already use, now
            # safely reused here too.
            original_language=source_ref.original_language,
            translation_document_id=f"recently-updated-signals:{story.id}",
        ))
    return rows


def _effective_editorial_tier(story: EditorialStory) -> NewsMaterialityTier:
    """Same shared, data-layer rule src.ui.pages.daily_news._effective_tier()
    uses: a stored tier wins; a legacy None is tiered at read time and
    never written back (Signals quality pass, legacy-tier option (a))."""
    return editorial_pipeline.effective_editorial_tier(story)


def _load_editorial_rows(settings: Settings, now: datetime) -> list[_Row]:
    """Real Daily News editorial stories that already passed the
    existing high-signal eligibility rules (beta-blocker fix, design/
    DECISIONS.md) — get_visible_editorial_stories() is the exact same,
    unmodified function src.ui.pages.daily_news itself calls: fail-closed
    company/theme matching, 72-hour freshness, and the existing per-
    source/total display caps are all already applied by the time this
    function receives them. Previously absent from this feed entirely —
    only issuer stories were ever considered, so a real, matched,
    high-signal editorial item (CNBC, Reuters-style wire coverage, etc.)
    could never win "Latest" no matter how new it was. A materially
    future published_at is excluded here too, as a local, independent
    safety net — this component's own guarantee never depends on any
    other page's own freshness gate alone.

    Dashboard tier-aware preview (Signals precision follow-up, live-card
    audit, design/POST_MERGE_SIGNALS_LIVE_CARD_AUDIT_2026_09_16.md) — a
    Background-tier editorial story is excluded from this default
    preview entirely (never merely relabeled or reordered): this
    component's own row shape carries no tier badge at all (see this
    module's own top-of-file docstring — "no claim-type, confidence,
    strength, importance, priority, score... this is a factual,
    source-attributed feed only"), so a Background item shown here would
    be visually indistinguishable from genuine Signal content. High
    Signal and Watchlist stories are unaffected. The Signals page's own
    "Show Background (N)" expander (src/ui/pages/daily_news.py) is a
    completely separate code path and is untouched by this filter."""
    rows: list[_Row] = []
    try:
        stories = get_visible_editorial_stories(settings)
    except Exception:  # noqa: BLE001 — fail closed; Daily News unavailability must never take down the feed
        return rows
    for story in stories:
        if _effective_editorial_tier(story) == NewsMaterialityTier.BACKGROUND:
            continue
        sort_key = _parse_iso(story.published_at)
        if sort_key is None or _is_materially_future(sort_key, now):
            continue
        rows.append(_Row(
            sort_key=sort_key,
            company_name=", ".join(story.matched_companies),
            title=story.headline,
            source_label="Signals",
            display_date=fmt_datetime_local(story.published_at),
            source_url=story.source_url or None,
        ))
    return rows


def _row_identity_key(row: _Row) -> str:
    """A stable, content-derived identifier for this row — used only as
    the per-row Streamlit container key (visual/CSS grouping), never for
    translation cache/session-state (those already key exclusively off
    row.translation_document_id, unchanged by this function). `shown`'s
    order/membership can shift between reruns as new filings/stories are
    discovered, so a positional index would not reliably identify "the
    same row" across reruns the way this does. Every filing row already
    has a real translation_document_id (set unconditionally in
    _load_filing_rows regardless of language); only Daily News rows can
    reach the fallbacks, and a real story's source_url is effectively
    always present in practice — the final fallback exists only as a
    deterministic, collision-safe last resort, never expected to fire."""
    if row.translation_document_id:
        return row.translation_document_id
    if row.source_url:
        return row.source_url
    return f"{row.company_name}|{row.title}|{row.display_date}"


def _can_translate(row: _Row) -> bool:
    return bool(row.translation_document_id) and row.original_language in _LANGUAGE_CODE_BY_ORIGINAL_LANGUAGE


def _translated_text_key(row: _Row) -> str:
    return f"ru-translated-text-{row.translation_document_id}"


def _translate_failed_key(row: _Row) -> str:
    return f"ru-translate-failed-{row.translation_document_id}"


def _show_english_key(row: _Row) -> str:
    return f"ru-show-english-{row.translation_document_id}"


def _translation_provider(settings: Settings) -> DeepLProvider:
    return DeepLProvider(settings.translation_api_key)


def _do_translate(row: _Row, settings: Settings) -> None:
    """The one and only place this component ever calls the translation
    provider — inside a button's on_click handler, never during a plain
    render/page-load. Reuses translation_service.translate_cached_with_
    outcome() exactly as DART/EDINET's own pipelines do (same cache file
    convention, keyed by document_id + text hash) — a repeat click for
    the same row/title is a cache hit, not a second live call. Never
    raises: a failure is recorded as a concise, non-blocking flag; the
    original title remains the only thing shown."""
    provider = _translation_provider(settings)
    source_lang = _LANGUAGE_CODE_BY_ORIGINAL_LANGUAGE[row.original_language]
    attempt = translation_service.translate_cached_with_outcome(
        provider, document_id=row.translation_document_id, text=row.title,
        cache_dir=settings.cache_dir, source_lang=source_lang,
    )
    if attempt.translation is not None:
        st.session_state[_translated_text_key(row)] = attempt.translation.translated_text
        st.session_state[_show_english_key(row)] = True
    else:
        st.session_state[_translate_failed_key(row)] = True


def _toggle_show_english(row: _Row) -> None:
    key = _show_english_key(row)
    st.session_state[key] = not st.session_state.get(key, False)


def _render_row(row: _Row, settings: Settings) -> None:
    """Visual restyle only (design/DECISIONS.md) — `row` is already fully
    computed by the unchanged loading/sort logic above; this function
    only decides how to display it. Metadata (company, source, date)
    renders via the existing, deliberately colorless `er-status-tag
    er-tag-neutral` pill and `er-date-badge` — a uniform neutral badge
    for every source, so no color ever implies a ranking between SEC
    EDGAR / Korea DART / Japan EDINET / Daily News. The whole row is one
    real <a> element (never a second, separately-clickable link nested
    inside it — invalid HTML) when a real source URL exists; "View
    source ↗" is a visible text affordance inside that same anchor, not
    an independent link. A row with no real source URL renders the
    identical content without any anchor wrapper or affordance — never a
    fabricated or dead link.

    On-demand translation (Dashboard/Filings usability pass, design/
    DECISIONS.md): the title displayed inside the anchor/content block is
    the original filing title unless a translation was already
    successfully fetched this session AND the row's own toggle is
    currently set to English — the original title is always what a fresh
    page load shows. The translate action/toggle itself renders as a
    separate widget below the row (an <a> block cannot contain a nested
    <button>), matching the same st.button-below-content pattern
    radar_card.py's own toggles already use."""
    translated_text = st.session_state.get(_translated_text_key(row))
    show_english = st.session_state.get(_show_english_key(row), False)
    display_title = translated_text if (translated_text and show_english) else row.title

    company_html = f"{cjk_html(row.company_name, row.original_language)} " if row.company_name else ""
    metadata_html = (
        f'<div class="er-muted" style="font-size:0.78rem; margin-top:0.2rem; display:flex; align-items:center; '
        f'gap:0.4rem; flex-wrap:wrap;">{company_html}'
        f'<span class="er-status-tag er-tag-neutral">{_esc(row.source_label)}</span>'
        f'<span class="er-date-badge">{_esc(row.display_date)}</span></div>'
    )
    content_html = (
        f'<div style="flex:1; min-width:0;">'
        f'<div class="er-card-title" style="font-size:0.88rem;">{cjk_html(display_title, row.original_language)}</div>'
        f"{metadata_html}"
        f"</div>"
    )

    if row.source_url:
        st.markdown(
            f'<a href="{html.escape(row.source_url, quote=True)}" target="_blank" rel="noopener noreferrer" '
            f'style="text-decoration:none; color:inherit; display:block;">'
            f'<div class="er-row" style="display:flex; align-items:center; justify-content:space-between; '
            f'gap:var(--space-2); flex-wrap:wrap;">'
            f"{content_html}"
            f'<div class="er-muted" style="font-size:0.76rem; white-space:nowrap;">View source ↗</div>'
            f"</div></a>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div class="er-row" style="display:flex; align-items:center; gap:var(--space-2); flex-wrap:wrap;">'
            f"{content_html}"
            f"</div>",
            unsafe_allow_html=True,
        )

    # EDINET filing-source usability fix (design/
    # EDINET_FILING_SOURCE_USABILITY_DESIGN.md): "View source ↗" above
    # resolves to the bare EDINET portal homepage, never a per-document
    # URL (see src.logic.source_link.public_source_url) — this
    # filing-specific instruction carries the context a reader needs to
    # actually find the filing there. None/absent for every non-EDINET
    # row, so this is a no-op for EDGAR/DART/Daily News.
    if row.edinet_instruction:
        st.markdown(
            f'<div class="er-muted" style="font-size:0.76rem; margin-top:0.2rem;">{_esc(row.edinet_instruction)}</div>',
            unsafe_allow_html=True,
        )

    if not _can_translate(row):
        return

    if translated_text:
        label = "Original" if show_english else "English"
        with st.container(key=f"cta-tertiary-{row.translation_document_id}"):
            st.button(label, key=f"ru-toggle-{row.translation_document_id}-btn", on_click=_toggle_show_english, args=(row,))
    elif st.session_state.get(_translate_failed_key(row)):
        st.markdown('<div class="er-muted" style="font-size:0.76rem;">Translation unavailable</div>', unsafe_allow_html=True)
    else:
        with st.container(key=f"cta-tertiary-translate-{row.translation_document_id}"):
            st.button(
                "Translate to English", key=f"ru-translate-{row.translation_document_id}-btn",
                on_click=_do_translate, args=(row, settings),
            )


def _select_recently_updated_rows(
    settings: Settings, now: datetime | None = None, preloaded_by_source: dict | None = None,
) -> list[_Row]:
    """Pure selection logic (beta-blocker fix, design/DECISIONS.md) —
    load, exclude, deduplicate, and deterministically sort every eligible
    row across all three real sources, with no Streamlit rendering. Kept
    separate from render_recently_updated() so the actual selection
    outcome (what wins "Latest", and why) is directly testable without
    driving a full page render."""
    now = now or datetime.now(timezone.utc)
    rows = (
        _load_filing_rows(settings, now, preloaded_by_source)
        + _load_daily_news_rows(settings, now)
        + _load_editorial_rows(settings, now)
    )

    # Duplicate-row safety net (beta-blocker fix, design/DECISIONS.md):
    # keeps the first occurrence of each content-derived identity key
    # (see _row_identity_key's own docstring) and drops any later repeat
    # — each real source is already deduplicated at ingestion, so this
    # only ever guards against the same record loading twice within one
    # render, never against two genuinely different real items.
    seen_keys: set[str] = set()
    deduped: list[_Row] = []
    for row in rows:
        key = _row_identity_key(row)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(row)

    # Deterministic sort: newest sort_key first; a content-derived
    # identity key (never list-build/source order) breaks an exact-
    # timestamp tie, so the shown order never depends on which source
    # happened to be loaded first.
    deduped.sort(key=lambda r: (r.sort_key.timestamp(), _row_identity_key(r)), reverse=True)
    return _merge_same_headline_signal_rows(deduped)


def _merge_same_headline_signal_rows(rows: list[_Row]) -> list[_Row]:
    """Signals quality pass: one joint announcement published by several
    tracked issuers ("AMD, Cisco and HUMAIN Expand...") arrives as one
    story per issuer, each with its own URL — the identity-key dedup
    above can never collapse those. Signals rows with the same
    normalized headline collapse into the first (newest) one, whose
    company label lists every issuer once, in first-seen order. Filing
    rows are never merged. Each row's own label is kept whole (a legal
    name such as "Cisco Systems, Inc." contains a comma, so labels are
    never split). `rows` must already be in display order."""
    merged: list[_Row] = []
    index_by_headline: dict[str, int] = {}
    labels_by_index: dict[int, list[str]] = {}
    for row in rows:
        key = dedup.normalize_title_unicode(row.title) if row.source_label == "Signals" else ""
        if not key or key not in index_by_headline:
            if key:
                index_by_headline[key] = len(merged)
                labels_by_index[len(merged)] = [row.company_name] if row.company_name else []
            merged.append(row)
            continue
        index = index_by_headline[key]
        labels = labels_by_index[index]
        if row.company_name and row.company_name not in labels:
            labels.append(row.company_name)
            merged[index] = replace(merged[index], company_name=", ".join(labels))
    return merged


def render_recently_updated(settings: Settings, preloaded_by_source: dict | None = None) -> None:
    """`preloaded_by_source` (Phase 2B, additive and optional) carries the
    per-source candidate objects the caller already read; supplied, this
    component constructs no repository of its own. The Daily News and
    editorial-story paths are untouched by it — they are different data
    sources, and both still load exactly as before."""
    st.markdown('<div class="er-section-label">Recently Updated</div>', unsafe_allow_html=True)

    shown = _select_recently_updated_rows(settings, preloaded_by_source=preloaded_by_source)[:PREVIEW_COUNT]

    with st.container(border=True, key="card-recently-updated-feed"):
        if not shown:
            st.markdown(
                '<div class="er-muted" style="margin-top:0.3rem;">No recent updates available.</div>',
                unsafe_allow_html=True,
            )
        else:
            # Detached-translation-control fix (design/DECISIONS.md): each
            # row's own markdown content and its (optional) translate
            # action/toggle/status render inside one shared, stable
            # per-row container — assets/styles.css moves the row divider
            # onto this wrapper (and suppresses .er-row's own) so the
            # divider always falls after a row's translation control,
            # never between the row and its own control. Keyed by
            # _row_identity_key(row) — a content-derived identifier, not
            # list position — so a row keeps the same container identity
            # across reruns even if `shown`'s order/membership shifts
            # (e.g. a new filing/story is discovered between reruns).
            for row in shown:
                with st.container(key=f"card-recently-updated-row-{_row_identity_key(row)}"):
                    _render_row(row, settings)

    link_cols = st.columns(2)
    with link_cols[0]:
        filings_page = get_page("radar_inbox")
        if filings_page is not None:
            with st.container(key="cta-tertiary-recently-updated-filings"):
                st.page_link(filings_page, label="View all filings →")
    with link_cols[1]:
        daily_news_page = get_page("daily_news")
        if daily_news_page is not None:
            with st.container(key="cta-tertiary-recently-updated-daily-news"):
                st.page_link(daily_news_page, label="View all Signals →")
