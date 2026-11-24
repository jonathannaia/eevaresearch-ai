"""Cross-publisher same-story detection (cross_publisher_dedup.py).

The live feed on 2026-11-23 showed these as two separate High Signals,
because the editorial duplicate key included the publisher:

    "Reports: Russia may launch nuclear weapon into orbit as soon as
     next year"
    "Russia may launch a nuclear-armed satellite far sooner than we
     thought"

They share three words. Ordinary whole-text similarity puts them near
0.2, below any threshold that would not also merge unrelated stories.
The signal is not how much the headlines overlap — it is WHICH words
they share, which is why similarity runs over rare tokens only and
drops this product's own taxonomy vocabulary.

The separation test below is the one that matters. It asserts the
measured gap between real non-duplicates and real duplicates from that
day, so the threshold can never be quietly tuned onto a knife edge."""
from __future__ import annotations

import pytest

from src.data_access.daily_news.cross_publisher_dedup import (
    MIN_RARE_TOKEN_SIMILARITY,
    find_cross_publisher_duplicate,
    rare_token_similarity,
    rare_tokens,
)

T0 = "2026-11-22T10:00:00+00:00"
T4H = "2026-11-22T14:00:00+00:00"
T8D = "2026-11-30T10:00:00+00:00"

RUSSIA_A = "Russia may launch a nuclear-armed satellite far sooner than we thought"
RUSSIA_B = "Reports: Russia may launch nuclear weapon into orbit as soon as next year"

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
    assert find_cross_publisher_duplicate(
        [("The Register", left, T0)], publisher="Ars Technica", headline=right, published_at=T4H,
    ) is not None


@pytest.mark.parametrize("left,right", NON_DUPLICATES)
def test_two_genuinely_different_stories_are_both_kept(left, right):
    """Suppressing real coverage is a far worse failure than showing a
    visible duplicate, so this direction matters more than the one
    above."""
    assert find_cross_publisher_duplicate(
        [("The Register", left, T0)], publisher="Ars Technica", headline=right, published_at=T4H,
    ) is None


# --- every structural gate must hold independently --------------------

def test_the_same_publisher_is_never_matched_here():
    """Same-publisher duplicates are caught exactly, upstream. Matching
    them again here would double-count and could suppress a publisher's
    legitimate follow-up."""
    assert find_cross_publisher_duplicate(
        [("The Register", RUSSIA_A, T0)], publisher="The Register", headline=RUSSIA_B, published_at=T4H,
    ) is None


def test_items_far_apart_in_time_are_not_duplicates():
    """A story genuinely re-reported a week later is a follow-up."""
    assert find_cross_publisher_duplicate(
        [("The Register", RUSSIA_A, T0)], publisher="Ars Technica", headline=RUSSIA_B, published_at=T8D,
    ) is None


@pytest.mark.parametrize("bad_time", ["", "not-a-date", "2026-11-22T10:00:00"])
def test_an_unusable_timestamp_fails_the_gate_rather_than_passing_it(bad_time):
    """An unknown interval is not evidence of sameness. The last case
    is naive-vs-aware, which raises on comparison."""
    assert find_cross_publisher_duplicate(
        [("The Register", RUSSIA_A, bad_time)], publisher="Ars Technica", headline=RUSSIA_B, published_at=T4H,
    ) is None


@pytest.mark.parametrize("headline", ["", "Chips", "AI chip demand", "HBM DRAM NAND"])
def test_a_headline_with_too_little_distinctive_content_never_matches(headline):
    """Two three-word headlines sharing two words score 0.5 while
    saying almost nothing. The last case is all taxonomy vocabulary,
    which carries no information in this feed at all."""
    assert find_cross_publisher_duplicate(
        [("The Register", "AI chip demand surges", T0)],
        publisher="Ars Technica", headline=headline, published_at=T4H,
    ) is None


# --- why taxonomy words are dropped -----------------------------------

def test_taxonomy_vocabulary_is_not_evidence_of_sameness():
    """Two different Samsung memory stories both say "memory", "HBM"
    and "chips", because that is what this entire feed is about.
    Counting those as similarity would merge the feed into itself."""
    tokens = rare_tokens("Samsung expands HBM DRAM memory chip capacity at its foundry")
    for taxonomy_word in ("memory", "foundry", "dram"):
        assert taxonomy_word not in tokens, tokens
    assert "samsung" in tokens, "a company name is exactly the kind of distinctive token to keep"


def test_commentary_verbs_are_not_evidence_of_sameness():
    """"far sooner than we thought" is editorial framing, not subject
    matter. Leaving "thought" in put the real Russia pair at 0.33,
    below the threshold, purely on a filler word."""
    assert "thought" not in rare_tokens("far sooner than we thought")
    assert rare_token_similarity(RUSSIA_A, RUSSIA_B) >= MIN_RARE_TOKEN_SIMILARITY


def test_the_first_seen_story_wins():
    """Matching this package's existing convention: nothing is merged,
    and no corroborating source is attached to the survivor."""
    match = find_cross_publisher_duplicate(
        [("The Register", RUSSIA_A, T0)], publisher="Ars Technica", headline=RUSSIA_B, published_at=T4H,
    )
    assert match is not None
    matched_publisher, matched_headline, similarity = match
    assert (matched_publisher, matched_headline) == ("The Register", RUSSIA_A)
    assert similarity >= MIN_RARE_TOKEN_SIMILARITY
