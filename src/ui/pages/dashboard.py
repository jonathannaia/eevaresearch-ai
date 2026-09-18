"""Dashboard — answers one question: "what should I investigate now?"
(UX-refinement pass, design/DECISIONS.md; redesign v2 layout).

Reader-facing data-integrity pass (design/DECISIONS.md): every module on
this page renders only from a real, live, source-backed data source, or
does not render at all. Nothing is invented to fill a slot. The exact
real data source powering each module:

  - Greeting / date: the signed-in Google account's own `name` claim
    (st.user — the same claim feedback.py already reads) and
    today_local() in the app's one display timezone (Eastern).
  - Summary tiles: High Signals = src.ui.pages.daily_news.high_signal_
    count() — the exact items the Signals page lists; Theme items =
    the same 14-day rollup Theme Activity renders, totalled across every
    theme; Active theses = backend_factory.get_theme_repository().
    list_published_themes() with real evidence/company counts; Filings
    refreshed = the worker's durable ProviderScanStatus via the sidebar's
    own _latest_filings_refresh_label(), omitted when unavailable.
  - Latest signals: daily_news.build_signals_feed() ("All companies",
    High Signal tier only) — never a separate selection.
  - Theme activity: src/ui/components/recent_theme_activity.py (the two
    real repositories, re-aggregated by theme_slug; NOT_MATERIAL filings
    excluded via src.logic.filing_visibility).
  - Research theses: the published-only ThemeRepositoryProtocol — the
    exact read path src/ui/pages/themes_research.py uses, so an internal
    or candidate Theme can never appear here; real evidence-direction
    and distinct-company counts only, never a price/breadth statistic.
  - Regional Brief: backend_factory.get_filing_event_repository() per
    jurisdiction, NOT_MATERIAL filings excluded; an explicit "not
    connected yet" state for China.
  - Recently Updated / Radar Signals / Policy Developments: unchanged
    modules (see their own docstrings), kept below the fold.

The Dashboard never surfaces a suppressed / NOT_MATERIAL filing.
"""
from __future__ import annotations

import html
from collections import Counter

import streamlit as st

from src.config.settings import get_settings
from src.data_access import backend_factory
from src.data_access.container import get_repositories
from src.logic.formatting import fmt_datetime_local, fmt_long_date, fmt_time_local, today_local
from src.logic.unread import is_unread
from src.ui.components.cards import priority_signal_row
from src.ui.components.policy_developments import render_policy_developments
from src.ui.components.primitives import (
    EVIDENCE_DIRECTIONS,
    chip_html,
    esc,
    evidence_bar_html,
    eyebrow_html,
    summary_tile,
    theme_dot_html,
)
from src.ui.components.recent_theme_activity import load_theme_activity_rows, render_recent_theme_activity
from src.ui.components.recently_updated import render_recently_updated
from src.ui.components.regional_brief import render_regional_brief
from src.ui.components.section import section_header
from src.ui.ui import LAST_SEEN_KEY, READ_IDS_KEY, _latest_filings_refresh_label, get_page

PRIORITY_SIGNAL_COUNT = 3

_MAX_THEME_HEALTH_CARDS = 5
_LATEST_SIGNALS_COUNT = 5
_SUBTITLE = "What moved across tracked coverage."


def _esc(value: object) -> str:
    if value is None:
        return ""
    return html.escape(str(value))


def _enum_label(value: object) -> str:
    return _esc(getattr(value, "value", value))


# --- header -----------------------------------------------------------------

def _render_header() -> None:
    logged_in = getattr(st.user, "is_logged_in", False)
    name = (st.user.get("name") if logged_in else None) or ""
    first_name = name.strip().split(" ")[0] if name.strip() else ""
    greeting = f"Hello, {first_name}" if first_name else "Hello"

    left, right = st.columns([3, 2], vertical_alignment="center")
    with left:
        st.markdown(eyebrow_html(fmt_long_date(today_local())), unsafe_allow_html=True)
        st.markdown(f'<div class="er-greeting">{_esc(greeting)}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="er-page-subtitle" style="margin-bottom:0;">{_esc(_SUBTITLE)}</div>', unsafe_allow_html=True)
    with right:
        filings_page = get_page("radar_inbox")
        signals_page = get_page("daily_news")
        action_cols = st.columns([1, 1.2])
        if filings_page is not None:
            with action_cols[0], st.container(key="cta-secondary-dashboard-view-filings"):
                st.page_link(filings_page, label="View filings")
        if signals_page is not None:
            with action_cols[1], st.container(key="cta-primary-dashboard-review-high-signals"):
                st.page_link(signals_page, label="Review high signals →", query_params={"tier": "high_signal"})


# --- summary tiles ----------------------------------------------------------

