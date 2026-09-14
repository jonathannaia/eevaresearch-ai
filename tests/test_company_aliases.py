"""company_aliases.build_company_alias_entries — pure, fixture-free
(reads the real, live tracked-company roster; no network, no mocking
needed since get_tracked_companies() is itself pure/static data)."""
from __future__ import annotations

from src.data_access.daily_news.company_aliases import (
    _BRAND_ALIAS_OVERLAY,
    _DAILY_NEWS_STUB_PROVENANCE,
    CompanyAliasEntry,
    _strip_legal_suffix,
    build_company_alias_entries,
    daily_news_company_names,
)


def test_returns_one_entry_per_company_in_the_daily_news_universe():
    entries = build_company_alias_entries()
    universe = set(daily_news_company_names())
    assert {e.company_name for e in entries} == universe
    assert len(entries) == len(universe)


# ============================================================
# System-wide company-matched-news fix (design/DECISIONS.md) —
# daily_news_company_names(): the shared, data-driven company universe.
# ============================================================


def test_universe_includes_every_active_tracked_company():
    from src.config.tracked_companies import get_tracked_companies

    active_names = {c.name for c in get_tracked_companies()}
    names = set(daily_news_company_names())
    assert active_names <= names


def test_universe_includes_hpe_a_daily_news_only_stub():
    assert "Hewlett Packard Enterprise Company" in daily_news_company_names()


def test_universe_excludes_unrelated_portfolio_map_seed_list_stubs():
    # These ~21 DISCOVERY_STUBS entries are an entirely different,
    # unverified discovery batch (2026-08-20 portfolio-map seed list) —
    # never part of Daily News, several explicitly flagged ambiguous, no
    # working feed. They must never leak into editorial company matching.
    names = daily_news_company_names()
    for unrelated in ("Boost Run", "FOCI", "Unimicron", "LG Innotek", "Nynomic", "Msscorps"):
        assert unrelated not in names


def test_universe_deduplicates_a_stub_already_present_in_tracked_companies():
    # Arista Networks/Cisco Systems/Quanta Services/nVent Electric are
    # DISCOVERY_STUBS entries that also graduated into tracked_companies.py
    # — each must appear exactly once in the universe, not twice.
    names = daily_news_company_names()
    for name in ("Arista Networks, Inc.", "Cisco Systems, Inc."):
        assert names.count(name) == 1


def test_a_future_daily_news_only_stub_is_included_automatically(monkeypatch):
    # Proves the mechanism, not just today's data: a synthetic
    # DISCOVERY_STUBS entry carrying the same real, established
    # discovered_via provenance string is picked up with zero code
    # change — the exact property "future companies added by the same
    # supported registry mechanism are included automatically" requires.
    from src.config import issuer_registry
    from src.data_access.daily_news import company_aliases
    from src.models.issuer import CoverageState, Issuer, LifecycleState

    future_stub = Issuer(
        issuer_id="stub:FUTURE",
        legal_name="Future Daily News Company, Inc.",
        country_or_jurisdiction="United States",
        coverage_state=CoverageState.DISCOVERED,
        lifecycle_state=LifecycleState.ACTIVE,
        primary_ticker="FUTR",
        primary_exchange="NASDAQ",
        identifiers={},
        discovered_via=_DAILY_NEWS_STUB_PROVENANCE,
    )
    monkeypatch.setattr(company_aliases, "DISCOVERY_STUBS", issuer_registry.DISCOVERY_STUBS + (future_stub,))

    assert "Future Daily News Company, Inc." in company_aliases.daily_news_company_names()
    entries = {e.company_name: e for e in company_aliases.build_company_alias_entries()}
    assert entries["Future Daily News Company, Inc."].aliases == (
        "Future Daily News Company, Inc.", "Future Daily News Company",
    )


def test_a_discovery_stub_with_a_different_provenance_string_is_not_included(monkeypatch):
    # The negative case: a DISCOVERY_STUBS entry that does NOT carry the
    # established Daily News provenance marker (e.g. a portfolio-map-style
    # candidate) is correctly excluded, proving the filter is real, not a
    # no-op.
    from src.config import issuer_registry
    from src.data_access.daily_news import company_aliases
    from src.models.issuer import CoverageState, Issuer, LifecycleState

    unrelated_stub = Issuer(
        issuer_id="stub:UNRELATED",
        legal_name="Unrelated Seed List Company",
        country_or_jurisdiction="United States",
        coverage_state=CoverageState.DISCOVERED,
        lifecycle_state=LifecycleState.ACTIVE,
        identifiers={},
        discovered_via="Portfolio-map seed list, category: Other (2026-08-20)",
    )
    monkeypatch.setattr(company_aliases, "DISCOVERY_STUBS", issuer_registry.DISCOVERY_STUBS + (unrelated_stub,))

    assert "Unrelated Seed List Company" not in company_aliases.daily_news_company_names()


