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

    section_header("Automated detection can be wrong")
    st.write(
        "Filings are matched, excerpted, and classified by deterministic rules, and news items are "
        "matched to companies and themes the same way. Those rules miss context, match the wrong "
        "company, and flag routine material as interesting. Treat anything here as the start of your "
        "research, never the end of it, and verify anything you intend to act on against the linked "
        "source. There is no question-answering assistant in this build."
    )

    section_header("Not a substitute for the source")
    st.write(
        "Every Fact claim is backed by source attribution. A claim without attribution has not been "
        "verified, and its label will tell you so."
    )

    section_header("Data may be delayed or incomplete")
    st.write(
        "Coverage varies by filing venue, by issuer within a venue, and by language. Filings and "
        "Signals each carry their own freshness line, computed from what the background worker last "
        "recorded rather than from how recent the newest item looks — read it before relying on what "
        "the page shows, and note that it reports an unknown status rather than guessing when the "
        "stored record cannot support a claim. Other panels carry no freshness indicator of their own."
    )

    section_header("Account data")
    st.write(
        "EevaResearch records an account's email address, display name, first and last sign-in "
        "timestamps, and sign-in count solely for private-beta administration."
    )

    section_header("What is and is not live in this build")
    st.write(
        "Live: filings are ingested from the connected venues on a schedule, and the Signals feed polls "
        "company and market-news sources on a schedule. Both run unattended in the background."
    )
    st.write(
        "Not live: market pricing. Every price, breadth, and rotation figure comes from a built-in "
        "illustrative data provider, not a market data feed."
    )
    st.write(
        "Two different things happen to source text, and they are not the same. A news item's summary "
        "is extractive: the publisher's own words, stripped of markup, trimmed at a sentence boundary, "
        "and attributed to them — nothing is rewritten and no model is involved. A translated headline "
        "is the opposite: it is machine translation, produced by DeepL when a translation key is "
        "configured, so the English you read there is machine-generated text rather than anyone's own "
        "wording. The original-language headline stays the authoritative version and remains visible. "
        "No filing is summarized, interpreted, or analyzed by a language model anywhere in this build."
    )
    st.write(
        "What reaches you unattended, and what does not, differ by path. Signals news items are "
        "published unattended: the background worker fetches, matches, classifies, and publishes them "
        "on its own, and there is no human review step anywhere in that path — the rules described on "
        "the Methodology page are the only gate between a publisher's feed and this app."
    )
    st.write(
        "The filing-derived path is different. Automatic publication there is forced off in the "
        "background worker regardless of configuration, so detected material is routed to a review "
        "state instead of being published, and the research agent in this release cannot publish at "
        "all — a ceiling set in code, not a setting an operator can raise."
    )
    st.write(
        "A review state records one narrow fact: the item has not been marked reviewed. It is not "
        "evidence that nobody has looked at it, and not evidence that anybody has."
    )
    st.write(
        "No trading integrations of any kind, and no order, position, or brokerage connection exists in "
        "this build."
    )
