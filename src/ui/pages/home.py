"""Home — first-visit-only landing page (brief §8). No sidebar (see
app.py: show_sidebar=False); single 900px column with a minimal top bar
instead. No dashboard content, no financial claims, no live metrics.

Homepage rewrite (Batch 1, design/DECISIONS.md): replaces the prior
generic "Market Intelligence" / "capital rotation" framing (neither
capital-rotation nor market-pricing data exists anywhere in this build —
see dashboard.py's own docstring) with a precise description of Eeva's
actual, currently-shipped capabilities: cross-market primary sources
(SEC EDGAR / DART / EDINET), company research, Themes, supply-chain
research (company roles within a theme's value chain), Daily News, and
original-source links. "What this tool won't do" keeps pointing directly
at Disclaimer, unchanged from before.

Homepage correction (design/DECISIONS.md): the standalone "How claims
are labeled" Fact/Interpretation/Inference/Uncertainty block this batch
originally added has been removed by product decision — Eeva does not
display or market visible claim labels; researchers assess evidence
themselves. Original-source links and source attribution stay (see
_CAPABILITIES below); the underlying claim-type model, evidence_chip
component, and Methodology's own "The four labels" section are
untouched — this is a Home-page-only copy change, not a removal of the
underlying methodology. No route, nav, auth, data model, or other page
changed.
"""
from __future__ import annotations

import streamlit as st

from src.ui.components.section import section_header
from src.ui.ui import brand_mark_html, get_page

_CAPABILITIES = [
    ("Cross-market primary sources", "SEC EDGAR (U.S.), DART (Korea), and EDINET (Japan) filings."),
    ("Company research", "Tracked issuers across AI infrastructure, industrial automation, space, defense, and supply-chain themes."),
    ("Themes", "Evidence-first research narratives connecting official-source evidence across companies."),
    ("Supply-chain research", "Each company's role in a theme's value chain, from constraint owner to demand driver."),
    ("Daily News", "Official company releases, as published, from investor-relations and newsroom sources."),
    ("Original-source links", "Every fact links back to the real filing, release, or document it came from."),
]


def render() -> None:
    st.markdown('<div class="er-home-column">', unsafe_allow_html=True)

    st.markdown(
        f'<div class="er-home-topbar"><span class="er-rail-logo">{brand_mark_html()}</span>'
        '<span style="font-weight:700; font-size:0.9rem;">EevaResearch</span></div>',
        unsafe_allow_html=True,
    )

    st.markdown('<div class="er-hero-wrap" style="padding:0 0 1rem 0;">', unsafe_allow_html=True)
    st.markdown(
        '<div class="er-eyebrow">Evidence-first research across AI infrastructure, industrial automation, '
        "space, defense, and supply chains</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="er-hero-title">Research grounded in what was actually filed and said.</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="er-hero-sub">Eeva tracks primary disclosures, investor-relations releases, and news '
        "across the U.S., Korea, and Japan — organized into themes, company research, and supply-chain "
        "context, every claim linked back to its original source.</div>",
        unsafe_allow_html=True,
    )

    cta_cols = st.columns([1, 1, 2])
    with cta_cols[0]:
        page = get_page("dashboard")
        if page is not None:
            with st.container(key="cta-primary-home-dashboard"):
                st.page_link(page, label="Explore the research →", width="stretch")
    with cta_cols[1]:
        disclaimer_page = get_page("disclaimer")
        if disclaimer_page is not None:
            st.markdown('<div style="padding-top:0.5rem;">', unsafe_allow_html=True)
            with st.container(key="cta-tertiary-home-disclaimer"):
                st.page_link(disclaimer_page, label="What this tool won't do")
            st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    st.divider()
    section_header("What Eeva does today")
    for title, description in _CAPABILITIES:
        st.markdown(f'<div class="er-card-title" style="font-size:0.9rem;">{title}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="er-muted" style="margin-bottom:var(--space-2);">{description}</div>', unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)
