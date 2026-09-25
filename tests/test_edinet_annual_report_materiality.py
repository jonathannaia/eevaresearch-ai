"""edinet.annual_report_materiality — the first-pass annual-report
evidence gate. Pure functions over strings: no archive, no client, no
cache, no network, no database. Every Japanese string here is either a
statutory form heading (document structure, not filing content) or prose
written for this test suite; no live EDINET document body is copied,
captured, or committed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.data_access.edinet.annual_report_materiality import (
    BOILERPLATE_MARKERS,
    OUTCOME_NOT_MATERIAL,
    OUTCOME_PREFERRED_SECTION,
    AnnualReportMaterialityResult,
    assess_annual_report_materiality,
    find_boilerplate_marker,
)
from src.data_access.edinet.document_extractor import (
    MAX_ANNUAL_REPORT_MEMBERS_SCANNED,
    PREFERRED_ANNUAL_REPORT_SECTIONS,
)

_ISSUER_SPECIFIC = "当連結会計年度の受注高は前期比32.4%増の1,284億円となりました。"
_BOILERPLATE = "1. 連結財務諸表及び財務諸表の作成方法について 当社の連結財務諸表は規則に基づき作成しております。"

_ALL_HEADINGS = [h for rank in PREFERRED_ANNUAL_REPORT_SECTIONS for h in rank]


# --- decision 1: a selected preferred section qualifies -----------------

@pytest.mark.parametrize("heading", _ALL_HEADINGS)
def test_every_preferred_heading_qualifies_as_evidence_location(heading):
    result = assess_annual_report_materiality(_ISSUER_SPECIFIC, heading)

    assert result.outcome == OUTCOME_PREFERRED_SECTION
    assert result.matched_section == heading
    assert result.detail == f"Preferred annual-report section selected: {heading}."
    assert result.matched_boilerplate_marker is None


@pytest.mark.parametrize("marker", BOILERPLATE_MARKERS)
def test_boilerplate_marker_inside_a_selected_section_does_not_reject_it(marker):
    # A cross-reference to the accounting section from inside an
    # issuer-specific section is not the evidence; it must not suppress.
    excerpt = f"【事業等のリスク】 {_ISSUER_SPECIFIC} なお詳細は{marker}を参照。"
    result = assess_annual_report_materiality(excerpt, "【事業等のリスク】")

    assert result.outcome == OUTCOME_PREFERRED_SECTION
    assert result.matched_boilerplate_marker is None


def test_a_selected_section_qualifies_even_with_an_empty_excerpt():
    # location_section is only ever set by a successful anchored
    # extraction, so it is authoritative on its own.
    assert assess_annual_report_materiality("", "【事業の状況】").outcome == OUTCOME_PREFERRED_SECTION


def test_accepting_wording_claims_evidence_location_not_economic_materiality():
    detail = assess_annual_report_materiality(_ISSUER_SPECIFIC, "【事業等のリスク】").detail
    lowered = detail.lower()
    assert "section selected" in lowered
    for overclaim in ("material", "significant", "important", "impact"):
        assert overclaim not in lowered, detail


# --- decision 2: no selected section -> NOT material --------------------

@pytest.mark.parametrize("marker", BOILERPLATE_MARKERS)
def test_each_boilerplate_marker_without_a_section_is_suppressed_by_name(marker):
    result = assess_annual_report_materiality(f"{marker} に関する記載です。", None)

    assert result.outcome == OUTCOME_NOT_MATERIAL
    assert result.matched_boilerplate_marker == marker
    assert result.detail == f"Selected annual-report evidence matched boilerplate marker: {marker}."
    assert result.matched_section is None


def test_the_real_world_accounting_preamble_shape_is_suppressed():
    result = assess_annual_report_materiality(f"第5【経理の状況】 {_BOILERPLATE}", None)

    assert result.outcome == OUTCOME_NOT_MATERIAL
    assert result.matched_boilerplate_marker in BOILERPLATE_MARKERS


def test_no_section_and_no_marker_is_still_suppressed():
    result = assess_annual_report_materiality("第4【提出会社の状況】 記載事項はありません。", None)

    assert result.outcome == OUTCOME_NOT_MATERIAL
    assert result.matched_boilerplate_marker is None
    assert result.detail == (
        "No preferred issuer-specific annual-report section found in the first "
        f"{MAX_ANNUAL_REPORT_MEMBERS_SCANNED} safe body members."
    )


def test_issuer_specific_text_without_a_selected_section_is_still_suppressed():
    # Deliberate: the gate trusts the extractor's bounded, anchored
    # selection, never an ad-hoc keyword read of unanchored text.
    assert assess_annual_report_materiality(_ISSUER_SPECIFIC, None).outcome == OUTCOME_NOT_MATERIAL


@pytest.mark.parametrize("excerpt", [None, "", "   ", "\n\t "])
def test_missing_or_blank_excerpt_is_suppressed_with_a_stated_reason(excerpt):
    result = assess_annual_report_materiality(excerpt, None)

    assert result.outcome == OUTCOME_NOT_MATERIAL
    assert result.detail == "No annual-report excerpt was available to assess."


@pytest.mark.parametrize("location_section", ["", None])
def test_blank_location_section_is_treated_as_no_section(location_section):
    assert assess_annual_report_materiality(_BOILERPLATE, location_section).outcome == OUTCOME_NOT_MATERIAL


# --- invariants ---------------------------------------------------------

@pytest.mark.parametrize(
    "excerpt,section",
    [
        (_ISSUER_SPECIFIC, "【事業等のリスク】"),
        (_BOILERPLATE, None),
        ("第4【提出会社の状況】", None),
        (None, None),
        ("", ""),
    ],
)
def test_detail_is_never_empty_on_any_branch(excerpt, section):
    assert assess_annual_report_materiality(excerpt, section).detail.strip()


@pytest.mark.parametrize(
    "excerpt,section",
    [(_ISSUER_SPECIFIC, "【事業等のリスク】"), (_BOILERPLATE, None), (None, None)],
)
def test_outcome_is_always_one_of_the_two_declared_values(excerpt, section):
    assert assess_annual_report_materiality(excerpt, section).outcome in (
        OUTCOME_PREFERRED_SECTION, OUTCOME_NOT_MATERIAL,
    )


def test_assessment_is_deterministic_and_frozen():
    first = assess_annual_report_materiality(_BOILERPLATE, None)
    second = assess_annual_report_materiality(_BOILERPLATE, None)
    assert first == second
    with pytest.raises(Exception):
        first.outcome = "mutated"  # type: ignore[misc]


def test_result_is_the_declared_dataclass():
    assert isinstance(assess_annual_report_materiality(None, None), AnnualReportMaterialityResult)


def test_find_boilerplate_marker_returns_the_first_configured_match_or_none():
    assert find_boilerplate_marker("【経理の状況】") == "【経理の状況】"
    assert find_boilerplate_marker(_ISSUER_SPECIFIC) is None


def test_the_four_approved_markers_are_exactly_the_configured_set():
    assert BOILERPLATE_MARKERS == (
        "連結財務諸表の用語、様式及び作成方法",
        "連結財務諸表及び財務諸表の作成方法について",
        "【経理の状況】",
        "監査報告書",
    )


def test_the_gate_takes_no_translation_input_of_any_kind():
    # Structural, not conventional: translation-independence is enforced
    # by there being no parameter through which a translation could pass.
    import inspect
    params = list(inspect.signature(assess_annual_report_materiality).parameters)
    assert params == ["excerpt_original", "location_section"]


def test_the_gate_module_performs_no_io():
    source = Path(assess_annual_report_materiality.__module__.replace(".", "/") + ".py")
    text = (Path(__file__).parent.parent / source).read_text(encoding="utf-8")
    for forbidden in ("import requests", "open(", "Path(", "requests.", "urllib", "sqlite3", "psycopg"):
        assert forbidden not in text, forbidden


def test_no_live_edinet_content_is_present_in_this_module():
    source = Path(__file__).read_text(encoding="utf-8")
    for needle in ("Laser" + "tec", "E0" + "1991", "S100" + "Z32V"):
        assert needle not in source, needle
