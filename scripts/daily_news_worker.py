"""Daily News autonomous worker (design/DECISIONS.md) — the standalone,
continuous counterpart to scripts/radar_worker.py for the Daily News
pipeline. Reuses daily_news_pipeline.run_discovery(), the registered
PILOT_FEEDS, the URL quality gate, canonicalization, deduplication, and
extractive-only grounding entirely unchanged — this file adds no new
fetch/parse/summarization logic of its own, only scheduling, per-feed
isolation, concurrency safety, and internal health/status persistence.

STANDALONE ENTRY POINT ONLY. Not imported by app.py, any UI page, or any
normal Daily News entry point. Run (once separately approved for a real
deployment) as:

    .venv/bin/python -m scripts.daily_news_worker

Master switch: EDGE_DAILY_NEWS_LIVE_SCAN_ENABLED
(Settings.daily_news_live_scan_enabled), default false. If unset/false,
main() prints a clear, safe message and exits 0 immediately — a no-op,
not an error — without scanning, writing status, or opening a live
database connection.

Backend: EDGE_DAILY_NEWS_WORKER_DB_BACKEND
(Settings.daily_news_worker_db_backend) must be exactly "postgres" for
live mode — "json"/"sqlite"/unset/blank/anything else is a hard,
sanitized startup failure (stricter than scripts/radar_worker.py, which
also permits "sqlite" for a deployed worker). SQLite
(EDGE_DAILY_NEWS_WORKER_STATE_DB_PATH /
Settings.daily_news_worker_state_db_path) exists only so a direct, local
unit/integration-style test can construct its own Settings and call the
tick logic (run_one_tick/_run_tick_body) without ever going through
main()'s live-mode gate — that gate itself never accepts it. A real
deployed worker must use Postgres
(EDGE_DAILY_NEWS_WORKER_STATE_DB_URL) — see design/RADAR_WORKER_DEPLOYMENT.md
for the analogous Radar deployment discussion; the same "SQLite is
local/test-only, a separate dashboard+worker pair needs a real shared
database" reasoning applies here. These are deliberately separate
settings from the ordinary EDGE_DB_BACKEND/EDGE_STATE_DB_URL pair the
dashboard may also have configured, so a dashboard secrets
misconfiguration can never make this worker (or vice versa) silently
point at the wrong database.

Per-feed isolation: each registered feed in feed_registry.PILOT_FEEDS is
discovered inside its own try/except per tick, by calling
daily_news_pipeline.run_discovery() once per feed (feed_sources=(source,))
rather than once for the whole batch — this gives per-feed status
granularity without needing any change to run_discovery() itself, whose
own DailyNewsScanReport only aggregates totals across whatever
feed_sources tuple it's given. One feed's exception is caught, recorded
in that feed's own DailyNewsFeedScanStatus.last_failure_code (only
`type(exc).__name__` — never a raw exception message, matching this
codebase's existing BackendConfigurationError/ProviderScanStatus
discipline), and never prevents the other feeds from being attempted in
the same tick. A tick-level failure (e.g. the shared repository/
scan-status connection itself failing) is caught one level up, in
main()'s own loop, so it can never kill future ticks either.

Concurrency safety: a single, worker-level, non-blocking, session-level
Postgres advisory lock (pg_try_advisory_lock/pg_advisory_unlock) —
acquired immediately before a tick's work and released in a finally
block at the end of that same tick. If another instance already holds
it, this tick is skipped entirely (no repository or status mutation) and
the loop proceeds straight to its next sleep. A crashed or disconnected
holder's session-level lock is released automatically by Postgres, so a
failed run self-heals on the very next tick with no separate
staleness-timeout logic needed — the same self-healing property
scripts/radar_worker.py's own fcntl file lock already has. Advisory lock
IDs are reserved per worker role in this codebase; any future worker
must choose its own distinct constant.

Reliability: the normal 30-minute (EDGE_DAILY_NEWS_SCAN_INTERVAL_MINUTES)
tick simply re-runs the same idempotent discovery pass. Once every
EDGE_DAILY_NEWS_RECONCILIATION_INTERVAL_HOURS (default 24), a separate
reconciliation health pass runs: it does NOT re-query feeds for older
history (RSS/Atom exposes no lookback/date-range query — a feed only
ever returns its own most-recent items, so there is no way to "search
back" further), it only checks each feed's own
`last_fetch_success_at` against
EDGE_DAILY_NEWS_RECONCILIATION_STALENESS_HOURS (default 72) and prints a
warning for any feed that has not had a successful fetch/parse in that
window. A healthy feed that simply has published no new story is never
flagged — that check deliberately uses `last_fetch_success_at`, never
`last_story_published_at`.

Graceful shutdown: SIGTERM/SIGINT set a flag checked between ticks (the
same _sleep_in_chunks() responsive-sleep pattern as
scripts/radar_worker.py) — an in-progress tick is never interrupted
mid-call, but no new tick starts once the flag is set.

No worker status or controls are exposed anywhere in the public UI,
sidebar, or the hidden daily_news_admin.py page in this workstream —
every status field this file writes is read back only by tests and any
future, separately-approved internal tooling.
"""
from __future__ import annotations

