"""Deterministic org-mention + relationship-pattern extraction — pure,
zero I/O. No network calls anywhere in this file."""
from __future__ import annotations

from src.data_access.company_discovery.extraction_rules import (
    extract_all_matches,
    extract_relationship_matches,
    extract_thematic_mentions,
)
from src.models.company_discovery_models import RelationshipType


def test_supplier_pattern_matches_nearby_org_mention():
    text = "NVIDIA announced components supplied by Example Materials Corp. for the new facility."
    matches = extract_relationship_matches(text)
    assert len(matches) == 1
    assert matches[0].relationship_type == RelationshipType.SUPPLIER
    assert matches[0].org_text == "Example Materials Corp."
    assert matches[0].matched_pattern_category == "supplied_by"


def test_customer_pattern_matches():
    text = "Our customer Example Systems Ltd. placed a large order this quarter."
    matches = extract_relationship_matches(text)
    assert any(m.relationship_type == RelationshipType.CUSTOMER and m.org_text == "Example Systems Ltd." for m in matches)


def test_partner_pattern_matches():
    text = "NVIDIA and Example Photonics Inc. announced a strategic partnership today."
    matches = extract_relationship_matches(text)
    assert any(m.relationship_type == RelationshipType.PARTNER and "Example Photonics" in m.org_text for m in matches)


def test_competitor_pattern_matches():
    text = "Example Rival Corp. remains a competitor in the same market segment."
    matches = extract_relationship_matches(text)
    assert any(m.relationship_type == RelationshipType.COMPETITOR for m in matches)


def test_trigger_phrase_with_no_nearby_org_mention_yields_nothing():
    text = "The company reported strong results, supplied by internal manufacturing improvements."
    matches = extract_relationship_matches(text)
    assert matches == ()


def test_plain_prose_with_no_trigger_phrase_yields_no_relationship_matches():
    text = "The quarterly results were in line with analyst expectations across the board."
    assert extract_relationship_matches(text) == ()


def test_thematic_mention_requires_theme_keyword_and_org_mention():
    text = "The new memory fab will be built with equipment from Example Tools GmbH."
    matches = extract_thematic_mentions(text)
    assert any(m.relationship_type == RelationshipType.THEMATIC_MENTION and "Example Tools" in m.org_text for m in matches)


def test_thematic_mention_alone_never_outranks_a_real_relationship_match():
    text = "Memory components supplied by Example Materials Corp. for the new facility."
    matches = extract_all_matches(text)
    # Exactly one match for this org — the stronger supplier category,
    # never double-counted under both supplier and thematic_mention.
    org_matches = [m for m in matches if "Example Materials" in m.org_text]
    assert len(org_matches) == 1
    assert org_matches[0].relationship_type == RelationshipType.SUPPLIER


def test_empty_text_yields_no_matches():
    assert extract_all_matches("") == ()
    assert extract_relationship_matches("") == ()
    assert extract_thematic_mentions("") == ()


def test_extract_all_matches_combines_relationship_and_thematic():
    text = (
        "NVIDIA is supplied by Example Materials Corp. Separately, the photonics segment "
        "mentioned Example Optics Ltd. as a research partner in the field."
    )
    matches = extract_all_matches(text)
    org_texts = {m.org_text for m in matches}
    assert "Example Materials Corp." in org_texts
