"""Autonomous Research Agent, Phase 3 — the publication-policy matrix
(design/AUTONOMOUS_EVIDENCE_FIRST_RESEARCH_AGENT_DESIGN_2026_09_17.md,
§5.2) and the six mandatory regression cases (§10), exercised as
pure-function tests over constructed fixtures — no network, no model call,
no filesystem (design §12 Phase 3, §13 offline harness).

Acceptance criteria covered (§13): (1) every §10 example reaches exactly
its stated terminal state deterministically; (2) nothing but a
direct_reported_fact can auto-publish; (4) an AUTO_PUBLISHED decision
passes every row on independent re-evaluation; (5) the kill switch
drives 100% of decisions to REVIEW_REQUIRED."""
from __future__ import annotations

import pytest

from src.data_access.dart.equity_transaction_materiality import assess_equity_transaction_materiality
from src.logic import publication_policy as policy
from src.logic.publication_policy import (
    DECISION_TO_CANDIDATE_STATUS,
    PublicationContext,
    PublicationDecision,
    classify_claim,
    evaluate_publication_eligibility,
)
from src.mcp_agent.contracts import (
    Claim,
    ClaimCategory,
    ClaimProposal,
    ClaimType,
    EvidenceRecord,
    FreshnessStatus,
    IssuerResolution,
    PriorFilingComparison,
    RelationshipContextResult,
    RelationshipContextStatus,
    RelationshipEdgeContext,
    ResolutionConfidence,
    SourceTier,
    Suppression,
    ToolError,
    ToolErrorKind,
)
from src.models.models import CandidateStatus

# --- fixtures ---------------------------------------------------------------

_AT = "2026-09-17T12:00:00+00:00"


def _evidence(
    evidence_id: str = "ev-1", *, issuer_id: str = "coreweave", tier: SourceTier = SourceTier.SEC_EDGAR,
    freshness: FreshnessStatus = FreshnessStatus.CURRENT, source_url: str = "https://www.sec.gov/Archives/edgar/data/1769628/000176962826000042/",
    document_id: str = "0001769628-26-000042", **overrides,
) -> EvidenceRecord:
    defaults = dict(
        evidence_id=evidence_id, session_id="sess-1", issuer_id=issuer_id, source_tier=tier, source_name="SEC EDGAR",
        source_url=source_url, source_document_id=document_id, source_date="2026-08-14",
        excerpt_or_locator="Item 2 — Management's Discussion, paragraph 3", excerpt_sha256="d3adb33f", retrieved_at=_AT,
        confidence="high", freshness_status=freshness,
    )
    defaults.update(overrides)
    return EvidenceRecord(**defaults)


def _claim(
    claim_id: str = "c1", *, issuer_id: str = "coreweave", statement: str = "CoreWeave states that all GPUs in its infrastructure are NVIDIA GPUs.",
    headline: str = "CoreWeave reports an all-NVIDIA GPU fleet", evidence_ids: tuple[str, ...] = ("ev-1",),
    counterparty_issuer_id: str | None = None, category: ClaimCategory = ClaimCategory.DIRECT_FACT,
    factual_context_evidence_ids: tuple[str, ...] = (), claim_type: ClaimType = ClaimType.DIRECT_REPORTED_FACT,
) -> Claim:
    return Claim(
        claim_id=claim_id, claim_type=claim_type, claim_category=category, headline=headline, issuer_id=issuer_id,
        statement=statement, evidence_ids=evidence_ids,
        what_this_does_not_establish="Does not establish future GPU sourcing, pricing terms, or exclusivity duration.",
        factual_context_evidence_ids=factual_context_evidence_ids, counterparty_issuer_id=counterparty_issuer_id,
    )


def _proposal(*claims: Claim, retrieved: tuple[str, ...] | None = None) -> ClaimProposal:
    claims = claims or (_claim(),)
    if retrieved is None:
        retrieved = tuple(dict.fromkeys(e for c in claims for e in (*c.evidence_ids, *c.factual_context_evidence_ids)))
    return ClaimProposal(session_id="sess-1", candidate_id="cand-1", claims=claims, retrieved_evidence_ids=retrieved)


_UNAVAILABLE = RelationshipContextResult(RelationshipContextStatus.UNAVAILABLE, detail="registry not on this branch")


