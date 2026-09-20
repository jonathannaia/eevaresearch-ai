"""request_publication_decision (design §6, §7.5) — the backend policy
service the agent may ask for a decision. The agent can ask; it supplies no input that can change the
outcome once packet_id is fixed: the matrix is evaluated against the
already-persisted packet and the server-side session context, and the
recorded decision is returned unchanged on any repeat call (idempotent).

The agent can ask; it supplies no input that can change the outcome once
packet_id is fixed: the matrix is evaluated against the already-persisted
packet and the server-side session context, and the recorded decision is
returned unchanged on any repeat call (idempotent).

Fail closed: a decision that cannot be persisted returns an error and
changes nothing. Since blocker E3 this tool performs no write outside the
packet store — see src/mcp_agent/publication_writes.py for the candidate
and public-store writes, which only a future publish-mode worker calls.
1 call per session."""
from __future__ import annotations

from src.logic import quote_verification
from src.logic.publication_policy import PublicationContext, evaluate_publication_eligibility
from src.mcp_agent import packet_store
from src.mcp_agent.contracts import (
    IssuerResolution,
    PriorFilingComparison,
    PublicationDecisionResult,
    RelationshipContextResult,
    RelationshipContextStatus,
    ResolutionConfidence,
    Suppression,
    ToolError,
    ToolErrorKind,
)
from src.mcp_agent.tools._common import consume, guarded, reject
from src.mcp_agent.tools._context import ToolContext
NAME = "request_publication_decision"


def _quote_support(ctx: ToolContext, stored: packet_store.StoredPacket) -> tuple[dict[str, bool], dict[str, str]]:
    """Row 11's input: every claim checked against the excerpt its own
    evidence carried, using the issuer's resolved name so attribution is
    not mistaken for an unsupported word."""
    issuer_names = [n for n in (
        ctx.resolved_issuer.tracked_company_name if ctx.resolved_issuer else None,
        ctx.scope.issuer_id,
    ) if n]
    support = quote_verification.verify_proposal(
        stored.proposal, {e.evidence_id: e for e in stored.evidence}, issuer_names=issuer_names,
        evidence_text=stored.evidence_text,
    )
    return ({cid: r.verified for cid, r in support.items()}, {cid: r.detail for cid, r in support.items()})


def _build_context(ctx: ToolContext, stored: packet_store.StoredPacket) -> PublicationContext:
    seed_row = ctx.metadata_rows_by_id.get(ctx.scope.seed_document_id)
    hashes, evidence_sets = packet_store.previously_published(ctx.settings.cache_dir, exclude_packet_id=stored.packet_id)
    quote_support, quote_detail = _quote_support(ctx, stored)
    return PublicationContext(
        issuer_resolution=ctx.resolved_issuer or IssuerResolution(ResolutionConfidence.UNRESOLVED, error=ToolError(ToolErrorKind.INVALID_INPUT, "issuer was never resolved this session")),
        suppression=seed_row.suppression if seed_row is not None else Suppression.NONE,
        suppression_detail=seed_row.suppression_detail if seed_row is not None else "",
        evidence_by_id={e.evidence_id: e for e in stored.evidence},
        prior_comparison=ctx.last_comparison or PriorFilingComparison(error=ToolError(ToolErrorKind.INVALID_INPUT, "prior-filing comparison was never performed this session")),
        relationship_context=ctx.last_relationship or RelationshipContextResult(RelationshipContextStatus.UNAVAILABLE, detail="relationship context was never retrieved this session"),
        previously_published_hashes=hashes, previously_published_evidence_sets=evidence_sets,
        kill_switch_enabled=ctx.settings.research_agent_publication_kill_switch_enabled,
        retrieval_error=ctx.retrieval_error,
        quote_support=quote_support, quote_support_detail=quote_detail,
    )


def run(ctx: ToolContext, packet_id: str) -> PublicationDecisionResult:
    inputs = {"packet_id": packet_id}
    if (error := consume(ctx, NAME, inputs)) is not None:
        return PublicationDecisionResult(decision="", error=error)
    if not packet_id or packet_id != ctx.packet_id:
        return PublicationDecisionResult(decision="", error=reject(ctx, NAME, inputs, ToolErrorKind.NOT_FOUND, "packet_id was not saved by this session"))
    stored = packet_store.load_packet(ctx.settings.cache_dir, packet_id)
    if stored is None:
        return PublicationDecisionResult(decision="", error=reject(ctx, NAME, inputs, ToolErrorKind.NOT_FOUND, "packet not found"))
    ctx.audit("publication_decision_requested", inputs, f"packet_id={packet_id} already_decided={stored.decision is not None}", tool_name=NAME, case_id=packet_id)
    if stored.decision is not None:
        return PublicationDecisionResult(decision=stored.decision, reasons=stored.decision_reasons)

    decision = evaluate_publication_eligibility(stored.proposal, _build_context(ctx, stored))
    now = ctx.now_iso()
    recorded, error = guarded(ctx, NAME, inputs, lambda: packet_store.record_decision(ctx.settings.cache_dir, packet_id, decision.decision.value, decision.reasons, now))
    if error is not None or recorded is None:
        return PublicationDecisionResult(decision="", row_results=decision.as_row_tuples(), error=error or reject(ctx, NAME, inputs, ToolErrorKind.VALIDATION_FAILED, "decision could not be persisted; publication withheld"))
    ctx.audit("publication_decision_made", {"packet_id": packet_id, "rows": decision.as_row_tuples()}, f"{decision.decision.value}: {'; '.join(decision.reasons)}", tool_name=NAME, case_id=packet_id)
    result = PublicationDecisionResult(decision=decision.decision.value, reasons=decision.reasons, row_results=decision.as_row_tuples())

    # Blocker E3: the tool records the decision and stops there. Every write
    # that leaves the agent's own tables — the candidate status and the
    # public store — belongs to the worker, in `publish` mode only, and no
    # such mode exists in this release. A session can therefore change
    # nothing a reader sees, whatever the matrix returns.
    return result
