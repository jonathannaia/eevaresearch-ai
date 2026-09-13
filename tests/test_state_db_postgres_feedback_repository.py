"""Open-beta feedback (design/DECISIONS.md) — the isolated Postgres
counterpart to test_state_db_feedback_repository.py, against the real
local disposable Postgres test container. Uses pg_conn (an isolated,
already-migrated schema) — see tests/_postgres_test_support.py. Skips
cleanly (never fails) when no local disposable Postgres instance is
reachable."""
from __future__ import annotations

from src.data_access.postgres_state_db.feedback_repository import get_submission, list_submissions, submit_feedback
from src.models.feedback_submission import FeedbackPrimaryInterest, FeedbackRole, FeedbackTrackingWorkflow

from tests._postgres_test_support import pg_conn, pg_isolated_connection  # noqa: F401


def test_get_submission_returns_none_when_absent(pg_conn):
    assert get_submission(pg_conn, "nobody@example.test") is None


def test_first_submission_creates_one_record(pg_conn):
    submit_feedback(
        pg_conn, "Founder@Example.Test", "Ada", FeedbackRole.INDIVIDUAL_INVESTOR,
        FeedbackTrackingWorkflow.NEWS_ALERTS, None, FeedbackPrimaryInterest.DAILY_NEWS,
        "Weekly summary email", "2026-01-01T00:00:00+00:00",
    )

    submission = get_submission(pg_conn, "founder@example.test")
    assert submission is not None
    assert submission.email == "founder@example.test"
    assert submission.display_name == "Ada"
    assert submission.role is FeedbackRole.INDIVIDUAL_INVESTOR
    assert submission.tracking_workflow is FeedbackTrackingWorkflow.NEWS_ALERTS
    assert submission.tracking_workflow_other is None
    assert submission.primary_interest is FeedbackPrimaryInterest.DAILY_NEWS
    assert submission.weekly_value_feedback == "Weekly summary email"


def test_other_workflow_stores_the_detail(pg_conn):
    submit_feedback(
        pg_conn, "founder@example.test", "Ada", FeedbackRole.OTHER, FeedbackTrackingWorkflow.OTHER,
        "A custom spreadsheet", FeedbackPrimaryInterest.US_FILINGS_EDGAR, None, "2026-01-01T00:00:00+00:00",
    )
    submission = get_submission(pg_conn, "founder@example.test")
    assert submission.tracking_workflow is FeedbackTrackingWorkflow.OTHER
    assert submission.tracking_workflow_other == "A custom spreadsheet"


def test_resubmission_replaces_the_prior_row_rather_than_creating_a_second(pg_conn):
    submit_feedback(
        pg_conn, "founder@example.test", "Ada", FeedbackRole.INDIVIDUAL_INVESTOR,
        FeedbackTrackingWorkflow.NEWS_ALERTS, None, FeedbackPrimaryInterest.DAILY_NEWS,
        "First answer", "2026-01-01T00:00:00+00:00",
    )
    submit_feedback(
        pg_conn, "founder@example.test", "Ada", FeedbackRole.PROFESSIONAL_INVESTOR_OR_ANALYST,
        FeedbackTrackingWorkflow.PAID_TERMINAL, None, FeedbackPrimaryInterest.KOREA_FILINGS_DART,
        "Updated answer", "2026-01-02T00:00:00+00:00",
    )

    submission = get_submission(pg_conn, "founder@example.test")
    assert submission.role is FeedbackRole.PROFESSIONAL_INVESTOR_OR_ANALYST
    assert submission.tracking_workflow is FeedbackTrackingWorkflow.PAID_TERMINAL
    assert submission.weekly_value_feedback == "Updated answer"
    assert len(list_submissions(pg_conn)) == 1


def test_email_normalization_trims_and_lowercases_to_one_row(pg_conn):
    submit_feedback(
        pg_conn, "  Founder@Example.Test  ", None, FeedbackRole.OTHER, FeedbackTrackingWorkflow.DOES_NOT_CURRENTLY_TRACK,
        None, FeedbackPrimaryInterest.DAILY_NEWS, None, "2026-01-01T00:00:00+00:00",
    )
    submit_feedback(
        pg_conn, "founder@example.test", None, FeedbackRole.OTHER, FeedbackTrackingWorkflow.DOES_NOT_CURRENTLY_TRACK,
        None, FeedbackPrimaryInterest.DAILY_NEWS, None, "2026-01-02T00:00:00+00:00",
    )
    assert get_submission(pg_conn, "founder@example.test") is not None
    assert len(list_submissions(pg_conn)) == 1


def test_list_submissions_orders_most_recently_submitted_first(pg_conn):
    submit_feedback(
        pg_conn, "first@example.test", None, FeedbackRole.OTHER, FeedbackTrackingWorkflow.DOES_NOT_CURRENTLY_TRACK,
        None, FeedbackPrimaryInterest.DAILY_NEWS, None, "2026-01-01T00:00:00+00:00",
    )
    submit_feedback(
        pg_conn, "second@example.test", None, FeedbackRole.OTHER, FeedbackTrackingWorkflow.DOES_NOT_CURRENTLY_TRACK,
        None, FeedbackPrimaryInterest.DAILY_NEWS, None, "2026-01-03T00:00:00+00:00",
    )
    assert [s.email for s in list_submissions(pg_conn)] == ["second@example.test", "first@example.test"]


def test_list_submissions_empty_store_returns_empty_list(pg_conn):
    assert list_submissions(pg_conn) == []