def _published_theme_stats(settings) -> tuple[list, dict[str, tuple[tuple, tuple]]]:
    """Published themes plus each one's real evidence/company-map, read
    through the published-only protocol; ([], {}) on any failure."""
    try:
        repository = backend_factory.get_theme_repository(settings)
        themes = repository.list_published_themes()
    except Exception:  # noqa: BLE001 — best-effort supplementary module; never show a raw error on the dashboard
        return [], {}
    details: dict[str, tuple[tuple, tuple]] = {}
    for theme in themes:
        try:
            details[theme.id] = (tuple(repository.evidence_for_theme(theme.id)), tuple(repository.company_map_for_theme(theme.id)))
        except Exception:  # noqa: BLE001 — one theme's count lookup failing must not take down the row
            details[theme.id] = ((), ())
    return list(themes), details


def _render_summary_tiles(settings, high_signal_total: int, theme_rows, themes, theme_details) -> None:
    refresh_label = _latest_filings_refresh_label()
    tiles: list[tuple] = []

    tiles.append(("high-signals", "High signals · 7d", str(high_signal_total), "Flagged for review across tracked coverage", False))

    theme_total = sum(r.count for r in theme_rows)
    if theme_rows:
        top = max(theme_rows, key=lambda r: r.count)
        tiles.append(("theme-items", "Theme items · 14d", str(theme_total), f"{top.theme_name} accounts for {top.count}", False))
    else:
        tiles.append(("theme-items", "Theme items · 14d", "0", "No tracked activity in the last 14 days", False))

    evidence_total = sum(len(theme_details.get(t.id, ((), ()))[0]) for t in themes)
    companies = set()
    for t in themes:
        evidence, company_map = theme_details.get(t.id, ((), ()))
        companies |= {item.company for item in evidence} | {entry.company_name for entry in company_map}
    company_word = "company" if len(companies) == 1 else "companies"
    tiles.append(("active-theses", "Active theses", str(len(themes)), f"{evidence_total} evidence items across {len(companies)} {company_word}" if themes else "No published research theses yet", False))

    if refresh_label:
        # "Filings refreshed HH:MM EDT" -> value "HH:MM", detail "EDT · latest completed scan"
        parts = refresh_label.replace("Filings refreshed ", "").split(" ", 1)
        tiles.append(("filings-refreshed", "Filings refreshed", parts[0], f"{parts[1] if len(parts) > 1 else ''} · latest completed scan".strip(" ·"), True))

    cols = st.columns(len(tiles))
    for col, (key, label, value, detail, live) in zip(cols, tiles):
        with col:
            summary_tile(key, label, value, detail, live_dot=live)


# --- latest signals ---------------------------------------------------------

def _signal_row_html(kind: str, item) -> str:
    if kind == "issuer":
        source = item.sources[0]
        published_at, url = source.published_at, source.url
        headline = item.original_title if item.translation_unavailable else item.headline
        chips = chip_html(item.company_name, "theme") + '<span class="er-muted">Company news</span>'
    else:
        published_at, url = item.published_at, item.source_url
        headline = item.headline
        company_chips = "".join(chip_html(c, "theme") for c in item.matched_companies[:2])
        theme_chips = "".join(
            f'<span class="er-status-tag er-tag-theme">{theme_dot_html(t)}{_esc(t.replace("-", " ").title())}</span>'
            for t in item.matched_themes[:1]
        )
        chips = company_chips + theme_chips + f'<span class="er-muted">Market news · {_esc(item.publisher)}</span>'
    clock, zone = fmt_time_local(published_at) if published_at else ("", "")
    link = f'<a class="er-feed-link" href="{_esc(url)}" target="_blank" rel="noopener noreferrer" aria-label="Open source">↗</a>' if url else ""
    return (
        '<div class="er-feed-row">'
        f'<div class="er-time-cell">{_esc(clock)}<span class="er-time-zone">{_esc(zone)}</span></div>'
        f'<div><div class="er-feed-title">{_esc(headline)}</div><div class="er-feed-meta">{chips}</div></div>'
        f"<div>{link}</div></div>"
    )


def _render_latest_signals(feed) -> None:
    signals_page = get_page("daily_news")
    with st.container(key="card-latest-signals"):
        head_cols = st.columns([3, 1.4], vertical_alignment="center")
        with head_cols[0]:
            st.markdown(
                '<div class="er-split-head" style="margin:0;"><div class="er-section-label" style="margin:0;">Latest signals</div>'
                f'{chip_html("High signal", "pos", dot=True)}</div>',
                unsafe_allow_html=True,
            )
        with head_cols[1]:
            if signals_page is not None:
                with st.container(key="cta-tertiary-dashboard-latest-signals"):
                    st.page_link(signals_page, label="View all signals →", query_params={"tier": "high_signal"})
        items = list(feed.high_signal)[:_LATEST_SIGNALS_COUNT]
        if not items:
            st.markdown('<div class="er-muted">No High Signals in the past 7 days.</div>', unsafe_allow_html=True)
            return
        st.markdown("".join(_signal_row_html(kind, item) for kind, item in items), unsafe_allow_html=True)


# --- research theses --------------------------------------------------------

