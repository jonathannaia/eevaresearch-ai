"""About — what the tool does and where its data comes from. New page
(brief §4), the landing spot for marketing/explanatory copy relocated off
Home during the IA restructure — nothing here is deleted, only moved."""
from __future__ import annotations

import streamlit as st

from src.data_access.container import get_repositories
from src.logic.market_map import REGION_SOURCE
from src.ui.components.cards import theme_icon_html
from src.ui.components.section import section_header


_COUNT_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")


def count_word(count: int) -> str:
    """A small count as an English word ("five"), falling back to the
    numeral beyond ten. Lets this page derive the theme count from the
    repository it renders without the sentence reading as "5 themes".
    Local to this page, its only consumer — hardcoding the number is how
    this page came to claim five filing venues while three of them had
    no adapter, so the fix is to derive it, not to add a shared helper
    nothing else uses."""
    return _COUNT_WORDS[count] if 0 <= count < len(_COUNT_WORDS) else str(count)


def _and_list(items: list[str]) -> str:
    """"A, B, and C" — keeps the retained copy's own cadence while the
    venue names themselves come from src.logic.market_map.REGION_SOURCE
    rather than being retyped here (reader-facing truthfulness pass:
    this page previously named five venues, three of which have no
    adapter anywhere in the codebase)."""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def render() -> None:
    ctx = get_repositories()
    themes = ctx.theme_repository.get_all_themes()

    st.markdown('<div class="er-page-title">About</div>', unsafe_allow_html=True)

    section_header("What the tool does")
    st.write(
        "EevaResearch tracks the companies, supply chains, catalysts, bottlenecks, and capital flows "
        f"behind {count_word(len(themes))} technology investment themes. The premise is that major "
        "trends create opportunity across an entire supply chain, not only in the largest headline "
        "companies — so the work is figuring out which layer benefits, where the bottleneck actually "
        "sits, and who has direct versus second-order exposure."
    )
    # Retained from the retired in-app landing page (src/ui/pages/home.py),
    # which is now a redirect. These three lines were the only copy on it
    # not already said better elsewhere; the rest duplicated this section
    # and "Data sources" below.
    st.write(
        "Evidence-first research across AI infrastructure, industrial automation, space, defense, "
        f"and supply chains — grounded in what was actually filed and said. Coverage includes "
        f"{_and_list(list(REGION_SOURCE.values()))}, and claims link to their original sources."
    )

    section_header(f"{count_word(len(themes)).capitalize()} themes")
    theme_cols = st.columns(min(len(themes), 5) or 1)
    for col, theme in zip(theme_cols, themes):
        with col:
            with st.container(border=True, key=f"card-about-theme-{theme.slug}"):
                st.markdown(theme_icon_html(theme.slug), unsafe_allow_html=True)
                st.markdown(f'<div class="er-card-title" style="font-size:0.95rem;">{theme.name}</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="er-muted" style="font-size:0.8rem;">{theme.description}</div>', unsafe_allow_html=True)

    section_header("Data sources")
    st.write(
        "Filing coverage comes from "
        + _and_list([f"{source} ({region})" for region, source in REGION_SOURCE.items()])
        + " — the venues this build has adapters for. There is no adapter for any other venue, so a "
        "region without one is shown as not connected rather than as quiet. Filing coverage also "
        "varies by venue and by issuer within a venue."
    )
    st.write(
        "Filings and the Signals news feed are ingested live on a schedule. Market pricing is not: "
        "every price, breadth, and rotation figure in this build comes from a built-in illustrative "
        "data provider rather than a market data feed, and the theme cards above are the product's own "
        "seeded theme definitions."
    )

    section_header("How to use it")
    st.write(
        "Start with the Dashboard for an overview, open Filings for the disclosures themselves and what "
        "was detected in them, and open Signals for the company and market-news feed. Every material "
        "claim carries one of four labels — Fact, Interpretation, Inference, or Uncertainty — and a "
        "Fact links to the source it came from."
    )
    st.write(
        "There is no question-answering assistant in this build, and nothing here writes analysis for "
        "you: a news item's summary is an extract of the source's own words, trimmed, never a generated "
        "rewrite."
    )
