"""Home — first-visit-only landing page (brief §8). No sidebar (see
app.py: show_sidebar=False); single 900px column with a minimal top bar
instead. No dashboard content, no financial claims, no live metrics.

UI audit Phase A (design/DECISIONS.md): simplified to hero + a 3-step
"how to use it" + one primary CTA. The previous evidence-chip legend,
five-theme preview, and "what this tool does not do" grid each
duplicated content that already lives at its own canonical page
(Methodology's "The four labels", About's "Five themes", and
Disclaimer's own four full sections, respectively) — removed from this
page only, not deleted from the app; each remains fully reachable via
nav/footer. The "What this tool won't do" link now points directly at
Disclaimer instead of an in-page anchor to content that no longer lives
on this page, so that safety disclosure stays exactly as reachable as
before, just not duplicated here.
"""
from __future__ import annotations

import streamlit as st

from src.ui.ui import brand_mark_html, get_page
from src.ui.components.section import section_header

_STEPS = [
    "Start with the Dashboard for today's market read",
    "Open a theme to see its value chain, signals, and catalysts",
    "Ask Research a question and get an evidence-labeled answer",
]


def render() -> None:
    st.markdown('<div class="er-home-column">', unsafe_allow_html=True)

    st.markdown(
        f'<div class="er-home-topbar"><span class="er-rail-logo">{brand_mark_html()}</span>'
        '<span style="font-weight:700; font-size:0.9rem;">EevaResearch</span></div>',
        unsafe_allow_html=True,
    )

    st.markdown('<div class="er-hero-wrap" style="padding:0 0 1rem 0;">', unsafe_allow_html=True)
    st.markdown('<div class="er-eyebrow">EevaResearch · Market Intelligence</div>', unsafe_allow_html=True)
    st.markdown('<div class="er-hero-title">Start with what was filed.</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="er-hero-sub">EevaResearch tracks the companies, supply chains, catalysts, and capital '
        "rotation behind five technology themes, and labels every claim by how well it's backed — Fact, "
        "Interpretation, Inference, or Uncertainty.</div>",
        unsafe_allow_html=True,
    )

    cta_cols = st.columns([1, 1, 2])
    with cta_cols[0]:
        page = get_page("dashboard")
        if page is not None:
            with st.container(key="cta-primary-home-dashboard"):
                st.page_link(page, label="Open Dashboard", width="stretch")
    with cta_cols[1]:
        disclaimer_page = get_page("disclaimer")
        if disclaimer_page is not None:
            st.markdown('<div style="padding-top:0.5rem;">', unsafe_allow_html=True)
            with st.container(key="cta-tertiary-home-disclaimer"):
                st.page_link(disclaimer_page, label="What this tool won't do")
            st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    st.divider()
    section_header("How to use it")
    step_cols = st.columns(3)
    for col, (i, step) in zip(step_cols, enumerate(_STEPS, start=1)):
        with col:
            st.markdown(f'<div class="er-metric-label">Step {i}</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="er-card-title" style="font-size:0.9rem;">{step}</div>', unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)
