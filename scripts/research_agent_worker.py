"""Standalone, dormant-by-default orchestrator for the Autonomous Research
Agent (design §8.3 identity 2, §11, §12 Phase 4) — modeled on
scripts/radar_worker.py: an always-on loop behind its own master switch
(EDGE_RESEARCH_AGENT_LIVE_ENABLED, default off), never imported by app.py
or any UI page (tests/test_research_agent_worker_scope_guard.py).

This worker performs NO ingestion, scan, fetch, or backfill of any kind.
It only ever considers candidates the existing Radar pipelines already
retrieved AND already stored an excerpt for, and it reads the
authoritative decision back from the packet store — never from the
model's own report.

Since the Agent Observability and Shadow Mode release, work is durable.
A tick enqueues jobs, claims one under a lease, runs it, and records the
outcome; a crash mid-session leaves a leased job whose lease expires and
is reclaimed, rather than losing the work or silently redoing it. Jobs
are keyed on (candidate, candidate version, policy version, mode), so
replaying a tick is a no-op rather than a duplicate.

Nothing here can write outside the agent tables. The mode is capped at
`shadow` for this release (src/logic/agent_mode.py), the publish-mode
writes are unwired and guarded (src/logic/agent_write_guard.py), and the
effective decision recorded for every evaluation is NO_ACTION.

Run with:  python -m scripts.research_agent_worker
"""
from __future__ import annotations

import asyncio
import os
import shutil
import signal
import socket
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from src.config.issuer_registry import get_all_issuers
from src.config.settings import Settings, get_settings
from src.data_access import backend_factory
from src.logic import agent_mode, agent_scheduler
from src.logic.publication_policy import POLICY_VERSION
from src.mcp_agent import agent_session, packet_store
from src.mcp_agent.contracts import FILING_SOURCE_NAMES, SessionScope
from src.models.models import CandidateSignal, FilingEvent

RETRYABLE_DECISIONS: frozenset[str] = frozenset({"FAILED_RETRIEVAL"})
DEFAULT_INTERVAL_MINUTES = 60
_MIN_INTERVAL_SECONDS = 300

# The agent SDK's CLI. Probed for presence only — never executed here, and
# never passed a credential on its command line (blocker E1).
AGENT_CLI_EXECUTABLE = "claude"

_shutdown_requested = False


def _request_shutdown(signum: int, frame: Any) -> None:
    global _shutdown_requested
    _shutdown_requested = True


def _install_signal_handlers() -> None:
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _request_shutdown)


def _sleep_in_chunks(seconds: float, *, on_tick: Callable[[], None] | None = None) -> None:
    """Sleeps in short slices so shutdown is prompt, and beats the
    heartbeat while it waits — a worker idling between ticks is alive and
    must not be reclaimed as crashed."""
    deadline = time.monotonic() + seconds
    next_beat = time.monotonic() + agent_scheduler.HEARTBEAT_EVERY_SECONDS
    while not _shutdown_requested and time.monotonic() < deadline:
        time.sleep(min(5.0, max(0.0, deadline - time.monotonic())))
        if on_tick is not None and time.monotonic() >= next_beat:
            on_tick()
            next_beat = time.monotonic() + agent_scheduler.HEARTBEAT_EVERY_SECONDS


def worker_instance() -> str:
    """Identifies this process for leases and the single-runner guard.
    Host and pid only — never anything derived from a credential."""
    return f"{socket.gethostname()}:{os.getpid()}"


@dataclass(frozen=True)
class TickReport:
    considered: int = 0
    enqueued_new: int = 0
    enqueued_backlog: int = 0
    started: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    outcomes: tuple[tuple[str, str | None, str | None], ...] = ()
    reclaimed: int = 0
    halted: str = ""
    mode: str = "off"


def _issuer_id_for(filing: FilingEvent, issuers: Sequence[Any], *, resolved: dict[str, str] | None = None) -> str | None:
    """The registry only carries an identifier when one was already known
    statically, which today is true of the EDINET entries alone: DART and
    EDGAR identifiers are resolved lazily and live in the resolver cache.
    So the registry is consulted first and the cache second — checking
    only the registry would leave every EDGAR candidate unresolvable."""
    for issuer in issuers:
        registered = issuer.identifiers.get(filing.source_name)
        if not registered:
            continue
        if filing.source_name == "SEC EDGAR":
            try:
                if int(registered) == int(filing.corp_code):
                    return issuer.issuer_id
            except ValueError:
                continue
        elif registered == filing.corp_code:
            return issuer.issuer_id

    for issuer_id, identifier in (resolved or {}).items():
        if filing.source_name == "SEC EDGAR":
            try:
                if int(identifier) == int(filing.corp_code):
                    return issuer_id
            except (TypeError, ValueError):
                continue
        elif identifier == filing.corp_code:
            return issuer_id
    return None


