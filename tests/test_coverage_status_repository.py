"""Measured coverage persistence — SQLite repository (Coverage Control
Plane, Milestone 1).

Offline only: a private in-memory SQLite database per test, no network,
no credential, no Postgres. The Postgres counterpart's parity and
transaction behavior are proved separately in
tests/test_coverage_status_postgres.py against a real server, because
neither can be proved with an in-memory fake."""
from __future__ import annotations

import pytest

from src.data_access.state_db import connection, schema
from src.data_access.state_db.coverage_status_repository import (
    CoverageEvent,
    InstrumentLaneCoverage,
    get_all_coverage_statuses,
    get_coverage_events,
    get_coverage_status,
    get_coverage_statuses_for_lane,
    record_coverage_event,
    upsert_coverage_status,
)


@pytest.fixture
def conn():
    c = connection.connect_in_memory()
    schema.migrate(c)
    return c


def _status(issuer_id="edgar:NVDA", lane="edgar", state="Covered", **kwargs) -> InstrumentLaneCoverage:
    base = dict(
        issuer_id=issuer_id, lane=lane, expected=True, coverage_state=state,
        last_attempt_at="2026-09-29T12:00:00+00:00", last_success_at="2026-09-29T12:00:00+00:00",
        last_no_data_at=None, last_item_at=None, last_material_item_at=None,
        consecutive_empty_runs=0, consecutive_failures=0, failure_class=None,
        blocking_reason="", updated_at="2026-09-29T12:00:00+00:00",
    )
    base.update(kwargs)
    return InstrumentLaneCoverage(**base)


def test_the_migration_creates_both_tables_and_is_idempotent(conn):
    assert schema.migrate(conn) == schema.CURRENT_SCHEMA_VERSION
    assert schema.migrate(conn) == schema.CURRENT_SCHEMA_VERSION  # second call is a no-op


def test_a_status_round_trips_every_field(conn):
    status = _status(
        last_no_data_at="2026-09-29T11:00:00+00:00", last_item_at="2026-09-28T09:00:00+00:00",
        last_material_item_at="2026-09-28T09:30:00+00:00", consecutive_empty_runs=3,
        consecutive_failures=0, failure_class=None, blocking_reason="",
    )
    upsert_coverage_status(conn, status)

    assert get_coverage_status(conn, "edgar:NVDA", "edgar") == status


def test_upsert_is_idempotent_and_replaces_rather_than_duplicating(conn):
    upsert_coverage_status(conn, _status())
    upsert_coverage_status(conn, _status())
    upsert_coverage_status(conn, _status(state="NoData"))

    assert len(get_all_coverage_statuses(conn)) == 1
    assert get_coverage_status(conn, "edgar:NVDA", "edgar").coverage_state == "NoData"


def test_the_same_issuer_on_two_lanes_is_two_rows(conn):
    upsert_coverage_status(conn, _status(issuer_id="x:1", lane="edgar"))
    upsert_coverage_status(conn, _status(issuer_id="x:1", lane="dart"))

    assert len(get_all_coverage_statuses(conn)) == 2
    assert set(get_all_coverage_statuses(conn)) == {("x:1", "edgar"), ("x:1", "dart")}


def test_statuses_can_be_read_back_by_lane(conn):
    upsert_coverage_status(conn, _status(issuer_id="edgar:A", lane="edgar"))
    upsert_coverage_status(conn, _status(issuer_id="edgar:B", lane="edgar"))
    upsert_coverage_status(conn, _status(issuer_id="dart:C", lane="dart"))

    assert [s.issuer_id for s in get_coverage_statuses_for_lane(conn, "edgar")] == ["edgar:A", "edgar:B"]
    assert [s.issuer_id for s in get_coverage_statuses_for_lane(conn, "dart")] == ["dart:C"]


def test_a_lane_scoped_event_stores_a_null_issuer(conn):
    record_coverage_event(conn, CoverageEvent(
        scope="lane", issuer_id=None, lane="edinet", from_state="lane_healthy",
        to_state="lane_failing", failure_class="EdinetError",
        blocking_reason="lane_untrusted", detail="", at="2026-09-29T12:00:00+00:00",
    ))

    events = get_coverage_events(conn)
    assert len(events) == 1
    assert events[0].scope == "lane"
    assert events[0].issuer_id is None
    assert events[0].blocking_reason == "lane_untrusted"


def test_events_are_returned_in_insertion_order_and_filterable_by_lane(conn):
    for lane in ("edgar", "edinet", "edgar"):
        record_coverage_event(conn, CoverageEvent(
            scope="lane", issuer_id=None, lane=lane, from_state="lane_healthy",
            to_state="lane_failing", failure_class=None, blocking_reason="provider_error",
            detail="", at="2026-09-29T12:00:00+00:00",
        ))

    assert [e.lane for e in get_coverage_events(conn)] == ["edgar", "edinet", "edgar"]
    assert len(get_coverage_events(conn, "edgar")) == 2


def test_an_unknown_pair_reads_back_as_none(conn):
    assert get_coverage_status(conn, "edgar:NOPE", "edgar") is None


def test_the_expected_flag_survives_as_a_real_boolean(conn):
    upsert_coverage_status(conn, _status(state="NotExpected", expected=False))

    row = get_coverage_status(conn, "edgar:NVDA", "edgar")
    assert row.expected is False
    assert isinstance(row.expected, bool)
