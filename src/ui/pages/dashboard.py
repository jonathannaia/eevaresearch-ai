"""Dashboard — answers one question: "what should I investigate now?"
(UX-refinement pass, design/DECISIONS.md). Not an archive of every module —
Today's Read, Theme Health, 2-3 Priority Signals, a compact Capital
Rotation snapshot, 2-3 Catalysts, and 1-2 Watchlist Changes only. Full
signal discovery lives on Signals, full watchlist management on
Watchlists, full rotation detail on each theme's Rotation tab.
"""
from __future__ import annotations

import streamlit as st

from src.data_access.container import get_repositories
from src.logic.formatting import fmt_pct
from src.logic.theme_metrics import leaders_and_laggards, rank_by_performance
from src.logic.unread import is_unread
from src.logic.watchlist_risk import is_moving_against_thesis
from src.models.models import Direction
from src.ui.components.badges import direction_rail_class, direction_status_tag_html
from src.ui.components.cards import catalyst_timeline_row, priority_signal_row
from src.ui.components.freshness import freshness_chip
from src.ui.components.section import section_header
from src.ui.pages.watchlists import WATCHLIST_NAMES, seed_watchlists
from src.ui.ui import LAST_SEEN_KEY, READ_IDS_KEY, get_page

PRIORITY_SIGNAL_COUNT = 3
WATCHLIST_ALERT_COUNT = 2


def _infer_direction(relative_performance_pct: float) -> Direction:
    if relative_performance_pct > 2:
        return Direction.IMPROVING
    if relative_performance_pct < -2:
        return Direction.WEAKENING
    return Direction.MIXED


def _render_todays_read(ctx) -> None:
    """One evidence-labeled editorial paragraph derived from existing
    rotation-metrics logic — the thing a new user reads first."""
    themes = {t.slug: t for t in ctx.theme_repository.get_all_themes()}
    metrics = ctx.market_data_provider.get_rotation_metrics()
    ranked = rank_by_performance(metrics)
    # Phase B (UI audit): a locally-scoped, slightly heavier label — this
    # and Priority Signals are the page's two primary modules — rather
    # than editing the shared section_header() component, which every
    # other section on every other page also uses.
    st.markdown(
        '<div class="er-section-label" style="color:var(--text); font-weight:600; font-size:0.92rem;">Today\'s Read</div>',
        unsafe_allow_html=True,
    )
    with st.container(border=True, key="card-todays-read"):
        if not ranked or ranked[0].theme_slug not in themes:
            st.markdown('<div class="er-muted">Not enough sample data yet to build a read.</div>', unsafe_allow_html=True)
            return
        leader = themes[ranked[0].theme_slug]
        breadth_note = "broad participation" if ranked[0].breadth_pct >= 60 else "breadth that's still building"
        second_line = ""
        if len(ranked) > 1 and ranked[1].theme_slug in themes:
            second = themes[ranked[1].theme_slug]
            second_line = f" {second.name} follows; the next question is whether that leadership broadens or stays concentrated in {leader.name}."
        st.markdown(
            f'<div style="line-height:1.6;">{leader.name} leads this sample snapshot on relative performance '
            f'({fmt_pct(ranked[0].relative_performance_pct)}) with {ranked[0].breadth_pct:.0f}% breadth — {breadth_note}.'
            f'{second_line}</div>',
            unsafe_allow_html=True,
        )
        # Separation from the sentence above (so these read as distinct
        # actions, not a continuation of the paragraph) comes from the
        # cta-tertiary wrapper's own top margin in styles.css.
        # Phase B (UI audit): these were plain prose anchors and read as
        # continuation of the paragraph above rather than actions. Same
        # hrefs/fragment-jump/container keys — only the anchor's own
        # inline styling changed, to visually match the secondary-pill
        # treatment already defined in assets/styles.css (same tokens:
        # var(--hairline-2), var(--text), 999px radius), since that CSS
        # targets Streamlit's own stPageLink/stBaseButton elements, not a
        # raw markdown <a>.
        _secondary_pill_style = (
            "display:inline-flex; align-items:center; justify-content:center; "
            "box-sizing:border-box; min-height:2.5rem; padding:0.5rem 1.1rem; "
            "border:1px solid var(--hairline-2); border-radius:999px; "
            "color:var(--text); font-size:0.85rem; font-weight:600; "
            "text-decoration:none; white-space:nowrap;"
        )
        link_cols = st.columns([2, 2, 1], gap="medium")
        with link_cols[0]:
            with st.container(key="cta-tertiary-read-rotation"):
                st.markdown(
                    f'<a href="#capital-rotation-snapshot" style="{_secondary_pill_style}">Open Capital Rotation →</a>',
                    unsafe_allow_html=True,
                )
        with link_cols[1]:
            with st.container(key="cta-tertiary-read-signals"):
                st.markdown(
                    f'<a href="#priority-signals" style="{_secondary_pill_style}">Review priority signals →</a>',
                    unsafe_allow_html=True,
                )


