"""Company Discovery Phase 2 — the standalone, continuous worker for the
passive Candidate Ledger. Reuses candidate_pipeline.run_candidate_
discovery_tick() entirely unchanged — this file adds no extraction/
resolution/scoring logic of its own, only scheduling, concurrency
safety, and internal health/status persistence, mirroring
scripts/daily_news_worker.py's own separation of concerns exactly.

STANDALONE ENTRY POINT ONLY. Not imported by app.py, any UI page, or
any normal Company Discovery entry point. Run (once separately approved
for a real deployment) as:

    .venv/bin/python -m scripts.company_discovery_worker

Master switch: EDGE_COMPANY_DISCOVERY_LIVE_ENABLED
(Settings.company_discovery_live_enabled), default false. If unset/
false, main() prints a clear, safe message and exits 0 immediately — a
no-op, not an error — without reading, scoring, writing status, or
opening a live database connection.

Backend: EDGE_COMPANY_DISCOVERY_WORKER_DB_BACKEND
(Settings.company_discovery_worker_db_backend) must be exactly
"postgres" for live mode — "json"/"sqlite"/unset/blank/anything else is
a hard, sanitized startup failure, the same stricter-than-Radar posture
scripts/daily_news_worker.py already established. A real deployed
worker's EDGE_COMPANY_DISCOVERY_WORKER_STATE_DB_URL is an operational
choice pointed at the SAME physical database the dashboard/Radar/Daily
News already use — this worker only ever reads their existing Filing/
CandidateSignal/NewsStory data, never a second copy of it — see
design/COMPANY_DISCOVERY_WORKER_DEPLOYMENT.md.

No network I/O of any kind: this worker never imports `requests`,
`feedparser`, any `*_client.py`, `rss_atom_client`, or any translation-
provider module — see tests/test_company_discovery_scope_guard.py for
the AST-based proof, not just this claim. Every tick is pure
computation over already-persisted data plus its own Postgres
read/write.

Concurrency safety: a single, worker-level, non-blocking, session-level
Postgres advisory lock — same pg_try_advisory_lock/pg_advisory_unlock
pattern as scripts/daily_news_worker.py, with its own distinct reserved
key (see _COMPANY_DISCOVERY_WORKER_ADVISORY_LOCK_KEY below — never
reused from Daily News's or any other worker's own key).

No promotion path exists anywhere in this file or the pipeline it
calls: no write to TrackedCompany/SEED_ISSUERS/any live-monitoring
configuration, ever, in Phase 2.

Graceful shutdown: SIGTERM/SIGINT set a flag checked between ticks, the
same _sleep_in_chunks() responsive-sleep pattern as the other two
workers — an in-progress tick is never interrupted mid-call.
"""
from __future__ import annotations

import dataclasses
import signal
import sys
import time
from contextlib import contextmanager
from typing import Iterator

import psycopg

from src.config.settings import Settings, get_settings
from src.data_access.company_discovery import candidate_pipeline
from src.data_access.company_discovery.company_discovery_backend import (
    CandidateIssuerRepositoryProtocol,
    PostgresCandidateIssuerRepository,
    get_candidate_issuer_repository,
)
from src.data_access.state_db.candidate_issuer_repository import WORKER_STATUS_KEY
from src.models.company_discovery_models import CandidateWorkerStatus

_MIN_INTERVAL_SECONDS = 60  # defense-in-depth floor, regardless of a misconfigured tiny interval value

# Advisory lock IDs are reserved per worker role in this codebase — this
# constant ("COMPDISC" ASCII-encoded as a 64-bit integer) is reserved
# for the Company Discovery worker only; distinct from Daily News's own
# 0x454556414E455753 key. Any future worker sharing the same Postgres
# instance must choose its own distinct value.
_COMPANY_DISCOVERY_WORKER_ADVISORY_LOCK_KEY = 0x434F4D5044495343


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
    """Live mode requires db_backend == "postgres" exactly — "json" and
    "sqlite" are both rejected here with a sanitized error. SQLite
    remains usable only for direct, local test calls to run_one_tick()
    that never go through this function."""
    backend = ambient.company_discovery_worker_db_backend
    if backend != "postgres":
        raise WorkerConfigurationError(
            'EDGE_COMPANY_DISCOVERY_WORKER_DB_BACKEND must be exactly "postgres" for live Company '
            f"Discovery worker mode (got {backend!r}). JSON and SQLite are not supported for a live, "
            "continuously-scheduled deployment of this worker."
        )
    if not ambient.company_discovery_worker_state_db_url:
        raise WorkerConfigurationError(
            "EDGE_COMPANY_DISCOVERY_WORKER_STATE_DB_URL is required when "
            "EDGE_COMPANY_DISCOVERY_WORKER_DB_BACKEND=postgres."
        )
    return dataclasses.replace(
        ambient,
        db_backend=backend,
        state_db_url=ambient.company_discovery_worker_state_db_url,
    )


