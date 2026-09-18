"""Signals (renamed from "Daily News", product-naming separation, design/
DECISIONS.md — user-facing label only; internal route key/url_path stay
"daily_news"/"daily-news") — an independent, autonomous discovery
surface, entirely separate from Radar Inbox (see design/DECISIONS.md for
the product clarification this follows). Not to be confused with "Radar
Signals" (src/ui/pages/signals.py), the renamed, unrelated, Radar-
filing-derived concept this page's name previously collided with. Reads
NewsStory records via
src.data_access.daily_news.daily_news_backend.get_daily_news_repository()
(JSON by default, unless EDGE_DB_BACKEND selects sqlite/postgres — see
the Daily News durability workstream) and EditorialStory records via
src.ui.components.editorial_coverage.get_visible_editorial_stories() —
never CandidateSignal/FilingEvent or any Radar-owned file.

Unified feed presentation (design/DECISIONS.md): issuer and editorial
stories render as one reverse-chronological feed, newest published_at
first, each card carrying a compact "Company news"/"Market news" label
so provenance stays visible without a separate large section heading.
This is a presentation-only merge — the two repositories, their own
matching/freshness/cap/dedup rules, and their own persisted data stay
completely separate and untouched; _merge_feed_items() below only ever
combines two already-independently-filtered lists in memory, at render
time, for display order.

The issuer card shows exactly six fields, per the approved scope:
company name; publisher; a visible source-type label (design/
DECISIONS.md, source-attribution pass — see _SOURCE_CLASS_LABELS below)
resolved from the story's own already-typed NewsSourceReference.
source_class, never inferred or guessed; local publication time;
headline; a short Eeva-authored summary (or nothing, for an original-
language story — see below); and a direct "Read original source" link.
Every story produced by this pipeline today is still
SourceClass.OFFICIAL_COMPANY (see daily_news_pipeline.py — no non-
official source is wired in by this pass), so the source-type label
reads "Official company source" on every currently-real card; the label
itself is what keeps this honest once/if that ever changes. Deliberately
still excluded from this page: ranking/dedup data, internal status,
technical failures, and any Radar terminology — that detail lives only
in daily_news_admin.py.

The public page shows only issuer stories published within a rolling,
inclusive 7*24-hour window (compared in UTC; naive timestamps treated as
UTC, matching src.logic.formatting.days_ago()'s own convention) in the
default "All companies" view. Older stories stay in the underlying store
untouched; this page only ever hides them, never deletes anything.
Editorial stories keep their own separate 72-hour freshness window and
per-source/total caps (editorial_pipeline.select_visible_editorial_stories()),
entirely independent of the issuer 7-day window — see
_merge_feed_items() for how the two independently-filtered lists are
combined for display only. This "All companies" path is byte-for-byte
unchanged by the system-wide company-matched-news fix below.

Company selector and universe (system-wide company-matched-news fix,
design/DECISIONS.md): the selector lists every company in the Daily News
company universe (company_aliases.daily_news_company_names() — every
active tracked company plus every Daily-News-provenance
issuer_registry.DISCOVERY_STUBS company not already tracked), not only
companies with an existing persisted issuer story. Most tracked
companies have no official IR/newsroom feed at all and depend entirely
on qualified editorial matching for any coverage; hiding them from the
selector would make them permanently unreachable regardless of how good
that matching is. This universe is automatically inclusive of any future
company added via either supported mechanism — no per-company code
change is ever required here.

When a specific company is selected: issuer stories are those whose
company_name matches; editorial stories are queried fresh from the FULL
persisted editorial store via
editorial_coverage.get_editorial_stories_for_company() — never from
visible_editorial, the "All companies" view's own cross-company
top-20-capped list — so a story correctly matched to this company but
ranked outside that shared list by other companies'/themes' newer
coverage still appears here. Same 72-hour freshness and per-source cap
as the "All companies" editorial list; no cross-company total cap, since
that cap exists only to bound the shared list's own length. A
theme-only editorial story with no matched company is only ever visible
in the default "All companies" view.

Per-company stale-feed fallback (design/DECISIONS.md): the default "All
companies" view stays strictly limited to the 7-day window for issuer
stories — it never shows an older issuer story. But selecting one
specific company whose own persisted issuer stories are all older than 7
days (e.g. right after a previously-broken feed's first successful
ingestion) no longer shows an empty state indistinguishable from "this
company has never published anything" — it shows that company's own
latest available official stories instead, each still carrying its
real, publisher-provided `published_at` date (never `created_at`/
`retrieved_at`/discovery time) and an explicit, unmistakable
"Historical" label on every such card (system-wide company-matched-news
fix, requirement 6 — see _render_card()'s own `is_historical` handling).
The page-level "no official updates" notice is shown only when there is
also no fresh qualified editorial coverage to display alongside it
(requirement 5) — a quiet official IR feed is never allowed to imply
"no company news" when real, current editorial coverage exists. This
fallback is issuer-only: it is never triggered or satisfied by editorial
stories, and activating it never waives editorial stories' own
independent 72-hour freshness window. A company with zero persisted
PUBLISHED issuer stories and zero qualified editorial stories still gets
the original, true, honest empty state — reachable now for any company
in the universe, not only ones that already have a persisted issuer
story.
"""
from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime, timezone

