"""Headline ceiling for the editorial lane (materiality_classification).

Every fixture here is a REAL item taken from the live feed on
2026-11-23, with its real headline and a faithful reconstruction of the
excerpt shape that promoted it. The live High Signals list contained a
homelab build, a retail CPU deal, a newsletter roundup and a sponsored
post, all of which scored BACKGROUND on their own headlines and were
carried to the top of the feed entirely by excerpt text.

The rule under test: an item may only reach HIGH_SIGNAL if its headline
carries SOME materiality signal — a taxonomy phrase, an anchor keyword,
a number, formal-action or earnings language, a definitive transaction,
or a corporate-action verb. That is a much weaker test than a gate, on
purpose: the excerpt keeps all of its power to SUPPLY the proof of
materiality, and loses only the power to supply the subject as well.

Capping stops at WATCHLIST. Nothing here is suppressed."""
from __future__ import annotations

import pytest

from src.data_access.daily_news.materiality_classification import classify_editorial_story
from src.data_access.daily_news.source_registry import SourceCategory
from src.models.daily_news_models import NewsMaterialityTier

NEWS = SourceCategory.INDEPENDENT_NEWS


# --- the live false positives, each with the gate that promoted it ----

@pytest.mark.parametrize("headline,excerpt,gate", [
    pytest.param(
        "Homelabber's second-gen Optane build uses two RTX 3090s",
        "The build pairs 2TB of Intel Optane memory with two RTX 3090 GPUs to run a 70B parameter "
        "model locally, reaching 18 tokens per second. Total capacity across the array is 128GB of VRAM.",
        "taxonomy_anchored_consequence", id="homelab-build"),
    pytest.param(
        "Act fast to score Intel's Core i7-14700K for its lowest price ever",
        "The Core i7-14700K has dropped to $294, a 35% price cut from its launch price of $450. "
        "Retailers are clearing inventory ahead of the next generation.",
        "quantified_change", id="retail-cpu-deal"),
    pytest.param(
        # Deliberately NOT the Tom's Hardware newsletter roundup, which
        # was the obvious fixture here. A roundup is blocked outright
        # as a disqualifying format, which is strictly better than a
        # cap — using it would make this test pass for the other
        # change's reason and quietly stop testing the ceiling.
        "AI boom threatens circuit board crunch to rival pandemic-era chip shortage",
        "Circuit board capacity is 40% oversubscribed into 2027, with substrate lead times "
        "stretching past 30 weeks across the AI data center supply chain.",
        "quantified_change", id="market-commentary"),
    pytest.param(
        "Memory crunch makes refurbished kit look a lot less silly",
        "DRAM contract prices have risen 60% this quarter as HBM capacity absorbs supply, pushing "
        "buyers toward the refurbished market.",
        "quantified_change", id="market-colour"),
])
def test_an_excerpt_cannot_carry_a_contentless_headline_to_high_signal(headline, excerpt, gate):
    tier, reasons = classify_editorial_story(headline, excerpt, NEWS)

    assert tier is NewsMaterialityTier.WATCHLIST, reasons
    assert reasons[0].startswith("headline_ceiling_applied:"), reasons
    assert any(gate in r for r in reasons), (
        "the original gate must be preserved in the reasons, so the cap is auditable "
        "rather than an unexplained demotion"
    )


def test_the_capped_item_is_retained_not_suppressed():
    """The whole design depends on this. Capping to BACKGROUND would
    hide items whose excerpts show genuine relevance; Watchlist says
    'relevant, not shown to be material', which is the true statement."""
    tier, _ = classify_editorial_story(
        "Homelabber's second-gen Optane build uses two RTX 3090s",
        "The build pairs 2TB of Intel Optane memory with two RTX 3090 GPUs, 128GB of VRAM capacity.",
        NEWS,
    )
    assert tier is not NewsMaterialityTier.BACKGROUND


# --- genuine items that must be untouched -----------------------------

