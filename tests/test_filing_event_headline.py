"""Event headlines for filing cards (filing_event_headline).

Every excerpt below is the real stored text of a real filing from the
2026-11-18..25 production window, transcribed from the read-only dump
in scripts/preview_filing_event_headlines.py. Fabricated 8-K prose is
exactly the thing that would make these tests pass while the feature
fails: a hand-written excerpt has a tidy single sentence, and a real
one begins with a statutory item heading that is identical on every
filing of that item.

The property that matters is in test_every_extracted_headline_is_a_
subsequence_of_its_excerpt: an extracted headline can only ever be the
excerpt with words deleted. That is what makes "grounded" a checkable
claim rather than an intention."""
from __future__ import annotations

import pytest

from src.data_access.daily_news.filing_event_headline import (
    EVENT_HEADLINE_MAX_CHARS,
    edgar_event_headline,
    fallback_headline,
    is_grounded_in,
    native_filing_headline,
    normalized_item_anchor,
)

# --- real excerpts ----------------------------------------------------

NVIDIA_5_02 = (
    "Item 5.02. Departure of Directors or Certain Officers; Election of Directors; "
    "Appointment of Certain Officers; Compensatory Arrangements of Certain Officers. "
    "On November 22, 2026, the Board of Directors of NVIDIA Corporation reinstated "
    "Jen-Hsun Huang as NVIDIA’s President, Chief Executive Officer and member of the "
    "Board of Directors, effective immediately. Following the reinstatement of Mr. Huang, "
    "Ms. Kress will continue to serve as NVIDIA’s Executive Vice President and Chief "
    "Financial Officer. A copy of the press release announcing the above is attached as "
    "Exhibit 99.1 to this Current Report on Form 8-K."
)

DELL_2_02 = (
    "Item 2.02 Results of Operations and Financial Condition. On November 24, 2026, "
    "Dell Technologies Inc. (the “Company” or “Dell”) issued a press release announcing "
    "its financial results for its fiscal quarter ended October 30, 2026. A copy of the "
    "press release is furnished as Exhibit 99.1 to this current report."
)

ADI_2_02 = (
    "Item 2.02. Results of Operations and Financial Condition On November 24, 2026, "
    "Analog Devices, Inc. (the “Registrant”) announced its financial results for its "
    "fourth quarter and fiscal year ended October 31, 2026. The full text of the press "
    "release issued by the Registrant concerning the foregoing results is furnished "
    "herewith as Exhibit 99.1."
)

QUALCOMM_5_02 = (
    "Item 5.02. Departure of Directors or Certain Officers; Election of Directors; "
    "Appointment of Certain Officers; Compensatory Arrangements of Certain Officers. "
    "(e) Value Creation Awards On November 20, 2026 (the “Grant Date”), the Board of "
    "Directors (the “Board”) of QUALCOMM Incorporated (the “Company”) approved the grant "
    "of special long-term equity incentive awards (the “Value Creation Awards” or the "
    "“Awards”) to Cristiano Amon, the Company’s President and Chief Executive Officer, "
    "and Akash Palkhiwala, the Company’s Executive Vice President, Chief Financial "
    "Officer and Chief Operating Officer."
)

#: A 10-K's stored excerpt in this corpus. Not prose at all.
ADI_10K_XBRL = (
    "adi-20261031 0000006281 False 2026 FY http://fasb.org/us-gaap/2026#AccruedIncomeTaxesCurrent "
    "http://fasb.org/us-gaap/2026#OtherAssetsNoncurrent http://fasb.org/us-gaap/2026#AccruedLiabilitiesCurrent"
)

REAL_8K = [
    ("NVIDIA", "governance_or_management_change", NVIDIA_5_02),
    ("Dell Technologies Inc.", "earnings_or_results", DELL_2_02),
    ("Analog Devices, Inc.", "earnings_or_results", ADI_2_02),
    ("Qualcomm Incorporated", "governance_or_management_change", QUALCOMM_5_02),
]


def _extract(company, category, excerpt, **kw):
    return edgar_event_headline(
        company=company, category=category, excerpt=excerpt,
        form_type=kw.pop("form_type", "8-K"), item_anchor=kw.pop("item_anchor", None), **kw,
    )


# --- the property the whole module exists to guarantee ----------------

@pytest.mark.parametrize("company,category,excerpt", REAL_8K)
def test_every_extracted_headline_is_a_subsequence_of_its_excerpt(company, category, excerpt):
    result = _extract(company, category, excerpt)
    assert result.is_extracted, result.rejected_because
    body = result.headline.removeprefix(f"{company} — ").rstrip("…")
    assert is_grounded_in(body, excerpt)


def test_a_paraphrase_is_not_grounded_even_though_every_name_appears():
    """The headline a model would write for the NVIDIA filing. Every
    proper noun in it is in the excerpt, which is exactly why "do the
    names appear?" is not a sufficient check: the verb tense changed and
    "Chief Executive Officer" became "CEO", so it is new text."""
    assert not is_grounded_in("NVIDIA reinstates Jen-Hsun Huang as CEO", NVIDIA_5_02)


def test_reordering_the_words_breaks_grounding():
    assert is_grounded_in("Jen-Hsun Huang as NVIDIA’s President", NVIDIA_5_02)
    assert not is_grounded_in("NVIDIA’s President Jen-Hsun Huang", NVIDIA_5_02)