import streamlit as st

from src.config.settings import Settings, get_settings
from src.data_access.daily_news import daily_news_backend, daily_news_pipeline
from src.data_access.daily_news.company_aliases import daily_news_company_names
from src.data_access.translation import translation_service
from src.data_access.translation.deepl_provider import DeepLProvider
from src.logic.formatting import fmt_datetime_local
from src.models.daily_news_models import EditorialStory, NewsMaterialityTier, NewsStory, NewsStoryStatus, SourceClass
from src.ui.components.editorial_coverage import (
    get_editorial_stories_for_company,
    get_visible_editorial_stories,
    render_editorial_card,
)
from src.ui.components.empty_state import empty_state
from src.ui.components.section import section_header

_FRESHNESS_WINDOW_DAYS = 7
_ALL_COMPANIES_OPTION = "All companies"

# Source-attribution pass (design/DECISIONS.md): exact, approved
# user-facing labels for each existing SourceClass value — no new
# category invented, no wording beyond what was explicitly approved.
_SOURCE_CLASS_LABELS: dict[SourceClass, str] = {
    SourceClass.OFFICIAL_COMPANY: "Official company source",
    SourceClass.REGULATORY_FILING: "Regulatory filing",
    SourceClass.PRESS_RELEASE_WIRE: "Press-release wire",
    SourceClass.INDEPENDENT_JOURNALISM: "Independent journalism",
}
# Product-naming separation (design/DECISIONS.md): "Daily News" renamed
# to "Signals" — the exact, approved subtitle replaces the former two
# source-mix-dependent variants (_OFFICIAL_SUBTITLE/_MIXED_SUBTITLE),
# since the approved copy is one fixed sentence regardless of source
# mix; the per-card source-type label already carries that distinction.
_SUBTITLE = "Material disclosures and developments across AI infrastructure and global technology supply chains."

# Signals materiality classification (design/DECISIONS.md) — tier-based
# presentation. Reuses the existing er-status-tag/er-tag-* badge system
# (Research Theses' evidence-direction chips already established
# pos/neg/mix as green/rose/amber) — no new CSS, no new design language.
_TIER_BADGE_CLASS: dict[NewsMaterialityTier, str] = {
    NewsMaterialityTier.HIGH_SIGNAL: "er-tag-pos",
    NewsMaterialityTier.WATCHLIST: "er-tag-mix",
    NewsMaterialityTier.BACKGROUND: "er-tag-neutral",
}
_NO_HIGH_SIGNAL_EMPTY_STATE = "No High Signals match the current filters."
_NO_WATCHLIST_EMPTY_STATE = "No Watchlist items match the current filters."

