"""Source-excerpt rendering — one shared implementation used by evidence
rows, the evidence spine, and the signal drawer, so "original above
translation, one tone dimmer, ending in a <cite>" (brief §7) never drifts
between call sites. The CJK fallback lives in styles.css's --font-serif
stack (Source Serif 4 has no Japanese/Korean/Chinese glyphs)."""
from __future__ import annotations

import streamlit as st

from src.logic.evidence import cite_label
from src.models.models import EvidenceItem


def excerpt_html(evidence: EvidenceItem) -> str:
    original_html = (
        f'<div class="er-excerpt" style="opacity:0.75; margin-bottom:0.3rem;">{evidence.excerpt_original}</div>'
        if evidence.excerpt_original else ""
    )
    return (
        f'{original_html}<div class="er-excerpt">{evidence.excerpt}</div>'
        f'<cite class="er-spine-source" style="display:block; margin-top:0.35rem; font-style:normal;">{cite_label(evidence)}</cite>'
    )


def render_excerpt(evidence: EvidenceItem) -> None:
    st.markdown(excerpt_html(evidence), unsafe_allow_html=True)
