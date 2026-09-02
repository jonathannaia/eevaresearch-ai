"""Dashboard — answers one question: "what should I investigate now?"
(UX-refinement pass, design/DECISIONS.md).

Reader-facing data-integrity pass (design/DECISIONS.md): every module on
this page now renders only from a real, live, source-backed data source,
or does not render at all. Today's Read (a canned narrative built from
100% static demo rotation-metrics seed data), Theme Health's old
breadth/performance-bar treatment, Capital Rotation, Catalysts, and
Watchlist Changes are all removed — no real live source exists in this
build for market performance, breadth, rotation, or catalyst-calendar
data, and none was invented to replace them. The exact real data source
powering each remaining module:

  - Market Map: src/config/tracked_companies.py (the real tracked-issuer
    registry) via src.logic.market_map.group_companies_by_theme; matched
    signals in the selected-company detail come from the real Radar
    signal repository. No price/quote/movement data exists anywhere in
    this build, so every tile says "Price coverage not connected" rather
    than a dash or invented value. Known, disclosed limitation: the
    theme-group *display names/order* (e.g. "AI Buildout") still come
    from ctx.theme_repository — the same real, permanent 5-category
    taxonomy tracked_companies.py itself encodes on every company's
    `themes` field, just currently housed in a legacy demo-data-shaped
    repository rather than a plain static config module. No company,
    figure, or fact shown here is fabricated; this is a mislocation of
    real label text, not mock content, and is flagged here for a
    follow-up relocation rather than silently left unmentioned.
  - Regional Brief: backend_factory.get_filing_event_repository() per
    jurisdiction — real, dated tracked-issuer filing titles for US/KR/JP;
    an explicit "not connected yet" state for China.
  - Theme Health: backend_factory.get_theme_repository().
    list_published_themes() — the exact published-only protocol
    src/ui/pages/themes_research.py itself uses, so an internal or
    candidate Theme can never appear here. Renders nothing at all (no
    header, no link into Themes) unless at least one real published
    Theme exists; shows only real evidence-item/distinct-company counts
    for each one, never a price/breadth/performance statistic.
  - Priority Signals: backend_factory.get_signal_repository() (real
    EDGAR/DART/EDINET Radar promotions only, see
    src/data_access/live/radar_signal_repository.py) — renders nothing
    at all unless at least one real signal qualifies.
"""
from __future__ import annotations

import html

import streamlit as st

from src.config.settings import get_settings
from src.data_access import backend_factory
from src.data_access.container import get_repositories
from src.logic.market_map import group_companies_by_theme
from src.logic.unread import is_unread
from src.ui.components.cards import priority_signal_row
from src.ui.components.market_map import render_market_map
from src.ui.components.regional_brief import render_regional_brief
from src.ui.components.section import section_header
from src.ui.ui import LAST_SEEN_KEY, READ_IDS_KEY, get_page

PRIORITY_SIGNAL_COUNT = 3

_MAX_THEME_HEALTH_CARDS = 5


def _esc(value: object) -> str:
    if value is None:
        return ""
    return html.escape(str(value))


