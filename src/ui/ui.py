"""Shared UI shell: the persistent left sidebar, global CSS loader, and
footer — replaces chrome.py's top-nav-based design (round 3) with the
sidebar-based IA from design/eevaresearch-brief.md §4. `st.navigation`
still drives routing/`st.Page` objects; only the nav *widget* itself moved
from a custom top header to `st.sidebar` + real `st.page_link`s.
"""
from __future__ import annotations

import base64
import html
import re
from pathlib import Path
from typing import Callable

import streamlit as st

from src.config.settings import APP_NAME, APP_VERSION, Settings, get_settings
from src.data_access import backend_factory
from src.logic.formatting import fmt_time_local, today_local
from src.ui import render_timing, theme
from src.ui.theme_tokens import ThemePreference, render_token_css

METHODOLOGY_STATEMENT = (
    "EevaResearch separates source-backed facts, market interpretation, model "
    "inference, and uncertainty. It is for informational research only and "
    "does not provide investment advice."
)

# Sidebar nav, in order — UI redesign item 1: one flat list of six
# destinations (Today · Feed · Companies · Themes · Theses · Coverage),
# exactly as design/ui-redesign/main-dashboard.html's rail shows it.
# PRIMARY_NAV/SYSTEM_NAV stay two tables because every breadcrumb group,
# test and admin-visibility check already reads them that way; the only
# visible change is that render_sidebar() no longer prints a group label
# above either, so the reader sees a single six-item list.
#
# RENAMES ARE DISPLAY-ONLY. Every route key and url_path below is the one
# it has always been, except the two new pages and the "themes"/"theses"
# url_path swap called out in app.py's _URL_PATHS. "dashboard" is now
# labelled "Today"; "daily_news" (internally still Daily News, see
# src/ui/pages/daily_news.py) is now "Feed"; "coverage" is now plain
# "Coverage" rather than "Methodology & Coverage" — Methodology keeps its
# own route, reachable from Coverage's own cross-link and the footer.
PRIMARY_NAV: list[tuple[str, str]] = [
    ("dashboard", "Today"),
    # Redesign item 2 merges Radar's filings into this one Feed list. It
    # has NOT happened yet: today "daily_news" is still the news-lane
    # editorial feed and "radar_inbox" is still the filing list, and the
    # six-item nav the redesign specifies has no room for both. Filings
    # therefore moved to HIDDEN_FROM_NAV below — still a registered
    # route, still reachable by direct URL and by the command palette's
    # own "Open Filings" action — rather than being dropped. Item 2 is
    # what makes that honest; until it lands, see the palette.
    ("daily_news", "Feed"),
    # The tracked-company index (src/ui/pages/companies.py). Per-company
    # detail pages are item 3; this is the list only.
    ("companies", "Companies"),
    # The content-theme TAG TAXONOMY (src/ui/pages/theme_index.py) — the
    # five slugs on Issuer.themes/EditorialStory.matched_themes. NOT the
    # curated research narratives below, which this codebase also calls
    # "theme" internally. See src/logic/theme_taxonomy.py's docstring for
    # the full naming split; getting these two the wrong way round builds
    # the wrong page.
    ("theme_index", "Themes"),
    # The curated, cross-company research narratives — route key "themes",
    # page src/ui/pages/themes_research.py, user-facing "Research Theses"
    # since that page was written, now shortened to "Theses" to fit the
    # rail. Internal model/table/repository identifiers are untouched.
    #
    # This entry replaces render_sidebar()'s former conditional link,
    # which appeared only once at least one Thesis was PUBLISHED
    # (_has_published_themes(), a beta-launch gate). A fixed six-item nav
    # cannot have an item that comes and goes, so the gate is gone from
    # the nav: the item is always shown, and themes_research.py's own
    # index empty-state is what a visitor sees when nothing is published
    # yet. _published_theme_count() still drives the count badge, which
    # correctly shows nothing at zero.
    ("themes", "Theses"),
]

