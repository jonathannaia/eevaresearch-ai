"""EevaResearch AI — foundation-phase entry point.

Run with: streamlit run app.py

Registers Home (first-visit landing, no sidebar), the WORKSPACE routes
(Dashboard, Radar, Daily News, Watchlists), the SYSTEM route (Methodology &
Coverage, reusing the Coverage page/route), and routes that stay fully
reachable but are no longer linked from any visible sidebar group —
Coverage/Themes/Signals/Research/Methodology/About (direct URL, the command
palette, in-page cross-links), plus Company (clicking a ticker) and
Disclaimer (Methodology's cross-link and the page footer) — see
design/eevaresearch-brief.md §4 for the original route table and
design/DECISIONS.md for the navigation-cleanup pass that reorganized it.
src/ui/ui.render_sidebar is the persistent left-rail nav widget.
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from src.config.settings import get_settings
from src.data_access.container import get_repositories
from src.logic.unread import seed_initial_last_seen
from src.ui.beta_gate import evaluate_beta_gate
from src.ui.pages import (
    about,
    company,
    coverage,
    daily_news,
    daily_news_admin,
    dashboard,
    disclaimer,
    home,
    methodology,
    radar_inbox,
    research,
    research_cases,
    signals,
    theme_workspace,
    themes,
    themes_research,
    watchlists,
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
    "watchlists": watchlists.render,
    "coverage": coverage.render,
    # Evidence-First Themes MVP (design/DECISIONS.md): "themes" now
    # points at the new public research-narrative page, not the legacy
    # demo ticker/theme/subtheme browser — that page moved to
    # "theme_browser" below, same module, new hidden route/url_path.
    "themes": themes_research.render,
    "theme_browser": themes.render,
    "signals": signals.render,
    "research": research.render,
    "methodology": methodology.render,
    "about": about.render,
}

_URL_PATHS = {
    "dashboard": "dashboard",
    "radar_inbox": "radar-inbox",
    "daily_news": "daily-news",
    "watchlists": "watchlists",
    "coverage": "coverage",
    "themes": "themes",
    "theme_browser": "theme-browser",
    "signals": "signals",
    "research": "research",
    "methodology": "methodology",
    "about": "about",
}

# Navigation-cleanup pass (design/DECISIONS.md): Coverage/Themes/Signals/
# Research/Methodology/About stay fully registered routes (direct URL,
# command palette, in-page cross-links) — only visibility="hidden" changes,
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
    pages["company"] = st.Page(
        with_chrome(company.render, "company"), title="Company", url_path="company", visibility="hidden",
    )
    # Disclaimer is no longer a primary sidebar item, but stays a real
    # reachable route via Methodology's cross-link and the page footer.
    pages["disclaimer"] = st.Page(
        with_chrome(disclaimer.render, "disclaimer"), title="Disclaimer", url_path="disclaimer", visibility="hidden",
    )
    # Daily News admin/status (Slice 1) — same hidden-but-reachable pattern
    # as company/disclaimer above: not linked in the sidebar, reachable only
    # by direct URL, for controlled pilot verification.
    pages["daily_news_admin"] = st.Page(
        with_chrome(daily_news_admin.render, "daily_news_admin"),
        title="Daily News — Admin", url_path="daily-news-admin", visibility="hidden",
    )
    # Research Cases (Phase 4, Step 3C) — same hidden-but-reachable
    # pattern as company/disclaimer/daily_news_admin above: not linked in
    # the sidebar or any nav group, reachable only by direct URL, for
    # invited-tester review of manually curated research cases.
    pages["research_cases"] = st.Page(
        with_chrome(research_cases.render, "research_cases"),
        title="Research Cases", url_path="research-cases", visibility="hidden",
    )
    # Constraint Research Workspace (Citrini-style Theme research
    # workspace vertical slice, design/DECISIONS.md) — same hidden-but-
    # reachable pattern as company/disclaimer/daily_news_admin/
    # research_cases above: never linked in the sidebar, any nav group,
    # or the command palette. Internal-only; also gated a second way by
    # settings.theme_workspace_enabled (checked inside the page itself,
    # default disabled).
    pages["theme_workspace"] = st.Page(
        with_chrome(theme_workspace.render, "theme_workspace"),
        title="Constraint Research Workspace", url_path="theme-workspace", visibility="hidden",
    )
    return pages


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

# Private-beta access foundation, Phase 1 (design/DECISIONS.md) — no
# identity/sign-in exists yet, so `email` is always None; the flag defaults
# to disabled, which keeps this a no-op and every page working exactly as
# before. If a deployment enables the flag ahead of real sign-in wiring,
# this fails closed with a neutral placeholder rather than ever running
# `selected.run()` unauthenticated. The placeholder wording distinguishes
# an unconfigured allowlist from "sign-in just isn't wired up yet" purely
# to be honest with whoever operates the deployment — it never displays
# the allowlist itself, its size, any email, or the gate's internal reason
# value.
_beta_settings = get_settings()

_beta_is_logged_in = getattr(st.user, "is_logged_in", False)
_beta_email = st.user.get("email") if _beta_is_logged_in else None

if _beta_settings.private_beta_auth_enabled and not _beta_is_logged_in:
    st.title("Private beta")
    st.write("Sign in with your approved Google account to access EevaResearch AI.")
    st.button("Continue with Google", on_click=st.login, args=("google",))
    st.stop()

_beta_gate_decision = evaluate_beta_gate(_beta_settings, email=_beta_email)

if not _beta_gate_decision.allowed:
    st.title("Private beta")
    if _beta_settings.private_beta_allowed_emails:
        st.error("This Google account is not approved for the private beta.")
        if _beta_is_logged_in:
            st.button("Sign out", on_click=st.logout)
    else:
        st.info(
            "Private beta access is being configured. "
            "Approved beta accounts have not been configured on this deployment yet."
        )
    st.stop()

selected = st.navigation(list(pages.values()), position="hidden")
selected.run()
