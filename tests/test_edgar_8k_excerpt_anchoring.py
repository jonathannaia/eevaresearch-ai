"""8-K excerpts must start at the item section, not the cover page.

THE CIRCULAR DEPENDENCY THIS BREAKS. SEC does not always publish an
8-K's item numbers when the filing appears. With items unknown the
caller had no expected_items to pass, so extraction fell to the
prefix-from-start path and captured the 600-character cover page —
company name, address, CIK, boilerplate — which stops before the item
section. The later re-check then read that excerpt looking for item
numbers, found none, and left the filing pending.

The excerpt could never contain what was needed to resolve the
excerpt.

Measured live on 2026-11-24: eight 8-Ks (Meta, AMD, Oracle, Western
Digital, Coherent, IBM, SanDisk, Lumentum) each stored exactly 600
characters of boilerplate, while every filing that did resolve had an
excerpt beginning at its item text."""
from __future__ import annotations

import pytest

from src.data_access.edgar.document_extractor import (
    EIGHT_K_ITEM_EXCERPT_CHARS,
    MAX_EXCERPT_CHARS,
    extract_excerpt,
)
from src.data_access.edgar.edgar_rules import extract_item_numbers

#: A faithful 8-K shape: SEC cover-page boilerplate long enough to
#: exhaust MAX_EXCERPT_CHARS on its own, then the item section.
_COVER_PAGE = (
    "lite-20261118 0001633978 false 0001633978 2026-11-23 UNITED STATES SECURITIES AND EXCHANGE "
    "COMMISSION Washington, D.C. 20549 FORM 8-K CURRENT REPORT Pursuant to Section 13 OR 15(d) of "
    "the Securities Exchange Act of 1934 Date of Report: November 23, 2026 "
    + ("Commission File Number 001-12345 Delaware 94-3177549 " * 12)
)
_ITEM_SECTION = (
    "Item 5.02. Departure of Directors or Certain Officers; Election of Directors; Appointment of "
    "Certain Officers. On November 23, 2026 the Board of Directors reinstated Jen-Hsun Huang as "
    "President and Chief Executive Officer, effective immediately."
)


def _document(cover: str = _COVER_PAGE, body: str = _ITEM_SECTION) -> bytes:
    return (cover + " " + body).encode("utf-8")


def test_the_cover_page_is_long_enough_to_hide_the_item_section():
    """Without this the rest of the file proves nothing."""
    assert len(_COVER_PAGE) > MAX_EXCERPT_CHARS


# --- the bug, and the fix ---------------------------------------------

def test_without_anchoring_the_excerpt_is_cover_page_and_yields_no_items():
    """The live failure, reproduced. This is the behaviour every
    pending-items 8-K got."""
    result = extract_excerpt(_document())

    assert result.excerpt_original.startswith("lite-20261118")
    assert "Item 5.02" not in result.excerpt_original
    assert extract_item_numbers(result.excerpt_original) == (), (
        "the re-check reads this excerpt; finding nothing is what left the filing pending"
    )


def test_anchoring_on_any_item_reaches_the_item_section():
    result = extract_excerpt(_document(), (), anchor_on_any_item=True)

    assert result.excerpt_original.startswith("Item 5.02")
    assert extract_item_numbers(result.excerpt_original) == ("5.02",), (
        "this is the whole point: the re-check can now resolve the filing from its own excerpt"
    )


def test_a_known_expected_item_still_anchors_exactly_as_before():
    """The existing path is untouched. anchor_on_any_item is additive."""
    known = extract_excerpt(_document(), ("5.02",))
    anchored = extract_excerpt(_document(), ("5.02",), anchor_on_any_item=True)
    assert known.excerpt_original == anchored.excerpt_original


def test_a_non_8k_document_keeps_the_prefix_from_start_behaviour():
    """The flag is passed only where the form type is known to be 8-K.
    Defaulting it off is what keeps every other form unchanged."""
    result = extract_excerpt(_document())
    assert len(result.excerpt_original) <= MAX_EXCERPT_CHARS


def test_an_8k_with_no_item_header_anywhere_falls_back_safely():
    """Anchoring must not fail a document it cannot anchor. A filing
    with no Item header at all still gets its prefix."""
    result = extract_excerpt(_document(body="No item headers appear in this document at all."),
                             (), anchor_on_any_item=True)
    assert result.excerpt_original
    assert len(result.excerpt_original) <= MAX_EXCERPT_CHARS


def test_the_anchored_excerpt_uses_the_larger_cap():
    result = extract_excerpt(_document(), (), anchor_on_any_item=True)
    assert len(result.excerpt_original) <= EIGHT_K_ITEM_EXCERPT_CHARS
    assert EIGHT_K_ITEM_EXCERPT_CHARS > MAX_EXCERPT_CHARS


def test_the_location_section_names_the_anchor_item():
    """So a reader of the card can see which part of the filing the
    excerpt came from, rather than trusting it blindly."""
    result = extract_excerpt(_document(), (), anchor_on_any_item=True)
    assert result.location_section == "Item 5.02"


# --- the recovery this enables, end to end ----------------------------

@pytest.mark.parametrize("item,expected", [
    ("Item 1.01. Entry into a Material Definitive Agreement.", "1.01"),
    ("Item 2.02 Results of Operations and Financial Condition.", "2.02"),
    ("Item 5.02. Departure of Directors or Certain Officers.", "5.02"),
    ("Item 2.03 Creation of a Direct Financial Obligation.", "2.03"),
])
def test_each_pending_filing_shape_recovers_its_item(item, expected):
    """One per item type seen among the eight filings that were stuck.
    Anchoring is what makes the re-check able to resolve them at all."""
    result = extract_excerpt(_document(body=item + " Details follow."), (), anchor_on_any_item=True)
    assert extract_item_numbers(result.excerpt_original) == (expected,)
