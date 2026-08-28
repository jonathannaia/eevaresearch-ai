"""Themes — one shared detail renderer reused across all five fixed
themes via an outer tab per theme (unchanged mechanism), with the brief's
required Map / Rotation / Companies / Catalysts tabs nested inside each one
(brief §4: Themes absorbs Themes + Capital Rotation; within-theme rotation
lives on the Rotation tab here, cross-theme rotation is the Dashboard
panel — Capital Rotation itself is never a nav item). Each theme's data
comes from AppContext, so a real ticker universe drops in later without
touching this file.
"""
from __future__ import annotations

import streamlit as st

from src.data_access.container import AppContext, get_repositories
from src.logic.formatting import fmt_pct
from src.models.models import ClaimType, Theme
from src.ui.components.badges import claim_type_badge, direction_dot_html, direction_status_tag_html
from src.ui.components.cards import catalyst_timeline_row, metric_pair_html, signal_card, theme_icon_html
from src.ui.components.empty_state import empty_state
from src.ui.components.filters import ticker_filter_bar
from src.ui.components.freshness import panel_header
from src.ui.components.section import section_header
from src.ui.components.tables import ticker_table
from src.ui.ui import get_page


def _infer_direction(relative_performance_pct: float):
    from src.models.models import Direction

    if relative_performance_pct > 2:
        return Direction.IMPROVING
    if relative_performance_pct < -2:
        return Direction.WEAKENING
    return Direction.MIXED


def _render_map_tab(theme: Theme) -> None:
    panel_header("Supply-chain layers", key=f"fresh-map-{theme.slug}")
    sub_cols = st.columns(min(len(theme.subthemes), 3) or 1)
    for i, sub in enumerate(theme.subthemes):
        with sub_cols[i % len(sub_cols)]:
            # Shared card treatment (same [class*="st-key-card-"] rule as
            # every other card in the app) with no colored top bar — these
            # cards don't carry a status, so nothing would be honest to
            # color-code (Phase D, design/DECISIONS.md). The hover-brighten
            # is already suppressed for this card-subtheme- prefix in
            # assets/styles.css so a non-interactive card doesn't imply
            # otherwise.
            with st.container(border=True, key=f"card-subtheme-{sub.slug}"):
                st.markdown(f'<div class="er-card-title" style="font-size:0.95rem;">{sub.name}</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="er-muted">{sub.description}</div>', unsafe_allow_html=True)
    if theme.subthemes:
        # UX-refinement pass: this exact guidance used to repeat verbatim
        # on every subtheme card above (identical text N times per theme,
        # 5 themes) — same content, stated once for the whole layer grid
        # instead, immediately below it. Phase D: the separate "Informational
        # — these layers aren't drill-down links in this phase" framing
        # sentence is gone (the cards' own suppressed hover already makes
        # that plain without a caption saying so); this analytical note is
        # kept since it's real guidance, not a disclaimer.
        st.markdown(
            '<div class="er-muted" style="font-size:0.76rem; margin-top:0.5rem;"><strong>What to investigate:</strong> '
            "which companies sit in each layer, and whether the bottleneck is here or elsewhere in the chain.</div>",
            unsafe_allow_html=True,
        )


def _render_rotation_tab(theme: Theme, ctx: AppContext, is_leader: bool) -> None:
    panel_header("Rotation", key=f"fresh-rotation-{theme.slug}")
    metric = ctx.market_data_provider.get_rotation_metric_for_theme(theme.slug)
    if metric:
        st.markdown(
            metric_pair_html("Rel. perf.", fmt_pct(metric.relative_performance_pct), "Breadth", f"{metric.breadth_pct:.0f}%"),
            unsafe_allow_html=True,
        )
        bar_cls = "leader" if is_leader else ""
        st.markdown(
            f'<div class="bar" style="margin:0.3rem 0 0.5rem 0;"><i class="{bar_cls}" style="--w:{metric.breadth_pct:.0f}%;"></i></div>'
            f'<div>{direction_dot_html(_infer_direction(metric.relative_performance_pct))}</div>',
            unsafe_allow_html=True,
        )
        with st.container(border=True, key=f"card-rotation-narrative-{theme.slug}"):
            top = st.columns([4, 1])
            top[0].markdown(f'<div class="er-card-title">Within-theme rotation read</div>', unsafe_allow_html=True)
            with top[1]:
                claim_type_badge(ClaimType.INTERPRETATION)
            st.markdown(f"**What changed**\n\n{theme.name} shows a demo relative-performance read of {fmt_pct(metric.relative_performance_pct)} with {metric.breadth_pct:.0f}% breadth this period.")
            st.markdown("**Why it may matter**\n\nBreadth above roughly half the tracked names suggests participation isn't concentrated in one name — on its own, still not conclusive.")
            st.markdown("**What would invalidate this read**\n\nA reversal in the next demo rotation snapshot, or breadth concentrating in a single ticker.")
    else:
        empty_state(
            "No rotation data for this theme yet.",
            "Rotation metrics are demo data in this phase — see Methodology for how they're built.",
            action_label="Read Methodology",
            action_page=get_page("methodology"),
            key=f"theme-rotation-{theme.slug}",
        )

    # Phase D: real (sometimes populated) content before the permanently-
    # empty "Sub-theme rotation" framework note — that section is always
    # an empty state in this phase, so it reads as supporting/future
    # context rather than something to wade through before Related
    # signals, which is genuinely useful the moment a theme has any.
    section_header("Related signals")
    theme_signals = ctx.signal_repository.get_signals_for_theme(theme.slug)
    if not theme_signals:
        empty_state(
            "No signals for this theme yet.",
            "New signals across every theme show up on Signals as they're found.",
            action_label="View all signals",
            action_page=get_page("signals"),
            key=f"theme-signals-{theme.slug}",
        )
    else:
        for s in theme_signals:
            signal_card(s, evidence_repository=ctx.evidence_repository, theme_name=theme.name)

    section_header("Sub-theme rotation", "Framework only in this phase — subtheme-level rotation metrics aren't populated yet.")
    empty_state(
        "Sub-theme rotation isn't populated yet.",
        "Once subtheme-level metrics exist (Phase 2+), this tab would show rotation within the theme "
        "(e.g. DRAM vs. HBM within Memory) using the same leaders/laggards pattern as the Dashboard panel.",
    )


