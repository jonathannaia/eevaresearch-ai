"""Durable-State Phase 4M-0 — the standalone, continuous autonomous
Radar worker.

STANDALONE ENTRY POINT ONLY. Not imported by app.py, any UI page, or
any normal scan entry point — the Streamlit dashboard never performs a
recurring external scan on page render, with or without this file
existing. This process is meant to run separately from the dashboard,
on its own always-on worker host or scheduled-job platform (see
design/RADAR_WORKER_DEPLOYMENT.md) — Streamlit Community Cloud hosts
the dashboard, never this loop.

Run (later, once separately approved) as:
    .venv/bin/python -m scripts.radar_worker

Master switch: EDGE_RADAR_LIVE_SCAN_ENABLED (Settings.radar_live_scan_enabled),
default false. If unset/false, main() prints a clear, safe message and
exits 0 immediately — a no-op, not an error — so accidentally starting
this process without the flag is inert.

Backend: EDGE_RADAR_WORKER_DB_BACKEND (Settings.radar_worker_db_backend)
must be exactly "sqlite" or "postgres" — "json" (or unset/blank/
anything else) is a hard, sanitized startup failure. SQLite
(EDGE_RADAR_WORKER_STATE_DB_PATH) is local/test-only; a real deployed
worker running separately from the Streamlit dashboard must use
Postgres (EDGE_RADAR_WORKER_STATE_DB_URL) — see
design/RADAR_WORKER_DEPLOYMENT.md. These are deliberately separate
settings from the ordinary EDGE_DB_BACKEND/EDGE_STATE_DB_URL pair the
dashboard may also have configured, so a dashboard secrets
misconfiguration can never make this worker (or vice versa) silently
point at the wrong database.

This worker NEVER resolves an issuer identifier itself — see
scripts/resolve_tracked_identifiers.py, the one explicit, manual
bootstrap step an operator runs separately, before starting this
process (and again, occasionally, whenever a new tracked issuer is
added). An issuer with no already-resolved identifier is simply skipped
for that tick — the exact behavior scan_service.scan() already has
("... CIK not resolved — run cik_resolver first.", recorded as a
warning, not an error) — and counted in that provider's own
ProviderScanStatus.skipped_unresolved_count.

Provider isolation: each of EDGAR/DART/EDINET is scanned inside its own
try/except per tick — one provider's exception (a missing credential, a
network failure, anything) is caught, recorded in that provider's own
ProviderScanStatus.failure_code (only `type(exc).__name__` — never a raw
exception message, DSN, or credential), and never prevents the other
configured providers from scanning in the same tick.

PUBLISHED-safety: this worker's own settings object always forces
edgar_auto_publish_enabled=False, structurally, regardless of what
EDGE_EDGAR_AUTO_PUBLISH_ENABLED is set to in this process's own
environment — see _build_worker_settings()'s own docstring for why.
This worker never imports review_actions or signal_promotion, and never
constructs a SignalRepository — Signals stays strictly PUBLISHED-only,
entirely gated by the existing human review action, unaffected by
anything in this file. It also never calls
scripts.resolve_tracked_identifiers or any source client/resolver
directly — only each source's own existing, unmodified `run_scan()`.

Locking: a per-(provider, backend) advisory file lock (stdlib `fcntl` —
POSIX only, matching this project's Streamlit Community Cloud + Unix
worker deployment target) held for the duration of that provider's own
scan attempt. A non-blocking lock attempt means an overlapping scan
attempt is *skipped* for that tick, never queued or duplicated. flock()
is released automatically by the OS if the holding process dies for any
reason (crash, kill -9, ...), so a failed run self-heals on the very
next tick with no separate staleness-timeout logic needed.

Graceful shutdown: SIGTERM/SIGINT set a flag checked between providers
and between ticks — an in-progress provider's own scan call is never
interrupted mid-call (that could leave a worse partial-write state than
letting it finish), but the loop will not start a new provider or a new
tick once the flag is set.
"""
from __future__ import annotations

