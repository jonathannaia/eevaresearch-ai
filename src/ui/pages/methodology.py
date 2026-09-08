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

from src.models.models import ClaimType
from src.ui.components.badges import claim_type_badge
from src.ui.components.section import section_header
from src.ui.ui import METHODOLOGY_STATEMENT, get_page


def render() -> None:
    st.markdown('<div class="er-page-title">Methodology</div>', unsafe_allow_html=True)
    st.write(METHODOLOGY_STATEMENT)

    disclaimer_page = get_page("disclaimer")
    if disclaimer_page is not None:
        with st.container(key="cta-secondary-methodology-disclaimer-top"):
            st.page_link(disclaimer_page, label="Read the full disclaimer →")

    section_header("Where the material comes from")
    st.write(
        "Signals are extracted from primary filings across five venues — EDGAR, TDnet, DART, CNINFO, "
        "and HKEX — rather than from press coverage. A large share of what matters appears first in a "
        "footnote, a segment note, or a Japanese- or Korean-language disclosure that English coverage "
        "does not pick up for days. In this foundation phase, every filing referenced is demo data — "
        "no live filing feed is connected yet."
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

    section_header("Novelty check")
    st.write(
        "Before a signal is published it is checked against prior coverage by source and date. If the "
        "material has already been reported, it is not a signal."
    )

    section_header("Contrary evidence")
    st.write(
        "Every signal carries a contrary-evidence field. When none has been recorded, that is stated "
        "explicitly rather than hiding the section — an absent counter-argument and an unexamined one "
        "are different things."
    )

    section_header("Freshness")
    st.write(
        "Every panel carries its own fetch timestamp and one of three states: Live (a connected data "
        "source, current), Stale (a connected source that hasn't refreshed recently), or Demo (this "
        "phase's default — no live source connected). Demo mode is active everywhere in this build."
    )

