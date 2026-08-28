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

from src.config.settings import APP_NAME, APP_VERSION, get_settings

METHODOLOGY_STATEMENT = (
    "EevaResearch separates source-backed facts, market interpretation, model "
    "inference, and uncertainty. It is for informational research only and "
    "does not provide investment advice."
)

# Primary sidebar nav, in order — see brief §4's route table. Home and
# Company are deliberately excluded (Home is first-visit-only with no
# sidebar at all; Company is reached only by clicking a ticker).
PRIMARY_NAV: list[tuple[str, str]] = [
    ("dashboard", "Dashboard"),
    ("radar_inbox", "Radar Inbox"),
    # Coverage (Phase A, Issuer Registry — design/ISSUER_REGISTRY_FOUNDATION.md):
    # a read-only observability map over the registry, placed next to Radar
    # Inbox since both are "what's in our system" views, distinct from the
    # theme-browsing/published-Signal pages that follow.
    ("coverage", "Coverage"),
    ("themes", "Themes"),
    ("signals", "Signals"),
    ("research", "Research"),
]

# Static doc pages — "REFERENCE" group in the sidebar (UX-refinement pass:
# Disclaimer is no longer a primary destination here, only reachable via
# Methodology's own cross-link and the page footer).
FOOTER_NAV: list[tuple[str, str]] = [
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
# future command-palette index) — primary + footer, in nav order.
NAV_ITEMS: list[tuple[str, str]] = PRIMARY_NAV + FOOTER_NAV

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


def _css_text() -> str:
    # Deliberately uncached (unlike the logo data-URI) — it's a cheap local
    # read, and caching it meant CSS edits needed a process restart to
    # take effect during development.
    if not _CSS_PATH.exists():
        return ""
    return _CSS_PATH.read_text(encoding="utf-8")


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
    from src.data_access.container import get_repositories
    from src.logic.unread import unread_count

    _correct_sidebar_state_for_width()

    pages = st.session_state.get("_pages", {})
    home_page = pages.get("home")

    unread = 0
    if "signals" in pages:
        ctx = get_repositories()
        unread = unread_count(
            ctx.signal_repository.get_all_signals(),
            st.session_state.get(LAST_SEEN_KEY),
            st.session_state.get(READ_IDS_KEY, set()),
        )

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

        # WORKSPACE — the four core pages (UX-refinement pass groups these
        # explicitly apart from personal watchlists and reference docs).
        st.markdown('<div class="er-rail-group-label">Workspace</div>', unsafe_allow_html=True)
        for key, label in PRIMARY_NAV:
            page = pages.get(key)
            if page is None:
                continue
            active_cls = "er-rail-navactive" if key == current_key else ""
            with st.container(key=f"navitem-{key}"):
                if active_cls:
                    st.markdown(f'<div class="{active_cls}">', unsafe_allow_html=True)
                if key == "signals" and unread:
                    # A real separate badge, not text folded into the link
                    # label (st.page_link only accepts a plain string) —
                    # rendered beside it via a narrow second column.
                    link_col, badge_col = st.columns([5, 1.4], vertical_alignment="center")
                    with link_col:
                        st.page_link(page, label=label)
                    with badge_col:
                        st.markdown(f'<span class="er-nav-badge">{unread}</span>', unsafe_allow_html=True)
                else:
                    st.page_link(page, label=label)
                if active_cls:
                    st.markdown("</div>", unsafe_allow_html=True)

        # MY WATCHLISTS — group header links to the standalone Watchlists
        # page (restored in the UX-refinement pass); each list name below
        # stays a quick-filter shortcut straight into Signals, unchanged.
        watchlists_page = pages.get("watchlists")
        if watchlists_page is not None:
            # A plain st.markdown('<div>') doesn't wrap subsequent st.*
            # calls as children (confirmed elsewhere in this codebase) —
            # so the group-label look comes from a CSS rule targeting this
            # container's key instead of nesting.
            with st.container(key="navitem-watchlists-header"):
                st.page_link(watchlists_page, label="My Watchlists")
        else:
            st.markdown('<div class="er-rail-group-label">My Watchlists</div>', unsafe_allow_html=True)

        from src.ui.pages.watchlists import WATCHLIST_NAMES, seed_watchlists

        signals_page = pages.get("signals")
        if "watchlists" not in st.session_state:
            st.session_state["watchlists"] = seed_watchlists()
        lists = st.session_state["watchlists"]
        for name in WATCHLIST_NAMES:
            count = len(lists.get(name, []))
            if signals_page is not None:
                # Streamlit highlights a page_link as "current" whenever its
                # target Page object matches the active page, regardless of
                # query string — so all four of these would light up at once
                # just from being on Signals. A dedicated container key lets
                # CSS strip that native highlight back to the plain nav look.
                with st.container(key=f"navitem-watchlist-{name}"):
                    st.page_link(signals_page, label=f"{name} ({count})", query_params={"watchlist": name})
            else:
                st.markdown(f'<div class="er-muted" style="padding:0.2rem 0.5rem;">{name} ({count})</div>', unsafe_allow_html=True)

        # Recent research — last 5-8 threads by question text (brief §4).
        # "Thread" here is just the asked question string (research.py has
        # no richer thread object); session-only, like watchlists.
        research_page = pages.get("research")
        asked = st.session_state.get("chat_messages", [])
        if asked and research_page is not None:
            recent = list(dict.fromkeys(reversed(asked)))[:8]
            st.markdown('<div class="er-rail-group-label">Recent research</div>', unsafe_allow_html=True)
            for q in recent:
                label = q if len(q) <= 40 else q[:37] + "…"
                st.page_link(research_page, label=label)

        # REFERENCE — static docs. Disclaimer is deliberately not a primary
        # item here; it's reachable via Methodology's cross-link and the
        # page footer instead (UX-refinement pass).
        st.markdown('<div class="er-rail-group-label">Reference</div>', unsafe_allow_html=True)
        st.markdown('<div class="er-rail-footlinks">', unsafe_allow_html=True)
        for key, label in FOOTER_NAV:
            page = pages.get(key)
            if page is not None:
                st.page_link(page, label=label)
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown(
            '<div class="er-rail-status"><span class="dot"></span>Demo environment · sample data</div>',
            unsafe_allow_html=True,
        )


# Full text stays on Methodology/Disclaimer (which already carry this
# content in their own page body — see methodology.py) and, redundantly
# but harmlessly, in this longer footer variant reserved for just those
# two pages. Every other page gets the compact one-liner below instead of
# repeating it at the same length (usability follow-up).
_COMPACT_FOOTER_TEXT = "Evidence-first research · Sample data only · Not investment advice"
_FULL_FOOTER_PAGES = {"methodology", "disclaimer"}


def render_footer(nav_key: str | None = None) -> None:
    if nav_key in _FULL_FOOTER_PAGES:
        st.markdown(
            f"""
            <div class="er-footer">
                <div>{APP_NAME} is evidence-first: every claim is labeled Fact, Interpretation,
                Inference, or Uncertainty, and material claims link to their source.</div>
                <div style="margin-top:0.4rem;">Data freshness: demo/mock data only — no live feed connected in this phase.</div>
                <div style="margin-top:0.4rem;">{METHODOLOGY_STATEMENT}</div>
                <div class="er-footer-version">{APP_NAME} v{APP_VERSION} · Foundation phase (demo data)</div>
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


def _render_review_mode_banner() -> None:
    """Throwaway UI-review deployment marker — renders only when
    EDGE_REVIEW_MODE_ENABLED is set (never on eevaresearch.com, the
    production Render service, or Streamlit Community Cloud). Runs once
    per page via with_chrome() below, the single shared entry point
    every page (including Home) already routes through."""
    if get_settings().review_mode_enabled:
        st.markdown(
            '<div style="background:#B45309; color:#fff; padding:0.5rem 1rem; '
            'font-weight:600; text-align:center; border-radius:4px; margin-bottom:0.75rem;">'
            "⚠ LOCAL/PRIVATE REVIEW BUILD — sample data only, not for production use"
            "</div>",
            unsafe_allow_html=True,
        )


def with_chrome(page_fn: Callable[[], None], nav_key: str, show_sidebar: bool = True) -> Callable[[], None]:
    def _wrapped() -> None:
        load_css()
        _render_review_mode_banner()
        if show_sidebar:
            render_sidebar(nav_key)

        from src.ui.components.save_dialog import render_pending_save_dialog

        render_pending_save_dialog()

        with st.container(key="page-content"):
            page_fn()
        render_footer(nav_key)

    _wrapped.__name__ = getattr(page_fn, "__name__", "page")
    return _wrapped
