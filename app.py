"""EevaResearch AI — foundation-phase entry point.

Run with: streamlit run app.py

Registers Home (first-visit landing, no sidebar), the WORKSPACE routes
(Dashboard, Radar, Themes, Daily News), the SYSTEM route (Methodology &
Coverage, reusing the Coverage page/route), and routes that stay fully
reachable but are no longer linked from any visible sidebar group —
Coverage/Signals/Methodology/About (direct URL, the command palette,
in-page cross-links) and Disclaimer (Methodology's cross-link and the
page footer) — see design/eevaresearch-brief.md §4 for the original
route table and design/DECISIONS.md for the navigation-cleanup pass that
reorganized it. Watchlists, Research (canned-demo-answer chat), and
Company (a single fictional ticker) were removed entirely in the
reader-facing data-integrity pass (design/DECISIONS.md) — none had any
live real data of its own. src/ui/ui.render_sidebar is the persistent
left-rail nav widget.

Admin Users v1 (design/DECISIONS.md) adds one more hidden-but-reachable
route, admin_users — see that page's own module docstring and
src/ui/ui.py's is_admin() for its authorization design.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from src.config.settings import get_settings
from src.data_access import backend_factory
from src.data_access.container import get_repositories
from src.logic.unread import seed_initial_last_seen
from src.ui.beta_gate import BetaGateReason, evaluate_beta_gate
from src.ui.pages import (
    about,
    admin_users,
    company_discovery_admin,
    coverage,
    daily_news,
    daily_news_admin,
    dashboard,
    disclaimer,
    home,
    methodology,
    radar_inbox,
    research_cases,
    signals,
    theme_workspace,
    themes_research,
)
from src.ui.ui import HIDDEN_FROM_NAV, LAST_SEEN_KEY, PRIMARY_NAV, READ_IDS_KEY, SYSTEM_NAV, with_chrome

_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "eeva-logo.png"

st.set_page_config(
    page_title="EevaResearch AI",
    page_icon=str(_LOGO_PATH) if _LOGO_PATH.exists() else None,
    layout="wide",
    initial_sidebar_state="expanded",
)

_RENDER_FNS = {
    "dashboard": dashboard.render,
    "radar_inbox": radar_inbox.render,
    "daily_news": daily_news.render,
    "coverage": coverage.render,
    "themes": themes_research.render,
    "signals": signals.render,
    "methodology": methodology.render,
    "about": about.render,
}

_URL_PATHS = {
    "dashboard": "dashboard",
    "radar_inbox": "radar-inbox",
    "daily_news": "daily-news",
    "coverage": "coverage",
    "themes": "themes",
    "signals": "signals",
    "methodology": "methodology",
    "about": "about",
}

# Navigation-cleanup pass (design/DECISIONS.md): Coverage/Signals/
# Methodology/About stay fully registered routes (direct URL, command
# palette, in-page cross-links) — only visibility="hidden" changes,
# since none of them are linked from any visible sidebar group any more.
_HIDDEN_KEYS = {key for key, _ in HIDDEN_FROM_NAV}

# Navigation-bug repair (design/DECISIONS.md): `st.Page` objects — and the
# `with_chrome(...)` closures wrapped inside them — used to be rebuilt from
# scratch on every single rerun (this whole module re-executes on every
# navigation). Live reproduction confirmed that broke click-driven sidebar
# navigation: after a couple of reruns, `st.page_link` clicks silently
# stopped changing the page (a hard URL reload always still worked, proving
# server-side url_path routing itself was fine — only the client-side
# page-identity tracking that `st.page_link` clicks depend on had gone
# stale). `st.cache_resource` makes each distinct page set a stable,
# singleton set of Python objects reused across reruns instead of fresh
# ones every time — the officially-recommended fix for exactly this class
# of `st.navigation` instability.
@st.cache_resource(show_spinner=False)
def _build_pages(dashboard_is_default: bool) -> dict[str, st.Page]:
    pages = {
        "home": st.Page(with_chrome(home.render, "home", show_sidebar=False), title="Home", default=not dashboard_is_default),
    }
    for key, _label in PRIMARY_NAV + SYSTEM_NAV + HIDDEN_FROM_NAV:
        pages[key] = st.Page(
            with_chrome(_RENDER_FNS[key], key),
            title=_label,
            url_path=_URL_PATHS.get(key),
            default=(key == "dashboard" and dashboard_is_default),
            visibility="hidden" if key in _HIDDEN_KEYS else "visible",
        )
    # Disclaimer is no longer a primary sidebar item, but stays a real
    # reachable route via Methodology's cross-link and the page footer.
    pages["disclaimer"] = st.Page(
        with_chrome(disclaimer.render, "disclaimer"), title="Disclaimer", url_path="disclaimer", visibility="hidden",
    )
    # Daily News admin/status (Slice 1) — same hidden-but-reachable pattern
    # as disclaimer above: not linked in the sidebar, reachable only by
    # direct URL, for controlled pilot verification. Also gated a second
    # way by settings.daily_news_admin_enabled (checked inside the page
    # itself, default disabled — reader-facing data-integrity pass).
    pages["daily_news_admin"] = st.Page(
        with_chrome(daily_news_admin.render, "daily_news_admin"),
        title="Daily News — Admin", url_path="daily-news-admin", visibility="hidden",
    )
    # Research Cases (Phase 4, Step 3C) — same hidden-but-reachable
    # pattern as disclaimer/daily_news_admin above: not linked in the
    # sidebar or any nav group, reachable only by direct URL, for
    # invited-tester review of manually curated research cases. Also
    # gated a second way by settings.research_cases_enabled (default
    # disabled — reader-facing data-integrity pass).
    pages["research_cases"] = st.Page(
        with_chrome(research_cases.render, "research_cases"),
        title="Research Cases", url_path="research-cases", visibility="hidden",
    )
    # Constraint Research Workspace (Citrini-style Theme research
    # workspace vertical slice, design/DECISIONS.md) — same hidden-but-
    # reachable pattern as disclaimer/daily_news_admin/research_cases
    # above: never linked in the sidebar, any nav group, or the command
    # palette. Internal-only; also gated a second way by
    # settings.theme_workspace_enabled (checked inside the page itself,
    # default disabled).
    pages["theme_workspace"] = st.Page(
        with_chrome(theme_workspace.render, "theme_workspace"),
        title="Constraint Research Workspace", url_path="theme-workspace", visibility="hidden",
    )
    # Company Discovery — Phase 2 admin/status (design/DECISIONS.md) —
    # same hidden-but-reachable pattern as disclaimer/daily_news_admin/
    # research_cases/theme_workspace above: never linked in the sidebar,
    # any nav group, or the command palette. Internal-only, strictly
    # read-only (no promotion action exists in Phase 2); also gated a
    # second way by settings.company_discovery_admin_enabled (checked
    # inside the page itself, default disabled).
    pages["company_discovery_admin"] = st.Page(
        with_chrome(company_discovery_admin.render, "company_discovery_admin"),
        title="Company Discovery — Admin", url_path="company-discovery-admin", visibility="hidden",
    )
    # Admin Users v1 (design/DECISIONS.md) — same hidden-but-reachable
    # pattern as the admin pages above: `visibility="hidden"` keeps it out
    # of Streamlit's own nav; src/ui/ui.py:render_sidebar() adds the one
    # manual, conditional st.page_link only when is_admin() is true — the
    # page's own authorization check (before any repository access) is
    # the real boundary, not the link's visibility.
    pages["admin_users"] = st.Page(
        with_chrome(admin_users.render, "admin_users"),
        title="Admin — Users", url_path="admin-users", visibility="hidden",
    )
    return pages


# Mandatory Google sign-in gate (design/DECISIONS.md) — runs before
# _build_pages(), any session-state seeding, any repository access, or
# st.navigation, so an unauthenticated visitor never triggers a protected
# data read or sees any page, nav, or dashboard content. Every visitor
# must authenticate; there is no flag that reopens this.
_beta_settings = get_settings()
_beta_is_logged_in = getattr(st.user, "is_logged_in", False)

if not _beta_is_logged_in:
    st.title("Sign in to EevaResearch AI")
    st.write("Sign in with your Google account to continue.")
    st.button("Continue with Google", on_click=st.login, args=("google",))
    st.stop()

_beta_email = st.user.get("email")

# EDGE_PRIVATE_BETA_ALLOWED_EMAILS is an optional, secondary, invite-only
# layer for a later phase (Admin Users page not yet built) — any
# authenticated Google account is allowed through today unless the
# allowlist is non-empty and excludes it. evaluate_beta_gate() itself is
# unchanged (src/ui/beta_gate.py) and keeps its own standalone contract —
# including EMPTY_ALLOWLIST meaning "deny" — because that contract is
# exercised directly by tests/test_beta_gate.py. Its AUTH_DISABLED/
# EMPTY_ALLOWLIST branches both described the old "beta invite is
# optional" design, which no longer applies now that sign-in itself is
# mandatory (checked above); forcing private_beta_auth_enabled=True for
# just this call routes the decision into evaluate_beta_gate's real
# allowlist-comparison branches (ALLOWED_EMAIL/INVITE_REQUIRED) instead of
# the now-stale AUTH_DISABLED shortcut, and this call site — not the
# shared function — is what treats EMPTY_ALLOWLIST as "allow," per the
# product decision that an empty allowlist must never lock out an
# authenticated user.
_beta_gate_decision = evaluate_beta_gate(
    dataclasses.replace(_beta_settings, private_beta_auth_enabled=True), email=_beta_email
)
_beta_allowed = _beta_gate_decision.allowed or _beta_gate_decision.reason is BetaGateReason.EMPTY_ALLOWLIST

if not _beta_allowed:
    st.title("Private beta")
    st.error("This Google account is not approved for the private beta.")
    st.button("Sign out", on_click=st.logout)
    st.stop()

# Admin Users v1 (design/DECISIONS.md) — records this authenticated,
# allowed visitor's sign-in at most once per Streamlit browser session
# (the same "_has_visited"-style session_state guard app.py already uses
# below), never on every rerun. Runs only after both the mandatory
# sign-in gate and the optional allowlist gate above have already
# passed, and before any protected page/nav content builds. Calls
# backend_factory.get_user_account_repository(...) directly — not via
# get_repositories()/AppContext — so that every other page's ordinary
# get_repositories() call (dashboard, Radar, Daily News, Themes, ...)
# never constructs, connects, or migration-checks a UserAccount
# repository it doesn't need; only this block and
# src/ui/pages/admin_users.py's own is_admin()-gated render() ever do. A
# write failure — including the repository construction itself, e.g. a
# misconfigured sqlite/postgres backend — must never block a legitimate
# user or retry every rerun — the guard flag is set regardless of
# outcome — but must not be completely silent either: exactly one
# sanitized, non-sensitive line identifying only the event type, never
# the email, exception text, backend, or any connection detail.
if not st.session_state.get("_user_account_recorded", False):
    try:
        backend_factory.get_user_account_repository(_beta_settings).record_sign_in(
            email=_beta_email,
            display_name=st.user.get("name"),
            now=datetime.now(timezone.utc).isoformat(),
        )
    except Exception:  # noqa: BLE001 — bookkeeping only, must never block a legitimate user
        print("[app] User-account session recording failed.")
    st.session_state["_user_account_recorded"] = True

# Home renders on first visit only; Dashboard is the default thereafter
# (brief §4) — a page keeps the root path "/" via default=True regardless
# of its own url_path, so Dashboard stays reachable at both "/" and
# "/dashboard" once it takes over as default. This per-session flip is
# unchanged by the cache-stability fix above: `_build_pages` has exactly
# two possible cache entries (dashboard_is_default True/False), each built
# once and then reused — so within one session, every rerun after the
# first consistently gets the SAME "dashboard is default" page set, and a
# brand-new session's first rerun consistently gets the SAME "home is
# default" page set, instead of a fresh, unstable set every single time.
_first_visit = "_has_visited" not in st.session_state
st.session_state["_has_visited"] = True

pages = _build_pages(dashboard_is_default=not _first_visit)
st.session_state["_pages"] = pages

if LAST_SEEN_KEY not in st.session_state:
    # One-time per-session seed so the unread/last-seen pattern (brief §10)
    # has something to demonstrate on the very first view, not just after a
    # real Signals visit. Only Signals itself advances this afterward.
    st.session_state[LAST_SEEN_KEY] = seed_initial_last_seen(get_repositories().signal_repository.get_all_signals())
st.session_state.setdefault(READ_IDS_KEY, set())

selected = st.navigation(list(pages.values()), position="hidden")
selected.run()
