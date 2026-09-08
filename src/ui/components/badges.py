"""Badge/indicator components. Claim-type rendering delegates to
evidence_chips.py (brief §7's custom HTML chip system replaces st.badge for
that one case). Freshness/strength badges use native st.badge.

Direction is glyph + text-weight, with a restrained color accent added in
the UX-refinement pass: muted green/rose/amber on small dots, thin rails,
and compact status tags only — never large fills, never a return to a
colored visual system. See assets/styles.css's :root note for the exact
scope of what's allowed.
"""
from __future__ import annotations

import streamlit as st

from src.models.models import Direction, EvidenceItem, Strength, ClaimType
from src.ui.components.evidence_chips import evidence_chip

_FRESHNESS_COLOR = {"Fresh": "green", "Aging": "orange", "Stale": "gray", "Unknown": "gray"}

_STRENGTH_COLOR = {Strength.STRONG: "green", Strength.MODERATE: "yellow", Strength.WEAK: "gray"}

_DIRECTION_GLYPH = {
    Direction.IMPROVING: "▲",
    Direction.WEAKENING: "▼",
    Direction.EMERGING: "●",
    Direction.MIXED: "◆",
}

_DIRECTION_WEIGHT_CLASS = {
    Direction.IMPROVING: "er-dir",
    Direction.WEAKENING: "er-dir er-dir-weakening",
    Direction.EMERGING: "er-dir",
    Direction.MIXED: "er-dir er-dir-mixed",
}

# Semantic accent bucket per direction: positive (green) / negative (rose) /
# mixed (amber). EMERGING reads as "watch" rather than a firm positive or
# negative, so it buckets with mixed/amber per the approved definition
# ("amber: mixed / watch / uncertainty").
_DIRECTION_ACCENT = {
    Direction.IMPROVING: "pos",
    Direction.WEAKENING: "neg",
    Direction.MIXED: "mix",
    Direction.EMERGING: "mix",
}


def claim_type_badge(claim_type: ClaimType, has_source: bool = True) -> None:
    evidence_chip(claim_type, has_source=has_source)


def freshness_badge(evidence: EvidenceItem) -> None:
    label = evidence.freshness_label
    st.badge(label, color=_FRESHNESS_COLOR.get(label, "gray"))


def strength_badge(strength: Strength) -> None:
    st.badge(strength.value, color=_STRENGTH_COLOR.get(strength, "gray"))


def demo_badge(label: str = "Demo data") -> None:
    st.badge(label, color="gray")


def direction_accent(direction: Direction) -> str:
    """'pos' / 'neg' / 'mix' — the restrained color bucket for this
    direction, used to pick an er-glyph-*/er-rail-*/er-tag-* class."""
    return _DIRECTION_ACCENT.get(direction, "mix")


def direction_dot_html(direction: Direction) -> str:
    """Glyph + label, weight-differentiated, with a small color accent on
    the glyph only — the label text itself stays neutral."""
    glyph = _DIRECTION_GLYPH.get(direction, "●")
    cls = _DIRECTION_WEIGHT_CLASS.get(direction, "er-dir")
    accent = direction_accent(direction)
    return f'<span class="{cls}"><span class="er-dir-glyph er-glyph-{accent}">{glyph}</span>{direction.value}</span>'


def direction_rail_class(direction: Direction) -> str:
    """CSS class for a card's thin left-rail accent (e.g. Dashboard's
    Priority Signals rows)."""
    return f"er-rail-{direction_accent(direction)}"


def direction_status_tag_html(direction: Direction) -> str:
    """A compact tinted pill for tight spaces (Theme Health cards)."""
    glyph = _DIRECTION_GLYPH.get(direction, "●")
    accent = direction_accent(direction)
    return f'<span class="er-status-tag er-tag-{accent}">{glyph} {direction.value}</span>'
