"""Same-story detection (same_story_dedup.py).

The live feed on 2026-11-23 showed these as two separate High Signals,
because the editorial duplicate key is an EXACT title match:

    "Reports: Russia may launch nuclear weapon into orbit as soon as
     next month"
    "Russia may launch a nuclear-armed satellite far sooner than we
     thought"

They share three words. Ordinary whole-text similarity puts them near
0.2, below any threshold that would not also merge unrelated stories.
The signal is not how much the headlines overlap — it is WHICH words
they share, which is why similarity runs over rare tokens only and
drops this product's own taxonomy vocabulary.

PUBLISHER IS NOT A GATE. This module first required the two items to
come from different publishers. Both Russia items are from Ars
Technica, minutes apart — the gate would have excluded the only true
duplicate in the window. test_the_same_publisher_is_matched_too below
is the regression for that.

The separation test is the one that matters most. It asserts the
measured gap between real non-duplicates and real duplicates from that
day, so the threshold can never be quietly tuned onto a knife edge."""
from __future__ import annotations

import pytest

from src.data_access.daily_news.same_story_dedup import (
    MIN_RARE_TOKEN_SIMILARITY,
    find_same_story_duplicate,
    rare_token_similarity,
    rare_tokens,
)

T0 = "2026-11-22T10:00:00+00:00"
T4H = "2026-11-22T14:00:00+00:00"
T8D = "2026-11-30T10:00:00+00:00"

RUSSIA_A = "Russia may launch a nuclear-armed satellite far sooner than we thought"
RUSSIA_B = "Reports: Russia may launch nuclear weapon into orbit as soon as next month"

#: Real pairs from the live feed, with what they are.
DUPLICATES = [
    pytest.param(RUSSIA_A, RUSSIA_B, id="live-russia-satellite-pair"),
    pytest.param("SoftBank's Son Weighed $1 Trillion Acquisition of SK hynix",
                 "SoftBank Son weighed a $1 trillion bid for SK hynix, sources say", id="softbank-sk-hynix"),
]
NON_DUPLICATES = [
    pytest.param("Samsung Bets on V9 Chips Amid NAND Price Slump",
                 "Samsung Orders Additional Protec Bonding Equipment for HBM Expansion", id="two-samsung-stories"),
    pytest.param("Samsung Retains Dual-CEO Structure, Promoting Three Semiconductor Executives",
                 "Samsung names new memory, mobile chiefs as chips surge", id="samsung-leadership-pair"),
    pytest.param("NVIDIA reinstates Jensen Huang after 11-day absence",
                 "NVIDIA SEC Probe Puts Quality of $115.8 Billion Revenue Under Scrutiny", id="two-nvidia-stories"),
    pytest.param("TSMC reportedly pauses shipments to Chinese designers after U.S. rule change",
                 "Blacklisted Chinese Universities Buy Nvidia AI Chips", id="two-export-control-stories"),
    pytest.param("US let Nvidia and AMD sell bucketloads of chips to military-linked Chinese firms",
                 "U.S. Tightens Controls on Chinese Access to AI Chips", id="opposite-export-stories"),
]


# --- the property that keeps the threshold honest ---------------------

def test_duplicates_and_non_duplicates_are_separated_by_a_wide_margin():
    """Not a threshold test — a SEPARATION test. If a later change
    narrows the gap between real duplicates and real non-duplicates,
    this fails even when every individual case still happens to land
    on the right side."""
    dupes = [rare_token_similarity(a, b) for a, b in [(p.values[0], p.values[1]) for p in DUPLICATES]]
    others = [rare_token_similarity(a, b) for a, b in [(p.values[0], p.values[1]) for p in NON_DUPLICATES]]

    assert min(dupes) >= MIN_RARE_TOKEN_SIMILARITY, dupes
    assert max(others) < MIN_RARE_TOKEN_SIMILARITY, others
    assert min(dupes) - max(others) >= 0.20, (
        f"the margin has narrowed to {min(dupes) - max(others):.2f}; duplicates={dupes} others={others}"
    )


@pytest.mark.parametrize("left,right", DUPLICATES)
def test_a_real_duplicate_pair_is_detected(left, right):
    assert find_same_story_duplicate(
        [("s1", "The Register", left, T0)], headline=right, published_at=T4H,
    ) is not None


@pytest.mark.parametrize("left,right", NON_DUPLICATES)
def test_two_genuinely_different_stories_are_both_kept(left, right):
    """Suppressing real coverage is a far worse failure than showing a
    visible duplicate, so this direction matters more than the one
    above."""
    assert find_same_story_duplicate(
        [("s1", "The Register", left, T0)], headline=right, published_at=T4H,
    ) is None


# --- every structural gate must hold independently --------------------

