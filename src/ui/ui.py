"""Shared UI shell: the persistent left sidebar, global CSS loader, and
footer — replaces chrome.py's top-nav-based design (round 3) with the
sidebar-based IA from design/eevaresearch-brief.md §4. `st.navigation`
still drives routing/`st.Page` objects; only the nav *widget* itself moved
from a custom top header to `st.sidebar` + real `st.page_link`s.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Callable

import streamlit as st

from src.config.settings import APP_NAME, APP_VERSION

METHODOLOGY_STATEMENT = (
    "EevaResearch separates source-backed facts, market interpretation, model "
    "inference, and uncertainty. It is for informational research only and "
    "does not provide investment advice."
)

# Primary sidebar nav ("WORKSPACE" group), in order — navigation-cleanup
# pass (design/DECISIONS.md): exactly the four core destinations. Home and
# Company are deliberately excluded (Home is first-visit-only with no
# sidebar at all; Company is reached only by clicking a ticker). Coverage,
# Themes, Signals, and Research are intentionally NOT here any more — see
# HIDDEN_FROM_NAV below; their pages/routes/data are untouched, they are
# simply no longer linked from any visible sidebar group.
PRIMARY_NAV: list[tuple[str, str]] = [
    ("dashboard", "Dashboard"),
    ("radar_inbox", "Filings"),
    # Themes (Evidence-First Themes MVP, design/DECISIONS.md) — the
    # public, curated cross-company research narrative surface. Placed
    # directly beneath Filings: Filings surfaces individual captured
    # filings, Themes connects official evidence across companies into
    # a testable thesis. The legacy demo ticker/theme/subtheme browser
    # that previously occupied this url_path/nav slot was removed
    # entirely (reader-facing data-integrity pass, design/DECISIONS.md)
    # rather than kept as a hidden route — it had no live real data of
    # its own.
    ("themes", "Themes"),
    # Daily News (Slice 1, design/DECISIONS.md) — an independent, separately-
    # scoped autonomous discovery surface, not a Filings view (see the
    # Radar-vs-Daily-News product clarification the same document records).
    # Placed next to Filings for IA/UX grouping only ("what's new" feeds
    # together) — this has no bearing on their code/data independence.
    ("daily_news", "Daily News"),
]

# Lower-priority "SYSTEM" group in the sidebar (navigation-cleanup pass) —
# reuses the existing Coverage page/route verbatim (smallest routing
# footprint: same st.Page/url_path, only the sidebar link text differs) as
# the one "Methodology & Coverage" entry. A "Settings" entry belongs here
# too once a real Settings route exists; none does yet, so it's
# deliberately omitted rather than inventing a new page for it.
SYSTEM_NAV: list[tuple[str, str]] = [
    ("coverage", "Methodology & Coverage"),
]

# Routes that stay fully registered and reachable — direct URL, the
# command palette (src/ui/components/command_palette.py), and existing
# in-page cross-links all still work exactly as before — but are no
# longer linked from any visible sidebar group (navigation-cleanup pass,
# design/DECISIONS.md). Signals stays conceptually part of Radar, not a
# separate visible destination. Watchlists and Research (the canned-
# demo-answer chat) were removed entirely (reader-facing data-integrity
# pass, design/DECISIONS.md) rather than kept as hidden routes — neither
# had any live real data of its own; likewise Company, previously
# reachable only via query params, not this list.
HIDDEN_FROM_NAV: list[tuple[str, str]] = [
    ("signals", "Signals"),
    ("methodology", "Methodology"),
    ("about", "About"),
]

# Session-state keys for unread/last-seen tracking (brief §10) — defined
# here rather than in logic/unread.py since that module stays Streamlit-free.
LAST_SEEN_KEY = "signals_last_seen_at"
READ_IDS_KEY = "read_signal_ids"

# Streamlit remembers the sidebar's collapsed/expanded state in the
# browser's own localStorage, independent of Python session state and
# outliving `initial_sidebar_state="expanded"` (set in app.py) — a sidebar
# collapsed once, even in an earlier unrelated visit to this origin, stays
# collapsed on every fresh open afterward, including at desktop widths,
# which reads as broken navigation. Corrected once per session only (see
# _correct_sidebar_state_for_width below), not every rerun, so it doesn't
# fight a user who deliberately collapses it mid-session.
_SIDEBAR_FORCE_CHECK_KEY = "_sidebar_force_checked"
_SIDEBAR_DESKTOP_BREAKPOINT_PX = 768

# Kept for any code that still enumerates "every visible page" (e.g. a
# future command-palette index) — every page linked from a visible
# sidebar group, in nav order. HIDDEN_FROM_NAV is deliberately excluded.
NAV_ITEMS: list[tuple[str, str]] = PRIMARY_NAV + SYSTEM_NAV

_LOGO_PATH = Path(__file__).resolve().parents[2] / "assets" / "eeva-logo.png"
_CSS_PATH = Path(__file__).resolve().parents[2] / "assets" / "styles.css"

_BRAND_MARK_SVG = """
<svg viewBox="0 0 24 24" fill="none">
    <rect x="3" y="4" width="18" height="2.2" rx="1.1" fill="currentColor"/>
    <rect x="3" y="11" width="13" height="2.2" rx="1.1" fill="currentColor"/>
    <rect x="3" y="18" width="18" height="2.2" rx="1.1" fill="currentColor"/>
    <circle cx="20" cy="12.1" r="1.6" fill="currentColor"/>