# High Signals / Watchlist tier navigation (design/DAILY_NEWS_HIGH_
# SIGNALS_WATCHLIST_IMPLEMENTATION_PLAN_2026_09_16.md) — the canonical
# query parameter and its exactly two valid values. Deliberately
# separate string constants from NewsMaterialityTier.value (never an
# alias of "High Signal"/"Watchlist") — this is the URL's own vocabulary,
# not the persisted model's, so the two can evolve independently.
_TIER_QUERY_PARAM = "tier"
_HIGH_SIGNALS_QUERY_VALUE = "high_signal"
_WATCHLIST_QUERY_VALUE = "watchlist"
_DEFAULT_TIER_QUERY_VALUE = _HIGH_SIGNALS_QUERY_VALUE
_VALID_TIER_QUERY_VALUES = (_HIGH_SIGNALS_QUERY_VALUE, _WATCHLIST_QUERY_VALUE)
_TIER_SWITCH_LABELS: dict[str, str] = {
    _HIGH_SIGNALS_QUERY_VALUE: "High Signals", _WATCHLIST_QUERY_VALUE: "Watchlist",
}
_TIER_SWITCH_VALUE_BY_LABEL: dict[str, str] = {v: k for k, v in _TIER_SWITCH_LABELS.items()}


def _resolve_active_tier_view() -> str:
    """Reads, validates, and (only when necessary) normalizes the
    canonical `tier` query parameter — the single source of truth for
    which primary view is active. Missing, invalid, repeated (resolved
    by st.query_params' own single-value .get()), or any unsupported
    value all take the same path: normalize to High Signals. Mutating
    st.query_params only when the current raw value actually differs
    from the target is what keeps this from ever looping — the very
    next rerun reads the now-canonical value and this function returns
    immediately without writing again. Every OTHER query parameter is
    left completely untouched (st.query_params is a mapping over the
    full query string; this function only ever reads/writes its own
    "tier" key, never touches or clears any other key)."""
    raw = st.query_params.get(_TIER_QUERY_PARAM, "").strip()
    if raw not in _VALID_TIER_QUERY_VALUES:
        if raw != _DEFAULT_TIER_QUERY_VALUE:
            st.query_params[_TIER_QUERY_PARAM] = _DEFAULT_TIER_QUERY_VALUE
        return _DEFAULT_TIER_QUERY_VALUE
    return raw


def _handle_tier_switch_change(widget_key: str) -> None:
    """The segmented control's own on_change callback — reads the
    widget's just-updated session-state value (Streamlit sets this
    before invoking on_change) and writes it back to the canonical
    `tier` query parameter, the single source of truth. The guard
    mirrors _resolve_active_tier_view()'s own "only write if different"
    discipline; a genuine click always differs, so this is defense-in-
    depth, not a load-bearing condition here."""
    selected_label = st.session_state[widget_key]
    new_value = _TIER_SWITCH_VALUE_BY_LABEL[selected_label]
    if st.query_params.get(_TIER_QUERY_PARAM, "") != new_value:
        st.query_params[_TIER_QUERY_PARAM] = new_value


def _render_tier_switch(active_tier_query_value: str) -> None:
    """Two-option, URL-synchronized segmented control. Keyed off the
    currently-resolved tier value itself (not a fixed key) — this is
    what prevents stale widget session-state from ever overriding a
    freshly-read URL (e.g. the address bar edited directly to a
    different `tier` value): whenever the resolved tier changes, for
    any reason, Streamlit sees a brand-new widget key and starts that
    widget fresh at `default`, rather than reusing a stored selection
    from a previous, now-stale key."""
    widget_key = f"daily-news-tier-switch-{active_tier_query_value}"
    st.segmented_control(
        "View", options=list(_TIER_SWITCH_LABELS.values()),
        default=_TIER_SWITCH_LABELS[active_tier_query_value],
        key=widget_key, on_change=_handle_tier_switch_change, args=(widget_key,),
        label_visibility="collapsed",
    )


