"""Admin Users v1 (design/DECISIONS.md) — src/ui/pages/admin_users.py,
via the per-page AppTest harness (tests/apptest_pages/admin_users_page.py),
the same isolation convention test_company_discovery_admin_page.py uses.
`is_admin` and `backend_factory.get_user_account_repository` are patched
where admin_users.py imports/uses them (not where they're defined) — the
standard Python patch-target convention this repo's own admin-page
tests already use. Patching the factory function itself (not
get_repositories()) matches the current implementation: admin_users.py
calls backend_factory.get_user_account_repository(get_settings())
directly, precisely so an admin-page render is the only thing that ever
constructs this repository — never every page's own get_repositories()
call (see src/data_access/container.py's own comment)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest

from src.data_access import backend_factory
from src.models.feedback_submission import FeedbackPrimaryInterest, FeedbackRole, FeedbackTrackingWorkflow

_HARNESS = Path(__file__).parent / "apptest_pages" / "admin_users_page.py"


def _patch_repo_construction(cache_dir) -> MagicMock:
    """A spy that still delegates to a real JsonUserAccountRepository
    pointed at `cache_dir` — so tests get both real read/write behavior
    and a call-count assertion for "was the repository ever
    constructed"."""
    real_repo = backend_factory.JsonUserAccountRepository(cache_dir=cache_dir)
    return MagicMock(side_effect=lambda settings: real_repo)


def _patch_feedback_repo_construction(cache_dir) -> MagicMock:
    """Same shape as _patch_repo_construction above, for the open-beta
    feedback section's own separate repository."""
    real_repo = backend_factory.JsonFeedbackRepository(cache_dir=cache_dir)
    return MagicMock(side_effect=lambda settings: real_repo)


def _main_text(at: AppTest) -> str:
    # at.main (not the top-level at.markdown/at.caption, which also
    # include the sidebar's own brand mark/base64 logo and nav links) —
    # this page's own content area only. with_chrome()'s own load_css()
    # injects a <style> markdown element outside st.sidebar too (same
    # exclusion tests/test_dashboard_data_integrity.py's own _main_text
    # helper already established) — its raw CSS text (@media/@font-face)
    # is noise unrelated to this page's actual content.
    return (
        " ".join(m.value for m in at.main.get("markdown") if not m.value.startswith("<style>"))
        + " ".join(c.value for c in at.main.get("caption"))
        + " ".join(e.value for e in at.main.get("error"))
        + " ".join(i.value for i in at.main.get("info"))
    )


def test_non_admin_sees_only_access_denied_and_repository_is_never_constructed(tmp_path):
    construct = _patch_repo_construction(tmp_path)
    with patch("src.ui.pages.admin_users.is_admin", return_value=False), \
         patch("src.ui.pages.admin_users.backend_factory.get_user_account_repository", construct):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    assert "Access denied." in _main_text(at)
    construct.assert_not_called()


def test_direct_route_access_by_non_admin_is_blocked_the_same_way(tmp_path):
    """Same assertion as the test above, phrased for the direct/deep-link
    requirement explicitly: a non-admin reaching this page's own URL
    (which is exactly what the harness simulates — it drives the page's
    render() function directly, the same callable app.py's admin-users
    st.Page wraps) sees only the denial state, never the user list, and
    never constructs or queries the user-account repository."""
    construct = _patch_repo_construction(tmp_path)
    with patch("src.ui.pages.admin_users.is_admin", return_value=False), \
         patch("src.ui.pages.admin_users.backend_factory.get_user_account_repository", construct):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    assert "@" not in _main_text(at)  # no email ever rendered
    construct.assert_not_called()


def test_admin_render_constructs_and_queries_the_repository_exactly_once(tmp_path):
    construct = _patch_repo_construction(tmp_path)
    with patch("src.ui.pages.admin_users.is_admin", return_value=True), \
         patch("src.ui.pages.admin_users.backend_factory.get_user_account_repository", construct):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    construct.assert_called_once()


def test_admin_sees_empty_state_with_zero_users(tmp_path):
    construct = _patch_repo_construction(tmp_path)
    with patch("src.ui.pages.admin_users.is_admin", return_value=True), \
         patch("src.ui.pages.admin_users.backend_factory.get_user_account_repository", construct):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    assert "No users yet." in _main_text(at)


