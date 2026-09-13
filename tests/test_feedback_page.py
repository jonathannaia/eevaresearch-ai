"""Open-beta feedback (design/DECISIONS.md) — src/ui/pages/feedback.py.
Two layers tested separately:

  - `_validate_submission()` is a pure function (no Streamlit, no I/O) —
    tested directly for every validation rule, the conditional Other
    requirement, and length limits, without driving the page through
    AppTest.
  - render() itself, via the per-page AppTest harness
    (tests/apptest_pages/feedback_page.py), the same isolation
    convention test_admin_users_page.py already uses —
    `backend_factory.get_feedback_repository` is patched where
    feedback.py imports/uses it, not where it's defined.

`st.user` is simulated the same way tests/test_app_auth_gate.py and
tests/test_theme_workspace_page.py already do: monkeypatching the
private streamlit.user_info._get_user_info seam every st.user access
funnels through (AppTest has no public API for a logged-in st.user)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest

from src.data_access import backend_factory
from src.models.feedback_submission import (
    MAX_TRACKING_WORKFLOW_OTHER_LENGTH,
    MAX_WEEKLY_VALUE_FEEDBACK_LENGTH,
    FeedbackPrimaryInterest,
    FeedbackRole,
    FeedbackTrackingWorkflow,
)
from src.ui.pages.feedback import _validate_submission

_HARNESS = Path(__file__).parent / "apptest_pages" / "feedback_page.py"
_APP_PATH = Path(__file__).parent.parent / "app.py"


# ============================================================
# Part A — _validate_submission() is pure; tested directly
# ============================================================


def test_missing_email_is_rejected_with_the_generic_message():
    submission, error = _validate_submission(
        "", "Ada", FeedbackRole.INDIVIDUAL_INVESTOR.value, FeedbackTrackingWorkflow.NEWS_ALERTS.value,
        "", FeedbackPrimaryInterest.DAILY_NEWS.value, "", "2026-01-01T00:00:00+00:00",
    )
    assert submission is None
    assert error == "Could not save your feedback. Please try again later."


def test_invalid_enum_value_is_rejected_with_the_generic_message():
    submission, error = _validate_submission(
        "founder@example.test", "Ada", "Not a real role", FeedbackTrackingWorkflow.NEWS_ALERTS.value,
        "", FeedbackPrimaryInterest.DAILY_NEWS.value, "", "2026-01-01T00:00:00+00:00",
    )
    assert submission is None
    assert error == "Could not save your feedback. Please try again later."


def test_other_workflow_with_blank_detail_is_rejected():
    submission, error = _validate_submission(
        "founder@example.test", "Ada", FeedbackRole.OTHER.value, FeedbackTrackingWorkflow.OTHER.value,
        "   ", FeedbackPrimaryInterest.DAILY_NEWS.value, "", "2026-01-01T00:00:00+00:00",
    )
    assert submission is None
    assert error == "Please tell us what you use."


def test_other_workflow_detail_over_the_length_limit_is_rejected():
    too_long = "x" * (MAX_TRACKING_WORKFLOW_OTHER_LENGTH + 1)
    submission, error = _validate_submission(
        "founder@example.test", "Ada", FeedbackRole.OTHER.value, FeedbackTrackingWorkflow.OTHER.value,
        too_long, FeedbackPrimaryInterest.DAILY_NEWS.value, "", "2026-01-01T00:00:00+00:00",
    )
    assert submission is None
    assert error == f"Please keep that under {MAX_TRACKING_WORKFLOW_OTHER_LENGTH} characters."


def test_other_workflow_detail_at_exactly_the_length_limit_is_accepted():
    exactly_at_limit = "x" * MAX_TRACKING_WORKFLOW_OTHER_LENGTH
    submission, error = _validate_submission(
        "founder@example.test", "Ada", FeedbackRole.OTHER.value, FeedbackTrackingWorkflow.OTHER.value,
        exactly_at_limit, FeedbackPrimaryInterest.DAILY_NEWS.value, "", "2026-01-01T00:00:00+00:00",
    )
    assert error is None
    assert submission.tracking_workflow_other == exactly_at_limit


def test_non_other_workflow_with_a_nonempty_other_detail_discards_it_rather_than_storing_stale_data():
    """A stray/stale 'Other' detail must never be persisted alongside a
    non-Other workflow, regardless of what the caller passes."""
    submission, error = _validate_submission(
        "founder@example.test", "Ada", FeedbackRole.INDIVIDUAL_INVESTOR.value, FeedbackTrackingWorkflow.NEWS_ALERTS.value,
        "leftover stale text", FeedbackPrimaryInterest.DAILY_NEWS.value, "", "2026-01-01T00:00:00+00:00",
    )
    assert error is None
    assert submission.tracking_workflow is FeedbackTrackingWorkflow.NEWS_ALERTS
    assert submission.tracking_workflow_other is None


def test_weekly_value_feedback_over_the_length_limit_is_rejected():
    too_long = "x" * (MAX_WEEKLY_VALUE_FEEDBACK_LENGTH + 1)
    submission, error = _validate_submission(
        "founder@example.test", "Ada", FeedbackRole.INDIVIDUAL_INVESTOR.value, FeedbackTrackingWorkflow.NEWS_ALERTS.value,
        "", FeedbackPrimaryInterest.DAILY_NEWS.value, too_long, "2026-01-01T00:00:00+00:00",
    )
    assert submission is None
    assert error == f"Please keep that under {MAX_WEEKLY_VALUE_FEEDBACK_LENGTH} characters."


def test_blank_weekly_value_feedback_is_stored_as_none():
    submission, error = _validate_submission(
        "founder@example.test", "Ada", FeedbackRole.INDIVIDUAL_INVESTOR.value, FeedbackTrackingWorkflow.NEWS_ALERTS.value,
        "", FeedbackPrimaryInterest.DAILY_NEWS.value, "   ", "2026-01-01T00:00:00+00:00",
    )
    assert error is None
    assert submission.weekly_value_feedback is None


def test_free_text_fields_are_trimmed_before_storage():
    submission, error = _validate_submission(
        "founder@example.test", "Ada", FeedbackRole.OTHER.value, FeedbackTrackingWorkflow.OTHER.value,
        "  A spreadsheet  ", FeedbackPrimaryInterest.DAILY_NEWS.value, "  more context please  ",
        "2026-01-01T00:00:00+00:00",
    )
    assert error is None
    assert submission.tracking_workflow_other == "A spreadsheet"
    assert submission.weekly_value_feedback == "more context please"


def test_valid_submission_returns_a_populated_submission_with_the_given_email_verbatim():
    submission, error = _validate_submission(
        "founder@example.test", "Ada Lovelace", FeedbackRole.RESEARCHER_OR_ACADEMIC.value,
        FeedbackTrackingWorkflow.PAID_TERMINAL.value, "", FeedbackPrimaryInterest.JAPAN_FILINGS_EDINET.value,
        "More Japan coverage", "2026-01-01T00:00:00+00:00",
    )
    assert error is None
    assert submission.email == "founder@example.test"
    assert submission.display_name == "Ada Lovelace"
    assert submission.role is FeedbackRole.RESEARCHER_OR_ACADEMIC
    assert submission.tracking_workflow is FeedbackTrackingWorkflow.PAID_TERMINAL
    assert submission.primary_interest is FeedbackPrimaryInterest.JAPAN_FILINGS_EDINET
    assert submission.weekly_value_feedback == "More Japan coverage"
    assert submission.submitted_at == "2026-01-01T00:00:00+00:00"


# ============================================================
# Part B — render(), via AppTest
# ============================================================


def _sign_in_as(monkeypatch, email: str = "founder@example.test", name: str = "Ada Lovelace") -> None:
    import streamlit.user_info as user_info_module

    monkeypatch.setattr(
        user_info_module, "_get_user_info", lambda: {"is_logged_in": True, "email": email, "name": name},
    )


def _patch_repo_construction(cache_dir) -> MagicMock:
    real_repo = backend_factory.JsonFeedbackRepository(cache_dir=cache_dir)
    return MagicMock(side_effect=lambda settings: real_repo)


def _main_button(at: AppTest):
    """The page's own submit button only — at.button also includes the
    sidebar's command-palette trigger and "Sign out" button, neither of
    which this page's own tests are about."""
    buttons = list(at.main.get("button"))
    assert len(buttons) == 1, [b.label for b in buttons]
    return buttons[0]


