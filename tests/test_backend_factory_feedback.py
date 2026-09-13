"""Open-beta feedback (design/DECISIONS.md) — focused tests for
backend_factory.py's FeedbackRepositoryProtocol seam
(get_feedback_repository), covering the JSON backend directly (the
SQLite/Postgres branches are covered by
tests/test_state_db_feedback_repository.py and
tests/test_state_db_postgres_feedback_repository.py, which exercise the
same underlying module functions this seam delegates to) plus the
backend-selection behavior itself. Mirrors
tests/test_backend_factory_user_account.py."""
from __future__ import annotations

from src.config.settings import Settings
from src.data_access import backend_factory
from src.models.feedback_submission import FeedbackPrimaryInterest, FeedbackRole, FeedbackTrackingWorkflow


def test_json_backend_is_the_default_selection(tmp_path):
    repo = backend_factory.get_feedback_repository(Settings(cache_dir=tmp_path))
    assert isinstance(repo, backend_factory.JsonFeedbackRepository)


def test_json_repository_submit_and_get_round_trip(tmp_path):
    repo = backend_factory.JsonFeedbackRepository(cache_dir=tmp_path)
    repo.submit_feedback(
        "Founder@Example.Test", "Ada", FeedbackRole.INDIVIDUAL_INVESTOR, FeedbackTrackingWorkflow.NEWS_ALERTS,
        None, FeedbackPrimaryInterest.DAILY_NEWS, "Weekly summary email", "2026-01-01T00:00:00+00:00",
    )

    submission = repo.get_submission("founder@example.test")
    assert submission.email == "founder@example.test"
    assert submission.display_name == "Ada"
    assert submission.role is FeedbackRole.INDIVIDUAL_INVESTOR
    assert submission.tracking_workflow is FeedbackTrackingWorkflow.NEWS_ALERTS
    assert submission.weekly_value_feedback == "Weekly summary email"


def test_json_repository_other_workflow_round_trips_the_detail(tmp_path):
    repo = backend_factory.JsonFeedbackRepository(cache_dir=tmp_path)
    repo.submit_feedback(
        "founder@example.test", None, FeedbackRole.OTHER, FeedbackTrackingWorkflow.OTHER, "A custom spreadsheet",
        FeedbackPrimaryInterest.US_FILINGS_EDGAR, None, "2026-01-01T00:00:00+00:00",
    )
    submission = repo.get_submission("founder@example.test")
    assert submission.tracking_workflow is FeedbackTrackingWorkflow.OTHER
    assert submission.tracking_workflow_other == "A custom spreadsheet"


def test_json_repository_resubmission_replaces_rather_than_duplicates(tmp_path):
    repo = backend_factory.JsonFeedbackRepository(cache_dir=tmp_path)
    repo.submit_feedback(
        "founder@example.test", "Ada", FeedbackRole.INDIVIDUAL_INVESTOR, FeedbackTrackingWorkflow.NEWS_ALERTS,
        None, FeedbackPrimaryInterest.DAILY_NEWS, "First answer", "2026-01-01T00:00:00+00:00",
    )
    repo.submit_feedback(
        "founder@example.test", "Ada", FeedbackRole.PROFESSIONAL_INVESTOR_OR_ANALYST, FeedbackTrackingWorkflow.PAID_TERMINAL,
        None, FeedbackPrimaryInterest.KOREA_FILINGS_DART, "Updated answer", "2026-01-02T00:00:00+00:00",
    )

    submission = repo.get_submission("founder@example.test")
    assert submission.role is FeedbackRole.PROFESSIONAL_INVESTOR_OR_ANALYST
    assert submission.weekly_value_feedback == "Updated answer"
    assert len(repo.list_submissions()) == 1


def test_json_repository_persists_to_disk_across_instances(tmp_path):
    backend_factory.JsonFeedbackRepository(cache_dir=tmp_path).submit_feedback(
        "founder@example.test", "Ada", FeedbackRole.INDIVIDUAL_INVESTOR, FeedbackTrackingWorkflow.NEWS_ALERTS,
        None, FeedbackPrimaryInterest.DAILY_NEWS, None, "2026-01-01T00:00:00+00:00",
    )
    reloaded = backend_factory.JsonFeedbackRepository(cache_dir=tmp_path)
    assert reloaded.get_submission("founder@example.test") is not None
    assert (tmp_path / "feedback_submissions.json").exists()


def test_json_repository_list_submissions_orders_most_recent_first(tmp_path):
    repo = backend_factory.JsonFeedbackRepository(cache_dir=tmp_path)
    repo.submit_feedback(
        "first@example.test", "Ada", FeedbackRole.OTHER, FeedbackTrackingWorkflow.DOES_NOT_CURRENTLY_TRACK,
        None, FeedbackPrimaryInterest.DAILY_NEWS, None, "2026-01-01T00:00:00+00:00",
    )
    repo.submit_feedback(
        "second@example.test", "Bob", FeedbackRole.OTHER, FeedbackTrackingWorkflow.DOES_NOT_CURRENTLY_TRACK,
        None, FeedbackPrimaryInterest.DAILY_NEWS, None, "2026-01-03T00:00:00+00:00",
    )
    assert [s.email for s in repo.list_submissions()] == ["second@example.test", "first@example.test"]


def test_json_repository_empty_store_returns_empty_list(tmp_path):
    repo = backend_factory.JsonFeedbackRepository(cache_dir=tmp_path)
    assert repo.list_submissions() == []
    assert repo.get_submission("nobody@example.test") is None


def test_sqlite_backend_selection_returns_sqlite_repository(tmp_path):
    settings = Settings(db_backend="sqlite", state_db_path=tmp_path / "state.db")
    repo = backend_factory.get_feedback_repository(settings)
    assert isinstance(repo, backend_factory.SqliteFeedbackRepository)