import dataclasses
import signal
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

import psycopg

from src.config.settings import Settings, get_settings
from src.data_access.daily_news import daily_news_backend, daily_news_pipeline
from src.data_access.daily_news.daily_news_backend import (
    DailyNewsScanStatusRepositoryProtocol,
    PostgresDailyNewsScanStatusRepository,
)
from src.data_access.daily_news.feed_registry import DailyNewsFeedSource, PILOT_FEEDS
from src.data_access.state_db.daily_news_scan_status_repository import (
    WORKER_STATUS_KEY,
    DailyNewsFeedScanStatus,
    DailyNewsWorkerStatus,
)

_MIN_INTERVAL_SECONDS = 60  # defense-in-depth floor, regardless of a misconfigured tiny interval value

# Advisory lock IDs are reserved per worker role in this codebase — this
# constant is reserved for the Daily News worker only; any future worker
# sharing the same Postgres instance must choose its own distinct value.
_DAILY_NEWS_WORKER_ADVISORY_LOCK_KEY = 0x454556414E455753


class WorkerConfigurationError(Exception):
    """Raised at startup for a sanitized, fatal configuration problem —
    never a raw exception, DSN, or credential."""


_shutdown_requested = False


def _handle_shutdown_signal(signum: int, frame: object) -> None:
    global _shutdown_requested
    _shutdown_requested = True


def _install_signal_handlers() -> None:
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)


def _build_worker_settings(ambient: Settings) -> Settings:
    """Constructs the one explicit Settings object every tick this worker
    performs actually uses — never the ambient `ambient` object directly.
    Live mode requires db_backend == "postgres" exactly; "json" and
    "sqlite" are both rejected here with a sanitized error (SQLite is
    still usable for direct, local tick-level tests that call
    run_one_tick()/run_discovery() without ever going through this
    function — see module docstring)."""
    backend = ambient.daily_news_worker_db_backend
    if backend != "postgres":
        raise WorkerConfigurationError(
            'EDGE_DAILY_NEWS_WORKER_DB_BACKEND must be exactly "postgres" for live Daily News '
            f"worker mode (got {backend!r}). JSON and SQLite are not supported for a live, "
            "continuously-scheduled deployment of this worker."
        )
    if not ambient.daily_news_worker_state_db_url:
        raise WorkerConfigurationError(
            "EDGE_DAILY_NEWS_WORKER_STATE_DB_URL is required when EDGE_DAILY_NEWS_WORKER_DB_BACKEND=postgres."
        )
    return dataclasses.replace(
        ambient,
        db_backend=backend,
        state_db_url=ambient.daily_news_worker_state_db_url,
    )


@contextmanager
def _advisory_lock(conn: psycopg.Connection) -> Iterator[bool]:
    """Yields True if the worker-level advisory lock was acquired (caller
    should proceed), False if another process already holds it (caller
    should skip this tick entirely) — never blocks waiting for it.
    Released explicitly in a finally block; a crashed or disconnected
    holder's session-level lock is released automatically by Postgres, so
    no stale-lock cleanup is ever needed."""
    row = conn.execute(
        "SELECT pg_try_advisory_lock(%s) AS acquired", (_DAILY_NEWS_WORKER_ADVISORY_LOCK_KEY,),
    ).fetchone()
    conn.commit()
    if not row["acquired"]:
        yield False
        return
    try:
        yield True
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (_DAILY_NEWS_WORKER_ADVISORY_LOCK_KEY,))
        conn.commit()


