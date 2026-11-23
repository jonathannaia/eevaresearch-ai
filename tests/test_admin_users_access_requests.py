"""Paid professional access, phase 1 — the admin page's access-request
queue and direct-grant form (src/ui/pages/admin_users.py).

Driven through the same AppTest harness as tests/test_admin_users_page.py
— see that file for why the harness calls render() directly: it is
exactly the callable app.py's st.Page wraps, so a test that reaches it
is simulating a direct/deep-link route, not a nav click.

The first test is the one that matters most. This page is the only place
in the product where access is granted or removed, so a non-admin
reaching it must not merely be shown a denial — they must never cause an
entitlement to be read at all."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from streamlit.testing.v1 import AppTest

from src.data_access import backend_factory
from src.models.account_access import AccessRequest, AccessStatus, AccessTier

_HARNESS = Path(__file__).parent / "apptest_pages" / "admin_users_page.py"
_ADMIN = "owner@example.test"
_USER = "analyst@example.test"
_NOW = "2026-06-01T10:00:00+00:00"


@pytest.fixture
def repository(tmp_path):
    return backend_factory.JsonAccountEntitlementRepository(cache_dir=tmp_path)


def _run(tmp_path, repository, *, is_admin: bool, signed_in_as: str = _ADMIN):
    """Runs the page with real JSON-backed repositories, so a button
    click performs a real write that the test then reads back."""
    import streamlit.user_info as user_info_module

    entitlement_spy = MagicMock(side_effect=lambda settings: repository)
    with patch("src.ui.pages.admin_users.is_admin", return_value=is_admin), \
         patch.object(user_info_module, "_get_user_info",
                      lambda: {"is_logged_in": True, "email": signed_in_as}), \
         patch("src.ui.pages.admin_users.backend_factory.get_user_account_repository",
               MagicMock(side_effect=lambda settings: backend_factory.JsonUserAccountRepository(cache_dir=tmp_path))), \
         patch("src.ui.pages.admin_users.backend_factory.get_feedback_repository",
               MagicMock(side_effect=lambda settings: backend_factory.JsonFeedbackRepository(cache_dir=tmp_path))), \
         patch("src.ui.pages.admin_users.backend_factory.get_account_entitlement_repository",
               entitlement_spy):
        at = AppTest.from_file(str(_HARNESS), default_timeout=20)
        at.run()
    return at, entitlement_spy


def _clickable(at: AppTest, label: str):
    """A form submit button is reported under "button" on some Streamlit
    versions and under "form_submit_button" on others — the same
    version sensitivity that broke six tests in this suite when
    requirements.txt allowed a newer Streamlit. Look in both rather
    than pinning the dependency to a test's convenience."""
    for kind in ("button", "form_submit_button"):
        for widget in at.main.get(kind):
            if widget.label == label:
                return widget
    raise AssertionError(
        f"no clickable labelled {label!r}; found "
        f"{[w.label for w in at.main.get('button')] + [w.label for w in at.main.get('form_submit_button')]}"
    )


def _text_input(at: AppTest, label: str):
    """By label, never by position: the page has three text inputs and
    positional indexing silently targets the wrong one as soon as a
    field is added."""
    for widget in at.main.get("text_input"):
        if widget.label == label:
            return widget
    raise AssertionError(f"no text input labelled {label!r}")


def _main_text(at: AppTest) -> str:
    chunks = [m.value for m in at.main.get("markdown") if not m.value.startswith("<style>")]
    for kind in ("caption", "error", "info", "warning", "success"):
        chunks.extend(element.value for element in at.main.get(kind))
    return " ".join(chunks)


# --- the authorization boundary -------------------------------------------

def test_a_non_admin_never_causes_an_entitlement_to_be_read(tmp_path, repository):
    """Not "sees a denial" — never constructs the repository. This page
    is the only place access is granted or removed, so the boundary has
    to sit before any read, exactly as it already does for the user
    list and the feedback list."""
    repository.request_access(_USER, AccessRequest(reason="Evaluating."), _NOW)

    at, spy = _run(tmp_path, repository, is_admin=False, signed_in_as=_USER)

    assert not at.exception
    assert "Access denied." in _main_text(at)
    spy.assert_not_called()
    assert _USER not in _main_text(at), "no requester's identity leaks to a non-admin"


