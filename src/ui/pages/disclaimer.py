"""Disclaimer — research, not advice. Split out of methodology.py (brief
§4) so the "what this tool does not do" content has a dedicated, easy-to-
link page rather than being buried at the bottom of the methodology
legend."""
from __future__ import annotations

import streamlit as st

from src.ui.components.section import section_header


def render() -> None:
    st.markdown('<div class="er-page-title">Disclaimer</div>', unsafe_allow_html=True)
    st.markdown('<div class="er-muted">Research, not advice.</div>', unsafe_allow_html=True)

    section_header("Not financial advice")
    st.write(
        "Nothing in this tool is a recommendation to buy, sell, or hold any security. EevaResearch is "
        "not a registered investment adviser, dealer, or analyst, and using it creates no advisory "
        "relationship. Every decision you make with it is yours. EevaResearch does not execute trades, "
        "move money, or give personalized investment advice, and never states a buy/sell/hold call or a "
        "price target."
    )

    section_header("The conversational agent can be wrong")
    st.write(
        "It can misread a filing, miss context, or state something confidently that turns out to be "
        "false. Treat its answers as the start of your research, never the end of it. Verify anything "
        "you intend to act on against the linked source."
    )

    section_header("Not a substitute for the source")
    st.write(
        "Every Fact claim is backed by source attribution. A claim without attribution has not been "
        "verified, and its label will tell you so."
    )

    section_header("Data may be delayed or incomplete")
    st.write(
        "Coverage varies by filing venue and language. Prices are not real-time. Check the freshness "
        "indicator on each panel before relying on what it shows."
    )

    section_header("This build (foundation phase)")
    st.write(
        "Application foundation, data model, navigation, UI system, and mock/demo data only. No real "
        "ticker coverage, no paid APIs, no autonomous research loops, no live news ingestion, no "
        "trading integrations, and no LLM wiring."
    )