def test_admin_sees_users_most_recently_seen_first_with_all_fields(tmp_path):
    repo = backend_factory.JsonUserAccountRepository(cache_dir=tmp_path)
    repo.record_sign_in("founder@example.test", "Ada Lovelace", "2026-01-01T00:00:00+00:00")
    repo.record_sign_in("stranger@example.test", "Bob", "2026-01-03T00:00:00+00:00")
    repo.record_sign_in("founder@example.test", "Ada Lovelace", "2026-01-04T00:00:00+00:00")

    construct = _patch_repo_construction(tmp_path)
    with patch("src.ui.pages.admin_users.is_admin", return_value=True), \
         patch("src.ui.pages.admin_users.backend_factory.get_user_account_repository", construct):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = _main_text(at)
    # most-recently-seen first: founder (last_seen 01-04) before stranger (01-03)
    assert all_text.index("Ada Lovelace") < all_text.index("Bob")
    assert "founder@example.test" in all_text
    assert "Sign-ins: 2" in all_text
    assert "First seen: 2026-01-01T00:00:00+00:00" in all_text
    assert "Last seen: 2026-01-04T00:00:00+00:00" in all_text


def test_admin_search_filters_by_email_or_name(tmp_path):
    repo = backend_factory.JsonUserAccountRepository(cache_dir=tmp_path)
    repo.record_sign_in("founder@example.test", "Ada Lovelace", "2026-01-01T00:00:00+00:00")
    repo.record_sign_in("stranger@example.test", "Bob", "2026-01-02T00:00:00+00:00")

    construct = _patch_repo_construction(tmp_path)
    with patch("src.ui.pages.admin_users.is_admin", return_value=True), \
         patch("src.ui.pages.admin_users.backend_factory.get_user_account_repository", construct):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        at.text_input[0].set_value("ada").run()

    assert not at.exception
    all_text = _main_text(at)
    assert "founder@example.test" in all_text
    assert "stranger@example.test" not in all_text


def test_admin_search_with_no_match_shows_safe_empty_state(tmp_path):
    repo = backend_factory.JsonUserAccountRepository(cache_dir=tmp_path)
    repo.record_sign_in("founder@example.test", "Ada Lovelace", "2026-01-01T00:00:00+00:00")

    construct = _patch_repo_construction(tmp_path)
    with patch("src.ui.pages.admin_users.is_admin", return_value=True), \
         patch("src.ui.pages.admin_users.backend_factory.get_user_account_repository", construct):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        at.text_input[0].set_value("nomatch").run()

    assert not at.exception
    all_text = _main_text(at)
    assert "No users match that search." in all_text
    assert "founder@example.test" not in all_text


def test_list_retrieval_failure_shows_fixed_generic_message_with_no_leaked_detail(tmp_path):
    """A failing repository construction/query — e.g. a real Postgres
    connection error whose exception class name and message can embed
    host/port/dbname/user — must never surface anything beyond the one
    fixed, generic sentence."""

    def _boom(settings):
        raise RuntimeError(
            "connection refused to postgres://real-host.internal:5432/real-db with password hunter2"
        )

    with patch("src.ui.pages.admin_users.is_admin", return_value=True), \
         patch("src.ui.pages.admin_users.backend_factory.get_user_account_repository", _boom):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = _main_text(at)
    assert "Could not read the user list. Please try again later." in all_text
    for leaked in ("RuntimeError", "hunter2", "postgres://", "real-host", "5432", "real-db", "connection refused"):
        assert leaked not in all_text


