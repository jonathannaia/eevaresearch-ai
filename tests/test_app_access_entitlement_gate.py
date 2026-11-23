"""Paid professional access, phase 1 — app.py's entitlement gate, end
to end through Streamlit's AppTest harness against a real SQLite store.

This is the file that proves the feature works rather than that its
parts do: a signed-in visitor with no entitlement is stopped and offered
the form, submitting it creates a real row, an administrator approving
that row lets the same visitor in on their next sign-in, and revoking it
stops them again.

Same harness and the same st.user seam as tests/test_app_auth_gate.py —
see that file's own docstring for why `_get_user_info` is monkeypatched
and for the caveat that it is a Streamlit internal, not public API.

No real Google login, OAuth secret or network call anywhere. Every
email is synthetic."""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from src.config.settings import Settings
from src.data_access import backend_factory
from src.models.account_access import AccessRequest, AccessStatus, AccessTier

_APP_PATH = Path(__file__).parent.parent / "app.py"
_USER = "analyst@example.test"
_ADMIN = "owner@example.test"
_NOW = "2026-06-01T10:00:00+00:00"


def _configure(monkeypatch, tmp_path, *, enforcement: str | None, admin_emails: str | None = None):
    """Points the app at a throwaway SQLite store and sets only the
    variables this feature reads. Everything else is left at its real
    default, so the test exercises the deployed configuration shape."""
    monkeypatch.setenv("EDGE_DB_BACKEND", "sqlite")
    monkeypatch.setenv("EDGE_STATE_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.delenv("EDGE_PRIVATE_BETA_ALLOWED_EMAILS", raising=False)
    if enforcement is None:
        monkeypatch.delenv("EDGE_ACCESS_ENFORCEMENT_ENABLED", raising=False)
    else:
        monkeypatch.setenv("EDGE_ACCESS_ENFORCEMENT_ENABLED", enforcement)
    if admin_emails is None:
        monkeypatch.delenv("EEVA_ADMIN_EMAILS", raising=False)
    else:
        monkeypatch.setenv("EEVA_ADMIN_EMAILS", admin_emails)


def _repository(tmp_path):
    return backend_factory.get_account_entitlement_repository(
        dataclasses.replace(
            Settings(), db_backend="sqlite", state_db_path=tmp_path / "state.db", cache_dir=tmp_path
        )
    )


def _run_app(monkeypatch, *, email: str):
    import streamlit.user_info as user_info_module

    monkeypatch.setattr(
        user_info_module, "_get_user_info",
        lambda: {"is_logged_in": True, "email": email, "name": "Test Person"},
    )
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(_APP_PATH), default_timeout=25)
    at.run()
    return at


def _text(at) -> str:
    chunks: list[str] = []
    for collection in (at.title, at.markdown, at.caption, at.error, at.warning, at.info, at.success):
        chunks.extend(element.value for element in collection)
    return "\n".join(chunks)


def _reached_the_app(at) -> bool:
    """The app proper builds its page set; a gate screen never does."""
    return "_pages" in at.session_state


# --- dormant by default: the deploy that creates the table ------------------

def test_with_enforcement_unset_an_account_with_no_entitlement_reaches_the_app(monkeypatch, tmp_path):
    """The property that makes this safe to deploy. On the deploy that
    applies the migration every account has no entitlement row, so if
    this failed, shipping it would lock out every existing user."""
    _configure(monkeypatch, tmp_path, enforcement=None)
    at = _run_app(monkeypatch, email=_USER)

    assert not at.exception
    assert _reached_the_app(at)
    assert "Request access to EevaResearch" not in _text(at)


def test_with_enforcement_unset_the_entitlement_store_is_never_even_read(monkeypatch, tmp_path):
    """Dormant must also mean no cost: no connection, no query, no
    decision cached. A page that pays for a disabled feature on every
    session is how dormant features stop being free."""
    _configure(monkeypatch, tmp_path, enforcement=None)

    calls = []
    original = backend_factory.get_account_entitlement_repository
    monkeypatch.setattr(
        backend_factory, "get_account_entitlement_repository",
        lambda settings: calls.append(1) or original(settings),
    )
    at = _run_app(monkeypatch, email=_USER)

    assert not at.exception
    assert calls == [], "the repository was constructed while enforcement was off"
    assert "_account_access_decision" not in at.session_state


# --- enforcement on: the denial path ---------------------------------------

def test_an_account_with_no_entitlement_is_stopped_and_offered_the_form(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, enforcement="true")
    at = _run_app(monkeypatch, email=_USER)

    assert not at.exception
    assert not _reached_the_app(at), "a non-entitled visitor must never build the page set"
    assert "Request access to EevaResearch" in _text(at)
    labels = [b.label for b in at.button] + [b.label for b in at.get("form_submit_button")]
    assert "Request access" in labels, labels


def test_a_pending_request_is_told_it_is_pending_and_not_asked_again(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, enforcement="true")
    _repository(tmp_path).request_access(_USER, AccessRequest(reason="Evaluating."), _NOW)

    at = _run_app(monkeypatch, email=_USER)

    assert not at.exception
    assert not _reached_the_app(at)
    text = _text(at)
    assert "Access requested" in text
    assert "Your request is with us" in text
    assert "Request access to EevaResearch" not in text, "re-offering the form invites spam"


def test_a_revoked_account_is_told_access_ended_and_not_offered_the_form(monkeypatch, tmp_path):
    """Revocation is a decision somebody made on purpose. Offering the
    form back would let a removed account rejoin the queue immediately."""
    _configure(monkeypatch, tmp_path, enforcement="true")
    repository = _repository(tmp_path)
    repository.set_status(
        email=_USER, status=AccessStatus.REVOKED, tier=None, actor=_ADMIN, now=_NOW
    )
    at = _run_app(monkeypatch, email=_USER)

    assert not at.exception
    assert not _reached_the_app(at)
    assert "Access ended" in _text(at)