@pytest.mark.parametrize("headline,excerpt,why", [
    pytest.param(
        "Nvidia Raises GPU Prices by 15% Amid Tariff Pressures",
        "Nvidia confirmed a 15% price increase across its GPU lineup will take effect next quarter.",
        "a number and a corporate-action verb in the headline", id="price-rise"),
    pytest.param(
        "Samsung Orders Additional Protec Bonding Equipment for HBM Expansion",
        "Samsung Electronics has placed an additional order worth 30 billion won for thermal "
        "compression bonders to expand HBM capacity at its Pyeongtaek campus.",
        "taxonomy phrase and an action verb in the headline", id="hbm-equipment-order"),
    pytest.param(
        "KSAT Selected by Intuitive Machines to Support NASA JPL EAGLE-VSWIR Mission",
        "Intuitive Machines selected KSAT to provide ground segment support, delivering the "
        "spacecraft platform and mission operations under a new contract.",
        "a contract award stated as a verb, with no anchor noun anywhere in it", id="contract-award"),
    pytest.param(
        "TSMC reportedly pauses shipments to Chinese designers after U.S. rule change",
        "TSMC has paused shipments of advanced nodes to at least 12 Chinese chip designers "
        "following the latest export control revision, affecting roughly 8% of its foundry volume.",
        "formal-action language and an action verb", id="supply-halt"),
    pytest.param(
        "Tuya Reports Third Quarter 2026 Unaudited Financial Results",
        "Tuya Inc. today announced its unaudited financial results for the third quarter.",
        "earnings language in the headline", id="earnings-release"),
])
def test_a_headline_that_states_an_event_is_never_capped(headline, excerpt, why):
    tier, reasons = classify_editorial_story(headline, excerpt, NEWS)

    assert tier is NewsMaterialityTier.HIGH_SIGNAL, f"{why}: {reasons}"
    assert not reasons[0].startswith("headline_ceiling_applied:"), reasons


def test_the_contract_award_shape_is_why_verbs_were_needed():
    """Regression for the first version of this fix, which required the
    headline to clear a full gate by itself. That rejected this real
    contract award, because the anchor vocabulary holds nouns and this
    headline states its event as a verb. Headlines are written in
    English, not in gate vocabulary."""
    # The headline alone clears no gate — that is the point. It must
    # still not be capped once the body supplies the evidence.
    headline_only_tier, _ = classify_editorial_story(
        "KSAT Selected by Intuitive Machines to Support NASA JPL EAGLE-VSWIR Mission", "", NEWS,
    )
    assert headline_only_tier is NewsMaterialityTier.BACKGROUND

    tier_with_body, reasons = classify_editorial_story(
        "KSAT Selected by Intuitive Machines to Support NASA JPL EAGLE-VSWIR Mission",
        "Intuitive Machines selected KSAT to provide ground segment support, delivering the "
        "spacecraft platform and mission operations under a new contract.",
        NEWS,
    )
    assert tier_with_body is NewsMaterialityTier.HIGH_SIGNAL, reasons
    assert not reasons[0].startswith("headline_ceiling_applied:"), reasons


# --- exemptions --------------------------------------------------------

def test_a_primary_disclosure_is_never_capped_by_its_headline():
    """A filing's headline is a form name. It was never written to
    carry the event, so a headline test says nothing about it, and the
    materiality comes from what the document IS."""
    tier, reasons = classify_editorial_story(
        "Form 8-K", "The Board appointed a new Chief Financial Officer effective immediately.",
        SourceCategory.OFFICIAL_FILING,
    )
    assert tier is NewsMaterialityTier.HIGH_SIGNAL, reasons
    assert "primary_disclosure" in reasons


def test_an_item_with_no_excerpt_is_unaffected():
    """With nothing but a headline there is no excerpt contribution to
    cap, so the ceiling must be a no-op rather than a second filter."""
    headline = "Nvidia Raises GPU Prices by 15% Amid Tariff Pressures"
    assert classify_editorial_story(headline, "", NEWS) == classify_editorial_story(headline, None, NEWS)


def test_the_ceiling_never_raises_a_tier():
    """It can only ever reduce what reaches the top of the feed. A
    Background item stays Background however promising its headline."""
    tier, _ = classify_editorial_story(
        "Acme Corp acquires and expands and signs and orders", "Nothing of substance here.", NEWS,
    )
    assert tier is not NewsMaterialityTier.HIGH_SIGNAL
