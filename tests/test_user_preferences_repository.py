"""user_preferences (Theming + Typography release) — SQLite V21 /
Postgres V23, and the three repository backends behind
backend_factory.get_user_preferences_repository().

Postgres tests use tests/_postgres_test_support.py's disposable,
loopback-only container fixture and skip (never fail) when it is not
running."""
from __future__ import annotations

import sqlite3

import psycopg
import pytest

from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.postgres_state_db import schema as postgres_schema
from src.data_access.postgres_state_db import user_preferences_repository as pg_repo
from src.data_access.state_db import connection, schema
from src.data_access.state_db import user_preferences_repository as sqlite_repo
from src.models.user_preferences import THEME_PREFERENCES
from tests._postgres_test_support import pg_isolated_connection  # noqa: F401 (fixture import)

_CREATE_PREFIX = "CREATE TABLE USER_PREFERENCES ("


# --- SQLite V21 -------------------------------------------------------------

def test_sqlite_v21_is_registered_immediately_after_v20():
    # Was "... and is current" until V22 (the agent tables) landed; the
    # "is current" pin moved to tests/test_agent_schema_v24.py.
    versions = [v for v, _ in schema._MIGRATIONS]
    assert versions == sorted(versions) and len(set(versions)) == len(versions)
    assert versions.index(21) == versions.index(20) + 1
    assert dict(schema._MIGRATIONS)[21] is schema._V21_STATEMENTS


def test_sqlite_v21_only_creates_the_new_table():
    assert len(schema._V21_STATEMENTS) == 1
    assert " ".join(schema._V21_STATEMENTS[0].split()).upper().startswith(_CREATE_PREFIX)


def _sqlite_up_to(conn, target: int) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (0)")
    for version, statements in schema._MIGRATIONS:
        if version <= target:
            for statement in statements:
                conn.execute(statement)
            conn.execute("UPDATE schema_version SET version = ?", (version,))
    conn.commit()


def test_sqlite_v20_database_upgrades_to_v21_without_touching_user_accounts():
    conn = connection.connect_in_memory()
    _sqlite_up_to(conn, 20)
    conn.execute(
        "INSERT INTO user_accounts (email, display_name, first_seen_at, last_seen_at, sign_in_count) "
        "VALUES ('a@example.test', 'A', 't0', 't1', 3)"
    )
    conn.commit()
    before = [tuple(r) for r in conn.execute("PRAGMA table_info(user_accounts)").fetchall()]
    assert schema.migrate(conn) == schema.CURRENT_SCHEMA_VERSION
    assert [tuple(r) for r in conn.execute("PRAGMA table_info(user_accounts)").fetchall()] == before
    assert conn.execute("SELECT sign_in_count FROM user_accounts").fetchone()["sign_in_count"] == 3
    assert conn.execute("SELECT COUNT(*) AS n FROM user_preferences").fetchone()["n"] == 0
    assert schema.migrate(conn) == schema.CURRENT_SCHEMA_VERSION  # idempotent


def test_sqlite_check_constraint_rejects_an_unknown_theme():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO user_preferences VALUES ('a@example.test', 'sepia', 'now')")


# --- Postgres V23 --------------------------------------------------------------

def test_postgres_v23_is_registered_immediately_after_v22():
    # Was "... and is current" until V24 (the agent tables) landed; the
    # "is current" pin moved to tests/test_agent_schema_v24.py.
    versions = [v for v, _ in postgres_schema._MIGRATIONS]
    assert versions == sorted(versions) and len(set(versions)) == len(versions)
    assert versions.index(23) == versions.index(22) + 1
    assert dict(postgres_schema._MIGRATIONS)[23] is postgres_schema._V23_STATEMENTS


def test_postgres_v23_only_creates_the_new_table_and_matches_sqlite():
    assert len(postgres_schema._V23_STATEMENTS) == 1
    assert " ".join(postgres_schema._V23_STATEMENTS[0].split()) == " ".join(schema._V21_STATEMENTS[0].split())
    assert " ".join(postgres_schema._V23_STATEMENTS[0].split()).upper().startswith(_CREATE_PREFIX)


def _pg_up_to(conn, target: int) -> None:
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (0)")
    for version, statements in postgres_schema._MIGRATIONS:
        if version <= target:
            for statement in statements:
                conn.execute(statement)
            conn.execute("UPDATE schema_version SET version = %s", (version,))
    conn.commit()


