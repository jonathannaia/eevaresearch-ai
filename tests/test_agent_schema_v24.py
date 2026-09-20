"""Agent Observability and Shadow Mode — Postgres V24 / SQLite V22.

The seven durable agent tables (agent_runs, agent_jobs, agent_packets,
agent_evidence, agent_decisions, agent_audit_events, agent_control).
Additive only: no existing table is altered, so the migration takes no
lock on candidates or any other live relation.

Postgres tests use tests/_postgres_test_support.py's disposable,
loopback-only container fixture and skip (never fail) when it is absent."""
from __future__ import annotations

import re
import sqlite3

import psycopg
import pytest

from src.data_access.postgres_state_db import schema as postgres_schema
from src.data_access.state_db import connection, schema
from tests._postgres_test_support import pg_isolated_connection  # noqa: F401 (fixture import)

AGENT_TABLES = (
    "agent_runs", "agent_jobs", "agent_packets", "agent_evidence",
    "agent_decisions", "agent_audit_events", "agent_control",
)


# --- registration ---------------------------------------------------------------

def test_sqlite_v22_is_registered_after_v21_and_is_current():
    assert schema.CURRENT_SCHEMA_VERSION == 22
    versions = [v for v, _ in schema._MIGRATIONS]
    assert versions == sorted(versions) and len(set(versions)) == len(versions)
    assert versions[-2:] == [21, 22]
    assert dict(schema._MIGRATIONS)[22] is schema._V22_STATEMENTS


def test_postgres_v24_is_registered_after_v23_and_is_current():
    assert postgres_schema.CURRENT_SCHEMA_VERSION == 24
    versions = [v for v, _ in postgres_schema._MIGRATIONS]
    assert versions == sorted(versions) and len(set(versions)) == len(versions)
    assert versions[-2:] == [23, 24]
    assert dict(postgres_schema._MIGRATIONS)[24] is postgres_schema._V24_STATEMENTS


def test_both_backends_create_the_same_seven_tables_and_only_create():
    for statements in (postgres_schema._V24_STATEMENTS, schema._V22_STATEMENTS):
        created = {re.search(r"CREATE TABLE (\w+)", s).group(1) for s in statements if "CREATE TABLE" in s}
        assert created == set(AGENT_TABLES)
        for statement in statements:
            head = " ".join(statement.split())[:20].upper()
            assert head.startswith("CREATE TABLE") or head.startswith("CREATE INDEX"), statement[:60]


def test_the_two_backends_differ_only_in_the_audit_identity_column():
    def normalize(statements):
        text = "\n".join(" ".join(s.split()) for s in statements)
        return text.replace("BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY", "<IDENTITY>") \
                   .replace("INTEGER PRIMARY KEY AUTOINCREMENT", "<IDENTITY>")
    assert normalize(postgres_schema._V24_STATEMENTS) == normalize(schema._V22_STATEMENTS)


# --- SQLite behavior ------------------------------------------------------------

def _sqlite_at(version: int):
    conn = connection.connect_in_memory()
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (0)")
    for v, statements in schema._MIGRATIONS:
        if v <= version:
            for statement in statements:
                conn.execute(statement)
            conn.execute("UPDATE schema_version SET version = ?", (v,))
    conn.commit()
    return conn


def test_sqlite_v21_upgrades_to_v22_without_touching_existing_tables():
    conn = _sqlite_at(21)
    conn.execute("INSERT INTO user_preferences VALUES ('a@example.test', 'dark', 't0')")
    conn.execute(
        "INSERT INTO user_accounts (email, display_name, first_seen_at, last_seen_at, sign_in_count) "
        "VALUES ('a@example.test', 'A', 't0', 't1', 2)"
    )
    conn.commit()
    before = {
        table: [tuple(r) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        for table in ("candidates", "user_accounts", "user_preferences")
    }
    assert schema.migrate(conn) == 22
    after = {
        table: [tuple(r) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        for table in ("candidates", "user_accounts", "user_preferences")
    }
    assert after == before
    assert conn.execute("SELECT theme_preference FROM user_preferences").fetchone()["theme_preference"] == "dark"
    for table in AGENT_TABLES:
        assert conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] == 0
    assert schema.migrate(conn) == 22  # idempotent


@pytest.mark.parametrize("sql,label", [
    ("INSERT INTO agent_control (id, mode_override, updated_at) VALUES (2, 'off', 't')", "control is a singleton"),
    ("INSERT INTO agent_control (id, mode_override, updated_at) VALUES (1, 'sideways', 't')", "mode_override vocabulary"),
    ("INSERT INTO agent_runs (run_id, worker_instance, mode, started_at, status, policy_version) "
     "VALUES ('r', 'w', 'sideways', 't', 'running', 'v1')", "run mode vocabulary"),
    ("INSERT INTO agent_runs (run_id, worker_instance, mode, started_at, status, policy_version) "
     "VALUES ('r', 'w', 'shadow', 't', 'exploded', 'v1')", "run status vocabulary"),
    ("INSERT INTO agent_jobs (job_id, candidate_id, source, candidate_version, policy_version, mode, state, "
     "enqueue_reason, created_at, updated_at) VALUES ('j', 'c', 's', 1, 'v1', 'shadow', 'flying', 'new', 't', 't')", "job state vocabulary"),
    ("INSERT INTO agent_jobs (job_id, candidate_id, source, candidate_version, policy_version, mode, state, "
     "enqueue_reason, created_at, updated_at) VALUES ('j', 'c', 's', 1, 'v1', 'shadow', 'pending', 'guessed', 't', 't')", "enqueue reason vocabulary"),
])
def test_sqlite_check_constraints_reject_unknown_vocabulary(sql, label):
    conn = _sqlite_at(22)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql)


