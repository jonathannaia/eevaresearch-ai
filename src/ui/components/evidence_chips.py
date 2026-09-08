"""Evidence chips — the app's "signature element" (brief §7). Custom HTML,
not st.badge: st.badge's fixed color enum can't produce the fill/tint/
outline/dashed treatment the four claim types need, and can't enforce that
a Fact chip is backed by an identifiable source.

A Fact chip asserts "this is stated in a source document." In this
foundation phase there are no external source URLs (demo data only, by a
carried-forward project rule) — so "backed by a source" here means the
evidence has real source attribution (source_name), not a live external
link. `evidence_chip` fails loudly if a FACT claim has no attribution at
all, which is the actual bug the brief is guarding against.
"""
from __future__ import annotations

import streamlit as st

from src.models.models import ClaimType

_CHIP_CLASS = {
    ClaimType.FACT: "er-chip-fact",
    ClaimType.INTERPRETATION: "er-chip-interpretation",
    ClaimType.INFERENCE: "er-chip-inference",
    ClaimType.UNCERTAINTY: "er-chip-uncertainty",
}


class UnlinkedFactChipError(ValueError):
    """Raised when a Fact chip would render with no source attribution at
    all — fail loudly in dev rather than render a Fact claim that looks
    sourced but isn't backed by anything (brief §7)."""


def evidence_chip_html(claim_type: ClaimType, has_source: bool = True) -> str:
    if claim_type == ClaimType.FACT and not has_source:
        raise UnlinkedFactChipError(
            "A Fact chip must be backed by source attribution — got claim_type=FACT with has_source=False."
        )
    cls = _CHIP_CLASS.get(claim_type, "er-chip-interpretation")
    return f'<span class="er-chip {cls}">{claim_type.value}</span>'


def evidence_chip(claim_type: ClaimType, has_source: bool = True) -> None:
    st.markdown(evidence_chip_html(claim_type, has_source=has_source), unsafe_allow_html=True)
