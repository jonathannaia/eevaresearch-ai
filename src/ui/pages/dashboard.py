"""Dashboard — answers one question: "what should I investigate now?"
(UX-refinement pass, design/DECISIONS.md). Not an archive of every module —
Today's Read, Theme Health, 2-3 Priority Signals, a compact Capital
Rotation snapshot, 2-3 Catalysts, and 1-2 Watchlist Changes only. Full
signal discovery lives on Signals, full watchlist management on
Watchlists, full rotation detail on each theme's Rotation tab.

Phase C (editorial-simplicity pass, design/DECISIONS.md): the page-level
"Market Overview" title/subtitle is gone — the sidebar's own active-nav
highlight already establishes "you are on Dashboard," so repeating that in
a page title read as redundant admin-tool chrome. Today's Read is now the
first, strongest, and only "primary" module on the page (Priority Signals
reverts to the same supporting-section weight as Catalysts/Watchlist
Changes — it was briefly promoted to a second "primary" module in the
prior UI-audit pass, but this phase's product rule is one clear primary
element per page). The stacked full-width st.divider() rule between every
module is gone too, replaced by the vertical spacing every section header
already carries via --space-6 (assets/styles.css's own documented "32:
between major Dashboard modules" token) — removing the divider doesn't
remove any spacing, since that margin was never coming from the divider.
"""
from __future__ import annotations

import streamlit as st

from src.config.settings import get_settings
from src.data_access.container import get_repositories
from src.logic.formatting import fmt_date, fmt_pct
from src.logic.market_map import group_companies_by_theme
from src.logic.theme_metrics import leaders_and_laggards, rank_by_performance
from src.logic.unread import is_unread
from src.logic.watchlist_risk import is_moving_against_thesis
from src.models.models import Direction
from src.ui.components.badges import direction_dot_html
from src.ui.components.cards import catalyst_timeline_row, priority_signal_row
from src.ui.components.freshness import freshness_chip
from src.ui.components.market_map import render_market_map
from src.ui.components.regional_brief import render_regional_brief
from src.ui.components.section import section_header
from src.ui.pages.watchlists import WATCHLIST_NAMES, seed_watchlists
from src.ui.ui import LAST_SEEN_KEY, READ_IDS_KEY, get_page

PRIORITY_SIGNAL_COUNT = 3
WATCHLIST_ALERT_COUNT = 2

# Phase C: the two Today's Read actions must not read as visually
# identical outlined buttons — one filled primary pill, one quiet ghost
# text link. These are same-page fragment jumps (#capital-rotation-
# snapshot / #priority-signals), not real st.page_link targets, so the
# shared cta-primary-*/cta-tertiary-* CSS (which selects specifically for
# Streamlit's own stPageLink/stBaseButton descendants) can't reach a raw
# <a> automatically — the exact same declarations from those CSS rules
# are duplicated here inline instead, rather than inventing new colors.
_PRIMARY_PILL_STYLE = (
    "display:inline-flex; align-items:center; justify-content:center; "
    "box-sizing:border-box; min-height:2.5rem; padding:0.5rem 1.1rem; "
    "background:var(--invert-bg); color:var(--invert-fg); "
    "border:none; border-radius:999px; font-weight:600; font-size:0.85rem; "
    "text-decoration:none; white-space:nowrap; "
    "box-shadow: 0 0 0 1px rgba(16,42,67,.25), 0 0 22px var(--glow), 0 2px 10px rgba(16,42,67,.18);"
)
_GHOST_LINK_STYLE = (
    "display:inline-flex; align-items:center; box-sizing:border-box; min-height:2.5rem; "
    "color:var(--text-3); font-size:0.85rem; font-weight:500; "
    "text-decoration:none; white-space:nowrap;"
)


def _infer_direction(relative_performance_pct: float) -> Direction:
    if relative_performance_pct > 2:
        return Direction.IMPROVING
    if relative_performance_pct < -2:
        return Direction.WEAKENING
    return Direction.MIXED


