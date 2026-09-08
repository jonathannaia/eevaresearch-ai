"""Durable-State Phase 4M-0 — provider scan-status/cursor persistence
for the isolated Postgres backend
(src/data_access/postgres_state_db/scan_status_repository.py), against
the real local disposable Postgres test container. Uses pg_conn (an
isolated, already-migrated schema) — see tests/_postgres_test_support.py.
Direct sibling of test_state_db_scan_status_repository.py (SQLite)."""
from __future__ import annotations

from src.data_access.postgres_state_db.scan_status_repository import (
    ProviderScanStatus,
    get_all_scan_statuses,
    get_scan_status,
    upsert_scan_status,
)

from tests._postgres_test_support import pg_conn, pg_isolated_connection  # noqa: F401


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


def test_get_scan_status_returns_none_when_absent(pg_conn):
    assert get_scan_status(pg_conn, "SEC EDGAR") is None


def test_upsert_and_get_round_trip(pg_conn):
    status = _status()
    upsert_scan_status(pg_conn, status)
    assert get_scan_status(pg_conn, "SEC EDGAR") == status


def test_upsert_overwrites_by_provider_no_duplicate_row(pg_conn):
    upsert_scan_status(pg_conn, _status(items_discovered=3))
    upsert_scan_status(pg_conn, _status(items_discovered=7, cursor_value="20260102"))
    loaded = get_scan_status(pg_conn, "SEC EDGAR")
    assert loaded.items_discovered == 7
    assert loaded.cursor_value == "20260102"
    assert len(get_all_scan_statuses(pg_conn)) == 1


def test_statuses_are_provider_scoped_and_never_collide(pg_conn):
    edgar_status = _status(provider="SEC EDGAR")
    dart_status = _status(provider="OpenDART / DART", cursor_value="20260201")
    upsert_scan_status(pg_conn, edgar_status)
    upsert_scan_status(pg_conn, dart_status)
    assert get_scan_status(pg_conn, "SEC EDGAR") == edgar_status
    assert get_scan_status(pg_conn, "OpenDART / DART") == dart_status


def test_get_all_scan_statuses_keyed_by_provider(pg_conn):
    upsert_scan_status(pg_conn, _status(provider="SEC EDGAR"))
    upsert_scan_status(pg_conn, _status(provider="EDINET", cursor_value=None))
    all_statuses = get_all_scan_statuses(pg_conn)
    assert set(all_statuses.keys()) == {"SEC EDGAR", "EDINET"}


def test_failure_code_and_null_cursor_round_trip(pg_conn):
    status = _status(
        cursor_value=None, last_successful_at=None, failure_code="ConnectionError",
        items_discovered=0, candidates_created=0,
    )
    upsert_scan_status(pg_conn, status)
    loaded = get_scan_status(pg_conn, "SEC EDGAR")
    assert loaded.cursor_value is None
    assert loaded.last_successful_at is None
    assert loaded.failure_code == "ConnectionError"