# --- the queue ------------------------------------------------------------

def test_a_pending_request_is_shown_with_what_the_requester_typed(tmp_path, repository):
    repository.request_access(
        _USER,
        AccessRequest(reason="Tracking Korean memory capex.", organisation="Example Capital", role="analyst"),
        _NOW,
    )
    at, _ = _run(tmp_path, repository, is_admin=True)

    assert not at.exception
    text = _main_text(at)
    assert _USER in text
    assert "Tracking Korean memory capex." in text
    assert "Example Capital" in text and "analyst" in text


def test_an_empty_queue_says_so_rather_than_rendering_nothing(tmp_path, repository):
    at, _ = _run(tmp_path, repository, is_admin=True)
    assert not at.exception
    assert "No pending access requests." in _main_text(at)


def test_a_decided_request_leaves_the_queue(tmp_path, repository):
    repository.request_access(_USER, AccessRequest(reason="Evaluating."), _NOW)
    repository.set_status(
        email=_USER, status=AccessStatus.ACTIVE, tier=AccessTier.PAID, actor=_ADMIN, now=_NOW
    )
    at, _ = _run(tmp_path, repository, is_admin=True)

    assert not at.exception
    assert "No pending access requests." in _main_text(at)


# --- approving ------------------------------------------------------------

def test_approving_a_request_writes_an_active_trial_with_an_expiry(tmp_path, repository, monkeypatch):
    monkeypatch.setenv("EDGE_ACCESS_TRIAL_DAYS", "21")
    repository.request_access(_USER, AccessRequest(reason="Evaluating."), _NOW)

    import streamlit.user_info as user_info_module
    entitlement_spy = MagicMock(side_effect=lambda settings: repository)
    with patch("src.ui.pages.admin_users.is_admin", return_value=True), \
         patch.object(user_info_module, "_get_user_info",
                      lambda: {"is_logged_in": True, "email": _ADMIN}), \
         patch("src.ui.pages.admin_users.backend_factory.get_user_account_repository",
               MagicMock(side_effect=lambda s: backend_factory.JsonUserAccountRepository(cache_dir=tmp_path))), \
         patch("src.ui.pages.admin_users.backend_factory.get_feedback_repository",
               MagicMock(side_effect=lambda s: backend_factory.JsonFeedbackRepository(cache_dir=tmp_path))), \
         patch("src.ui.pages.admin_users.backend_factory.get_account_entitlement_repository",
               entitlement_spy):
        at = AppTest.from_file(str(_HARNESS), default_timeout=20)
        at.run()
        _clickable(at, "Approve").click().run()

    entitlement = repository.get_entitlement(_USER)
    assert entitlement.status is AccessStatus.ACTIVE
    assert entitlement.tier is AccessTier.TRIAL, "the selectbox default is a trial, not paid"
    assert entitlement.expires_at is not None, "a trial must expire"
    assert entitlement.decided_by == _ADMIN, "the actor is the signed-in admin, never a form value"

    events = repository.list_events(_USER)
    assert events[-1].to_status is AccessStatus.ACTIVE
    assert events[-1].actor == _ADMIN


def test_denying_a_request_records_a_denial_with_no_tier(tmp_path, repository):
    repository.request_access(_USER, AccessRequest(reason="Evaluating."), _NOW)

    import streamlit.user_info as user_info_module
    with patch("src.ui.pages.admin_users.is_admin", return_value=True), \
         patch.object(user_info_module, "_get_user_info",
                      lambda: {"is_logged_in": True, "email": _ADMIN}), \
         patch("src.ui.pages.admin_users.backend_factory.get_user_account_repository",
               MagicMock(side_effect=lambda s: backend_factory.JsonUserAccountRepository(cache_dir=tmp_path))), \
         patch("src.ui.pages.admin_users.backend_factory.get_feedback_repository",
               MagicMock(side_effect=lambda s: backend_factory.JsonFeedbackRepository(cache_dir=tmp_path))), \
         patch("src.ui.pages.admin_users.backend_factory.get_account_entitlement_repository",
               MagicMock(side_effect=lambda s: repository)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=20)
        at.run()
        _clickable(at, "Deny").click().run()

    entitlement = repository.get_entitlement(_USER)
    assert entitlement.status is AccessStatus.DENIED and entitlement.tier is None


