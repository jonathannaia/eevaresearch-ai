"""Standalone, dormant-by-default orchestrator for the Autonomous Research
Agent (design §8.3 identity 2, §11, §12 Phase 4) — modeled on
scripts/radar_worker.py: an always-on loop behind its own master switch
(EDGE_RESEARCH_AGENT_LIVE_ENABLED, default off), never imported by app.py
or any UI page (tests/test_research_agent_worker_scope_guard.py).

This worker performs NO ingestion, scan, fetch, or backfill of any kind.
It only ever considers candidates the existing Radar pipelines already
retrieved (status EXTRACTED/TRANSLATED), runs at most MAX_SESSIONS_PER_
TICK bounded Agent SDK sessions per tick, and reads the authoritative
decision back from the packet store — never from the model's own report.

Retry semantics extend src/data_access/dart/retry_policy.py's shape:
only FAILED_RETRIEVAL-class outcomes (or a session that never saved a
packet) are eligible again, after a backoff, up to MAX_ATTEMPTS_PER_
CANDIDATE; attempts are derived from the append-only audit stream, so no
new store is needed. Any other decision makes the candidate permanently
done for this worker (idempotency).

Run with:  python -m scripts.research_agent_worker
"""
from __future__ import annotations

import asyncio
import signal
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from src.config.issuer_registry import get_all_issuers
from src.config.settings import Settings, get_settings
from src.data_access import backend_factory
from src.data_access.agent_audit import AuditEvent, load_all_audit_events
from src.mcp_agent import agent_session, packet_store
from src.mcp_agent.contracts import FILING_SOURCE_NAMES, SessionScope
from src.models.models import CandidateSignal, CandidateStatus, FilingEvent

# Only candidates whose document text the existing pipelines ALREADY
# retrieved. Never NEW_FILING_EVENT / QUEUED / RETRIEVAL_IN_PROGRESS —
# this worker must never become an ingestion path.
ELIGIBLE_STATUSES: frozenset[CandidateStatus] = frozenset({CandidateStatus.EXTRACTED, CandidateStatus.TRANSLATED})
RETRYABLE_DECISIONS: frozenset[str] = frozenset({"FAILED_RETRIEVAL"})

MAX_SESSIONS_PER_TICK = 2
MAX_ATTEMPTS_PER_CANDIDATE = 3
RETRY_BACKOFF_MINUTES: tuple[int, ...] = (60, 120)
DEFAULT_INTERVAL_MINUTES = 60
_MIN_INTERVAL_SECONDS = 300

_shutdown_requested = False


def _request_shutdown(signum: int, frame: Any) -> None:
    global _shutdown_requested
    _shutdown_requested = True


def _install_signal_handlers() -> None:
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _request_shutdown)


