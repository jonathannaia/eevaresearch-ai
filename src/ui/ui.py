"""Shared UI shell: the persistent left sidebar, global CSS loader, and
footer — replaces chrome.py's top-nav-based design (round 3) with the
sidebar-based IA from design/eevaresearch-brief.md §4. `st.navigation`
still drives routing/`st.Page` objects; only the nav *widget* itself moved
from a custom top header to `st.sidebar` + real `st.page_link`s.
"""
from __future__ import annotations

import base64
import html
from pathlib import Path
from typing import Callable

import streamlit as st

from src.config.settings import APP_NAME, APP_VERSION, Settings, get_settings
from src.data_access import backend_factory
from src.logic.formatting import fmt_time_local, today_local

METHODOLOGY_STATEMENT = (
    "EevaResearch separates source-backed facts, market interpretation, model "
    "inference, and uncertainty. It is for informational research only and "
    "does not provide investment advice."
)

# Primary sidebar nav ("WORKSPACE" group), in order — navigation-cleanup
# pass (design/DECISIONS.md): the core destinations. Home and Company are
# deliberately excluded (Home is first-visit-only with no sidebar at all;
# Company is reached only by clicking a ticker). Coverage, Themes,
# Radar Signals, and Research are intentionally NOT here any more — see
# HIDDEN_FROM_NAV below; their pages/routes/data are untouched, they are
# simply no longer linked from any visible sidebar group.
PRIMARY_NAV: list[tuple[str, str]] = [
    ("dashboard", "Dashboard"),
    ("radar_inbox", "Filings"),
    # Daily News (Slice 1, design/DECISIONS.md), product-naming separation
    # (design/DECISIONS.md) — an independent, separately-scoped autonomous
    # discovery surface, not a Filings view (see the Radar-vs-Daily-News
    # product clarification the same document records). Now user-facing
    # "Signals": a selective, material, cross-source disclosure feed —
    # distinct from "Radar Signals" (the Radar-derived, filing-only
    # concept previously just called "Signals", see HIDDEN_FROM_NAV
    # below). Route key/url_path stay "daily_news"/"daily-news" —
    # unchanged — only this display label moved. Placed next to Filings
    # for IA/UX grouping only ("what's new" feeds together) — this has no
    # bearing on their code/data independence.
    ("daily_news", "Signals"),
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
#
# Themes (beta UI polish pass, design/DECISIONS.md) — moved here from
# PRIMARY_NAV: the public Themes page has no content-readiness gate of
# its own, so a beta launch should not unconditionally link to it from
# the sidebar. "themes" staying in this list only controls its
# underlying st.Page's own (Streamlit-native, unused by this app's
# custom sidebar) visibility="hidden" in app.py — it does NOT mean the
# link never appears. render_sidebar() below adds its own manual,
# conditional st.page_link (same pattern as the Admin Users link further
# down this file) once at least one Theme is actually PUBLISHED — see
# _has_published_themes(). The route, its data, repository, and the
# separate internal authoring workflow (theme_workspace.py) are all
# completely untouched — see src/ui/pages/themes_research.py's own
# render()-level guard for what a non-admin visitor who reaches this
# route directly sees instead of the real (possibly sparse) index.
HIDDEN_FROM_NAV: list[tuple[str, str]] = [
    # Product-naming separation (design/DECISIONS.md): renamed from bare
    # "Signals" to "Radar Signals" once Daily News claimed "Signals" for
    # its own, unrelated feed (see PRIMARY_NAV's "daily_news" entry
    # above). Route key/url_path/model/repository are all unchanged.
    ("signals", "Radar Signals"),
    ("methodology", "Methodology"),
    ("about", "About"),
    ("themes", "Research Theses"),
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


# Themes sidebar visibility (design/DECISIONS.md) — the "Themes" link
# below is the one WORKSPACE entry whose presence is data-driven rather
# than static like every other PRIMARY_NAV/SYSTEM_NAV item: it appears
# only once at least one Theme is actually PUBLISHED, so the beta
# sidebar never links to what would otherwise be an empty index (see
# src/ui/pages/themes_research.py's own empty-state). 60s, not per-
# render: this check runs on every single page load across the whole
# app (sidebar chrome, not just the Themes page itself), so an uncached
# repository call here would add a DB round trip to every navigation
# site-wide.
_THEMES_NAV_CACHE_TTL_SECONDS = 60


@st.cache_data(show_spinner=False, ttl=_THEMES_NAV_CACHE_TTL_SECONDS)
def _published_theme_count() -> int:
    """Real count of PUBLISHED Research Theses through the same public,
    published-only protocol _has_published_themes() reads — the sidebar's
    "Research theses N" badge (redesign v2). Fails closed to 0."""
    settings = get_settings()
    try:
        return len(backend_factory.get_theme_repository(settings).list_published_themes())
    except Exception:  # noqa: BLE001 — fail closed, sidebar chrome never raises
        return 0


@st.cache_data(show_spinner=False, ttl=_THEMES_NAV_CACHE_TTL_SECONDS)
def _has_published_themes() -> bool:
    """True only if at least one Theme is visible through the public,
    published-only protocol (backend_factory.get_theme_repository) —
    the exact same read path src/ui/pages/themes_research.py itself
    uses, never the private curator seam
    (get_theme_curator_repository), so an INTERNAL, READY_TO_PUBLISH,
    or ARCHIVED Theme can never make this true. Fails closed (returns
    False, keeping the sidebar link hidden) on any repository error —
    global sidebar chrome must never raise because of a Theme backend
    hiccup, matching themes_research.py's own fail-closed convention
    for the exact same failure case."""
    settings = get_settings()
    try:
        repository = backend_factory.get_theme_repository(settings)
        return len(repository.list_published_themes()) > 0
    except Exception:  # noqa: BLE001 — fail closed, see docstring above
        return False


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
    home_page = pages.get("home")

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
        brand_cols = st.columns([1, 4, 1], vertical_alignment="center")
        with brand_cols[0]:
            st.markdown(f'<span class="er-rail-logo">{brand_mark_html()}</span>', unsafe_allow_html=True)
        with brand_cols[1]:
            if home_page is not None:
                st.page_link(home_page, label="EevaResearch")
            else:
                st.markdown('<span class="er-rail-word">EevaResearch</span>', unsafe_allow_html=True)
        with brand_cols[2]:
            render_palette_trigger(compact=True)
        st.markdown("</div>", unsafe_allow_html=True)

        # WORKSPACE — the four core visible destinations (navigation-cleanup
        # pass, design/DECISIONS.md). Coverage/Themes/Signals/Research and
        # the old "My watchlists"/per-list-shortcut/"Recent research"
        # sections are gone from here — see PRIMARY_NAV/HIDDEN_FROM_NAV.
        st.markdown('<div class="er-rail-group-label">Workspace</div>', unsafe_allow_html=True)
        for key, label in PRIMARY_NAV:
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
            # styles.css's nav-item rules now key off that directly. See
            # this file's own docstring-level "why" note is intentionally
            # kept local to this one call site — the mechanism is small
            # enough not to need a separate module-level explanation.
            _nav_item(page, key, label, current_key, badges.get(key))

        # Themes — data-driven WORKSPACE entry (design/DECISIONS.md): not
        # in PRIMARY_NAV/HIDDEN_FROM_NAV's static split at all, since its
        # visibility depends on published content, not a fixed nav table.
        # Same styling/active-state as the PRIMARY_NAV loop above; the
        # underlying "themes" page/route is unaffected either way (still
        # registered, still reachable by direct URL, still visibility=
        # "hidden" in app.py's own st.Page — only this manual link is new).
        themes_page = pages.get("themes")
        if themes_page is not None and _has_published_themes():
            _nav_item(themes_page, "themes", "Research Theses", current_key, badges.get("themes"))

        # SYSTEM — lower-priority destinations (navigation-cleanup pass).
        # A Settings entry belongs here once a real Settings route exists.
        st.markdown('<div class="er-rail-group-label">System</div>', unsafe_allow_html=True)
        for key, label in SYSTEM_NAV:
            page = pages.get(key)
            if page is not None:
                _nav_item(page, key, label, current_key)

        # Admin Users v1 (design/DECISIONS.md) — cosmetic only: hiding
        # this link from a non-admin is not authorization (the page's own
        # is_admin() check before any repository access is), but a real
        # admin should still be able to reach it without typing the URL.
        if is_admin():
            admin_users_page = pages.get("admin_users")
            if admin_users_page is not None:
                st.markdown('<div class="er-rail-group-label">Admin</div>', unsafe_allow_html=True)
                _nav_item(admin_users_page, "admin_users", "Users", current_key)

        # Redesign v2: real refresh data only — omitted entirely when no
        # durable scan status is available (see _latest_filings_refresh_label).
        _render_sidebar_refresh()

        # Account control — anchored to the visual bottom of the sidebar
        # (application-shell dark/dim pass, Perplexity-inspired layout).
        # Rendered last, after every nav group above, so it is always the
        # final element in source order; .er-rail-account's own
        # margin-top: auto (assets/styles.css) is what actually pushes it
        # to the bottom of the sidebar's flex column rather than its
        # position in the markup alone. Same st.user.is_logged_in/
        # st.user.get("email")/st.logout calls as the prior top-bar avatar
        # popover used, same "only the email claim is shown, never a
        # token/cookie/session id" rule — only the location and widget
        # keys changed (topbar-avatar-*/topbar-sign-out-* -> sidebar-
        # account-*/sidebar-sign-out-*), not the mechanism or the content.
        _render_sidebar_account(current_key)

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
        load_css()
        if show_sidebar:
            render_sidebar(nav_key)
            _render_topbar(nav_key)

        with st.container(key="page-content"):
            page_fn()
        render_footer(nav_key)

    _wrapped.__name__ = getattr(page_fn, "__name__", "page")
    return _wrapped
