import streamlit as st

from src.ui.ui import NAV_ITEMS, load_css, render_sidebar

# Minimal stand-in Page objects (not real navigation) so render_sidebar's
# st.page_link calls have something valid to target — mirrors app.py's
# real pages dict shape without needing the actual page render functions.
st.session_state["_pages"] = {key: st.Page((lambda: None), title=label, url_path=key) for key, label in NAV_ITEMS}

load_css()
render_sidebar("dashboard")
