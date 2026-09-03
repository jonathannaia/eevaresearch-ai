"""Alias/entity resolution, canonical ID generation, and quarantine/
reject classification — pure, zero I/O."""
from __future__ import annotations

from src.data_access.company_discovery.entity_resolution import (
    ResolutionOutcome,
    canonical_daily_news_record_id,
    canonical_filing_record_id,
    classify_entity_kind,
    generate_evidence_dedup_key,
    generate_issuer_id,
    is_well_formed_mention,
    normalize_entity_name,
    resolve_mention,
)
from src.models.company_discovery_models import EntityKind


def test_normalize_strips_legal_suffix_and_case_folds():
    assert normalize_entity_name("Example Materials Corp.") == "example materials"
    assert normalize_entity_name("Example Systems Ltd.") == "example systems"
    assert normalize_entity_name("  NVIDIA   Corporation  ") == "nvidia"


def test_classify_entity_kind_detects_government_and_fund():
    assert classify_entity_kind("Ministry of Trade") == EntityKind.GOVERNMENT
    assert classify_entity_kind("Government of Example Country") == EntityKind.GOVERNMENT
    assert classify_entity_kind("Example Growth Fund") == EntityKind.FUND
    assert classify_entity_kind("Department of Commerce") == EntityKind.AGENCY
    assert classify_entity_kind("Example Materials Corp.") == EntityKind.CORPORATE


def test_is_well_formed_mention_rejects_short_fragments():
    assert not is_well_formed_mention("ab")
    assert is_well_formed_mention("example")


# --- Deterministic issuer_id scheme (approved exact form) -----------------


def test_issuer_id_exact_approved_form():
    issuer_id = generate_issuer_id("Example Materials Corp.", "Unconfirmed")
    assert issuer_id.startswith("candidate:")
    assert len(issuer_id) == len("candidate:") + 16


def test_issuer_id_is_deterministic_and_stable():
    a = generate_issuer_id("Example Materials Corp.", "Unconfirmed")
    b = generate_issuer_id("Example Materials Corp.", "Unconfirmed")
    assert a == b


def test_issuer_id_differs_by_jurisdiction():
    a = generate_issuer_id("Example Materials Corp.", "Unconfirmed")
    b = generate_issuer_id("Example Materials Corp.", "South Korea (listing exchange)")
    assert a != b


# --- Source-namespaced source_record_id — no cross-type collision ---------


def test_filing_record_id_is_source_namespaced():
    assert canonical_filing_record_id("SEC EDGAR", "0001045810-26-000078") == "edgar:0001045810-26-000078"
    assert canonical_filing_record_id("OpenDART / DART", "20260812000001") == "dart:20260812000001"
    assert canonical_filing_record_id("EDINET", "S100YGH5") == "edinet:S100YGH5"


def test_daily_news_record_id_is_source_namespaced():
    assert canonical_daily_news_record_id("newsitem-nvidia-abc123") == "daily_news:newsitem-nvidia-abc123"


def test_two_different_source_types_with_the_same_raw_id_never_collide():
    """A DART rcept_no and a NewsStory.id that happen to share the exact
    same raw string must resolve to distinct source_record_ids."""
    raw_id = "X"
    filing_record_id = canonical_filing_record_id("OpenDART / DART", raw_id)
    news_record_id = canonical_daily_news_record_id(raw_id)
    assert filing_record_id != news_record_id
    assert filing_record_id == "dart:X"
    assert news_record_id == "daily_news:X"


def test_evidence_dedup_key_is_deterministic_and_distinguishes_by_every_component():
    base = generate_evidence_dedup_key("candidate:abc", "edgar:123", "supplier", "supplied_by")
    assert base == generate_evidence_dedup_key("candidate:abc", "edgar:123", "supplier", "supplied_by")
    assert base != generate_evidence_dedup_key("candidate:abc", "edgar:124", "supplier", "supplied_by")
    assert base != generate_evidence_dedup_key("candidate:abc", "edgar:123", "customer", "supplied_by")


# --- Resolution ordering ---------------------------------------------------


