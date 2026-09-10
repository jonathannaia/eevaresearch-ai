"""Admin Users v1 (design/DECISIONS.md) — app.py's own once-per-session
sign-in tracking hook: records an authenticated, allowed visitor's
sign-in via backend_factory.get_user_account_repository(settings)
.record_sign_in(), called directly (not via get_repositories()/
AppContext — see src/data_access/container.py's own comment for why),
at most once per Streamlit browser session (st.session_state guard),
only after the mandatory sign-in gate and the optional allowlist gate
have both already passed. A write failure — including the repository
construction call itself — must never block a legitimate user, and
must not retry every rerun within the same session, but must emit
exactly one sanitized, non-sensitive operational log line, never the
email, exception text, backend, or any connection detail.

Patches src.data_access.backend_factory.get_user_account_repository
directly (the defining module's own attribute) rather than a
from-import-rebound name — app.py imports the backend_factory *module*
(`from src.data_access import backend_factory`), so patching the
module's own attribute is what every re-execution of app.py's script
body actually reads, regardless of how many times AppTest reruns it."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from streamlit.testing.v1 import AppTest

_APP_PATH = Path(__file__).parent.parent / "app.py"


def _sign_in_as(monkeypatch, email: str = "member@example.test") -> None:
    import streamlit.user_info as user_info_module

    monkeypatch.setattr(
        user_info_module, "_get_user_info", lambda: {"is_logged_in": True, "email": email, "name": "Member"}
    )
    monkeypatch.delenv("EDGE_PRIVATE_BETA_ALLOWED_EMAILS", raising=False)


def _patch_factory(monkeypatch, recorder=None, *, raises: Exception | None = None) -> MagicMock:
    construct = MagicMock()
    fake_repo = MagicMock()
    if raises is not None:
        fake_repo.record_sign_in.side_effect = raises
    elif recorder is not None:
        fake_repo.record_sign_in = recorder
    construct.side_effect = lambda settings: fake_repo
    monkeypatch.setattr("src.data_access.backend_factory.get_user_account_repository", construct)
    return construct


def test_first_authenticated_run_records_sign_in_exactly_once(monkeypatch):
    _sign_in_as(monkeypatch)
    recorder = MagicMock()
    construct = _patch_factory(monkeypatch, recorder=recorder)

    at = AppTest.from_file(str(_APP_PATH), default_timeout=15)
    at.run()

    assert not at.exception
    assert construct.call_count == 1
    assert recorder.call_count == 1
    _, kwargs = recorder.call_args
    assert kwargs["email"] == "member@example.test"
    assert kwargs["display_name"] == "Member"


def test_repeat_rerun_in_the_same_session_does_not_record_again(monkeypatch):
    _sign_in_as(monkeypatch)
    recorder = MagicMock()
    construct = _patch_factory(monkeypatch, recorder=recorder)

    at = AppTest.from_file(str(_APP_PATH), default_timeout=15)
    at.run()
    at.run()
    at.run()

    assert not at.exception
    assert construct.call_count == 1
    assert recorder.call_count == 1


def test_fresh_simulated_session_records_again(monkeypatch):
    _sign_in_as(monkeypatch)
    recorder = MagicMock()
    construct = _patch_factory(monkeypatch, recorder=recorder)

    at_session_one = AppTest.from_file(str(_APP_PATH), default_timeout=15)
    at_session_one.run()
    at_session_two = AppTest.from_file(str(_APP_PATH), default_timeout=15)
    at_session_two.run()

    assert not at_session_one.exception
    assert not at_session_two.exception
    assert construct.call_count == 2
    assert recorder.call_count == 2


def test_ordinary_render_only_constructs_the_repository_from_the_sign_in_block(monkeypatch):
    """Proves the global-AppContext-ripple fix directly: an ordinary
    authenticated, non-admin visitor's full app.py render (Dashboard
    content and all) constructs get_user_account_repository exactly
    once — from the once-per-session sign-in block — never from
    anywhere else (dashboard.py, radar_inbox.py, or any other page
    get_repositories() itself might reach)."""
    _sign_in_as(monkeypatch, email="nonadmin@example.test")
    monkeypatch.delenv("EEVA_ADMIN_EMAILS", raising=False)
    construct = _patch_factory(monkeypatch)

    at = AppTest.from_file(str(_APP_PATH), default_timeout=15)
    at.run()
    at.run()  # second run: lands on Dashboard, full page content renders

    assert not at.exception
    assert construct.call_count == 1


def test_user_rejected_by_nonempty_allowlist_never_records_sign_in_or_reaches_navigation(monkeypatch):
    """The beta-allowlist gate (dataclasses.replace(...).../evaluate_beta_gate
    in app.py) sits BEFORE the sign-in-recording block — an authenticated
    visitor excluded by a non-empty EDGE_PRIVATE_BETA_ALLOWED_EMAILS must
    st.stop() at that gate, never reaching record_sign_in (and therefore
    never constructing a UserAccount repository at all) or st.navigation()."""
    _sign_in_as(monkeypatch, email="stranger@example.test")
    monkeypatch.setenv("EDGE_PRIVATE_BETA_ALLOWED_EMAILS", "founder@example.test")
    construct = _patch_factory(monkeypatch)

    at = AppTest.from_file(str(_APP_PATH), default_timeout=15)
    at.run()

    assert not at.exception
    assert [t.value for t in at.title] == ["Private beta"]
    assert construct.call_count == 0
    assert "_pages" not in at.session_state
    assert "_user_account_recorded" not in at.session_state


def test_write_failure_does_not_block_app_access_and_logs_one_sanitized_line(monkeypatch, capsys):
    _sign_in_as(monkeypatch)
    _patch_factory(
        monkeypatch,
        raises=RuntimeError("connection refused to postgres://real-host/real-db with password hunter2"),
    )

    at = AppTest.from_file(str(_APP_PATH), default_timeout=15)
    at.run()

    assert not at.exception  # a legitimate user must still reach the app
    assert "_pages" in at.session_state

    captured = capsys.readouterr()
    log_lines = [line for line in captured.out.splitlines() if "User-account session recording failed" in line]
    assert len(log_lines) == 1
    assert "member@example.test" not in captured.out
    assert "hunter2" not in captured.out
    assert "postgres://" not in captured.out
    assert "RuntimeError" not in captured.out


def test_write_failure_does_not_retry_on_a_later_rerun_in_the_same_session(monkeypatch, capsys):
    _sign_in_as(monkeypatch)
    construct = _patch_factory(monkeypatch, raises=RuntimeError("boom"))

    at = AppTest.from_file(str(_APP_PATH), default_timeout=15)
    at.run()
    at.run()
    at.run()

    assert not at.exception
    assert construct.call_count == 1

    captured = capsys.readouterr()
    log_lines = [line for line in captured.out.splitlines() if "User-account session recording failed" in line]
    assert len(log_lines) == 1