import dataclasses
import fcntl
import signal
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from src.config.settings import Settings, get_settings
from src.data_access import backend_factory
from src.data_access.dart import radar_service as dart_radar_service
from src.data_access.edgar import edgar_service
from src.data_access.edinet import edinet_service
from src.data_access.state_db.scan_status_repository import ProviderScanStatus

# Duck-typed deliberately: postgres_state_db.scan_status_repository.ProviderScanStatus
# has an identical field shape, and each repository's own upsert_scan_status()
# reads attributes by name, not by isinstance check — so this one dataclass
# is used uniformly for both backends. See scan_status_repository.py's own
# docstring for the shared shape.

_PROVIDERS: tuple[str, ...] = ("edgar", "dart", "edinet")
_SOURCE_DISPLAY_NAMES = {"edgar": "SEC EDGAR", "dart": "OpenDART / DART", "edinet": "EDINET"}
_SERVICE_MODULES = {"edgar": edgar_service, "dart": dart_radar_service, "edinet": edinet_service}

_LOCK_DIR = Path(tempfile.gettempdir()) / "eevaresearch-radar-worker-locks"
_MIN_INTERVAL_SECONDS = 60  # defense-in-depth floor, regardless of a misconfigured tiny interval value


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


@contextmanager
def _provider_lock(lock_key: str) -> Iterator[bool]:
    """Yields True if the lock was acquired (caller should proceed),
    False if another process already holds it (caller should skip this
    tick for this provider) — never blocks waiting for it."""
    _LOCK_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = _LOCK_DIR / f"{lock_key}.lock"
    fh = open(lock_path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        yield False
        return
    try:
        yield True
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def _build_worker_settings(ambient: Settings) -> Settings:
    """Constructs the ONE explicit Settings object every scan this
    worker performs actually uses — never the ambient `ambient` object
    directly, for two structural safety reasons: (1) db_backend/
    state_db_path/state_db_url come from the dedicated
    EDGE_RADAR_WORKER_* fields, never the ordinary EDGE_DB_BACKEND/
    EDGE_STATE_DB_URL pair the dashboard might also have set;
    (2) edgar_auto_publish_enabled is forced False here, structurally,
    regardless of EDGE_EDGAR_AUTO_PUBLISH_ENABLED's real value in this
    process's environment — this worker must never be able to
    autonomously set a candidate to PUBLISHED, and that pre-existing,
    separate feature flag is the one code path in this codebase that
    could otherwise do so (see edgar_pipeline.run_pipeline's own
    auto_publish_enabled parameter and design/DECISIONS.md's own record
    of this exact finding)."""
    backend = ambient.radar_worker_db_backend
    if backend not in ("sqlite", "postgres"):
        raise WorkerConfigurationError(
            'EDGE_RADAR_WORKER_DB_BACKEND must be exactly "sqlite" or "postgres" for continuous '
            f"worker mode (got {backend!r}). JSON is not supported for a separate dashboard+worker pair."
        )
    if backend == "sqlite" and not ambient.radar_worker_state_db_path:
        raise WorkerConfigurationError(
            "EDGE_RADAR_WORKER_STATE_DB_PATH is required when EDGE_RADAR_WORKER_DB_BACKEND=sqlite."
        )
    if backend == "postgres" and not ambient.radar_worker_state_db_url:
        raise WorkerConfigurationError(
            "EDGE_RADAR_WORKER_STATE_DB_URL is required when EDGE_RADAR_WORKER_DB_BACKEND=postgres."
        )

    return dataclasses.replace(
        ambient,
        db_backend=backend,
        state_db_path=ambient.radar_worker_state_db_path,
        state_db_url=ambient.radar_worker_state_db_url,
        edgar_auto_publish_enabled=False,
    )


def _record_failure(scan_status_repo, display_source: str, previous, started_at: str, failure_code: str) -> None:
    """Preserves the last known-good cursor/last_successful_at/counts
    from `previous` (if any) — a failed tick must never erase the
    provider's prior progress, only record that this attempt failed."""
    now = datetime.now(timezone.utc).isoformat()
    scan_status_repo.upsert_scan_status(ProviderScanStatus(
        provider=display_source,
        cursor_value=previous.cursor_value if previous else None,
        started_at=started_at,
        completed_at=now,
        last_successful_at=previous.last_successful_at if previous else None,
        items_discovered=previous.items_discovered if previous else 0,
        candidates_created=previous.candidates_created if previous else 0,
        skipped_unresolved_count=previous.skipped_unresolved_count if previous else 0,
        failure_code=failure_code,
        updated_at=now,
    ))


def _run_provider_tick(provider_key: str, worker_settings: Settings, scan_status_repo) -> None:
    display_source = _SOURCE_DISPLAY_NAMES[provider_key]
    service_module = _SERVICE_MODULES[provider_key]
    started_at = datetime.now(timezone.utc).isoformat()

    with _provider_lock(f"{provider_key}-{worker_settings.db_backend}") as acquired:
        if not acquired:
            print(f"{provider_key.upper()}: skipped this tick — another scan for this provider is already in progress.")
            return

        previous_status = scan_status_repo.get_scan_status(display_source)

        try:
            candidate_repository = backend_factory.get_candidate_repository(worker_settings, display_source)
            report = service_module.run_scan(worker_settings, candidate_repository=candidate_repository)
        except Exception as exc:  # noqa: BLE001 — one provider's failure must never stop the others
            _record_failure(scan_status_repo, display_source, previous_status, started_at, type(exc).__name__)
            print(f"{provider_key.upper()}: scan failed ({type(exc).__name__}) — skipped this tick.")
            return

        completed_at = datetime.now(timezone.utc).isoformat()
        cursor_value = getattr(report, "end_de", None) or getattr(report, "end_date", None)
        skipped_unresolved = sum(1 for w in getattr(report, "warnings", ()) if "not resolved" in w)

        scan_status_repo.upsert_scan_status(ProviderScanStatus(
            provider=display_source,
            cursor_value=cursor_value,
            started_at=started_at,
            completed_at=completed_at,
            last_successful_at=completed_at,
            items_discovered=report.candidates_detected,
            candidates_created=report.candidates_processed,
            skipped_unresolved_count=skipped_unresolved,
            failure_code=None,
            updated_at=completed_at,
        ))
        print(
            f"{provider_key.upper()}: ok — candidates_detected={report.candidates_detected} "
            f"candidates_processed={report.candidates_processed} skipped_unresolved={skipped_unresolved}"
        )


def run_one_tick(worker_settings: Settings, scan_status_repo) -> None:
    """Runs exactly one scan attempt per provider, in order, each fully
    isolated from the others' exceptions. Never loops, never sleeps,
    never checks the shutdown flag itself — the only function tests
    should call directly; main()'s own while-loop is not meant to be
    unit-tested as a whole."""
    for provider_key in _PROVIDERS:
        _run_provider_tick(provider_key, worker_settings, scan_status_repo)


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

    if not ambient.radar_live_scan_enabled:
        print("EDGE_RADAR_LIVE_SCAN_ENABLED is not enabled — nothing to do. Exiting.")
        return 0

    try:
        worker_settings = _build_worker_settings(ambient)
    except WorkerConfigurationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    try:
        scan_status_repo = backend_factory.get_scan_status_repository(worker_settings)
    except Exception as exc:  # noqa: BLE001 — never leak a raw connection/config error
        print(f"ERROR: could not construct the scan-status repository ({type(exc).__name__}).", file=sys.stderr)
        return 1

    interval_seconds = max(_MIN_INTERVAL_SECONDS, ambient.radar_scan_interval_minutes * 60)
    print(
        f"Radar worker starting — backend={worker_settings.db_backend} "
        f"interval_minutes={ambient.radar_scan_interval_minutes}"
    )

    while not _shutdown_requested:
        run_one_tick(worker_settings, scan_status_repo)
        if _shutdown_requested:
            break
        _sleep_in_chunks(interval_seconds)

    print("Radar worker shutting down (signal received).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
