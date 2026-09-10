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

_HARNESS = Path(__file__).parent / "apptest_pages" / "admin_users_page.py"


def _patch_repo_construction(cache_dir) -> MagicMock:
    """A spy that still delegates to a real JsonUserAccountRepository
    pointed at `cache_dir` — so tests get both real read/write behavior
    and a call-count assertion for "was the repository ever
    constructed"."""
    real_repo = backend_factory.JsonUserAccountRepository(cache_dir=cache_dir)
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
