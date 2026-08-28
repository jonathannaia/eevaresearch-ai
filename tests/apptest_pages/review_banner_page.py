import streamlit as st

from src.ui.ui import with_chrome


def _noop_page() -> None:
    st.markdown("noop-page-content-marker", unsafe_allow_html=False)


with_chrome(_noop_page, "dashboard", show_sidebar=False)()