def _render_theme_health(ctx) -> None:
    section_header("Theme Health")
    themes = ctx.theme_repository.get_all_themes()
    metrics = {m.theme_slug: m for m in ctx.market_data_provider.get_rotation_metrics()}
    themes_page = get_page("themes")
    cols = st.columns(min(len(themes), 5) or 1)
    rail_var = {"er-rail-pos": "var(--pos)", "er-rail-neg": "var(--neg)", "er-rail-mix": "var(--mix)"}
    for i, (col, theme) in enumerate(zip(cols, themes)):
        metric = metrics.get(theme.slug)
        with col:
            key = f"card-breadth-{theme.slug}"
            if metric is not None:
                # Thin top rail carrying the same restrained direction
                # color as the status tag below it — a second, faster scan
                # cue (usability follow-up) alongside the existing pill,
                # not a replacement for it.
                color = rail_var[direction_rail_class(_infer_direction(metric.relative_performance_pct))]
                st.markdown(
                    f'<style>.st-key-{key} {{ border-top: 2px solid {color} !important; }}</style>',
                    unsafe_allow_html=True,
                )
            with st.container(border=True, key=key):
                st.markdown(f'<div class="er-metric-label">{theme.name}</div>', unsafe_allow_html=True)
                if metric is None:
                    st.markdown('<div class="er-muted">No data.</div>', unsafe_allow_html=True)
                    continue
                st.markdown(f'<div class="er-metric-value">{fmt_pct(metric.relative_performance_pct)}</div>', unsafe_allow_html=True)
                st.markdown(
                    f'<div class="er-muted" style="margin-top:var(--space-1);">{metric.breadth_pct:.0f}% breadth</div>',
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f'<div class="bar" style="margin-top:var(--space-3);">'
                    f'<i style="--w:{metric.breadth_pct:.0f}%; animation-delay:{i * 40}ms;"></i></div>',
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f'<div style="margin-top:var(--space-2);">{direction_status_tag_html(_infer_direction(metric.relative_performance_pct))}</div>',
                    unsafe_allow_html=True,
                )
                # Only real if it opens Themes — it does (page_link, not a
                # decorative click target).
                if themes_page is not None:
                    with st.container(key=f"cta-tertiary-health-{theme.slug}"):
                        st.page_link(themes_page, label=f"Explore {theme.name} →")


def _render_priority_signals(ctx) -> None:
    st.markdown('<div id="priority-signals"></div>', unsafe_allow_html=True)
    # Phase B (UI audit): same heavier local emphasis as Today's Read
    # above — this page's two primary modules.
    st.markdown(
        '<div class="er-section-label" style="color:var(--text); font-weight:600; font-size:0.92rem;">Priority Signals</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="er-muted" style="font-size:0.78rem; margin:-0.3rem 0 0.6rem 0;">'
        "Highest-conviction sample signals by direction, strength, and evidence.</div>",
        unsafe_allow_html=True,
    )
    signals = ctx.signal_repository.get_all_signals()
    if not signals:
        st.caption("No signals loaded.")
        return

    prev_last_seen = st.session_state.get(LAST_SEEN_KEY)
    read_ids = st.session_state.setdefault(READ_IDS_KEY, set())
    strength_rank = {"Strong": 2, "Moderate": 1, "Weak": 0}

    unread_signals = [s for s in signals if is_unread(s, prev_last_seen, read_ids)]
    ranked_rest = sorted(
        (s for s in signals if s not in unread_signals),
        key=lambda s: strength_rank.get(s.strength.value, 0), reverse=True,
    )
    priority = (unread_signals + ranked_rest)[:PRIORITY_SIGNAL_COUNT]

    themes = {t.slug: t.name for t in ctx.theme_repository.get_all_themes()}
    for i, s in enumerate(priority, start=1):
        priority_signal_row(s, evidence_repository=ctx.evidence_repository, theme_name=themes.get(s.theme_slug), order=i)

    signals_page = get_page("signals")
    if signals_page is not None:
        with st.container(key="cta-tertiary-dashboard-signals"):
            st.page_link(signals_page, label="View all signals →")