def test_postgres_v22_database_upgrades_to_v23_without_touching_existing_tables(pg_isolated_connection):
    conn = pg_isolated_connection
    _pg_up_to(conn, 22)
    conn.execute(
        "INSERT INTO user_accounts (email, display_name, first_seen_at, last_seen_at, sign_in_count) "
        "VALUES ('a@example.test', 'A', 't0', 't1', 3)"
    )
    conn.commit()
    columns = "SELECT column_name, data_type, is_nullable FROM information_schema.columns " \
              "WHERE table_schema = current_schema() AND table_name = %s ORDER BY ordinal_position"
    before = conn.execute(columns, ("user_accounts",)).fetchall()
    assert postgres_schema.migrate(conn) == postgres_schema.CURRENT_SCHEMA_VERSION
    assert conn.execute(columns, ("user_accounts",)).fetchall() == before
    assert conn.execute("SELECT sign_in_count FROM user_accounts").fetchone()["sign_in_count"] == 3
    prefs = {r["column_name"]: r for r in conn.execute(columns, ("user_preferences",)).fetchall()}
    assert list(prefs) == ["email", "theme_preference", "updated_at"]
    assert all(r["is_nullable"] == "NO" for r in prefs.values())
    assert postgres_schema.migrate(conn) == postgres_schema.CURRENT_SCHEMA_VERSION  # idempotent


def test_postgres_check_constraint_rejects_an_unknown_theme(pg_isolated_connection):
    conn = pg_isolated_connection
    postgres_schema.migrate(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO user_preferences VALUES ('a@example.test', 'sepia', 'now')")
    conn.rollback()


def test_postgres_read_never_leaves_the_connection_idle_in_transaction(pg_isolated_connection):
    conn = pg_isolated_connection
    postgres_schema.migrate(conn)
    pg_repo.set_theme_preference(conn, "a@example.test", "light", "t1")
    assert pg_repo.get_preferences(conn, "a@example.test").theme_preference == "light"
    assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE


# --- repository behavior, every backend -------------------------------------------

@pytest.fixture(params=["json", "sqlite", "postgres"])
def repo(request, tmp_path):
    if request.param == "json":
        yield backend_factory.JsonUserPreferencesRepository(cache_dir=tmp_path)
    elif request.param == "sqlite":
        conn = connection.connect_in_memory()
        schema.migrate(conn)
        yield backend_factory.SqliteUserPreferencesRepository(conn=conn)
    else:
        conn = request.getfixturevalue("pg_isolated_connection")
        postgres_schema.migrate(conn)
        yield backend_factory.PostgresUserPreferencesRepository(conn=conn)


def test_unknown_account_has_no_saved_preference(repo):
    assert repo.get_preferences("nobody@example.test") is None


def test_each_theme_round_trips_and_a_later_choice_replaces_the_earlier_one(repo):
    for n, theme in enumerate(THEME_PREFERENCES):
        repo.set_theme_preference("Reader@Example.test ", theme, f"t{n}")
        saved = repo.get_preferences("reader@example.test")
        assert (saved.email, saved.theme_preference, saved.updated_at) == ("reader@example.test", theme, f"t{n}")


def test_preferences_are_per_account(repo):
    repo.set_theme_preference("a@example.test", "dark", "t1")
    repo.set_theme_preference("b@example.test", "light", "t2")
    assert repo.get_preferences("a@example.test").theme_preference == "dark"
    assert repo.get_preferences("b@example.test").theme_preference == "light"


def test_an_unknown_theme_is_rejected_before_anything_is_written(repo):
    with pytest.raises(ValueError):
        repo.set_theme_preference("a@example.test", "sepia", "t1")
    assert repo.get_preferences("a@example.test") is None


# --- factory -------------------------------------------------------------------------

def test_factory_selects_the_backend_from_settings(tmp_path):
    assert isinstance(backend_factory.get_user_preferences_repository(Settings(db_backend="json", cache_dir=tmp_path)),
                      backend_factory.JsonUserPreferencesRepository)
    sqlite_repo_instance = backend_factory.get_user_preferences_repository(
        Settings(db_backend="sqlite", state_db_path=tmp_path / "state.db", cache_dir=tmp_path)
    )
    try:
        assert isinstance(sqlite_repo_instance, backend_factory.SqliteUserPreferencesRepository)
        sqlite_repo_instance.set_theme_preference("a@example.test", "light", "t1")
        assert sqlite_repo.get_preferences(sqlite_repo_instance.conn, "a@example.test").theme_preference == "light"
    finally:
        sqlite_repo_instance.close()
