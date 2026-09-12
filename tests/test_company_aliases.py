"""company_aliases.build_company_alias_entries — pure, fixture-free
(reads the real, live tracked-company roster; no network, no mocking
needed since get_tracked_companies() is itself pure/static data)."""
from __future__ import annotations

from src.data_access.daily_news.company_aliases import (
    CompanyAliasEntry,
    _strip_legal_suffix,
    build_company_alias_entries,
)


def test_returns_one_entry_per_active_tracked_company():
    from src.config.tracked_companies import get_tracked_companies

    entries = build_company_alias_entries()
    active_names = {c.name for c in get_tracked_companies() if c.active}
    assert {e.company_name for e in entries} == active_names
    assert len(entries) == len(active_names)


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
