"""What the agent works on, how much of it, and whether it may start.

The old worker selected candidates in EXTRACTED/TRANSLATED order and ran
them immediately. That was wrong twice over: it matched nothing in
production (every real candidate sits in NEEDS_REVIEW, which the old
filter excluded), and it had no durable queue, so a crash mid-session
lost the work and a restart redid it.

Eligibility here is exactly one rule, and it is deliberately narrow:

    status = NEEDS_REVIEW
    AND extraction_state = EXTRACTED
    AND excerpt_original is present

A candidate the reviewers have already dispositioned is not the agent's
business, and a candidate whose text was never retrieved cannot be
quote-verified — row 11 would refuse it anyway, so enqueuing it would
only burn a session to reach a foregone conclusion. The excerpt check is
what makes this a no-fetch design: the agent reads text the Radar
pipelines already stored and never retrieves a document itself.

Enqueueing has two sources with different orderings, which matters. New
qualifying candidates go in as they appear. The existing backlog goes in
OLDEST FIRST and capped per tick, so a first run against thousands of
historical candidates drains steadily from the far end rather than
flooding the queue or starving the old rows forever.

Everything in this module is pure or store-only: no model session, no
network, no candidate write.
"""
from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from src.logic.publication_policy import POLICY_VERSION
from src.models.agent_records import HEARTBEAT_EVENT, AgentAuditRow, AgentJob, AgentRun
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState

# Per tick.
BACKLOG_ENQUEUE_CAP = 25
MAX_SESSIONS_PER_TICK = 2
# Per UTC day, across every tick — the spend ceiling.
MAX_SESSIONS_PER_DAY = 50

MAX_ATTEMPTS_PER_JOB = 3
RETRY_BACKOFF_MINUTES: tuple[int, ...] = (60, 240)
LEASE_MINUTES = 30
# A run whose heartbeat is older than this is presumed dead, and another
# worker may take over. Comfortably longer than one session.
HEARTBEAT_STALE_MINUTES = 45
HEARTBEAT_EVERY_SECONDS = 300

# A constant, so every instance of this worker competes for the same lock.
RUNNER_LOCK_KEY = 0x45455641  # "EEVA"


def is_eligible(candidate: CandidateSignal) -> bool:
    """The one rule. Kept as a predicate over a loaded candidate rather
    than a SQL filter so both backends and the JSON fixtures agree."""
    return (
        candidate.status is CandidateStatus.NEEDS_REVIEW
        and candidate.extraction_state is ExtractionState.EXTRACTED
        and bool((candidate.excerpt_original or "").strip())
    )


def ineligibility_reason(candidate: CandidateSignal) -> str | None:
    if candidate.status is not CandidateStatus.NEEDS_REVIEW:
        return f"status:{candidate.status.value}"
    if candidate.extraction_state is not ExtractionState.EXTRACTED:
        return f"extraction_state:{candidate.extraction_state.value}"
    if not (candidate.excerpt_original or "").strip():
        return "no_excerpt_original"
    return None


def job_id_for(candidate_id: str, version: int, policy_version: str, mode: str) -> str:
    """Derived, not random: the same candidate at the same version under
    the same policy and mode is the same job, so a replayed tick after a
    crash re-derives the id instead of creating a duplicate."""
    digest = hashlib.sha256(f"{candidate_id}|{version}|{policy_version}|{mode}".encode("utf-8")).hexdigest()
    return f"job-{digest[:24]}"


@dataclass(frozen=True)
class EnqueuePlan:
    """What a tick intends to add, before anything is written."""
    new: tuple[AgentJob, ...] = ()
    backlog: tuple[AgentJob, ...] = ()
    skipped: tuple[str, ...] = ()

    @property
    def jobs(self) -> tuple[AgentJob, ...]:
        return self.new + self.backlog


