"""Mandatory Google sign-in gate (design/DECISIONS.md) — app.py's own
pre-navigation auth check, plus the sidebar sign-out control added to
src/ui/ui.py.render_sidebar(). Exercised entirely through Streamlit's
AppTest harness (this repo's existing convention — see
tests/test_beta_gate.py's own app-level smoke checks); no real Google
login, OAuth secrets, or network call anywhere.

Streamlit's AppTest (installed version 1.61.1) has no public API to
simulate a logged-in st.user — there is no documented hook to set
is_logged_in/email for a test run. These tests instead monkeypatch the
private streamlit.user_info._get_user_info() function, the single
internal seam every st.user access funnels through (confirmed by reading
.venv/lib/python3.12/site-packages/streamlit/user_info.py:378-392 — every
st.user attribute/item access calls this same module-level function).
This is an implementation detail, not part of Streamlit's public/stable
API surface — a future Streamlit upgrade could rename or remove it and
silently break these tests without any change to app.py or ui.py
themselves. Likewise, AppTest cannot click a button bound to
on_click=st.logout and drive its real cookie-clearing flow (that flow is
entirely server/browser-cookie based); the logout test below simulates
its documented effect directly instead — st.user.is_logged_in becoming
False on the next rerun.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_APP_PATH = Path(__file__).parent.parent / "app.py"

# Never expect any of these — token/cookie/secret material, or a raw env
# var name that would confirm a secret's presence — anywhere in rendered
# output, regardless of auth state.
_FORBIDDEN_SUBSTRINGS = (
    "cookie_secret",
    "client_secret",
    "id_token",
    "access_token",
    "authorization: bearer",
    "oauth2callback",
    "edge_google_client_secret",
    "edge_auth_cookie_secret",
)


def _run_app(monkeypatch, *, logged_in: bool, email: str | None = None, allowed_emails: str | None = None):
    if allowed_emails is None:
        monkeypatch.delenv("EDGE_PRIVATE_BETA_ALLOWED_EMAILS", raising=False)
    else:
        monkeypatch.setenv("EDGE_PRIVATE_BETA_ALLOWED_EMAILS", allowed_emails)

    if logged_in:
        import streamlit.user_info as user_info_module

        monkeypatch.setattr(
            user_info_module, "_get_user_info", lambda: {"is_logged_in": True, "email": email}
        )

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(_APP_PATH), default_timeout=15)
    at.run()
    return at


def _all_rendered_text(at) -> str:
    chunks: list[str] = []
    chunks.extend(t.value for t in at.title)
    chunks.extend(m.value for m in at.markdown)
    chunks.extend(c.value for c in at.caption)
    chunks.extend(e.value for e in at.error)
    return "\n".join(chunks)


# --- Anonymous visitor: blocked before navigation/any page content ---


def test_anonymous_visitor_sees_only_sign_in_screen(monkeypatch):
    at = _run_app(monkeypatch, logged_in=False)

    assert not at.exception
    assert [t.value for t in at.title] == ["Sign in to EevaResearch AI"]
    assert [b.label for b in at.button] == ["Continue with Google"]


def test_anonymous_visitor_never_builds_or_reaches_any_page(monkeypatch):
    at = _run_app(monkeypatch, logged_in=False)

    assert not at.exception
    # _build_pages()/st.navigation() never run for an anonymous visitor —
    # this is what makes every route (sidebar-linked or deep-linked)
    # unreachable, not merely hidden from a nav widget.
    assert "_pages" not in at.session_state
    assert "_has_visited" not in at.session_state


# --- Authenticated visitor: allowlist behavior ---


def test_authenticated_user_with_empty_allowlist_reaches_normal_app(monkeypatch):
    at = _run_app(monkeypatch, logged_in=True, email="anyone@example.com", allowed_emails=None)

    assert not at.exception
    assert "_pages" in at.session_state
    titles = [t.value for t in at.title]
    assert "Sign in to EevaResearch AI" not in titles
    assert "Private beta" not in titles


def test_authenticated_user_on_nonempty_allowlist_reaches_normal_app(monkeypatch):
    at = _run_app(
        monkeypatch, logged_in=True, email="Founder@Example.com", allowed_emails="founder@example.com"
    )

    assert not at.exception
    assert "_pages" in at.session_state


def test_authenticated_user_not_on_nonempty_allowlist_is_blocked_before_navigation(monkeypatch):
    at = _run_app(
        monkeypatch, logged_in=True, email="stranger@example.com", allowed_emails="founder@example.com"
    )

    assert not at.exception
    assert [t.value for t in at.title] == ["Private beta"]
    assert "_pages" not in at.session_state
    assert [b.label for b in at.button] == ["Sign out"]


# --- Sidebar: signed-in email + sign-out control ---


def test_authenticated_user_sees_signed_in_email_and_sign_out_in_sidebar(monkeypatch):
    # Home renders chrome-free on a session's very first visit (by design —
    # see app.py's own "Home renders on first visit only" comment), so the
    # sidebar (and its sign-out control) only appears once Dashboard takes
    # over as default — i.e. on a second visit. Pre-seeding "_has_visited"
    # simulates that without needing a real second AppTest.run() round trip.
    monkeypatch.delenv("EDGE_PRIVATE_BETA_ALLOWED_EMAILS", raising=False)
    import streamlit.user_info as user_info_module

    monkeypatch.setattr(
        user_info_module,
        "_get_user_info",
        lambda: {"is_logged_in": True, "email": "member@example.com"},
    )

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(_APP_PATH), default_timeout=15)
    at.session_state["_has_visited"] = True
    at.run()

    assert not at.exception
    text = _all_rendered_text(at)
    assert "Signed in as member@example.com" in text
    assert "Sign out" in [b.label for b in at.button]


def test_denied_user_does_not_see_sidebar_signed_in_text(monkeypatch):
    # The sidebar's "Signed in as ..." caption only exists inside
    # with_chrome()-wrapped pages, which a denied user never reaches —
    # only app.py's own denial screen's "Sign out" button is expected.
    at = _run_app(
        monkeypatch, logged_in=True, email="stranger@example.com", allowed_emails="founder@example.com"
    )

    assert not at.exception
    text = _all_rendered_text(at)
    assert "Signed in as" not in text


# --- Logout: next rerun returns to the anonymous/sign-in state ---


def test_logout_returns_next_rerun_to_sign_in_screen(monkeypatch):
    import streamlit.user_info as user_info_module

    monkeypatch.delenv("EDGE_PRIVATE_BETA_ALLOWED_EMAILS", raising=False)
    monkeypatch.setattr(
        user_info_module, "_get_user_info", lambda: {"is_logged_in": True, "email": "member@example.com"}
    )

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(_APP_PATH), default_timeout=15)
    at.run()
    assert not at.exception
    assert "_pages" in at.session_state

    # st.logout() clears Streamlit's own identity cookie/session — AppTest
    # cannot click the sidebar's on_click=st.logout button or exercise the
    # real cookie-clearing flow (see module docstring), so this simulates
    # its documented effect directly on the next rerun.
    monkeypatch.setattr(user_info_module, "_get_user_info", lambda: {"is_logged_in": False})
    at.run()

    assert not at.exception
    # A stale "_pages" dict object from the prior authenticated run can
    # legitimately linger in session_state (st.stop() halts execution
    # without clearing earlier keys) — that's harmless, since nothing
    # reads it again this rerun. The real proof of "back to anonymous" is
    # that the gate stops here: only the sign-in screen renders, and
    # st.navigation()/selected.run() never executes.
    assert [t.value for t in at.title] == ["Sign in to EevaResearch AI"]
    assert [b.label for b in at.button] == ["Continue with Google"]


# --- No secret/token/cookie/session material is ever rendered ---


@pytest.mark.parametrize(
    "logged_in,email,allowed_emails",
    [
        (False, None, None),
        (True, "member@example.com", None),
        (True, "stranger@example.com", "founder@example.com"),
    ],
)
def test_no_secret_token_cookie_or_session_material_is_rendered(monkeypatch, logged_in, email, allowed_emails):
    at = _run_app(monkeypatch, logged_in=logged_in, email=email, allowed_emails=allowed_emails)

    assert not at.exception
    text = _all_rendered_text(at).lower()
    for forbidden in _FORBIDDEN_SUBSTRINGS:
        assert forbidden not in text
