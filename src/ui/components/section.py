"""Section labels — 13px Inter in --text-3 (brief §5: "Do not use uppercase
letterspaced mono for section headers — that reads as a Bloomberg
terminal"). Deliberately not st.subheader, which renders as a large bold
native heading with none of that."""
from __future__ import annotations

import streamlit as st


def section_header(title: str, subtitle: str | None = None) -> None:
    st.markdown(f'<div class="er-section-label">{title}</div>', unsafe_allow_html=True)
    if subtitle:
        st.markdown(f'<div class="er-muted">{subtitle}</div>', unsafe_allow_html=True)