def _job(candidate: CandidateSignal, *, source: str, mode: str, now: str, reason: str, version: int) -> AgentJob:
    return AgentJob(
        job_id=job_id_for(candidate.id, version, POLICY_VERSION, mode),
        candidate_id=candidate.id, source=source, candidate_version=version,
        policy_version=POLICY_VERSION, mode=mode, state="pending", attempts=0,
        enqueue_reason=reason, created_at=now, updated_at=now,
    )


def plan_enqueue(
    candidates: Iterable[tuple[CandidateSignal, str, int]], *, mode: str, now: str, known_after: str | None,
    backlog_cap: int = BACKLOG_ENQUEUE_CAP,
) -> EnqueuePlan:
    """`candidates` is (candidate, source, version). `known_after` is the
    watermark separating "new" from "backlog" — a candidate whose filing
    is more recent goes in immediately; anything at or before it is
    backlog and is taken oldest first, up to the cap.

    Age is the filing's own receipt date (`filing.rcept_dt`), not a row
    timestamp: it is what "oldest" means to a reader, it is stable across
    a backfill that re-inserts rows, and CandidateSignal carries it."""
    new: list[AgentJob] = []
    backlog: list[CandidateSignal] = []
    backlog_meta: dict[str, tuple[str, int]] = {}
    skipped: list[str] = []

    for candidate, source, version in candidates:
        reason = ineligibility_reason(candidate)
        if reason is not None:
            skipped.append(f"{candidate.id}:{reason}")
            continue
        filed = candidate.filing.rcept_dt or ""
        if known_after is not None and filed > known_after:
            new.append(_job(candidate, source=source, mode=mode, now=now, reason="new", version=version))
        else:
            backlog.append(candidate)
            backlog_meta[candidate.id] = (source, version)

    backlog.sort(key=lambda c: (c.filing.rcept_dt or "", c.id))  # oldest first
    capped = [
        _job(c, source=backlog_meta[c.id][0], mode=mode, now=now, reason="backlog", version=backlog_meta[c.id][1])
        for c in backlog[:max(0, backlog_cap)]
    ]
    if len(backlog) > len(capped):
        skipped.append(f"backlog_cap:{len(backlog) - len(capped)}_deferred")
    return EnqueuePlan(new=tuple(new), backlog=tuple(capped), skipped=tuple(skipped))


def next_attempt_at(attempts: int, *, now: datetime) -> str | None:
    """None means no further attempt — the job is dead."""
    if attempts >= MAX_ATTEMPTS_PER_JOB:
        return None
    minutes = RETRY_BACKOFF_MINUTES[min(attempts - 1, len(RETRY_BACKOFF_MINUTES) - 1)] if attempts else RETRY_BACKOFF_MINUTES[0]
    return (now + timedelta(minutes=minutes)).isoformat()


def lease_until(now: datetime) -> str:
    return (now + timedelta(minutes=LEASE_MINUTES)).isoformat()


def day_start(now: datetime) -> str:
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def sessions_remaining_today(store, *, now: datetime) -> int:
    """Counted from packets actually written, not from jobs attempted, so
    a crash loop cannot silently consume the day's budget twice."""
    used = store.count_packets_since(since=day_start(now))
    return max(0, MAX_SESSIONS_PER_DAY - used)


def heartbeat_row(run_id: str, *, worker_instance: str, at: str) -> AgentAuditRow:
    """Liveness as an audit event. Carries no input payload at all — a
    heartbeat is a timestamp, and payloads are where secrets leak."""
    return AgentAuditRow(
        session_id=run_id, event_type=HEARTBEAT_EVENT, created_at=at, run_id=run_id,
        inputs_json=None, outcome=worker_instance,
    )


# --- single runner ------------------------------------------------------------

@dataclass(frozen=True)
class RunnerClaim:
    acquired: bool
    run_id: str | None = None
    reason: str = ""
    reclaimed: tuple[str, ...] = ()


