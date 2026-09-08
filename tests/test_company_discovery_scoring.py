"""Composite candidate relevance scoring — pure, zero I/O."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.data_access.company_discovery.scoring import (
    WEIGHT_CORE_PROXIMITY,
    WEIGHT_IDENTIFIER_CONFIDENCE,
    WEIGHT_INDEPENDENT_RECURRENCE,
    WEIGHT_RECENCY,
    WEIGHT_RELATIONSHIP_SPECIFICITY,
    WEIGHT_SOURCE_AUTHORITY,
    WEIGHT_THEMATIC_FIT,
    ScoreInputs,
    compute_composite_score,
)
from src.models.company_discovery_models import RelationshipType, ResolutionConfidence, SourceType


def test_approved_weights_sum_to_one():
    total = (
        WEIGHT_RELATIONSHIP_SPECIFICITY + WEIGHT_THEMATIC_FIT + WEIGHT_CORE_PROXIMITY
        + WEIGHT_SOURCE_AUTHORITY + WEIGHT_INDEPENDENT_RECURRENCE + WEIGHT_RECENCY + WEIGHT_IDENTIFIER_CONFIDENCE
    )
    assert round(total, 6) == 1.0


def test_approved_weight_values_exact():
    assert WEIGHT_RELATIONSHIP_SPECIFICITY == 0.30
    assert WEIGHT_THEMATIC_FIT == 0.10
    assert WEIGHT_CORE_PROXIMITY == 0.15
    assert WEIGHT_SOURCE_AUTHORITY == 0.15
    assert WEIGHT_INDEPENDENT_RECURRENCE == 0.15
    assert WEIGHT_RECENCY == 0.05
    assert WEIGHT_IDENTIFIER_CONFIDENCE == 0.10


def _inputs(**overrides) -> ScoreInputs:
    now = datetime.now(timezone.utc)
    defaults = dict(
        relationship_types=(RelationshipType.SUPPLIER,), theme_or_layer_present=True, has_core_relationship=True,
        source_types=(SourceType.FILING,), distinct_source_count=2, most_recent_evidence_at=now.isoformat(),
        resolution_confidence=ResolutionConfidence.HIGH, now=now,
    )
    defaults.update(overrides)
    return ScoreInputs(**defaults)


def test_maximal_inputs_score_close_to_one():
    composite, breakdown = compute_composite_score(_inputs())
    assert composite > 0.95
    assert all(v == 1.0 for v in breakdown.values())


def test_minimal_inputs_score_near_zero():
    composite, breakdown = compute_composite_score(_inputs(
        relationship_types=(), theme_or_layer_present=False, has_core_relationship=False,
        source_types=(), distinct_source_count=0, most_recent_evidence_at=None,
        resolution_confidence=ResolutionConfidence.LOW,
    ))
    assert composite == 0.0


def test_thematic_mention_scores_lower_relationship_specificity_than_supplier():
    supplier_score, _ = compute_composite_score(_inputs(relationship_types=(RelationshipType.SUPPLIER,)))
    thematic_score, _ = compute_composite_score(_inputs(relationship_types=(RelationshipType.THEMATIC_MENTION,)))
    assert thematic_score < supplier_score


def test_daily_news_only_source_scores_lower_authority_than_filing():
    filing_score, _ = compute_composite_score(_inputs(source_types=(SourceType.FILING,)))
    news_score, _ = compute_composite_score(_inputs(source_types=(SourceType.DAILY_NEWS,)))
    assert news_score < filing_score


def test_independent_recurrence_caps_at_two_sources():
    _, one_source = compute_composite_score(_inputs(distinct_source_count=1))
    _, two_sources = compute_composite_score(_inputs(distinct_source_count=2))
    _, five_sources = compute_composite_score(_inputs(distinct_source_count=5))
    assert one_source["independent_recurrence"] == 0.5
    assert two_sources["independent_recurrence"] == 1.0
    assert five_sources["independent_recurrence"] == 1.0


def test_recency_full_credit_within_thirty_days():
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(days=10)).isoformat()
    _, breakdown = compute_composite_score(_inputs(most_recent_evidence_at=recent, now=now))
    assert breakdown["recency"] == 1.0


def test_recency_zero_credit_at_or_beyond_180_days():
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=200)).isoformat()
    _, breakdown = compute_composite_score(_inputs(most_recent_evidence_at=old, now=now))
    assert breakdown["recency"] == 0.0


def test_recency_decays_linearly_between_bounds():
    now = datetime.now(timezone.utc)
    midpoint = (now - timedelta(days=105)).isoformat()  # halfway between 30 and 180
    _, breakdown = compute_composite_score(_inputs(most_recent_evidence_at=midpoint, now=now))
    assert 0.4 < breakdown["recency"] < 0.6


def test_identifier_confidence_bands():
    _, high = compute_composite_score(_inputs(resolution_confidence=ResolutionConfidence.HIGH))
    _, medium = compute_composite_score(_inputs(resolution_confidence=ResolutionConfidence.MEDIUM))
    _, low = compute_composite_score(_inputs(resolution_confidence=ResolutionConfidence.LOW))
    assert high["identifier_confidence"] == 1.0
    assert medium["identifier_confidence"] == 0.5
    assert low["identifier_confidence"] == 0.0


def test_score_breakdown_has_exactly_the_seven_named_factors():
    _, breakdown = compute_composite_score(_inputs())
    assert set(breakdown.keys()) == {
        "relationship_specificity", "thematic_fit", "core_proximity", "source_authority",
        "independent_recurrence", "recency", "identifier_confidence",
    }