@dataclass(frozen=True)
class SignalsFeed:
    """The already-decided, already-ordered Signals feed for one company
    selection, partitioned by tier — exactly what render() below used to
    compute inline. Built by build_signals_feed(); shared with the
    sidebar count badge and the Dashboard's Latest signals module so
    every surface shows the same eligible items."""
    high_signal: tuple[tuple[str, NewsStory | EditorialStory], ...]
    watchlist: tuple[tuple[str, NewsStory | EditorialStory], ...]
    background: tuple[tuple[str, NewsStory | EditorialStory], ...]
    fallback_notice: str | None
    issuer_items_are_historical: bool


def build_signals_feed(settings: Settings, selected_company: str) -> SignalsFeed:
    """Redesign v2 — the selection composition render() has always
    performed, moved into one function without changing a single rule:
    each lane still applies its own existing, unmodified inclusion logic
    first (issuer: 7-day window + per-company stale-feed fallback;
    editorial: 72-hour window + per-source/total caps via
    get_visible_editorial_stories()/get_editorial_stories_for_company()),
    the two already-decided lists are merged via _merge_feed_items(), and
    the merged list is partitioned by _effective_tier() — never re-
    deriving or waiving either lane's own decision."""
    all_stories = _published_stories(settings)
    visible_editorial = get_visible_editorial_stories(settings)

    fallback_notice: str | None = None
    issuer_items_are_historical = False

    if selected_company == _ALL_COMPANIES_OPTION:
        issuer_items = _recent_stories(all_stories)
        editorial_items: list[EditorialStory] | tuple[EditorialStory, ...] = visible_editorial
    else:
        company_stories = _stories_for_company(all_stories, selected_company)
        recent = _recent_stories(company_stories)
        editorial_items = get_editorial_stories_for_company(settings, selected_company)
        if recent:
            issuer_items = recent
        elif company_stories:
            issuer_items = company_stories
            issuer_items_are_historical = True
            if not editorial_items:
                fallback_notice = (
                    f'<div class="er-muted">No {selected_company} official updates were published in the last 7 days. '
                    "Showing the latest available official updates.</div>"
                )
        else:
            issuer_items = []

    merged = _merge_feed_items(issuer_items, editorial_items)

    high_signal_items: list[tuple[str, NewsStory | EditorialStory]] = []
    watchlist_items: list[tuple[str, NewsStory | EditorialStory]] = []
    background_items: list[tuple[str, NewsStory | EditorialStory]] = []
    for kind, item in merged:
        tier = _effective_tier(item)
        if tier == NewsMaterialityTier.HIGH_SIGNAL:
            high_signal_items.append((kind, item))
        elif tier == NewsMaterialityTier.BACKGROUND:
            background_items.append((kind, item))
        else:
            watchlist_items.append((kind, item))
    return SignalsFeed(
        high_signal=tuple(high_signal_items), watchlist=tuple(watchlist_items), background=tuple(background_items),
        fallback_notice=fallback_notice, issuer_items_are_historical=issuer_items_are_historical,
    )


_HIGH_SIGNAL_COUNT_CACHE_TTL_SECONDS = 60


@st.cache_data(ttl=_HIGH_SIGNAL_COUNT_CACHE_TTL_SECONDS, show_spinner=False)
def _high_signal_count_cached(cache_dir: str, backend: str, _settings: Settings) -> int:
    # `_settings` is underscore-prefixed (never hashed — it can carry a DSN);
    # cache_dir/backend are the only cache-identity inputs, mirroring
    # radar_inbox._load_dashboard_snapshot's own convention.
    return len(build_signals_feed(_settings, _ALL_COMPANIES_OPTION).high_signal)


