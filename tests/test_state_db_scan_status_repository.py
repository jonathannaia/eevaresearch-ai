"""state_db.scan_status_repository — round-trip, per-provider scoping,
and failure-preservation behavior for Durable-State Phase 4M-0's
provider scan-status/cursor table. In-memory SQLite only; no real
scan/pipeline/network call."""
from __future__ import annotations

from src.data_access.state_db import connection, schema
from src.data_access.state_db.scan_status_repository import (
    ProviderScanStatus,
    get_all_scan_statuses,
    get_scan_status,
    upsert_scan_status,
)


def _conn():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    return conn


def _status(**overrides) -> ProviderScanStatus:
    fields = dict(
        provider="SEC EDGAR",
        cursor_value="20260101",
        started_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:01:00+00:00",
        last_successful_at="2026-01-01T00:01:00+00:00",
        items_discovered=3,
        candidates_created=1,
        skipped_unresolved_count=0,
        failure_code=None,
        updated_at="2026-01-01T00:01:00+00:00",
    )
    fields.update(overrides)
    return ProviderScanStatus(**fields)


def test_get_scan_status_returns_none_when_absent():
    conn = _conn()
    assert get_scan_status(conn, "SEC EDGAR") is None


def test_upsert_and_get_round_trip():
    conn = _conn()
    status = _status()
    upsert_scan_status(conn, status)
    assert get_scan_status(conn, "SEC EDGAR") == status


def test_upsert_overwrites_by_provider_no_duplicate_row():
    conn = _conn()
    upsert_scan_status(conn, _status(items_discovered=3))
    upsert_scan_status(conn, _status(items_discovered=7, cursor_value="20260102"))
    loaded = get_scan_status(conn, "SEC EDGAR")
    assert loaded.items_discovered == 7
    assert loaded.cursor_value == "20260102"
    assert len(get_all_scan_statuses(conn)) == 1


def test_statuses_are_provider_scoped_and_never_collide():
    conn = _conn()
    edgar_status = _status(provider="SEC EDGAR")
    dart_status = _status(provider="OpenDART / DART", cursor_value="20260201")
    upsert_scan_status(conn, edgar_status)
    upsert_scan_status(conn, dart_status)
    assert get_scan_status(conn, "SEC EDGAR") == edgar_status
    assert get_scan_status(conn, "OpenDART / DART") == dart_status


def test_get_all_scan_statuses_keyed_by_provider():
    conn = _conn()
    upsert_scan_status(conn, _status(provider="SEC EDGAR"))
    upsert_scan_status(conn, _status(provider="EDINET", cursor_value=None))
    all_statuses = get_all_scan_statuses(conn)
    assert set(all_statuses.keys()) == {"SEC EDGAR", "EDINET"}


def test_failure_code_and_null_cursor_round_trip():
    conn = _conn()
    status = _status(
        cursor_value=None, last_successful_at=None, failure_code="ConnectionError",
        items_discovered=0, candidates_created=0,
    )
    upsert_scan_status(conn, status)
    loaded = get_scan_status(conn, "SEC EDGAR")
    assert loaded.cursor_value is None
    assert loaded.last_successful_at is None
    assert loaded.failure_code == "ConnectionError"


def test_a_failed_tick_can_preserve_the_prior_successful_cursor_across_upserts():
    """Mirrors radar_worker.py's own _record_failure() behavior: a
    caller may explicitly carry forward the previous cursor/
    last_successful_at into a new, failed-status upsert — proving the
    repository itself imposes no read-modify-write requirement, the
    caller fully controls what each upsert writes."""
    conn = _conn()
    successful = _status(cursor_value="20260105", last_successful_at="2026-01-05T00:00:00+00:00", failure_code=None)
    upsert_scan_status(conn, successful)

    failed_attempt = _status(
        cursor_value=successful.cursor_value,
        last_successful_at=successful.last_successful_at,
        started_at="2026-01-06T00:00:00+00:00",
        completed_at="2026-01-06T00:00:05+00:00",
        failure_code="TimeoutError",
        updated_at="2026-01-06T00:00:05+00:00",
    )
    upsert_scan_status(conn, failed_attempt)

    loaded = get_scan_status(conn, "SEC EDGAR")
    assert loaded.cursor_value == "20260105"
    assert loaded.last_successful_at == "2026-01-05T00:00:00+00:00"
    assert loaded.failure_code == "TimeoutError"