def test_user_repository_failure_with_feedback_repository_succeeding_shows_only_the_user_list_generic_message(tmp_path):
    """The two sections' repository failures are independent: a broken
    user-account backend must not prevent the (separately constructed)
    feedback section from reading and rendering real data."""
    feedback_repo = backend_factory.JsonFeedbackRepository(cache_dir=tmp_path)
    feedback_repo.submit_feedback(
        "founder@example.test", "Ada", FeedbackRole.INDIVIDUAL_INVESTOR, FeedbackTrackingWorkflow.NEWS_ALERTS,
        None, FeedbackPrimaryInterest.DAILY_NEWS, None, "2026-01-01T00:00:00+00:00",
    )
    feedback_construct = _patch_feedback_repo_construction(tmp_path)

    def _boom(settings):
        raise RuntimeError("connection refused to postgres://real-host.internal:5432/real-db with password hunter2")

    with patch("src.ui.pages.admin_users.is_admin", return_value=True), \
         patch("src.ui.pages.admin_users.backend_factory.get_user_account_repository", _boom), \
         patch("src.ui.pages.admin_users.backend_factory.get_feedback_repository", feedback_construct):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = _main_text(at)
    assert "Could not read the user list. Please try again later." in all_text
    assert "founder@example.test" in all_text  # the feedback section still rendered real data
    for leaked in ("RuntimeError", "hunter2", "postgres://", "real-host", "5432", "real-db", "connection refused"):
        assert leaked not in all_text


def test_both_repositories_failing_shows_both_generic_messages_independently(tmp_path):
    def _user_boom(settings):
        raise RuntimeError("connection refused to postgres://user-host.internal:5432/user-db with password hunter1")

    def _feedback_boom(settings):
        raise RuntimeError("connection refused to postgres://feedback-host.internal:5432/feedback-db with password hunter2")

    with patch("src.ui.pages.admin_users.is_admin", return_value=True), \
         patch("src.ui.pages.admin_users.backend_factory.get_user_account_repository", _user_boom), \
         patch("src.ui.pages.admin_users.backend_factory.get_feedback_repository", _feedback_boom):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = _main_text(at)
    assert "Could not read the user list. Please try again later." in all_text
    assert "Could not read feedback submissions. Please try again later." in all_text
    for leaked in (
        "RuntimeError", "hunter1", "hunter2", "postgres://", "user-host", "feedback-host",
        "5432", "user-db", "feedback-db", "connection refused",
    ):
        assert leaked not in all_text


def test_no_secret_token_cookie_or_session_material_is_rendered_to_an_admin(tmp_path):
    repo = backend_factory.JsonUserAccountRepository(cache_dir=tmp_path)
    repo.record_sign_in("founder@example.test", "Ada Lovelace", "2026-01-01T00:00:00+00:00")

    construct = _patch_repo_construction(tmp_path)
    with patch("src.ui.pages.admin_users.is_admin", return_value=True), \
         patch("src.ui.pages.admin_users.backend_factory.get_user_account_repository", construct):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = _main_text(at).lower()
    for forbidden in (
        "cookie_secret", "client_secret", "id_token", "access_token", "authorization: bearer",
        "oauth2callback", "password", "api_key", "database_url",
    ):
        assert forbidden not in all_text


# ============================================================
# Open-beta feedback section (design/DECISIONS.md)
# ============================================================


def test_non_admin_sees_only_access_denied_and_feedback_repository_is_never_constructed(tmp_path):
    """Same is_admin()-first boundary as the user list above — the one
    check at the top of render() guards both sections."""
    user_construct = _patch_repo_construction(tmp_path)
    feedback_construct = _patch_feedback_repo_construction(tmp_path)
    with patch("src.ui.pages.admin_users.is_admin", return_value=False), \
         patch("src.ui.pages.admin_users.backend_factory.get_user_account_repository", user_construct), \
         patch("src.ui.pages.admin_users.backend_factory.get_feedback_repository", feedback_construct):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    assert "Access denied." in _main_text(at)
    assert "Open-beta feedback" not in _main_text(at)
    user_construct.assert_not_called()
    feedback_construct.assert_not_called()


def test_admin_feedback_render_constructs_and_queries_the_repository_exactly_once(tmp_path):
    user_construct = _patch_repo_construction(tmp_path)
    feedback_construct = _patch_feedback_repo_construction(tmp_path)
    with patch("src.ui.pages.admin_users.is_admin", return_value=True), \
         patch("src.ui.pages.admin_users.backend_factory.get_user_account_repository", user_construct), \
         patch("src.ui.pages.admin_users.backend_factory.get_feedback_repository", feedback_construct):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    feedback_construct.assert_called_once()


