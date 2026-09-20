"""Durable agent records (Agent Observability and Shadow Mode release).

One dataclass per agent table, shared by the SQLite and Postgres
repository modules exactly as src.models.user_preferences is shared by
theirs. Nothing here reaches a database, a model session or a network.

The decision record deliberately keeps four separate facts rather than
one verdict:

  * `policy_decision`   — what the publication matrix decided with the
    publication hold excluded (row 0 skipped), i.e. what would happen if
    nothing were holding the agent back;
  * `effective_decision` — what actually happened. In shadow mode this is
    always NO_ACTION, because nothing outside the agent tables is written;
  * `blocked_by`        — which hold applied. When both apply, the kill
    switch wins, because it is the operator's deliberate, explicit stop;
  * `mode` + `kill_switch_on` — the complete context, so a row blocked by
    the kill switch inside shadow mode still records both.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

AgentMode = Literal["off", "shadow", "publish"]
AGENT_MODES: tuple[AgentMode, ...] = ("off", "shadow", "publish")

JobState = Literal["pending", "leased", "done", "failed", "dead"]
JOB_STATES: tuple[JobState, ...] = ("pending", "leased", "done", "failed", "dead")

EnqueueReason = Literal["backlog", "new"]
ENQUEUE_REASONS: tuple[EnqueueReason, ...] = ("backlog", "new")

BlockedBy = Literal["kill_switch", "shadow_mode"]
BLOCKED_BY_KILL_SWITCH: BlockedBy = "kill_switch"
BLOCKED_BY_SHADOW_MODE: BlockedBy = "shadow_mode"

# The effective decision recorded whenever the agent changes nothing
# outside its own tables. Never a PublicationDecision member: those are
# policy outcomes, and this is the absence of an action.
EFFECTIVE_NO_ACTION = "NO_ACTION"

RunStatus = Literal["running", "completed", "failed"]

# Liveness is an append-only audit event rather than a column, so a run's
# heartbeat history survives and the agent tables need no new migration.
HEARTBEAT_EVENT = "run_heartbeat"


def is_agent_mode(value: object) -> bool:
    return value in AGENT_MODES


def more_restrictive(left: AgentMode, right: AgentMode) -> AgentMode:
    """The stricter of two modes. Used for the database override, which
    may only ever narrow what the environment allows, never widen it."""
    order = {"off": 0, "shadow": 1, "publish": 2}
    return left if order[left] <= order[right] else right


def resolve_blocked_by(*, mode: AgentMode, kill_switch_on: bool) -> BlockedBy | None:
    """Kill switch first: an operator's explicit stop outranks the mode."""
    if kill_switch_on:
        return BLOCKED_BY_KILL_SWITCH
    if mode != "publish":
        return BLOCKED_BY_SHADOW_MODE
    return None


@dataclass(frozen=True)
class AgentRun:
    run_id: str
    worker_instance: str
    mode: str
    started_at: str
    policy_version: str
    status: str = "running"
    completed_at: str | None = None
    considered: int = 0
    sessions: int = 0
    decisions: int = 0
    errors: int = 0
    model: str | None = None
    cost_usd: str | None = None


@dataclass(frozen=True)
class AgentJob:
    job_id: str
    candidate_id: str
    source: str
    candidate_version: int
    policy_version: str
    mode: str
    created_at: str
    updated_at: str
    enqueue_reason: str = "new"
    state: str = "pending"
    attempts: int = 0
    lease_expires_at: str | None = None
    worker_instance: str | None = None
    last_error_code: str | None = None
    next_attempt_at: str | None = None


@dataclass(frozen=True)
class AgentEvidenceRow:
    packet_id: str
    evidence_id: str
    source_tier: str
    source_name: str
    source_url: str | None = None
    source_document_id: str | None = None
    source_date: str | None = None
    excerpt_or_locator: str | None = None
    excerpt_sha256: str | None = None


@dataclass(frozen=True)
class AgentPacket:
    packet_id: str
    session_id: str
    candidate_id: str
    source: str
    seed_document_id: str
    proposal_json: str
    content_hash: str
    created_at: str
    job_id: str | None = None
    run_id: str | None = None
    issuer_id: str | None = None
    issuer_resolution_json: str | None = None
    evidence: tuple[AgentEvidenceRow, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AgentDecisionRow:
    packet_id: str
    policy_decision: str
    effective_decision: str
    mode: str
    kill_switch_on: bool
    policy_version: str
    decided_at: str
    reasons_json: str = "[]"
    row_results_json: str = "[]"
    blocked_by: str | None = None
    quote_verified: bool = False
    candidate_status_written: bool = False
    published: bool = False


@dataclass(frozen=True)
class AgentAuditRow:
    session_id: str
    event_type: str
    created_at: str
    id: int | None = None
    run_id: str | None = None
    packet_id: str | None = None
    candidate_id: str | None = None
    tool_name: str | None = None
    inputs_json: str | None = None
    outcome: str | None = None


@dataclass(frozen=True)
class AgentControl:
    mode_override: str | None
    updated_at: str
    reason: str | None = None
    updated_by: str | None = None


@dataclass(frozen=True)
class DecisionListRow:
    """One row of the Agent Review list: the decision joined to the
    packet fields the list and its filters need."""
    packet_id: str
    candidate_id: str
    source: str
    issuer_id: str | None
    seed_document_id: str
    created_at: str
    policy_decision: str
    effective_decision: str
    blocked_by: str | None
    mode: str
    kill_switch_on: bool
    quote_verified: bool
    decided_at: str
    reasons_json: str = "[]"
