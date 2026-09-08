"""Loading skeletons (brief §14) — match real row geometry so nothing
shifts when data lands; subtle 1.4s shimmer via .er-skel-line in
assets/styles.css. This build's demo data loads synchronously (no real
network latency to cover), so these aren't wired into a live loading path
anywhere — provided for spec-completeness and for Phase 2, when a real
provider call can take a moment. The absence of bare spinners anywhere in
the app (grepped, none found) already satisfies the acceptance-checklist
line on its own.
"""
from __future__ import annotations

import streamlit as st


def skeleton_card(lines: int = 3, key: str | None = None) -> None:
    """Placeholder shaped like a signal_card/evidence_row — title-width
    line, then `lines` body-width lines."""
    with st.container(border=True, key=f"card-skel-{key}" if key else None):
        st.markdown('<div class="er-skel-line" style="width:55%; height:1rem;"></div>', unsafe_allow_html=True)
        for i in range(lines):
            width = "90%" if i < lines - 1 else "40%"
            st.markdown(f'<div class="er-skel-line" style="width:{width};"></div>', unsafe_allow_html=True)


def skeleton_rows(count: int = 3, lines: int = 3) -> None:
    for i in range(count):
        skeleton_card(lines=lines, key=str(i))
