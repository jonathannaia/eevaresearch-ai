"""Cut points must not fall inside an abbreviation (filing_display).

Observed live on Navitas Semiconductor's 8-K Item 1.01 card:

    "Item 1.01. Entry into a Material Definitive Agreement. On
     November 20, 2026, Navitas Semiconductor USA, Inc."

shown as the whole summary. The sentence continues for another three
hundred characters — the period after "Inc." is an abbreviation, not a
sentence end, and extractive_summary() took it as the last boundary
inside its window.

Every corporate filing in this corpus is affected, because every
issuer's legal name ends in one of these words."""
from __future__ import annotations

import pytest

from src.logic import filing_display

NAVITAS = (
    "Item 1.01. Entry into a Material Definitive Agreement. On November 20, 2026, "
    "Navitas Semiconductor USA, Inc. (“Assignee”), a Delaware corporation and indirect "
    "subsidiary of Navitas Semiconductor Corporation (the “Company”), entered into a Patent "
    "Assignment Agreement with Longitude Flash Memory Solutions Limited (“LFMS”), a private "
    "company organized under the laws of the Republic of Ireland."
)

ADI = (
    "On November 24, 2026, Analog Devices, Inc. (the “Registrant”) announced its financial "
    "results for its fourth quarter and fiscal year ended October 31, 2026."
)


def test_the_summary_no_longer_stops_at_the_issuers_own_legal_suffix():
    summary = filing_display.extractive_summary(NAVITAS)
    assert not summary.endswith("Inc.")
    assert "Navitas Semiconductor USA, Inc." not in summary


def test_a_whole_statement_ending_after_an_abbreviation_survives_intact():
    """The case the defect truncated hardest: the only boundary before
    the real one is the period in "Inc.", so the summary was the first
    half of the sentence."""
    assert filing_display.extractive_summary(ADI) == ADI


@pytest.mark.parametrize("abbreviation", ["Inc.", "Corp.", "Co.", "Ltd.", "plc.", "Mr.", "No."])
def test_each_known_abbreviation_is_not_a_cut_point(abbreviation):
    text = f"The counterparty is Acme {abbreviation} and the agreement runs for five years."
    assert filing_display.extractive_summary(text) == text


def test_a_period_at_the_very_end_still_terminates_whatever_precedes_it():
    """Otherwise a document closing on an abbreviation is judged to
    have no sentence boundary at all and loses its summary entirely."""
    text = "Revenue grew in memory, logic, etc."
    assert filing_display.extractive_summary(text) == text
    assert filing_display.trim_excerpt_for_display(text) == text


def test_a_real_sentence_end_is_still_a_cut_point():
    text = "The board approved the plan. A copy is furnished as Exhibit 99.1 to this report."
    assert filing_display.extractive_summary(text) == text
    assert filing_display.trim_excerpt_for_display(
        "The board approved the plan. A copy is furnish"
    ) == "The board approved the plan."


def test_the_readability_gate_is_deliberately_untouched():
    """is_readable_extracted_text() asks whether a boundary EXISTS, as
    one shape signal among several for prose-versus-XBRL. Narrowing it
    would newly fail text whose only period follows an abbreviation —
    changing a readability verdict in order to fix a cut point."""
    text = "Acme Corp. " + "filed a report with the commission and disclosed the terms. " * 4
    assert filing_display.is_readable_extracted_text(text)
