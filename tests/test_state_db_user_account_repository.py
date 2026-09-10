"""Admin Users v1 (design/DECISIONS.md) — SQLite persistence for
`user_accounts` (schema.py's own V15). Direct sibling of
test_state_db_postgres_user_account_repository.py."""
from __future__ import annotations

from src.data_access.state_db import connection, schema
from src.data_access.state_db.user_account_repository import get_user, list_users, record_sign_in


def _conn():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    return conn


def test_get_user_returns_none_when_absent():
    conn = _conn()
    assert get_user(conn, "nobody@example.test") is None


def test_first_sign_in_creates_one_record_with_count_one():
    conn = _conn()
    record_sign_in(conn, "Founder@Example.Test", "Ada", "2026-01-01T00:00:00+00:00")

    account = get_user(conn, "founder@example.test")
    assert account is not None
    assert account.email == "founder@example.test"
    assert account.display_name == "Ada"
    assert account.first_seen_at == "2026-01-01T00:00:00+00:00"
    assert account.last_seen_at == "2026-01-01T00:00:00+00:00"
    assert account.sign_in_count == 1


def test_second_sign_in_increments_count_and_updates_last_seen_only():
    conn = _conn()
    record_sign_in(conn, "founder@example.test", "Ada", "2026-01-01T00:00:00+00:00")
    record_sign_in(conn, "founder@example.test", "Ada", "2026-01-02T00:00:00+00:00")

    account = get_user(conn, "founder@example.test")
    assert account.sign_in_count == 2
    assert account.first_seen_at == "2026-01-01T00:00:00+00:00"
    assert account.last_seen_at == "2026-01-02T00:00:00+00:00"


def test_email_normalization_trims_and_lowercases_to_one_row():
    conn = _conn()
    record_sign_in(conn, "  Founder@Example.Test  ", None, "2026-01-01T00:00:00+00:00")
    record_sign_in(conn, "founder@example.test", None, "2026-01-02T00:00:00+00:00")

    assert get_user(conn, "founder@example.test").sign_in_count == 2
    assert len(list_users(conn)) == 1


def test_display_name_backfills_from_null_but_never_overwrites_a_real_name():
    conn = _conn()
    record_sign_in(conn, "founder@example.test", None, "2026-01-01T00:00:00+00:00")
    assert get_user(conn, "founder@example.test").display_name is None

    record_sign_in(conn, "founder@example.test", "Ada", "2026-01-02T00:00:00+00:00")
    assert get_user(conn, "founder@example.test").display_name == "Ada"

    # A later sign-in whose provider/claim omits the name must not erase it.
    record_sign_in(conn, "founder@example.test", None, "2026-01-03T00:00:00+00:00")
    assert get_user(conn, "founder@example.test").display_name == "Ada"


def test_list_users_orders_most_recently_seen_first():
    conn = _conn()
    record_sign_in(conn, "first@example.test", None, "2026-01-01T00:00:00+00:00")
    record_sign_in(conn, "second@example.test", None, "2026-01-03T00:00:00+00:00")
    record_sign_in(conn, "third@example.test", None, "2026-01-02T00:00:00+00:00")

    assert [a.email for a in list_users(conn)] == ["second@example.test", "third@example.test", "first@example.test"]


def test_list_users_search_matches_email_or_display_name_case_insensitively():
    conn = _conn()
    record_sign_in(conn, "founder@example.test", "Ada Lovelace", "2026-01-01T00:00:00+00:00")
    record_sign_in(conn, "stranger@example.test", "Bob", "2026-01-02T00:00:00+00:00")

    assert [a.email for a in list_users(conn, search="ADA")] == ["founder@example.test"]
    assert [a.email for a in list_users(conn, search="stranger")] == ["stranger@example.test"]
    assert list_users(conn, search="nomatch") == []


def test_list_users_search_with_null_display_name_never_matches_a_nonblank_term():
    conn = _conn()
    record_sign_in(conn, "founder@example.test", None, "2026-01-01T00:00:00+00:00")
    assert list_users(conn, search="ada") == []


def test_list_users_empty_store_returns_empty_list():
    conn = _conn()
    assert list_users(conn) == []
