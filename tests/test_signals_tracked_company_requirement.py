"""A High Signal must be a claim about a company this product tracks.

Measured on the live feed, 8 of 32 High Signals had NO identified
company at all: both Russia satellite items, a Tuya earnings release
(Tuya is not tracked), "AI server makers hike prices up to 45%", and
"Korea's exports forecast to exceed $1 tril.". Each is real news; none
is news about a tracked issuer.

WHY A CAP AND NOT A REJECTION. An item with no tracked company can
still be the first sign of something — a supply-chain constraint, a
policy change, a sector-wide price move. Watchlist is where a reader
goes looking for that; Background would hide it.

WHY THE VETTED LIST. The rule reads matched_companies, the
admission-vetted subset, not identified_companies, the raw
pre-attribution text match this project's own design documents say
must never drive a visible tag."""
from __future__ import annotations

import pytest

from src.data_access.daily_news.materiality_classification import (
    cap_without_tracked_company,
    flag_vague_headline,
    headline_is_vague,
)
from src.models.daily_news_models import NewsMaterialityTier

GATE = ("formal_earnings_materials:financial results",)


# --- the live false positives ----------------------------------------

@pytest.mark.parametrize("label", [
    "Tuya Reports Third Quarter 2026 Unaudited Financial Results",
    "AI server makers hike prices up to 45% months after contracts are signed",
    "Korea's exports forecast to exceed $1 tril. for 2nd straight year in 2027",
    "Reports: Russia may launch nuclear weapon into orbit as soon as next month",
])
def test_a_high_signal_with_no_tracked_company_is_capped(label):
    tier, reasons = cap_without_tracked_company(NewsMaterialityTier.HIGH_SIGNAL, GATE, ())

    assert tier is NewsMaterialityTier.WATCHLIST, label
    assert reasons[0] == "no_tracked_company:formal_earnings_materials:financial results"
    assert GATE[0] in reasons, "the original gate must survive the cap, so it stays auditable"


def test_a_high_signal_with_a_tracked_company_is_untouched():
    tier, reasons = cap_without_tracked_company(NewsMaterialityTier.HIGH_SIGNAL, GATE, ("NVIDIA",))
    assert tier is NewsMaterialityTier.HIGH_SIGNAL
    assert reasons == GATE


def test_the_cap_stops_at_watchlist_not_background():
    """An item with no tracked company can still be the first sign of
    something. Background would hide it."""
    tier, _ = cap_without_tracked_company(NewsMaterialityTier.HIGH_SIGNAL, GATE, ())
    assert tier is not NewsMaterialityTier.BACKGROUND


@pytest.mark.parametrize("tier", [NewsMaterialityTier.WATCHLIST, NewsMaterialityTier.BACKGROUND])
def test_a_lower_tier_passes_through_unchanged(tier):
    """This can only reduce. It must never raise a Background item to
    Watchlist merely because it has no company."""
    out_tier, out_reasons = cap_without_tracked_company(tier, GATE, ())
    assert out_tier is tier and out_reasons == GATE


def test_an_empty_reason_tuple_does_not_crash():
    tier, reasons = cap_without_tracked_company(NewsMaterialityTier.HIGH_SIGNAL, (), ())
    assert tier is NewsMaterialityTier.WATCHLIST
    assert reasons == ("no_tracked_company:unknown",)


# --- the vague-headline flag ------------------------------------------

@pytest.mark.parametrize("headline", [
    "Nvidia gets more supplier capacity to meet AI demand",
    "Samsung sees more demand for HBM",
    "TSMC looks to expand its Arizona footprint",
    "Intel ramps up foundry output",
    "SK hynix is set to increase memory production",
])
def test_a_direction_without_an_event_is_flagged(headline):
    assert headline_is_vague(headline), headline
    assert "vague_headline" in flag_vague_headline(headline, GATE)


@pytest.mark.parametrize("headline", [
    "Samsung Orders Additional Protec Bonding Equipment for HBM Expansion",
    "NVIDIA reinstates Jensen Huang after 11-day absence",
    "Nvidia Raises GPU Prices by 15% Amid Tariff Pressures",
    "AI server makers hike prices up to 45% months after contracts are signed",
])
def test_a_concrete_headline_is_not_flagged(headline):
    assert not headline_is_vague(headline), headline
    assert flag_vague_headline(headline, GATE) == GATE


def test_a_number_makes_a_headline_concrete_enough():
    """A figure stops it being a direction, whatever the wording
    around it."""
    assert headline_is_vague("Nvidia gets more supplier capacity to meet AI demand")
    assert not headline_is_vague("Nvidia gets 30% more supplier capacity to meet AI demand")


def test_the_flag_changes_no_tier():
    """It is a marker, deliberately. Some of these are genuinely the
    first report of something real, so demoting them all would lose
    signal — this counts them first."""
    reasons = flag_vague_headline("Nvidia gets more supplier capacity to meet AI demand", GATE)
    assert reasons[0] == GATE[0], "reasons[0] stays the operative gate for every caller that reads it"
    assert reasons[-1] == "vague_headline"


def test_the_flag_is_not_applied_twice():
    once = flag_vague_headline("Intel ramps up foundry output", GATE)
    twice = flag_vague_headline("Intel ramps up foundry output", once)
    assert once == twice


@pytest.mark.parametrize("headline", ["", None])
def test_a_missing_headline_is_not_vague(headline):
    assert not headline_is_vague(headline)
