"""Evidence spine — the left-gutter vertical rule used on Research claim
rows (brief §7). Each claim gets a 2px bar | 96px chip column | body grid;
consecutive rows' bars abut exactly so they read as one continuous line
down the left edge of an answer. Below 860px the chip column collapses to
inline (see .er-spine media rule in assets/styles.css) while the bar
persists.
"""
from __future__ import annotations

import streamlit as st

from src.models.models import ClaimType
from src.ui.components.evidence_chips import evidence_chip_html


def evidence_spine_row(
    text: str,
    claim_type: ClaimType,
    has_source: bool = True,
    excerpt: str | None = None,
    excerpt_original: str | None = None,
    source_label: str | None = None,
    is_first: bool = False,
    is_last: bool = False,
    answer_claim_type: ClaimType | None = None,
) -> None:
    """`answer_claim_type` (Phase C, editorial-simplicity pass): repeating
    a full evidence-type chip on every claim row when it just matches the
    answer's own headline type is visual noise once that type is already
    named once in the answer-level metadata line above — the chip only
    renders here when this claim's type is an *exception* to the
    answer-level type (e.g. an Uncertainty claim inside an Interpretation
    answer). Left as None (the default), every row's chip renders exactly
    as before this parameter existed — no caller is required to pass it.

    `evidence_chip_html` is still always called, whether or not its output
    is used, so its fail-loud guard against an unattributed Fact chip
    (UnlinkedFactChipError) keeps running unconditionally — suppressing the
    *chip* must never also suppress that safety check."""
    bar_cls = "er-spine-bar"
    if is_first:
        bar_cls += " first"
    if is_last:
        bar_cls += " last"

    body_html = f"<div>{text}</div>"
    if excerpt:
        original_html = f'<span class="er-excerpt" style="display:block; opacity:0.75; margin-bottom:0.15rem;">{excerpt_original}</span>' if excerpt_original else ""
        body_html += f'<div class="er-excerpt">{original_html}{excerpt}</div>'
    if source_label:
        body_html += f'<div class="er-spine-source">{source_label}</div>'

    chip_html = evidence_chip_html(claim_type, has_source=has_source)
    is_exception = answer_claim_type is not None and claim_type != answer_claim_type
    show_chip = answer_claim_type is None or is_exception
    chip_column_html = chip_html if show_chip else ""

    st.markdown(
        f"""
        <div class="er-spine">
            <div class="{bar_cls}"></div>
            <div class="er-spine-chip">{chip_column_html}</div>
            <div class="er-spine-body">{body_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
