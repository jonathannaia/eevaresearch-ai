"""Measured coverage persistence against a REAL PostgreSQL server
(Coverage Control Plane, Milestone 1).

Local disposable test infrastructure only — the same loopback container
`scripts/postgres_test_container.sh` starts for every other Postgres
test here. Never a hosted database, never production.

Two of these are merge gates, and neither can be proved with an
in-memory fake:

  * SQLite/PostgreSQL parity — the two repositories share no code by
    deliberate convention, so "identical contract" is an assertion until
    both are run against their own real engine.
  * Transaction atomicity — psycopg is autocommit=False, so a failure
    part-way through a lane's writes must leave NO partial state. Only a
    real server exhibits the rollback semantics that guarantees.

Deliberately a separate file from tests/test_backend_factory_postgres.py,
which carries a pre-existing whole-file ordering/locking hang (present on
pristine base, unrelated to this work)."""
from __future__ import annotations

import pytest

from src.data_access.postgres_state_db import connection as pg_connection
from src.data_access.postgres_state_db import coverage_status_repository as pg_repo
from src.data_access.postgres_state_db import schema as pg_schema
from src.data_access.state_db import connection as sqlite_connection
from src.data_access.state_db import coverage_status_repository as sqlite_repo
from src.data_access.state_db import schema as sqlite_schema

from tests._postgres_test_support import pg_isolated_dsn  # noqa: F401 (fixture import)

NOW = "2026-09-29T12:00:00+00:00"


def _status(module, issuer_id="edgar:NVDA", lane="edgar", state="Covered", **kwargs):
    base = dict(
        issuer_id=issuer_id, lane=lane, expected=True, coverage_state=state,
        last_attempt_at=NOW, last_success_at=NOW, last_no_data_at=None,
        last_item_at=None, last_material_item_at=None, consecutive_empty_runs=0,
        consecutive_failures=0, failure_class=None, blocking_reason="", updated_at=NOW,
    )
    base.update(kwargs)
    return module.InstrumentLaneCoverage(**base)


def _event(module, **kwargs):
    base = dict(
        scope="lane", issuer_id=None, lane="edinet", from_state="lane_healthy",
        to_state="lane_failing", failure_class="EdinetError",
        blocking_reason="lane_untrusted", detail="", at=NOW,
    )
    base.update(kwargs)
    return module.CoverageEvent(**base)


