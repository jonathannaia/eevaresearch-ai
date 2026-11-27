"""The four 8-K item types that had no category, and what each is now.

WHY THESE EXISTED AS A GAP. Fourteen 8-Ks sat in the "items pending"
state. Anchoring their excerpts on the item header showed what they
actually were, and not one was a type the taxonomy knew:

    5.07  eight filings   Oracle, Western Digital, Coherent, Lumentum,
                          SanDisk, Modine, Credo, Aehr Test Systems
    3.02  two filings     Qualcomm, AMD
    9.01  two filings     IBM
    5.03  one filing      SanDisk

They were being excluded correctly and described wrongly. An unmapped
item reads as "we could not resolve this", which is what led to a
report of eight lost material filings that were in fact annual-meeting
housekeeping. Naming them makes the exclusion a statement rather than
a gap."""
from __future__ import annotations

import pytest

from src.data_access.daily_news.filing_signal_bridge import build_filing_signal_story, card_label_for
from src.data_access.daily_news.filing_signal_tiers import tier_for_filing_category
from src.data_access.edgar.edgar_rules import (
    EIGHT_K_ITEM_CATEGORIES,
    NAMED_BUT_EXCLUDED_ITEM_CATEGORIES,
    extract_item_numbers,
)
from src.models.daily_news_models import NewsMaterialityTier
from src.models.models import FilingEvent

NOW = "2026-11-24T00:00:00+00:00"
PENDING = ("material_event_8k_pending_items:8-K",)

#: Real opening lines from the fourteen re-extracted filings.
LIVE_EXCERPTS = {
    "5.07": "Item 5.07 Submission of Matters to a Vote of Security Holders. The annual meeting was held.",
    "9.01": "Item 9.01. Financial Statements and Exhibits. The following exhibits are filed herewith.",
    "3.02": "Item 3.02 Unregistered Sale of Equity Securities. The Company issued 2,000,000 shares.",
    "5.03": "Item 5.03 Amendments to Articles of Incorporation or Bylaws; Change in Fiscal Year.",
}


def _edgar() -> FilingEvent:
    return FilingEvent(
        rcept_no="0000804328-26-000100", corp_code="0000804328", corp_name="Qualcomm Incorporated",
        stock_code="QCOM", report_nm="8-K", pblntf_ty="8-K", rcept_dt="2026-11-23",
        flr_nm="Qualcomm", source_name="SEC EDGAR", source_url="https://www.sec.gov/x", retrieved_at=NOW,
    )


@pytest.mark.parametrize("item,category", [
    ("3.02", "unregistered_equity_sale"),
    ("5.03", "charter_amendment"),
    ("5.07", "shareholder_vote_results"),
    ("9.01", "financial_statements_and_exhibits"),
])
def test_each_item_now_has_a_category(item, category):
    assert EIGHT_K_ITEM_CATEGORIES[item] == category


@pytest.mark.parametrize("item", list(LIVE_EXCERPTS))
def test_each_item_is_now_readable_from_a_real_excerpt(item):
    """Before this, extract_item_numbers returned () for all four —
    which is what left them indistinguishable from an unresolvable
    filing."""
    assert extract_item_numbers(LIVE_EXCERPTS[item]) == (item,)


# --- 3.02: Early, labelled Financing ----------------------------------

def test_an_unregistered_equity_sale_is_early_not_high():
    """The filing reports that shares were issued, not what they were
    issued for. Dilution and a stock-funded acquisition look identical
    here, and only one of those belongs at the top of the feed."""
    assert tier_for_filing_category("unregistered_equity_sale") is NewsMaterialityTier.WATCHLIST


def test_an_unregistered_equity_sale_is_labelled_financing():
    assert card_label_for("unregistered_equity_sale") == "Financing"


def test_a_pending_3_02_filing_now_recovers_into_the_feed():
    """End to end: a filing stuck at "items pending" resolves from its
    own re-extracted excerpt and is projected as Early."""
    story = build_filing_signal_story(
        _edgar(), matched_rules=PENDING, retrieved_at=NOW,
        excerpt=LIVE_EXCERPTS["3.02"],
    )
    assert story is not None
    assert story.materiality_tier is NewsMaterialityTier.WATCHLIST
    assert story.materiality_reasons[0] == "filing_event:unregistered_equity_sale"
    assert "filing_label:Financing" in story.materiality_reasons


# --- 5.07 / 9.01 / 5.03: named, and still excluded --------------------

@pytest.mark.parametrize("item", ["5.07", "9.01", "5.03"])
def test_procedural_items_are_named_and_still_not_projected(item):
    """Naming them changes what we can SAY about them, not whether
    they appear. A shareholder-vote outcome, an exhibit index and a
    charter amendment are all procedural."""
    story = build_filing_signal_story(
        _edgar(), matched_rules=PENDING, retrieved_at=NOW,
        excerpt=LIVE_EXCERPTS[item],
    )
    assert story is None


@pytest.mark.parametrize("item", ["5.07", "9.01", "5.03"])
def test_the_exclusion_is_recorded_rather_than_implied(item):
    """The distinction this whole change is about: "known and not
    wanted" must be visible somewhere, not inferred from absence."""
    assert EIGHT_K_ITEM_CATEGORIES[item] in NAMED_BUT_EXCLUDED_ITEM_CATEGORIES


def test_no_approved_category_is_also_listed_as_excluded():
    """Two lists that could disagree. This is what stops them."""
    from src.data_access.daily_news.edgar_filing_candidate_adapter import _EIGHT_K_APPROVED_CATEGORIES

    assert not (_EIGHT_K_APPROVED_CATEGORIES & NAMED_BUT_EXCLUDED_ITEM_CATEGORIES)


def test_every_item_category_is_either_approved_or_named_excluded():
    """No third state. An item with a category that is neither
    admitted nor explicitly excluded is the gap this change closed."""
    from src.data_access.daily_news.edgar_filing_candidate_adapter import _EIGHT_K_APPROVED_CATEGORIES

    for item, category in EIGHT_K_ITEM_CATEGORIES.items():
        assert category in _EIGHT_K_APPROVED_CATEGORIES or category in NAMED_BUT_EXCLUDED_ITEM_CATEGORIES, (
            f"Item {item} -> {category} is in neither list"
        )


def test_the_previously_approved_items_are_unchanged():
    """This change admits exactly one new item. A regression here would
    mean it quietly admitted more."""
    from src.data_access.daily_news.edgar_filing_candidate_adapter import _EIGHT_K_APPROVED_CATEGORIES

    assert _EIGHT_K_APPROVED_CATEGORIES == {
        "material_agreement", "acquisition_or_disposition", "earnings_or_results",
        "financing_or_debt", "governance_or_management_change", "unregistered_equity_sale",
    }
