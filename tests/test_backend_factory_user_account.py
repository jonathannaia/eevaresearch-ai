"""Admin Users v1 (design/DECISIONS.md) — focused tests for
backend_factory.py's UserAccountRepositoryProtocol seam
(get_user_account_repository), covering the JSON backend directly
(the SQLite/Postgres branches are covered by
tests/test_state_db_user_account_repository.py and
tests/test_state_db_postgres_user_account_repository.py, which exercise
the same underlying module functions this seam delegates to) plus the
backend-selection behavior itself."""
from __future__ import annotations

from src.config.settings import Settings
from src.data_access import backend_factory


def test_json_backend_is_the_default_selection(tmp_path):
    repo = backend_factory.get_user_account_repository(Settings(cache_dir=tmp_path))
    assert isinstance(repo, backend_factory.JsonUserAccountRepository)


def test_json_repository_record_sign_in_and_get_user_round_trip(tmp_path):
    repo = backend_factory.JsonUserAccountRepository(cache_dir=tmp_path)
    repo.record_sign_in("Founder@Example.Test", "Ada", "2026-01-01T00:00:00+00:00")

    account = repo.get_user("founder@example.test")
    assert account.email == "founder@example.test"
    assert account.display_name == "Ada"
    assert account.sign_in_count == 1
    assert account.first_seen_at == account.last_seen_at == "2026-01-01T00:00:00+00:00"


def test_json_repository_second_sign_in_increments_and_preserves_display_name(tmp_path):
    repo = backend_factory.JsonUserAccountRepository(cache_dir=tmp_path)
    repo.record_sign_in("founder@example.test", "Ada", "2026-01-01T00:00:00+00:00")
    repo.record_sign_in("founder@example.test", None, "2026-01-02T00:00:00+00:00")

    account = repo.get_user("founder@example.test")
    assert account.sign_in_count == 2
    assert account.display_name == "Ada"
    assert account.last_seen_at == "2026-01-02T00:00:00+00:00"
    assert account.first_seen_at == "2026-01-01T00:00:00+00:00"


def test_json_repository_persists_to_disk_across_instances(tmp_path):
    backend_factory.JsonUserAccountRepository(cache_dir=tmp_path).record_sign_in(
        "founder@example.test", "Ada", "2026-01-01T00:00:00+00:00"
    )
    reloaded = backend_factory.JsonUserAccountRepository(cache_dir=tmp_path)
    assert reloaded.get_user("founder@example.test") is not None
    assert (tmp_path / "user_accounts.json").exists()


def test_json_repository_list_users_orders_most_recent_first_and_supports_search(tmp_path):
    repo = backend_factory.JsonUserAccountRepository(cache_dir=tmp_path)
    repo.record_sign_in("first@example.test", "Ada", "2026-01-01T00:00:00+00:00")
    repo.record_sign_in("second@example.test", "Bob", "2026-01-03T00:00:00+00:00")

    assert [a.email for a in repo.list_users()] == ["second@example.test", "first@example.test"]
    assert [a.email for a in repo.list_users(search="ada")] == ["first@example.test"]
    assert repo.list_users(search="nomatch") == []


def test_json_repository_empty_store_returns_empty_list(tmp_path):
    repo = backend_factory.JsonUserAccountRepository(cache_dir=tmp_path)
    assert repo.list_users() == []
    assert repo.get_user("nobody@example.test") is None


def test_sqlite_backend_selection_returns_sqlite_repository(tmp_path):
    settings = Settings(db_backend="sqlite", state_db_path=tmp_path / "state.db")
    repo = backend_factory.get_user_account_repository(settings)
    assert isinstance(repo, backend_factory.SqliteUserAccountRepository)