def _render_theme_health(settings) -> None:
    """Real data only, via the published-only ThemeRepositoryProtocol
    (see module docstring). Renders nothing — no header, no Themes
    link — unless at least one real published Theme exists. Only ever
    shows a published Theme's own real evidence-item count and distinct-
    company count, computed the same way themes_research.py's own index
    cards compute them; never a price/breadth/performance statistic,
    since no real live source for any of those exists in this build."""
    try:
        repository = backend_factory.get_theme_repository(settings)
        themes = repository.list_published_themes()
    except Exception:  # noqa: BLE001 — best-effort supplementary module; never show a raw error on the dashboard
        return
    if not themes:
        return

    section_header("Theme Health")
    themes_page = get_page("themes")
    shown = themes[:_MAX_THEME_HEALTH_CARDS]
    cols = st.columns(min(len(shown), _MAX_THEME_HEALTH_CARDS) or 1)
    for col, theme in zip(cols, shown):
        with col:
            with st.container(border=True, key=f"card-theme-health-{theme.id}"):
                st.markdown(f'<div class="er-metric-label">{_esc(theme.title)}</div>', unsafe_allow_html=True)
                try:
                    evidence = repository.evidence_for_theme(theme.id)
                    company_map = repository.company_map_for_theme(theme.id)
                    distinct_companies = {item.company for item in evidence} | {entry.company_name for entry in company_map}
                except Exception:  # noqa: BLE001 — one theme's count lookup failing must not take down the row
                    evidence, distinct_companies = (), set()
                st.markdown(f'<div class="er-metric-value">{len(evidence)}</div>', unsafe_allow_html=True)
                st.markdown('<div class="er-metric-label" style="margin-top:var(--space-2);">Evidence items</div>', unsafe_allow_html=True)
                company_word = "company" if len(distinct_companies) == 1 else "companies"
                st.markdown(
                    f'<div class="er-muted" style="margin-top:var(--space-1);">{len(distinct_companies)} {company_word}</div>',
                    unsafe_allow_html=True,
                )
                if themes_page is not None:
                    with st.container(key=f"cta-tertiary-health-{theme.id}"):
                        st.page_link(themes_page, label="Explore →", query_params={"theme_id": theme.id})
    remaining = len(themes) - len(shown)
    if remaining > 0 and themes_page is not None:
        with st.container(key="cta-tertiary-health-more"):
            st.page_link(themes_page, label=f"+{remaining} more — view all in Themes →")


def _render_priority_signals(ctx) -> None:
    """Real signals only (ctx.signal_repository is backend_factory.
    get_signal_repository()'s RadarSignalRepository — see module
    docstring). Renders nothing at all — no header, no anchor — if zero
    real signals qualify, rather than a placeholder caption."""
    signals = ctx.signal_repository.get_all_signals()
    if not signals:
        return

    st.markdown('<div id="priority-signals"></div>', unsafe_allow_html=True)
    section_header("Priority Signals", "Highest-conviction real signals by direction, strength, and evidence.")

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


def _themes_available(settings) -> bool:
    """Navigation/empty-state pass (design/DECISIONS.md) — whether at
    least one real published Theme exists, via the same published-only
    protocol Theme Health uses. Never raises; a repository-construction
    failure degrades to "unavailable" (no dashboard link into Themes),
    never a raw error."""
    try:
        return bool(backend_factory.get_theme_repository(settings).list_published_themes())
    except Exception:  # noqa: BLE001 — best-effort; never show a raw error on the dashboard
        return False


def _render_market_map(ctx, themes_available: bool) -> None:
    """Primary visual/research module (Phase E1, design/
    DASHBOARD_MARKET_MAP_PHASE_E.md) — a theme-grouped navigator over
    Eeva's tracked-company universe (src/config/tracked_companies.py, the
    one authoritative source — see src/logic/market_map.py). No price,
    quote, or movement data exists anywhere in this build; "Price
    coverage not connected" is removed (navigation/empty-state pass,
    design/DECISIONS.md) rather than shown as implementation-status
    language — cards simply omit price entirely."""
    themes = ctx.theme_repository.get_all_themes()
    companies_by_theme = group_companies_by_theme([t.slug for t in themes])
    render_market_map(ctx, themes, companies_by_theme, themes_available)


def _render_regional_brief(settings) -> None:
    """Compact supporting module beneath the Market Map (Phase E1) — real,
    dated tracked-issuer filing titles for US/KR/JP; an explicit "not
    connected yet" state for China (see src/ui/components/
    regional_brief.py for why no other honest state is available)."""
    render_regional_brief(settings)


def render() -> None:
    ctx = get_repositories()
    settings = get_settings()

    _render_market_map(ctx, _themes_available(settings))
    _render_regional_brief(settings)
    _render_theme_health(settings)
    _render_priority_signals(ctx)
