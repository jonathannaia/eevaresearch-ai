"""Open-beta feedback (design/DECISIONS.md) — sanity tests for
src/models/feedback_submission.py's enums and dataclass shape. No
persistence involved here (see tests/test_state_db_feedback_repository.py
and friends for round-trip coverage)."""
from __future__ import annotations

from src.models.feedback_submission import (
    MAX_TRACKING_WORKFLOW_OTHER_LENGTH,
    MAX_WEEKLY_VALUE_FEEDBACK_LENGTH,
    FeedbackPrimaryInterest,
    FeedbackRole,
    FeedbackSubmission,
    FeedbackTrackingWorkflow,
)


def test_role_enum_has_exactly_the_approved_choices():
    assert [choice.value for choice in FeedbackRole] == [
        "Individual investor",
        "Professional investor or analyst",
        "Journalist or media",
        "Researcher or academic",
        "Other",
    ]


def test_tracking_workflow_enum_has_exactly_the_approved_choices_with_other_last():
    assert [choice.value for choice in FeedbackTrackingWorkflow] == [
        "Company investor-relations pages",
        "News alerts",
        "Paid terminal",
        "Another research tool",
        "I do not currently track them",
        "Other",
    ]


def test_primary_interest_enum_has_exactly_the_approved_choices():
    assert [choice.value for choice in FeedbackPrimaryInterest] == [
        "U.S. filings (EDGAR)",
        "Korea filings (DART)",
        "Japan filings (EDINET)",
        "Daily News",
        "Theme and supply-chain research",
    ]


def test_length_constants_are_positive_and_distinct():
    assert MAX_TRACKING_WORKFLOW_OTHER_LENGTH == 200
    assert MAX_WEEKLY_VALUE_FEEDBACK_LENGTH == 1000


def test_submission_is_frozen_and_carries_exactly_the_approved_fields():
    submission = FeedbackSubmission(
        email="founder@example.test", display_name="Ada", submitted_at="2026-01-01T00:00:00+00:00",
        role=FeedbackRole.INDIVIDUAL_INVESTOR, tracking_workflow=FeedbackTrackingWorkflow.NEWS_ALERTS,
        tracking_workflow_other=None, primary_interest=FeedbackPrimaryInterest.DAILY_NEWS,
        weekly_value_feedback=None,
    )
    assert submission.__dataclass_fields__.keys() == {
        "email", "display_name", "submitted_at", "role", "tracking_workflow",
        "tracking_workflow_other", "primary_interest", "weekly_value_feedback",
    }
    try:
        submission.email = "someone-else@example.test"  # type: ignore[misc]
    except Exception as exc:
        assert type(exc).__name__ == "FrozenInstanceError"
    else:
        raise AssertionError("FeedbackSubmission must be frozen")
