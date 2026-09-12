"""Daily News autonomous worker — internal scan-status/health persistence
(design/DECISIONS.md). Three tables: one row per registered feed
(`daily_news_scan_status`, keyed by `company_name` — the exact string
feed_registry.DailyNewsFeedSource.company_name /
daily_news_pipeline.DailyNewsScanReport.source_failures already key by)
tracking per-feed fetch/publish/failure health, one row per registered
SOURCE (`daily_news_source_status`, keyed by `source_id` — see below),
and one single row (`daily_news_worker_status`, keyed by a fixed
WORKER_STATUS_KEY constant) tracking the worker process's own tick/
reconciliation bookkeeping. Written only by scripts/daily_news_worker.py.
Read-only exposure was added later (Daily News operational-fix
workstream, design/DECISIONS.md): daily_news_admin.py's own hidden
"Autonomous worker health" section reads these tables, via
daily_news_backend.get_daily_news_scan_status_repository() — never
directly, and never writes to any of them. daily_news.py (the public
page) still never reads or writes any of them.

`last_fetch_success_at` updates on every tick where that feed's own
fetch+parse succeeded, regardless of whether any new story was
published — distinct from `last_story_published_at`, which only updates
when a new story was actually persisted. This distinction is deliberate:
the worker's own daily reconciliation health check flags staleness using
`last_fetch_success_at` only, so a feed that is fetching successfully but
simply has nothing new to report is never mistaken for a broken one.

Daily News worker observability, Part A (design/DECISIONS.md) —
`daily_news_source_status`/`DailyNewsSourceScanStatus`: `daily_news_
scan_status` is keyed by `company_name`, which collapses distinct
sources for the same company into one shared row (e.g. Meta's
meta-ir-rss and meta-newsroom-rss both write company_name="Meta
Platforms, Inc." and silently overwrite each other's status). This new,
additive table is keyed by `source_id` instead, so each registered feed
is independently measurable. `daily_news_scan_status` is left completely
unchanged — it remains the legacy/aggregate view for a company with
multiple sources (and is still what the worker's own reconciliation
check reads); any new monitoring for a specific source, especially one
of several sources sharing a company, should read
`daily_news_source_status` instead. `last_result_at` is distinct from
`updated_at`: `last_result_at` is when THIS TICK's fetch attempt for
this source concluded (a fact about the external source — set once,
right after the fetch returns); `updated_at` is when this database row
was last written (a fact about our own persistence operation). They are
computed from two separate timestamps, not aliases of one another, even
though in the normal case they differ by milliseconds."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from src.data_access.state_db.connection import transaction

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
    # design/DECISIONS.md) — additive, defaulted fields so every existing
    # keyword-constructed call site (worker, tests) keeps working
    # unchanged. Mirror DailyNewsScanReport's own same-named fields
    # (daily_news_pipeline.py) for exactly this one feed's most recent
    # tick, closing the gap where items_discovered_last_run/
    # stories_published_last_run alone couldn't distinguish "healthy
    # idempotent rerun" from "silent suppression."
    items_already_seen_last_run: int = 0
    items_deduplicated_last_run: int = 0
    items_suppressed_no_url_last_run: int = 0


@dataclass(frozen=True)
class DailyNewsSourceScanStatus:
    """Source_id-keyed counterpart to DailyNewsFeedScanStatus above — see
    this module's own docstring for why this table exists and how
    last_result_at differs from updated_at."""

    source_id: str
    company_name: str
    last_attempt_at: str | None
    last_result_at: str | None
    last_fetch_success_at: str | None
    last_story_published_at: str | None
    last_failure_code: str | None
    last_http_status: int | None
    last_request_duration_ms: float | None
    items_discovered_last_run: int
    stories_published_last_run: int
    items_already_seen_last_run: int
    items_deduplicated_last_run: int
    items_suppressed_no_url_last_run: int
    updated_at: str


@dataclass(frozen=True)
class DailyNewsWorkerStatus:
    worker_key: str
    last_tick_started_at: str | None
    last_tick_completed_at: str | None
    last_reconciliation_at: str | None
    last_failure_code: str | None
    updated_at: str


def _row_to_feed_status(row: sqlite3.Row) -> DailyNewsFeedScanStatus:
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


def get_feed_status(conn: sqlite3.Connection, company_name: str) -> DailyNewsFeedScanStatus | None:
    row = conn.execute(
        "SELECT * FROM daily_news_scan_status WHERE company_name = ?", (company_name,),
    ).fetchone()
    return _row_to_feed_status(row) if row is not None else None


def get_all_feed_statuses(conn: sqlite3.Connection) -> dict[str, DailyNewsFeedScanStatus]:
    rows = conn.execute("SELECT * FROM daily_news_scan_status").fetchall()
    return {row["company_name"]: _row_to_feed_status(row) for row in rows}


def upsert_feed_status(conn: sqlite3.Connection, status: DailyNewsFeedScanStatus) -> None:
    """Insert-or-replace by `company_name` — the worker always writes the
    complete, current record for that feed in one call; there is no
    partial-field update."""
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO daily_news_scan_status (
                company_name, last_attempt_at, last_fetch_success_at, last_story_published_at,
                last_failure_code, items_discovered_last_run, stories_published_last_run, updated_at,
                items_already_seen_last_run, items_deduplicated_last_run, items_suppressed_no_url_last_run
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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


def _row_to_source_status(row: sqlite3.Row) -> DailyNewsSourceScanStatus:
    return DailyNewsSourceScanStatus(
        source_id=row["source_id"],
        company_name=row["company_name"],
        last_attempt_at=row["last_attempt_at"],
        last_result_at=row["last_result_at"],
        last_fetch_success_at=row["last_fetch_success_at"],
        last_story_published_at=row["last_story_published_at"],
        last_failure_code=row["last_failure_code"],
        last_http_status=row["last_http_status"],
        last_request_duration_ms=row["last_request_duration_ms"],
        items_discovered_last_run=row["items_discovered_last_run"],
        stories_published_last_run=row["stories_published_last_run"],
        items_already_seen_last_run=row["items_already_seen_last_run"],
        items_deduplicated_last_run=row["items_deduplicated_last_run"],
        items_suppressed_no_url_last_run=row["items_suppressed_no_url_last_run"],
        updated_at=row["updated_at"],
    )


def get_source_status(conn: sqlite3.Connection, source_id: str) -> DailyNewsSourceScanStatus | None:
    row = conn.execute(
        "SELECT * FROM daily_news_source_status WHERE source_id = ?", (source_id,),
    ).fetchone()
    return _row_to_source_status(row) if row is not None else None


def get_all_source_statuses(conn: sqlite3.Connection) -> dict[str, DailyNewsSourceScanStatus]:
    rows = conn.execute("SELECT * FROM daily_news_source_status").fetchall()
    return {row["source_id"]: _row_to_source_status(row) for row in rows}


def upsert_source_status(conn: sqlite3.Connection, status: DailyNewsSourceScanStatus) -> None:
    """Insert-or-replace by `source_id` — same one-call, whole-record
    contract as upsert_feed_status above, just source_id-keyed."""
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO daily_news_source_status (
                source_id, company_name, last_attempt_at, last_result_at, last_fetch_success_at,
                last_story_published_at, last_failure_code, last_http_status, last_request_duration_ms,
                items_discovered_last_run, stories_published_last_run, items_already_seen_last_run,
                items_deduplicated_last_run, items_suppressed_no_url_last_run, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (source_id) DO UPDATE SET
                company_name = excluded.company_name,
                last_attempt_at = excluded.last_attempt_at,
                last_result_at = excluded.last_result_at,
                last_fetch_success_at = excluded.last_fetch_success_at,
                last_story_published_at = excluded.last_story_published_at,
                last_failure_code = excluded.last_failure_code,
                last_http_status = excluded.last_http_status,
                last_request_duration_ms = excluded.last_request_duration_ms,
                items_discovered_last_run = excluded.items_discovered_last_run,
                stories_published_last_run = excluded.stories_published_last_run,
                items_already_seen_last_run = excluded.items_already_seen_last_run,
                items_deduplicated_last_run = excluded.items_deduplicated_last_run,
                items_suppressed_no_url_last_run = excluded.items_suppressed_no_url_last_run,
                updated_at = excluded.updated_at
            """,
            (
                status.source_id, status.company_name, status.last_attempt_at, status.last_result_at,
                status.last_fetch_success_at, status.last_story_published_at, status.last_failure_code,
                status.last_http_status, status.last_request_duration_ms,
                status.items_discovered_last_run, status.stories_published_last_run,
                status.items_already_seen_last_run, status.items_deduplicated_last_run,
                status.items_suppressed_no_url_last_run, status.updated_at,
            ),
        )


def _row_to_worker_status(row: sqlite3.Row) -> DailyNewsWorkerStatus:
    return DailyNewsWorkerStatus(
        worker_key=row["worker_key"],
        last_tick_started_at=row["last_tick_started_at"],
        last_tick_completed_at=row["last_tick_completed_at"],
        last_reconciliation_at=row["last_reconciliation_at"],
        last_failure_code=row["last_failure_code"],
        updated_at=row["updated_at"],
    )


def get_worker_status(conn: sqlite3.Connection, worker_key: str = WORKER_STATUS_KEY) -> DailyNewsWorkerStatus | None:
    row = conn.execute(
        "SELECT * FROM daily_news_worker_status WHERE worker_key = ?", (worker_key,),
    ).fetchone()
    return _row_to_worker_status(row) if row is not None else None


def upsert_worker_status(conn: sqlite3.Connection, status: DailyNewsWorkerStatus) -> None:
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO daily_news_worker_status (
                worker_key, last_tick_started_at, last_tick_completed_at, last_reconciliation_at,
                last_failure_code, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
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