def _available(*edges: RelationshipEdgeContext) -> RelationshipContextResult:
    return RelationshipContextResult(RelationshipContextStatus.AVAILABLE, edges=edges)


def _edge(counterparty: str, relationship_type: str = "supplier", status: str = "active", direction: str = "issuer_to_counterparty") -> RelationshipEdgeContext:
    return RelationshipEdgeContext(relationship_type=relationship_type, status=status, direction=direction, confidence="high", counterparty_issuer_id=counterparty, evidence_ids=("rel-ev-1",))


def _context(**overrides) -> PublicationContext:
    defaults = dict(
        issuer_resolution=IssuerResolution(ResolutionConfidence.EXACT, issuer_id="coreweave", tracked_company_name="CoreWeave, Inc.", exchange="NASDAQ"),
        suppression=Suppression.NONE,
        evidence_by_id={"ev-1": _evidence()},
        prior_comparison=PriorFilingComparison(),
        relationship_context=_UNAVAILABLE,
    )
    defaults.update(overrides)
    return PublicationContext(**defaults)


def _last_row(decision: policy.PolicyDecision) -> int:
    return decision.row_results[-1].row


NEVER_PUBLIC = {PublicationDecision.AUTO_PUBLISHED, PublicationDecision.VERIFIED_DRAFT}


# --- happy path + determinism ------------------------------------------------

def test_fully_verified_direct_fact_auto_publishes_with_every_row_passing():
    decision = evaluate_publication_eligibility(_proposal(), _context())
    assert decision.decision is PublicationDecision.AUTO_PUBLISHED
    assert [r.row for r in decision.row_results] == list(range(11))
    assert all(r.passed for r in decision.row_results)
    assert decision.surviving_claim_ids == ("c1",)
    assert decision.content_hash


def test_evaluation_is_deterministic_across_repeated_runs():
    runs = [evaluate_publication_eligibility(_proposal(), _context()) for _ in range(5)]
    assert all(run == runs[0] for run in runs)


def test_auto_published_decision_re_evaluates_identically_against_persisted_inputs():
    # §13 criterion 4 — an out-of-band second evaluation of the same
    # packet must reproduce the same row-by-row verdict.
    first = evaluate_publication_eligibility(_proposal(), _context())
    second = evaluate_publication_eligibility(_proposal(), _context())
    assert first.as_row_tuples() == second.as_row_tuples()
    assert all(passed for _, _, passed, _ in second.as_row_tuples())


def test_decision_to_candidate_status_reuses_existing_statuses_and_maps_new_ones():
    assert DECISION_TO_CANDIDATE_STATUS[PublicationDecision.AUTO_PUBLISHED] is CandidateStatus.PUBLISHED
    assert DECISION_TO_CANDIDATE_STATUS[PublicationDecision.REVIEW_REQUIRED] is CandidateStatus.NEEDS_REVIEW
    assert DECISION_TO_CANDIDATE_STATUS[PublicationDecision.FAILED_RETRIEVAL] is CandidateStatus.RETRIEVAL_FAILED
    assert DECISION_TO_CANDIDATE_STATUS[PublicationDecision.NOT_MATERIAL] is CandidateStatus.NOT_MATERIAL
    assert DECISION_TO_CANDIDATE_STATUS[PublicationDecision.VERIFIED_DRAFT] is CandidateStatus.VERIFIED_DRAFT
    assert DECISION_TO_CANDIDATE_STATUS[PublicationDecision.INSUFFICIENT_EVIDENCE] is CandidateStatus.INSUFFICIENT_EVIDENCE
    assert PublicationDecision.DUPLICATE not in DECISION_TO_CANDIDATE_STATUS


# --- row 0: kill switch (§11, §13 criterion 5) -------------------------------

def test_kill_switch_forces_every_decision_to_review_required():
    otherwise_public = evaluate_publication_eligibility(_proposal(), _context())
    assert otherwise_public.decision is PublicationDecision.AUTO_PUBLISHED
    killed = evaluate_publication_eligibility(_proposal(), _context(kill_switch_enabled=True))
    assert killed.decision is PublicationDecision.REVIEW_REQUIRED
    assert _last_row(killed) == 0 and killed.reasons == ("row0:kill_switch_enabled",)
    assert len(killed.row_results) == 1  # nothing after the switch is evaluated


# --- rows 1–10, one fail case each --------------------------------------------

