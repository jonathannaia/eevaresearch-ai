"""Private-beta access foundation, Phase 1 — src/ui/beta_gate.py's pure
decision logic, plus the two new Settings env-var fields it reads (see
design/DECISIONS.md). No network calls, no Streamlit runtime except the one
narrow app-level smoke check at the bottom, matching this repo's existing
AppTest convention (test_app_smoke.py).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.config.settings import Settings
from src.ui.beta_gate import BetaGateReason, evaluate_beta_gate


# --- Settings: EDGE_PRIVATE_BETA_AUTH_ENABLED parsing ---


def test_beta_auth_defaults_disabled_when_env_absent(monkeypatch):
    monkeypatch.delenv("EDGE_PRIVATE_BETA_AUTH_ENABLED", raising=False)
    assert Settings().private_beta_auth_enabled is False


@pytest.mark.parametrize(
    "raw_value",
    ["1", "true", "TRUE", " true ", "True", "yes", "YES", "on", "ON", " on ", "  1  "],
)
def test_beta_auth_enabled_for_every_accepted_truthy_spelling(monkeypatch, raw_value):
    monkeypatch.setenv("EDGE_PRIVATE_BETA_AUTH_ENABLED", raw_value)
    assert Settings().private_beta_auth_enabled is True


@pytest.mark.parametrize(
    "raw_value",
    ["0", "false", "False", "no", "off", "", "   ", "enabled", "yup", "2"],
)
def test_beta_auth_stays_disabled_for_false_or_unrecognized_values(monkeypatch, raw_value):
    monkeypatch.setenv("EDGE_PRIVATE_BETA_AUTH_ENABLED", raw_value)
    assert Settings().private_beta_auth_enabled is False


# --- Settings: EDGE_PRIVATE_BETA_ALLOWED_EMAILS parsing ---


def test_beta_allowed_emails_defaults_empty_when_env_absent(monkeypatch):
    monkeypatch.delenv("EDGE_PRIVATE_BETA_ALLOWED_EMAILS", raising=False)
    assert Settings().private_beta_allowed_emails == frozenset()


def test_beta_allowed_emails_defaults_empty_when_env_blank(monkeypatch):
    monkeypatch.setenv("EDGE_PRIVATE_BETA_ALLOWED_EMAILS", "   ")
    assert Settings().private_beta_allowed_emails == frozenset()


def test_beta_allowed_emails_strips_lowercases_dedupes_and_ignores_blank_entries(monkeypatch):
    monkeypatch.setenv(
        "EDGE_PRIVATE_BETA_ALLOWED_EMAILS",
        "Founder@Example.com, tester@example.com ,, TESTER@example.com , ",
    )
    assert Settings().private_beta_allowed_emails == frozenset(
        {"founder@example.com", "tester@example.com"}
    )


# --- beta_gate.evaluate_beta_gate: pure decision logic ---


def _settings(*, enabled: bool, allowed_emails: frozenset[str] = frozenset()) -> Settings:
    return Settings(
        private_beta_auth_enabled=enabled,
        private_beta_allowed_emails=allowed_emails,
    )


def test_flag_disabled_allows_regardless_of_email():
    decision = evaluate_beta_gate(_settings(enabled=False), email=None)
    assert decision.allowed is True
    assert decision.reason == BetaGateReason.AUTH_DISABLED


def test_flag_enabled_missing_email_requires_sign_in():
    settings = _settings(enabled=True, allowed_emails=frozenset({"founder@example.com"}))
    decision = evaluate_beta_gate(settings, email=None)
    assert decision.allowed is False
    assert decision.reason == BetaGateReason.SIGN_IN_REQUIRED


def test_flag_enabled_blank_email_requires_sign_in():
    settings = _settings(enabled=True, allowed_emails=frozenset({"founder@example.com"}))
    decision = evaluate_beta_gate(settings, email="   ")
    assert decision.allowed is False
    assert decision.reason == BetaGateReason.SIGN_IN_REQUIRED


def test_flag_enabled_empty_allowlist_denies_even_with_email():
    settings = _settings(enabled=True, allowed_emails=frozenset())
    decision = evaluate_beta_gate(settings, email="founder@example.com")
    assert decision.allowed is False
    assert decision.reason == BetaGateReason.EMPTY_ALLOWLIST


def test_flag_enabled_allowlisted_email_allows_case_insensitively():
    settings = _settings(enabled=True, allowed_emails=frozenset({"founder@example.com"}))
    decision = evaluate_beta_gate(settings, email="  Founder@Example.com  ")
    assert decision.allowed is True
    assert decision.reason == BetaGateReason.ALLOWED_EMAIL


def test_flag_enabled_non_allowlisted_email_requires_invite():
    settings = _settings(enabled=True, allowed_emails=frozenset({"founder@example.com"}))
    decision = evaluate_beta_gate(settings, email="stranger@example.com")
    assert decision.allowed is False
    assert decision.reason == BetaGateReason.INVITE_REQUIRED


# --- beta_gate.py must stay a pure, Streamlit-free module ---


def test_beta_gate_module_has_no_streamlit_import():
    import src.ui.beta_gate as beta_gate_module

    source = Path(beta_gate_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_names = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    assert not any(name.startswith("streamlit") for name in imported_names)


# --- Narrow app-level smoke check (existing AppTest convention) ---


_APP_PATH = Path(__file__).parent.parent / "app.py"


def test_app_with_beta_auth_disabled_by_default_renders_normally(monkeypatch):
    monkeypatch.delenv("EDGE_PRIVATE_BETA_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("EDGE_PRIVATE_BETA_ALLOWED_EMAILS", raising=False)

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(_APP_PATH), default_timeout=15)
    at.run()

    assert not at.exception
    assert not at.info
    assert len(at.markdown) > 0


def test_app_with_beta_auth_enabled_and_empty_allowlist_shows_unconfigured_placeholder(monkeypatch):
    monkeypatch.setenv("EDGE_PRIVATE_BETA_AUTH_ENABLED", "true")
    monkeypatch.delenv("EDGE_PRIVATE_BETA_ALLOWED_EMAILS", raising=False)

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(_APP_PATH), default_timeout=15)
    at.run()

    assert not at.exception
    assert [info.value for info in at.info] == [
        "Private beta access is being configured. "
        "Approved beta accounts have not been configured on this deployment yet."
    ]


def test_app_with_beta_auth_enabled_and_configured_allowlist_reaches_auth_flow(monkeypatch):
    monkeypatch.setenv("EDGE_PRIVATE_BETA_AUTH_ENABLED", "true")
    monkeypatch.setenv("EDGE_PRIVATE_BETA_ALLOWED_EMAILS", "founder@example.com")

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(_APP_PATH), default_timeout=15)
    at.run()

    assert not at.exception
    assert [button.label for button in at.button] in (
        ["Continue with Google"],
        ["Sign out"],
    )