def test_admin_sees_empty_feedback_state_with_zero_submissions(tmp_path):
    user_construct = _patch_repo_construction(tmp_path)
    feedback_construct = _patch_feedback_repo_construction(tmp_path)
    with patch("src.ui.pages.admin_users.is_admin", return_value=True), \
         patch("src.ui.pages.admin_users.backend_factory.get_user_account_repository", user_construct), \
         patch("src.ui.pages.admin_users.backend_factory.get_feedback_repository", feedback_construct):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    assert "No feedback submitted yet." in _main_text(at)


def test_admin_sees_every_feedback_submission_with_all_fields_in_order(tmp_path):
    feedback_repo = backend_factory.JsonFeedbackRepository(cache_dir=tmp_path)
    feedback_repo.submit_feedback(
        "founder@example.test", "Ada Lovelace", FeedbackRole.OTHER, FeedbackTrackingWorkflow.OTHER,
        "A custom spreadsheet", FeedbackPrimaryInterest.DAILY_NEWS, "Faster Korea coverage",
        "2026-01-01T00:00:00+00:00",
    )
    feedback_repo.submit_feedback(
        "stranger@example.test", "Bob", FeedbackRole.JOURNALIST_OR_MEDIA, FeedbackTrackingWorkflow.NEWS_ALERTS,
        None, FeedbackPrimaryInterest.US_FILINGS_EDGAR, None, "2026-01-03T00:00:00+00:00",
    )

    user_construct = _patch_repo_construction(tmp_path)
    feedback_construct = _patch_feedback_repo_construction(tmp_path)
    with patch("src.ui.pages.admin_users.is_admin", return_value=True), \
         patch("src.ui.pages.admin_users.backend_factory.get_user_account_repository", user_construct), \
         patch("src.ui.pages.admin_users.backend_factory.get_feedback_repository", feedback_construct):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = _main_text(at)
    # most-recently-submitted first: stranger (01-03) before founder (01-01)
    assert all_text.index("Bob") < all_text.index("Ada Lovelace")
    assert "founder@example.test" in all_text
    assert "Submitted: 2026-01-01T00:00:00+00:00" in all_text
    assert "Role: Other" in all_text
    assert "Primary interest: Daily News" in all_text
    assert "Tracking workflow: Other — A custom spreadsheet" in all_text
    assert "Faster Korea coverage" in all_text
    # a submission with no Other detail and no optional feedback renders cleanly
    assert "Tracking workflow: News alerts" in all_text


def test_admin_feedback_list_retrieval_failure_shows_fixed_generic_message_with_no_leaked_detail(tmp_path):
    user_construct = _patch_repo_construction(tmp_path)

    def _boom(settings):
        raise RuntimeError(
            "connection refused to postgres://real-host.internal:5432/real-db with password hunter2"
        )

    with patch("src.ui.pages.admin_users.is_admin", return_value=True), \
         patch("src.ui.pages.admin_users.backend_factory.get_user_account_repository", user_construct), \
         patch("src.ui.pages.admin_users.backend_factory.get_feedback_repository", _boom):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = _main_text(at)
    assert "Could not read feedback submissions. Please try again later." in all_text
    for leaked in ("RuntimeError", "hunter2", "postgres://", "real-host", "5432", "real-db", "connection refused"):
        assert leaked not in all_text


def test_no_secret_token_cookie_or_session_material_is_rendered_in_the_feedback_section(tmp_path):
    feedback_repo = backend_factory.JsonFeedbackRepository(cache_dir=tmp_path)
    feedback_repo.submit_feedback(
        "founder@example.test", "Ada Lovelace", FeedbackRole.OTHER, FeedbackTrackingWorkflow.OTHER,
        "A custom spreadsheet", FeedbackPrimaryInterest.DAILY_NEWS, "Faster Korea coverage",
        "2026-01-01T00:00:00+00:00",
    )
    user_construct = _patch_repo_construction(tmp_path)
    feedback_construct = _patch_feedback_repo_construction(tmp_path)
    with patch("src.ui.pages.admin_users.is_admin", return_value=True), \
         patch("src.ui.pages.admin_users.backend_factory.get_user_account_repository", user_construct), \
         patch("src.ui.pages.admin_users.backend_factory.get_feedback_repository", feedback_construct):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = _main_text(at).lower()
    for forbidden in (
        "cookie_secret", "client_secret", "id_token", "access_token", "authorization: bearer",
        "oauth2callback", "password", "api_key", "database_url",
    ):
        assert forbidden not in all_text