def high_signal_count(settings: Settings) -> int:
    """Real count of currently eligible High Signals ("All companies"
    view) — the same items the Signals page itself lists — for the
    sidebar badge and Dashboard tile. Cached 60s (this runs on every page
    load site-wide); fails closed to 0 so global chrome never raises on a
    Signals-backend problem."""
    try:
        return _high_signal_count_cached(str(settings.cache_dir), (settings.db_backend or "json"), settings)
    except Exception:  # noqa: BLE001 — fail closed, see docstring
        return 0


def _effective_tier(item: NewsStory | EditorialStory) -> NewsMaterialityTier:
    """None (materiality_tier's own default — see NewsMaterialityTier's
    docstring) means "persisted before this field existed, never
    reclassified" — a display-only, never-persisted safe default of
    Watchlist: shown, not silently hidden like Background, but never
    overclaimed as confidently material like High Signal either. The
    stored record itself is never written back to or modified here."""
    return item.materiality_tier or NewsMaterialityTier.WATCHLIST


def _tier_badge_html(tier: NewsMaterialityTier) -> str:
    css_class = _TIER_BADGE_CLASS.get(tier, "er-tag-neutral")
    return f'<span class="er-status-tag {css_class}">{tier.value}</span>'


def _published_stories(settings: Settings) -> list[NewsStory]:
    stories = daily_news_backend.get_daily_news_repository(settings).load_stories()
    # Dashboard/Signals quality fix (design/
    # DASHBOARD_SIGNAL_QUALITY_FIX_DESIGN.md): read-time reconciliation
    # — collapses a cross-language localized-duplicate pair (e.g. an
    # English/French pair for the same company event) down to its one
    # preferred (English/global) card. Every story this excludes stays
    # fully intact in the underlying store; only what this page renders
    # changes. A no-op whenever no such pair exists in `stories`.
    # select_canonical_stories() takes no TranslationProvider — it only
    # ever reads an already-cached translation (translation_service.
    # get_cached_translation), never triggers a live translation request
    # during render; see that function's own docstring.
    canonical = daily_news_pipeline.select_canonical_stories(stories, settings.cache_dir)
    published = [s for s in canonical.values() if s.status == NewsStoryStatus.PUBLISHED]
    return sorted(published, key=lambda s: s.sources[0].published_at if s.sources else "", reverse=True)


def _company_options() -> list[str]:
    """System-wide company-matched-news fix (design/DECISIONS.md): every
    company in the Daily News company universe is selectable — not only
    companies that already happen to have a persisted issuer story.
    Most tracked companies have no official IR/newsroom feed at all and
    depend entirely on qualified editorial matching for any coverage;
    this selector must offer them too, or they can never be viewed
    regardless of how good that matching is. See
    company_aliases.daily_news_company_names()'s own docstring for the
    exact, data-driven universe definition."""
    return [_ALL_COMPANIES_OPTION] + list(daily_news_company_names())


def _stories_for_company(stories: list[NewsStory], company_name: str) -> list[NewsStory]:
    return [s for s in stories if s.company_name == company_name]


def _feed_sort_key(kind: str, item: NewsStory | EditorialStory) -> str:
    if kind == "issuer":
        return item.sources[0].published_at if item.sources else ""
    return item.published_at


def _merge_feed_items(
    issuer_items: list[NewsStory], editorial_items: list[EditorialStory] | tuple[EditorialStory, ...],
) -> list[tuple[str, NewsStory | EditorialStory]]:
    """Presentation-only merge (design/DECISIONS.md, unified Daily News
    feed) — combines two already-independently-filtered lists (issuer's
    own 7-day/fallback rules already applied; editorial's own 72-hour/
    cap rules already applied) into one reverse-chronological list, by
    each item's own type-appropriate published_at field. Never re-derives
    or waives either lane's own freshness/cap decision — this function
    only ever reorders items both lanes already decided to show."""
    tagged: list[tuple[str, NewsStory | EditorialStory]] = (
        [("issuer", s) for s in issuer_items] + [("editorial", s) for s in editorial_items]
    )
    tagged.sort(key=lambda pair: _feed_sort_key(*pair), reverse=True)
    return tagged