def test_row1_unapproved_source_tier_routes_to_review_required():
    ctx = _context(evidence_by_id={"ev-1": _evidence(tier=SourceTier.UNKNOWN, source_url="https://example-blog.com/post")})
    decision = evaluate_publication_eligibility(_proposal(), ctx)
    assert decision.decision is PublicationDecision.REVIEW_REQUIRED and _last_row(decision) == 1


def test_row2_issuer_resolution_error_routes_to_failed_retrieval():
    ctx = _context(issuer_resolution=IssuerResolution(ResolutionConfidence.UNRESOLVED, error=ToolError(ToolErrorKind.RETRIEVAL_FAILED, "boom")))
    decision = evaluate_publication_eligibility(_proposal(), ctx)
    assert decision.decision is PublicationDecision.FAILED_RETRIEVAL and _last_row(decision) == 2


def test_row2_session_retrieval_error_routes_to_failed_retrieval():
    decision = evaluate_publication_eligibility(_proposal(), _context(retrieval_error=True))
    assert decision.decision is PublicationDecision.FAILED_RETRIEVAL and _last_row(decision) == 2


def test_row2_ambiguous_issuer_routes_to_review_required():
    ctx = _context(issuer_resolution=IssuerResolution(ResolutionConfidence.AMBIGUOUS, candidates=("coreweave", "core-scientific")))
    decision = evaluate_publication_eligibility(_proposal(), ctx)
    assert decision.decision is PublicationDecision.REVIEW_REQUIRED and _last_row(decision) == 2


def test_row2_claim_about_a_different_issuer_than_resolved_routes_to_review_required():
    decision = evaluate_publication_eligibility(_proposal(_claim(issuer_id="nvidia")), _context())
    assert decision.decision is PublicationDecision.REVIEW_REQUIRED and _last_row(decision) == 2
    assert "claim_issuer_mismatch" in decision.reasons[0]


def test_row3_suppressed_filing_routes_to_not_material():
    decision = evaluate_publication_eligibility(_proposal(), _context(suppression=Suppression.ROUTINE_EXCLUDE))
    assert decision.decision is PublicationDecision.NOT_MATERIAL and _last_row(decision) == 3


def test_row4_schema_violation_routes_to_insufficient_evidence():
    decision = evaluate_publication_eligibility(_proposal(_claim(headline="x" * 141)), _context())
    assert decision.decision is PublicationDecision.INSUFFICIENT_EVIDENCE and _last_row(decision) == 4
    assert "headline_too_long" in decision.reasons[0]


def test_row4_evidence_id_never_retrieved_this_session_routes_to_insufficient_evidence():
    decision = evaluate_publication_eligibility(_proposal(_claim(), retrieved=()), _context())
    assert decision.decision is PublicationDecision.INSUFFICIENT_EVIDENCE and _last_row(decision) == 4
    assert "not_retrieved_this_session" in decision.reasons[0]


@pytest.mark.parametrize("statement,expected_category", [
    ("Analysts say investors should buy the stock with a price target of $150.", ClaimCategory.RECOMMENDATION_OR_PRICE_TARGET),
    ("The NVIDIA dependence will lead to margin expansion and is likely to drive re-rating.", ClaimCategory.SECOND_ORDER_INFERENCE),
    ("CoreWeave reportedly plans to switch GPU vendors, people familiar with the matter said.", ClaimCategory.RUMOR_OR_ANONYMOUS),
])
def test_row5_non_direct_fact_content_routes_to_review_required(statement, expected_category):
    claim = _claim(statement=statement)
    assert classify_claim(claim) is expected_category
    decision = evaluate_publication_eligibility(_proposal(claim), _context())
    assert decision.decision is PublicationDecision.REVIEW_REQUIRED and _last_row(decision) == 5


def test_row5_declared_taxonomy_change_is_never_promoted_by_the_classifier():
    claim = _claim(category=ClaimCategory.TAXONOMY_CHANGE)
    assert classify_claim(claim) is ClaimCategory.TAXONOMY_CHANGE
    decision = evaluate_publication_eligibility(_proposal(claim), _context())
    assert decision.decision is PublicationDecision.REVIEW_REQUIRED and _last_row(decision) == 5


def test_row7_unresolved_evidence_id_routes_to_insufficient_evidence():
    decision = evaluate_publication_eligibility(_proposal(), _context(evidence_by_id={}))
    assert decision.decision is PublicationDecision.INSUFFICIENT_EVIDENCE and _last_row(decision) == 7


