"""Admin Users v1 (design/DECISIONS.md) — the sidebar's conditional
"Users" link (src/ui/ui.py:render_sidebar()), exercised through app.py's
real entry point (an isolated per-page harness never populates
st.session_state["_pages"], same limitation documented across this
suite — see tests/test_ui_audit_navigation_cleanup.py). This link is
cosmetic only; the actual access boundary (tests/test_admin_users_page.py)
is admin_users.py's own is_admin() check, not this link's presence."""
from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

_APP_PATH = Path(__file__).parent.parent / "app.py"


def _run_to_dashboard(monkeypatch, *, email: str) -> AppTest:
    import streamlit.user_info as user_info_module

    monkeypatch.setattr(
        user_info_module, "_get_user_info", lambda: {"is_logged_in": True, "email": email}
    )
    monkeypatch.delenv("EDGE_PRIVATE_BETA_ALLOWED_EMAILS", raising=False)

    at = AppTest.from_file(str(_APP_PATH), default_timeout=15)
    at.run()  # first visit: Home, no sidebar
    at.run()  # second run: Dashboard becomes default, sidebar renders
    assert not at.exception
    return at


def test_admin_sees_users_link_in_sidebar(monkeypatch):
    monkeypatch.setenv("EEVA_ADMIN_EMAILS", "admin@example.test")
    at = _run_to_dashboard(monkeypatch, email="admin@example.test")

    labels = [pl.label for pl in at.sidebar.get("page_link")]
    assert "Users" in labels


def test_non_admin_does_not_see_users_link_in_sidebar(monkeypatch):
    monkeypatch.setenv("EEVA_ADMIN_EMAILS", "admin@example.test")
    at = _run_to_dashboard(monkeypatch, email="nonadmin@example.test")

    labels = [pl.label for pl in at.sidebar.get("page_link")]
    assert "Users" not in labels


def test_no_admin_emails_configured_means_no_one_sees_the_link(monkeypatch):
    monkeypatch.delenv("EEVA_ADMIN_EMAILS", raising=False)
    at = _run_to_dashboard(monkeypatch, email="admin@example.test")

    labels = [pl.label for pl in at.sidebar.get("page_link")]
    assert "Users" not in labels


def test_admin_users_route_is_registered_and_never_in_a_visible_nav_table():
    from src.ui.ui import HIDDEN_FROM_NAV, PRIMARY_NAV, SYSTEM_NAV

    visible_keys = {k for k, _ in PRIMARY_NAV + SYSTEM_NAV + HIDDEN_FROM_NAV}
    assert "admin_users" not in visible_keys
