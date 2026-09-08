"""EevaResearch Phase 4, Step 4B-1 (design/DECISIONS.md) — focused tests
for the new `get_existing_case_ids()` bulk membership read added to
`src/data_access/state_db/research_repository.py` and
`src/data_access/postgres_state_db/research_repository.py`. Every
fixture is synthetic; Postgres tests use the shared, fail-soft
local-only fixtures from tests/_postgres_test_support.py and skip
cleanly when no local disposable Postgres instance is available."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.data_access.research_store import build_case_id
from src.data_access.state_db import connection as sqlite_connection
from src.data_access.state_db import research_repository as sqlite_research_repository
from src.data_access.state_db import schema as sqlite_schema
from src.models.research_case import ResearchCase, ResearchCaseStatus

from tests._postgres_test_support import pg_conn, pg_isolated_connection  # noqa: F401

try:
    from src.data_access.postgres_state_db import research_repository as postgres_research_repository
except ImportError:  # pragma: no cover - psycopg always installed in this repo
    postgres_research_repository = None


def _case(trigger_source_id="cand-1", created_at="2026-08-20T00:00:00+00:00", **overrides):
    defaults = dict(
        id=build_case_id("radar", trigger_source_id, created_at),
        trigger_source_type="radar", trigger_source_id=trigger_source_id,
        trigger_source_name="Example Corp", trigger_summary="Filed a material event.",
        title="Example research case", research_question="What is the supply-chain exposure?",
        status=ResearchCaseStatus.OPEN, created_at=created_at, version=1,
    )
    defaults.update(overrides)
    return ResearchCase(**defaults)


def _sqlite_conn():
    conn = sqlite_connection.connect_in_memory()
    sqlite_schema.migrate(conn)
    return conn


# ============================================================
# SQLite
# ============================================================


def test_sqlite_empty_input_causes_zero_sql_calls():
    conn = MagicMock()
    result = sqlite_research_repository.get_existing_case_ids(conn, [])
    assert result == frozenset()
    conn.execute.assert_not_called()


def test_sqlite_nonempty_request_causes_exactly_one_parameterized_query():
    conn = _sqlite_conn()
    case = _case()
    sqlite_research_repository.insert_research_case(conn, case)

    tracking_conn = MagicMock(wraps=conn)
    result = sqlite_research_repository.get_existing_case_ids(tracking_conn, [case.id, "case-does-not-exist"])
    assert tracking_conn.execute.call_count == 1
    assert result == frozenset({case.id})


def test_sqlite_existing_and_missing_ids_return_correct_intersection():
    conn = _sqlite_conn()
    case_a = _case(trigger_source_id="cand-a")
    case_b = _case(trigger_source_id="cand-b")
    sqlite_research_repository.insert_research_case(conn, case_a)
    sqlite_research_repository.insert_research_case(conn, case_b)

    result = sqlite_research_repository.get_existing_case_ids(conn, [case_a.id, "case-missing", case_b.id])
    assert result == frozenset({case_a.id, case_b.id})


def test_sqlite_duplicate_ids_in_input_do_not_affect_result():
    conn = _sqlite_conn()
    case = _case()
    sqlite_research_repository.insert_research_case(conn, case)
    result = sqlite_research_repository.get_existing_case_ids(conn, [case.id, case.id, case.id])
    assert result == frozenset({case.id})


def test_sqlite_no_id_value_is_interpolated_into_sql_text():
    conn = _sqlite_conn()
    tracking_conn = MagicMock(wraps=conn)
    sqlite_research_repository.get_existing_case_ids(tracking_conn, ["case-a", "case-b", "case-c"])
    sql_text, params = tracking_conn.execute.call_args[0]
    assert "case-a" not in sql_text and "case-b" not in sql_text and "case-c" not in sql_text
    assert params == ("case-a", "case-b", "case-c")
    assert sql_text.count("?") == 3


def test_sqlite_no_matches_returns_empty_frozenset():
    conn = _sqlite_conn()
    result = sqlite_research_repository.get_existing_case_ids(conn, ["case-does-not-exist"])
    assert result == frozenset()


# ============================================================
# Postgres
# ============================================================


def test_postgres_empty_input_causes_zero_sql_calls():
    if postgres_research_repository is None:
        pytest.skip("psycopg not available")
    conn = MagicMock()
    result = postgres_research_repository.get_existing_case_ids(conn, [])
    assert result == frozenset()
    conn.execute.assert_not_called()


def test_postgres_query_construction_uses_any_with_single_list_param():
    if postgres_research_repository is None:
        pytest.skip("psycopg not available")
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = []
    postgres_research_repository.get_existing_case_ids(conn, ["case-a", "case-b"])
    assert conn.execute.call_count == 1
    sql_text, params = conn.execute.call_args[0]
    assert "= ANY(%s)" in sql_text
    assert params == (["case-a", "case-b"],)
    assert "case-a" not in sql_text and "case-b" not in sql_text


def test_postgres_existing_and_missing_ids_return_correct_intersection(pg_conn):
    case_a = _case(trigger_source_id="cand-pg-a")
    case_b = _case(trigger_source_id="cand-pg-b")
    postgres_research_repository.insert_research_case(pg_conn, case_a)
    postgres_research_repository.insert_research_case(pg_conn, case_b)

    result = postgres_research_repository.get_existing_case_ids(pg_conn, [case_a.id, "case-missing", case_b.id])
    assert result == frozenset({case_a.id, case_b.id})


def test_postgres_duplicate_ids_in_input_do_not_affect_result(pg_conn):
    case = _case(trigger_source_id="cand-pg-dup")
    postgres_research_repository.insert_research_case(pg_conn, case)
    result = postgres_research_repository.get_existing_case_ids(pg_conn, [case.id, case.id])
    assert result == frozenset({case.id})