# Lower-priority "SYSTEM" group (navigation-cleanup pass) — still its own
# table so _BREADCRUMB_GROUPS and the admin-visibility tests keep reading
# it, but rendered without a group label of its own, as the last of the
# six flat nav items. A "Settings" entry belongs here once a real
# Settings route exists; none does yet, so it stays deliberately omitted
# rather than inventing a page for it.
SYSTEM_NAV: list[tuple[str, str]] = [
    ("coverage", "Coverage"),
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
    # UI redesign item 1: moved here from PRIMARY_NAV because the
    # six-item rail has no slot for it and item 2 is what merges its
    # filings into the Feed. Route key, url_path, page, model and
    # repository are all completely unchanged, and the command palette
    # carries an explicit "Open Filings" action so the page is one ⌘K
    # away in the meantime. Restore it to PRIMARY_NAV if item 2 is
    # abandoned — do not leave the app with no path to the filing list.
    ("radar_inbox", "Filings"),
    # Product-naming separation (design/DECISIONS.md): renamed from bare
    # "Signals" to "Radar Signals" once Daily News claimed "Signals" for
    # its own, unrelated feed. Route key/url_path/model/repository are
    # all unchanged. ("Signals" is now the Feed, see PRIMARY_NAV.)
    ("signals", "Radar Signals"),
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


_IMPORT_RULE = re.compile(r"@import url\([^)]*\);\s*")


def load_css(preference: ThemePreference = "system") -> None:
    """One <style> block: the resolved theme's color tokens
    (src/ui/theme_tokens.py) followed by assets/styles.css, which only
    ever consumes them. The stylesheet's @import rules (web fonts) are
    hoisted to the very top of the block — a browser ignores an @import
    that follows any other rule."""
    css = _css_text()
    imports = "".join(match.group(0).strip() + "\n" for match in _IMPORT_RULE.finditer(css))
    rest = _IMPORT_RULE.sub("", css)
    st.markdown(f"<style>{imports}{render_token_css(preference)}\n{rest}</style>", unsafe_allow_html=True)


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


def is_admin(settings: Settings | None = None) -> bool:
    """Admin Users v1 (design/DECISIONS.md) — the single authorization
    check for the hidden Admin -> Users page, driven only by
    EEVA_ADMIN_EMAILS (Settings.admin_emails). Fails closed by
    construction: an absent/blank env var parses to an empty frozenset
    (src.config.settings._parse_beta_allowed_emails), which no email can
    ever be a member of, so an unconfigured deployment always denies
    everyone, including a real signed-in user. This is a cosmetic
    convenience for src/ui/pages/admin_users.py's own sidebar link and
    render_sidebar() below — the page's own authorization check (run
    before it ever constructs a repository) is the real access
    boundary, not this function's use here."""
    settings = settings or get_settings()
    if not getattr(st.user, "is_logged_in", False):
        return False
    email = (st.user.get("email") or "").strip().lower()
    return bool(email) and email in settings.admin_emails


# Research Theses count badge. The former _has_published_themes() gate
# that used to live here is GONE (UI redesign item 1): "themes" is a
# static PRIMARY_NAV entry now, always linked, so nothing needs to ask
# whether anything is published before drawing the nav. What remains is
# the count beside it, which is zero-safe by construction —
# _nav_badge_counts() drops any count that is not > 0, so an empty
# Theses index shows the item with no badge rather than "0".
#
# 60s, not per-render: this count runs on every single page load across
# the whole app (sidebar chrome, not just the Theses page itself), so an
# uncached repository call here would add a DB round trip to every
# navigation site-wide.
_THEMES_NAV_CACHE_TTL_SECONDS = 60


@st.cache_data(show_spinner=False, ttl=_THEMES_NAV_CACHE_TTL_SECONDS)
def _published_theme_count() -> int:
    """Real count of PUBLISHED Research Theses through the public,
    published-only protocol (backend_factory.get_theme_repository) — the
    exact same read path src/ui/pages/themes_research.py itself uses,
    never the private curator seam (get_theme_curator_repository), so an
    INTERNAL, READY_TO_PUBLISH or ARCHIVED Thesis can never be counted
    here. Fails closed to 0 on any repository error: global sidebar
    chrome must never raise because of a Theme backend hiccup, matching
    themes_research.py's own fail-closed convention for the same case."""
    settings = get_settings()
    try:
        return len(backend_factory.get_theme_repository(settings).list_published_themes())
    except Exception:  # noqa: BLE001 — fail closed, see docstring above
        return 0


def _nav_badge_counts() -> dict[str, int]:
    """Count badges shown beside a nav item only when backed by a real,
    currently-eligible count (redesign v2): Signals = the High Signals
    the Signals page itself lists ("All companies" view, cached 60s in
    src/ui/pages/daily_news.py), Research theses = published theses."""
    from src.ui.pages.daily_news import high_signal_count

    counts = {"daily_news": high_signal_count(get_settings()), "themes": _published_theme_count()}
    return {key: n for key, n in counts.items() if n > 0}


def _nav_item(page, key: str, label: str, current_key: str, badge: int | None = None) -> None:
    """One sidebar destination: a real st.page_link inside the keyed
    container the active-state/icon CSS reads (st-key-navitem-{key}[-active]),
    plus an optional real count badge rendered as a sibling the CSS
    overlays on the row's right edge."""
    container_key = f"navitem-{key}-active" if key == current_key else f"navitem-{key}"
    with st.container(key=container_key):
        st.page_link(page, label=label)
        if badge:
            st.markdown(f'<span class="er-rail-count">{badge}</span>', unsafe_allow_html=True)


def _latest_filings_refresh_label() -> str | None:
    """"Filings refreshed HH:MM EDT" from the durable per-provider scan
    status the standalone Radar worker writes (ProviderScanStatus.
    last_successful_at, via radar_inbox's own cached snapshot) — never a
    page-render time. None whenever that status is not available (the
    JSON backend has no worker status; an unreachable store; no completed
    scan yet), so the sidebar block is omitted rather than guessed."""
    settings = get_settings()
    if (settings.db_backend or "json").strip().lower() not in ("sqlite", "postgres"):
        return None
    try:
        from src.ui.pages.radar_inbox import _dashboard_config_fingerprint, _load_dashboard_snapshot

        snapshot = _load_dashboard_snapshot(settings.cache_dir, _dashboard_config_fingerprint(settings), settings)
        if snapshot.worker_status_state != "ok" or not snapshot.worker_status_statuses:
            return None
        stamps = [st_.last_successful_at for st_ in snapshot.worker_status_statuses.values() if st_ and st_.last_successful_at]
        if not stamps:
            return None
        clock, zone = fmt_time_local(max(stamps))
        return f"Filings refreshed {clock} {zone}" if clock else None
    except Exception:  # noqa: BLE001 — fail closed, sidebar chrome never raises
        return None


def _render_sidebar_refresh() -> None:
    label = _latest_filings_refresh_label()
    if not label:
        return
    st.markdown(
        '<div class="er-rail-status-block"><div class="er-rail-status-title"><span class="dot live"></span>Live data</div>'
        f'<div class="er-rail-status">{label}</div></div>',
        unsafe_allow_html=True,
    )


# Breadcrumb group + label per route key (redesign v2 top bar). Nav tables
# are the source of truth; the hidden admin/system routes below are the
# same fixed labels app.py registers them with.
_BREADCRUMB_GROUPS: dict[str, str] = {
    **{key: "Workspace" for key, _ in PRIMARY_NAV},
    **{key: "System" for key, _ in SYSTEM_NAV},
    **{key: "Workspace" for key, _ in HIDDEN_FROM_NAV},
    "home": "Workspace", "feedback": "Workspace", "verified_updates": "Workspace", "research_cases": "Workspace",
    "disclaimer": "System",
    "admin_users": "Admin", "daily_news_admin": "Admin", "company_discovery_admin": "Admin", "theme_workspace": "Admin",
}
_BREADCRUMB_LABELS: dict[str, str] = {
    **dict(PRIMARY_NAV), **dict(SYSTEM_NAV), **dict(HIDDEN_FROM_NAV),
    "home": "Home", "feedback": "Feedback", "verified_updates": "Verified Updates", "research_cases": "Research Cases",
    "disclaimer": "Disclaimer", "admin_users": "Users", "daily_news_admin": "Signals Admin",
    "company_discovery_admin": "Company Discovery — Admin", "theme_workspace": "Theme Workspace",
}


def _render_topbar(nav_key: str) -> None:
    """Breadcrumb from the actual current route + today's date in the
    app's one display timezone (today_local, Eastern) — no search here:
    the command-palette trigger stays in the sidebar brand row (its one
    keyed widget instance)."""
    page = get_page(nav_key)
    label = _BREADCRUMB_LABELS.get(nav_key) or (getattr(page, "title", None) or nav_key.replace("_", " ").title())
    group = _BREADCRUMB_GROUPS.get(nav_key, "Workspace")
    today = today_local()
    date_label = f"{today:%a} · {today:%b} {today.day}, {today.year}"
    st.markdown(
        '<div class="er-topbar">'
        f'<div class="er-breadcrumb"><span>{group}</span><span class="er-crumb-sep">/</span>'
        f'<span class="er-crumb-current">{label}</span></div>'
        f'<div class="er-topbar-date">{date_label}</div>'
        "</div>",
        unsafe_allow_html=True,
    )


def render_sidebar(current_key: str) -> None:
    _correct_sidebar_state_for_width()
    badges = _nav_badge_counts()

    pages = st.session_state.get("_pages", {})
    # The wordmark goes to Dashboard, not Home. Home is a redirect-only
    # compatibility route now (src/ui/pages/home.py); pointing the brand
    # link at it would send every click through an extra rerun for no
    # reason, and is the shape a redirect loop would take.
    brand_page = pages.get("dashboard")

    with st.sidebar:
        # Application-shell dark/dim pass (Perplexity-inspired sidebar
        # layout, narrowly scoped) — brand mark/wordmark at top-left, a
        # compact search icon directly beside it (render_palette_trigger()
        # itself is unchanged, same ⌘K dialog/keyboard-shortcut mechanism
        # as before; only `compact=True` changes its own rendering, and it
        # is still called exactly once per page render, never duplicated).
        # Previously this search trigger and the account control both lived
        # in a separate sticky top bar above the main content — both moved
        # back into the sidebar here (top and bottom respectively, see
        # _render_sidebar_account() below), which is now the one persistent
        # place either control renders; the top bar itself is retired.
        from src.ui.components.command_palette import render_palette_trigger

        st.markdown('<div class="er-rail-brand">', unsafe_allow_html=True)
        brand_cols = st.columns([1, 5], vertical_alignment="center")
        with brand_cols[0]:
            st.markdown(f'<span class="er-rail-logo">{brand_mark_html()}</span>', unsafe_allow_html=True)
        with brand_cols[1]:
            if brand_page is not None:
                st.page_link(brand_page, label="EevaResearch")
            else:
                st.markdown('<span class="er-rail-word">EevaResearch</span>', unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

        # UI redesign item 1 — the search box. The trigger moved out of
        # the brand row (where it was a 34px icon button, compact=True)
        # to its own full-width row directly beneath it, reading "Ticker
        # or company    ⌘K", which is what design/ui-redesign/
        # main-dashboard.html's rail shows and what the search is now
        # actually for: the palette indexes all 129 tracked companies by
        # name, ticker and native name (see command_palette._index).
        #
        # Still the SAME trigger — one call per page render, the same
        # st.dialog, the same working global ⌘K listener (that listener
        # keys off the button's own st-key-cmdk-trigger class, which is
        # unchanged by this move). Only `compact` flipped, which only
        # ever changed this control's own rendering. assets/styles.css
        # gives the full-width rendering an input-like box treatment
        # inside the rail, so it reads as a search field rather than a
        # button.
        with st.container(key="rail-search"):
            render_palette_trigger()

        # The six flat nav destinations (UI redesign item 1): no
        # "Workspace"/"System" group label above either table any more —
        # design/ui-redesign/main-dashboard.html's rail is one
        # uninterrupted list, and two labels over a five-item and a
        # one-item group read as structure the reader does not need. The
        # tables themselves still exist and still mean what they did (see
        # PRIMARY_NAV/SYSTEM_NAV above and _BREADCRUMB_GROUPS below).
        #
        # The former conditional "Research Theses" link that sat between
        # these two loops is gone: "themes" is a real PRIMARY_NAV entry
        # now, always shown, with no _has_published_themes() gate. See
        # PRIMARY_NAV's own comment for why a fixed six-item rail cannot
        # have an item that appears and disappears.
        for key, label in PRIMARY_NAV + SYSTEM_NAV:
            page = pages.get(key)
            if page is None:
                continue
            # Midnight Teal pass — active-state fix: two separate
            # st.markdown() calls used as pseudo "open tag"/"close tag"
            # around st.page_link() never actually nested in the real
            # browser DOM (each st.markdown() renders as its own isolated
            # element; raw HTML can't span across a Streamlit element
            # boundary) — confirmed live: the "opening" <div class=
            # "er-rail-navactive"> rendered self-closed and empty, as a
            # sibling of st.page_link's own <a>, never an ancestor, so the
            # CSS descendant selector that used to key off it never
            # matched anything in production (AppTest's own element tree
            # doesn't model real DOM nesting the same way, which is why
            # this stayed invisible to every existing test). Fixed by
            # putting the active marker on the one wrapper Streamlit
            # already renders correctly nested — this container's own
            # key — instead: a real "st-key-navitem-{key}-active" class
            # exists only when this item is active, and assets/
            # styles.css's nav-item rules now key off that directly.
            _nav_item(page, key, label, current_key, badges.get(key))

        # Admin Users v1 (design/DECISIONS.md) — cosmetic only: hiding
        # this link from a non-admin is not authorization (the page's own
        # is_admin() check before any repository access is), but a real
        # admin should still be able to reach it without typing the URL.
        if is_admin():
            admin_users_page = pages.get("admin_users")
            if admin_users_page is not None:
                st.markdown('<div class="er-rail-group-label">Admin</div>', unsafe_allow_html=True)
                _nav_item(admin_users_page, "admin_users", "Users", current_key)

        # Redesign v2: the bottom group (real refresh data, if any, above the
        # account control) — one keyed container so assets/styles.css can
        # anchor the pair to the sidebar's visual bottom via margin-top:auto
        # on a real flex child (stSidebarUserContent is made a flex column).
        with st.container(key="rail-bottom"):
            _render_sidebar_refresh()
            _render_sidebar_account(current_key)

        # Account control — rendered inside the rail-bottom container above
        # (redesign v2), still last in source order after every nav group.

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


def _esc(value: object) -> str:
    return "" if value is None else html.escape(str(value))


def _render_sidebar_account(nav_key: str) -> None:
    """Account control anchored to the visual bottom of the sidebar
    (application-shell dark/dim pass, Perplexity-inspired layout) — an
    avatar popover (initial letter) showing the signed-in email + sign
    out, or "Not signed in". Same st.user/st.logout calls the prior top
    bar's avatar popover used before it was retired; only the location,
    widget keys (topbar-avatar-*/topbar-sign-out-* -> sidebar-account-*/
    sidebar-sign-out-*), and CSS class (.er-topbar-avatar-email ->
    .er-rail-account-email) changed. Must be called from inside
    render_sidebar()'s own `with st.sidebar:` block, as the last thing
    rendered, so assets/styles.css's .er-rail-account `margin-top: auto`
    rule can push it to the bottom of the sidebar's flex column."""
    logged_in = getattr(st.user, "is_logged_in", False)
    email = st.user.get("email") if logged_in else None
    # Redesign v2: the real display name claim beside the avatar (the same
    # st.user.get("name") app.py/feedback.py already read), falling back to
    # the email; the secondary line is "Admin" only when is_admin() is
    # actually true — never an invented role.
    display_name = (st.user.get("name") if logged_in else None) or email
    initial = (display_name or "?")[0].upper()
    secondary = "Admin" if (email and is_admin()) else (email if display_name != email else None)

    st.markdown('<div class="er-rail-account">', unsafe_allow_html=True)
    with st.container(key=f"sidebar-account-{nav_key}"):
        avatar_col, identity_col = st.columns([1, 4], vertical_alignment="center")
        with avatar_col:
            with st.popover(initial, use_container_width=False, help="Account"):
                if email:
                    st.markdown(f'<div class="er-rail-account-email">{email}</div>', unsafe_allow_html=True)
                    st.button(
                        "Sign out", on_click=st.logout, key=f"sidebar-sign-out-{nav_key}", use_container_width=True,
                    )
                    st.caption("Ends your EevaResearch session. Google may remain signed in in this browser.")
                else:
                    st.caption("Not signed in")
                theme.render_theme_control()
        with identity_col:
            if display_name:
                secondary_html = f'<div class="er-rail-identity-sub">{_esc(secondary)}</div>' if secondary else ""
                st.markdown(
                    f'<div class="er-rail-identity"><div class="er-rail-identity-name">{_esc(display_name)}</div>{secondary_html}</div>',
                    unsafe_allow_html=True,
                )
    st.markdown("</div>", unsafe_allow_html=True)


def with_chrome(page_fn: Callable[[], None], nav_key: str, show_sidebar: bool = True) -> Callable[[], None]:
    def _wrapped() -> None:
        # Phase 2A instrumentation (src/ui/render_timing.py). This is the
        # one wrapper every routed page already passes through, so timing
        # it here covers the whole navigation without touching routing,
        # state, caching, or any page's own behavior. The helpers below
        # only read a clock and, once per render, write a log record.
        with render_timing.page_render(nav_key):
            preference = theme.current_preference()
            load_css(preference)
            theme.render_bridge(preference)
            if show_sidebar:
                render_sidebar(nav_key)
                _render_topbar(nav_key)
            # Everything above is chrome: theme, CSS, sidebar, topbar.
            # Everything below is the page's own body plus the footer.
            render_timing.mark_setup_complete()

            with st.container(key="page-content"):
                page_fn()
            render_footer(nav_key)

    _wrapped.__name__ = getattr(page_fn, "__name__", "page")
    return _wrapped
