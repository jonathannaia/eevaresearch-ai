"""Theme preference: resolution, persistence, and the client bridge.

A reader chooses System (follow the operating system), Dark, or Light
from the sidebar account menu or the ⌘K palette. The choice is resolved
server-side on every render, in this order:

  1. this browser session's own choice (st.session_state);
  2. the signed-in account's saved preference (user_preferences table),
     read once per session, never per rerun;
  3. the `eeva_theme` cookie, which the bridge below writes on every
     render so the choice survives reloads, sign-out/sign-in, and the
     Google OAuth round trip (a SameSite=Lax cookie is sent on that
     top-level redirect back to the app);
  4. System.

The resolved preference feeds load_css() (src/ui/ui.py), whose token CSS
is correct on the first paint for all three choices. The bridge then
brings the browser in line: it stamps data-theme on <html> (removed for
System, so prefers-color-scheme decides), mirrors the choice into the
cookie and localStorage, and writes Streamlit's own per-path theme key
(`stActiveTheme-<path>-v2`) so the native widgets use the same theme.
Streamlit reads that key only at page load, so when the stored native
value for the current path differs from the choice, the bridge reloads
the page once — guarded against loops, and skipped entirely if storage
is unavailable.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import streamlit as st

from src.models.user_preferences import THEME_PREFERENCES, ThemePreference, is_theme_preference

COOKIE_NAME = "eeva_theme"
STORAGE_KEY = "eeva.theme"
_SESSION_KEY = "_eeva_theme_preference"
_ACCOUNT_LOADED_KEY = "_eeva_theme_account_loaded"

LABELS: dict[ThemePreference, str] = {"system": "System", "dark": "Dark", "light": "Light"}

# Every page path the app registers (app.py's url_path values plus the
# root). Streamlit keys its stored theme by window.location.pathname;
# writing all of them means a later full load of any page already
# agrees, and the current path is always written as well.
ROUTE_PATHS: tuple[str, ...] = (
    "/", "/dashboard", "/radar-inbox", "/daily-news", "/coverage", "/themes", "/signals", "/methodology", "/about",
    "/disclaimer", "/daily-news-admin", "/research-cases", "/theme-workspace", "/company-discovery-admin",
    "/verified-updates", "/admin-users", "/feedback",
)
_ACCOUNT_PREF_KEY = "_eeva_theme_account_preference"
_RELOAD_GUARD_KEY = "eeva.theme.reload"
_RELOAD_GUARD_MS = 10_000


def _signed_in_email() -> str | None:
    try:
        if getattr(st.user, "is_logged_in", False):
            email = st.user.get("email")
            return email.strip().lower() if email else None
    except Exception:  # noqa: BLE001 — no auth configured (tests, local harness)
        return None
    return None


def _cookie_preference() -> ThemePreference | None:
    try:
        value = st.context.cookies.get(COOKIE_NAME)
    except Exception:  # noqa: BLE001 — no browser context (bare/test runs)
        return None
    return value if is_theme_preference(value) else None


def _preferences_repository():
    from src.config.settings import get_settings
    from src.data_access import backend_factory

    return backend_factory.get_user_preferences_repository(get_settings())


def _account_preference(email: str) -> ThemePreference | None:
    """The account's saved choice, read at most once per browser session
    (cached in session state) — never a database round trip per rerun."""
    if st.session_state.get(_ACCOUNT_LOADED_KEY) == email:
        return st.session_state.get(_ACCOUNT_PREF_KEY)
    preference = None
    try:
        repository = _preferences_repository()
        try:
            saved = repository.get_preferences(email)
        finally:
            repository.close()
        preference = saved.theme_preference if saved is not None else None
    except Exception:  # noqa: BLE001 — a preference must never block a page
        print("[theme] Account theme preference read failed.")
    st.session_state[_ACCOUNT_LOADED_KEY] = email
    st.session_state[_ACCOUNT_PREF_KEY] = preference
    return preference


def current_preference() -> ThemePreference:
    chosen = st.session_state.get(_SESSION_KEY)
    if is_theme_preference(chosen):
        return chosen
    email = _signed_in_email()
    if email:
        saved = _account_preference(email)
        if saved is not None:
            return saved
    return _cookie_preference() or "system"


def set_preference(preference: object) -> bool:
    """Record a reader's choice for this session and, when signed in, on
    their account. Returns False (and changes nothing) for any value
    other than the three choices."""
    if not is_theme_preference(preference):
        return False
    st.session_state[_SESSION_KEY] = preference
    email = _signed_in_email()
    if email:
        st.session_state[_ACCOUNT_LOADED_KEY] = email
        st.session_state[_ACCOUNT_PREF_KEY] = preference
        try:
            repository = _preferences_repository()
            try:
                repository.set_theme_preference(email, preference, datetime.now(timezone.utc).isoformat())
            finally:
                repository.close()
        except Exception:  # noqa: BLE001 — the session choice still applies
            print("[theme] Account theme preference save failed.")
    return True


def resolved_theme(preference: ThemePreference) -> str:
    """"dark" or "light" — for server-drawn output that cannot read CSS
    custom properties (charts). System follows Streamlit's own reading of
    the browser's color scheme, falling back to Dark."""
    if preference in ("dark", "light"):
        return preference
    try:
        detected = st.context.theme.type
    except Exception:  # noqa: BLE001
        detected = None
    return detected if detected in ("dark", "light") else "dark"


