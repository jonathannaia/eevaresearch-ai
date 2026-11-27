"""The Item anchor label on an EDGAR filing card (filing_display.item_anchor_label).

Every anchored 8-K card in the 2026-11-18..25 production window read
"Item Item 2.02". Two modules each believed they owned the word "Item":
edgar/document_extractor.extract_excerpt() stores
location_section=f"Item {anchor_item}", and the display prefixed "Item "
onto whatever it found there.

The fix belongs in the display, not the extractor. EvidenceLocation is
write-once (models.record_excerpt), so every row already stored carries
the prefixed form and always will — changing the extractor would fix
new rows and leave the existing ones doubled forever. And `section` is
not EDGAR-only: EDINET writes a Japanese statutory heading into it, so
the display cannot assume the value is a bare item number."""
from __future__ import annotations

import pytest

from src.data_access.edgar import document_extractor
from src.logic.filing_display import item_anchor_label
from src.models.models import EvidenceLocation, LocationKind


def _section(value: str | None) -> EvidenceLocation:
    return EvidenceLocation(kind=LocationKind.SECTION, section=value)


def test_the_value_the_extractor_actually_stores_is_not_doubled():
    """The regression. "Item 2.02" is what is in the database."""
    assert item_anchor_label(_section("Item 2.02")) == "Item 2.02"


def test_the_extractor_really_does_store_the_prefixed_form():
    """Pins the premise above, so this test file stops making sense the
    moment the extractor's contract changes, rather than quietly
    guarding a case that no longer occurs."""
    result = document_extractor.extract_excerpt(
        b"<html><body>Item 2.02 Results of Operations and Financial Condition. "
        b"On November 24, 2026, the Company issued a press release.</body></html>",
        expected_items=("2.02",),
    )
    assert result.location_section == "Item 2.02"


def test_a_bare_item_number_still_gets_the_word():
    """_item_anchored_excerpt's own documented return value is the bare
    number, so a caller that stores that form must still render."""
    assert item_anchor_label(_section("2.02")) == "Item 2.02"


def test_the_check_is_case_insensitive():
    assert item_anchor_label(_section("item 5.02")) == "item 5.02"


def test_a_japanese_heading_would_be_mislabelled_and_only_the_caller_prevents_it():
    """EDINET writes statutory headings like this into the same
    `section` field, and this function would call one an Item — a form
    structure Japanese filings do not have.

    Nothing here guards against that; radar_card.py does, by calling
    this only inside its English-native branch. Asserting the unguarded
    behaviour rather than quietly fixing it keeps the dependency
    visible: widen the caller and this is what the card will print."""
    assert item_anchor_label(_section("【事業等のリスク】")) == (
        "Item 【事業等のリスク】"
    )


@pytest.mark.parametrize("value", [None, "", "   "])
def test_nothing_recorded_means_no_label(value):
    assert item_anchor_label(_section(value)) is None


def test_a_non_section_location_has_no_item_label():
    assert item_anchor_label(EvidenceLocation(kind=LocationKind.UNAVAILABLE)) is None
    assert item_anchor_label(None) is None