def _lock_connection(scan_status_repository: DailyNewsScanStatusRepositoryProtocol) -> psycopg.Connection | None:
    """Returns the underlying psycopg connection when the scan-status
    repository is Postgres-backed, else None. SQLite-backed repositories
    (direct tick-level tests only — never a live worker) skip advisory
    locking entirely: pg_try_advisory_lock has no SQLite equivalent, and
    a single-process, single-connection test needs no concurrency
    guard."""
    if isinstance(scan_status_repository, PostgresDailyNewsScanStatusRepository):
        return scan_status_repository.conn
    return None


def _run_feed_tick(
    feed_source: DailyNewsFeedSource,
    worker_settings: Settings,
    repository,
    scan_status_repository: DailyNewsScanStatusRepositoryProtocol,
) -> None:
    """One feed's own discovery attempt this tick, fully isolated: an
    exception here is caught and recorded only in that feed's own status
    row, never raised to the caller. Calls run_discovery() scoped to
    exactly this one feed (feed_sources=(feed_source,)) so this file
    never needs to change run_discovery()'s own aggregate-only
    DailyNewsScanReport shape to get per-feed granularity."""
    company_name = feed_source.company_name
    started_at = datetime.now(timezone.utc).isoformat()
    previous = scan_status_repository.get_feed_status(company_name)

    try:
        report = daily_news_pipeline.run_discovery(
            worker_settings.cache_dir, feed_sources=(feed_source,), daily_news_repository=repository,
        )
    except Exception as exc:  # noqa: BLE001 — one feed's failure must never stop the others
        now = datetime.now(timezone.utc).isoformat()
        scan_status_repository.upsert_feed_status(DailyNewsFeedScanStatus(
            company_name=company_name,
            last_attempt_at=started_at,
            last_fetch_success_at=previous.last_fetch_success_at if previous else None,
            last_story_published_at=previous.last_story_published_at if previous else None,
            last_failure_code=type(exc).__name__,
            items_discovered_last_run=0,
            stories_published_last_run=0,
            updated_at=now,
        ))
        print(f"{company_name}: tick failed ({type(exc).__name__}) — skipped.")
        return

    now = datetime.now(timezone.utc).isoformat()
    failure_code = report.source_failures.get(company_name)
    fetch_succeeded = failure_code is None
    last_story_published_at = (
        now if report.stories_published > 0 else (previous.last_story_published_at if previous else None)
    )

    scan_status_repository.upsert_feed_status(DailyNewsFeedScanStatus(
        company_name=company_name,
        last_attempt_at=started_at,
        last_fetch_success_at=now if fetch_succeeded else (previous.last_fetch_success_at if previous else None),
        last_story_published_at=last_story_published_at,
        last_failure_code=failure_code,
        items_discovered_last_run=report.items_discovered,
        stories_published_last_run=report.stories_published,
        updated_at=now,
    ))
    if fetch_succeeded:
        print(
            f"{company_name}: ok — items_discovered={report.items_discovered} "
            f"stories_published={report.stories_published}"
        )
    else:
        print(f"{company_name}: fetch failed ({failure_code}).")


def _reconciliation_due(worker_status: DailyNewsWorkerStatus | None, worker_settings: Settings) -> bool:
    if worker_status is None or not worker_status.last_reconciliation_at:
        return True
    last = datetime.fromisoformat(worker_status.last_reconciliation_at)
    return datetime.now(timezone.utc) - last >= timedelta(hours=worker_settings.daily_news_reconciliation_interval_hours)


def _run_reconciliation_pass(
    worker_settings: Settings, scan_status_repository: DailyNewsScanStatusRepositoryProtocol,
) -> None:
    """Feed-health reconciliation, not a backfill: RSS/Atom feeds expose
    no lookback/date-range query, so this never re-fetches further back
    in time than the normal tick already does. It only flags a feed
    whose own `last_fetch_success_at` is older than the configured
    staleness threshold — a healthy feed that has simply published no
    new story is never flagged, since that check deliberately never
    reads `last_story_published_at`."""
    staleness_threshold = timedelta(hours=worker_settings.daily_news_reconciliation_staleness_hours)
    now = datetime.now(timezone.utc)
    for feed_source in PILOT_FEEDS:
        status = scan_status_repository.get_feed_status(feed_source.company_name)
        if status is None or not status.last_fetch_success_at:
            print(f"{feed_source.company_name}: reconciliation — no successful fetch recorded yet.")
            continue
        last_success = datetime.fromisoformat(status.last_fetch_success_at)
        if now - last_success >= staleness_threshold:
            print(
                f"{feed_source.company_name}: RECONCILIATION WARNING — no successful fetch/parse in "
                f"{worker_settings.daily_news_reconciliation_staleness_hours}+ hours "
                f"(last success: {status.last_fetch_success_at})."
            )