def _sleep_in_chunks(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while not _shutdown_requested and time.monotonic() < deadline:
        time.sleep(min(5.0, max(0.0, deadline - time.monotonic())))


@dataclass(frozen=True)
class TickReport:
    considered: int
    started: tuple[str, ...]
    skipped: tuple[str, ...]
    outcomes: tuple[tuple[str, str | None, str | None], ...]  # (candidate_id, packet_id, authoritative_decision)


def _issuer_id_for(filing: FilingEvent, issuers: Sequence[Any]) -> str | None:
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
    return None


def _attempts(events: Sequence[AuditEvent], candidate_id: str) -> tuple[int, datetime | None]:
    starts = [e for e in events if e.candidate_id == candidate_id and e.event_type == "session_started"]
    if not starts:
        return 0, None
    return len(starts), max(datetime.fromisoformat(e.at) for e in starts)


def is_eligible(settings: Settings, candidate: CandidateSignal, *, now: datetime, events: Sequence[AuditEvent]) -> tuple[bool, str]:
    if candidate.status not in ELIGIBLE_STATUSES:
        return False, f"status:{candidate.status.value}"
    decisions = packet_store.decisions_for_candidate(settings.cache_dir, candidate.id)
    if any(d is not None and d not in RETRYABLE_DECISIONS for d in decisions):
        return False, "already_decided"
    attempts, last = _attempts(events, candidate.id)
    if attempts >= MAX_ATTEMPTS_PER_CANDIDATE:
        return False, "max_attempts"
    if attempts and last is not None:
        backoff = RETRY_BACKOFF_MINUTES[min(attempts - 1, len(RETRY_BACKOFF_MINUTES) - 1)]
        if now < last + timedelta(minutes=backoff):
            return False, "backoff"
    return True, "eligible"


def select_candidates(
    settings: Settings, *, now: datetime, issuers: Sequence[Any] | None = None, events: Sequence[AuditEvent] | None = None,
) -> tuple[list[tuple[CandidateSignal, SessionScope]], list[str]]:
    issuers = get_all_issuers() if issuers is None else issuers
    events = load_all_audit_events(settings.cache_dir) if events is None else events
    selected: list[tuple[CandidateSignal, SessionScope]] = []
    skipped: list[str] = []
    for source in sorted(FILING_SOURCE_NAMES):
        try:
            candidates = backend_factory.get_candidate_repository(settings, source).load_candidates()
        except Exception as exc:  # noqa: BLE001 — a broken store skips the source, never the tick
            skipped.append(f"{source}:load_error:{type(exc).__name__}")
            continue
        for candidate in sorted(candidates.values(), key=lambda c: (c.filing.rcept_dt, c.id), reverse=True):
            eligible, reason = is_eligible(settings, candidate, now=now, events=events)
            if not eligible:
                if not reason.startswith("status:"):
                    skipped.append(f"{candidate.id}:{reason}")
                continue
            issuer_id = _issuer_id_for(candidate.filing, issuers)
            if issuer_id is None:
                skipped.append(f"{candidate.id}:issuer_unresolved")
                continue
            scope = SessionScope(
                session_id=f"agent-{uuid.uuid4().hex[:12]}", candidate_id=candidate.id, issuer_id=issuer_id,
                source_name=source, seed_document_id=candidate.filing.rcept_no, started_at=now.isoformat(),
            )
            selected.append((candidate, scope))
            if len(selected) >= MAX_SESSIONS_PER_TICK:
                return selected, skipped
    return selected, skipped


def run_one_tick(
    settings: Settings, *, session_runner: Callable[..., Any] = agent_session.run_session, now: datetime | None = None,
) -> TickReport:
    now = now or datetime.now(timezone.utc)
    selected, skipped = select_candidates(settings, now=now)
    outcomes: list[tuple[str, str | None, str | None]] = []
    for candidate, scope in selected:
        if _shutdown_requested:
            break
        try:
            outcome = asyncio.run(session_runner(settings, scope, seed_title=candidate.filing.report_nm))
        except Exception as exc:  # noqa: BLE001 — one failed session never stops the tick
            print(f"research_agent_worker: session for {candidate.id} failed: {type(exc).__name__}")
            outcomes.append((candidate.id, None, f"error:{type(exc).__name__}"))
            continue
        authoritative: str | None = None
        if outcome.packet_id:
            stored = packet_store.load_packet(settings.cache_dir, outcome.packet_id)
            authoritative = stored.decision if stored is not None else None
        outcomes.append((candidate.id, outcome.packet_id, authoritative))
        print(f"research_agent_worker: {candidate.id} packet={outcome.packet_id} decision={authoritative} error={outcome.error}")
    return TickReport(considered=len(selected) + len(skipped), started=tuple(c.id for c, _ in selected), skipped=tuple(skipped), outcomes=tuple(outcomes))


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
    interval_seconds = max(_MIN_INTERVAL_SECONDS, DEFAULT_INTERVAL_MINUTES * 60)
    while not _shutdown_requested:
        report = run_one_tick(ambient)
        print(f"research_agent_worker: tick considered={report.considered} started={len(report.started)} skipped={len(report.skipped)}")
        if _shutdown_requested:
            break
        _sleep_in_chunks(interval_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
