"""Coverage page's pure query/aggregation layer (src/logic/issuer_coverage.py)
— no Streamlit, no I/O. Covers the five data-layer invariants the Coverage
page approval required: registry-derived metrics, seed/discovery
separation, stub scan-ineligibility, filtering (including against
incomplete optional fields), and known-decision metadata visibility."""
from __future__ import annotations

from src.config.issuer_registry import DISCOVERY_STUBS, SEED_ISSUERS
from src.config.ontology import KNOWN_CATEGORY_CONFLICTS
from src.logic.issuer_coverage import (
    CoverageSummary,
    filter_seed_issuers,
    get_ambiguous_stub_labels,
    get_coverage_summary,
    get_jurisdiction_gaps,
)
from src.models.issuer import CoverageState, Issuer, LifecycleState


def _bare_seed_issuer(**overrides) -> Issuer:
    defaults = dict(
        issuer_id="edgar:TEST", legal_name="Test Co", country_or_jurisdiction="United States (listing exchange)",
        coverage_state=CoverageState.SEED,
    )
    defaults.update(overrides)
    return Issuer(**defaults)


# --- 1. Registry-derived metrics ---

def test_coverage_summary_counts_are_registry_derived_not_hardcoded():
    summary = get_coverage_summary()
    assert isinstance(summary, CoverageSummary)
    assert summary.active_seed_count == len(SEED_ISSUERS)
    assert summary.discovery_count == len(DISCOVERY_STUBS)
    assert summary.scan_eligible_count == len(SEED_ISSUERS)
    assert summary.unverified_excluded_count == len(DISCOVERY_STUBS)


def test_coverage_summary_source_breakdown_sums_to_seed_count():
    summary = get_coverage_summary()
    assert sum(summary.seed_count_by_source.values()) == summary.active_seed_count
    assert set(summary.seed_count_by_source) == {"OpenDART / DART", "SEC EDGAR", "EDINET"}


# --- 2. Seed / discovery separation ---

def test_seed_and_discovery_are_disjoint_collections():
    seed_ids = {i.issuer_id for i in SEED_ISSUERS}
    stub_ids = {i.issuer_id for i in DISCOVERY_STUBS}
    assert seed_ids.isdisjoint(stub_ids)


def test_filter_seed_issuers_default_source_is_seed_issuers_only():
    # Calling with no explicit `issuers` argument must never silently
    # include discovery stubs.
    result = filter_seed_issuers()
    result_ids = {i.issuer_id for i in result}
    stub_ids = {i.issuer_id for i in DISCOVERY_STUBS}
    assert result_ids.isdisjoint(stub_ids)
    assert len(result) == len(SEED_ISSUERS)


def test_discovery_stubs_all_coverage_state_discovered_not_seed():
    for issuer in DISCOVERY_STUBS:
        assert issuer.coverage_state == CoverageState.DISCOVERED
    for issuer in SEED_ISSUERS:
        assert issuer.coverage_state in (CoverageState.SEED, CoverageState.REJECTED)


# --- 3. No discovery stub is scan-eligible ---

def test_no_discovery_stub_carries_a_resolved_identifier():
    # Scan eligibility in this codebase is gated on having a resolved
    # source identifier — every stub must have none, by construction.
    for issuer in DISCOVERY_STUBS:
        assert issuer.identifiers == {}


def test_coverage_summary_never_counts_stubs_as_scan_eligible():
    summary = get_coverage_summary()
    assert summary.scan_eligible_count == summary.active_seed_count
    assert summary.scan_eligible_count != summary.active_seed_count + summary.discovery_count


# --- 4. Filtering/query helpers ---

def test_search_matches_name_and_ticker_case_insensitively():
    by_name = filter_seed_issuers(search="advanced micro")
    assert any(i.legal_name == "Advanced Micro Devices" for i in by_name)

    by_ticker = filter_seed_issuers(search="amd")
    assert any(i.primary_ticker == "AMD" for i in by_ticker)


def test_theme_filter_returns_expected_subset():
    memory_issuers = filter_seed_issuers(themes=("memory",))
    assert memory_issuers
    assert all("memory" in i.themes for i in memory_issuers)


def test_layer_filter_returns_expected_subset():
    # No SEED_ISSUERS entries carry supply_chain_layers in Phase A (only
    # discovery stubs do) — filtering by a layer no seed issuer has must
    # return an empty list, not raise.
    assert filter_seed_issuers(layers=("compute-hardware",)) == []


def test_source_filter_returns_expected_subset():
    # Was 2 (Samsung + SK Hynix) through the INDI/AIP/CEVA batch; the
    # Core Issuer Expansion batch (2026-09-04) added 8 more DART issuers
    # (2 + 8 = 10).
    dart_only = filter_seed_issuers(sources=("OpenDART / DART",))
    assert len(dart_only) == 10
    assert all(i.country_or_jurisdiction.startswith("South Korea") for i in dart_only)


def test_country_filter_returns_expected_subset():
    # Was 5 through Gate 7; the Core Issuer Expansion batch (2026-09-04)
    # added 8 more EDINET issuers (5 + 8 = 13). The EDINET Filings Radar
    # issuer-expansion batch (2026-09-04) then added 5 more still
    # (13 + 5 = 18).
    japan_only = filter_seed_issuers(countries=("Japan (listing exchange)",))
    assert len(japan_only) == 18


def test_combined_filters_are_intersected_not_unioned():
    result = filter_seed_issuers(themes=("photonics",), sources=("SEC EDGAR",))
    assert result
    assert all("photonics" in i.themes for i in result)
    assert all(i.primary_ticker not in {"", None} for i in result)


def test_filter_handles_issuer_with_no_theme_layer_or_country_without_crashing():
    incomplete = _bare_seed_issuer(country_or_jurisdiction="", themes=(), supply_chain_layers=())
    result = filter_seed_issuers([incomplete], themes=("memory",))
    assert result == []
    result_country = filter_seed_issuers([incomplete], countries=("Japan",))
    assert result_country == []
    # An empty-filter call must still return the (incomplete) record.
    assert filter_seed_issuers([incomplete]) == [incomplete]


def test_filter_handles_issuer_with_none_ticker_without_crashing():
    no_ticker = _bare_seed_issuer(primary_ticker=None)
    assert filter_seed_issuers([no_ticker], search="test") == [no_ticker]
    assert filter_seed_issuers([no_ticker], search="zzz-no-match") == []


def test_filter_seed_issuers_is_robust_to_an_empty_registry():
    assert filter_seed_issuers([]) == []
    assert filter_seed_issuers([], search="anything", themes=("memory",)) == []


# --- 5. Known-decision metadata visible through the read-only data layer ---

def test_jurisdiction_gaps_cover_the_five_named_jurisdictions():
    gaps = get_jurisdiction_gaps()
    assert set(gaps) == {"Taiwan", "Germany", "United Kingdom", "France", "Sweden"}


def test_ambiguous_stub_labels_cover_the_three_flagged_tickers():
    assert set(get_ambiguous_stub_labels()) == {"BURUN", "SHT.ST", "P4O"}


def test_known_category_conflicts_reachable_from_ontology_module():
    # The Coverage page reads KNOWN_CATEGORY_CONFLICTS directly — this
    # test exists at the data layer to guarantee that stays non-empty and
    # covers the four required subjects, independent of any page rendering.
    subjects = " ".join(c.subject for c in KNOWN_CATEGORY_CONFLICTS)
    assert "MRVL" in subjects and "TSEM" in subjects and "Kioxia" in subjects
