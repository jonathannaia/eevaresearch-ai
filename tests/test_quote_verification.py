"""Exact-quote verification (policy row 11): a would-publish decision must
be supported by the excerpt the session actually retrieved.

This closes the risk the production-readiness review called out — a
fluent, well-formed claim citing a real evidence id, asserting something
the document never said. Pure functions and fixtures only."""
from __future__ import annotations

import pytest

from src.logic import quote_verification as qv
from src.logic.publication_policy import PublicationContext, PublicationDecision, evaluate_publication_eligibility
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
    ResolutionConfidence,
    SourceTier,
    Suppression,
)

EXCERPT = (
    "Item 2. Management's Discussion and Analysis. Overview. We operate one of the largest GPU clouds. "
    "All of the GPUs in our infrastructure are NVIDIA GPUs. Revenue increased to $1,212.5 million, "
    "and contracted power capacity rose 12.5% during the quarter."
)
ISSUERS = ("CoreWeave, Inc.", "coreweave")


def _support(statement: str) -> qv.QuoteSupport:
    return qv.verify_claim(statement, [EXCERPT], claim_id="c1", issuer_names=ISSUERS)


# --- what must be accepted ------------------------------------------------------

@pytest.mark.parametrize("statement", [
    "All of the GPUs in our infrastructure are NVIDIA GPUs.",
    "CoreWeave states that all of the GPUs in its infrastructure are NVIDIA GPUs.",
    "CoreWeave, Inc. reported that revenue increased to $1,212.5 million.",
    "According to the filing, contracted power capacity rose 12.5% during the quarter.",
    "all of the gpus in our infrastructure are nvidia gpus",
    "All of the GPUs in our infrastructure are NVIDIA GPUs—as disclosed.",
])
def test_a_faithful_restatement_is_supported(statement):
    assert _support(statement).verified is True


def test_thousands_separators_and_currency_symbols_do_not_break_a_match():
    assert qv.verify_claim("Revenue increased to $1212.5 million.", [EXCERPT], issuer_names=ISSUERS).verified is True


# --- what must be refused --------------------------------------------------------

def test_a_figure_the_document_never_states_is_fatal():
    result = _support("CoreWeave reported that revenue increased to $2,000.0 million.")
    assert result.verified is False
    assert "2000.0" in result.missing_numbers and "figures not in the excerpt" in result.detail


def test_a_percentage_the_document_never_states_is_fatal():
    result = _support("Contracted power capacity rose 40% during the quarter.")
    assert result.verified is False and "40%" in result.missing_numbers


def test_an_invented_counterparty_is_refused():
    result = _support("CoreWeave states that all of the GPUs in its infrastructure are supplied by Acme Semiconductor.")
    assert result.verified is False
    assert "acme" in result.missing_words and "semiconductor" in result.missing_words


def test_a_fact_from_somewhere_else_entirely_is_refused():
    result = _support("CoreWeave announced a share buyback programme of $500 million.")
    assert result.verified is False


def test_an_empty_or_missing_excerpt_is_never_treated_as_support():
    assert qv.verify_claim("Anything at all.", [], claim_id="c1").verified is False
    assert qv.verify_claim("Anything at all.", [""], claim_id="c1").detail == "no stored excerpt to verify against"
    assert qv.verify_claim("", [EXCERPT], claim_id="c1").verified is False


def test_the_issuer_name_is_allowed_but_only_the_issuer_name():
    assert qv.verify_claim("CoreWeave operates one of the largest GPU clouds.", [EXCERPT],
                           issuer_names=ISSUERS).verified is True
    assert qv.verify_claim("Databricks operates one of the largest GPU clouds.", [EXCERPT],
                           issuer_names=ISSUERS).verified is False


# --- per-claim scoping -------------------------------------------------------------

def _evidence(evidence_id: str = "ev-1") -> EvidenceRecord:
    """Carries a locator, not the text — exactly as the real record does."""
    return EvidenceRecord(
        evidence_id=evidence_id, session_id="sess-1", issuer_id="coreweave", source_tier=SourceTier.SEC_EDGAR,
        source_name="SEC EDGAR", source_url="https://example.test/a", source_document_id="acc-1",
        source_date="2026-09-10", excerpt_or_locator="section:Item 2", excerpt_sha256="sha",
        retrieved_at="2026-09-17T12:00:00+00:00", confidence="High", freshness_status=FreshnessStatus.CURRENT,
    )


