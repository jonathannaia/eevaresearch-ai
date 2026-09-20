"""Turns one finished agent evaluation into durable agent rows.

This is the seam that closes blocker E5: the worker calls it after a
session ends, and everything the agent produced — the packet, its
evidence, the decision with its full blocking context, and the session's
audit trail — lands in the agent tables (Postgres V24 / SQLite V22)
instead of a per-process JSON file.

It is deliberately write-only and agent-table-only: nothing here can
reach candidates, Signals or Verified Updates. The caller supplies
already-computed values; no policy is evaluated and no model is called.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from typing import Any, Protocol

from src.logic.publication_policy import POLICY_VERSION, PolicyDecision
from src.models.agent_records import (
    AgentAuditRow,
    AgentDecisionRow,
    AgentEvidenceRow,
    AgentPacket,
    EFFECTIVE_NO_ACTION,
    resolve_blocked_by,
)


class SupportsAgentWrites(Protocol):
    def save_packet(self, packet: AgentPacket) -> None: ...
    def record_decision(self, decision: AgentDecisionRow) -> None: ...
    def append_audit_events(self, events: Sequence[AgentAuditRow]) -> None: ...


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def evidence_rows(packet_id: str, evidence: Sequence[Any]) -> tuple[AgentEvidenceRow, ...]:
    """Maps EvidenceRecord contracts onto storage rows. The excerpt is
    stored as retrieved, together with its hash, so a later reader can
    check a quoted claim against exactly what the session saw."""
    rows = []
    for item in evidence:
        tier = getattr(item.source_tier, "value", item.source_tier)
        rows.append(AgentEvidenceRow(
            packet_id=packet_id, evidence_id=item.evidence_id, source_tier=str(tier),
            source_name=item.source_name, source_url=item.source_url or None,
            source_document_id=item.source_document_id or None, source_date=item.source_date or None,
            excerpt_or_locator=item.excerpt_or_locator or None, excerpt_sha256=item.excerpt_sha256 or None,
        ))
    return tuple(rows)


def proposal_json(proposal: Any) -> str:
    return _json(asdict(proposal)) if hasattr(proposal, "__dataclass_fields__") else _json(proposal)


def build_packet(
    *, packet_id: str, session_id: str, candidate_id: str, source: str, seed_document_id: str,
    proposal: Any, content_hash: str, created_at: str, job_id: str | None = None, run_id: str | None = None,
    issuer_id: str | None = None, issuer_resolution: Any = None, evidence: Sequence[Any] = (),
) -> AgentPacket:
    return AgentPacket(
        packet_id=packet_id, session_id=session_id, candidate_id=candidate_id, source=source,
        seed_document_id=seed_document_id, proposal_json=proposal_json(proposal), content_hash=content_hash,
        created_at=created_at, job_id=job_id, run_id=run_id, issuer_id=issuer_id,
        issuer_resolution_json=_json(asdict(issuer_resolution)) if hasattr(issuer_resolution, "__dataclass_fields__")
        else (_json(issuer_resolution) if issuer_resolution is not None else None),
        evidence=evidence_rows(packet_id, evidence),
    )


def build_decision(
    *, packet_id: str, policy: PolicyDecision, mode: str, kill_switch_on: bool, quote_verified: bool,
    decided_at: str, candidate_status_written: bool = False, published: bool = False,
) -> AgentDecisionRow:
    """`policy` is the matrix result evaluated with the publication hold
    excluded — what would happen with nothing holding the agent back. The
    effective decision is NO_ACTION whenever a hold applies, which in this
    release is always: shadow mode writes nothing outside these tables."""
    blocked_by = resolve_blocked_by(mode=mode, kill_switch_on=kill_switch_on)
    effective = EFFECTIVE_NO_ACTION if blocked_by is not None else policy.decision.value
    return AgentDecisionRow(
        packet_id=packet_id, policy_decision=policy.decision.value, effective_decision=effective,
        blocked_by=blocked_by, mode=mode, kill_switch_on=kill_switch_on, quote_verified=quote_verified,
        reasons_json=_json(list(policy.reasons)), row_results_json=_json([list(r) for r in policy.as_row_tuples()]),
        policy_version=POLICY_VERSION, decided_at=decided_at,
        candidate_status_written=candidate_status_written, published=published,
    )


def audit_rows(
    events: Sequence[Any], *, run_id: str | None = None, packet_id: str | None = None,
) -> tuple[AgentAuditRow, ...]:
    """Maps the session's own AuditEvent objects onto storage rows.
    `inputs_json` carries the already-sanitized input hash/summary the
    session recorded — never raw secrets."""
    rows = []
    for event in events:
        rows.append(AgentAuditRow(
            session_id=event.session_id, event_type=event.event_type, created_at=event.at,
            run_id=run_id, packet_id=packet_id or getattr(event, "case_id", None),
            candidate_id=getattr(event, "candidate_id", None), tool_name=getattr(event, "tool_name", None),
            inputs_json=getattr(event, "input_hash", None), outcome=getattr(event, "output_summary", None),
        ))
    return tuple(rows)


def persist_evaluation(
    store: SupportsAgentWrites, *, packet: AgentPacket, decision: AgentDecisionRow,
    events: Sequence[AgentAuditRow] = (),
) -> str:
    """Packet and evidence first, then the decision, then the audit trail:
    a decision row can never exist without the packet it decided on."""
    store.save_packet(packet)
    store.record_decision(decision)
    if events:
        store.append_audit_events(events)
    return packet.packet_id