def _elapsed_seconds(published_at: str, now: datetime) -> float | None:
    try:
        dt = datetime.fromisoformat(published_at)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds()


def _is_recent(story: NewsStory, now: datetime, window_days: int = _FRESHNESS_WINDOW_DAYS) -> bool:
    """Rolling, inclusive window compared in UTC — a story exactly
    window_days*24 hours old is included; anything older is not. Uses
    exact elapsed seconds rather than formatting.days_ago()'s own
    floor-to-integer-days rounding, which would otherwise let a story
    up to nearly window_days+1 calendar days old slip through."""
    if not story.sources or not story.sources[0].published_at:
        return False
    elapsed = _elapsed_seconds(story.sources[0].published_at, now)
    return elapsed is not None and elapsed <= window_days * 86400


def _recent_stories(stories: list[NewsStory], now: datetime | None = None) -> list[NewsStory]:
    now = now or datetime.now(timezone.utc)
    return [s for s in stories if _is_recent(s, now)]


# Dashboard/Signals quality fix (design/
# DASHBOARD_SIGNAL_QUALITY_FIX_DESIGN.md) — reuses the exact same
# on-demand, cached, click-triggered translate pattern already proven
# end-to-end for Dashboard filing rows (recently_updated.py's own
# _do_translate/_can_translate/session-state convention), extended to
# this page's issuer-lane headline. Kept as this page's own small,
# local copy (French added) rather than importing recently_updated.py's
# private constant — matches this codebase's own established precedent
# of each component keeping its own copy of a small shared helper (see
# that module's own docstring for why).
_LANGUAGE_CODE_BY_ORIGINAL_LANGUAGE: dict[str, str] = {"Korean": "KO", "Japanese": "JA", "French": "FR"}


def _can_translate_headline(original_language: str) -> bool:
    return original_language in _LANGUAGE_CODE_BY_ORIGINAL_LANGUAGE


def _translated_headline_key(story_id: str) -> str:
    return f"signals-translated-headline-{story_id}"


def _translate_failed_key(story_id: str) -> str:
    return f"signals-translate-failed-{story_id}"


def _show_translation_key(story_id: str) -> str:
    return f"signals-show-translation-{story_id}"


def _do_translate_headline(story: NewsStory, settings: Settings) -> None:
    """The one and only place this page ever calls the translation
    provider — inside a button's on_click handler, never on page load.
    Translates ONLY the headline/title text (story.headline) — never
    eeva_summary, never any generated "why it matters" text; a failure
    is recorded as a concise, non-blocking flag, never raised into the
    page."""
    source = story.sources[0]
    provider = DeepLProvider(settings.translation_api_key)
    lang_code = _LANGUAGE_CODE_BY_ORIGINAL_LANGUAGE[source.original_language]
    attempt = translation_service.translate_cached_with_outcome(
        provider, document_id=f"signals-headline:{story.id}", text=story.headline,
        cache_dir=settings.cache_dir, source_lang=lang_code,
    )
    if attempt.translation is not None:
        st.session_state[_translated_headline_key(story.id)] = attempt.translation.translated_text
        st.session_state[_show_translation_key(story.id)] = True
    else:
        st.session_state[_translate_failed_key(story.id)] = True


def _toggle_show_translation(story_id: str) -> None:
    key = _show_translation_key(story_id)
    st.session_state[key] = not st.session_state.get(key, False)