def test_resolve_matches_core_exactly():
    result = resolve_mention(
        "NVIDIA", core_names=frozenset({"nvidia"}), stub_names=frozenset(), known_aliases={},
    )
    assert result.outcome == ResolutionOutcome.MATCHED_CORE


def test_resolve_matches_stub_exactly_and_never_creates_a_candidate():
    result = resolve_mention(
        "Quanta Services, Inc.", core_names=frozenset(), stub_names=frozenset({"quanta services"}), known_aliases={},
    )
    assert result.outcome == ResolutionOutcome.MATCHED_STUB


def test_resolve_matches_known_alias():
    result = resolve_mention(
        "Example Materials Corp.", core_names=frozenset(), stub_names=frozenset(),
        known_aliases={"example materials": "candidate:abc123"},
    )
    assert result.outcome == ResolutionOutcome.MATCHED_EXISTING_CANDIDATE
    assert result.matched_issuer_id == "candidate:abc123"


def test_resolve_new_corporate_mention_is_a_new_candidate():
    result = resolve_mention(
        "Example Materials Corp.", core_names=frozenset({"nvidia"}), stub_names=frozenset(), known_aliases={},
    )
    assert result.outcome == ResolutionOutcome.NEW_CANDIDATE
    assert result.entity_kind == EntityKind.CORPORATE


# --- Quarantine cases -------------------------------------------------------


def test_quarantine_when_mention_is_a_substring_of_a_known_core_name():
    result = resolve_mention(
        "NVIDIA Korea Ltd.", core_names=frozenset({"nvidia"}), stub_names=frozenset(), known_aliases={},
    )
    assert result.outcome == ResolutionOutcome.NEW_QUARANTINED
    assert "nvidia" in (result.reason or "")


def test_quarantine_when_mention_contains_a_known_candidate_alias():
    result = resolve_mention(
        "Example Materials Asia Corp.", core_names=frozenset(), stub_names=frozenset(),
        known_aliases={"example materials": "candidate:abc123"},
    )
    assert result.outcome == ResolutionOutcome.NEW_QUARANTINED


def test_exact_match_is_never_quarantined_even_though_it_also_contains_itself():
    result = resolve_mention(
        "NVIDIA", core_names=frozenset({"nvidia"}), stub_names=frozenset(), known_aliases={},
    )
    assert result.outcome == ResolutionOutcome.MATCHED_CORE


# --- Reject cases ------------------------------------------------------------


def test_reject_non_corporate_government_mention():
    result = resolve_mention(
        "Ministry of Trade and Industry", core_names=frozenset(), stub_names=frozenset(), known_aliases={},
    )
    assert result.outcome == ResolutionOutcome.NEW_REJECTED
    assert result.entity_kind == EntityKind.GOVERNMENT


def test_reject_non_corporate_fund_mention():
    result = resolve_mention(
        "Example Growth Fund", core_names=frozenset(), stub_names=frozenset(), known_aliases={},
    )
    assert result.outcome == ResolutionOutcome.NEW_REJECTED
    assert result.entity_kind == EntityKind.FUND


def test_reject_too_short_or_generic_mention():
    result = resolve_mention(
        "ABC Inc.", core_names=frozenset(), stub_names=frozenset(), known_aliases={},
    )
    # "abc" (3 chars after suffix-strip) is below the well-formedness
    # minimum — rejected as too generic, never scored.
    assert result.outcome == ResolutionOutcome.NEW_REJECTED
    assert result.reason == "too_short_or_generic"


def test_reject_takes_priority_over_quarantine_for_non_corporate_names():
    # A government agency name that also happens to overlap a known Core
    # name must still be rejected outright, never quarantined — a
    # human-review queue for a plainly non-corporate entity would be
    # wasted attention.
    result = resolve_mention(
        "NVIDIA Ministry Liaison Office", core_names=frozenset({"nvidia"}), stub_names=frozenset(), known_aliases={},
    )
    assert result.outcome == ResolutionOutcome.NEW_REJECTED
    assert result.entity_kind == EntityKind.GOVERNMENT