def _render_todays_read(ctx) -> None:
    """One evidence-labeled editorial paragraph derived from existing
    rotation-metrics logic — the first, strongest, and only "primary"
    element on the page (Phase C). The card itself is the shared white
    card surface plus one added rule (.st-key-card-todays-read in
    assets/styles.css) giving it a restrained midnight-blue left-edge
    accent — no new fill, gradient, or one-off visual system."""
    themes = {t.slug: t for t in ctx.theme_repository.get_all_themes()}
    metrics = ctx.market_data_provider.get_rotation_metrics()
    ranked = rank_by_performance(metrics)

    head_cols = st.columns([4, 2])
    with head_cols[0]:
        st.markdown(
            '<div class="er-section-label" style="color:var(--text); font-weight:600; font-size:0.92rem;">Today\'s Read</div>',
            unsafe_allow_html=True,
        )
    with head_cols[1]:
        st.markdown('<div style="text-align:right; margin-top:0.3rem;">', unsafe_allow_html=True)
        freshness_chip("demo", key="fresh-dashboard-head")
        st.markdown("</div>", unsafe_allow_html=True)

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
        link_cols = st.columns([2, 2, 3], gap="medium")
        with link_cols[0]:
            with st.container(key="cta-primary-read-market-map"):
                st.markdown(
                    f'<a href="#market-map" style="{_PRIMARY_PILL_STYLE}">Explore Market Map →</a>',
                    unsafe_allow_html=True,
                )
        with link_cols[1]:
            with st.container(key="cta-tertiary-read-signals"):
                st.markdown(
                    f'<a href="#priority-signals" style="{_GHOST_LINK_STYLE}">Review priority signals →</a>',
                    unsafe_allow_html=True,
                )


def _render_theme_health(ctx) -> None:
    """Quick-scan card row, standardized to the shared card treatment
    (Phase C) — the previous per-card injected <style> for a colored top
    border was a one-off visual pattern not used anywhere else on the
    page/product; the direction glyph+text below already carries the same
    signal without it. The pill-style status tag is now a small semantic
    dot+text (direction_dot_html, the same shared component Themes/
    Company/Signals already use for this) instead of a tinted-background
    pill — same underlying direction data and terminology, less visual
    weight. "Breadth" gets its own small metric-label above the bar so the
    measure is named, not just implied by the number beside it."""
    section_header("Theme Health")
    themes = ctx.theme_repository.get_all_themes()
    metrics = {m.theme_slug: m for m in ctx.market_data_provider.get_rotation_metrics()}
    themes_page = get_page("themes")
    cols = st.columns(min(len(themes), 5) or 1)
    for i, (col, theme) in enumerate(zip(cols, themes)):
        metric = metrics.get(theme.slug)
        with col:
            key = f"card-breadth-{theme.slug}"
            with st.container(border=True, key=key):
                st.markdown(f'<div class="er-metric-label">{theme.name}</div>', unsafe_allow_html=True)
                if metric is None:
                    st.markdown('<div class="er-muted">No data.</div>', unsafe_allow_html=True)
                    continue
                st.markdown(f'<div class="er-metric-value">{fmt_pct(metric.relative_performance_pct)}</div>', unsafe_allow_html=True)
                st.markdown(
                    f'<div class="er-metric-label" style="margin-top:var(--space-2);">Breadth</div>'
                    f'<div class="er-muted" style="margin-top:var(--space-1);">{metric.breadth_pct:.0f}%</div>',
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f'<div class="bar" style="margin-top:var(--space-1);">'
                    f'<i style="--w:{metric.breadth_pct:.0f}%; animation-delay:{i * 40}ms;"></i></div>',
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f'<div style="margin-top:var(--space-2);">{direction_dot_html(_infer_direction(metric.relative_performance_pct))}</div>',
                    unsafe_allow_html=True,
                )
                # Only real if it opens Themes — it does (page_link, not a
                # decorative click target).
                if themes_page is not None:
                    with st.container(key=f"cta-tertiary-health-{theme.slug}"):
                        st.page_link(themes_page, label=f"Explore {theme.name} →")


