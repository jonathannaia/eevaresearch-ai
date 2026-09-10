"""Admin Users v1 (design/DECISIONS.md) — the isolated Postgres
counterpart to test_state_db_user_account_repository.py, against the
real local disposable Postgres test container. Uses pg_conn (an
isolated, already-migrated schema) — see tests/_postgres_test_support.py.
Skips cleanly (never fails) when no local disposable Postgres instance
is reachable."""
from __future__ import annotations

from src.data_access.postgres_state_db.user_account_repository import get_user, list_users, record_sign_in

from tests._postgres_test_support import pg_conn, pg_isolated_connection  # noqa: F401


def test_get_user_returns_none_when_absent(pg_conn):
    assert get_user(pg_conn, "nobody@example.test") is None


def test_first_sign_in_creates_one_record_with_count_one(pg_conn):
    record_sign_in(pg_conn, "Founder@Example.Test", "Ada", "2026-01-01T00:00:00+00:00")

    account = get_user(pg_conn, "founder@example.test")
    assert account is not None
    assert account.email == "founder@example.test"
    assert account.display_name == "Ada"
    assert account.first_seen_at == "2026-01-01T00:00:00+00:00"
    assert account.last_seen_at == "2026-01-01T00:00:00+00:00"
    assert account.sign_in_count == 1


def test_second_sign_in_increments_count_and_updates_last_seen_only(pg_conn):
    record_sign_in(pg_conn, "founder@example.test", "Ada", "2026-01-01T00:00:00+00:00")
    record_sign_in(pg_conn, "founder@example.test", "Ada", "2026-01-02T00:00:00+00:00")

    account = get_user(pg_conn, "founder@example.test")
    assert account.sign_in_count == 2
    assert account.first_seen_at == "2026-01-01T00:00:00+00:00"
    assert account.last_seen_at == "2026-01-02T00:00:00+00:00"


def test_email_normalization_trims_and_lowercases_to_one_row(pg_conn):
    record_sign_in(pg_conn, "  Founder@Example.Test  ", None, "2026-01-01T00:00:00+00:00")
    record_sign_in(pg_conn, "founder@example.test", None, "2026-01-02T00:00:00+00:00")

    assert get_user(pg_conn, "founder@example.test").sign_in_count == 2
    assert len(list_users(pg_conn)) == 1


def test_display_name_backfills_from_null_but_never_overwrites_a_real_name(pg_conn):
    record_sign_in(pg_conn, "founder@example.test", None, "2026-01-01T00:00:00+00:00")
    assert get_user(pg_conn, "founder@example.test").display_name is None

    record_sign_in(pg_conn, "founder@example.test", "Ada", "2026-01-02T00:00:00+00:00")
    assert get_user(pg_conn, "founder@example.test").display_name == "Ada"

    record_sign_in(pg_conn, "founder@example.test", None, "2026-01-03T00:00:00+00:00")
    assert get_user(pg_conn, "founder@example.test").display_name == "Ada"


def test_list_users_orders_most_recently_seen_first(pg_conn):
    record_sign_in(pg_conn, "first@example.test", None, "2026-01-01T00:00:00+00:00")
    record_sign_in(pg_conn, "second@example.test", None, "2026-01-03T00:00:00+00:00")
    record_sign_in(pg_conn, "third@example.test", None, "2026-01-02T00:00:00+00:00")

    assert [a.email for a in list_users(pg_conn)] == [
        "second@example.test", "third@example.test", "first@example.test",
    ]


def test_list_users_search_matches_email_or_display_name_case_insensitively(pg_conn):
    record_sign_in(pg_conn, "founder@example.test", "Ada Lovelace", "2026-01-01T00:00:00+00:00")
    record_sign_in(pg_conn, "stranger@example.test", "Bob", "2026-01-02T00:00:00+00:00")

    assert [a.email for a in list_users(pg_conn, search="ADA")] == ["founder@example.test"]
    assert list_users(pg_conn, search="nomatch") == []


def test_list_users_empty_store_returns_empty_list(pg_conn):
    assert list_users(pg_conn) == []