def bridge_script(preference: ThemePreference) -> str:
    """The script body never contains "<": Streamlit sanitizes st.html
    with DOMPurify, which silently drops a script whose text has "<"
    followed by a word character (e.g. "a<10000")."""
    native = json.dumps(LABELS[preference])
    return (
        "<script>(function(){"
        f"var p={json.dumps(preference)},n={native},paths={json.dumps(list(ROUTE_PATHS))};"
        "var d=document.documentElement;"
        "if(p==='system'){d.removeAttribute('data-theme');}else{d.setAttribute('data-theme',p);}"
        f"document.cookie='{COOKIE_NAME}='+p+'; Path=/; Max-Age=31536000; SameSite=Lax'"
        "+(location.protocol==='https:'?'; Secure':'');"
        "var ls,ss;try{ls=window.localStorage;ss=window.sessionStorage;ls.getItem('x');ss.getItem('x');}catch(e){return;}"
        "var key=function(x){return 'stActiveTheme-'+x+'-v2';};"
        "var want=JSON.stringify(n),before=ls.getItem(key(location.pathname));"
        "try{"
        f"ls.setItem('{STORAGE_KEY}',p);"
        "paths.concat([location.pathname]).forEach(function(x){ls.setItem(key(x),want);});"
        "}catch(e){return;}"
        # Streamlit already follows the OS when nothing is stored.
        "if(before===want||(before===null&&n==='System')){return;}"
        # One reload per choice within the guard window — never a loop.
        f"var last=null;try{{last=JSON.parse(ss.getItem('{_RELOAD_GUARD_KEY}')||'null');}}catch(e){{return;}}"
        f"if(last&&last.n===n&&last.t+{_RELOAD_GUARD_MS}>Date.now()){{return;}}"
        f"try{{ss.setItem('{_RELOAD_GUARD_KEY}',JSON.stringify({{n:n,t:Date.now()}}));}}catch(e){{return;}}"
        "location.reload();"
        "})();</script>"
    )


def render_bridge(preference: ThemePreference) -> None:
    """Emits the bridge in a keyed container that assets/styles.css hides,
    so it never takes layout space."""
    with st.container(key="theme-bridge"):
        st.html(bridge_script(preference), unsafe_allow_javascript=True)


_CONTROL_KEY = "sidebar-theme-control"


def _on_control_change() -> None:
    choice = st.session_state.get(_CONTROL_KEY)
    if not set_preference(choice):
        # Segmented controls can be deselected; keep the current choice shown.
        st.session_state[_CONTROL_KEY] = current_preference()


def render_theme_control() -> None:
    """System / Dark / Light, in the sidebar account menu."""
    current = current_preference()
    if st.session_state.get(_CONTROL_KEY) != current:
        st.session_state[_CONTROL_KEY] = current
    st.segmented_control(
        "Theme", options=list(THEME_PREFERENCES), format_func=LABELS.get, key=_CONTROL_KEY,
        on_change=_on_control_change, width="stretch",
    )
