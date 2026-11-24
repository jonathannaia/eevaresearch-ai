"""Advertorials and newsletter roundups can never be High Signal.

Both fixtures below are real items from the live High Signals feed on
2026-11-23. They are not weak signals — they are not corporate events
at all, and no amount of gate evidence in the body changes that.

WHY THIS IS A HARD SHORT-CIRCUIT. The existing low-signal-format
machinery demotes only on the weak path, so a hard gate still wins —
deliberately and correctly, for a genuine disclosure that happens to
be announced at a conference. But "This week on Tom's Hardware
Premium: 21 November 2026" reached High Signal through
`quantified_change:capacity`, which IS a hard gate. A weak-path
demotion could never have caught it.

These formats are reliably full of taxonomy phrases and numbers,
because that is what the publication is about. The evidence a gate
looks for is exactly what they contain. The gates cannot distinguish
them and should not be asked to."""
from __future__ import annotations

import pytest

from src.data_access.daily_news.materiality_classification import classify_editorial_story
from src.data_access.daily_news.source_registry import SourceCategory
from src.models.daily_news_models import NewsMaterialityTier

NEWS = SourceCategory.INDEPENDENT_NEWS

#: Body text that independently clears a HARD gate (quantified change
#: plus a taxonomy phrase). Every blocked fixture is paired with this,
#: so each test proves the block beats real gate evidence rather than
#: merely agreeing with a weak classification.
HARD_GATE_BODY = (
    "AI data center capacity rose 40% this quarter, with a $2 billion investment in HBM "
    "supply and 50 megawatts of new GPU cluster deployment."
)


@pytest.mark.parametrize("headline,fmt", [
    pytest.param("Sponsored: Solving cabling challenges behind AI data center growth",
                 "sponsored_content", id="live-sponsored-post"),
    pytest.param("This week on Tom's Hardware Premium: 21 November 2026",
                 "newsletter_roundup", id="live-newsletter-roundup"),
    pytest.param("[Sponsored] How to scale your HBM supply chain", "sponsored_content", id="bracketed-label"),
    pytest.param("(Ad) The new standard in liquid cooling", "sponsored_content", id="parenthesised-ad"),
    pytest.param("Advertorial: why memory pricing is changing", "sponsored_content", id="advertorial"),
    pytest.param("Paid post: inside the AI buildout", "sponsored_content", id="paid-post"),
    pytest.param("Partner content: securing the supply chain", "sponsored_content", id="partner-content"),
    pytest.param("Weekly roundup: the chips that mattered", "newsletter_roundup", id="weekly-roundup"),
    pytest.param("This week in semiconductors", "newsletter_roundup", id="this-week-in"),
    pytest.param("The week in AI infrastructure", "newsletter_roundup", id="the-week-in"),
    pytest.param("Daily briefing: memory prices climb", "newsletter_roundup", id="daily-briefing"),
    pytest.param("Our newsletter covers the HBM shortage", "newsletter_roundup", id="newsletter"),
])
def test_a_disqualifying_format_is_background_even_with_hard_gate_evidence(headline, fmt):
    tier, reasons = classify_editorial_story(headline, HARD_GATE_BODY, NEWS)

    assert tier is NewsMaterialityTier.BACKGROUND, reasons
    assert reasons == (f"disqualifying_format:{fmt}",), reasons


def test_the_body_alone_would_otherwise_clear_a_hard_gate():
    """Proves the fixtures above are a real test and not a tautology:
    the same body under an ordinary headline reaches High Signal."""
    tier, _ = classify_editorial_story(
        "Samsung Expands HBM Capacity at Pyeongtaek", HARD_GATE_BODY, NEWS,
    )
    assert tier is NewsMaterialityTier.HIGH_SIGNAL


# --- negative controls: the word is not the format --------------------

@pytest.mark.parametrize("headline", [
    pytest.param("Company X sponsored research shows HBM demand rising 40%", id="sponsored-mid-sentence"),
    pytest.param("Nvidia promoted three executives to lead its memory unit", id="promoted-as-a-verb"),
    pytest.param("Samsung Orders Additional Protec Bonding Equipment for HBM Expansion", id="ordinary-news"),
    pytest.param("TSMC reportedly pauses shipments to Chinese designers", id="ordinary-news-2"),
])
def test_an_ordinary_headline_containing_one_of_these_words_is_not_blocked(headline):
    """An unanchored \\bsponsored\\b would catch real news about a real
    company action. Every pattern is anchored to how publishers
    actually mark advertising: at the start, or in a bracketed label."""
    _, reasons = classify_editorial_story(headline, HARD_GATE_BODY, NEWS)
    assert not reasons[0].startswith("disqualifying_format:"), reasons


def test_the_block_reads_the_headline_not_the_body():
    """A story that merely mentions a newsletter is not a newsletter."""
    tier, reasons = classify_editorial_story(
        "Samsung Expands HBM Capacity at Pyeongtaek",
        "Details appeared first in the company's weekly newsletter. " + HARD_GATE_BODY,
        NEWS,
    )
    assert tier is NewsMaterialityTier.HIGH_SIGNAL, reasons


def test_blocked_items_land_on_background_not_watchlist():
    """Watchlist is a list a reader is expected to monitor. An
    advertisement is not an early or unquantified signal — it is not a
    signal. The item stays stored and retrievable either way."""
    tier, _ = classify_editorial_story(
        "Sponsored: Solving cabling challenges behind AI data center growth", HARD_GATE_BODY, NEWS,
    )
    assert tier is NewsMaterialityTier.BACKGROUND
