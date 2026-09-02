"""Command palette (brief §12) — ⌘K/Ctrl+K fuzzy search over themes,
signals, and actions. The former "Companies"/"Watchlists"/"Recent
research"/"New research thread" groups were removed (reader-facing
data-integrity pass, design/DECISIONS.md) along with the Company,
Watchlists, and Research pages they pointed at — none had live real
data.

Streamlit has no native palette. Two things had to be tested for real
feasibility (per the brief's own instruction) rather than assumed:

1. **A global ⌘K listener.** `st.iframe` (with a raw HTML/script string —
   the "st.components.v1.html" pattern, since renamed) renders in a
   sandboxed same-origin iframe — a `keydown` listener attached only inside that
   iframe would never fire while focus is anywhere else on the page. It
   DOES work to reach into `window.parent.document` from inside the iframe
   (same-origin), attach the listener there, and `.click()` a real
   Streamlit button found via its stable `st-key-{key}` class. That's what
   `render_palette_trigger()` does — the shortcut drives an actual
   Streamlit button/rerun, not a fake overlay. Verified working in-browser.
2. **Arrow-key result navigation, a literal ⌘↵/↵, and ⌘↵-new-thread.**
   `st.text_input` commits its value on Enter *or* blur with no public way
   to tell those two apart — so "Enter opens the top result" can't be
   built as a real global keyboard behavior without a custom component
   (ruled out: "stays Streamlit, no React"). Search still works — typing
   then pressing Enter (or clicking away) reruns and filters the grouped
   list live, first result visibly bolded — but opening the top result is
   an explicit, honestly-labeled **button** rather than an intercepted
   keypress. ↑/↓ and ⌘↵ are click/tap only. Logged in design/DECISIONS.md.

`Esc` closing and focus restoring on close are `st.dialog`'s own native
behavior — free, not reimplemented here.
"""
from __future__ import annotations

import streamlit as st

from src.ui.ui import get_page

PALETTE_TRIGGER_KEY = "cmdk-trigger"


def render_palette_trigger() -> None:
    """Sidebar search button showing the ⌘K hint, plus the JS that makes
    the real keyboard shortcut open it from anywhere in the workspace."""
    with st.container(key=f"cta-secondary-{PALETTE_TRIGGER_KEY}"):
        if st.button("Search…    ⌘K", key=PALETTE_TRIGGER_KEY, width="stretch"):
            _open_palette()

    st.iframe(
        f"""
        <script>
        (function() {{
            var doc = window.parent.document;
            if (doc.__eevaCmdKBound) return;
            doc.__eevaCmdKBound = true;
            doc.addEventListener('keydown', function(e) {{
                if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {{
                    var btn = doc.querySelector('.st-key-{PALETTE_TRIGGER_KEY} button');
                    if (btn) {{ e.preventDefault(); btn.click(); }}
                }}
            }});
        }})();
        </script>
        """,
        height=1,
    )


def _index(ctx) -> list[dict]:
    items: list[dict] = []
    for th in ctx.theme_repository.get_all_themes():
        items.append({"group": "Themes", "label": th.name, "sub": f"{len(th.subthemes)} subcategories", "go": "themes"})
    for s in ctx.signal_repository.get_all_signals():
        items.append({"group": "Signals", "label": s.title, "sub": s.theme_slug, "go": "signals"})
    items.append({"group": "Actions", "label": "Open Methodology", "sub": "", "go": "methodology"})
    return items


def _matches(item: dict, query: str) -> bool:
    q = query.lower()
    return q in item["label"].lower() or q in item["sub"].lower() or q in item["group"].lower()


def _open_palette() -> None:
    from src.data_access.container import get_repositories

    @st.dialog("Search", width="large")
    def _dialog() -> None:
        ctx = get_repositories()
        # Plain (non-form) text_input — Streamlit commits its value on
        # Enter *or* blur, with no public way to tell those apart, so a
        # literal "Enter opens the top result" can't be built without a
        # custom component (see module docstring). The button below is an
        # explicit, honestly-labeled action instead of overloading Enter.
        query = st.text_input(
            "Search", placeholder="Search themes, signals…",
            label_visibility="collapsed", key="cmdk-query",
        )
        items = _index(ctx)
        shown = [i for i in items if _matches(i, query)] if query else items

        with st.container(key="cta-secondary-cmdk-open-top"):
            open_top = st.button("Open top result ↵", key="cmdk-open-top", width="stretch", disabled=not shown)
        if open_top and shown:
            _navigate(shown[0])
            return

        if not shown:
            st.markdown('<div class="er-muted">No matches. Try a theme or signal.</div>', unsafe_allow_html=True)
            return

        last_group = None
        for n, item in enumerate(shown[:25]):
            if item["group"] != last_group:
                st.markdown(f'<div class="er-section-label" style="margin-top:0.75rem;">{item["group"]}</div>', unsafe_allow_html=True)
                last_group = item["group"]
            row_cols = st.columns([5, 2])
            with row_cols[0]:
                label = f"**{item['label']}**" if n == 0 else item["label"]
                if st.button(label, key=f"cmdk-result-{n}", width="stretch"):
                    _navigate(item)
            with row_cols[1]:
                st.markdown(f'<div class="er-muted" style="text-align:right; padding-top:0.4rem;">{item["sub"]}</div>', unsafe_allow_html=True)

        st.markdown(
            '<div class="er-muted" style="margin-top:0.75rem; font-size:11px;">'
            "Search commits on Enter or click-away · \"Open top result\" opens the first match · "
            "click any row to open it directly · Esc closes</div>",
            unsafe_allow_html=True,
        )

    _dialog()


def _navigate(item: dict) -> None:
    page = get_page(item["go"])
    if page is None:
        return
    st.switch_page(page)
