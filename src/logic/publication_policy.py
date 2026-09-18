"""Publication-policy matrix (design §5.2) — the ONLY thing that turns a
private research packet into a public state. A pure function: the agent
supplies no input that can change the outcome once the packet is fixed,
and calling it twice on the same inputs returns the same decision (§6,
request_publication_decision; §13 acceptance criterion 4).

Rows are evaluated in order and the FIRST FAILING ROW WINS. Every row's
pass/fail is recorded in RowResult for the audit stream (§8.2), so a
decision is reproducible without re-running the agent.

Fail closed (§8.1): an unexpected error inside evaluation is itself a
REVIEW_REQUIRED decision, never an exception and never a publication.
The publication kill switch (§11) is checked before row 1.

Row 5 / row 10 use a rule-based classifier over the claim's own text
(classify_claim). It can only DEMOTE a declared category, never promote
one, and its lexicon is deliberately biased toward false positives — a
genuine fact that trips a marker routes to a human (REVIEW_REQUIRED),
which is the safe direction; nothing here can route toward publication.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from src.mcp_agent.contracts import (
    AUTO_PUBLISHABLE_CLAIM_CATEGORIES,
    AUTO_PUBLISHABLE_SOURCE_TIERS,
    Claim,
    ClaimCategory,
    ClaimProposal,
    ClaimType,
    EvidenceRecord,
    IssuerResolution,
    PriorFilingComparison,
    RelationshipContextResult,
    RelationshipContextStatus,
    ResolutionConfidence,
    Suppression,
    validate_claim_proposal,
)
from src.models.models import CandidateStatus


class PublicationDecision(str, Enum):
    AUTO_PUBLISHED = "AUTO_PUBLISHED"
    VERIFIED_DRAFT = "VERIFIED_DRAFT"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NOT_MATERIAL = "NOT_MATERIAL"
    FAILED_RETRIEVAL = "FAILED_RETRIEVAL"
    # Row 9: "terminate silently — already handled, logged, not surfaced
    # as a new failure." Carries no CandidateStatus of its own.
    DUPLICATE = "DUPLICATE"


# design §5.1 — reuse mapping. DUPLICATE deliberately absent: no status change.
DECISION_TO_CANDIDATE_STATUS: dict[PublicationDecision, CandidateStatus] = {
    PublicationDecision.AUTO_PUBLISHED: CandidateStatus.PUBLISHED,
    PublicationDecision.VERIFIED_DRAFT: CandidateStatus.VERIFIED_DRAFT,
    PublicationDecision.REVIEW_REQUIRED: CandidateStatus.NEEDS_REVIEW,
    PublicationDecision.INSUFFICIENT_EVIDENCE: CandidateStatus.INSUFFICIENT_EVIDENCE,
    PublicationDecision.NOT_MATERIAL: CandidateStatus.NOT_MATERIAL,
    PublicationDecision.FAILED_RETRIEVAL: CandidateStatus.RETRIEVAL_FAILED,
}

PUBLIC_DECISIONS: frozenset[PublicationDecision] = frozenset({PublicationDecision.AUTO_PUBLISHED})


@dataclass(frozen=True)
class RowResult:
    row: int
    condition: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class PolicyDecision:
    decision: PublicationDecision
    reasons: tuple[str, ...]
    row_results: tuple[RowResult, ...]
    content_hash: str
    surviving_claim_ids: tuple[str, ...] = ()

    def as_row_tuples(self) -> tuple[tuple[int, str, bool, str], ...]:
        return tuple((r.row, r.condition, r.passed, r.detail) for r in self.row_results)


@dataclass(frozen=True)
class PublicationContext:
    """Everything the matrix needs, all already-persisted/already-validated
    server-side state — nothing here is agent-authored free text."""
    issuer_resolution: IssuerResolution
    suppression: Suppression
    evidence_by_id: Mapping[str, EvidenceRecord]
    prior_comparison: PriorFilingComparison
    relationship_context: RelationshipContextResult
    suppression_detail: str = ""
    previously_published_hashes: frozenset[str] = frozenset()
    previously_published_evidence_sets: frozenset[frozenset[str]] = frozenset()
    kill_switch_enabled: bool = False
    retrieval_error: bool = False


# --- row 5 / row 10 classifier ----------------------------------------------

def _words(*phrases: str) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(re.escape(p) for p in phrases) + r")\b", re.IGNORECASE)


_RUMOR = _words(
    "reportedly", "people familiar", "sources say", "sources said", "rumor", "rumour", "rumored", "rumoured",
    "unconfirmed", "anonymous", "anonymously", "according to sources", "is said to", "are said to",
)
_RECOMMENDATION = _words(
    "buy", "sell", "hold", "overweight", "underweight", "outperform", "underperform", "price target",
    "target price", "upgrade", "downgrade", "we recommend", "undervalued", "overvalued", "fair value",
)
_CAUSAL = _words(
    "because of", "as a result of", "will lead to", "will cause", "will drive", "therefore", "implies that",
    "suggests that", "is likely to", "are likely to", "expected to drive", "will benefit from", "poised to",
)
_RELATIONSHIP = _words(
    "supplies", "supplier to", "supplier of", "supplier for", "sells to", "ships to", "customer of",
    "buys from", "purchases from", "sources from", "partners with", "partner of", "partnership with",
    "competitor of", "competes with",
)

ACCEPTABLE_RELATIONSHIP_STATUSES: frozenset[str] = frozenset({"active"})


def classify_claim(claim: Claim) -> ClaimCategory:
    """Deterministic, demotion-only. The declared category is a hint; the
    text decides, and only ever toward a more restrictive category."""
    if claim.claim_type is not ClaimType.DIRECT_REPORTED_FACT:
        return ClaimCategory.RUMOR_OR_ANONYMOUS
    text = f"{claim.headline}\n{claim.statement}"
    if _RUMOR.search(text):
        return ClaimCategory.RUMOR_OR_ANONYMOUS
    if _RECOMMENDATION.search(text):
        return ClaimCategory.RECOMMENDATION_OR_PRICE_TARGET
    if _CAUSAL.search(text):
        return ClaimCategory.SECOND_ORDER_INFERENCE
    if claim.counterparty_issuer_id or _RELATIONSHIP.search(text):
        return ClaimCategory.RELATIONSHIP_ASSERTION
    return claim.claim_category


_ASSERTED_RELATION: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (_words("supplies", "supplier to", "supplier of", "supplier for", "sells to", "ships to"), "supplier", "issuer_to_counterparty"),
    (_words("customer of", "buys from", "purchases from", "sources from"), "customer", "issuer_to_counterparty"),
    (_words("competitor of", "competes with"), "competitor", "bidirectional"),
    (_words("partners with", "partner of", "partnership with"), "partner", "bidirectional"),
)


def asserted_relationship(claim: Claim) -> tuple[str, str] | None:
    text = f"{claim.headline}\n{claim.statement}"
    for pattern, relation_type, direction in _ASSERTED_RELATION:
        if pattern.search(text):
            return relation_type, direction
    return None


# --- content hash (row 9) ----------------------------------------------------

def content_hash(proposal: ClaimProposal) -> str:
    canonical = [
        {
            "issuer_id": c.issuer_id.strip().lower(),
            "statement": " ".join(c.statement.split()).lower(),
            "evidence_ids": sorted(c.evidence_ids),
        }
        for c in sorted(proposal.claims, key=lambda c: c.claim_id)
    ]
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def evidence_id_set(proposal: ClaimProposal) -> frozenset[str]:
    return frozenset(e for c in proposal.claims for e in c.evidence_ids)


# --- the matrix --------------------------------------------------------------

def evaluate_publication_eligibility(proposal: ClaimProposal, context: PublicationContext) -> PolicyDecision:
    try:
        return _evaluate(proposal, context)
    except Exception as exc:  # noqa: BLE001 — fail closed, never raise into the caller
        return PolicyDecision(
            decision=PublicationDecision.REVIEW_REQUIRED,
            reasons=(f"policy_evaluation_error:{type(exc).__name__}",),
            row_results=(RowResult(0, "policy evaluation completed without error", False, type(exc).__name__),),
            content_hash="",
        )


def _evaluate(proposal: ClaimProposal, context: PublicationContext) -> PolicyDecision:
    rows: list[RowResult] = []
    digest = content_hash(proposal)

    def fail(decision: PublicationDecision, row: int, condition: str, detail: str) -> PolicyDecision:
        rows.append(RowResult(row, condition, False, detail))
        return PolicyDecision(decision, (f"row{row}:{detail}",), tuple(rows), digest)

    def ok(row: int, condition: str, detail: str = "") -> None:
        rows.append(RowResult(row, condition, True, detail))

    # Row 0 — kill switch (§11): checked before any policy row.
    if context.kill_switch_enabled:
        return fail(PublicationDecision.REVIEW_REQUIRED, 0, "publication kill switch disabled", "kill_switch_enabled")
    ok(0, "publication kill switch disabled")

    # Row 1 — only approved source tiers may support an auto-published fact.
    referenced = [e for c in proposal.claims for e in (*c.evidence_ids, *c.factual_context_evidence_ids)]
    unapproved = sorted({
        e for e in referenced
        if e in context.evidence_by_id and context.evidence_by_id[e].source_tier not in AUTO_PUBLISHABLE_SOURCE_TIERS
    })
    if unapproved:
        return fail(PublicationDecision.REVIEW_REQUIRED, 1, "every referenced source is an approved tier", f"unapproved_source_tier:{','.join(unapproved)}")
    ok(1, "every referenced source is an approved tier")

    # Row 2 — issuer resolves to a tracked issuer, and every claim is about it.
    resolution = context.issuer_resolution
    if resolution.error is not None or context.retrieval_error:
        return fail(PublicationDecision.FAILED_RETRIEVAL, 2, "issuer resolves to a tracked issuer", "issuer_resolution_errored" if resolution.error else "retrieval_error_during_session")
    if resolution.resolution_confidence is not ResolutionConfidence.EXACT or not resolution.issuer_id:
        return fail(PublicationDecision.REVIEW_REQUIRED, 2, "issuer resolves to a tracked issuer", f"issuer_{resolution.resolution_confidence.value}")
    foreign = sorted({c.claim_id for c in proposal.claims if c.issuer_id != resolution.issuer_id})
    if foreign:
        return fail(PublicationDecision.REVIEW_REQUIRED, 2, "issuer resolves to a tracked issuer", f"claim_issuer_mismatch:{','.join(foreign)}")
    ok(2, "issuer resolves to a tracked issuer", resolution.issuer_id)

    # Row 3 — low-value suppression (belt; the MCP layer is the suspenders).
    if context.suppression is not Suppression.NONE:
        return fail(PublicationDecision.NOT_MATERIAL, 3, "passes low-value filing suppression", f"suppressed:{context.suppression.value}:{context.suppression_detail}")
    ok(3, "passes low-value filing suppression")

    # Row 4 — structured output validates against the schema.
    violations = validate_claim_proposal(proposal)
    if violations:
        return fail(PublicationDecision.INSUFFICIENT_EVIDENCE, 4, "structured output validates against the schema", "schema:" + ";".join(violations))
    ok(4, "structured output validates against the schema")

    # Row 5 — direct reported fact only (relationship claims proceed to row 8).
    categories = {c.claim_id: classify_claim(c) for c in proposal.claims}
    blocked = sorted(
        f"{cid}={cat.value}" for cid, cat in categories.items()
        if cat in {ClaimCategory.RUMOR_OR_ANONYMOUS, ClaimCategory.RECOMMENDATION_OR_PRICE_TARGET,
                   ClaimCategory.SECOND_ORDER_INFERENCE, ClaimCategory.TAXONOMY_CHANGE}
    )
    if blocked:
        return fail(PublicationDecision.REVIEW_REQUIRED, 5, "content classifies as a direct reported fact", "not_direct_fact:" + ",".join(blocked))
    ok(5, "content classifies as a direct reported fact")

    # Row 6 — every claim has at least one evidence id.
    bare = sorted(c.claim_id for c in proposal.claims if not c.evidence_ids)
    if bare:
        return fail(PublicationDecision.INSUFFICIENT_EVIDENCE, 6, "every claim references at least one evidence id", "no_evidence:" + ",".join(bare))
    ok(6, "every claim references at least one evidence id")

    # Row 7 — every referenced evidence record resolves completely.
    missing = sorted({e for e in referenced if e not in context.evidence_by_id})
    if missing:
        return fail(PublicationDecision.INSUFFICIENT_EVIDENCE, 7, "every referenced evidence record resolves", "unresolved_evidence:" + ",".join(missing))
    incomplete = sorted(
        f"{e}:{'|'.join(v)}" for e in dict.fromkeys(referenced)
        if (v := context.evidence_by_id[e].resolution_violations())
    )
    if incomplete:
        return fail(PublicationDecision.INSUFFICIENT_EVIDENCE, 7, "every referenced evidence record resolves", "incomplete_evidence:" + ",".join(incomplete))
    ok(7, "every referenced evidence record resolves")

    # Row 8 — no newer/stale/superseded/disputed/contradictory evidence.
    comparison = context.prior_comparison
    if comparison.error is not None:
        return fail(PublicationDecision.REVIEW_REQUIRED, 8, "no unresolved newer, stale, superseded, disputed, or contradictory evidence", "prior_comparison_errored")
    if comparison.has_superseding_disclosure:
        return fail(PublicationDecision.REVIEW_REQUIRED, 8, "no unresolved newer, stale, superseded, disputed, or contradictory evidence", "superseding_disclosure:" + ",".join(comparison.prior_document_ids))
    if comparison.has_newer_filing:
        return fail(PublicationDecision.REVIEW_REQUIRED, 8, "no unresolved newer, stale, superseded, disputed, or contradictory evidence", "newer_filing:" + ",".join(comparison.prior_document_ids))
    relationship_claims = [c for c in proposal.claims if categories[c.claim_id] is ClaimCategory.RELATIONSHIP_ASSERTION]
    if relationship_claims:
        detail = _relationship_contradiction(relationship_claims, context.relationship_context)
        if detail:
            return fail(PublicationDecision.REVIEW_REQUIRED, 8, "no unresolved newer, stale, superseded, disputed, or contradictory evidence", detail)
    ok(8, "no unresolved newer, stale, superseded, disputed, or contradictory evidence")

    # Row 9 — duplicate / idempotency.
    if digest in context.previously_published_hashes or evidence_id_set(proposal) in context.previously_published_evidence_sets:
        return fail(PublicationDecision.DUPLICATE, 9, "not a duplicate of a prior packet", "duplicate_content_or_evidence_set")
    ok(9, "not a duplicate of a prior packet")

    # Row 10 — category within the auto-publish allowlist.
    outside = sorted(f"{cid}={cat.value}" for cid, cat in categories.items() if cat not in AUTO_PUBLISHABLE_CLAIM_CATEGORIES)
    surviving = tuple(c.claim_id for c in proposal.claims)
    if outside:
        rows.append(RowResult(10, "category within the auto-publish allowlist", False, "category_requires_review:" + ",".join(outside)))
        return PolicyDecision(PublicationDecision.VERIFIED_DRAFT, ("row10:category_requires_review:" + ",".join(outside),), tuple(rows), digest, surviving)
    ok(10, "category within the auto-publish allowlist")
    return PolicyDecision(PublicationDecision.AUTO_PUBLISHED, ("all_rows_passed",), tuple(rows), digest, surviving)


def _relationship_contradiction(claims: list[Claim], rel: RelationshipContextResult) -> str | None:
    """Row 8's relationship half. UNAVAILABLE means 'cannot confirm no
    contradiction exists' — never 'no relationship exists' (design §6)."""
    if rel.status is not RelationshipContextStatus.AVAILABLE or rel.error is not None:
        return "relationship_registry_unavailable_cannot_confirm_no_contradiction"
    for claim in claims:
        counterparty = (claim.counterparty_issuer_id or "").strip()
        if not counterparty:
            return f"relationship_claim_without_counterparty:{claim.claim_id}"
        edges = [e for e in rel.edges if e.counterparty_issuer_id == counterparty]
        if not edges:
            return f"unverified_relationship:{claim.claim_id}:{counterparty}"
        asserted = asserted_relationship(claim)
        for edge in edges:
            if edge.status not in ACCEPTABLE_RELATIONSHIP_STATUSES:
                return f"relationship_{edge.status}:{claim.claim_id}:{counterparty}"
            if asserted is None:
                continue
            asserted_type, asserted_direction = asserted
            if edge.relationship_type != asserted_type:
                return f"relationship_type_contradiction:{claim.claim_id}:asserted={asserted_type}:registry={edge.relationship_type}"
            if asserted_direction != "bidirectional" and edge.direction not in (asserted_direction, "bidirectional"):
                return f"relationship_direction_contradiction:{claim.claim_id}:asserted={asserted_direction}:registry={edge.direction}"
    return None
