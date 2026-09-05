"""Daily News autonomous worker — internal scan-status/health persistence,
the isolated Postgres counterpart to
state_db/daily_news_scan_status_repository.py, identical shape,
independently implemented (no shared code, per this package's existing
no-dialect-abstraction constraint). See that module's own docstring for
the full design rationale, including the last_fetch_success_at vs.
last_story_published_at distinction."""
from __future__ import annotations

from dataclasses import dataclass

import psycopg

from src.data_access.postgres_state_db.connection import transaction

WORKER_STATUS_KEY = "daily_news"


@dataclass(frozen=True)
class DailyNewsFeedScanStatus:
    company_name: str
    last_attempt_at: str | None
    last_fetch_success_at: str | None
    last_story_published_at: str | None
    last_failure_code: str | None
    items_discovered_last_run: int
    stories_published_last_run: int
    updated_at: str
    # Observability fix (Daily News worker observability workstream,
    # design/DECISIONS.md) — additive, defaulted fields, isolated
    # Postgres counterpart to state_db/daily_news_scan_status_repository.
    # py's own identical addition; see that module's own docstring for
    # the full rationale.
    items_already_seen_last_run: int = 0
    items_deduplicated_last_run: int = 0
    items_suppressed_no_url_last_run: int = 0


@dataclass(frozen=True)
class DailyNewsWorkerStatus:
    worker_key: str
    last_tick_started_at: str | None
    last_tick_completed_at: str | None
    last_reconciliation_at: str | None
    last_failure_code: str | None
    updated_at: str


def _row_to_feed_status(row: dict) -> DailyNewsFeedScanStatus:
    return DailyNewsFeedScanStatus(
        company_name=row["company_name"],
        last_attempt_at=row["last_attempt_at"],
        last_fetch_success_at=row["last_fetch_success_at"],
        last_story_published_at=row["last_story_published_at"],
        last_failure_code=row["last_failure_code"],
        items_discovered_last_run=row["items_discovered_last_run"],
        stories_published_last_run=row["stories_published_last_run"],
        updated_at=row["updated_at"],
        items_already_seen_last_run=row["items_already_seen_last_run"],
        items_deduplicated_last_run=row["items_deduplicated_last_run"],
        items_suppressed_no_url_last_run=row["items_suppressed_no_url_last_run"],
    )


def get_feed_status(conn: psycopg.Connection, company_name: str) -> DailyNewsFeedScanStatus | None:
    row = conn.execute(
        "SELECT * FROM daily_news_scan_status WHERE company_name = %s", (company_name,),
    ).fetchone()
    return _row_to_feed_status(row) if row is not None else None


def get_all_feed_statuses(conn: psycopg.Connection) -> dict[str, DailyNewsFeedScanStatus]:
    rows = conn.execute("SELECT * FROM daily_news_scan_status").fetchall()
    return {row["company_name"]: _row_to_feed_status(row) for row in rows}


def upsert_feed_status(conn: psycopg.Connection, status: DailyNewsFeedScanStatus) -> None:
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO daily_news_scan_status (
                company_name, last_attempt_at, last_fetch_success_at, last_story_published_at,
                last_failure_code, items_discovered_last_run, stories_published_last_run, updated_at,
                items_already_seen_last_run, items_deduplicated_last_run, items_suppressed_no_url_last_run
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (company_name) DO UPDATE SET
                last_attempt_at = excluded.last_attempt_at,
                last_fetch_success_at = excluded.last_fetch_success_at,
                last_story_published_at = excluded.last_story_published_at,
                last_failure_code = excluded.last_failure_code,
                items_discovered_last_run = excluded.items_discovered_last_run,
                stories_published_last_run = excluded.stories_published_last_run,
                updated_at = excluded.updated_at,
                items_already_seen_last_run = excluded.items_already_seen_last_run,
                items_deduplicated_last_run = excluded.items_deduplicated_last_run,
                items_suppressed_no_url_last_run = excluded.items_suppressed_no_url_last_run
            """,
            (
                status.company_name, status.last_attempt_at, status.last_fetch_success_at,
                status.last_story_published_at, status.last_failure_code,
                status.items_discovered_last_run, status.stories_published_last_run, status.updated_at,
                status.items_already_seen_last_run, status.items_deduplicated_last_run,
                status.items_suppressed_no_url_last_run,
            ),
        )


def _row_to_worker_status(row: dict) -> DailyNewsWorkerStatus:
    return DailyNewsWorkerStatus(
        worker_key=row["worker_key"],
        last_tick_started_at=row["last_tick_started_at"],
        last_tick_completed_at=row["last_tick_completed_at"],
        last_reconciliation_at=row["last_reconciliation_at"],
        last_failure_code=row["last_failure_code"],
        updated_at=row["updated_at"],
    )


def get_worker_status(conn: psycopg.Connection, worker_key: str = WORKER_STATUS_KEY) -> DailyNewsWorkerStatus | None:
    row = conn.execute(
        "SELECT * FROM daily_news_worker_status WHERE worker_key = %s", (worker_key,),
    ).fetchone()
    return _row_to_worker_status(row) if row is not None else None


def upsert_worker_status(conn: psycopg.Connection, status: DailyNewsWorkerStatus) -> None:
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO daily_news_worker_status (
                worker_key, last_tick_started_at, last_tick_completed_at, last_reconciliation_at,
                last_failure_code, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (worker_key) DO UPDATE SET
                last_tick_started_at = excluded.last_tick_started_at,
                last_tick_completed_at = excluded.last_tick_completed_at,
                last_reconciliation_at = excluded.last_reconciliation_at,
                last_failure_code = excluded.last_failure_code,
                updated_at = excluded.updated_at
            """,
            (
                status.worker_key, status.last_tick_started_at, status.last_tick_completed_at,
                status.last_reconciliation_at, status.last_failure_code, status.updated_at,
            ),
        )