</svg>
"""


@st.cache_data(show_spinner=False)
def _logo_data_uri() -> str | None:
    if not _LOGO_PATH.exists():
        return None
    encoded = base64.b64encode(_LOGO_PATH.read_bytes()).decode()
    return f"data:image/png;base64,{encoded}"


@st.cache_data(show_spinner=False)
def _css_text_cached(mtime: float) -> str:
    # `mtime` (the file's own last-modified time) is the cache key, not
    # the CSS content or path — a `st.cache_data` singleton, invalidated
    # automatically the instant the file changes on disk. This is what
    # lets this be cached at all without reintroducing the dev-reload
    # problem the previous, deliberately-uncached version called out.
    # Deliberately NOT underscore-prefixed: Streamlit excludes
    # underscore-prefixed parameters from the cache key entirely (see
    # radar_inbox.py's own `_settings` convention for the same rule used
    # the other way around) — this argument is exactly what must be
    # hashed for invalidation to work at all.
    return _CSS_PATH.read_text(encoding="utf-8")


def _css_text() -> str:
    if not _CSS_PATH.exists():
        return ""
    try:
        mtime = _CSS_PATH.stat().st_mtime
    except OSError:
        # Fall back to an uncached read rather than fail — matches the
        # pre-existing behavior for any filesystem hiccup.
        return _CSS_PATH.read_text(encoding="utf-8")
    return _css_text_cached(mtime)


def load_css() -> None:
    css = _css_text()
    if css:
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def brand_mark_html(size_px: int | None = None) -> str:
    uri = _logo_data_uri()
    style = f' style="width:{size_px}px;height:{size_px}px;"' if size_px else ""
    if uri:
        return f'<img src="{uri}" alt="" {style}/>'
    return _BRAND_MARK_SVG


def _correct_sidebar_state_for_width() -> None:
    """Runs once per session (see module docstring above the session-state
    keys) to match the sidebar's open/closed state to the current viewport
    on load: expanded at desktop/laptop widths, collapsed at narrow/mobile
    widths — correcting whatever a prior, unrelated visit to this origin
    left in the browser's localStorage. Checked in an iframe/script, same
    pattern as research.py's composer-focus trick, since Streamlit doesn't
    execute <script> tags placed directly via st.markdown."""
    if st.session_state.get(_SIDEBAR_FORCE_CHECK_KEY):
        return
    st.session_state[_SIDEBAR_FORCE_CHECK_KEY] = True
    st.iframe(
        "<script>"
        "var w = window.parent;"
        f"var desktop = w.innerWidth >= {_SIDEBAR_DESKTOP_BREAKPOINT_PX};"
        "var expandBtn = w.document.querySelector('[data-testid=\"stExpandSidebarButton\"]');"
        "var collapseBtn = w.document.querySelector('[data-testid=\"stSidebarCollapseButton\"] button');"
        "if (desktop && expandBtn) { expandBtn.click(); }"
        "else if (!desktop && collapseBtn) { collapseBtn.click(); }"
        "</script>",
        height=1,
    )


def render_sidebar(current_key: str) -> None:
    _correct_sidebar_state_for_width()

    pages = st.session_state.get("_pages", {})
    home_page = pages.get("home")

    with st.sidebar:
        st.markdown('<div class="er-rail-brand">', unsafe_allow_html=True)
        brand_cols = st.columns([1, 5], vertical_alignment="center")
        with brand_cols[0]:
            st.markdown(f'<span class="er-rail-logo">{brand_mark_html()}</span>', unsafe_allow_html=True)
        with brand_cols[1]:
            if home_page is not None:
                st.page_link(home_page, label="EevaResearch")
            else:
                st.markdown('<span class="er-rail-word">EevaResearch</span>', unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

        from src.ui.components.command_palette import render_palette_trigger

        render_palette_trigger()

        # WORKSPACE — the four core visible destinations (navigation-cleanup
        # pass, design/DECISIONS.md). Coverage/Themes/Signals/Research and
        # the old "My watchlists"/per-list-shortcut/"Recent research"
        # sections are gone from here — see PRIMARY_NAV/HIDDEN_FROM_NAV.
        st.markdown('<div class="er-rail-group-label">Workspace</div>', unsafe_allow_html=True)
        for key, label in PRIMARY_NAV:
            page = pages.get(key)
            if page is None:
                continue
            active_cls = "er-rail-navactive" if key == current_key else ""
            with st.container(key=f"navitem-{key}"):
                if active_cls:
                    st.markdown(f'<div class="{active_cls}">', unsafe_allow_html=True)
                st.page_link(page, label=label)
                if active_cls:
                    st.markdown("</div>", unsafe_allow_html=True)

        # SYSTEM — lower-priority destinations (navigation-cleanup pass).
        # A Settings entry belongs here once a real Settings route exists.
        st.markdown('<div class="er-rail-group-label">System</div>', unsafe_allow_html=True)
        st.markdown('<div class="er-rail-footlinks">', unsafe_allow_html=True)
        for key, label in SYSTEM_NAV:
            page = pages.get(key)
            if page is not None:
                st.page_link(page, label=label)
        st.markdown("</div>", unsafe_allow_html=True)

        # Reader-facing data-integrity pass (design/DECISIONS.md): the
        # previous blanket "Demo environment · sample data" status was
        # removed rather than replaced with an equally blanket claim —
        # some pages (Radar, Themes, Daily News, Signals, parts of
        # Dashboard) are real and live, others (About's theme cards,
        # Methodology/Disclaimer's own copy) are not yet, and there is
        # no single truthful word for the whole app at once.


# Full text stays on Methodology/Disclaimer (which already carry this
# content in their own page body — see methodology.py) and, redundantly
# but harmlessly, in this longer footer variant reserved for just those
# two pages. Every other page gets the compact one-liner below instead of
# repeating it at the same length (usability follow-up).
#
# Reader-facing data-integrity pass (design/DECISIONS.md): the previous
# "Sample data only"/"demo/mock data only"/"Foundation phase (demo
# data)" blanket claims are removed — several pages (Radar, Themes,
# Daily News, Signals, parts of Dashboard) are real and live now, so a
# blanket demo/sample claim on every page's footer would be false.
_COMPACT_FOOTER_TEXT = "Evidence-first research · Not investment advice"
_FULL_FOOTER_PAGES = {"methodology", "disclaimer"}


def render_footer(nav_key: str | None = None) -> None:
    if nav_key in _FULL_FOOTER_PAGES:
        st.markdown(
            f"""
            <div class="er-footer">
                <div>{APP_NAME} is evidence-first: every claim is labeled Fact, Interpretation,
                Inference, or Uncertainty, and material claims link to their source.</div>
                <div style="margin-top:0.4rem;">{METHODOLOGY_STATEMENT}</div>
                <div class="er-footer-version">{APP_NAME} v{APP_VERSION}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div class="er-footer er-footer-compact">{_COMPACT_FOOTER_TEXT}</div>',
            unsafe_allow_html=True,
        )
    # "Disclaimer" link on every page footer (brief §17 — one of the four
    # disclaimer placements, deliberately not a dismiss-once banner).
    disclaimer_page = get_page("disclaimer")
    if disclaimer_page is not None:
        with st.container(key="cta-tertiary-footer-disclaimer"):
            st.page_link(disclaimer_page, label="Disclaimer")


def get_page(name: str):
    return st.session_state.get("_pages", {}).get(name)


def with_chrome(page_fn: Callable[[], None], nav_key: str, show_sidebar: bool = True) -> Callable[[], None]:
    def _wrapped() -> None:
        load_css()
        if show_sidebar:
            render_sidebar(nav_key)

        with st.container(key="page-content"):
            page_fn()
        render_footer(nav_key)

    _wrapped.__name__ = getattr(page_fn, "__name__", "page")
    return _wrapped