def acquire_single_runner(
    store, *, worker_instance: str, mode: str, now: datetime, model: str | None = None,
) -> RunnerClaim:
    """One worker at a time. A run still marked `running` whose heartbeat
    has gone stale is presumed crashed and is closed as failed, which
    releases the slot; a run with a live heartbeat blocks this one.

    On Postgres a real advisory lock is taken as well, so a worker killed
    without any chance to clean up still releases its claim when its
    connection drops. SQLite has no equivalent, and is local-only."""
    stale_before = (now - timedelta(minutes=HEARTBEAT_STALE_MINUTES)).isoformat()
    reclaimed: list[str] = []
    for run in store.active_runs():
        beat = store.latest_heartbeat_at(run.run_id) or run.started_at
        if beat <= stale_before:
            store.complete_run(run.run_id, status="failed", completed_at=now.isoformat())
            reclaimed.append(run.run_id)
            continue
        if run.worker_instance != worker_instance:
            return RunnerClaim(False, reason=f"another worker is running (run {run.run_id}, heartbeat {beat})",
                               reclaimed=tuple(reclaimed))

    acquire = getattr(store, "try_acquire_runner_lock", None)
    if acquire is not None and not acquire(RUNNER_LOCK_KEY):
        return RunnerClaim(False, reason="advisory lock is held by another worker", reclaimed=tuple(reclaimed))

    run_id = f"run-{uuid.uuid4().hex[:12]}"
    store.start_run(AgentRun(
        run_id=run_id, worker_instance=worker_instance, mode=mode, started_at=now.isoformat(),
        status="running", policy_version=POLICY_VERSION, model=model,
    ))
    store.append_audit_events([heartbeat_row(run_id, worker_instance=worker_instance, at=now.isoformat())])
    return RunnerClaim(True, run_id=run_id, reclaimed=tuple(reclaimed))


# --- startup self-check --------------------------------------------------------

REQUIRED_AGENT_TABLES: tuple[str, ...] = (
    "agent_runs", "agent_jobs", "agent_packets", "agent_evidence", "agent_decisions",
    "agent_audit_events", "agent_control",
)
MINIMUM_POSTGRES_SCHEMA_VERSION = 24


@dataclass(frozen=True)
class SelfCheck:
    """Fail closed: `ok` is true only when every check passed."""
    ok: bool
    problems: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def summary(self) -> str:
        return "; ".join(self.problems) if self.problems else "all startup checks passed"


def run_self_check(
    settings, resolved, *, schema_version: int | None, tables: Sequence[str], runtime_probe=None,
) -> SelfCheck:
    """Everything a shadow run needs before it is allowed to start.

    `off` short-circuits: a disabled agent has no prerequisites, and
    demanding Postgres from an operator who has deliberately turned the
    agent off would be noise. Every other mode must satisfy all of it."""
    if resolved.is_off:
        return SelfCheck(True, notes=("mode is off; no prerequisites apply",))

    problems: list[str] = []

    backend = (getattr(settings, "db_backend", "") or "").strip().lower()
    if backend != "postgres":
        problems.append(f"EDGE_DB_BACKEND must be postgres for a shadow run; found {backend or 'unset'!r}")

    if schema_version is None:
        problems.append("the database reports no schema version")
    elif schema_version < MINIMUM_POSTGRES_SCHEMA_VERSION:
        problems.append(
            f"schema is V{schema_version}; the agent tables need V{MINIMUM_POSTGRES_SCHEMA_VERSION} or later"
        )

    present = {t.lower() for t in tables}
    missing = [t for t in REQUIRED_AGENT_TABLES if t not in present]
    if missing:
        problems.append("missing agent tables: " + ", ".join(missing))

    # Presence only — never the value, which is a credential.
    if not getattr(settings, "research_agent_service_token", None):
        problems.append("EDGE_RESEARCH_AGENT_SERVICE_TOKEN is not configured")
    if not (getattr(settings, "edgar_user_agent", None) or "").strip():
        problems.append("EDGE_EDGAR_USER_AGENT is not configured")

    if runtime_probe is not None:
        detail = runtime_probe()
        if detail:
            problems.append(f"agent runtime probe failed: {detail}")

    return SelfCheck(not problems, tuple(problems))
