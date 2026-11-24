"""Watchlist caps: headlines that are about something, but not about a
completed corporate event.

A tier below the disqualifying formats. Those are not events at all
and go to Background; these ARE about something, so Background would
hide real content — but none states a finished event, so none belongs
at the top of the feed.

Every fixture is a real item from the live High Signal feed, observed
AFTER the headline ceiling, the format block and deduplication had
already run. They survived all three, which is why a narrower rule was
needed: the ceiling asks "is this headline about a corporate event at
all", and these caps ask "has the event actually happened".

Each cap is paired with a negative control, because every one of these
patterns is one careless regex away from suppressing real news."""
from __future__ import annotations

import pytest

from src.data_access.daily_news.materiality_classification import classify_editorial_story
from src.data_access.daily_news.source_registry import SourceCategory
from src.models.daily_news_models import NewsMaterialityTier

NEWS = SourceCategory.INDEPENDENT_NEWS

#: Body text clearing a hard gate, so each test proves the cap beats
#: real evidence rather than agreeing with a weak classification.
HARD_GATE_BODY = (
    "The transaction is valued at $2 billion and would add 40% to HBM capacity across "
    "the AI data center supply chain, according to the filing."
)


@pytest.mark.parametrize("headline,cap", [
    # Deliberative / speculative
    pytest.param("SoftBank's Son Weighed $1 Trillion Acquisition of SK hynix",
                 "deliberative_or_speculative", id="live-softbank-weighed"),
    pytest.param("SK hynix Weighs Kioxia Bond Conversion",
                 "deliberative_or_speculative", id="live-kioxia-weighs"),
    pytest.param("Moore Threads' next 'Lushan' gaming GPU could pack a record 64GB of VRAM",
                 "deliberative_or_speculative", id="live-moore-threads-could"),
    pytest.param("Acme considers a $3 billion fab in Arizona",
                 "deliberative_or_speculative", id="considers"),
    pytest.param("Acme in talks to acquire Beta Corp for $5 billion",
                 "deliberative_or_speculative", id="in-talks"),
    pytest.param("Acme rumored to be planning a 2nm line", "deliberative_or_speculative", id="rumored"),
    # Consumer retail pricing
    pytest.param("Nintendo Switch 2 bundles drop to pre-hike pricing in Black Friday run-up sale",
                 "consumer_retail_pricing", id="live-nintendo-black-friday"),
    pytest.param("Act fast to score Intel's Core i7-14700K for its lowest price ever at $249.99",
                 "consumer_retail_pricing", id="live-intel-retail-deal"),
    pytest.param("The best Cyber Monday SSD deals, ranked", "consumer_retail_pricing", id="cyber-monday"),
    pytest.param("Save $400 on this RTX 5090 workstation", "consumer_retail_pricing", id="save-dollars"),
    # Trade-show demonstration
    pytest.param("Penguin shows off photonic memory box at SC26 that can expand AI accelerator capacity",
                 "trade_show_demo", id="live-penguin-sc26"),
    pytest.param("Acme demonstrates 3nm test chip at its booth", "trade_show_demo", id="booth-demo"),
    pytest.param("Beta Corp teases next-generation accelerator", "trade_show_demo", id="teases"),
    # Vague progress
    pytest.param("Google's Korea Maps push gains pace with investment plans",
                 "vague_progress", id="live-google-korea-maps"),
    pytest.param("Robotics enters its post-Jensen Huang era", "vague_progress", id="live-enters-its-era"),
    pytest.param("Acme's foundry drive gathers momentum", "vague_progress", id="gathers-momentum"),
])
def test_a_capped_shape_lands_on_watchlist_with_the_cap_named(headline, cap):
    tier, reasons = classify_editorial_story(headline, HARD_GATE_BODY, NEWS)

    assert tier is NewsMaterialityTier.WATCHLIST, reasons
    assert reasons[0].startswith(f"watchlist_cap:{cap}:"), reasons


def test_the_body_alone_would_otherwise_clear_a_hard_gate():
    """Proves every fixture above is a real test rather than a
    tautology: the same body under a plain event headline is High."""
    tier, _ = classify_editorial_story("Acme acquires Beta Corp", HARD_GATE_BODY, NEWS)
    assert tier is NewsMaterialityTier.HIGH_SIGNAL


def test_capped_items_stay_on_watchlist_not_background():
    """Unlike an advertorial, a deliberated acquisition is real
    content a reader should monitor. Background would hide it."""
    tier, _ = classify_editorial_story(
        "SK hynix Weighs Kioxia Bond Conversion", HARD_GATE_BODY, NEWS,
    )
    assert tier is NewsMaterialityTier.WATCHLIST


# --- negative controls: one careless regex from suppressing real news --

@pytest.mark.parametrize("headline,why", [
    pytest.param("Nvidia Raises GPU Prices by 15% Amid Tariff Pressures",
                 "a corporate pricing ACTION is not retail-deal content", id="corporate-price-rise"),
    pytest.param("Samsung Orders Additional Protec Bonding Equipment for HBM Expansion",
                 "an equipment order is not a demo", id="equipment-order"),
    pytest.param("Japan's Furukawa Electric Sues LG Chem Over DDF Patent Infringement",
                 "litigation is a completed corporate action", id="lawsuit"),
    pytest.param("Hanwha Tests UGV, Counter-Drone Integration With Romanian Military Network",
                 "a test performed is something done, not considered", id="test-performed"),
    pytest.param("NVIDIA reinstates Jensen Huang after 11-day absence",
                 "a governance change already made", id="governance"),
    pytest.param("TSMC pauses shipments to Chinese designers after U.S. rule change",
                 "the same event without the hedge keeps its tier", id="unhedged-supply-halt"),
])
def test_a_genuine_event_is_not_capped(headline, why):
    tier, reasons = classify_editorial_story(headline, HARD_GATE_BODY, NEWS)
    assert tier is NewsMaterialityTier.HIGH_SIGNAL, f"{why}: {reasons}"
    assert not reasons[0].startswith("watchlist_cap:"), reasons


def test_a_trade_show_demo_with_a_commercial_outcome_keeps_its_tier():
    """Showing a product is not news; selling it is. The exception
    exists because the most material announcements at a conference are
    exactly the ones that come with a customer attached."""
    tier, reasons = classify_editorial_story(
        "Samsung shows off HBM4 at CES and signs supply contract with three customers",
        HARD_GATE_BODY, NEWS,
    )
    assert tier is NewsMaterialityTier.HIGH_SIGNAL, reasons


@pytest.mark.parametrize("headline", [
    "Acme completes sale of its display unit to Beta Corp",
    "Acme announces sale and leaseback of its Austin campus",
])
def test_the_word_sale_in_a_transaction_is_not_a_retail_sale(headline):
    """"sale" is in the retail vocabulary and is also how a divestment
    is described. The pattern excludes "sale of" and "sale and" for
    exactly this reason."""
    tier, reasons = classify_editorial_story(headline, HARD_GATE_BODY, NEWS)
    assert tier is NewsMaterialityTier.HIGH_SIGNAL, reasons
