"""Admin Users v1 (design/DECISIONS.md) — src/ui/ui.py's is_admin(),
the single authorization check for the hidden Admin -> Users page.
Driven only by EEVA_ADMIN_EMAILS (Settings.admin_emails), reusing
_parse_beta_allowed_emails's exact comma-separated/case-insensitive/
fail-closed-on-empty parsing (see tests/test_beta_gate.py's own
EDGE_PRIVATE_BETA_ALLOWED_EMAILS parsing tests for that shared
contract). No Streamlit runtime/AppTest needed — is_admin() reads
st.user directly, which works outside AppTest too once
streamlit.user_info._get_user_info is monkeypatched (same seam
tests/test_app_auth_gate.py's own module docstring documents)."""
from __future__ import annotations

import streamlit.user_info as user_info_module

from src.config.settings import Settings
from src.ui.ui import is_admin


def _sign_in_as(monkeypatch, *, logged_in: bool, email: str | None = None) -> None:
    info = {"is_logged_in": logged_in}
    if logged_in:
        info["email"] = email
    monkeypatch.setattr(user_info_module, "_get_user_info", lambda: info)


def test_admin_emails_defaults_empty_when_env_absent(monkeypatch):
    monkeypatch.delenv("EEVA_ADMIN_EMAILS", raising=False)
    assert Settings().admin_emails == frozenset()


def test_admin_emails_strips_lowercases_dedupes_and_ignores_blank_entries(monkeypatch):
    monkeypatch.setenv("EEVA_ADMIN_EMAILS", "Admin@Example.test, second@example.test ,, SECOND@example.test , ")
    assert Settings().admin_emails == frozenset({"admin@example.test", "second@example.test"})


def test_matching_authenticated_email_is_admin(monkeypatch):
    _sign_in_as(monkeypatch, logged_in=True, email="admin@example.test")
    settings = Settings(admin_emails=frozenset({"admin@example.test"}))
    assert is_admin(settings) is True


def test_matching_is_case_insensitive(monkeypatch):
    _sign_in_as(monkeypatch, logged_in=True, email="  Admin@Example.Test  ")
    settings = Settings(admin_emails=frozenset({"admin@example.test"}))
    assert is_admin(settings) is True


def test_empty_admin_emails_denies_every_authenticated_user(monkeypatch):
    _sign_in_as(monkeypatch, logged_in=True, email="admin@example.test")
    settings = Settings(admin_emails=frozenset())
    assert is_admin(settings) is False


def test_non_matching_authenticated_email_is_denied(monkeypatch):
    _sign_in_as(monkeypatch, logged_in=True, email="stranger@example.test")
    settings = Settings(admin_emails=frozenset({"admin@example.test"}))
    assert is_admin(settings) is False


def test_unauthenticated_visitor_is_never_admin_even_if_email_would_match(monkeypatch):
    _sign_in_as(monkeypatch, logged_in=False)
    settings = Settings(admin_emails=frozenset({"admin@example.test"}))
    assert is_admin(settings) is False


def test_missing_email_claim_while_logged_in_is_denied(monkeypatch):
    monkeypatch.setattr(user_info_module, "_get_user_info", lambda: {"is_logged_in": True})
    settings = Settings(admin_emails=frozenset({"admin@example.test"}))
    assert is_admin(settings) is False


def test_is_admin_uses_get_settings_when_none_passed(monkeypatch):
    monkeypatch.delenv("EEVA_ADMIN_EMAILS", raising=False)
    _sign_in_as(monkeypatch, logged_in=True, email="admin@example.test")
    assert is_admin() is False
