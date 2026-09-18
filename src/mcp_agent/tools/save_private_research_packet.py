"""save_private_research_packet (design §6) — a narrow, policy-mediated
write REQUEST, not a general write permission. Persists the agent's
structured claim proposal as a ResearchCase + ResearchEvidenceItem bundle
(trigger_source_type="autonomous_agent") through the existing
research_case_validation gate and research_store/backend_factory
writers, plus the private packet record request_publication_decision
evaluates. Private by construction: no publication side effect of any
kind — signal_promotion never reads ResearchCase, only
CandidateSignal.status. 1 call per session.

Server-side authoritative twin of the SDK pre-draft hook (§7.4): every
evidence_id must be in THIS session's registry, i.e. must have come back
from an evidence tool this session — a fabricated id is rejected here
even if a hook were bypassed."""
from __future__ import annotations

from src.data_access import backend_factory, research_store
from src.logic.research_case_validation import ResearchCaseBundle, validate_research_case_bundle
from src.mcp_agent import packet_store
from src.mcp_agent.contracts import ClaimProposal, PacketSaveResult, ToolErrorKind, validate_claim_proposal
from src.mcp_agent.tools._common import consume, guarded, reject
from src.mcp_agent.tools._context import ToolContext
from src.models.research_case import ResearchCase, ResearchCaseStatus, ResearchEvidenceItem

NAME = "save_private_research_packet"
TRIGGER_SOURCE_TYPE = "autonomous_agent"
RESEARCH_QUESTION = "Which direct reported facts in this filing are supported by resolvable primary evidence?"


def _rejected(ctx: ToolContext, inputs: dict, violations: tuple[str, ...]) -> PacketSaveResult:
    error = reject(ctx, NAME, inputs, ToolErrorKind.VALIDATION_FAILED, "; ".join(violations))
    return PacketSaveResult(status="rejected", violations=violations, error=error)


def run(ctx: ToolContext, proposal: ClaimProposal) -> PacketSaveResult:
    inputs = {"session_id": proposal.session_id, "candidate_id": proposal.candidate_id, "claim_ids": [c.claim_id for c in proposal.claims], "retrieved_evidence_ids": list(proposal.retrieved_evidence_ids)}
    if (error := consume(ctx, NAME, inputs)) is not None:
        return PacketSaveResult(status="rejected", error=error)
    if proposal.session_id != ctx.scope.session_id or proposal.candidate_id != ctx.scope.candidate_id:
        return PacketSaveResult(status="rejected", error=reject(ctx, NAME, inputs, ToolErrorKind.UNAUTHORIZED, "proposal session/candidate does not match this session's scope"))
    if ctx.packet_id is not None:
        return PacketSaveResult(status="rejected", packet_id=ctx.packet_id, error=reject(ctx, NAME, inputs, ToolErrorKind.INVALID_INPUT, "a packet was already saved this session"))
    violations = validate_claim_proposal(proposal)
    if violations:
        return _rejected(ctx, inputs, violations)
    referenced = tuple(dict.fromkeys(e for c in proposal.claims for e in (*c.evidence_ids, *c.factual_context_evidence_ids)))
    unknown = tuple(sorted(e for e in referenced if e not in ctx.evidence))
    if unknown:
        return _rejected(ctx, inputs, tuple(f"evidence_id_not_in_session_registry:{e}" for e in unknown))

    now = ctx.now_iso()
    case_id = research_store.build_case_id(TRIGGER_SOURCE_TYPE, ctx.scope.candidate_id, now)
    filing = ctx.filing_events_by_id.get(ctx.scope.seed_document_id)
    first = proposal.claims[0]
    case = ResearchCase(
        id=case_id, trigger_source_type=TRIGGER_SOURCE_TYPE, trigger_source_id=ctx.scope.candidate_id,
        trigger_source_name=ctx.scope.source_name, trigger_summary=first.headline, title=first.headline,
        research_question=RESEARCH_QUESTION, status=ResearchCaseStatus.OPEN, created_at=now, version=1,
    )
    items = tuple(
        ResearchEvidenceItem(
            id=evidence_id, case_id=case_id, source_type=record.source_tier.value, source_id=record.source_document_id,
            source_url=record.source_url, source_publisher_or_system=record.source_name, source_date=record.source_date,
            retrieved_at=record.retrieved_at, excerpt_original=ctx.evidence_text.get(evidence_id, ""),
            original_language=(filing.original_language if filing is not None else "English"), added_at=now,
        )
        for evidence_id in referenced for record in (ctx.evidence[evidence_id],)
    )
    bundle = ResearchCaseBundle(case=case, evidence_items=items, assertions=())
    errors = validate_research_case_bundle(bundle)
    if errors:
        return _rejected(ctx, inputs, tuple(f"{e.record_type}:{e.record_id}:{e.code}" for e in errors))

    def _write() -> bool:
        if ctx.settings.db_backend in ("sqlite", "postgres"):
            return backend_factory.get_research_case_bundle_writer(ctx.settings).insert_bundle(bundle)
        return research_store.append_research_case_bundle(ctx.settings.cache_dir, bundle)

    written, error = guarded(ctx, NAME, inputs, _write)
    if error is not None:
        return PacketSaveResult(status="rejected", error=error)
    if not written:
        return _rejected(ctx, inputs, ("bundle_write_refused",))
    packet = packet_store.build_packet(
        packet_id=case_id, session_id=ctx.scope.session_id, candidate_id=ctx.scope.candidate_id, issuer_id=ctx.scope.issuer_id,
        source_name=ctx.scope.source_name, saved_at=now, proposal=proposal,
        evidence=tuple(ctx.evidence[e] for e in referenced), evidence_text={e: ctx.evidence_text.get(e, "") for e in referenced},
    )
    saved, error = guarded(ctx, NAME, inputs, lambda: packet_store.save_packet(ctx.settings.cache_dir, packet))
    if error is not None:
        return PacketSaveResult(status="rejected", error=error)
    if not saved:
        return _rejected(ctx, inputs, ("packet_already_exists",))
    ctx.packet_id = case_id
    ctx.audit("research_packet_saved", inputs, f"packet_id={case_id} claims={len(proposal.claims)} evidence={len(referenced)}", tool_name=NAME, case_id=case_id)
    return PacketSaveResult(status="saved", packet_id=case_id)