def _main_text(at: AppTest) -> str:
    return (
        " ".join(m.value for m in at.main.get("markdown") if not m.value.startswith("<style>"))
        + " ".join(c.value for c in at.main.get("caption"))
        + " ".join(e.value for e in at.main.get("error"))
        + " ".join(i.value for i in at.main.get("info"))
        + " ".join(s.value for s in at.main.get("success"))
    )


def test_first_time_visitor_sees_send_feedback_button_and_no_other_field_by_default(monkeypatch, tmp_path):
    _sign_in_as(monkeypatch)
    construct = _patch_repo_construction(tmp_path)
    with patch("src.ui.pages.feedback.backend_factory.get_feedback_repository", construct):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    assert _main_button(at).label == "Send feedback"
    assert len(at.text_input) == 0  # no "What do you use?" field until Other is selected


def test_selecting_other_workflow_reveals_the_required_text_field(monkeypatch, tmp_path):
    _sign_in_as(monkeypatch)
    construct = _patch_repo_construction(tmp_path)
    with patch("src.ui.pages.feedback.backend_factory.get_feedback_repository", construct):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        workflow_box = [s for s in at.selectbox if s.label == "How do you currently track company filings and news?"][0]
        workflow_box.select(FeedbackTrackingWorkflow.OTHER.value).run()

    assert not at.exception
    assert [t.label for t in at.text_input] == ["What do you use?"]