# --- the direct grant, i.e. the backfill path ----------------------------

def test_the_direct_grant_form_is_present_for_the_backfill_step(tmp_path, repository):
    """The rollout is migrate, backfill, verify, enable. Backfilling
    existing accounts needs a way to grant access to somebody who never
    asked — without it, the one step that decides whether everybody
    keeps working would depend on running SQL by hand against
    production."""
    at, _ = _run(tmp_path, repository, is_admin=True)

    assert not at.exception
    assert _clickable(at, "Grant access") is not None


def test_a_direct_grant_creates_an_active_row_with_no_request_attached(tmp_path, repository):
    """No request attached is the point: it keeps a direct grant
    distinguishable from an approval in the audit trail."""
    import streamlit.user_info as user_info_module
    with patch("src.ui.pages.admin_users.is_admin", return_value=True), \
         patch.object(user_info_module, "_get_user_info",
                      lambda: {"is_logged_in": True, "email": _ADMIN}), \
         patch("src.ui.pages.admin_users.backend_factory.get_user_account_repository",
               MagicMock(side_effect=lambda s: backend_factory.JsonUserAccountRepository(cache_dir=tmp_path))), \
         patch("src.ui.pages.admin_users.backend_factory.get_feedback_repository",
               MagicMock(side_effect=lambda s: backend_factory.JsonFeedbackRepository(cache_dir=tmp_path))), \
         patch("src.ui.pages.admin_users.backend_factory.get_account_entitlement_repository",
               MagicMock(side_effect=lambda s: repository)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=20)
        at.run()
        _text_input(at, "Email").set_value("Backfilled@Example.Test")
        _text_input(at, "Note (recorded in the audit trail)").set_value("existing beta user")
        _clickable(at, "Grant access").click().run()

    entitlement = repository.get_entitlement("backfilled@example.test")
    assert entitlement is not None, "the grant did not write a row"
    assert entitlement.status is AccessStatus.ACTIVE
    assert entitlement.tier is AccessTier.COMPLIMENTARY, "the default tier does not expire"
    assert entitlement.expires_at is None
    assert entitlement.request is None
    assert entitlement.requested_at is None

    events = repository.list_events("backfilled@example.test")
    assert events[-1].detail == "existing beta user"
    assert events[-1].actor == _ADMIN


def test_a_malformed_email_is_rejected_before_a_row_is_written(tmp_path, repository):
    """An obvious typo must not become an entitlement row nobody can
    sign in as — it would look granted and never work."""
    import streamlit.user_info as user_info_module
    with patch("src.ui.pages.admin_users.is_admin", return_value=True), \
         patch.object(user_info_module, "_get_user_info",
                      lambda: {"is_logged_in": True, "email": _ADMIN}), \
         patch("src.ui.pages.admin_users.backend_factory.get_user_account_repository",
               MagicMock(side_effect=lambda s: backend_factory.JsonUserAccountRepository(cache_dir=tmp_path))), \
         patch("src.ui.pages.admin_users.backend_factory.get_feedback_repository",
               MagicMock(side_effect=lambda s: backend_factory.JsonFeedbackRepository(cache_dir=tmp_path))), \
         patch("src.ui.pages.admin_users.backend_factory.get_account_entitlement_repository",
               MagicMock(side_effect=lambda s: repository)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=20)
        at.run()
        _text_input(at, "Email").set_value("not-an-email")
        _clickable(at, "Grant access").click().run()

    assert repository.list_entitlements() == []
    assert "Enter a full email address." in _main_text(at)
