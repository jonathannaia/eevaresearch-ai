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
original-source links. The Fact/Interpretation/Inference/Uncertainty
trust message is promoted from a buried hero-copy clause into its own
standalone, scannable block, reusing the exact same evidence_chip
component and the exact same four canonical descriptions Methodology's
own "The four labels" section already uses (src/ui/pages/methodology.py)
— never a second, divergent wording of the same four labels. "What this
tool won't do" keeps pointing directly at Disclaimer, unchanged from
before. No route, nav, auth, data model, or other page changed.
"""
from __future__ import annotations

import streamlit as st

from src.models.models import ClaimType
from src.ui.components.evidence_chips import evidence_chip
from src.ui.components.section import section_header
from src.ui.ui import brand_mark_html, get_page

# Verbatim from src/ui/pages/methodology.py's own "The four labels"
# section — the one canonical wording for these four descriptions;
# duplicated as a literal here (not imported) since methodology.py
# defines them inline in its own render() rather than as a module-level
# constant, and this batch's scope is Home only, never Methodology.
_CLAIM_LABELS = [
    (ClaimType.FACT, "Stated in a source document, and attributed to it. No attribution, no label."),
    (ClaimType.INTERPRETATION, "A market read built on facts shown alongside it."),
    (ClaimType.INFERENCE, "Follows logically from the evidence but is not confirmed anywhere."),
    (ClaimType.UNCERTAINTY, "A named open question. Recorded rather than smoothed over."),
]

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
    section_header("How claims are labeled")
    for claim_type, description in _CLAIM_LABELS:
        label_cols = st.columns([1, 4])
        with label_cols[0]:
            evidence_chip(claim_type)
        with label_cols[1]:
            st.markdown(f'<div class="er-muted" style="padding-top:0.3rem;">{description}</div>', unsafe_allow_html=True)

    st.divider()
    section_header("What Eeva does today")
    for title, description in _CAPABILITIES:
        st.markdown(f'<div class="er-card-title" style="font-size:0.9rem;">{title}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="er-muted" style="margin-bottom:var(--space-2);">{description}</div>', unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)