def test_the_same_publisher_is_matched_too():
    """THE CORRECTION THIS MODULE NEEDED. It first required different
    publishers, reasoning that same-publisher duplicates were already
    caught upstream and a publisher's own second piece is a follow-up.

    Both live Russia items are from Ars Technica, published minutes
    apart. The exact-title check upstream could not catch them because
    the wordings differ, and a different-publisher gate would have
    excluded the only true duplicate in the window. Checking the real
    data is what found this; the reasoning alone sounded fine."""
    assert find_same_story_duplicate(
        [("s1", "Ars Technica", RUSSIA_A, T0)], headline=RUSSIA_B, published_at=T4H,
    ) is not None


def test_items_far_apart_in_time_are_not_duplicates():
    """A story genuinely re-reported a week later is a follow-up."""
    assert find_same_story_duplicate(
        [("s1", "The Register", RUSSIA_A, T0)], headline=RUSSIA_B, published_at=T8D,
    ) is None


@pytest.mark.parametrize("bad_time", ["", "not-a-date", "2026-11-22T10:00:00"])
def test_an_unusable_timestamp_fails_the_gate_rather_than_passing_it(bad_time):
    """An unknown interval is not evidence of sameness. The last case
    is naive-vs-aware, which raises on comparison."""
    assert find_same_story_duplicate(
        [("s1", "The Register", RUSSIA_A, bad_time)], headline=RUSSIA_B, published_at=T4H,
    ) is None


@pytest.mark.parametrize("headline", ["", "Chips", "AI chip demand", "HBM DRAM NAND"])
def test_a_headline_with_too_little_distinctive_content_never_matches(headline):
    """Two three-word headlines sharing two words score 0.5 while
    saying almost nothing. The last case is all taxonomy vocabulary,
    which carries no information in this feed at all."""
    assert find_same_story_duplicate(
        [("s1", "The Register", "AI chip demand surges", T0)], headline=headline, published_at=T4H,
    ) is None


# --- why taxonomy words are dropped -----------------------------------

def test_taxonomy_vocabulary_is_not_evidence_of_sameness():
    """Two different Samsung memory stories both say "memory", "HBM"
    and "chips", because that is what this entire feed is about.
    Counting those as similarity would merge the feed into itself."""
    tokens = rare_tokens("Samsung expands HBM DRAM memory chip capacity at its foundry")
    for taxonomy_word in ("memory", "foundry", "dram"):
        assert taxonomy_word not in tokens, tokens


def test_a_company_name_is_not_evidence_of_sameness_either():
    """A LATER CORRECTION, and the opposite of what I first assumed.

    A company name looks like the most distinctive token in a
    headline, so it was deliberately kept. On a SHORT headline that is
    exactly wrong: two unrelated Oracle stories share "oracle" and
    "corporation", which can be half the distinctive content, and they
    scored 0.40 — above the threshold. The company is what both
    stories are ABOUT; what distinguishes them is everything else.

    Found by a test fixture collapsing six synthetic same-company
    headlines into one, not by reasoning about it."""
    tokens = rare_tokens("Samsung Electronics advances its Pyeongtaek foundry")
    assert "samsung" not in tokens and "electronics" not in tokens, tokens
    assert "pyeongtaek" in tokens, "the distinguishing word must survive"

    assert rare_token_similarity(
        "Oracle Corporation reports on its cloud backlog",
        "Oracle Corporation reports on its database licensing",
    ) < MIN_RARE_TOKEN_SIMILARITY


def test_an_identical_headline_is_a_duplicate_however_short_it_is():
    """Route 0. Once the company name is removed, "Oracle Corporation
    posts AI cloud growth" has two distinctive words — below the floor
    every similarity route requires. Two identical copies of it are
    still one story, so the exact-headline route carries no floor at
    all. Upstream catches this only when the publisher also matches, so
    a syndicated wire story reaches here untouched."""
    match = find_same_story_duplicate(
        [("s1", "CNBC", "Oracle Corporation posts AI cloud growth", T0)],
        headline="Oracle Corporation posts AI cloud growth!", published_at=T4H,
    )
    assert match is not None
    assert match[0] == "s1"
    assert match[3] == 1.0


def test_containment_catches_a_longer_retelling_of_a_shorter_headline():
    """Route 2. A ratio punishes this shape for the longer headline
    merely saying more; the shorter side's distinctive words appear
    wholly inside the longer one. Verified against all 595 pairs of
    the live window: this route adds the detection below and not one
    false positive."""
    match = find_same_story_duplicate(
        [("s1", "Business Korea", "SoftBank's Son Weighed $1 Trillion Acquisition of SK hynix", T0)],
        headline="SoftBank Son weighed a $1 trillion bid for SK hynix, sources say", published_at=T4H,
    )
    assert match is not None