def test_submitting_other_workflow_with_blank_detail_shows_the_specific_error_and_does_not_write(monkeypatch, tmp_path):
    _sign_in_as(monkeypatch)
    construct = _patch_repo_construction(tmp_path)
    with patch("src.ui.pages.feedback.backend_factory.get_feedback_repository", construct):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        [s for s in at.selectbox if s.label == "How do you currently track company filings and news?"][0].select(
            FeedbackTrackingWorkflow.OTHER.value
        ).run()
        _main_button(at).click().run()

    assert not at.exception
    assert "Please tell us what you use." in _main_text(at)
    assert backend_factory.JsonFeedbackRepository(cache_dir=tmp_path).get_submission("founder@example.test") is None


def test_first_submission_uses_the_signed_in_identity_and_shows_the_recorded_message(monkeypatch, tmp_path):
    _sign_in_as(monkeypatch, email="Founder@Example.Test", name="Ada Lovelace")
    construct = _patch_repo_construction(tmp_path)
    with patch("src.ui.pages.feedback.backend_factory.get_feedback_repository", construct):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        _main_button(at).click().run()

    assert not at.exception
    assert "Thanks — this has been recorded." in _main_text(at)

    # The email used is the signed-in identity, normalized — never a
    # widget the page never even renders.
    submission = backend_factory.JsonFeedbackRepository(cache_dir=tmp_path).get_submission("founder@example.test")
    assert submission is not None
    assert submission.email == "founder@example.test"
    assert submission.display_name == "Ada Lovelace"


