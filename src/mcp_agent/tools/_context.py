"""Per-session runtime state shared by the ten tools (design §6 common
contract, §7.2, §8.2). One ToolContext per agent session — one filing,
one issuer, one case — created by the MCP server process from its
environment and never widened afterwards.

Every audit event is persisted the moment it is recorded (append-only
JSONL, see agent_audit.py), so a session that crashes mid-way still
leaves a complete trail of everything it did up to that point.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.config.settings import Settings
from src.data_access.agent_audit import AuditEvent, append_audit_event, build_audit_event
from src.mcp_agent.budgets import SessionBudget
from src.mcp_agent.contracts import (
    EvidenceRecord,
    FilingMetadataRow,
    IssuerResolution,
    PriorFilingComparison,
    RelationshipContextResult,
    SessionScope,
    ToolError,
    ToolErrorKind,
)
from src.models.models import FilingEvent


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ToolContext:
    scope: SessionScope
    settings: Settings
    budget: SessionBudget
    # source_name -> adapter client. Tests inject fakes that raise on any
    # fetch, proving every tool stays on the cache-first path.
    clients: dict[str, Any] = field(default_factory=dict)
    now: Callable[[], datetime] = _utc_now
    # Evidence registry: the only IDs a claim may ever reference (§7.4).
    evidence: dict[str, EvidenceRecord] = field(default_factory=dict)
    evidence_text: dict[str, str] = field(default_factory=dict)
    # Server-side foreign-key set for the excerpt tools (§6): a document
    # id must have been returned by search_filing_metadata this session.
    filing_events_by_id: dict[str, FilingEvent] = field(default_factory=dict)
    metadata_rows_by_id: dict[str, FilingMetadataRow] = field(default_factory=dict)
    resolved_issuer: IssuerResolution | None = None
    last_comparison: PriorFilingComparison | None = None
    last_relationship: RelationshipContextResult | None = None
    packet_id: str | None = None
    retrieval_error: bool = False
    audit_events: list[AuditEvent] = field(default_factory=list)

    def now_iso(self) -> str:
        return self.now().isoformat()

    def audit(
        self, event_type: str, inputs: Any, output_summary: str, *, tool_name: str | None = None, case_id: str | None = None,
    ) -> AuditEvent:
        event = build_audit_event(
            session_id=self.scope.session_id, candidate_id=self.scope.candidate_id, event_type=event_type,
            inputs=inputs, output_summary=output_summary, case_id=case_id or self.packet_id, tool_name=tool_name,
            at=self.now_iso(),
        )
        self.audit_events.append(event)
        append_audit_event(self.settings.cache_dir, event)
        return event


def scope_check(ctx: ToolContext, issuer_id: str | None) -> ToolError | None:
    """The session credential is scoped to exactly one issuer (§6)."""
    if not (issuer_id or "").strip():
        return ToolError(ToolErrorKind.INVALID_INPUT, "issuer_id is required")
    if issuer_id != ctx.scope.issuer_id:
        return ToolError(ToolErrorKind.UNAUTHORIZED, f"issuer {issuer_id!r} is outside this session's scope")
    return None