def test_commentary_verbs_are_not_evidence_of_sameness():
    """"far sooner than we thought" is editorial framing, not subject
    matter. Leaving "thought" in put the real Russia pair at 0.33,
    below the threshold, purely on a filler word."""
    assert "thought" not in rare_tokens("far sooner than we thought")
    assert rare_token_similarity(RUSSIA_A, RUSSIA_B) >= MIN_RARE_TOKEN_SIMILARITY


def test_the_first_seen_story_wins():
    """Matching this package's existing convention: nothing is merged,
    and no corroborating source is attached to the survivor."""
    match = find_same_story_duplicate(
        [("s1", "The Register", RUSSIA_A, T0)], headline=RUSSIA_B, published_at=T4H,
    )
    assert match is not None
    matched_id, matched_publisher, matched_headline, similarity = match
    assert (matched_publisher, matched_headline) == ("The Register", RUSSIA_A)
    assert similarity >= MIN_RARE_TOKEN_SIMILARITY


# ============================================================
# Route 3 — same company, same event category, same window.
# Exists for the pairs whose headlines carry no shared evidence:
# measured on the live window, "Nvidia reinstates Jensen Huang as CEO"
# and "Huang Returns as Nvidia CEO 11 Days After Resigning" score 0.12,
# because "reinstates" and "returns as" are the same event in different
# words and this module does no stemming.
# ============================================================

from src.data_access.daily_news import same_story_dedup  # noqa: E402

_HUANG_EXISTING = [(
    "s-survivor",
    "Light Reading",
    "Nvidia reinstates Jensen Huang as CEO, board director",
    "2026-11-23T17:33:36+00:00",
    ("NVIDIA",),
    "governance_change",
)]


def test_route_3_catches_the_pair_the_headline_routes_cannot():
    """The real production pair. Route 1 scores it 0.12 — recorded here
    so the two routes' division of labour stays visible."""
    assert same_story_dedup.rare_token_similarity(
        "Nvidia reinstates Jensen Huang as CEO, board director",
        "Huang Returns as Nvidia CEO 11 Days After Resigning",
    ) < same_story_dedup.MIN_RARE_TOKEN_SIMILARITY

    match = same_story_dedup.find_same_event_duplicate(
        _HUANG_EXISTING,
        headline="Huang Returns as Nvidia CEO 11 Days After Resigning",
        published_at="2026-11-24T01:46:54+00:00",
        companies=("NVIDIA",),
        category="governance_change",
    )
    assert match is not None
    assert match[0] == "s-survivor"
    assert match[3] == "governance_change"


def test_route_3_requires_a_shared_company():
    assert same_story_dedup.find_same_event_duplicate(
        _HUANG_EXISTING,
        headline="Samsung names new memory, mobile chiefs",
        published_at="2026-11-24T01:46:54+00:00",
        companies=("Samsung Electronics",),
        category="governance_change",
    ) is None


def test_route_3_never_fires_without_an_attributed_company():
    """An unattributed story carries no evidence of which event it
    describes; guessing would collapse unrelated items."""
    assert same_story_dedup.find_same_event_duplicate(
        _HUANG_EXISTING,
        headline="Robotics enters its post-Jensen Huang era",
        published_at="2026-11-24T01:46:54+00:00",
        companies=(),
        category="governance_change",
    ) is None


def test_route_3_is_an_allow_list_and_earnings_is_not_on_it():
    """Two earnings stories about one company hours apart are routinely
    two separate, separately material disclosures — "Q3 results" and
    "raises full-year guidance". Earnings is deliberately absent."""
    assert "governance_change" in same_story_dedup.SAME_EVENT_CATEGORIES
    assert "formal_earnings_materials" not in same_story_dedup.SAME_EVENT_CATEGORIES
    assert same_story_dedup.event_category(("formal_earnings_materials:revenue",)) is None
    assert same_story_dedup.find_same_event_duplicate(
        [("s-earn", "Reuters", "Nvidia posts Q3 results", "2026-11-23T17:00:00+00:00",
          ("NVIDIA",), "formal_earnings_materials")],
        headline="Nvidia raises full-year guidance",
        published_at="2026-11-23T21:00:00+00:00",
        companies=("NVIDIA",),
        category="formal_earnings_materials",
    ) is None


def test_route_3_respects_the_48_hour_window():
    assert same_story_dedup.find_same_event_duplicate(
        _HUANG_EXISTING,
        headline="Huang Returns as Nvidia CEO",
        published_at="2026-11-27T01:46:54+00:00",
        companies=("NVIDIA",),
        category="governance_change",
    ) is None


def test_event_category_reads_the_head_of_a_materiality_reason():
    assert same_story_dedup.event_category(
        ("governance_change:ceo:names", "quantified_change:capacity")
    ) == "governance_change"
    assert same_story_dedup.event_category(()) is None
    assert same_story_dedup.event_category(("off_taxonomy_no_anchor",)) is None
