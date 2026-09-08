"""Composite candidate relevance score — Company Discovery Phase 2.
Computed and persisted every tick for explainability/audit purposes;
Phase 2 never acts on it (no promotion path exists). Pure, no I/O.

Every weight below is a named constant, approved exactly as given
(binding decision 1): relationship specificity 0.30, thematic fit 0.10,
Core proximity 0.15, source authority 0.15, independent recurrence
0.15, recency 0.05, identifier confidence 0.10 — sums to 1.0, checked
by `tests/test_company_discovery_scoring.py`.

QUARANTINED/REJECTED candidates are never scored — `compute_composite_
score` is only ever called for a `Discovered`/`Archived` candidate; the
caller (`candidate_pipeline.py`) enforces this, not this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.models.company_discovery_models import RelationshipType, ResolutionConfidence, SourceType

WEIGHT_RELATIONSHIP_SPECIFICITY = 0.30
WEIGHT_THEMATIC_FIT = 0.10
WEIGHT_CORE_PROXIMITY = 0.15
WEIGHT_SOURCE_AUTHORITY = 0.15
WEIGHT_INDEPENDENT_RECURRENCE = 0.15
WEIGHT_RECENCY = 0.05
WEIGHT_IDENTIFIER_CONFIDENCE = 0.10

_ALL_WEIGHTS = (
    WEIGHT_RELATIONSHIP_SPECIFICITY, WEIGHT_THEMATIC_FIT, WEIGHT_CORE_PROXIMITY,
    WEIGHT_SOURCE_AUTHORITY, WEIGHT_INDEPENDENT_RECURRENCE, WEIGHT_RECENCY, WEIGHT_IDENTIFIER_CONFIDENCE,
)

_RECENCY_FULL_CREDIT_DAYS = 30
_RECENCY_ZERO_CREDIT_DAYS = 180

_HIGH_SPECIFICITY_RELATIONSHIPS = frozenset({
    RelationshipType.SUPPLIER, RelationshipType.CUSTOMER, RelationshipType.PARTNER, RelationshipType.COMPETITOR,
})


@dataclass(frozen=True)
class ScoreInputs:
    """One row's worth of scoring inputs, aggregated across all of a
    candidate's evidence by the caller — this module never reads
    evidence rows itself."""

    relationship_types: tuple[RelationshipType, ...]
    theme_or_layer_present: bool
    has_core_relationship: bool
    source_types: tuple[SourceType, ...]
    distinct_source_count: int
    most_recent_evidence_at: str | None  # ISO 8601
    resolution_confidence: ResolutionConfidence
    now: datetime | None = None


def _relationship_specificity(relationship_types: tuple[RelationshipType, ...]) -> float:
    if not relationship_types:
        return 0.0
    return 1.0 if any(r in _HIGH_SPECIFICITY_RELATIONSHIPS for r in relationship_types) else 0.3


def _thematic_fit(theme_or_layer_present: bool) -> float:
    return 1.0 if theme_or_layer_present else 0.0


def _core_proximity(has_core_relationship: bool) -> float:
    return 1.0 if has_core_relationship else 0.0


def _source_authority(source_types: tuple[SourceType, ...]) -> float:
    if not source_types:
        return 0.0
    return 1.0 if SourceType.FILING in source_types else 0.6


def _independent_recurrence(distinct_source_count: int) -> float:
    return min(distinct_source_count / 2.0, 1.0)


def _recency(most_recent_evidence_at: str | None, now: datetime) -> float:
    if not most_recent_evidence_at:
        return 0.0
    try:
        evidence_dt = datetime.fromisoformat(most_recent_evidence_at)
    except (ValueError, TypeError):
        return 0.0
    if evidence_dt.tzinfo is None:
        evidence_dt = evidence_dt.replace(tzinfo=timezone.utc)
    age_days = (now - evidence_dt).total_seconds() / 86400
    if age_days <= _RECENCY_FULL_CREDIT_DAYS:
        return 1.0
    if age_days >= _RECENCY_ZERO_CREDIT_DAYS:
        return 0.0
    span = _RECENCY_ZERO_CREDIT_DAYS - _RECENCY_FULL_CREDIT_DAYS
    return 1.0 - (age_days - _RECENCY_FULL_CREDIT_DAYS) / span


def _identifier_confidence(resolution_confidence: ResolutionConfidence) -> float:
    return {
        ResolutionConfidence.HIGH: 1.0,
        ResolutionConfidence.MEDIUM: 0.5,
        ResolutionConfidence.LOW: 0.0,
    }[resolution_confidence]


def compute_composite_score(inputs: ScoreInputs) -> tuple[float, dict[str, float]]:
    """Returns (composite_score, breakdown) — breakdown is the exact
    per-factor sub-score dict persisted to `candidate_score_history.
    score_breakdown`, so every score is always explainable back to its
    inputs, never an opaque number."""
    now = inputs.now or datetime.now(timezone.utc)
    breakdown = {
        "relationship_specificity": _relationship_specificity(inputs.relationship_types),
        "thematic_fit": _thematic_fit(inputs.theme_or_layer_present),
        "core_proximity": _core_proximity(inputs.has_core_relationship),
        "source_authority": _source_authority(inputs.source_types),
        "independent_recurrence": _independent_recurrence(inputs.distinct_source_count),
        "recency": _recency(inputs.most_recent_evidence_at, now),
        "identifier_confidence": _identifier_confidence(inputs.resolution_confidence),
    }
    composite = (
        WEIGHT_RELATIONSHIP_SPECIFICITY * breakdown["relationship_specificity"]
        + WEIGHT_THEMATIC_FIT * breakdown["thematic_fit"]
        + WEIGHT_CORE_PROXIMITY * breakdown["core_proximity"]
        + WEIGHT_SOURCE_AUTHORITY * breakdown["source_authority"]
        + WEIGHT_INDEPENDENT_RECURRENCE * breakdown["independent_recurrence"]
        + WEIGHT_RECENCY * breakdown["recency"]
        + WEIGHT_IDENTIFIER_CONFIDENCE * breakdown["identifier_confidence"]
    )
    return composite, breakdown