def _render_theme_health(settings, themes=None, theme_details=None) -> None:
    """Real data only, via the published-only ThemeRepositoryProtocol
    (see module docstring). Renders nothing — no header, no Themes link
    — unless at least one real published Theme exists. Shows each
    published Theme's own real evidence-direction counts and distinct-
    company count; never a price/breadth/performance statistic."""
    if themes is None or theme_details is None:
        themes, theme_details = _published_theme_stats(settings)
    if not themes:
        return

    section_header("Research theses")
    themes_page = get_page("themes")
    shown = themes[:_MAX_THEME_HEALTH_CARDS]
    for theme in shown:
        evidence, company_map = theme_details.get(theme.id, ((), ()))
        distinct_companies = {item.company for item in evidence} | {entry.company_name for entry in company_map}
        counts = Counter(getattr(item.direction, "value", item.direction) for item in evidence)
        company_word = "company" if len(distinct_companies) == 1 else "companies"
        with st.container(key=f"card-thesis-summary-{theme.id}"):
            st.markdown(
                f'<div>{chip_html(_enum_label(theme.category), "info")} {chip_html(_enum_label(theme.status), "neutral")}</div>'
                f'<div class="er-serif-title" style="margin-top:0.55rem;">{_esc(theme.title)}</div>'
                f'<div style="margin-top:0.7rem;">{evidence_bar_html({label: counts.get(label, 0) for label, _ in EVIDENCE_DIRECTIONS})}</div>'
                f'<div class="er-muted" style="margin-top:0.5rem;">{len(evidence)} evidence items · {len(distinct_companies)} {company_word} · '
                f"updated {_esc(fmt_datetime_local(theme.updated_at))}</div>",
                unsafe_allow_html=True,
            )
            if themes_page is not None:
                with st.container(key=f"cta-tertiary-health-{theme.id}"):
                    st.page_link(themes_page, label="Open thesis →", query_params={"theme_id": theme.id})
    remaining = len(themes) - len(shown)
    if remaining > 0 and themes_page is not None:
        with st.container(key="cta-tertiary-health-more"):
            st.page_link(themes_page, label=f"+{remaining} more — view all in Themes →")


# --- radar signals (unchanged module) ---------------------------------------

def _render_priority_signals(ctx) -> None:
    """Real signals only (ctx.signal_repository is backend_factory.
    get_signal_repository()'s RadarSignalRepository — see module
    docstring). Renders nothing at all — no header, no anchor — if zero
    real signals qualify, rather than a placeholder caption."""
    signals = ctx.signal_repository.get_all_signals()
    if not signals:
        return

    st.markdown('<div id="priority-signals"></div>', unsafe_allow_html=True)
    section_header("Radar Signals", "Highest-conviction real signals by direction, strength, and evidence.")

    prev_last_seen = st.session_state.get(LAST_SEEN_KEY)
    read_ids = st.session_state.setdefault(READ_IDS_KEY, set())
    strength_rank = {"Strong": 2, "Moderate": 1, "Weak": 0}

    unread_signals = [s for s in signals if is_unread(s, prev_last_seen, read_ids)]
    ranked_rest = sorted(
        (s for s in signals if s not in unread_signals),
        key=lambda s: strength_rank.get(s.strength.value, 0), reverse=True,
    )
    priority = (unread_signals + ranked_rest)[:PRIORITY_SIGNAL_COUNT]

    for i, s in enumerate(priority, start=1):
        priority_signal_row(s, order=i)

    signals_page = get_page("signals")
    if signals_page is not None:
        with st.container(key="cta-tertiary-dashboard-signals"):
            st.page_link(signals_page, label="View all signals →")


def _render_regional_brief(settings) -> None:
    render_regional_brief(settings)


def render() -> None:
    from src.ui.pages.daily_news import _ALL_COMPANIES_OPTION, build_signals_feed

    ctx = get_repositories()
    settings = get_settings()

    _render_header()

    try:
        feed = build_signals_feed(settings, _ALL_COMPANIES_OPTION)
    except Exception:  # noqa: BLE001 — a Signals-backend problem must never take down the dashboard
        feed = None
    high_signal_total = len(feed.high_signal) if feed is not None else 0
    theme_rows = load_theme_activity_rows(ctx, settings, max_rows=1000)
    themes, theme_details = _published_theme_stats(settings)

    _render_summary_tiles(settings, high_signal_total, theme_rows, themes, theme_details)

    main_col, side_col = st.columns([1.55, 1], gap="medium")
    with main_col:
        if feed is not None:
            _render_latest_signals(feed)
    with side_col:
        render_recent_theme_activity(ctx, settings, rows=theme_rows)
        _render_theme_health(settings, themes, theme_details)

    _render_regional_brief(settings)
    render_recently_updated(settings)
    _render_priority_signals(ctx)

    # Federal Register Policy Monitor Pilot (design/DECISIONS.md) — a
    # self-contained, source-specific pilot, deliberately not a Daily
    # News expansion; live, read-time, in-memory Federal Register fetch.
    render_policy_developments()

    # Open-beta feedback (design/DECISIONS.md) — one small, secondary
    # entry-point link to the hidden feedback page, placed last.
    feedback_page = get_page("feedback")
    if feedback_page is not None:
        st.divider()
        with st.container(key="cta-tertiary-dashboard-feedback"):
            st.page_link(feedback_page, label="Share feedback")