def resolved_identifiers_for(settings: Settings, source: str, issuers: Sequence[Any]) -> dict[str, str]:
    """issuer_id -> source identifier, from the resolver cache the Radar
    pipelines already populated. Read-only, and a miss is never fatal."""
    tickers = {
        (issuer.primary_ticker or "").strip().upper(): issuer.issuer_id
        for issuer in issuers if (issuer.primary_ticker or "").strip()
    }
    try:
        repo = backend_factory.get_identifier_repository(settings, source)
    except Exception:  # noqa: BLE001 — an unavailable cache resolves nothing, it never halts a tick
        return {}
    out: dict[str, str] = {}
    for lookup_key, issuer_id in tickers.items():
        try:
            record = repo.get_identifier(lookup_key)
        except Exception:  # noqa: BLE001
            continue
        if record is not None and record.identifier:
            out[issuer_id] = record.identifier
    return out


def load_candidates(settings: Settings) -> tuple[list[tuple[CandidateSignal, str, int]], list[str]]:
    """Every candidate the agent could consider, with its source and
    version. Read-only: no candidate is written, ever."""
    rows: list[tuple[CandidateSignal, str, int]] = []
    skipped: list[str] = []
    for source in sorted(FILING_SOURCE_NAMES):
        try:
            repo = backend_factory.get_candidate_repository(settings, source)
            candidates = repo.load_candidates()
        except Exception as exc:  # noqa: BLE001 — a broken store skips the source, never the tick
            skipped.append(f"{source}:load_error:{type(exc).__name__}")
            continue
        for candidate in candidates.values():
            try:
                version = repo.get_candidate_version(candidate.id) or 1
            except Exception:  # noqa: BLE001
                version = 1
            rows.append((candidate, source, version))
    return rows, skipped


def _scope_for(candidate: CandidateSignal, source: str, *, issuers: Sequence[Any], now: datetime,
               resolved: dict[str, str] | None = None) -> SessionScope | None:
    issuer_id = _issuer_id_for(candidate.filing, issuers, resolved=resolved)
    if issuer_id is None:
        return None
    return SessionScope(
        session_id=f"agent-{uuid.uuid4().hex[:12]}", candidate_id=candidate.id, issuer_id=issuer_id,
        source_name=source, seed_document_id=candidate.filing.rcept_no, started_at=now.isoformat(),
    )


def probe_agent_runtime(executable: str = AGENT_CLI_EXECUTABLE) -> str:
    """Presence only. Running the CLI would start a model session, which
    startup must never do — a self-check that costs money or reaches an
    external service is a self-check nobody will leave enabled."""
    return "" if shutil.which(executable) else f"{executable!r} is not on PATH"


def run_self_check(settings: Settings, resolved, *, store=None, probe=probe_agent_runtime):
    """Asks the database what it actually is rather than trusting config."""
    schema_version: int | None = None
    tables: tuple[str, ...] = ()
    owned = False
    if not resolved.is_off:
        try:
            if store is None:
                store = backend_factory.get_agent_store_repository(settings)
                owned = True
            schema_version, tables = store.describe_database()
        except Exception as exc:  # noqa: BLE001 — type only: driver text can carry connection settings
            return agent_scheduler.SelfCheck(False, (f"cannot reach the agent store: {type(exc).__name__}",))
        finally:
            if owned and store is not None:
                store.close()
    return agent_scheduler.run_self_check(
        settings, resolved, schema_version=schema_version, tables=tables, runtime_probe=probe,
    )


def resolve_effective_mode(settings: Settings, store=None):
    """The mode this tick actually runs in: the environment, narrowed by
    the emergency override if one is stored, then capped by the release.
    Read every tick so `agent_control --mode off` takes effect within one
    interval instead of at the next deploy."""
    control = None
    if store is not None:
        try:
            control = store.get_control()
        except Exception:  # noqa: BLE001 — an unreadable control row never widens anything
            control = None
    return agent_mode.resolve_mode(settings, control)


