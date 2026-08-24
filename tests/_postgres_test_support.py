"""Shared, synthetic-only test support for Durable-State Phase 4B's
isolated Postgres backend (src/data_access/postgres_state_db/). Not a
conftest.py by explicit instruction — every new Postgres test file
imports what it needs from here directly.

Local-container-only: every constant and helper here targets exactly
the disposable, loopback-only container this phase's approvals created
— never a hosted database, never a value read from an environment
file, `Settings`, or `get_settings()`. The connection password is read
from exactly one
process-local environment variable (`EEVARESEARCH_PG_TEST_PASSWORD`) —
the same, and only, mechanism the implementing session's own bounded
shell process uses to pass it to this test run; no test, fixture, or
this module itself ever prints, logs, or writes that value anywhere.

Every real-connection fixture here fails soft: if the environment
variable is absent, or a fast bounded connection attempt doesn't
succeed, the requesting test is skipped (pytest.skip), never failed —
this is what keeps the full suite safe and fast whether or not the
local disposable container happens to be running."""
from __future__ import annotations

import os
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

from src.data_access.postgres_state_db import schema as postgres_schema

PG_HOST = "127.0.0.1"
PG_PORT = 55432
PG_DBNAME = "eevaresearch_test_phase4b"
PG_ROLE = "eevaresearch_test_user"
_PASSWORD_ENV_VAR = "EEVARESEARCH_PG_TEST_PASSWORD"
_CONNECT_TIMEOUT_SECONDS = 2


def _build_dsn() -> str | None:
    """None if the process-local test password isn't set — the one
    signal every fixture below uses to decide whether the local
    disposable container is available for this test run at all."""
    password = os.environ.get(_PASSWORD_ENV_VAR)
    if not password:
        return None
    return (
        f"host={PG_HOST} port={PG_PORT} dbname={PG_DBNAME} "
        f"user={PG_ROLE} password={password} "
        f"connect_timeout={_CONNECT_TIMEOUT_SECONDS}"
    )


def _try_connect() -> psycopg.Connection | None:
    dsn = _build_dsn()
    if dsn is None:
        return None
    try:
        return psycopg.connect(dsn, row_factory=dict_row)
    except psycopg.OperationalError:
        return None


@pytest.fixture
def pg_isolated_dsn():
    """Like pg_isolated_connection, but yields a DSN *string* rather
    than an open connection — for tests that go through
    backend_factory.py's own connection construction (which has no
    schema-isolation concept of its own; it just connects to whatever
    schema the DSN's connection defaults to). A dedicated setup
    connection creates a fresh, uniquely-named schema on the shared
    local test database, then the yielded DSN carries a libpq
    `options='-c search_path=<schema>'` parameter so every connection
    opened from it — including the one backend_factory.py's
    _require_postgres_connection() opens internally, and the migrate()
    call it makes — operates inside that schema, never the shared
    `public` schema. Drops the schema with CASCADE on teardown
    regardless of pass/fail. Skips the requesting test (never fails it)
    under the same conditions pg_isolated_connection does."""
    setup_conn = _try_connect()
    if setup_conn is None:
        pytest.skip(
            "Local disposable Postgres test container not reachable "
            f"({_PASSWORD_ENV_VAR} unset, or connection failed) — skipping."
        )
    schema_name = f"test_{uuid.uuid4().hex}"
    try:
        # Programmatically generated, not user input — safe to
        # interpolate as a SQL identifier; see pg_isolated_connection's
        # own comment for the same reasoning.
        setup_conn.execute(f'CREATE SCHEMA "{schema_name}"')
        setup_conn.commit()
        password = os.environ.get(_PASSWORD_ENV_VAR)
        dsn = (
            f"host={PG_HOST} port={PG_PORT} dbname={PG_DBNAME} "
            f"user={PG_ROLE} password={password} "
            f"connect_timeout={_CONNECT_TIMEOUT_SECONDS} "
            f"options='-c search_path={schema_name}'"
        )
        yield dsn
    finally:
        try:
            setup_conn.rollback()
        except psycopg.Error:
            pass
        setup_conn.execute(f'DROP SCHEMA "{schema_name}" CASCADE')
        setup_conn.commit()
        setup_conn.close()


@pytest.fixture
def pg_isolated_connection():
    """A real connection to the shared local disposable test database,
    isolated to a fresh, uniquely-named schema (never the default
    `public` schema) with nothing migrated into it yet — for schema/
    migration tests, which need to control migrate() themselves. The
    schema is dropped with CASCADE on teardown regardless of pass/fail,
    so no test's data survives it and `public` is never touched. Skips
    the requesting test (never fails it) if the local disposable
    container/password isn't available."""
    conn = _try_connect()
    if conn is None:
        pytest.skip(
            "Local disposable Postgres test container not reachable "
            f"({_PASSWORD_ENV_VAR} unset, or connection failed) — skipping."
        )
    # Programmatically generated, not user input — safe to interpolate
    # as a SQL identifier; Postgres identifiers can't be parameterized
    # via %s the way values can.
    schema_name = f"test_{uuid.uuid4().hex}"
    try:
        conn.execute(f'CREATE SCHEMA "{schema_name}"')
        conn.commit()
        conn.execute(f'SET search_path TO "{schema_name}"')
        conn.commit()
        yield conn
    finally:
        try:
            conn.rollback()
        except psycopg.Error:
            pass
        conn.execute("SET search_path TO public")
        conn.commit()
        conn.execute(f'DROP SCHEMA "{schema_name}" CASCADE')
        conn.commit()
        conn.close()


@pytest.fixture
def pg_conn(pg_isolated_connection):
    """Same isolation as pg_isolated_connection above, with the current
    schema version already migrated — for every repository test that
    needs a ready-to-use schema rather than testing migration itself."""
    postgres_schema.migrate(pg_isolated_connection)
    return pg_isolated_connection