@pytest.mark.parametrize("freshness", [FreshnessStatus.STALE, FreshnessStatus.SUPERSEDED, FreshnessStatus.DISPUTED, FreshnessStatus.CONTRADICTORY, FreshnessStatus.UNRESOLVED])
def test_row7_non_current_evidence_routes_to_insufficient_evidence(freshness):
    decision = evaluate_publication_eligibility(_proposal(), _context(evidence_by_id={"ev-1": _evidence(freshness=freshness)}))
    assert decision.decision is PublicationDecision.INSUFFICIENT_EVIDENCE and _last_row(decision) == 7
    assert f"freshness_{freshness.value}" in decision.reasons[0]


def test_row7_incomplete_evidence_record_routes_to_insufficient_evidence():
    decision = evaluate_publication_eligibility(_proposal(), _context(evidence_by_id={"ev-1": _evidence(source_date="")}))
    assert decision.decision is PublicationDecision.INSUFFICIENT_EVIDENCE and _last_row(decision) == 7
    assert "blank_source_date" in decision.reasons[0]


def test_row8_newer_filing_routes_to_review_required():
    ctx = _context(prior_comparison=PriorFilingComparison(has_newer_filing=True, prior_document_ids=("0001769628-26-000050",)))
    decision = evaluate_publication_eligibility(_proposal(), ctx)
    assert decision.decision is PublicationDecision.REVIEW_REQUIRED and _last_row(decision) == 8


def test_row8_superseding_disclosure_routes_to_review_required():
    ctx = _context(prior_comparison=PriorFilingComparison(has_superseding_disclosure=True, prior_document_ids=("0001769628-26-000050",)))
    decision = evaluate_publication_eligibility(_proposal(), ctx)
    assert decision.decision is PublicationDecision.REVIEW_REQUIRED and _last_row(decision) == 8


def test_row8_prior_comparison_error_fails_closed_to_review_required():
    ctx = _context(prior_comparison=PriorFilingComparison(error=ToolError(ToolErrorKind.RETRIEVAL_FAILED, "boom")))
    decision = evaluate_publication_eligibility(_proposal(), ctx)
    assert decision.decision is PublicationDecision.REVIEW_REQUIRED and _last_row(decision) == 8


def test_row9_duplicate_content_hash_terminates_silently_as_duplicate():
    hash_ = policy.content_hash(_proposal())
    decision = evaluate_publication_eligibility(_proposal(), _context(previously_published_hashes=frozenset({hash_})))
    assert decision.decision is PublicationDecision.DUPLICATE and _last_row(decision) == 9


def test_row9_duplicate_evidence_id_set_terminates_silently_as_duplicate():
    decision = evaluate_publication_eligibility(_proposal(), _context(previously_published_evidence_sets=frozenset({frozenset({"ev-1"})})))
    assert decision.decision is PublicationDecision.DUPLICATE and _last_row(decision) == 9


def test_row10_is_the_only_path_to_verified_draft():
    # A relationship claim whose registry edge exists, is active, and
    # matches the asserted type and direction passes rows 1–9 — and is
    # then withheld by category alone. Every fact and every evidence
    # record is already verified; only the category needs a human glance.
    claim = _claim(statement="Fabrinet supplies Coherent with optical assemblies.", issuer_id="fabrinet", counterparty_issuer_id="coherent")
    ctx = _context(
        issuer_resolution=IssuerResolution(ResolutionConfidence.EXACT, issuer_id="fabrinet", tracked_company_name="Fabrinet"),
        evidence_by_id={"ev-1": _evidence(issuer_id="fabrinet")},
        relationship_context=_available(_edge("coherent", "supplier", "active", "issuer_to_counterparty")),
    )
    decision = evaluate_publication_eligibility(_proposal(claim), ctx)
    assert decision.decision is PublicationDecision.VERIFIED_DRAFT and _last_row(decision) == 10
    assert all(r.passed for r in decision.row_results[:-1])


# --- fail closed --------------------------------------------------------------

def test_internal_evaluation_error_fails_closed_to_review_required(monkeypatch):
    def explode(_proposal):
        raise RuntimeError("validator bug")
    monkeypatch.setattr(policy, "validate_claim_proposal", explode)
    decision = evaluate_publication_eligibility(_proposal(), _context())
    assert decision.decision is PublicationDecision.REVIEW_REQUIRED
    assert decision.reasons == ("policy_evaluation_error:RuntimeError",)