def _run_tick_body(
    worker_settings: Settings, scan_status_repository: DailyNewsScanStatusRepositoryProtocol,
) -> None:
    started_at = datetime.now(timezone.utc).isoformat()
    worker_status = scan_status_repository.get_worker_status()
    repository = daily_news_backend.get_daily_news_repository(worker_settings)

    for feed_source in PILOT_FEEDS:
        _run_feed_tick(feed_source, worker_settings, repository, scan_status_repository)

    last_reconciliation_at = worker_status.last_reconciliation_at if worker_status else None
    if _reconciliation_due(worker_status, worker_settings):
        _run_reconciliation_pass(worker_settings, scan_status_repository)
        last_reconciliation_at = datetime.now(timezone.utc).isoformat()

    completed_at = datetime.now(timezone.utc).isoformat()
    scan_status_repository.upsert_worker_status(DailyNewsWorkerStatus(
        worker_key=WORKER_STATUS_KEY,
        last_tick_started_at=started_at,
        last_tick_completed_at=completed_at,
        last_reconciliation_at=last_reconciliation_at,
        last_failure_code=None,
        updated_at=completed_at,
    ))


def run_one_tick(worker_settings: Settings, scan_status_repository: DailyNewsScanStatusRepositoryProtocol) -> None:
    """Runs exactly one tick: acquires the worker-level advisory lock when
    the scan-status repository is Postgres-backed (skip entirely, no
    mutation, if another instance already holds it), then discovers
    across every registered feed and runs the reconciliation pass if due.
    Never loops, never sleeps, never checks the shutdown flag itself —
    the only function tests should call directly; main()'s own
    while-loop is not meant to be unit-tested as a whole."""
    conn = _lock_connection(scan_status_repository)
    if conn is None:
        _run_tick_body(worker_settings, scan_status_repository)
        return

    with _advisory_lock(conn) as acquired:
        if not acquired:
            print("Daily News worker: skipped this tick — lock held by another instance.")
            return
        _run_tick_body(worker_settings, scan_status_repository)


def _sleep_in_chunks(total_seconds: int, chunk_seconds: int = 5) -> None:
    """Sleeps in small increments so a shutdown signal is noticed
    promptly rather than only after the full interval elapses."""
    elapsed = 0
    while elapsed < total_seconds and not _shutdown_requested:
        time.sleep(min(chunk_seconds, total_seconds - elapsed))
        elapsed += chunk_seconds


def main(argv: list[str] | None = None) -> int:
    _install_signal_handlers()
    ambient = get_settings()

    if not ambient.daily_news_live_scan_enabled:
        print("EDGE_DAILY_NEWS_LIVE_SCAN_ENABLED is not enabled — nothing to do. Exiting.")
        return 0

    try:
        worker_settings = _build_worker_settings(ambient)
    except WorkerConfigurationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    try:
        scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)
    except Exception as exc:  # noqa: BLE001 — never leak a raw connection/config error
        print(f"ERROR: could not construct the Daily News scan-status repository ({type(exc).__name__}).", file=sys.stderr)
        return 1

    interval_seconds = max(_MIN_INTERVAL_SECONDS, ambient.daily_news_scan_interval_minutes * 60)
    print(
        f"Daily News worker starting — backend={worker_settings.db_backend} "
        f"interval_minutes={ambient.daily_news_scan_interval_minutes}"
    )

    while not _shutdown_requested:
        try:
            run_one_tick(worker_settings, scan_status_repository)
        except Exception as exc:  # noqa: BLE001 — a tick failure must never kill future ticks
            print(f"Daily News worker: tick failed unexpectedly ({type(exc).__name__}) — will retry next interval.")
        if _shutdown_requested:
            break
        _sleep_in_chunks(interval_seconds)

    print("Daily News worker shutting down (signal received).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