def _render_card(
    story: NewsStory, settings: Settings, is_historical: bool = False, tier: NewsMaterialityTier | None = None,
) -> None:
    # Text-only layout for every card, regardless of whether a validated
    # image_url/image_alt exists on the story — optional source-image
    # rendering is disabled for now (UI decision; the underlying
    # extraction/validation/storage of those fields is untouched, see
    # rss_atom_client.py / canonical_url.validate_image_url() /
    # daily_news_models.NewsSourceReference).
    source = story.sources[0]
    local_time = fmt_datetime_local(source.published_at) if source.published_at else ""
    # Source-attribution pass (design/DECISIONS.md): resolved only from
    # the story's own already-typed source_class — an unrecognized
    # enum-shaped value (should never occur; every SourceClass member is
    # mapped above) degrades to the raw stored value rather than
    # inventing a label or crashing the card.
    source_type_label = _SOURCE_CLASS_LABELS.get(source.source_class, source.source_class.value)

    with st.container(border=True, key=f"card-issuer-{story.id}"):
        tier_badge = f" {_tier_badge_html(tier)}" if tier is not None else ""
        st.markdown(f'<span class="er-status-tag er-tag-neutral">Company news</span>{tier_badge}', unsafe_allow_html=True)
        if is_historical:
            # System-wide company-matched-news fix (design/DECISIONS.md),
            # requirement 6: an older official card shown via the
            # stale-feed fallback must be unmistakably labeled as such,
            # not only via a page-level notice that can scroll out of
            # view or be suppressed entirely when fresh editorial
            # coverage is also shown (see render()'s own fallback_notice
            # logic below). Reuses the existing "genuinely incomplete,
            # not wrong" dashed/outline treatment already established
            # for this exact purpose (see radar_status.py's own
            # RETRIEVAL_FAILURE_STATUSES comment) — never the loud
            # er-tag-neg pill, which this codebase reserves for genuine
            # failures.
            st.markdown(
                '<span class="er-chip er-chip-uncertainty" style="margin-left:0.4rem;">'
                "Historical — not from the last 7 days</span>",
                unsafe_allow_html=True,
            )
        st.markdown(
            f'<div class="er-muted" style="margin-top:0.3rem;">{story.company_name} · {source.publisher} · {source_type_label} · {local_time}</div>',
            unsafe_allow_html=True,
        )
        headline = story.original_title if story.translation_unavailable else story.headline
        st.markdown(f"**{headline}**")

        if story.translation_unavailable:
            st.caption("Translation unavailable — original text shown above.")
        elif story.eeva_summary:
            st.write(story.eeva_summary)

        # Dashboard/Signals quality fix (design/
        # DASHBOARD_SIGNAL_QUALITY_FIX_DESIGN.md): a clear source-
        # language indicator plus an on-demand, clearly-labeled
        # Translate control for a Latin-script non-English headline
        # (translation_unavailable/original_title above already handle
        # the separate, unrelated CJK/Hangul-script case). Never shown
        # for English content. The translated text — when shown — is
        # always explicitly labeled "Title translation," a translation
        # of the headline only, never presented as source-original text
        # and never turned into a summary/interpretation.
        if _can_translate_headline(source.original_language):
            st.caption(f"Source language: {source.original_language}")
            translated_headline = st.session_state.get(_translated_headline_key(story.id))
            show_translation = st.session_state.get(_show_translation_key(story.id), False)
            if translated_headline:
                if show_translation:
                    st.markdown(
                        f'<div class="er-muted" style="font-size:0.82rem;">'
                        f'Title translation: {html.escape(translated_headline)}</div>',
                        unsafe_allow_html=True,
                    )
                label = "Hide translation" if show_translation else "Show translation"
                st.button(label, key=f"signals-toggle-translation-{story.id}", on_click=_toggle_show_translation, args=(story.id,))
            elif st.session_state.get(_translate_failed_key(story.id)):
                st.markdown('<div class="er-muted" style="font-size:0.76rem;">Translation unavailable</div>', unsafe_allow_html=True)
            else:
                st.button("Translate", key=f"signals-translate-{story.id}", on_click=_do_translate_headline, args=(story, settings))

        st.markdown(f"[Read original source →]({source.url})")


