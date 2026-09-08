"""About — what the tool does and where its data comes from. New page
(brief §4), the landing spot for marketing/explanatory copy relocated off
Home during the IA restructure — nothing here is deleted, only moved."""
from __future__ import annotations

import streamlit as st

from src.data_access.container import get_repositories
from src.ui.components.cards import theme_icon_html
from src.ui.components.section import section_header


def render() -> None:
    ctx = get_repositories()
    themes = ctx.theme_repository.get_all_themes()

    st.markdown('<div class="er-page-title">About</div>', unsafe_allow_html=True)

    section_header("What the tool does")
    st.write(
        "EevaResearch tracks the companies, supply chains, catalysts, bottlenecks, and capital flows "
        "behind five technology investment themes. The premise is that major trends create opportunity "
        "across an entire supply chain, not only in the largest headline companies — so the work is "
        "figuring out which layer benefits, where the bottleneck actually sits, and who has direct "
        "versus second-order exposure."
    )

    section_header("Five themes")
    theme_cols = st.columns(min(len(themes), 5) or 1)
    for col, theme in zip(theme_cols, themes):
        with col:
            with st.container(border=True, key=f"card-about-theme-{theme.slug}"):
                st.markdown(theme_icon_html(theme.slug), unsafe_allow_html=True)
                st.markdown(f'<div class="er-card-title" style="font-size:0.95rem;">{theme.name}</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="er-muted" style="font-size:0.8rem;">{theme.description}</div>', unsafe_allow_html=True)

    section_header("Data sources")
    st.write(
        "SEC EDGAR, TDnet (Japan), DART (Korea), CNINFO (China), and HKEXnews, plus market pricing for "
        "breadth and rotation calculations. Filing coverage varies by venue; pricing is delayed. In this "
        "foundation phase every figure and filing shown is demo data — no live source is connected yet."
    )

    section_header("How to use it")
    st.write(
        "Start with the Dashboard for today's market read, explore a theme or signal in depth, then use "
        "Research to ask a question and get a structured, evidence-labeled answer — every material claim "
        "carries one of four labels: Fact, Interpretation, Inference, or Uncertainty."
    )
