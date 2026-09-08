"""Issuer domain model — pure dataclass/enum behavior, zero registry data."""
from src.models.issuer import CoverageState, Issuer, LifecycleState


def test_issuer_requires_only_the_documented_core_fields():
    issuer = Issuer(
        issuer_id="test:ABC",
        legal_name="Test Co",
        country_or_jurisdiction="Unconfirmed",
        coverage_state=CoverageState.DISCOVERED,
    )
    assert issuer.lifecycle_state == LifecycleState.ACTIVE  # default
    assert issuer.identifiers == {}
    assert issuer.themes == ()
    assert issuer.aliases == ()
    assert issuer.evidence_confidence == "Not assessed"


def test_issuer_is_frozen():
    issuer = Issuer(
        issuer_id="test:ABC", legal_name="Test Co",
        country_or_jurisdiction="Unconfirmed", coverage_state=CoverageState.SEED,
    )
    try:
        issuer.legal_name = "Changed"
        assert False, "expected dataclasses.FrozenInstanceError"
    except Exception as exc:
        assert type(exc).__name__ == "FrozenInstanceError"


def test_issuer_identifiers_default_is_not_shared_across_instances():
    a = Issuer(issuer_id="a", legal_name="A", country_or_jurisdiction="X", coverage_state=CoverageState.SEED)
    b = Issuer(issuer_id="b", legal_name="B", country_or_jurisdiction="X", coverage_state=CoverageState.SEED)
    a.identifiers["SEC EDGAR"] = "0000000001"
    assert b.identifiers == {}


def test_coverage_state_values():
    # Company Discovery Phase 2 additively extended this enum with
    # ARCHIVED/QUARANTINED — SEED/DISCOVERED/REJECTED are unchanged.
    assert {s.value for s in CoverageState} == {"Seed", "Discovered", "Rejected", "Archived", "Quarantined"}


def test_lifecycle_state_values():
    assert {s.value for s in LifecycleState} == {"Active", "Monitoring", "Delisted", "Merged"}


# --- Company Discovery Phase 2 additions to Issuer — additive only ---


def test_issuer_parent_and_entity_kind_default_safely():
    issuer = Issuer(issuer_id="a", legal_name="A", country_or_jurisdiction="X", coverage_state=CoverageState.SEED)
    assert issuer.parent_issuer_id is None
    assert issuer.entity_kind == "corporate"


def test_issuer_parent_and_entity_kind_can_be_set():
    issuer = Issuer(
        issuer_id="candidate:abc123", legal_name="Example Subsidiary Ltd.", country_or_jurisdiction="Unconfirmed",
        coverage_state=CoverageState.DISCOVERED, parent_issuer_id="edgar:NVDA", entity_kind="subsidiary",
    )
    assert issuer.parent_issuer_id == "edgar:NVDA"
    assert issuer.entity_kind == "subsidiary"