def test_only_direct_reported_fact_claim_type_exists():
    # §13 criterion 2, enforced at the type level: the enum has one member.
    assert [m.value for m in ClaimType] == ["direct_reported_fact"]


# =============================================================================
# The six mandatory regression cases (design §10)
# =============================================================================

_WONIK_IPS_TITLE = "주요사항보고서(자기주식처분결정)"
_WONIK_IPS_EXCERPT_CONSTRUCTED = (
    "자기주식처분결정 1. 처분예정주식(주) 보통주식 51,456 2. 처분예정금액(원) 6,143,846,400 "
    "3. 처분목적 임직원 성과급 지급을 위한 자기주식 처분 4. 처분방법 시간외대량매매 "
    "5. 처분예정기간 2026년 09월 17일 ~ 2026년 09월 18일"
)


def test_case1_wonik_ips_employee_treasury_share_disposal_is_not_material_and_never_drafted():
    # Suppression is derived from the REAL deterministic gate on the
    # existing fixture strings (tests/test_equity_transaction_materiality.py),
    # not a hardcoded flag — the same function radar_pipeline applies.
    materiality = assess_equity_transaction_materiality(_WONIK_IPS_TITLE, _WONIK_IPS_EXCERPT_CONSTRUCTED)
    assert materiality.outcome == "not_material"
    ctx = _context(
        issuer_resolution=IssuerResolution(ResolutionConfidence.EXACT, issuer_id="wonik-ips", tracked_company_name="Wonik IPS", exchange="KOSDAQ"),
        suppression=Suppression.NOT_MATERIAL, suppression_detail=materiality.detail,
        evidence_by_id={"ev-1": _evidence(issuer_id="wonik-ips", tier=SourceTier.DART, source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260917000123", document_id="20260917000123")},
    )
    claim = _claim(issuer_id="wonik-ips", headline="Wonik IPS disposes of treasury shares", statement="Wonik IPS will dispose of 51,456 treasury shares for employee incentive payments.")
    decision = evaluate_publication_eligibility(_proposal(claim), ctx)
    assert decision.decision is PublicationDecision.NOT_MATERIAL
    assert DECISION_TO_CANDIDATE_STATUS[decision.decision] is CandidateStatus.NOT_MATERIAL
    # Terminated at row 3: nothing was drafted or validated beyond it.
    assert _last_row(decision) == 3 and len(decision.row_results) == 4
    assert decision.surviving_claim_ids == ()
    # Retained: evaluation is pure — the filing's evidence is untouched.
    assert "ev-1" in ctx.evidence_by_id


def test_case2_coreweave_nvidia_gpu_statement_publishes_only_when_every_direct_fact_condition_passes():
    ctx = _context()
    decision = evaluate_publication_eligibility(_proposal(), ctx)
    assert decision.decision is PublicationDecision.AUTO_PUBLISHED
    assert DECISION_TO_CANDIDATE_STATUS[decision.decision] is CandidateStatus.PUBLISHED
    assert ctx.evidence_by_id["ev-1"].source_tier is SourceTier.SEC_EDGAR
    assert classify_claim(_claim()) is ClaimCategory.DIRECT_FACT
    # Flip any single condition and publication is withheld.
    assert evaluate_publication_eligibility(_proposal(), _context(evidence_by_id={"ev-1": _evidence(freshness=FreshnessStatus.STALE)})).decision is PublicationDecision.INSUFFICIENT_EVIDENCE
    assert evaluate_publication_eligibility(_proposal(), _context(prior_comparison=PriorFilingComparison(has_newer_filing=True))).decision is PublicationDecision.REVIEW_REQUIRED
    assert evaluate_publication_eligibility(_proposal(), _context(evidence_by_id={"ev-1": _evidence(tier=SourceTier.UNKNOWN)})).decision is PublicationDecision.REVIEW_REQUIRED
    assert evaluate_publication_eligibility(_proposal(_claim(statement="CoreWeave's NVIDIA fleet suggests that pricing power is likely to improve.")), _context()).decision is PublicationDecision.REVIEW_REQUIRED


def _fabrinet_context(relationship_context: RelationshipContextResult) -> PublicationContext:
    return _context(
        issuer_resolution=IssuerResolution(ResolutionConfidence.EXACT, issuer_id="fabrinet", tracked_company_name="Fabrinet", exchange="NYSE"),
        evidence_by_id={"ev-1": _evidence(issuer_id="fabrinet")},
        relationship_context=relationship_context,
    )


_FABRINET_SUPPLIER_CLAIM = _claim(issuer_id="fabrinet", headline="Fabrinet named as Coherent supplier", statement="Fabrinet supplies Coherent with optical assemblies.", counterparty_issuer_id="coherent")


def test_case3_fabrinet_coherent_supplier_claim_blocked_when_primary_evidence_names_coherent_as_competitor():
    registry = _available(_edge("coherent", relationship_type="competitor", status="active", direction="bidirectional"))
    decision = evaluate_publication_eligibility(_proposal(_FABRINET_SUPPLIER_CLAIM), _fabrinet_context(registry))
    assert decision.decision is PublicationDecision.REVIEW_REQUIRED and _last_row(decision) == 8
    assert "relationship_type_contradiction" in decision.reasons[0]
    assert "asserted=supplier" in decision.reasons[0] and "registry=competitor" in decision.reasons[0]


def test_case3_fabrinet_coherent_supplier_claim_blocked_on_main_where_the_registry_is_unavailable():
    # feat/supply-chain-relationship-seed-v1 is not merged: the registry
    # reports UNAVAILABLE, which row 8 treats as "cannot confirm no
    # contradiction exists" — never as "no relationship exists".
    decision = evaluate_publication_eligibility(_proposal(_FABRINET_SUPPLIER_CLAIM), _fabrinet_context(_UNAVAILABLE))
    assert decision.decision is PublicationDecision.REVIEW_REQUIRED and _last_row(decision) == 8
    assert decision.reasons == ("row8:relationship_registry_unavailable_cannot_confirm_no_contradiction",)


def _ciena_context(relationship_context: RelationshipContextResult) -> PublicationContext:
    return _context(
        issuer_resolution=IssuerResolution(ResolutionConfidence.EXACT, issuer_id="ciena", tracked_company_name="Ciena", exchange="NYSE"),
        evidence_by_id={"ev-1": _evidence(issuer_id="ciena")},
        relationship_context=relationship_context,
    )


# The claim asserts supply flowing issuer -> counterparty ("Ciena supplies
# Marvell"); the registry's only Ciena–Marvell edge is the same type but
# points the other way (Marvell supplies Ciena). Same two parties, same
# nominal type — the block must come from direction, not entity or type.
_CIENA_SUPPLIES_MARVELL_CLAIM = _claim(issuer_id="ciena", headline="Ciena supplies Marvell", statement="Ciena supplies Marvell with coherent optical DSPs.", counterparty_issuer_id="marvell")


def test_case4_ciena_marvell_directionally_incorrect_relationship_claim_is_blocked():
    registry = _available(_edge("marvell", relationship_type="supplier", status="active", direction="counterparty_to_issuer"))
    decision = evaluate_publication_eligibility(_proposal(_CIENA_SUPPLIES_MARVELL_CLAIM), _ciena_context(registry))
    assert decision.decision is PublicationDecision.REVIEW_REQUIRED and _last_row(decision) == 8
    assert "relationship_direction_contradiction" in decision.reasons[0]
    assert "asserted=issuer_to_counterparty" in decision.reasons[0] and "registry=counterparty_to_issuer" in decision.reasons[0]


def test_case4_ciena_marvell_claim_with_no_registry_edge_is_unverified_and_blocked():
    registry = _available(_edge("nokia", relationship_type="customer", status="active"))
    decision = evaluate_publication_eligibility(_proposal(_CIENA_SUPPLIES_MARVELL_CLAIM), _ciena_context(registry))
    assert decision.decision is PublicationDecision.REVIEW_REQUIRED and _last_row(decision) == 8
    assert decision.reasons[0].startswith("row8:unverified_relationship:")


def _amat_context(relationship_context: RelationshipContextResult) -> PublicationContext:
    return _context(
        issuer_resolution=IssuerResolution(ResolutionConfidence.EXACT, issuer_id="applied-materials", tracked_company_name="Applied Materials", exchange="NASDAQ"),
        evidence_by_id={"ev-1": _evidence(issuer_id="applied-materials"), "ev-hist": _evidence("ev-hist", issuer_id="applied-materials", freshness=FreshnessStatus.SUPERSEDED, source_date="2023-11-16", document_id="0000006951-23-000045")},
        relationship_context=relationship_context,
    )


@pytest.mark.parametrize("edge_status", ["superseded", "lapsed", "historical", "disputed", "unverified"])
def test_case5_applied_materials_samsung_current_relationship_blocked_while_historical_evidence_is_retained(edge_status):
    claim = _claim(issuer_id="applied-materials", headline="Applied Materials supplies Samsung", statement="Applied Materials supplies Samsung with deposition equipment.", counterparty_issuer_id="samsung")
    ctx = _amat_context(_available(_edge("samsung", relationship_type="supplier", status=edge_status, direction="issuer_to_counterparty")))
    decision = evaluate_publication_eligibility(_proposal(claim), ctx)
    assert decision.decision is PublicationDecision.REVIEW_REQUIRED and _last_row(decision) == 8
    assert decision.reasons[0].startswith(f"row8:relationship_{edge_status}:")
    # Immutable-evidence side: the historical record is never deleted or
    # edited by evaluation — it stays exactly as persisted, just never
    # presented as current.
    assert ctx.evidence_by_id["ev-hist"].freshness_status is FreshnessStatus.SUPERSEDED
    assert ctx.evidence_by_id["ev-hist"].source_date == "2023-11-16"


def test_case5_historical_evidence_cannot_support_a_current_claim_on_its_own():
    claim = _claim(issuer_id="applied-materials", headline="Applied Materials supplies Samsung", statement="Applied Materials supplies Samsung with deposition equipment.", counterparty_issuer_id="samsung", evidence_ids=("ev-hist",))
    ctx = _amat_context(_available(_edge("samsung", relationship_type="supplier", status="active")))
    decision = evaluate_publication_eligibility(_proposal(claim), ctx)
    assert decision.decision is PublicationDecision.INSUFFICIENT_EVIDENCE and _last_row(decision) == 7
    assert "freshness_superseded" in decision.reasons[0]


_YIELD_RUMOR_STATEMENT = "Google is reportedly moving TPU packaging from TSMC CoWoS to Intel EMIB after unconfirmed reports of CoWoS yield problems, people familiar with the matter said."


def test_case6_anonymous_yield_rumor_never_becomes_a_verified_public_claim():
    outcomes = set()
    # (a) Rumor language on otherwise-approved evidence — row 5.
    claim = _claim(issuer_id="alphabet", headline="Google shifts TPU packaging from TSMC to Intel", statement=_YIELD_RUMOR_STATEMENT)
    ctx = _context(issuer_resolution=IssuerResolution(ResolutionConfidence.EXACT, issuer_id="alphabet", tracked_company_name="Alphabet"), evidence_by_id={"ev-1": _evidence(issuer_id="alphabet")})
    decision = evaluate_publication_eligibility(_proposal(claim), ctx)
    assert _last_row(decision) == 5
    outcomes.add(decision.decision)
    # (b) The only "evidence" is a secondary, non-allowlisted source — row 1.
    blog = _evidence(issuer_id="alphabet", tier=SourceTier.UNKNOWN, source_url="https://semianalysis-style-blog.example/cowos-emib", document_id="blog-post-1")
    ctx = _context(issuer_resolution=IssuerResolution(ResolutionConfidence.EXACT, issuer_id="alphabet", tracked_company_name="Alphabet"), evidence_by_id={"ev-1": blog})
    decision = evaluate_publication_eligibility(_proposal(claim), ctx)
    assert _last_row(decision) == 1
    outcomes.add(decision.decision)
    # (c) No resolvable evidence record could be constructed at all — row 7.
    ctx = _context(issuer_resolution=IssuerResolution(ResolutionConfidence.EXACT, issuer_id="alphabet", tracked_company_name="Alphabet"), evidence_by_id={})
    neutral = _claim(issuer_id="alphabet", headline="Google shifts TPU packaging", statement="Google will move TPU packaging from TSMC CoWoS to Intel EMIB.")
    decision = evaluate_publication_eligibility(_proposal(neutral), ctx)
    assert _last_row(decision) == 7
    outcomes.add(decision.decision)

    assert outcomes <= {PublicationDecision.INSUFFICIENT_EVIDENCE, PublicationDecision.REVIEW_REQUIRED}
    assert outcomes.isdisjoint(NEVER_PUBLIC)