# --- what the headlines actually say ----------------------------------

def test_the_nvidia_filing_names_the_event_instead_of_the_form():
    result = _extract("NVIDIA", "governance_or_management_change", NVIDIA_5_02)
    assert result.headline == (
        "The Board of Directors of NVIDIA Corporation reinstated Jen-Hsun Huang as "
        "NVIDIA’s President, Chief Executive Officer and member of the Board of Directors…"
    )
    assert "8-K" not in result.headline


def test_the_statutory_item_heading_never_becomes_the_headline():
    """"Item 5.02. Departure of Directors or Certain Officers; ..." is
    identical on every 5.02 filing ever made. It is a list of nouns, so
    requiring a finite verb is what excludes it."""
    for _, _, excerpt in REAL_8K:
        result = _extract("X", "governance_or_management_change", excerpt)
        assert not result.headline.startswith("Item ")
        assert "Departure of Directors or Certain Officers" not in result.headline


def test_the_date_preamble_is_dropped_and_the_parenthetical_with_it():
    result = _extract("Dell Technologies Inc.", "earnings_or_results", DELL_2_02)
    assert result.headline == (
        "Dell Technologies Inc. issued a press release announcing its financial results "
        "for its fiscal quarter ended October 30, 2026"
    )
    assert "On November 24" not in result.headline
    assert "the “Company”" not in result.headline


def test_a_dropped_trailing_clause_is_always_marked():
    """Qualcomm's sentence names two officers and only the first fits.
    Cutting the second silently would misstate who the filing is
    about, so a trimmed headline always ends in an ellipsis."""
    result = _extract("Qualcomm Incorporated", "governance_or_management_change", QUALCOMM_5_02)
    assert result.is_extracted
    assert "Cristiano Amon" in result.headline
    assert "Akash Palkhiwala" not in result.headline
    assert result.headline.endswith("…")


@pytest.mark.parametrize("company,category,excerpt", REAL_8K)
def test_no_headline_runs_past_the_cap(company, category, excerpt):
    result = _extract(company, category, excerpt)
    body = result.headline.removeprefix(f"{company} — ").rstrip("…")
    assert len(body) <= EVENT_HEADLINE_MAX_CHARS


def test_the_company_is_prefixed_only_when_the_sentence_does_not_name_it():
    named = _extract("Dell Technologies Inc.", "earnings_or_results", DELL_2_02)
    assert not named.headline.startswith("Dell Technologies Inc. —")
    unnamed = _extract("Cisco Systems, Inc.", "earnings_or_results", DELL_2_02)
    assert unnamed.headline.startswith("Cisco Systems, Inc. —")


# --- the fallback -----------------------------------------------------

def test_raw_xbrl_never_reaches_the_card():
    result = _extract(
        "Analog Devices, Inc.", "earnings_or_results", ADI_10K_XBRL,
        form_type="10-K", excerpt_is_readable=False,
    )
    assert result.headline == "Analog Devices, Inc. — Results of operations (Form 10-K)"
    assert "fasb.org" not in result.headline
    assert "http" not in result.headline


def test_an_excerpt_with_no_dated_event_sentence_falls_back():
    result = _extract(
        "NVIDIA", "governance_or_management_change",
        "Item 5.02 Departure of Directors or Certain Officers; Election of Directors.",
    )
    assert not result.is_extracted
    assert result.rejected_because == "no dated event sentence"
    assert result.headline == "NVIDIA — Change of directors or officers (Form 8-K)"


def test_a_missing_excerpt_still_produces_a_usable_card():
    result = _extract("NVIDIA", "governance_or_management_change", None, item_anchor="Item 5.02")
    assert result.headline == "NVIDIA — Change of directors or officers (Form 8-K Item 5.02)"


def test_an_unmapped_category_is_readable_rather_than_blank():
    assert fallback_headline("ACME", "some_new_category", None, None) == "ACME — Some new category"


@pytest.mark.parametrize("stored", ["Item 5.02", "5.02", "Item Item 5.02"])
def test_the_item_anchor_is_never_doubled(stored):
    """filing_display.item_anchor_label() prefixes "Item " onto a
    section that production already stores as "Item 2.02", so its output
    really is "Item Item 2.02". The card must not repeat that."""
    assert normalized_item_anchor(stored) == "Item 5.02"


def test_no_item_anchor_means_no_item_in_the_headline():
    assert normalized_item_anchor(None) is None
    assert "Item" not in fallback_headline("ACME", "earnings_or_results", "8-K", None)


# --- Korean and Japanese filings --------------------------------------

def test_a_korean_filing_gets_the_curated_english_document_type():
    result = native_filing_headline(
        source_name="OpenDART / DART", category="risk_disclosure", title="중대재해 발생",
    )
    assert result.headline == "Risk or Incident Disclosure — 중대재해 발생"
    assert result.provenance == "native_title"


def test_a_japanese_filing_gets_the_curated_english_document_type():
    result = native_filing_headline(
        source_name="EDINET", category="shelf_registration", title="発行登録書（株券、社債券等）",
    )
    assert result.headline.startswith("Shelf Registration Statement — ")


def test_an_unmapped_native_category_keeps_the_filings_own_title():
    result = native_filing_headline(
        source_name="EDINET", category="not_a_category", title="有価証券報告書",
    )
    assert result.headline == "有価証券報告書"
