"""request_publication_decision (design §6, §7.5) — the ONLY path to a
public state, and a backend policy service rather than an agent
decision. The agent can ask; it supplies no input that can change the
outcome once packet_id is fixed: the matrix is evaluated against the
already-persisted packet and the server-side session context, and the
recorded decision is returned unchanged on any repeat call (idempotent).

Fail closed, in order: a decision that cannot be persisted publishes
nothing; an AUTO_PUBLISHED decision whose candidate cannot be marked
PUBLISHED (missing candidate, version conflict, write error) publishes
nothing — the public store is written only after the candidate status
write succeeds, so CandidateSignal.status stays the single source of
truth for "is this public" (signal_promotion reads it, nothing else).
1 call per session."""
from __future__ import annotations

import hashlib

from src.data_access import backend_factory, verified_update_store
from src.logic.publication_policy import (
    DECISION_TO_CANDIDATE_STATUS,
    PublicationContext,
    PublicationDecision,
    evaluate_publication_eligibility,
)
from src.mcp_agent import packet_store
from src.mcp_agent.contracts import (
    IssuerResolution,
    PriorFilingComparison,
    PublicationDecisionResult,
    RelationshipContextResult,
    RelationshipContextStatus,
    ResolutionConfidence,
    SourceTier,
    Suppression,
    ToolError,
    ToolErrorKind,
)
from src.mcp_agent.tools._common import consume, guarded, reject
from src.mcp_agent.tools._context import ToolContext
from src.models.models import CandidateStatus, StateTransition
from src.models.verified_update import (
    PUBLISHED_BY_AUTONOMOUS_AGENT,
    VERIFIED_COMPANY_ANNOUNCEMENT,
    VERIFIED_FILING_FACT,
    VerifiedUpdate,
)

NAME = "request_publication_decision"
FACTUAL_CONTEXT_MAX_CHARS = 200


def _build_context(ctx: ToolContext, stored: packet_store.StoredPacket) -> PublicationContext:
    seed_row = ctx.metadata_rows_by_id.get(ctx.scope.seed_document_id)
    hashes, evidence_sets = packet_store.previously_published(ctx.settings.cache_dir, exclude_packet_id=stored.packet_id)
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
    )


def _verified_updates(ctx: ToolContext, stored: packet_store.StoredPacket, now: str) -> tuple[VerifiedUpdate, ...]:
    filing = ctx.filing_events_by_id.get(ctx.scope.seed_document_id)
    issuer_name = (ctx.resolved_issuer.tracked_company_name if ctx.resolved_issuer else None) or ctx.scope.issuer_id
    evidence = {e.evidence_id: e for e in stored.evidence}
    updates = []
    for claim in stored.proposal.claims:
        primary = evidence[claim.evidence_ids[0]]
        label = VERIFIED_COMPANY_ANNOUNCEMENT if primary.source_tier is SourceTier.ISSUER_IR else VERIFIED_FILING_FACT
        source_label = f"{primary.source_name} {(filing.report_nm or filing.pblntf_ty) if filing is not None else ''}".strip()
        updates.append(VerifiedUpdate(
            id="vu-" + hashlib.sha256(f"{stored.packet_id}|{claim.claim_id}".encode("utf-8")).hexdigest()[:16],
            candidate_id=stored.candidate_id, headline=claim.headline, issuer=issuer_name, entity_id=claim.issuer_id,
            fact_statement=claim.statement, source_label=source_label, source_date=primary.source_date,
            source_url=primary.source_url, source_document_id=primary.source_document_id,
            excerpt_or_locator=primary.excerpt_or_locator, what_this_does_not_establish=claim.what_this_does_not_establish,
            label=label, published_at=now, published_by=PUBLISHED_BY_AUTONOMOUS_AGENT, evidence_ids=tuple(claim.evidence_ids),
            audit_session_id=stored.session_id,
            factual_context=tuple(stored.evidence_text.get(e, "")[:FACTUAL_CONTEXT_MAX_CHARS] for e in claim.factual_context_evidence_ids if stored.evidence_text.get(e)),
        ))
    return tuple(updates)


def _write_candidate_status(ctx: ToolContext, decision: PublicationDecision, reasons: tuple[str, ...], now: str) -> str:
    """Returns 'updated' | 'not_found' | 'conflict' | 'error:<name>'. Never raises."""
    status = DECISION_TO_CANDIDATE_STATUS[decision]
    try:
        repo = backend_factory.get_candidate_repository(ctx.settings, ctx.scope.source_name)
        candidate = repo.get_candidate(ctx.scope.candidate_id)
        if candidate is None:
            return "not_found"
        version = repo.get_candidate_version(ctx.scope.candidate_id)
        candidate.status = status
        candidate.reviewed_at = now
        if status is CandidateStatus.PUBLISHED:
            candidate.published_by = PUBLISHED_BY_AUTONOMOUS_AGENT
        candidate.state_history.append(StateTransition(status=status, at=now, detail=f"autonomous_agent:{decision.value}:{reasons[0] if reasons else ''}"))
        outcome = repo.update_candidate(candidate, expected_version=version)
        return outcome.status if outcome is not None else "updated"
    except Exception as exc:  # noqa: BLE001 — audited, never raised
        return f"error:{type(exc).__name__}"


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

    if decision.decision in DECISION_TO_CANDIDATE_STATUS:
        write_status = _write_candidate_status(ctx, decision.decision, decision.reasons, now)
        ctx.audit("candidate_status_written", {"packet_id": packet_id, "decision": decision.decision.value}, write_status, tool_name=NAME, case_id=packet_id)
        if decision.decision is PublicationDecision.AUTO_PUBLISHED and write_status != "updated":
            return PublicationDecisionResult(decision=result.decision, reasons=result.reasons, row_results=result.row_results,
                                             error=reject(ctx, NAME, inputs, ToolErrorKind.VALIDATION_FAILED, f"candidate status write {write_status}; publication withheld"))

    if decision.decision is PublicationDecision.AUTO_PUBLISHED:
        updates = _verified_updates(ctx, stored, now)
        inserted, error = guarded(ctx, NAME, inputs, lambda: verified_update_store.append_verified_updates(ctx.settings.cache_dir, updates))
        if error is not None:
            return PublicationDecisionResult(decision=result.decision, reasons=result.reasons, row_results=result.row_results, error=error)
        ctx.audit("verified_updates_published", {"packet_id": packet_id, "ids": list(inserted)}, f"inserted={len(inserted)} of {len(updates)}", tool_name=NAME, case_id=packet_id)
        return PublicationDecisionResult(decision=result.decision, reasons=result.reasons, row_results=result.row_results, verified_update_id=inserted[0] if inserted else None)
    return result
