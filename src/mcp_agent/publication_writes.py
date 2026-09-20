"""The two writes that leave the agent's own tables — moved out of the MCP
tool by blocker E3.

Nothing in this release calls them. They are reachable only from the
worker, and only in `publish` mode with the kill switch off, which the
limited-autonomous-publishing release introduces together with atomic
publication (E6), Signal exclusion (E7) and retraction (E8). Keeping them
here, unwired, makes the boundary explicit: the model-driving session
holds no path to a candidate row or the public store.

Each write also asks assert_write_allowed for publish authority before
touching anything, so if a future change does wire one of these up, a
shadow-mode run raises ShadowWriteViolation rather than quietly writing
a candidate row. No resolvable mode in this release grants that
authority (src/logic/agent_mode.py caps at `shadow`), which makes these
functions unreachable-by-configuration as well as uncalled.
"""
from __future__ import annotations

import hashlib

from src.data_access import backend_factory, verified_update_store
from src.logic.agent_write_guard import assert_write_allowed
from src.logic.publication_policy import DECISION_TO_CANDIDATE_STATUS, PublicationDecision
from src.mcp_agent import packet_store
from src.mcp_agent.contracts import SourceTier
from src.mcp_agent.tools._context import ToolContext
from src.models.models import CandidateStatus, StateTransition
from src.models.verified_update import (
    PUBLISHED_BY_AUTONOMOUS_AGENT,
    VERIFIED_COMPANY_ANNOUNCEMENT,
    VERIFIED_FILING_FACT,
    VerifiedUpdate,
)

FACTUAL_CONTEXT_MAX_CHARS = 200


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


def _write_candidate_status(ctx: ToolContext, decision: PublicationDecision, reasons: tuple[str, ...], now: str, *, resolved) -> str:
    """Returns 'updated' | 'not_found' | 'conflict' | 'error:<name>'. Never
    raises — except through the authority check, which is deliberately
    outside the try: a mode violation is a bug to surface, not an outcome
    to record."""
    assert_write_allowed("candidates", resolved=resolved)
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