def _render_watchlist_changes(ctx) -> None:
    # Phase B (UI audit): visually secondary relative to Today's Read /
    # Priority Signals above — lighter weight/opacity only, same label
    # text and position, not reordered or hidden.
    st.markdown(
        '<div class="er-section-label" style="opacity:0.7;">Watchlist Changes</div>',
        unsafe_allow_html=True,
    )
    if "watchlists" not in st.session_state:
        st.session_state["watchlists"] = seed_watchlists()
    lists = st.session_state["watchlists"]
    entries = [(name, e) for name in WATCHLIST_NAMES for e in lists.get(name, [])]
    signals = ctx.signal_repository.get_all_signals()
    against_entries = [(name, e) for name, e in entries if is_moving_against_thesis(e.ticker_symbol, signals) and e.invalidates_if]

    if not against_entries:
        st.markdown('<div class="er-muted">No watchlist names are moving against thesis right now.</div>', unsafe_allow_html=True)
    for list_name, e in against_entries[:WATCHLIST_ALERT_COUNT]:
        st.markdown(
            f'<div class="er-row" style="border-bottom:none; padding-bottom:var(--space-1);">'
            f'<a href="company?symbol={e.ticker_symbol}" class="er-mono" '
            f'style="color:var(--text); text-decoration:underline;">{e.ticker_symbol}</a> '
            f'<span class="er-muted" style="margin-left:var(--space-2);">{list_name}</span></div>'
            # Muted-rose left rail + tinted background (the same --neg-dim
            # tint already used for status-tag pills) plus a small rose
            # dot on the label — readable at any width, not large/bright.
            f'<div class="er-alert-neg" style="margin-bottom:var(--space-4);">'
            f'<div class="er-alert-neg-label">Moving against thesis</div>'
            f'<div class="er-alert-neg-note">You wrote: "{e.invalidates_if}"</div></div>',
            unsafe_allow_html=True,
        )

    watchlists_page = get_page("watchlists")
    if watchlists_page is not None:
        with st.container(key="cta-tertiary-open-watchlists"):
            st.page_link(watchlists_page, label="Open Watchlists →")


def _render_rotation_snapshot(ctx) -> None:
    st.markdown('<div id="capital-rotation-snapshot"></div>', unsafe_allow_html=True)
    section_header("Capital Rotation")
    st.markdown(
        '<div class="er-muted" style="font-size:0.78rem; margin:-0.3rem 0 0.6rem 0;">'
        "Relative performance · sample data</div>",
        unsafe_allow_html=True,
    )
    themes = {t.slug: t for t in ctx.theme_repository.get_all_themes()}
    metrics = ctx.market_data_provider.get_rotation_metrics()
    ranked = rank_by_performance(metrics)
    if not ranked:
        st.caption("No rotation data loaded.")
        return
    max_abs = max(abs(m.relative_performance_pct) for m in ranked) or 1
    for m in ranked:
        if m.theme_slug not in themes:
            continue
        pct = m.relative_performance_pct
        half_width = min(abs(pct) / max_abs * 50, 50)
        # Restrained semantic color (not just neutral grey) so positive vs.
        # negative reads at a glance, matching the pos/neg direction system
        # used elsewhere (usability follow-up) — magnitude still carries
        # the bar length, color only adds direction legibility.
        bar_color = "var(--pos)" if pct >= 0 else "var(--neg)"
        side_style = (
            f"left:50%; width:{half_width:.1f}%; background:{bar_color};" if pct >= 0
            else f"right:50%; width:{half_width:.1f}%; background:{bar_color};"
        )
        st.markdown(
            f"""
            <div class="er-divbar-row">
                <div class="er-divbar-label">{themes[m.theme_slug].name}</div>
                <div class="er-divbar-track">
                    <div class="er-divbar-zero"></div>
                    <div class="er-divbar-fill" style="{side_style}"></div>
                </div>
                <div class="er-divbar-value">{fmt_pct(pct)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    leaders, laggards = leaders_and_laggards(metrics, top_n=1)
    if leaders and laggards and leaders[0].theme_slug in themes and laggards[0].theme_slug in themes:
        st.markdown(
            f'<div class="er-muted" style="font-size:0.8rem; margin-top:var(--space-3);">Leading: <strong>{themes[leaders[0].theme_slug].name}</strong> '
            f'· Lagging: <strong>{themes[laggards[0].theme_slug].name}</strong></div>',
            unsafe_allow_html=True,
        )
    themes_page = get_page("themes")
    if themes_page is not None:
        with st.container(key="cta-tertiary-dashboard-rotation"):
            st.page_link(themes_page, label="See full rotation →")


def _render_catalysts(ctx) -> None:
    section_header("Next Catalysts")
    upcoming = ctx.catalyst_repository.get_upcoming_catalysts(limit=3)
    if not upcoming:
        st.caption("No catalysts scheduled.")
    else:
        for c in upcoming:
            catalyst_timeline_row(c)


def render() -> None:
    ctx = get_repositories()

    header_cols = st.columns([4, 2])
    with header_cols[0]:
        st.markdown('<div class="er-page-title">Market Overview</div>', unsafe_allow_html=True)
    with header_cols[1]:
        st.markdown('<div style="text-align:right; margin-top:0.3rem;">', unsafe_allow_html=True)
        freshness_chip("demo", key="fresh-dashboard-head")
        st.markdown("</div>", unsafe_allow_html=True)
    st.markdown(
        '<div class="er-page-subtitle">Theme leadership, signals, catalysts, and watchlist changes.</div>',
        unsafe_allow_html=True,
    )

    _render_todays_read(ctx)
    st.divider()
    _render_theme_health(ctx)
    st.divider()
    _render_priority_signals(ctx)
    st.divider()
    _render_rotation_snapshot(ctx)
    st.divider()
    _render_catalysts(ctx)
    st.divider()
    _render_watchlist_changes(ctx)