def _render_companies_tab(theme: Theme, ctx: AppContext) -> None:
    panel_header("Companies", key=f"fresh-companies-{theme.slug}")
    st.caption("Filters are designed for the full curated ticker universe (Phase 3) — with one demo ticker, most won't narrow anything yet.")
    tickers = ctx.ticker_repository.get_tickers_for_theme(theme.slug)
    catalysts = ctx.catalyst_repository.get_catalysts_for_theme(theme.slug)
    filtered = ticker_filter_bar(tickers, theme.subthemes, catalysts, key_prefix=theme.slug)
    ticker_table(filtered)
    demo_ticker = next((t for t in filtered if t.is_demo), None)
    if demo_ticker is not None:
        ticker_page = get_page("company")
        if ticker_page is not None:
            with st.container(key=f"cta-tertiary-demo-ticker-{theme.slug}"):
                st.page_link(ticker_page, label=f"View {demo_ticker.symbol} company page →", query_params={"symbol": demo_ticker.symbol})


def _render_catalysts_tab(theme: Theme, ctx: AppContext) -> None:
    panel_header("Catalysts", key=f"fresh-catalysts-{theme.slug}")
    catalysts = ctx.catalyst_repository.get_catalysts_for_theme(theme.slug)
    if not catalysts:
        empty_state(
            "No catalysts scheduled for this theme yet.",
            "Upcoming catalysts across every theme are also collected on the Dashboard.",
            action_label="Open Dashboard",
            action_page=get_page("dashboard"),
            key=f"theme-catalysts-{theme.slug}",
        )
    else:
        for c in catalysts:
            catalyst_timeline_row(c)


def _render_theme_detail(theme: Theme, ctx: AppContext, leader_slug: str | None) -> None:
    """Concise header (theme identity + status + one current-state
    sentence) directly followed by the Map/Rotation/Companies/Catalysts
    tabs (Phase D, design/DECISIONS.md) — collapses the previous icon +
    title + description paragraph + separate bordered summary card (four
    reading steps before the tabs) into one compact block, so the
    selected sub-view's own content is reachable immediately below it.
    "Ask Research" stays available as a small secondary bridge in the
    header itself rather than inside whichever tab happens to be open, so
    it doesn't compete with that tab's content."""
    metric = ctx.market_data_provider.get_rotation_metric_for_theme(theme.slug)
    research_page = get_page("research")

    head_cols = st.columns([5, 2], vertical_alignment="center")
    with head_cols[0]:
        st.markdown(theme_icon_html(theme.slug), unsafe_allow_html=True)
        st.markdown(f'<div class="er-page-title" style="font-size:1.4rem; margin-bottom:0;">{theme.name}</div>', unsafe_allow_html=True)
    with head_cols[1]:
        if metric:
            direction = _infer_direction(metric.relative_performance_pct)
            st.markdown(
                f'<div style="text-align:right; margin-top:0.6rem;">{direction_status_tag_html(direction)}</div>',
                unsafe_allow_html=True,
            )

    if metric:
        direction = _infer_direction(metric.relative_performance_pct)
        breadth_note = (
            "broad participation" if metric.breadth_pct >= 50 else "participation concentrated in a smaller subset of names"
        )
        st.markdown(
            f'<div class="er-muted" style="margin:0.1rem 0 0.5rem 0;">{theme.description} Reading '
            f'<strong>{direction.value.lower()}</strong> this sample snapshot '
            f'({fmt_pct(metric.relative_performance_pct)}, {metric.breadth_pct:.0f}% breadth) — {breadth_note}.</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(f'<div class="er-muted" style="margin:0.1rem 0 0.5rem 0;">{theme.description}</div>', unsafe_allow_html=True)

    if research_page is not None:
        with st.container(key=f"cta-secondary-theme-ask-research-{theme.slug}"):
            st.page_link(research_page, label="Ask Research →")

    map_tab, rotation_tab, companies_tab, catalysts_tab = st.tabs(["Map", "Rotation", "Companies", "Catalysts"])
    with map_tab:
        _render_map_tab(theme)
    with rotation_tab:
        _render_rotation_tab(theme, ctx, is_leader=(theme.slug == leader_slug))
    with companies_tab:
        _render_companies_tab(theme, ctx)
    with catalysts_tab:
        _render_catalysts_tab(theme, ctx)


def render() -> None:
    ctx = get_repositories()
    themes = ctx.theme_repository.get_all_themes()

    # Phase D: the subtitle here used to just restate the tab names the
    # user is about to see (Map/Rotation/Companies/Catalysts) — removed as
    # redundant; the per-theme header below (name + status + one sentence)
    # now carries that framing per selected theme instead.
    st.markdown('<div class="er-page-title">Themes</div>', unsafe_allow_html=True)

    if not themes:
        empty_state("No themes loaded.")
        return

    metrics = ctx.market_data_provider.get_rotation_metrics()
    leader_slug = max(metrics, key=lambda m: m.relative_performance_pct).theme_slug if metrics else None

    tabs = st.tabs([t.name for t in themes])
    for tab, theme in zip(tabs, themes):
        with tab:
            _render_theme_detail(theme, ctx, leader_slug)
