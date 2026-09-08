"""state_db.daily_news_scan_status_repository — round-trip, per-feed
scoping, and the single worker-status row, for the Daily News autonomous
worker workstream. In-memory SQLite only; no real fetch/pipeline/network
call. Direct sibling of test_state_db_scan_status_repository.py
(Radar's own provider_scan_status)."""
from __future__ import annotations

from src.data_access.state_db import connection, schema
from src.data_access.state_db.daily_news_scan_status_repository import (
    WORKER_STATUS_KEY,
    DailyNewsFeedScanStatus,
    DailyNewsWorkerStatus,
    get_all_feed_statuses,
    get_feed_status,
    get_worker_status,
    upsert_feed_status,
    upsert_worker_status,
)


def _conn():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    return conn


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


def test_get_feed_status_returns_none_when_absent():
    conn = _conn()
    assert get_feed_status(conn, "NVIDIA") is None


def test_upsert_and_get_feed_status_round_trip():
    conn = _conn()
    status = _feed_status()
    upsert_feed_status(conn, status)
    assert get_feed_status(conn, "NVIDIA") == status


def test_upsert_feed_status_overwrites_by_company_no_duplicate_row():
    conn = _conn()
    upsert_feed_status(conn, _feed_status(items_discovered_last_run=2))
    upsert_feed_status(conn, _feed_status(items_discovered_last_run=5))
    loaded = get_feed_status(conn, "NVIDIA")
    assert loaded.items_discovered_last_run == 5
    assert len(get_all_feed_statuses(conn)) == 1


def test_feed_statuses_are_company_scoped_and_never_collide():
    conn = _conn()
    nvidia_status = _feed_status(company_name="NVIDIA")
    intel_status = _feed_status(company_name="Intel Corp.", items_discovered_last_run=0)
    upsert_feed_status(conn, nvidia_status)
    upsert_feed_status(conn, intel_status)
    assert get_feed_status(conn, "NVIDIA") == nvidia_status
    assert get_feed_status(conn, "Intel Corp.") == intel_status


def test_get_all_feed_statuses_keyed_by_company_name():
    conn = _conn()
    upsert_feed_status(conn, _feed_status(company_name="NVIDIA"))
    upsert_feed_status(conn, _feed_status(company_name="Cisco Systems Inc."))
    all_statuses = get_all_feed_statuses(conn)
    assert set(all_statuses.keys()) == {"NVIDIA", "Cisco Systems Inc."}


def test_failure_code_and_null_success_fields_round_trip():
    conn = _conn()
    status = _feed_status(
        last_fetch_success_at=None, last_story_published_at=None, last_failure_code="TimeoutError",
        items_discovered_last_run=0, stories_published_last_run=0,
    )
    upsert_feed_status(conn, status)
    loaded = get_feed_status(conn, "NVIDIA")
    assert loaded.last_fetch_success_at is None
    assert loaded.last_failure_code == "TimeoutError"


def test_a_failed_tick_can_preserve_prior_success_timestamps_across_upserts():
    """Mirrors scan_status_repository.py's own analogous test: a caller
    may explicitly carry forward the previous last_fetch_success_at/
    last_story_published_at into a new, failed-status upsert — the
    repository itself imposes no read-modify-write requirement."""
    conn = _conn()
    successful = _feed_status(last_fetch_success_at="2026-01-05T00:00:00+00:00", last_failure_code=None)
    upsert_feed_status(conn, successful)

    failed_attempt = _feed_status(
        last_attempt_at="2026-01-06T00:00:00+00:00",
        last_fetch_success_at=successful.last_fetch_success_at,
        last_story_published_at=successful.last_story_published_at,
        last_failure_code="ConnectionError",
        items_discovered_last_run=0,
        stories_published_last_run=0,
        updated_at="2026-01-06T00:00:00+00:00",
    )
    upsert_feed_status(conn, failed_attempt)

    loaded = get_feed_status(conn, "NVIDIA")
    assert loaded.last_fetch_success_at == "2026-01-05T00:00:00+00:00"
    assert loaded.last_failure_code == "ConnectionError"


def test_items_already_seen_deduplicated_and_suppressed_no_url_round_trip():
    """Observability fix — proves the three new counters actually persist
    non-default values through a real upsert/read cycle (not just an
    incidental 0==0 match on both sides, which a default-only test could
    mask)."""
    conn = _conn()
    status = _feed_status(
        items_discovered_last_run=9, stories_published_last_run=2,
        items_already_seen_last_run=4, items_deduplicated_last_run=2, items_suppressed_no_url_last_run=1,
    )
    upsert_feed_status(conn, status)
    loaded = get_feed_status(conn, "NVIDIA")
    assert loaded == status
    assert loaded.items_already_seen_last_run == 4
    assert loaded.items_deduplicated_last_run == 2
    assert loaded.items_suppressed_no_url_last_run == 1


def test_items_already_seen_deduplicated_and_suppressed_no_url_default_to_zero():
    conn = _conn()
    upsert_feed_status(conn, _feed_status())
    loaded = get_feed_status(conn, "NVIDIA")
    assert loaded.items_already_seen_last_run == 0
    assert loaded.items_deduplicated_last_run == 0
    assert loaded.items_suppressed_no_url_last_run == 0


def test_get_worker_status_returns_none_when_absent():
    conn = _conn()
    assert get_worker_status(conn) is None


def test_upsert_and_get_worker_status_round_trip():
    conn = _conn()
    status = _worker_status()
    upsert_worker_status(conn, status)
    assert get_worker_status(conn) == status


def test_upsert_worker_status_overwrites_single_row_no_duplicate():
    conn = _conn()
    upsert_worker_status(conn, _worker_status(last_reconciliation_at=None))
    upsert_worker_status(conn, _worker_status(last_reconciliation_at="2026-01-02T00:00:00+00:00"))
    loaded = get_worker_status(conn)
    assert loaded.last_reconciliation_at == "2026-01-02T00:00:00+00:00"
    assert conn.execute("SELECT COUNT(*) AS n FROM daily_news_worker_status").fetchone()["n"] == 1
