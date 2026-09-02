"""Market Map (Phase E1, design/DASHBOARD_MARKET_MAP_PHASE_E.md) — a
theme-grouped visual navigator over Eeva's tracked-company universe
(src/config/tracked_companies.py), not a price heatmap. No quote, price,
market-cap, or movement data exists anywhere in this build (see the Phase
E report, section A) — every tile says so explicitly rather than showing a
dash or blank space. Click-to-investigate uses a single selected-company
detail region beneath the whole map (not a per-tile dialog/expander),
matching the brief's "least complex, stable interaction" requirement.

Reuses the existing card container/hover treatment (any `st.container(key=
"card-...")` already gets assets/styles.css's shared card styling for
free), the existing `signal_card` component for evidence display (so the
Fact/Interpretation/Inference/Uncertainty distinction is inherited, not
reimplemented), and an existing route only (Radar Inbox) — no new page is
introduced. The "Open company"/"Ask Research" handoff links were removed
(reader-facing data-integrity pass, design/DECISIONS.md) along with the
Company and Research pages themselves, neither of which had live real
data.
"""
from __future__ import annotations

import streamlit as st

from src.config.tracked_companies import TrackedCompany
from src.logic.market_map import company_selection_key, find_company_by_selection_key
from src.ui.components.cards import signal_card
from src.ui.ui import get_page

MAX_TILES_PER_THEME = 6
SELECTED_KEY = "mm-selected-company"

_TICKER_LABEL_BY_SOURCE = {
    "SEC EDGAR": lambda code: code,
    "OpenDART / DART": lambda code: f"KRX {code}",
    "EDINET": lambda code: f"EDINET code {code}",
}


def _ticker_label(company: TrackedCompany) -> str:
    fmt = _TICKER_LABEL_BY_SOURCE.get(company.source)
    return fmt(company.krx_code) if fmt else company.krx_code


def _render_tile(company: TrackedCompany, theme_slug: str) -> None:
    tile_key = f"card-mm-tile-{theme_slug}-{company.source}-{company.krx_code}"
    with st.container(border=True, key=tile_key):
        st.markdown(f'<div class="er-card-title" style="font-size:0.9rem;">{company.name}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="er-muted er-mono" style="font-size:0.78rem;">{_ticker_label(company)}</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="er-muted" style="font-size:0.74rem; margin-top:0.2rem;">Price coverage not connected</div>',
            unsafe_allow_html=True,
        )
        btn_key = f"mm-investigate-{theme_slug}-{company.source}-{company.krx_code}"
        if st.button("Investigate →", key=btn_key, width="stretch"):
            st.session_state[SELECTED_KEY] = company_selection_key(company)


def _render_theme_group(theme_name: str, theme_slug: str, companies: list[TrackedCompany]) -> None:
    if not companies:
        return
    st.markdown(f'<div class="er-section-label" style="margin-top:0.6rem;">{theme_name}</div>', unsafe_allow_html=True)
    shown = companies[:MAX_TILES_PER_THEME]
    cols = st.columns(min(len(shown), 3) or 1)
    for i, company in enumerate(shown):
        with cols[i % len(cols)]:
            _render_tile(company, theme_slug)
    remaining = len(companies) - len(shown)
    if remaining > 0:
        themes_page = get_page("themes")
        if themes_page is not None:
            with st.container(key=f"cta-tertiary-mm-more-{theme_slug}"):
                st.page_link(themes_page, label=f"+{remaining} more — view all in Themes →")


def _render_selected_detail(ctx) -> None:
    selected_key = st.session_state.get(SELECTED_KEY)
    if not selected_key:
        return
    company = find_company_by_selection_key(selected_key)
    if company is None:
        return

    themes_page = get_page("themes")
    radar_page = get_page("radar_inbox")

    with st.container(border=True, key="card-mm-detail"):
        top = st.columns([5, 1])
        with top[0]:
            st.markdown(f'<div class="er-card-title">{company.name}</div>', unsafe_allow_html=True)
        with top[1]:
            with st.container(key="cta-tertiary-mm-close-detail"):
                if st.button("Close", key="mm-close-detail", width="stretch"):
                    st.session_state[SELECTED_KEY] = None
                    st.rerun()

        theme_names = ", ".join(t.replace("-", " ").title() for t in company.themes)
        st.markdown(
            f'<div class="er-muted" style="margin-top:0.1rem;">Theme membership: {theme_names}</div>'
            f'<div class="er-muted">Listing exchange: {company.exchange} · {_ticker_label(company)}</div>'
            '<div class="er-muted" style="font-size:0.78rem; margin-top:0.2rem;">Price coverage not connected</div>',
            unsafe_allow_html=True,
        )

        st.markdown('<div class="er-section-label" style="margin-top:0.5rem;">What may explain recent activity</div>', unsafe_allow_html=True)
        matched_signals = [
            s for s in ctx.signal_repository.get_all_signals()
            if s.exchange_symbol and s.exchange_symbol == company.krx_code
        ]
        if matched_signals:
            for s in matched_signals[:3]:
                signal_card(s, evidence_repository=ctx.evidence_repository)
        else:
            st.markdown('<div class="er-muted">No verified catalyst linked yet.</div>', unsafe_allow_html=True)

        if radar_page is not None:
            with st.container(key=f"cta-tertiary-mm-open-radar-{selected_key}"):
                st.page_link(radar_page, label="Related filings / Open Radar Inbox →")


def render_market_map(ctx, themes, companies_by_theme: dict[str, list[TrackedCompany]]) -> None:
    """`themes` is the ordered list of Theme records (theme_repository.
    get_all_themes()); `companies_by_theme` comes from
    src.logic.market_map.group_companies_by_theme(...) keyed by the same
    slugs. Renders the full map (grouped tiles) plus the single selected-
    company detail region beneath it."""
    st.markdown('<div id="market-map"></div>', unsafe_allow_html=True)
    st.markdown('<div class="er-section-label">Market Map</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="er-muted">Company and theme map · price coverage is being connected</div>',
        unsafe_allow_html=True,
    )
    for theme in themes:
        _render_theme_group(theme.name, theme.slug, companies_by_theme.get(theme.slug, []))
    _render_selected_detail(ctx)