def test_an_expired_trial_is_told_so_and_may_ask_again(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, enforcement="true")
    _repository(tmp_path).set_status(
        email=_USER, status=AccessStatus.ACTIVE, tier=AccessTier.TRIAL, actor=_ADMIN,
        now=_NOW, expires_at="2020-01-01T00:00:00+00:00",
    )
    at = _run_app(monkeypatch, email=_USER)

    assert not at.exception
    assert not _reached_the_app(at)
    text = _text(at)
    assert "Your access has ended" in text
    assert "trial" in text.lower()


def test_an_unreadable_store_does_not_claim_the_account_has_no_entitlement(monkeypatch, tmp_path):
    """Telling somebody their request was never received, when a read
    failed, is a worse error than saying nothing specific. Also asserts
    nothing technical leaks into the UI."""
    _configure(monkeypatch, tmp_path, enforcement="true")

    def _boom(settings):
        raise RuntimeError("connection refused to db-host-1 as user eeva_app")

    monkeypatch.setattr(backend_factory, "get_account_entitlement_repository", _boom)
    at = _run_app(monkeypatch, email=_USER)

    assert not at.exception
    assert not _reached_the_app(at), "a read failure must not fail open"
    text = _text(at)
    assert "Access check unavailable" in text
    assert "problem on our side" in text
    assert "Request access to EevaResearch" not in text
    for leak in ("connection refused", "db-host-1", "eeva_app", "RuntimeError"):
        assert leak not in text, leak


# --- enforcement on: the grant path ----------------------------------------

@pytest.mark.parametrize("tier", list(AccessTier))
def test_an_active_entitlement_reaches_the_app(monkeypatch, tmp_path, tier):
    _configure(monkeypatch, tmp_path, enforcement="true")
    _repository(tmp_path).set_status(
        email=_USER, status=AccessStatus.ACTIVE, tier=tier, actor=_ADMIN, now=_NOW
    )
    at = _run_app(monkeypatch, email=_USER)

    assert not at.exception
    assert _reached_the_app(at)


def test_an_admin_reaches_the_app_even_with_no_entitlement_and_a_broken_store(monkeypatch, tmp_path):
    """The operator's remedy stays reachable during an outage. Without
    this, enforcement plus a database incident locks the one person who
    can repair it out of the page that repairs it."""
    _configure(monkeypatch, tmp_path, enforcement="true", admin_emails=_ADMIN)

    def _boom(settings):
        raise RuntimeError("store down")

    monkeypatch.setattr(backend_factory, "get_account_entitlement_repository", _boom)
    at = _run_app(monkeypatch, email=_ADMIN)

    assert not at.exception
    assert _reached_the_app(at)


def test_the_break_glass_allowlist_still_admits_one_account(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, enforcement="true")
    monkeypatch.setenv("EDGE_PRIVATE_BETA_ALLOWED_EMAILS", _USER)
    at = _run_app(monkeypatch, email=_USER)

    assert not at.exception
    assert _reached_the_app(at)


# --- the whole cycle -------------------------------------------------------

def test_request_then_approve_then_revoke_changes_what_the_same_visitor_sees(monkeypatch, tmp_path):
    """The end-to-end claim, in one test, against one real store."""
    _configure(monkeypatch, tmp_path, enforcement="true", admin_emails=_ADMIN)
    repository = _repository(tmp_path)

    # 1. No entitlement: stopped, offered the form.
    at = _run_app(monkeypatch, email=_USER)
    assert not _reached_the_app(at)
    assert "Request access to EevaResearch" in _text(at)

    # 2. The request arrives (as the form's submit handler writes it).
    repository.request_access(
        _USER, AccessRequest(reason="Tracking memory capex.", organisation="Example Capital"), _NOW
    )
    at = _run_app(monkeypatch, email=_USER)
    assert not _reached_the_app(at)
    assert "Access requested" in _text(at)

    # 3. The admin sees exactly one pending request.
    pending = repository.list_entitlements(statuses=(AccessStatus.REQUESTED,))
    assert [e.email for e in pending] == [_USER]
    assert pending[0].request.organisation == "Example Capital"

    # 4. Approval, conditional on the status the queue was rendered with.
    repository.set_status(
        email=_USER, status=AccessStatus.ACTIVE, tier=AccessTier.TRIAL, actor=_ADMIN,
        now="2026-06-02T10:00:00+00:00", expires_at="2026-12-01T00:00:00+00:00",
        detail="approved from admin users page", expected_status=AccessStatus.REQUESTED,
    )
    at = _run_app(monkeypatch, email=_USER)
    assert _reached_the_app(at), "an approved account must reach the app"

    # 5. Revocation stops them again.
    repository.set_status(
        email=_USER, status=AccessStatus.REVOKED, tier=None, actor=_ADMIN,
        now="2026-06-03T10:00:00+00:00", expected_status=AccessStatus.ACTIVE,
    )
    at = _run_app(monkeypatch, email=_USER)
    assert not _reached_the_app(at)
    assert "Access ended" in _text(at)

    # 6. The whole sequence is answerable from the audit trail.
    events = repository.list_events(_USER)
    assert [(e.from_status, e.to_status) for e in events] == [
        (None, AccessStatus.REQUESTED),
        (AccessStatus.REQUESTED, AccessStatus.ACTIVE),
        (AccessStatus.ACTIVE, AccessStatus.REVOKED),
    ]
    assert events[1].actor == _ADMIN