@pytest.fixture
def pg_conn(pg_isolated_dsn):
    conn = pg_connection.connect(pg_isolated_dsn)
    pg_schema.migrate(conn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def sqlite_conn():
    conn = sqlite_connection.connect_in_memory()
    sqlite_schema.migrate(conn)
    return conn


# --- MERGE GATE 1: SQLite/PostgreSQL parity --------------------------------

def test_both_backends_round_trip_a_status_identically(pg_conn, sqlite_conn):
    fields = (
        "issuer_id", "lane", "expected", "coverage_state", "last_attempt_at",
        "last_success_at", "last_no_data_at", "last_item_at", "last_material_item_at",
        "consecutive_empty_runs", "consecutive_failures", "failure_class",
        "blocking_reason", "updated_at",
    )
    extras = dict(
        last_no_data_at="2026-09-29T11:00:00+00:00", last_item_at="2026-09-28T09:00:00+00:00",
        last_material_item_at="2026-09-28T09:30:00+00:00", consecutive_empty_runs=4,
        consecutive_failures=2, failure_class="EdgarError", blocking_reason="untrusted_outcome",
    )
    pg_repo.upsert_coverage_status(pg_conn, _status(pg_repo, state="Failing", **extras))
    sqlite_repo.upsert_coverage_status(sqlite_conn, _status(sqlite_repo, state="Failing", **extras))

    from_pg = pg_repo.get_coverage_status(pg_conn, "edgar:NVDA", "edgar")
    from_sqlite = sqlite_repo.get_coverage_status(sqlite_conn, "edgar:NVDA", "edgar")

    for field in fields:
        assert getattr(from_pg, field) == getattr(from_sqlite, field), field
    assert from_pg.expected is True and from_sqlite.expected is True


def test_both_backends_upsert_idempotently_and_never_duplicate(pg_conn, sqlite_conn):
    for _ in range(3):
        pg_repo.upsert_coverage_status(pg_conn, _status(pg_repo))
        sqlite_repo.upsert_coverage_status(sqlite_conn, _status(sqlite_repo))
    pg_repo.upsert_coverage_status(pg_conn, _status(pg_repo, state="NoData"))
    sqlite_repo.upsert_coverage_status(sqlite_conn, _status(sqlite_repo, state="NoData"))

    assert len(pg_repo.get_all_coverage_statuses(pg_conn)) == 1
    assert len(sqlite_repo.get_all_coverage_statuses(sqlite_conn)) == 1
    assert pg_repo.get_coverage_status(pg_conn, "edgar:NVDA", "edgar").coverage_state == "NoData"
    assert sqlite_repo.get_coverage_status(sqlite_conn, "edgar:NVDA", "edgar").coverage_state == "NoData"


def test_both_backends_store_a_lane_scoped_event_with_a_null_issuer(pg_conn, sqlite_conn):
    pg_repo.record_coverage_event(pg_conn, _event(pg_repo))
    sqlite_repo.record_coverage_event(sqlite_conn, _event(sqlite_repo))

    from_pg = pg_repo.get_coverage_events(pg_conn)
    from_sqlite = sqlite_repo.get_coverage_events(sqlite_conn)

    assert len(from_pg) == len(from_sqlite) == 1
    assert from_pg[0].issuer_id is None and from_sqlite[0].issuer_id is None
    # Compared field by field, never with ==: the two packages define
    # their own CoverageEvent by the same deliberate no-shared-code
    # convention ProviderScanStatus already follows, so two records with
    # identical values are still different classes.
    for field in ("scope", "issuer_id", "lane", "from_state", "to_state",
                  "failure_class", "blocking_reason", "detail", "at"):
        assert getattr(from_pg[0], field) == getattr(from_sqlite[0], field), field


def test_both_backends_filter_and_order_events_the_same_way(pg_conn, sqlite_conn):
    for lane in ("edgar", "edinet", "edgar"):
        pg_repo.record_coverage_event(pg_conn, _event(pg_repo, lane=lane))
        sqlite_repo.record_coverage_event(sqlite_conn, _event(sqlite_repo, lane=lane))

    assert [e.lane for e in pg_repo.get_coverage_events(pg_conn)] == ["edgar", "edinet", "edgar"]
    assert [e.lane for e in sqlite_repo.get_coverage_events(sqlite_conn)] == ["edgar", "edinet", "edgar"]
    assert len(pg_repo.get_coverage_events(pg_conn, "edgar")) == 2
    assert len(sqlite_repo.get_coverage_events(sqlite_conn, "edgar")) == 2


# --- MERGE GATE 2: transaction atomicity -----------------------------------

def test_a_failure_part_way_through_a_lane_leaves_no_partial_state(pg_conn):
    """One top-level transaction per lane per tick: if the second write
    raises, the first must not survive. Without this, a half-applied
    tick could leave some instruments looking healthy."""
    import psycopg

    with pytest.raises(psycopg.Error):
        with pg_connection.transaction(pg_conn):
            pg_repo.upsert_coverage_status(pg_conn, _status(pg_repo, issuer_id="edgar:AAA"))
            pg_conn.execute("SELECT 1 FROM a_table_that_does_not_exist_coverage_m1")

    pg_conn.rollback()
    assert pg_repo.get_all_coverage_statuses(pg_conn) == {}


def test_a_clean_lane_transaction_commits_every_row_together(pg_conn):
    with pg_connection.transaction(pg_conn):
        pg_repo.upsert_coverage_status(pg_conn, _status(pg_repo, issuer_id="edgar:AAA"))
        pg_repo.upsert_coverage_status(pg_conn, _status(pg_repo, issuer_id="edgar:BBB"))
        pg_repo.record_coverage_event(pg_conn, _event(pg_repo, lane="edgar"))

    assert len(pg_repo.get_all_coverage_statuses(pg_conn)) == 2
    assert len(pg_repo.get_coverage_events(pg_conn)) == 1


def test_the_whole_table_reads_back_in_a_bounded_number_of_queries(pg_conn):
    """get_all_coverage_statuses must not become N+1 as the universe
    grows — the mistake this codebase has already paid for twice."""
    for index in range(40):
        pg_repo.upsert_coverage_status(pg_conn, _status(pg_repo, issuer_id=f"edgar:{index:03d}"))

    executed: list[str] = []
    original = pg_conn.execute

    def _counting(query, *args, **kwargs):
        executed.append(str(query))
        return original(query, *args, **kwargs)

    pg_conn.execute = _counting  # type: ignore[method-assign]
    try:
        rows = pg_repo.get_all_coverage_statuses(pg_conn)
    finally:
        pg_conn.execute = original  # type: ignore[method-assign]

    assert len(rows) == 40
    assert len(executed) == 1