def render() -> None:
    """Unified Signals feed (design/DECISIONS.md): one page, one
    "Signals" heading, issuer and editorial stories interleaved into
    one reverse-chronological list. Each lane applies its own existing,
    unmodified rules first (issuer: 7-day window + per-company stale-feed
    fallback; editorial: 72-hour window + per-source/total caps, via
    get_visible_editorial_stories()) — only the already-decided display
    lists are merged, via _merge_feed_items(), never the underlying
    filtering logic itself."""
    settings = get_settings()

    active_tier_view = _resolve_active_tier_view()

    st.markdown('<div class="er-page-title">Signals</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="er-muted">{_SUBTITLE}</div>', unsafe_allow_html=True)

    _render_tier_switch(active_tier_view)

    selected_company = st.selectbox("Companies", options=_company_options(), index=0)

    st.markdown(
        '<div class="er-muted">Showing tracked coverage from the past 7 days.</div>',
        unsafe_allow_html=True,
    )

    # Redesign v2: the selection composition lives in build_signals_feed()
    # (same rules, same order, same tier partition — see its docstring);
    # render() only consumes the already-decided feed.
    feed = build_signals_feed(settings, selected_company)
    issuer_items_are_historical = feed.issuer_items_are_historical

    if not feed.high_signal and not feed.watchlist and not feed.background:
        # No persisted PUBLISHED story and no qualified editorial
        # coverage at all for this company — a true, honest empty
        # state, never implying older coverage exists.
        if selected_company == _ALL_COMPANIES_OPTION:
            empty_state("No recent company updates in the last 7 days.")
        else:
            empty_state(f"No recent official or qualified market coverage for {selected_company} right now.")
        return

    if feed.fallback_notice:
        st.markdown(feed.fallback_notice, unsafe_allow_html=True)

    high_signal_items = list(feed.high_signal)
    watchlist_items = list(feed.watchlist)
    background_items = list(feed.background)

    def _render_item(kind: str, item: NewsStory | EditorialStory, tier: NewsMaterialityTier) -> None:
        if kind == "issuer":
            _render_card(item, settings, is_historical=issuer_items_are_historical, tier=tier)
        else:
            render_editorial_card(item, tier=tier)

    # High Signals / Watchlist tier navigation (design/DAILY_NEWS_HIGH_
    # SIGNALS_WATCHLIST_IMPLEMENTATION_PLAN_2026_09_16.md) — the three
    # buckets above are computed unconditionally, exactly as before;
    # only which section(s) render below now depends on active_tier_view.
    # Background is reachable only from inside the Watchlist branch —
    # never rendered at all while High Signals is active, by construction
    # (not by an added exclusion check), so it can never appear there.
    if active_tier_view == _HIGH_SIGNALS_QUERY_VALUE:
        section_header(f"High Signals ({len(high_signal_items)})")
        if high_signal_items:
            for kind, item in high_signal_items:
                _render_item(kind, item, NewsMaterialityTier.HIGH_SIGNAL)
        else:
            # This is NOT the same as the true "zero coverage at all"
            # empty state above (which already returned): items exist
            # (in Watchlist/Background), just none reached the High
            # Signal bar, or none matched the current company filter.
            empty_state(_NO_HIGH_SIGNAL_EMPTY_STATE)
    else:
        section_header(f"Watchlist ({len(watchlist_items)})", "Relevant but early, unquantified, or not yet material.")
        if watchlist_items:
            for kind, item in watchlist_items:
                _render_item(kind, item, _effective_tier(item))
        else:
            empty_state(_NO_WATCHLIST_EMPTY_STATE)

        if background_items:
            # Retained, never deleted — accessible only through this
            # explicit control, excluded from the primary Watchlist list
            # above, and reachable only from within Watchlist itself
            # (design/DECISIONS.md; design/DAILY_NEWS_HIGH_SIGNALS_
            # WATCHLIST_IMPLEMENTATION_PLAN_2026_09_16.md).
            with st.expander(f"Show Background ({len(background_items)})"):
                for kind, item in background_items:
                    _render_item(kind, item, NewsMaterialityTier.BACKGROUND)