def _render_priority_signals(ctx) -> None:
    """Lower-priority supporting section (Phase C) — same standard
    section-header weight as Capital Rotation/Catalysts, not the elevated
    "second primary module" treatment from the prior UI-audit pass; Today's
    Read is this page's one primary element."""
    st.markdown('<div id="priority-signals"></div>', unsafe_allow_html=True)
    section_header("Priority Signals", "Highest-conviction sample signals by direction, strength, and evidence.")
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
    # Lower-priority supporting section (Phase C, carried over from the
    # prior UI-audit pass) — lighter weight/opacity only, same label text
    # and position, not reordered or hidden.
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
    """Secondary, collapsed disclosure (Phase E1, design/
    DASHBOARD_MARKET_MAP_PHASE_E.md) — Capital Rotation is 100% static
    demo seed data (data/seed/rotation_metrics.json, fixed as_of
    2026-08-15 on every record) and must never read as "today" or
    "live". The panel_header's freshness chip is passed that real as_of
    date explicitly (not "now") so the truthful date is what a reader
    sees, not an implied current one."""
    themes = {t.slug: t for t in ctx.theme_repository.get_all_themes()}
    metrics = ctx.market_data_provider.get_rotation_metrics()
    ranked = rank_by_performance(metrics)
    as_of = fmt_date(ranked[0].as_of) if ranked else "2026-08-15"
    freshness_chip("demo", timestamp=as_of, key="fresh-rotation-snapshot")
    st.markdown('<div class="er-muted" style="margin-top:0.2rem;">Relative performance · sample data, not current</div>', unsafe_allow_html=True)
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


def _render_market_map(ctx) -> None:
    """Primary visual/research module (Phase E1, design/
    DASHBOARD_MARKET_MAP_PHASE_E.md) — a theme-grouped navigator over
    Eeva's tracked-company universe (src/config/tracked_companies.py, the
    one authoritative source — see src/logic/market_map.py). No price,
    quote, or movement data exists anywhere in this build, so every tile
    and the map itself say "Price coverage not connected" rather than a
    dash, blank space, or invented value."""
    themes = ctx.theme_repository.get_all_themes()
    companies_by_theme = group_companies_by_theme([t.slug for t in themes])
    render_market_map(ctx, themes, companies_by_theme)


def _render_regional_brief(settings) -> None:
    """Compact supporting module beneath the Market Map (Phase E1) — real,
    dated tracked-issuer filing titles for US/KR/JP; an explicit "not
    connected yet" state for China (see src/ui/components/
    regional_brief.py for why no other honest state is available)."""
    render_regional_brief(settings)


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
    settings = get_settings()

    # Phase C: the "Market Overview" page title + subtitle are gone — the
    # sidebar's own active-nav highlight already establishes "you are on
    # Dashboard," and Today's Read (below) is now the first thing on the
    # page. The freshness chip that used to sit beside the title now sits
    # beside Today's Read's own heading instead.
    #
    # Phase E1 (design/DASHBOARD_MARKET_MAP_PHASE_E.md): Market Map is now
    # the primary visual/research module, with the Regional Brief right
    # beneath it — Capital Rotation moves into a collapsed, truthfully
    # labeled expander (it is 100% static demo data, never "today" or
    # "live"). Theme Health and Priority Signals keep their existing
    # secondary weight and underlying logic, just lower on the page.
    _render_todays_read(ctx)
    _render_market_map(ctx)
    _render_regional_brief(settings)
    _render_theme_health(ctx)
    _render_priority_signals(ctx)
    with st.expander("Capital Rotation — demo snapshot", expanded=False):
        _render_rotation_snapshot(ctx)
    _render_catalysts(ctx)
    _render_watchlist_changes(ctx)