def test_sqlite_job_key_is_unique_per_candidate_version_policy_and_mode():
    conn = _sqlite_at(22)
    row = ("c-1", "SEC EDGAR", 3, "v1", "shadow", "pending", "new", "t", "t")
    conn.execute(
        "INSERT INTO agent_jobs (job_id, candidate_id, source, candidate_version, policy_version, mode, state, "
        "enqueue_reason, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)", ("j-1", *row),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO agent_jobs (job_id, candidate_id, source, candidate_version, policy_version, mode, state, "
            "enqueue_reason, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)", ("j-2", *row),
        )
    # A different mode or policy version is a different job.
    conn.execute(
        "INSERT INTO agent_jobs (job_id, candidate_id, source, candidate_version, policy_version, mode, state, "
        "enqueue_reason, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("j-3", "c-1", "SEC EDGAR", 3, "v2", "shadow", "pending", "new", "t", "t"),
    )


def test_sqlite_decision_keeps_policy_effective_and_the_full_blocking_context():
    conn = _sqlite_at(22)
    conn.execute(
        "INSERT INTO agent_decisions (packet_id, policy_decision, effective_decision, blocked_by, mode, "
        "kill_switch_on, quote_verified, reasons_json, row_results_json, policy_version, decided_at) "
        "VALUES ('p-1', 'AUTO_PUBLISHED', 'NO_ACTION', 'kill_switch', 'shadow', 1, 1, '[]', '[]', 'v1', 't0')"
    )
    row = conn.execute("SELECT * FROM agent_decisions").fetchone()
    assert (row["policy_decision"], row["effective_decision"], row["blocked_by"]) == ("AUTO_PUBLISHED", "NO_ACTION", "kill_switch")
    assert (row["mode"], row["kill_switch_on"]) == ("shadow", 1)
    assert (row["candidate_status_written"], row["published"]) == (0, 0)


# --- Postgres behavior -------------------------------------------------------------

def _pg_at(conn, version: int) -> None:
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (0)")
    for v, statements in postgres_schema._MIGRATIONS:
        if v <= version:
            for statement in statements:
                conn.execute(statement)
            conn.execute("UPDATE schema_version SET version = %s", (v,))
    conn.commit()


def test_postgres_v23_upgrades_to_v24_without_touching_existing_tables(pg_isolated_connection):
    conn = pg_isolated_connection
    _pg_at(conn, 23)
    conn.execute("INSERT INTO user_preferences VALUES ('a@example.test', 'light', 't0')")
    conn.commit()
    columns = ("SELECT table_name, column_name, data_type, is_nullable FROM information_schema.columns "
               "WHERE table_schema = current_schema() AND table_name = ANY(%s) ORDER BY table_name, ordinal_position")
    existing = ["candidates", "user_accounts", "user_preferences"]
    before = conn.execute(columns, (existing,)).fetchall()
    assert postgres_schema.migrate(conn) == 24
    assert conn.execute(columns, (existing,)).fetchall() == before
    assert conn.execute("SELECT theme_preference FROM user_preferences").fetchone()["theme_preference"] == "light"
    present = {r["table_name"] for r in conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = ANY(%s)",
        (list(AGENT_TABLES),),
    ).fetchall()}
    assert present == set(AGENT_TABLES)
    assert postgres_schema.migrate(conn) == 24  # idempotent


def test_postgres_agent_audit_events_id_is_generated(pg_isolated_connection):
    conn = pg_isolated_connection
    postgres_schema.migrate(conn)
    for n in range(2):
        conn.execute(
            "INSERT INTO agent_audit_events (session_id, event_type, created_at) VALUES (%s, %s, %s)",
            (f"s-{n}", "session_started", "t0"),
        )
    ids = [r["id"] for r in conn.execute("SELECT id FROM agent_audit_events ORDER BY id").fetchall()]
    assert len(ids) == 2 and ids[0] < ids[1]
    conn.rollback()


def test_postgres_check_constraints_and_unique_job_key_hold(pg_isolated_connection):
    conn = pg_isolated_connection
    postgres_schema.migrate(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO agent_control (id, mode_override, updated_at) VALUES (1, 'sideways', 't')")
    conn.rollback()
    row = ("c-1", "SEC EDGAR", 3, "v1", "shadow", "pending", "new", "t", "t")
    conn.execute(
        "INSERT INTO agent_jobs (job_id, candidate_id, source, candidate_version, policy_version, mode, state, "
        "enqueue_reason, created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", ("j-1", *row),
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO agent_jobs (job_id, candidate_id, source, candidate_version, policy_version, mode, state, "
            "enqueue_reason, created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", ("j-2", *row),
        )
    conn.rollback()
