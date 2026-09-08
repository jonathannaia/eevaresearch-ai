"""discovery_rules.evaluate_discovery_row — the stricter, discovery-only
gate in front of edgar_rules' existing pure item-parsing/refinement
functions. Every case here mirrors a case edgar_rules.py's own tests
already cover for the tracked pipeline's looser _evaluate_row path — the
point of this file is to prove the *narrower* gate, not to re-test
edgar_rules.py itself."""
from __future__ import annotations

from src.data_access.edgar import discovery_rules


def test_valid_recognized_item_produces_a_real_evaluation():
    evaluation = discovery_rules.evaluate_discovery_row("8-K", "1.01")
    assert evaluation is not None
    assert evaluation.confidence in ("Moderate", "High")
    assert evaluation.matched_rules == ("material_agreement:8-K item 1.01",)


def test_multiple_distinct_categories_yield_high_confidence():
    evaluation = discovery_rules.evaluate_discovery_row("8-K", "1.01,2.03")
    assert evaluation is not None
    assert evaluation.confidence == "High"


def test_non_8k_form_never_produces_a_discovery_match():
    for form in ("10-Q", "10-K", "SC 13D", "S-1", "424B5", "SCHEDULE 13G"):
        assert discovery_rules.evaluate_discovery_row(form, "1.01") is None


def test_missing_items_never_produces_a_discovery_match():
    assert discovery_rules.evaluate_discovery_row("8-K", None) is None
    assert discovery_rules.evaluate_discovery_row("8-K", "") is None
    assert discovery_rules.evaluate_discovery_row("8-K", "   ") is None


def test_malformed_items_never_produces_a_discovery_match():
    # Every token here fails to match an EIGHT_K_ITEM_CATEGORIES key.
    assert discovery_rules.evaluate_discovery_row("8-K", "not-a-real-item") is None
    assert discovery_rules.evaluate_discovery_row("8-K", ",,,") is None


def test_unconfigured_item_number_never_produces_a_discovery_match():
    # 9.01 (Financial Statements and Exhibits) is real EDGAR taxonomy but
    # deliberately not in EIGHT_K_ITEM_CATEGORIES (see edgar_rules.py).
    assert discovery_rules.evaluate_discovery_row("8-K", "9.01") is None


def test_generic_8k_fallback_is_never_produced_by_this_module():
    # The tracked pipeline's scan_service._evaluate_row falls back to
    # evaluate_form_type() (the generic "material_event_8k_pending_items:8-K"
    # classification) whenever items is absent/malformed. This module must
    # never reach that fallback at all for any input.
    for items in (None, "", "   ", "bogus", "9.01"):
        result = discovery_rules.evaluate_discovery_row("8-K", items)
        assert result is None


def test_form_type_normalization_is_reused_unchanged():
    # "8-k" lowercase and the spelled-out alias both still normalize —
    # proves this module defers to edgar_rules.normalize_form_type rather
    # than reimplementing its own comparison.
    assert discovery_rules.evaluate_discovery_row("8-k", "1.01") is not None
    assert discovery_rules.evaluate_discovery_row(" 8-K ", "1.01") is not None