@contextmanager
def _advisory_lock(conn: psycopg.Connection) -> Iterator[bool]:
    row = conn.execute(
        "SELECT pg_try_advisory_lock(%s) AS acquired", (_COMPANY_DISCOVERY_WORKER_ADVISORY_LOCK_KEY,),
    ).fetchone()
    conn.commit()
    if not row["acquired"]:
        yield False
        return
    try:
        yield True
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (_COMPANY_DISCOVERY_WORKER_ADVISORY_LOCK_KEY,))
        conn.commit()


def _lock_connection(repository: CandidateIssuerRepositoryProtocol) -> psycopg.Connection | None:
    if isinstance(repository, PostgresCandidateIssuerRepository):
        return repository.conn
    return None


def _run_tick_body(worker_settings: Settings, repository: CandidateIssuerRepositoryProtocol) -> None:
    from datetime import datetime, timezone

    started_at = datetime.now(timezone.utc).isoformat()
    try:
        report = candidate_pipeline.run_candidate_discovery_tick(
            worker_settings, repository, stale_days=worker_settings.company_discovery_stale_days,
        )
    except Exception as exc:  # noqa: BLE001 — a tick failure must never kill future ticks or leak details
        completed_at = datetime.now(timezone.utc).isoformat()
        repository.upsert_worker_status(CandidateWorkerStatus(
            worker_key=WORKER_STATUS_KEY, last_tick_started_at=started_at, last_tick_completed_at=completed_at,
            last_failure_code=type(exc).__name__, evidence_created_last_run=0,
            candidates_created_last_run=0, candidates_quarantined_last_run=0, updated_at=completed_at,
        ))
        print(f"Company Discovery worker: tick failed ({type(exc).__name__}).")
        return

    completed_at = datetime.now(timezone.utc).isoformat()
    repository.upsert_worker_status(CandidateWorkerStatus(
        worker_key=WORKER_STATUS_KEY, last_tick_started_at=started_at, last_tick_completed_at=completed_at,
        last_failure_code=None, evidence_created_last_run=report.evidence_created,
        candidates_created_last_run=report.candidates_created,
        candidates_quarantined_last_run=report.candidates_quarantined, updated_at=completed_at,
    ))
    print(
        f"Company Discovery worker: ok — evidence_created={report.evidence_created} "
        f"candidates_created={report.candidates_created} candidates_quarantined={report.candidates_quarantined} "
        f"candidates_rejected={report.candidates_rejected} candidates_archived={report.candidates_archived}"
    )


def run_one_tick(worker_settings: Settings, repository: CandidateIssuerRepositoryProtocol) -> None:
    """Runs exactly one tick: acquires the worker-level advisory lock
    when Postgres-backed (skip entirely if another instance already
    holds it), then runs the pipeline. Never loops, never sleeps — the
    only function tests should call directly."""
    conn = _lock_connection(repository)
    if conn is None:
        _run_tick_body(worker_settings, repository)
        return

    with _advisory_lock(conn) as acquired:
        if not acquired:
            print("Company Discovery worker: skipped this tick — lock held by another instance.")
            return
        _run_tick_body(worker_settings, repository)


def _sleep_in_chunks(total_seconds: int, chunk_seconds: int = 5) -> None:
    elapsed = 0
    while elapsed < total_seconds and not _shutdown_requested:
        time.sleep(min(chunk_seconds, total_seconds - elapsed))
        elapsed += chunk_seconds


def main(argv: list[str] | None = None) -> int:
    _install_signal_handlers()
    ambient = get_settings()

    if not ambient.company_discovery_live_enabled:
        print("EDGE_COMPANY_DISCOVERY_LIVE_ENABLED is not enabled — nothing to do. Exiting.")
        return 0

    try:
        worker_settings = _build_worker_settings(ambient)
    except WorkerConfigurationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    try:
        repository = get_candidate_issuer_repository(worker_settings)
    except Exception as exc:  # noqa: BLE001 — never leak a raw connection/config error
        print(f"ERROR: could not construct the Company Discovery candidate repository ({type(exc).__name__}).", file=sys.stderr)
        return 1

    interval_seconds = max(_MIN_INTERVAL_SECONDS, ambient.company_discovery_scan_interval_minutes * 60)
    print(
        f"Company Discovery worker starting — backend={worker_settings.db_backend} "
        f"interval_minutes={ambient.company_discovery_scan_interval_minutes}"
    )

    while not _shutdown_requested:
        try:
            run_one_tick(worker_settings, repository)
        except Exception as exc:  # noqa: BLE001 — a tick failure must never kill future ticks
            print(f"Company Discovery worker: tick failed unexpectedly ({type(exc).__name__}) — will retry next interval.")
        if _shutdown_requested:
            break
        _sleep_in_chunks(interval_seconds)

    print("Company Discovery worker shutting down (signal received).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
