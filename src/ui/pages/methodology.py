"""Methodology — the evidence-first framework and claim-type legend. The
disclaimer/limitations content that used to live here has moved to its own
Disclaimer page, and the roadmap/what-it-does content that overlapped with
marketing copy has moved to About (brief §4: Methodology page splits into
three doc pages).

UX-refinement pass: Disclaimer is no longer a primary sidebar item, so this
page's cross-link to it (near the top, not buried at the bottom) plus the
global footer are now the two ways to reach it."""
from __future__ import annotations

import streamlit as st

from src.data_access.daily_news.feed_registry import PILOT_FEEDS
# Imported from source_registry, where both registries are defined, rather
# than from editorial_pipeline, which re-exports one of them but also
# pulls in the RSS/Atom fetch client — a doc page has no business
# importing a fetch client, even an uncalled one.
from src.data_access.daily_news.source_registry import EDITORIAL_SOURCE_REGISTRY
from src.logic.market_map import REGION_SOURCE, REGIONS_ALL
from src.models.models import ClaimType
from src.ui.components.badges import claim_type_badge
from src.ui.components.section import section_header
from src.ui.ui import METHODOLOGY_STATEMENT, get_page


def _venue_sentence() -> str:
    """Reader-facing truthfulness pass: the filing venues are read from
    src.logic.market_map.REGION_SOURCE — the same region/source mapping
    the live Dashboard regional brief and Themes modules already use —
    rather than being retyped here, so this copy cannot drift from the
    adapters that actually exist. Previously this page named five venues
    (adding TDnet, CNINFO and HKEX); only three have an adapter anywhere
    in the codebase, and regional_brief.py itself already renders China
    as 'not connected' for exactly that reason."""
    pairs = [f"{source} ({region})" for region, source in REGION_SOURCE.items()]
    joined = ", ".join(pairs[:-1]) + f", and {pairs[-1]}" if len(pairs) > 1 else pairs[0]
    return joined


def _uncovered_regions() -> tuple[str, ...]:
    return tuple(region for region in REGIONS_ALL if region not in REGION_SOURCE)


def render() -> None:
    st.markdown('<div class="er-page-title">Methodology</div>', unsafe_allow_html=True)
    st.write(METHODOLOGY_STATEMENT)

    disclaimer_page = get_page("disclaimer")
    if disclaimer_page is not None:
        with st.container(key="cta-secondary-methodology-disclaimer-top"):
            st.page_link(disclaimer_page, label="Read the full disclaimer →")

    section_header("Where the material comes from")
    st.write(
        f"Filing material is read from primary disclosures at {_venue_sentence()} — the venues this "
        "build has adapters for — rather than from press coverage. "
        "A large share of what matters appears first in a footnote, a segment note, or a Japanese- or "
        "Korean-language disclosure that English coverage does not pick up for days."
    )
    uncovered = _uncovered_regions()
    if uncovered:
        st.write(
            f"No adapter exists for any other venue. {', '.join(uncovered)} is therefore shown as not "
            "connected rather than as having nothing to report — an absent source and an empty one are "
            "different things, and this build never presents one as the other."
        )
    st.write(
        f"Filing ingestion and the Signals feed are live: filings are fetched from those venues on a "
        f"schedule, and Signals polls {len(PILOT_FEEDS)} company feeds and "
        f"{len(EDITORIAL_SOURCE_REGISTRY)} market-news sources. Market pricing is the exception — "
        "every price, breadth, and rotation figure in this build comes from a built-in illustrative "
        "data provider, not a market data feed, and nothing in the product prices a security."
    )

    section_header("The four labels")
    for ct, description in [
        (ClaimType.FACT, "Stated in a source document, and attributed to it. No attribution, no label."),
        (ClaimType.INTERPRETATION, "A market read built on facts shown alongside it."),
        (ClaimType.INFERENCE, "Follows logically from the evidence but is not confirmed anywhere."),
        (ClaimType.UNCERTAINTY, "A named open question. Recorded rather than smoothed over."),
    ]:
        with st.container(border=True, key=f"card-legend-{ct.value}"):
            cols = st.columns([1, 4])
            with cols[0]:
                claim_type_badge(ct)
            with cols[1]:
                st.write(description)

    section_header("Repeats and prior disclosure")
    st.write(
        "Two checks run, and neither is a novelty check against press coverage. In the Signals feed, an "
        "item already stored is not published again (each story's identity is derived from its company "
        "and canonical URL), and the same development arriving under a different URL from a second feed "
        "is suppressed rather than duplicated. For filings, a disclosure can be compared against the "
        "same issuer's own earlier comparable filing; that comparison reports only whether the set of "
        "detection categories changed, which is not a claim that the business, the financials, or the "
        "meaning changed."
    )
    st.write(
        "What does not exist: no check against outside press coverage, and no verification that a "
        "periodic report's earnings, guidance, or risk-factor content is new. A repeat is suppressed; "
        "novelty is not asserted."
    )

    section_header("Contrary evidence")
    st.write(
        "Every signal carries a contrary-evidence field, and when nothing has been recorded in it the "
        "field says so rather than being hidden — an absent counter-argument and an unexamined one are "
        "different things. Nothing searches for contrary evidence automatically: a signal promoted from "
        "a filing is recorded as not yet reviewed, and it keeps saying that until a person writes "
        "something there."
    )

    section_header("Freshness")
    st.write(
        "Two freshness statements exist, each derived from what the relevant background worker actually "
        "recorded in the database — never from how recent the newest item on screen happens to look. "
        "Filings carries a line computed from each venue's own last successful scan. Signals carries an "
        "ingestion line reporting when a run was last recorded as completed, whether the latest "
        "attempt reported a failure, whether the last recorded run is itself no longer recent, or that "
        "nothing has been recorded at all. A recorded run means the worker ran — not that anything new "
        "arrived."
    )
    st.write(
        "Neither line establishes that every source was reached. Per-source results are recorded for "
        "company feeds and not for market-news sources, so a completed run means the worker ran, not "
        "that the picture is complete — which is why an empty feed is never presented here as a "
        "confirmed quiet period. Where the stored record cannot support a claim, these lines report the "
        "status as unknown instead of guessing, and not every panel carries an indicator of its own."
    )