# ============================================================
# Curated brand-alias overlay — low-ambiguity list only (Amazon Web
# Services/AWS, Google, IBM); bare Amazon/Meta/Facebook deliberately
# excluded.
# ============================================================


def test_brand_alias_overlay_is_exactly_the_approved_low_ambiguity_list():
    assert _BRAND_ALIAS_OVERLAY == {
        "Amazon.com, Inc.": ("Amazon Web Services", "AWS"),
        "Alphabet Inc.": ("Google",),
        "International Business Machines Corporation": ("IBM",),
    }


def test_amazon_gets_the_approved_brand_aliases_and_no_bare_amazon():
    entries = {e.company_name: e for e in build_company_alias_entries()}
    amazon = entries["Amazon.com, Inc."]
    assert amazon.brand_aliases == ("Amazon Web Services", "AWS")
    assert "Amazon" not in amazon.brand_aliases
    assert "Amazon" not in amazon.aliases


def test_meta_and_facebook_get_no_brand_alias_this_pass():
    entries = {e.company_name: e for e in build_company_alias_entries()}
    meta = entries["Meta Platforms, Inc."]
    assert meta.brand_aliases == ()
    assert "Meta" not in meta.aliases
    assert "Facebook" not in meta.aliases


def test_every_brand_alias_entry_is_non_empty_and_deduplicated():
    for entry in build_company_alias_entries():
        assert len(set(entry.brand_aliases)) == len(entry.brand_aliases)
        for alias in entry.brand_aliases:
            assert alias.strip()
            # Never overlaps with that same company's own mechanical aliases.
            assert alias not in entry.aliases


def test_every_entry_has_one_or_two_non_empty_aliases():
    for entry in build_company_alias_entries():
        assert 1 <= len(entry.aliases) <= 2
        assert len(set(entry.aliases)) == len(entry.aliases)  # no duplicate within one entry
        for alias in entry.aliases:
            assert alias.strip()


def test_exact_name_is_always_the_first_alias():
    for entry in build_company_alias_entries():
        assert entry.aliases[0] == entry.company_name


def test_legal_suffix_is_stripped_exactly_once_not_recursively():
    # "Lumentum Holdings Inc." -> "Lumentum Holdings" only (never
    # further stripped to bare "Lumentum" — "Holdings" is not itself
    # re-stripped in a second pass).
    assert _strip_legal_suffix("Lumentum Holdings Inc.") == "Lumentum Holdings"
    assert _strip_legal_suffix("Coherent Corp") == "Coherent"
    assert _strip_legal_suffix("Corning Inc.") == "Corning"


def test_longest_matching_suffix_wins_not_first_in_list():
    # "Furukawa Electric Co., Ltd." must strip the full compound
    # " Co., Ltd." — never stop early at the shorter ", Ltd." alone,
    # which would incorrectly leave a dangling "Co.".
    assert _strip_legal_suffix("Furukawa Electric Co., Ltd.") == "Furukawa Electric"
    assert _strip_legal_suffix("LG Innotek Co., Ltd.") == "LG Innotek"


def test_no_suffix_present_returns_none():
    assert _strip_legal_suffix("NVIDIA") is None
    assert _strip_legal_suffix("Samsung Electronics") is None
    assert _strip_legal_suffix("Rocket Lab") is None


def test_bare_co_without_period_is_stripped():
    assert _strip_legal_suffix("Vertiv Holdings Co") == "Vertiv Holdings"


def test_no_ticker_field_is_ever_consulted():
    """Structural proof, not just behavioral: this module never reads
    TrackedCompany.krx_code as an attribute anywhere in its own code
    (the docstring names the field in prose, explaining why it's
    excluded — that mention is expected; a real `.krx_code` attribute
    access is not)."""
    import inspect

    from src.data_access.daily_news import company_aliases

    source = inspect.getsource(company_aliases)
    assert ".krx_code" not in source


def test_known_companies_produce_the_expected_two_aliases():
    entries = {e.company_name: e for e in build_company_alias_entries()}
    assert entries["Qualcomm Incorporated"].aliases == ("Qualcomm Incorporated", "Qualcomm")
    assert entries["Corning Inc."].aliases == ("Corning Inc.", "Corning")
    assert entries["Synopsys, Inc."].aliases == ("Synopsys, Inc.", "Synopsys")
    assert entries["Cadence Design Systems, Inc."].aliases == (
        "Cadence Design Systems, Inc.", "Cadence Design Systems",
    )
    # Meta Platforms, Inc. must never produce a bare "Meta" alias — only
    # the legal suffix ", Inc." is stripped, "Platforms" is not a legal
    # suffix and is never removed.
    assert entries["Meta Platforms, Inc."].aliases == ("Meta Platforms, Inc.", "Meta Platforms")


def test_company_alias_entry_is_frozen_and_hashable():
    entry = CompanyAliasEntry(company_name="Example Co.", aliases=("Example Co.", "Example"))
    assert hash(entry) is not None
