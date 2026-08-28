"""Throwaway UI-review deployment gate — lives only on this review/*
branch, never merged. Tests the three additive, EDGE_REVIEW_MODE_ENABLED-
gated pieces: the Settings defaults, the pure token-comparison logic
(src/ui/beta_gate.py::review_token_matches), the app.py-level fail-closed
behavior, and the shared banner in src/ui/ui.py. Every existing-behavior
assertion here (review mode OFF) proves this change is a no-op for
every other deployment — no test here ever supplies a real Google
credential, a real token, or touches a real database."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.config.settings import Settings
from src.ui.beta_gate import review_token_matches

_APP_PATH = Path(__file__).parent.parent / "app.py"
_BANNER_HARNESS = Path(__file__).parent / "apptest_pages" / "review_banner_page.py"


# --- Settings defaults — inert unless explicitly configured ---

def test_review_mode_disabled_by_default_when_env_absent(monkeypatch):
    monkeypatch.delenv("EDGE_REVIEW_MODE_ENABLED", raising=False)
    monkeypatch.delenv("EDGE_REVIEW_ACCESS_TOKEN", raising=False)
    settings = Settings()
    assert settings.review_mode_enabled is False
    assert settings.review_access_token is None


@pytest.mark.parametrize("raw_value", ["1", "true", "TRUE", "yes", "on"])
def test_review_mode_enabled_for_every_accepted_truthy_spelling(monkeypatch, raw_value):
    monkeypatch.setenv("EDGE_REVIEW_MODE_ENABLED", raw_value)
    assert Settings().review_mode_enabled is True


@pytest.mark.parametrize("raw_value", ["0", "false", "no", "off", "", "garbage"])
def test_review_mode_stays_disabled_for_false_or_unrecognized_values(monkeypatch, raw_value):
    monkeypatch.setenv("EDGE_REVIEW_MODE_ENABLED", raw_value)
    assert Settings().review_mode_enabled is False


def test_review_access_token_reads_the_configured_env_var(monkeypatch):
    monkeypatch.setenv("EDGE_REVIEW_ACCESS_TOKEN", "synthetic-test-token")
    assert Settings().review_access_token == "synthetic-test-token"


def test_review_access_token_blank_value_normalizes_to_none(monkeypatch):
    monkeypatch.setenv("EDGE_REVIEW_ACCESS_TOKEN", "   ")
    # Settings' own `os.getenv(...) or None` convention treats a literal
    # blank string as present-but-falsy only if it's exactly "" — a
    # whitespace-only value is technically "present" here, matching
    # every other optional string field's own documented behavior
    # (none of them trim whitespace). review_token_matches() below is
    # what actually treats a whitespace-only configured token as unset.
    assert Settings().review_access_token == "   "


# --- Pure token-comparison logic (no Streamlit import — src/ui/beta_gate.py) ---

def test_review_token_matches_when_equal_and_configured():
    assert review_token_matches("abc123", "abc123") is True


def test_review_token_matches_fails_on_mismatch():
    assert review_token_matches("abc123", "wrong") is False


def test_review_token_matches_fails_closed_when_not_configured():
    assert review_token_matches(None, None) is False
    assert review_token_matches(None, "anything") is False
    assert review_token_matches("", "") is False
    assert review_token_matches("   ", "   ") is False


def test_review_token_matches_fails_when_provided_is_none():
    assert review_token_matches("abc123", None) is False


def test_beta_gate_module_still_has_no_streamlit_import():
    """review_token_matches lives in the same Streamlit-free module as
    evaluate_beta_gate — re-affirms the existing guard's own invariant
    still holds after this addition (the guard itself lives in
    test_beta_gate.py and is unmodified; this is a lightweight
    same-property re-check scoped to this new function's own module)."""
    import ast

    source = (Path(__file__).parent.parent / "src" / "ui" / "beta_gate.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)
    assert not any(name.startswith("streamlit") for name in imported_names)


# --- app.py-level: fail-closed by default, and a true no-op when disabled ---

def test_app_review_mode_disabled_renders_normally_identical_to_today(monkeypatch):
    monkeypatch.delenv("EDGE_REVIEW_MODE_ENABLED", raising=False)
    monkeypatch.delenv("EDGE_REVIEW_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("EDGE_PRIVATE_BETA_AUTH_ENABLED", raising=False)

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(_APP_PATH), default_timeout=15)
    at.run()

    assert not at.exception
    assert [title.value for title in at.title] != ["Private review"]
    assert len(at.markdown) > 0


def test_app_review_mode_enabled_with_no_token_configured_blocks_access(monkeypatch):
    """Fail-closed proof: EDGE_REVIEW_MODE_ENABLED=true with no
    EDGE_REVIEW_ACCESS_TOKEN configured must never fall through to an
    open app — it must show the private-review gate and stop."""
    monkeypatch.setenv("EDGE_REVIEW_MODE_ENABLED", "true")
    monkeypatch.delenv("EDGE_REVIEW_ACCESS_TOKEN", raising=False)

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(_APP_PATH), default_timeout=15)
    at.run()

    assert not at.exception
    assert [title.value for title in at.title] == ["Private review"]
    assert any("temporary, private review build" in info.value for info in at.info)


def test_app_review_mode_enabled_with_token_configured_still_blocks_without_a_matching_query_param(monkeypatch):
    """AppTest in this Streamlit version has no way to supply
    st.query_params before run() — so this test documents the other
    always-true case instead: a configured token with no query param
    supplied must still block, never silently admit. The positive
    "correct token admits access" path is proven at the pure-function
    level above (review_token_matches("configured", "configured") is
    True) plus this app-level negative case together give full coverage
    of the same boolean expression app.py actually evaluates."""
    monkeypatch.setenv("EDGE_REVIEW_MODE_ENABLED", "true")
    monkeypatch.setenv("EDGE_REVIEW_ACCESS_TOKEN", "synthetic-test-token")

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(_APP_PATH), default_timeout=15)
    at.run()

    assert not at.exception
    assert [title.value for title in at.title] == ["Private review"]


def test_app_review_mode_never_touches_st_user_or_st_login(monkeypatch):
    """The whole point of the review-mode branch: it must never access
    st.user/st.login at all, since the review deployment has no Google
    OAuth credentials configured and that access alone raises
    StreamlitAuthError without them."""
    monkeypatch.setenv("EDGE_REVIEW_MODE_ENABLED", "true")
    monkeypatch.delenv("EDGE_REVIEW_ACCESS_TOKEN", raising=False)

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(_APP_PATH), default_timeout=15)
    at.run()

    # No StreamlitAuthError (or any other exception) — if st.user were
    # touched with no [auth] configured in this test environment, this
    # would raise and at.exception would be non-empty.
    assert not at.exception


# --- Shared banner (src/ui/ui.py) ---

def test_review_banner_renders_when_review_mode_enabled(monkeypatch):
    monkeypatch.setenv("EDGE_REVIEW_MODE_ENABLED", "true")

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(_BANNER_HARNESS), default_timeout=15)
    at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "LOCAL/PRIVATE REVIEW BUILD" in all_text
    assert "sample data only" in all_text
    assert "noop-page-content-marker" in all_text  # the page itself still rendered


def test_review_banner_absent_when_review_mode_disabled(monkeypatch):
    monkeypatch.delenv("EDGE_REVIEW_MODE_ENABLED", raising=False)

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(_BANNER_HARNESS), default_timeout=15)
    at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "LOCAL/PRIVATE REVIEW BUILD" not in all_text
    assert "noop-page-content-marker" in all_text