def run_one_tick(
    settings: Settings, *, store, session_runner: Callable[..., Any] = agent_session.run_session,
    now: datetime | None = None, instance: str | None = None, run_id: str | None = None,
    issuers: Sequence[Any] | None = None,
) -> TickReport:
    now = now or datetime.now(timezone.utc)
    instance = instance or worker_instance()
    resolved = resolve_effective_mode(settings, store)
    if resolved.is_off:
        return TickReport(halted=f"mode is off ({resolved.reason})", mode=resolved.mode)

    reclaimed = store.reclaim_expired_leases(now=now.isoformat())

    candidates, skipped = load_candidates(settings)
    plan = agent_scheduler.plan_enqueue(
        candidates, mode=resolved.mode, now=now.isoformat(),
        known_after=(now - timedelta(days=2)).strftime("%Y%m%d"),
    )
    enqueued_new = sum(1 for job in plan.new if store.enqueue_job(job))
    enqueued_backlog = sum(1 for job in plan.backlog if store.enqueue_job(job))

    budget = agent_scheduler.sessions_remaining_today(store, now=now)
    if budget <= 0:
        return TickReport(
            considered=len(candidates), enqueued_new=enqueued_new, enqueued_backlog=enqueued_backlog,
            skipped=tuple(skipped) + plan.skipped, reclaimed=reclaimed, mode=resolved.mode,
            halted=f"daily session cap of {agent_scheduler.MAX_SESSIONS_PER_DAY} reached",
        )

    issuers = get_all_issuers() if issuers is None else issuers
    resolved_ids = resolved_identifiers_for(settings, "SEC EDGAR", issuers)
    by_id = {c.id: (c, source) for c, source, _ in candidates}
    started: list[str] = []
    outcomes: list[tuple[str, str | None, str | None]] = []

    for _ in range(min(agent_scheduler.MAX_SESSIONS_PER_TICK, budget)):
        if _shutdown_requested:
            break
        job = store.claim_job(
            mode=resolved.mode, now=now.isoformat(),
            lease_expires_at=agent_scheduler.lease_until(now), worker_instance=instance,
        )
        if job is None:
            break
        found = by_id.get(job.candidate_id)
        if found is None:
            store.finish_job(job.job_id, state="dead", now=now.isoformat(),
                             last_error_code="candidate_missing", expected_worker=instance)
            outcomes.append((job.candidate_id, None, "dead:candidate_missing"))
            continue
        candidate, source = found
        scope = _scope_for(candidate, source, issuers=issuers, now=now, resolved=resolved_ids)
        if scope is None:
            store.finish_job(job.job_id, state="dead", now=now.isoformat(),
                             last_error_code="issuer_unresolved", expected_worker=instance)
            outcomes.append((job.candidate_id, None, "dead:issuer_unresolved"))
            continue

        started.append(job.candidate_id)
        if run_id:  # a session is the longest thing a tick does; beat before it
            store.append_audit_events([agent_scheduler.heartbeat_row(
                run_id, worker_instance=instance, at=now.isoformat())])
        try:
            outcome = asyncio.run(session_runner(settings, scope, seed_title=candidate.filing.report_nm))
        except Exception as exc:  # noqa: BLE001 — one failed session never stops the tick
            code = type(exc).__name__  # the type only: an exception's text can carry inputs
            print(f"research_agent_worker: session for {job.candidate_id} failed: {code}")
            _fail_job(store, job, code=code, now=now, instance=instance)
            outcomes.append((job.candidate_id, None, f"error:{code}"))
            continue

        authoritative: str | None = None
        if outcome.packet_id:
            stored = packet_store.load_packet(settings.cache_dir, outcome.packet_id)
            authoritative = stored.decision if stored is not None else None
        if outcome.packet_id is None or authoritative in RETRYABLE_DECISIONS:
            _fail_job(store, job, code=authoritative or "no_packet", now=now, instance=instance)
        else:
            store.finish_job(job.job_id, state="done", now=now.isoformat(), expected_worker=instance)
        outcomes.append((job.candidate_id, outcome.packet_id, authoritative))
        print(f"research_agent_worker: {job.candidate_id} packet={outcome.packet_id} decision={authoritative}")

    return TickReport(
        considered=len(candidates), enqueued_new=enqueued_new, enqueued_backlog=enqueued_backlog,
        started=tuple(started), skipped=tuple(skipped) + plan.skipped, outcomes=tuple(outcomes),
        reclaimed=reclaimed, mode=resolved.mode,
    )


