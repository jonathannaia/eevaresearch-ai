"""Daily News autonomous worker — internal scan-status/health persistence
for the isolated Postgres backend
(src/data_access/postgres_state_db/daily_news_scan_status_repository.py),
against the real local disposable Postgres test container. Uses pg_conn
(an isolated, already-migrated schema) — see
tests/_postgres_test_support.py. Direct sibling of
test_state_db_daily_news_scan_status_repository.py (SQLite)."""
from __future__ import annotations

from src.data_access.postgres_state_db.daily_news_scan_status_repository import (
    WORKER_STATUS_KEY,
    DailyNewsFeedScanStatus,
    DailyNewsWorkerStatus,
    get_all_feed_statuses,
    get_feed_status,
    get_worker_status,
    upsert_feed_status,
    upsert_worker_status,
)

from tests._postgres_test_support import pg_conn, pg_isolated_connection  # noqa: F401


def _feed_status(**overrides) -> DailyNewsFeedScanStatus:
    fields = dict(
        company_name="NVIDIA",
        last_attempt_at="2026-01-01T00:00:00+00:00",
        last_fetch_success_at="2026-01-01T00:00:05+00:00",
        last_story_published_at="2026-01-01T00:00:05+00:00",
        last_failure_code=None,
        items_discovered_last_run=2,
        stories_published_last_run=1,
        updated_at="2026-01-01T00:00:05+00:00",
    )
    fields.update(overrides)
    return DailyNewsFeedScanStatus(**fields)


def _worker_status(**overrides) -> DailyNewsWorkerStatus:
    fields = dict(
        worker_key=WORKER_STATUS_KEY,
        last_tick_started_at="2026-01-01T00:00:00+00:00",
        last_tick_completed_at="2026-01-01T00:00:10+00:00",
        last_reconciliation_at=None,
        last_failure_code=None,
        updated_at="2026-01-01T00:00:10+00:00",
    )
    fields.update(overrides)
    return DailyNewsWorkerStatus(**fields)


def test_get_feed_status_returns_none_when_absent(pg_conn):
    assert get_feed_status(pg_conn, "NVIDIA") is None


def test_upsert_and_get_feed_status_round_trip(pg_conn):
    status = _feed_status()
    upsert_feed_status(pg_conn, status)
    assert get_feed_status(pg_conn, "NVIDIA") == status


def test_upsert_feed_status_overwrites_by_company_no_duplicate_row(pg_conn):
    upsert_feed_status(pg_conn, _feed_status(items_discovered_last_run=2))
    upsert_feed_status(pg_conn, _feed_status(items_discovered_last_run=5))
    loaded = get_feed_status(pg_conn, "NVIDIA")
    assert loaded.items_discovered_last_run == 5
    assert len(get_all_feed_statuses(pg_conn)) == 1


def test_feed_statuses_are_company_scoped_and_never_collide(pg_conn):
    nvidia_status = _feed_status(company_name="NVIDIA")
    intel_status = _feed_status(company_name="Intel Corp.", items_discovered_last_run=0)
    upsert_feed_status(pg_conn, nvidia_status)
    upsert_feed_status(pg_conn, intel_status)
    assert get_feed_status(pg_conn, "NVIDIA") == nvidia_status
    assert get_feed_status(pg_conn, "Intel Corp.") == intel_status


def test_get_all_feed_statuses_keyed_by_company_name(pg_conn):
    upsert_feed_status(pg_conn, _feed_status(company_name="NVIDIA"))
    upsert_feed_status(pg_conn, _feed_status(company_name="Cisco Systems Inc."))
    all_statuses = get_all_feed_statuses(pg_conn)
    assert set(all_statuses.keys()) == {"NVIDIA", "Cisco Systems Inc."}


def test_failure_code_and_null_success_fields_round_trip(pg_conn):
    status = _feed_status(
        last_fetch_success_at=None, last_story_published_at=None, last_failure_code="TimeoutError",
        items_discovered_last_run=0, stories_published_last_run=0,
    )
    upsert_feed_status(pg_conn, status)
    loaded = get_feed_status(pg_conn, "NVIDIA")
    assert loaded.last_fetch_success_at is None
    assert loaded.last_failure_code == "TimeoutError"


def test_get_worker_status_returns_none_when_absent(pg_conn):
    assert get_worker_status(pg_conn) is None


def test_upsert_and_get_worker_status_round_trip(pg_conn):
    status = _worker_status()
    upsert_worker_status(pg_conn, status)
    assert get_worker_status(pg_conn) == status


def test_upsert_worker_status_overwrites_single_row_no_duplicate(pg_conn):
    upsert_worker_status(pg_conn, _worker_status(last_reconciliation_at=None))
    upsert_worker_status(pg_conn, _worker_status(last_reconciliation_at="2026-01-02T00:00:00+00:00"))
    loaded = get_worker_status(pg_conn)
    assert loaded.last_reconciliation_at == "2026-01-02T00:00:00+00:00"
    row = pg_conn.execute("SELECT COUNT(*) AS n FROM daily_news_worker_status").fetchone()
    assert row["n"] == 1