def test_returning_user_sees_prefilled_fields_and_update_button(monkeypatch, tmp_path):
    backend_factory.JsonFeedbackRepository(cache_dir=tmp_path).submit_feedback(
        "founder@example.test", "Ada", FeedbackRole.PROFESSIONAL_INVESTOR_OR_ANALYST, FeedbackTrackingWorkflow.PAID_TERMINAL,
        None, FeedbackPrimaryInterest.KOREA_FILINGS_DART, "Prior answer", "2026-01-01T00:00:00+00:00",
    )
    _sign_in_as(monkeypatch)
    construct = _patch_repo_construction(tmp_path)
    with patch("src.ui.pages.feedback.backend_factory.get_feedback_repository", construct):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    assert _main_button(at).label == "Update feedback"
    role_box = [s for s in at.selectbox if s.label == "What best describes you?"][0]
    assert role_box.value == FeedbackRole.PROFESSIONAL_INVESTOR_OR_ANALYST.value
    text_area = [t for t in at.text_area if "worth checking every week" in t.label][0]
    assert text_area.value == "Prior answer"


def test_resubmission_replaces_the_single_row_and_shows_the_updated_message(monkeypatch, tmp_path):
    backend_factory.JsonFeedbackRepository(cache_dir=tmp_path).submit_feedback(
        "founder@example.test", "Ada", FeedbackRole.PROFESSIONAL_INVESTOR_OR_ANALYST, FeedbackTrackingWorkflow.PAID_TERMINAL,
        None, FeedbackPrimaryInterest.KOREA_FILINGS_DART, "Prior answer", "2026-01-01T00:00:00+00:00",
    )
    _sign_in_as(monkeypatch)
    construct = _patch_repo_construction(tmp_path)
    with patch("src.ui.pages.feedback.backend_factory.get_feedback_repository", construct):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        _main_button(at).click().run()

    assert not at.exception
    assert "Thanks — your feedback has been updated." in _main_text(at)
    repo = backend_factory.JsonFeedbackRepository(cache_dir=tmp_path)
    assert len(repo.list_submissions()) == 1  # replaced, not duplicated


def test_repository_failure_shows_the_generic_message_with_no_leaked_detail(monkeypatch):
    _sign_in_as(monkeypatch)

    def _boom(settings):
        raise RuntimeError("connection refused to postgres://real-host:5432/real-db with password hunter2")

    with patch("src.ui.pages.feedback.backend_factory.get_feedback_repository", _boom):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        _main_button(at).click().run()

    assert not at.exception
    all_text = _main_text(at)
    assert "Could not save your feedback. Please try again later." in all_text
    for leaked in ("RuntimeError", "hunter2", "postgres://", "real-host", "5432", "real-db"):
        assert leaked not in all_text


# ============================================================
# Part C — the feedback route stays behind the global gates
# ============================================================


def _run_app(monkeypatch, *, logged_in: bool, email: str | None = None, allowed_emails: str | None = None):
    import streamlit.user_info as user_info_module

    if allowed_emails is None:
        monkeypatch.delenv("EDGE_PRIVATE_BETA_ALLOWED_EMAILS", raising=False)
    else:
        monkeypatch.setenv("EDGE_PRIVATE_BETA_ALLOWED_EMAILS", allowed_emails)

    if not logged_in:
        monkeypatch.setattr(user_info_module, "_get_user_info", lambda: {"is_logged_in": False})
    else:
        monkeypatch.setattr(
            user_info_module, "_get_user_info", lambda: {"is_logged_in": True, "email": email},
        )

    at = AppTest.from_file(str(_APP_PATH), default_timeout=15)
    at.run()
    return at


def test_global_beta_gate_still_covers_the_feedback_route(monkeypatch):
    """Mirrors tests/test_theme_workspace_page.py's and
    tests/test_research_cases_page.py's own
    test_global_beta_gate_still_covers_the_route: proves the feedback
    page doesn't bypass the (now-optional, secondary) allowlist gate —
    an excluded signed-in user sees only the denial screen, never
    feedback.py's own heading. The anonymous-visitor case is covered
    generically (for every page, including this one) by
    tests/test_app_auth_gate.py."""
    at = _run_app(monkeypatch, logged_in=True, email="stranger@example.test", allowed_emails="founder@example.test")

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown) + " ".join(t.value for t in at.title)
    assert "Access restricted" in all_text
    assert "Help us build this right" not in all_text