def _fail_job(store, job, *, code: str, now: datetime, instance: str) -> None:
    """A retryable outcome goes back to pending behind a backoff; an
    exhausted one is dead and is never attempted again."""
    retry_at = agent_scheduler.next_attempt_at(job.attempts, now=now)
    store.finish_job(
        job.job_id, state="pending" if retry_at else "dead", now=now.isoformat(),
        last_error_code=code, next_attempt_at=retry_at, expected_worker=instance,
    )


def _validate_live_settings(settings: Settings) -> str | None:
    """Live means durable: agent records have to outlive the container and
    be visible to the dashboard, so a live run requires Postgres. JSON is
    refused outright and SQLite is a local-development backend only."""
    if not settings.research_agent_service_token:
        return "EDGE_RESEARCH_AGENT_SERVICE_TOKEN is not configured"
    backend = (settings.db_backend or "").strip().lower()
    if backend != "postgres":
        return (
            f"a live agent run requires EDGE_DB_BACKEND=postgres for durable agent records; found {backend!r}"
        )
    resolved = agent_mode.resolve_mode(settings)
    if resolved.is_off:
        return f"the agent mode resolves to off ({resolved.reason})"
    return None


def main(argv: list[str] | None = None) -> int:
    _install_signal_handlers()
    ambient = get_settings()
    if not ambient.research_agent_live_enabled:
        print("research_agent_worker: EDGE_RESEARCH_AGENT_LIVE_ENABLED is not enabled — nothing to do. Exiting.")
        return 0
    problem = _validate_live_settings(ambient)
    if problem:
        print(f"research_agent_worker: refusing to start — {problem}")
        return 2

    resolved = agent_mode.resolve_mode(ambient)
    for line in resolved.status_lines():
        print(f"research_agent_worker: {line}")

    check = run_self_check(ambient, resolved)
    if not check.ok:
        print(f"research_agent_worker: refusing to start — {check.summary}")
        return 2

    store = backend_factory.get_agent_store_repository(ambient)
    instance = worker_instance()
    claim = agent_scheduler.acquire_single_runner(
        store, worker_instance=instance, mode=resolved.mode, now=datetime.now(timezone.utc),
    )
    if claim.reclaimed:
        print(f"research_agent_worker: reclaimed {len(claim.reclaimed)} stale run(s)")
    if not claim.acquired:
        print(f"research_agent_worker: refusing to start — {claim.reason}")
        store.close()
        return 3

    def beat() -> None:
        store.append_audit_events([agent_scheduler.heartbeat_row(
            claim.run_id or "", worker_instance=instance, at=datetime.now(timezone.utc).isoformat())])

    interval_seconds = max(_MIN_INTERVAL_SECONDS, DEFAULT_INTERVAL_MINUTES * 60)
    totals = {"considered": 0, "sessions": 0, "errors": 0}
    status = "completed"
    try:
        while not _shutdown_requested:
            report = run_one_tick(ambient, store=store, run_id=claim.run_id, instance=instance)
            totals["considered"] += report.considered
            totals["sessions"] += len(report.started)
            totals["errors"] += sum(1 for _, _, d in report.outcomes if (d or "").startswith("error:"))
            print(
                f"research_agent_worker: tick mode={report.mode} considered={report.considered} "
                f"enqueued={report.enqueued_new}+{report.enqueued_backlog} started={len(report.started)} "
                f"reclaimed={report.reclaimed}" + (f" halted={report.halted}" if report.halted else "")
            )
            beat()
            if _shutdown_requested:
                break
            _sleep_in_chunks(interval_seconds, on_tick=beat)
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        print(f"research_agent_worker: loop failed: {type(exc).__name__}")
    finally:
        store.complete_run(
            claim.run_id, status=status, completed_at=datetime.now(timezone.utc).isoformat(),
            considered=totals["considered"], sessions=totals["sessions"], errors=totals["errors"],
        )
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
