"""Window re-classification planning (editorial_reclassification.py).

Pure-function tests, no I/O. The fixtures are the production rows that
motivated the pass: stories whose stored classification a shipped fix
had already superseded, and the eight-copy Nvidia governance cluster
that proves dedupe has to run inside the pass rather than beside it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.data_access.daily_news.editorial_reclassification import (
    COMPLETE_EXCERPT_CHARS,
    RECLASSIFY_WINDOW_DAYS,
    SUPPRESSED_TIER,
    TRUNCATED_EXCERPT_CHARS,
    plan_reclassification,
    stories_in_window,
)
from src.models.daily_news_models import EditorialStory, NewsMaterialityTier

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


def _story(
    story_id: str, headline: str, *, hours_ago: int = 1, excerpt: str = "",
    tier: NewsMaterialityTier | None = NewsMaterialityTier.HIGH_SIGNAL,
    companies: tuple[str, ...] = (), publisher: str = "Example Wire",
    reasons: tuple[str, ...] = (),
) -> EditorialStory:
    published = (NOW - timedelta(hours=hours_ago)).isoformat()
    return EditorialStory(
        id=story_id, headline=headline, publisher=publisher,
        source_url=f"https://example.test/{story_id}", published_at=published,
        retrieved_at=published, excerpt=excerpt, matched_companies=companies,
        matched_themes=(), source_feed_id="example", materiality_tier=tier,
        materiality_reasons=reasons, identified_companies=companies,
    )


# --- window -------------------------------------------------------


def test_stories_outside_the_window_are_never_touched():
    """Outside the display window a story is no longer shown, and its
    original classification is the only honest record of what was
    published."""
    old = _story("old", "Something that happened", hours_ago=24 * (RECLASSIFY_WINDOW_DAYS + 1))
    assert stories_in_window([old], NOW) == []
    assert plan_reclassification([old], NOW) == ()


def test_a_story_with_an_unparseable_date_is_skipped_not_guessed():
    broken = _story("broken", "Undated story")
    broken = EditorialStory(**{**broken.__dict__, "published_at": "not a date"})
    assert plan_reclassification([broken], NOW) == ()


# --- corrections --------------------------------------------------


def test_high_signal_with_no_tracked_company_is_capped():
    """The production shape behind the item-3 review: a High Signal
    with nothing attached, stored before the tracked-company
    requirement shipped and frozen there ever since."""
    story = _story(
        "nintendo",
        "Nintendo Switch 2 bundles drop to pre-hike pricing in Black Friday run-up sale",
        excerpt="Nintendo raised the price of the Switch 2 earlier this year in response to "
                "rising NAND and DRAM prices, but on Black Friday you can wipe out that hike "
                "with a variety of bundles available on Amazon.",
        tier=NewsMaterialityTier.HIGH_SIGNAL,
    )
    changes = plan_reclassification([story], NOW)
    assert len(changes) == 1
    assert changes[0].new_tier is not NewsMaterialityTier.HIGH_SIGNAL
    assert changes[0].tier_changed


def test_an_unchanged_story_produces_no_revision_record():
    """The revision table is a log of corrections, not a second copy of
    the feed."""
    story = _story(
        "ok", "Samsung Electronics raises Xian NAND capacity by 40%",
        excerpt="Samsung Electronics announced it will raise Xian NAND output capacity by "
                "40% over two years.",
        tier=NewsMaterialityTier.HIGH_SIGNAL, companies=("Samsung Electronics",),
    )
    changes = plan_reclassification([story], NOW)
    assert [c for c in changes if c.story_id == "ok"] == []


def test_a_change_carries_everything_the_archive_needs():
    story = _story(
        "exportctl", "U.S. Tightens Controls on Chinese Access to AI Chips",
        excerpt="Regulators said the rule covers NVIDIA accelerators as well as equipment "
                "from Applied Materials, Inc.",
        tier=NewsMaterialityTier.HIGH_SIGNAL, companies=("NVIDIA",),
    )
    change = plan_reclassification([story], NOW)[0]
    assert change.old_companies == ("NVIDIA",)
    assert change.new_companies == ()
    assert change.old_tier is NewsMaterialityTier.HIGH_SIGNAL
    assert change.reason


# --- dedupe inside the pass ---------------------------------------


_HUANG_EXCERPT = (
    "Nvidia said its board has reinstated Jensen Huang as chief executive officer "
    "following a unanimous vote."
)


def test_duplicate_cluster_is_suppressed_rather_than_all_promoted():
    """The measured risk this pass was designed around: without dedupe
    inside the pass, the governance gate promotes every copy of one
    event at once. First seen by publication wins; the rest are
    suppressed, not deleted."""
    cluster = [
        _story("d1", "Nvidia board reinstates Jensen Huang as CEO", hours_ago=40,
               excerpt=_HUANG_EXCERPT, publisher="Data Center Dynamics",
               tier=NewsMaterialityTier.BACKGROUND, companies=("NVIDIA",)),
        _story("d2", "Nvidia reinstates Jensen Huang as CEO, board director", hours_ago=38,
               excerpt=_HUANG_EXCERPT, publisher="Light Reading",
               tier=NewsMaterialityTier.WATCHLIST, companies=("NVIDIA",)),
        _story("d3", "Jensen Huang reinstated as Nvidia CEO, board chair", hours_ago=36,
               excerpt=_HUANG_EXCERPT, publisher="The Korea Times",
               tier=NewsMaterialityTier.WATCHLIST, companies=("NVIDIA",)),
    ]
    changes = plan_reclassification(cluster, NOW)
    by_id = {c.story_id: c for c in changes}

    # The earliest is kept and judged on its own merits.
    assert "d1" not in by_id or by_id["d1"].new_tier is not SUPPRESSED_TIER
    # The later copies are suppressed, and say what they duplicated.
    for story_id in ("d2", "d3"):
        assert by_id[story_id].new_tier is SUPPRESSED_TIER
        assert "duplicate_of" in by_id[story_id].reason


def test_same_event_route_catches_the_differently_worded_copy():
    """"reinstates" and "returns as" share no distinctive words. Route 3
    catches this pair on company plus event category; Route 1 cannot."""
    cluster = [
        _story("first", "Nvidia reinstates Jensen Huang as CEO", hours_ago=30,
               excerpt=_HUANG_EXCERPT, publisher="Light Reading",
               tier=NewsMaterialityTier.WATCHLIST, companies=("NVIDIA",)),
        _story("second", "Huang Returns as Nvidia CEO 11 Days After Resigning", hours_ago=20,
               excerpt=_HUANG_EXCERPT, publisher="Business Korea",
               tier=NewsMaterialityTier.HIGH_SIGNAL, companies=("NVIDIA",)),
    ]
    by_id = {c.story_id: c for c in plan_reclassification(cluster, NOW)}
    assert by_id["second"].new_tier is SUPPRESSED_TIER
    assert by_id["second"].reason.startswith("same_event_duplicate_of:")


def test_an_already_suppressed_duplicate_is_not_rewritten_again():
    """Re-running the pass must be a no-op once it has run. A story
    already at the suppressed tier produces no further record."""
    cluster = [
        _story("first", "Nvidia reinstates Jensen Huang as CEO", hours_ago=30,
               excerpt=_HUANG_EXCERPT, companies=("NVIDIA",),
               tier=NewsMaterialityTier.WATCHLIST, publisher="Light Reading"),
        _story("second", "Huang Returns as Nvidia CEO 11 Days After Resigning", hours_ago=20,
               excerpt=_HUANG_EXCERPT, companies=("NVIDIA",),
               tier=SUPPRESSED_TIER, publisher="Business Korea"),
    ]
    assert [c.story_id for c in plan_reclassification(cluster, NOW)] != ["second"]


# --- truncated source text ----------------------------------------


def test_truncation_threshold_matches_the_pipelines_own_cap():
    """These two constants are one fact. If the pipeline ever stores
    longer excerpts, this pass must learn about it here rather than
    silently start re-classifying truncated text."""
    from src.data_access.daily_news import editorial_pipeline

    assert TRUNCATED_EXCERPT_CHARS == editorial_pipeline._MAX_EXCERPT_CHARS


def test_a_story_with_a_truncated_excerpt_is_left_alone():
    """The pass sees the stored excerpt, not the original feed summary.
    At the cap, the text was truncated, so any tier computed from it
    would be a guess — and a silently wrong demotion is exactly what
    the append-only record exists to prevent."""
    story = _story(
        "truncated",
        "Nintendo Switch 2 bundles drop to pre-hike pricing in Black Friday run-up sale",
        excerpt="x" * TRUNCATED_EXCERPT_CHARS,
        tier=NewsMaterialityTier.HIGH_SIGNAL,
    )
    assert plan_reclassification([story], NOW) == ()


def test_a_truncated_story_can_still_be_suppressed_as_a_duplicate():
    """Duplicate detection compares headlines, companies and times,
    never the excerpt, so truncation cannot affect it."""
    cluster = [
        _story("first", "Nvidia reinstates Jensen Huang as CEO", hours_ago=30,
               excerpt=_HUANG_EXCERPT, companies=("NVIDIA",),
               tier=NewsMaterialityTier.WATCHLIST, publisher="Light Reading"),
        _story("long", "Nvidia reinstates Jensen Huang as CEO, board director", hours_ago=20,
               excerpt=_HUANG_EXCERPT + "y" * TRUNCATED_EXCERPT_CHARS,
               companies=("NVIDIA",), tier=NewsMaterialityTier.HIGH_SIGNAL,
               publisher="Data Center Dynamics"),
    ]
    by_id = {c.story_id: c for c in plan_reclassification(cluster, NOW)}
    assert by_id["long"].new_tier is SUPPRESSED_TIER


def test_a_promotion_is_applied_even_on_long_possibly_truncated_text():
    """Direction matters. The pass sees at most the text ingest saw, so
    a gate firing on a truncated excerpt would have fired on the full
    summary too. Less evidence reaching the same conclusion is stronger
    evidence, not weaker."""
    story = _story(
        "promote", "ASML names Christophe Fouquet as CEO",
        excerpt="ASML Holding said Christophe Fouquet will take over as chief executive "
                "officer. " + ("Context follows. " * 30),
        tier=NewsMaterialityTier.BACKGROUND, companies=(),
    )
    assert len(story.excerpt) > COMPLETE_EXCERPT_CHARS
    changes = plan_reclassification([story], NOW)
    assert len(changes) == 1
    # Gate G fires; the tracked-company requirement then caps it to
    # Watchlist, since no company is attributed. Still a promotion from
    # Background, which is the direction this test is about.
    assert changes[0].new_tier is NewsMaterialityTier.WATCHLIST
    assert changes[0].old_tier is NewsMaterialityTier.BACKGROUND
    assert changes[0].new_reasons[-1] == "governance_change:ceo:names"


def test_a_demotion_on_long_text_is_declined_not_guessed():
    """The honest floor: the story keeps its original classification and
    ages out of the window, exactly as it would have before this pass
    existed."""
    story = _story(
        "demote", "Nintendo Switch 2 bundles drop to pre-hike pricing in Black Friday sale",
        excerpt="Nintendo raised the price of the Switch 2 earlier this year. " + ("Filler text. " * 30),
        tier=NewsMaterialityTier.HIGH_SIGNAL,
    )
    assert len(story.excerpt) > COMPLETE_EXCERPT_CHARS
    assert plan_reclassification([story], NOW) == ()


# --- related coverage ---------------------------------------------


def test_a_suppressed_duplicate_is_linked_to_its_survivor():
    """"One event = one card" — but the other reports are kept and
    linked, not discarded. A market reaction and a political reaction
    to the same event are real coverage; six separate cards was the
    only thing wrong with them."""
    cluster = [
        _story("survivor", "Nvidia reinstates Jensen Huang as CEO", hours_ago=30,
               excerpt=_HUANG_EXCERPT, companies=("NVIDIA",),
               tier=NewsMaterialityTier.WATCHLIST, publisher="Light Reading"),
        _story("reaction", "Huang Returns as Nvidia CEO 11 Days After Resigning",
               hours_ago=20, excerpt=_HUANG_EXCERPT, companies=("NVIDIA",),
               tier=NewsMaterialityTier.WATCHLIST, publisher="Business Korea"),
    ]
    by_id = {c.story_id: c for c in plan_reclassification(cluster, NOW)}
    assert by_id["reaction"].duplicate_of_story_id == "survivor"
    assert by_id["reaction"].new_tier is SUPPRESSED_TIER
    # The survivor is never linked to anything, so the chain is one deep.
    assert "survivor" not in by_id or by_id["survivor"].duplicate_of_story_id is None


def test_a_non_duplicate_change_never_touches_the_link():
    story = _story(
        "capped",
        "Nintendo Switch 2 bundles drop to pre-hike pricing in Black Friday run-up sale",
        excerpt="Nintendo raised the price of the Switch 2 earlier this year in response to "
                "rising NAND and DRAM prices.",
        tier=NewsMaterialityTier.HIGH_SIGNAL,
    )
    change = plan_reclassification([story], NOW)[0]
    assert change.duplicate_of_story_id is None


def test_rerunning_the_pass_does_not_relink_an_already_linked_duplicate():
    """Idempotence: once a duplicate is suppressed AND linked, the pass
    has nothing further to say about it."""
    from dataclasses import replace

    survivor = _story("survivor", "Nvidia reinstates Jensen Huang as CEO", hours_ago=30,
                      excerpt=_HUANG_EXCERPT, companies=("NVIDIA",),
                      tier=NewsMaterialityTier.WATCHLIST, publisher="Light Reading")
    linked = replace(
        _story("linked", "Huang Returns as Nvidia CEO 11 Days After Resigning",
               hours_ago=20, excerpt=_HUANG_EXCERPT, companies=("NVIDIA",),
               tier=SUPPRESSED_TIER, publisher="Business Korea"),
        duplicate_of_story_id="survivor",
    )
    assert [c.story_id for c in plan_reclassification([survivor, linked], NOW)] != ["linked"]
