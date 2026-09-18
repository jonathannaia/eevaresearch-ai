"""Typed input/output contracts for the ten MCP tools (design §6), the
agent's structured claim proposal (design §9.1), and the canonical
resolvable-evidence record every public claim must point at (design §5.2
row 7). Pure dataclasses/enums — no I/O, no imports from any adapter.

Closed vocabularies are real enums, not strings the model is asked to
respect: claim_type has exactly one member, and every tool returns a
typed ToolError instead of raising into the model (design §11).

Two implementation-time clarifications of the design's §9.1 schema, both
additive and both documented for the completion report:
- Claim.counterparty_issuer_id (optional): a relationship-shaped claim
  must self-declare the other tracked issuer so publication_policy's
  contradiction check (row 8) can look the pair up deterministically
  instead of mining the statement text.
- Claim.claim_category (closed enum): the self-declared publication
  category row 10 checks. publication_policy runs its own rule-based
  classifier over the claim and can only DEMOTE a declared category,
  never promote one — the declaration is a hint, the classifier decides.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum

from src.models.models import EvidenceLocation

HEADLINE_MAX_CHARS = 140
STATEMENT_MAX_CHARS = 400
WHAT_THIS_DOES_NOT_ESTABLISH_MAX_CHARS = 300
MAX_CLAIMS_PER_PROPOSAL = 5


# --- closed vocabularies --------------------------------------------------

class SourceTier(str, Enum):
    SEC_EDGAR = "sec_edgar"
    DART = "dart"
    EDINET = "edinet"
    ISSUER_IR = "issuer_ir"
    GOVERNMENT_REGULATOR = "government_regulator"
    UNKNOWN = "unknown"


# The only tiers that may support an auto-published fact (design §5.2 row 1).
AUTO_PUBLISHABLE_SOURCE_TIERS: frozenset[SourceTier] = frozenset({
    SourceTier.SEC_EDGAR, SourceTier.DART, SourceTier.EDINET,
    SourceTier.ISSUER_IR, SourceTier.GOVERNMENT_REGULATOR,
})

# FilingEvent.source_name values already used by the three adapters.
SOURCE_NAME_TO_TIER: dict[str, SourceTier] = {
    "SEC EDGAR": SourceTier.SEC_EDGAR,
    "OpenDART / DART": SourceTier.DART,
    "EDINET": SourceTier.EDINET,
}
FILING_SOURCE_NAMES: frozenset[str] = frozenset(SOURCE_NAME_TO_TIER)


class FreshnessStatus(str, Enum):
    CURRENT = "current"
    STALE = "stale"
    SUPERSEDED = "superseded"
    DISPUTED = "disputed"
    CONTRADICTORY = "contradictory"
    UNRESOLVED = "unresolved"


class Suppression(str, Enum):
    NONE = "none"
    NOT_MATERIAL = "not_material"
    ROUTINE_EXCLUDE = "routine_exclude"


class ResolutionConfidence(str, Enum):
    EXACT = "exact"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


class ClaimType(str, Enum):
    """Exactly one member, on purpose (design §9.1): rumor, recommendation,
    causal, and relationship claim types are never representable."""
    DIRECT_REPORTED_FACT = "direct_reported_fact"


class ClaimCategory(str, Enum):
    DIRECT_FACT = "direct_fact"
    RELATIONSHIP_ASSERTION = "relationship_assertion"
    TAXONOMY_CHANGE = "taxonomy_change"
    SECOND_ORDER_INFERENCE = "second_order_inference"
    RECOMMENDATION_OR_PRICE_TARGET = "recommendation_or_price_target"
    RUMOR_OR_ANONYMOUS = "rumor_or_anonymous"


# design §5.2 row 10 / §9.3 — the auto-publish content allowlist.
AUTO_PUBLISHABLE_CLAIM_CATEGORIES: frozenset[ClaimCategory] = frozenset({ClaimCategory.DIRECT_FACT})


class RelationshipContextStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class ToolErrorKind(str, Enum):
    INVALID_INPUT = "invalid_input"
    UNAUTHORIZED = "unauthorized"
    BUDGET_EXCEEDED = "budget_exceeded"
    NOT_FOUND = "not_found"
    RETRIEVAL_FAILED = "retrieval_failed"
    PARSE_FAILED = "parse_failed"
    SUPPRESSED = "suppressed"
    REGISTRY_UNAVAILABLE = "registry_unavailable"
    VALIDATION_FAILED = "validation_failed"


@dataclass(frozen=True)
class ToolError:
    kind: ToolErrorKind
    detail: str


# --- per-session authorization scope (design §6 common contract) -----------

@dataclass(frozen=True)
class SessionScope:
    """One agent session = one filing, one issuer, one case. Every tool
    checks its inputs against this scope and refuses anything outside it
    — the credential never widens mid-session."""
    session_id: str
    candidate_id: str
    issuer_id: str
    source_name: str
    seed_document_id: str
    started_at: str

    def violations(self) -> tuple[str, ...]:
        return tuple(
            f"blank_{name}" for name in ("session_id", "candidate_id", "issuer_id", "source_name", "seed_document_id", "started_at")
            if not str(getattr(self, name) or "").strip()
        )


# --- canonical resolvable evidence (design §5.2 row 7) --------------------

def mint_evidence_id(session_id: str, document_id: str, locator: str, excerpt: str) -> str:
    """Deterministic: the same excerpt at the same locator in the same
    document in the same session always mints the same ID, so re-running
    a session reproduces its evidence IDs byte-for-byte (design §13)."""
    digest = hashlib.sha256(f"{session_id}|{document_id}|{locator}|{hashlib.sha256(excerpt.encode('utf-8')).hexdigest()}".encode("utf-8")).hexdigest()
    return f"ev-{digest[:16]}"


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    session_id: str
    issuer_id: str
    source_tier: SourceTier
    source_name: str
    source_url: str
    source_document_id: str
    source_date: str
    excerpt_or_locator: str
    excerpt_sha256: str
    retrieved_at: str
    confidence: str
    freshness_status: FreshnessStatus
    evidence_location: EvidenceLocation | None = None

    def resolution_violations(self) -> tuple[str, ...]:
        """Row 7: every field a public claim needs to be independently
        re-checkable. Any violation means the record cannot support an
        auto-published fact."""
        violations: list[str] = []
        for name in ("evidence_id", "session_id", "issuer_id", "source_url", "source_document_id", "source_date", "excerpt_or_locator", "excerpt_sha256", "retrieved_at", "confidence"):
            if not str(getattr(self, name) or "").strip():
                violations.append(f"blank_{name}")
        if self.source_tier not in AUTO_PUBLISHABLE_SOURCE_TIERS:
            violations.append("source_tier_not_auto_publishable")
        if self.freshness_status is not FreshnessStatus.CURRENT:
            violations.append(f"freshness_{self.freshness_status.value}")
        return tuple(violations)


# --- tool result shapes (design §6, one per tool) ---------------------------

@dataclass(frozen=True)
class IssuerResolution:
    resolution_confidence: ResolutionConfidence
    issuer_id: str | None = None
    tracked_company_name: str | None = None
    exchange: str | None = None
    themes: tuple[str, ...] = ()
    candidates: tuple[str, ...] = ()
    error: ToolError | None = None


@dataclass(frozen=True)
class FilingMetadataRow:
    document_id: str
    source_name: str
    title: str
    filed_at: str
    matched_rules: tuple[str, ...]
    confidence: str | None
    suppression: Suppression
    suppression_detail: str = ""


@dataclass(frozen=True)
class FilingMetadataResult:
    rows: tuple[FilingMetadataRow, ...] = ()
    suppressed_count: int = 0
    error: ToolError | None = None


@dataclass(frozen=True)
class EvidenceExcerptResult:
    document_id: str
    evidence_id: str | None = None
    excerpt: str = ""
    excerpt_quality: str = ""
    retrieved_at: str = ""
    evidence_location: EvidenceLocation | None = None
    evidence_source_member: str | None = None
    source_url: str = ""
    source_date: str = ""
    source_tier: SourceTier = SourceTier.UNKNOWN
    error: ToolError | None = None


@dataclass(frozen=True)
class TableLocatorResult:
    document_id: str
    found: bool = False
    locator: EvidenceLocation | None = None
    excerpt: str = ""
    evidence_id: str | None = None
    error: ToolError | None = None


@dataclass(frozen=True)
class PriorFilingComparison:
    has_newer_filing: bool = False
    has_superseding_disclosure: bool = False
    prior_document_ids: tuple[str, ...] = ()
    added_categories: tuple[str, ...] = ()
    removed_categories: tuple[str, ...] = ()
    comparison_status: str = ""
    error: ToolError | None = None


@dataclass(frozen=True)
class ValidatedEvidenceRow:
    evidence_item_id: str
    case_id: str
    source_type: str
    source_id: str
    source_url: str
    source_date: str
    excerpt_original: str
    retrieved_at: str


@dataclass(frozen=True)
class ValidatedEvidenceResult:
    rows: tuple[ValidatedEvidenceRow, ...] = ()
    error: ToolError | None = None


@dataclass(frozen=True)
class RelationshipEdgeContext:
    relationship_type: str
    status: str
    direction: str
    confidence: str
    counterparty_issuer_id: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RelationshipContextResult:
    status: RelationshipContextStatus
    edges: tuple[RelationshipEdgeContext, ...] = ()
    detail: str = ""
    error: ToolError | None = None


@dataclass(frozen=True)
class ApprovedSourceRow:
    url: str
    title: str
    published_at: str
    domain: str
    source_tier: SourceTier


@dataclass(frozen=True)
class ApprovedSourceResult:
    rows: tuple[ApprovedSourceRow, ...] = ()
    error: ToolError | None = None


# --- structured claim proposal (design §9.1) -------------------------------

@dataclass(frozen=True)
class Claim:
    claim_id: str
    claim_type: ClaimType
    claim_category: ClaimCategory
    headline: str
    issuer_id: str
    statement: str
    evidence_ids: tuple[str, ...]
    what_this_does_not_establish: str
    factual_context_evidence_ids: tuple[str, ...] = ()
    counterparty_issuer_id: str | None = None


@dataclass(frozen=True)
class ClaimProposal:
    session_id: str
    candidate_id: str
    claims: tuple[Claim, ...]
    retrieved_evidence_ids: tuple[str, ...]


def validate_claim_proposal(proposal: ClaimProposal) -> tuple[str, ...]:
    """Row 4 (schema validity). Pure and exhaustive — reports every
    violation, never raises for content problems. A non-empty result means
    INSUFFICIENT_EVIDENCE per design §5.2 (malformed output is treated as
    'could not produce a valid claim', never retried with a looser schema)."""
    violations: list[str] = []
    if not proposal.session_id.strip():
        violations.append("blank_session_id")
    if not proposal.candidate_id.strip():
        violations.append("blank_candidate_id")
    if not proposal.claims:
        violations.append("no_claims")
    if len(proposal.claims) > MAX_CLAIMS_PER_PROPOSAL:
        violations.append("too_many_claims")
    seen_ids: set[str] = set()
    retrieved = set(proposal.retrieved_evidence_ids)
    for claim in proposal.claims:
        prefix = f"claim[{claim.claim_id or '?'}]"
        if not claim.claim_id.strip():
            violations.append(f"{prefix}.blank_claim_id")
        elif claim.claim_id in seen_ids:
            violations.append(f"{prefix}.duplicate_claim_id")
        seen_ids.add(claim.claim_id)
        if not isinstance(claim.claim_type, ClaimType):
            violations.append(f"{prefix}.invalid_claim_type")
        if not isinstance(claim.claim_category, ClaimCategory):
            violations.append(f"{prefix}.invalid_claim_category")
        if not claim.headline.strip():
            violations.append(f"{prefix}.blank_headline")
        elif len(claim.headline) > HEADLINE_MAX_CHARS:
            violations.append(f"{prefix}.headline_too_long")
        if not claim.issuer_id.strip():
            violations.append(f"{prefix}.blank_issuer_id")
        if not claim.statement.strip():
            violations.append(f"{prefix}.blank_statement")
        elif len(claim.statement) > STATEMENT_MAX_CHARS:
            violations.append(f"{prefix}.statement_too_long")
        if not claim.evidence_ids or any(not e.strip() for e in claim.evidence_ids):
            violations.append(f"{prefix}.missing_evidence_ids")
        elif any(e not in retrieved for e in claim.evidence_ids):
            violations.append(f"{prefix}.evidence_id_not_retrieved_this_session")
        if any(e not in retrieved for e in claim.factual_context_evidence_ids):
            violations.append(f"{prefix}.factual_context_evidence_id_not_retrieved_this_session")
        if not claim.what_this_does_not_establish.strip():
            violations.append(f"{prefix}.blank_what_this_does_not_establish")
        elif len(claim.what_this_does_not_establish) > WHAT_THIS_DOES_NOT_ESTABLISH_MAX_CHARS:
            violations.append(f"{prefix}.what_this_does_not_establish_too_long")
        if claim.counterparty_issuer_id is not None and not claim.counterparty_issuer_id.strip():
            violations.append(f"{prefix}.blank_counterparty_issuer_id")
    return tuple(violations)


# --- policy-mediated write-request results ---------------------------------

@dataclass(frozen=True)
class PacketSaveResult:
    status: str  # "saved" | "rejected"
    packet_id: str | None = None
    violations: tuple[str, ...] = ()
    error: ToolError | None = None


@dataclass(frozen=True)
class PublicationDecisionResult:
    decision: str
    reasons: tuple[str, ...] = ()
    row_results: tuple[tuple[int, str, bool, str], ...] = field(default_factory=tuple)
    verified_update_id: str | None = None
    error: ToolError | None = None
