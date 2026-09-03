"""Company Discovery — Phase 2 (passive Candidate Ledger). Own module,
mirroring `daily_news_models.py`'s existing "own product surface, own
types" convention — zero imports from `src.models.models`
(CandidateSignal/FilingEvent are read, never subclassed or reused as a
base type here).

Every dataclass here is purely additive infrastructure: nothing in this
module is read by any existing pipeline, page, or component. `Issuer`
(`src.models.issuer`) is reused, not duplicated, for identity — see
`CandidateIssuerRecord.issuer` below — this module only adds the
score/evidence/lifecycle fields `Issuer` itself deliberately does not
carry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from src.models.issuer import Issuer


class SourceType(str, Enum):
    FILING = "Filing"
    DAILY_NEWS = "DailyNews"


class RelationshipType(str, Enum):
    SUPPLIER = "supplier"
    CUSTOMER = "customer"
    PARTNER = "partner"
    COMPETITOR = "competitor"
    THEMATIC_MENTION = "thematic_mention"


class EntityKind(str, Enum):
    CORPORATE = "corporate"
    SUBSIDIARY = "subsidiary"
    FUND = "fund"
    AGENCY = "agency"
    GOVERNMENT = "government"
    UNKNOWN = "unknown"


class ResolutionConfidence(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


@dataclass(frozen=True)
class CandidateEvidence:
    """One append-only, source-backed mention. Every field the Phase 2
    approval required is a real column, never inferred: `source_url`,
    `source_snippet`, `extraction_timestamp`, `relationship_type`,
    resolution confidence live on the parent `candidate_issuers` row.
    `dedup_key` and `source_record_id` are both deterministic — see
    `src.data_access.company_discovery.entity_resolution` for their
    exact construction."""

    issuer_id: str
    source_type: SourceType
    source_name: str
    source_record_id: str  # canonical, source-namespaced — e.g. "edgar:0001045810-26-000078", "daily_news:newsitem-..."
    source_url: str
    source_snippet: str
    relationship_type: RelationshipType
    matched_pattern_category: str
    extraction_timestamp: str  # ISO 8601 — when this worker run found it, never a claim about when it was published
    dedup_key: str
    related_core_issuer_id: str | None = None
    theme_slug: str | None = None
    supply_chain_layer: str | None = None
    source_published_at: str | None = None  # the underlying filing/story's own official date, carried through for recency scoring only


@dataclass(frozen=True)
class CandidateIssuerRecord:
    """A persisted Candidate row: `Issuer` identity (reused, extended —
    see `src.models.issuer`) plus the lifecycle/score fields `Issuer`
    itself does not carry. `composite_score` is always a cache of the
    latest `CandidateScoreSnapshot`, recomputed from `CandidateEvidence`
    every worker tick — never hand-edited, never authoritative on its
    own."""

    issuer: Issuer
    resolution_confidence: ResolutionConfidence
    composite_score: float
    first_evidence_at: str
    last_evidence_at: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class CandidateScoreSnapshot:
    issuer_id: str
    computed_at: str
    composite_score: float
    evidence_count: int
    independent_source_count: int
    score_breakdown: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateWorkerStatus:
    worker_key: str
    last_tick_started_at: str | None
    last_tick_completed_at: str | None
    last_failure_code: str | None
    evidence_created_last_run: int
    candidates_created_last_run: int
    candidates_quarantined_last_run: int
    updated_at: str


@dataclass(frozen=True)
class CandidateStateTransition:
    issuer_id: str
    to_state: str
    at: str
    triggered_by: str
    from_state: str | None = None
    detail: str = ""