def _claim(claim_id: str, statement: str, evidence_ids: tuple[str, ...]) -> Claim:
    return Claim(
        claim_id=claim_id, claim_type=ClaimType.DIRECT_REPORTED_FACT, claim_category=ClaimCategory.DIRECT_FACT,
        headline="h", issuer_id="coreweave", statement=statement, evidence_ids=evidence_ids,
        what_this_does_not_establish="Nothing further.",
    )


def test_each_claim_is_checked_only_against_the_evidence_it_cites():
    proposal = ClaimProposal(
        session_id="sess-1", candidate_id="cand-1", retrieved_evidence_ids=("ev-1", "ev-2"),
        claims=(
            _claim("c1", "All of the GPUs in our infrastructure are NVIDIA GPUs.", ("ev-1",)),
            _claim("c2", "All of the GPUs in our infrastructure are NVIDIA GPUs.", ("ev-2",)),
        ),
    )
    support = qv.verify_proposal(
        proposal, {"ev-1": _evidence("ev-1"), "ev-2": _evidence("ev-2")},
        issuer_names=ISSUERS, evidence_text={"ev-1": EXCERPT, "ev-2": "An unrelated paragraph about datacenter leases."},
    )
    assert support["c1"].verified is True
    assert support["c2"].verified is False
    assert qv.unsupported_claim_ids(support) == ("c2",)


def test_the_retrieved_text_is_preferred_over_the_locator_string():
    proposal = ClaimProposal(session_id="s", candidate_id="c", retrieved_evidence_ids=("ev-1",),
                             claims=(_claim("c1", "All of the GPUs in our infrastructure are NVIDIA GPUs.", ("ev-1",)),))
    # The record itself only carries "section:Item 2"; without the text the
    # claim cannot be supported, with it the claim is verified.
    without = qv.verify_proposal(proposal, {"ev-1": _evidence()}, issuer_names=ISSUERS)
    with_text = qv.verify_proposal(proposal, {"ev-1": _evidence()}, issuer_names=ISSUERS,
                                   evidence_text={"ev-1": EXCERPT})
    assert without["c1"].verified is False and with_text["c1"].verified is True


# --- policy row 11 ---------------------------------------------------------------------

def _policy_context(**overrides) -> PublicationContext:
    defaults = dict(
        issuer_resolution=IssuerResolution(ResolutionConfidence.EXACT, issuer_id="coreweave",
                                           tracked_company_name="CoreWeave, Inc.", exchange="NASDAQ"),
        suppression=Suppression.NONE,
        evidence_by_id={"ev-1": _evidence()},
        prior_comparison=PriorFilingComparison(),
        relationship_context=RelationshipContextResult(RelationshipContextStatus.AVAILABLE, edges=()),
        quote_support={"c1": True},
    )
    defaults.update(overrides)
    return PublicationContext(**defaults)


def _policy_proposal() -> ClaimProposal:
    return ClaimProposal(session_id="sess-1", candidate_id="cand-1", retrieved_evidence_ids=("ev-1",),
                         claims=(_claim("c1", "All of the GPUs in our infrastructure are NVIDIA GPUs.", ("ev-1",)),))


def test_row11_passes_when_every_claim_is_verified():
    decision = evaluate_publication_eligibility(_policy_proposal(), _policy_context())
    assert decision.decision is PublicationDecision.AUTO_PUBLISHED
    assert decision.row_results[-1].row == 11 and decision.row_results[-1].passed is True


def test_row11_refuses_an_unverified_claim_and_says_why():
    decision = evaluate_publication_eligibility(
        _policy_proposal(),
        _policy_context(quote_support={"c1": False}, quote_support_detail={"c1": "figures not in the excerpt: 40%"}),
    )
    assert decision.decision is PublicationDecision.REVIEW_REQUIRED
    assert decision.row_results[-1].row == 11 and decision.row_results[-1].passed is False
    assert "40%" in decision.reasons[0]


def test_row11_fails_closed_when_verification_never_ran():
    """An absent result is not a pass: no support means no publication."""
    decision = evaluate_publication_eligibility(_policy_proposal(), _policy_context(quote_support={}))
    assert decision.decision is PublicationDecision.REVIEW_REQUIRED
    assert decision.row_results[-1].row == 11


def test_row11_is_evaluated_after_every_other_row_has_passed():
    """Verification is the last gate, so a decision that reaches it has
    already resolved its issuer, evidence and category. (The draft half of
    the gate is covered in tests/test_publication_policy.py, where the
    relationship-registry fixtures live.)"""
    decision = evaluate_publication_eligibility(_policy_proposal(), _policy_context())
    assert [r.row for r in decision.row_results] == list(range(12))
