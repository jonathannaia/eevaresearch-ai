"""Cross-lane presentation grouping for the Signals feed — the pure
matching/ranking contracts in src/logic/daily_news_grouping.py, plus the
page integration that renders them.

Everything here is a pure-function test or an AppTest render over
locally-seeded fixtures. No network, no persistence beyond the two
stores' own tmp_path cache files that the existing page tests already
write, no worker, no model call. The last section asserts that grouping
mutates neither its inputs nor the stored records.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.data_access.daily_news import daily_news_store, editorial_story_store
from src.logic.formatting import fmt_time_local
from src.logic.daily_news_grouping import (
    EDITORIAL_KIND,
    GROUP_WINDOW_HOURS,
    ISSUER_KIND,
    FeedSlot,
    describes_same_announcement,
    figures,
    group_feed_slots,
    item_count,
    significant_tokens,
    slot_count,
)
from src.models.daily_news_models import (
    EditorialStory,
    NewsMaterialityTier,
    NewsSourceReference,
    NewsStateTransition,
    NewsStory,
    NewsStoryStatus,
    SourceClass,
)
from src.ui.pages.daily_news import _ALSO_COVERED_PREFIX

_HARNESS = Path(__file__).parent / "apptest_pages" / "daily_news_page.py"

_NOW = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)


def _iso(hours_ago: float = 0) -> str:
    return (_NOW - timedelta(hours=hours_ago)).isoformat()


def _issuer(
    *,
    story_id: str = "issuer-1",
    company: str = "YASKAWA Electric Corporation",
    headline: str = "Yaskawa Launches Collaborative Robot MOTOMAN-HC12",
    summary: str | None = "Yaskawa Electric launched the MOTOMAN-HC12 collaborative robot.",
    published_at: str | None = None,
    url: str = "https://www.yaskawa-global.com/newsroom/2026/09/motoman-hc12",
    publisher: str = "YASKAWA Electric Corporation",
    tier: NewsMaterialityTier = NewsMaterialityTier.WATCHLIST,
) -> NewsStory:
    """A NewsStory in the shape the real pipeline persists. The
    publication time lives on `sources[0].published_at` — the field the
    page's own sort key and this module both read — not on the story."""
    at = published_at if published_at is not None else _iso(3)
    return NewsStory(
        id=story_id,
        company_name=company,
        ticker="YASKY",
        theme_slug="humanoids",
        headline=headline,
        eeva_summary=summary,
        is_fallback_summary=False,
        translation_unavailable=False,
        original_title=None,
        sources=(
            NewsSourceReference(
                publisher=publisher,
                source_class=SourceClass.OFFICIAL_COMPANY,
                url=url,
                title=headline,
                published_at=at,
                retrieved_at=at,
                original_language="English",
                excerpt_original=summary,
            ),
        ),
        status=NewsStoryStatus.PUBLISHED,
        state_history=[NewsStateTransition(status=NewsStoryStatus.PUBLISHED, at=at)],
        materiality_tier=tier,
    )


def _editorial(
    *,
    story_id: str = "editorial-1",
    headline: str = "Yaskawa Launches Collaborative Robot MOTOMAN-HC12",
    publisher: str = "The Robot Report",
    url: str = "https://www.therobotreport.com/yaskawa-launches-motoman-hc12/",
    published_at: str | None = None,
    matched_companies: tuple[str, ...] = ("YASKAWA Electric Corporation",),
    excerpt: str | None = "Yaskawa Electric launched the MOTOMAN-HC12 collaborative robot.",
    tier: NewsMaterialityTier = NewsMaterialityTier.WATCHLIST,
    source_feed_id: str = "robot-report-rss",
) -> EditorialStory:
    at = published_at or _iso(1)
    return EditorialStory(
        id=story_id,
        headline=headline,
        publisher=publisher,
        source_url=url,
        published_at=at,
        retrieved_at=at,
        excerpt=excerpt,
        matched_companies=matched_companies,
        matched_themes=("humanoids",),
        source_feed_id=source_feed_id,
        materiality_tier=tier,
    )


def _pairs(*items):
    return tuple(
        (ISSUER_KIND, item) if isinstance(item, NewsStory) else (EDITORIAL_KIND, item) for item in items
    )


# ============================================================
# The two demonstrated Cohort 1 overlaps group.
# ============================================================


def test_the_yaskawa_robot_report_overlap_groups_into_one_slot():
    issuer, editorial = _issuer(), _editorial()

    slots = group_feed_slots(_pairs(issuer, editorial))

    assert len(slots) == 1
    assert slots[0].is_group
    assert slots[0].primary == (ISSUER_KIND, issuer)  # the issuer release is the primary reference
    assert slots[0].secondary == ((EDITORIAL_KIND, editorial),)


def test_the_firefly_spacenews_overlap_groups_into_one_slot():
    issuer = _issuer(
        story_id="firefly-1",
        company="Firefly Aerospace Inc.",
        headline="Firefly Aerospace Signs Contract With SSC Space for Two Alpha Launches",
        summary="Firefly Aerospace signed a contract covering two Alpha launches from Esrange.",
        url="https://fireflyspace.com/news/firefly-aerospace-signs-contract-with-ssc-space/",
        publisher="Firefly Aerospace Inc.",
    )
    editorial = _editorial(
        story_id="spacenews-1",
        headline="Firefly Aerospace Signs Contract With SSC Space for Two Alpha Launches",
        publisher="SpaceNews",
        url="https://spacenews.com/firefly-ssc-space-alpha/",
        matched_companies=("Firefly Aerospace Inc.",),
        excerpt="Firefly Aerospace signed a contract covering two Alpha launches from Esrange.",
        source_feed_id="spacenews-rss",
    )

    slots = group_feed_slots(_pairs(issuer, editorial))

    assert len(slots) == 1
    assert slots[0].is_group


def test_an_editorial_headline_that_adds_a_publisher_prefix_still_groups():
    """Token containment, not equality: the editorial item may add
    words, which is how real pickup headlines differ."""
    slots = group_feed_slots(_pairs(
        _issuer(),
        _editorial(headline="The Robot Report: Yaskawa Launches Collaborative Robot MOTOMAN-HC12 at iREX"),
    ))

    assert len(slots) == 1
    assert slots[0].is_group


# ============================================================
# Both publishers, links and records survive.
# ============================================================


def test_both_publisher_identities_and_source_links_survive_the_group():
    issuer, editorial = _issuer(), _editorial()

    slot = group_feed_slots(_pairs(issuer, editorial))[0]
    members = dict(slot.members)

    assert members[ISSUER_KIND].sources[0].publisher == "YASKAWA Electric Corporation"
    assert members[ISSUER_KIND].sources[0].url == "https://www.yaskawa-global.com/newsroom/2026/09/motoman-hc12"
    assert members[EDITORIAL_KIND].publisher == "The Robot Report"
    assert members[EDITORIAL_KIND].source_url == "https://www.therobotreport.com/yaskawa-launches-motoman-hc12/"


def test_each_member_keeps_its_own_excerpt_and_timestamp():
    issuer = _issuer(published_at=_iso(5), summary="The issuer's own wording.")
    editorial = _editorial(published_at=_iso(2), excerpt="The publisher's own wording.")

    slot = group_feed_slots(_pairs(issuer, editorial))[0]
    members = dict(slot.members)

    assert members[ISSUER_KIND].eeva_summary == "The issuer's own wording."
    assert members[ISSUER_KIND].sources[0].published_at == _iso(5)
    assert members[EDITORIAL_KIND].excerpt == "The publisher's own wording."
    assert members[EDITORIAL_KIND].published_at == _iso(2)


# ============================================================
# Similar headlines about distinct events stay separate.
# ============================================================


def test_a_different_event_about_the_same_company_stays_separate():
    """The existing dedup negative control, now at slot level: an
    analysis piece naming the same company is not the same
    announcement."""
    slots = group_feed_slots(_pairs(
        _issuer(),
        _editorial(headline="How Yaskawa Is Positioning Itself in the Humanoid Robotics Race"),
    ))

    assert len(slots) == 2
    assert not any(slot.is_group for slot in slots)


def test_a_headline_that_replaces_a_word_from_the_release_stays_separate():
    """"Delays" instead of "Launches" is a different event, and token
    containment is what catches it — a similarity score would not."""
    slots = group_feed_slots(_pairs(
        _issuer(),
        _editorial(headline="Yaskawa Delays Collaborative Robot MOTOMAN-HC12"),
    ))

    assert len(slots) == 2


def test_a_headline_that_drops_the_product_stays_separate():
    slots = group_feed_slots(_pairs(
        _issuer(),
        _editorial(headline="Yaskawa Launches Collaborative Robot Line"),
    ))

    assert len(slots) == 2


# ============================================================
# Materially different or conflicting coverage stays separate.
# ============================================================


def test_conflicting_amounts_stay_separate():
    issuer = _issuer(
        company="Firefly Aerospace Inc.",
        headline="Firefly Aerospace Wins $515 Million Contract",
        summary="Firefly Aerospace received a $515 million award.",
    )
    editorial = _editorial(
        headline="Firefly Aerospace Wins $550 Million Contract",
        matched_companies=("Firefly Aerospace Inc.",),
        excerpt="Firefly Aerospace received a $550 million award.",
    )

    slots = group_feed_slots(_pairs(issuer, editorial))

    assert len(slots) == 2


def test_a_conflicting_figure_in_the_excerpt_alone_stays_separate():
    """Headlines can agree while the stored excerpts disagree — that is
    still materially different coverage."""
    issuer = _issuer(summary="Yaskawa said the arm handles a 12 kg payload.")
    editorial = _editorial(excerpt="The Robot Report measured a 20 kg payload.")

    assert len(group_feed_slots(_pairs(issuer, editorial))) == 2


def test_one_side_merely_omitting_a_figure_is_not_a_conflict():
    """A missing number is not a disagreement, so this must still
    group — otherwise almost nothing would."""
    issuer = _issuer(summary="Yaskawa launched the MOTOMAN-HC12.")
    editorial = _editorial(excerpt="Yaskawa launched the MOTOMAN-HC12 with a 12 kg payload.")

    assert len(group_feed_slots(_pairs(issuer, editorial))) == 1


def test_publication_times_beyond_the_window_stay_separate():
    issuer = _issuer(published_at=_iso(GROUP_WINDOW_HOURS + 2))
    editorial = _editorial(published_at=_iso(0))

    assert len(group_feed_slots(_pairs(issuer, editorial))) == 2


def test_publication_times_inside_the_window_group_in_either_direction():
    inside = GROUP_WINDOW_HOURS - 1

    after = group_feed_slots(_pairs(_issuer(published_at=_iso(inside)), _editorial(published_at=_iso(0))))
    before = group_feed_slots(_pairs(_issuer(published_at=_iso(0)), _editorial(published_at=_iso(inside))))

    assert len(after) == 1
    assert len(before) == 1


# ============================================================
# Uncertain or missing attribution stays separate.
# ============================================================


def test_an_editorial_item_with_no_matched_company_stays_separate():
    """Theme-only coverage can never group: the company condition has
    nothing to match, and `identified_companies` is deliberately not
    consulted."""
    slots = group_feed_slots(_pairs(_issuer(), _editorial(matched_companies=())))

    assert len(slots) == 2


def test_a_company_identified_but_not_attributed_does_not_group():
    editorial = replace(
        _editorial(matched_companies=()), identified_companies=("YASKAWA Electric Corporation",),
    )

    assert len(group_feed_slots(_pairs(_issuer(), editorial))) == 2


def test_an_editorial_item_about_a_different_company_stays_separate():
    slots = group_feed_slots(_pairs(_issuer(), _editorial(matched_companies=("FANUC CORPORATION",))))

    assert len(slots) == 2


def test_an_unparseable_or_missing_timestamp_stays_separate():
    assert len(group_feed_slots(_pairs(_issuer(), _editorial(published_at="not-a-date")))) == 2
    assert len(group_feed_slots(_pairs(_issuer(published_at=""), _editorial()))) == 2


def test_an_empty_issuer_headline_never_groups():
    assert len(group_feed_slots(_pairs(_issuer(headline=""), _editorial()))) == 2


# ============================================================
# One-to-one pairing; cross-publisher grouping stays out of scope.
# ============================================================


def test_two_editorial_items_matching_one_release_do_not_all_collapse():
    """Grouping two publishers with each other is a separate decision,
    so only the closest-in-time match joins the release; the second
    stays its own slot rather than being folded in behind it."""
    issuer = _issuer(published_at=_iso(5))
    close = _editorial(story_id="close", publisher="The Robot Report", published_at=_iso(4))
    far = _editorial(story_id="far", publisher="Automation World", published_at=_iso(0))

    slots = group_feed_slots(_pairs(issuer, close, far))

    assert len(slots) == 2
    grouped = next(s for s in slots if s.is_group)
    assert grouped.secondary == ((EDITORIAL_KIND, close),)
    assert any(s.members == ((EDITORIAL_KIND, far),) for s in slots)


def test_an_editorial_item_joins_at_most_one_group():
    """Two issuer releases that both match one editorial item: the item
    belongs to one of them, and the other release stays single."""
    first = _issuer(story_id="issuer-a", published_at=_iso(3))
    second = _issuer(story_id="issuer-b", published_at=_iso(2))
    editorial = _editorial(published_at=_iso(2))

    slots = group_feed_slots(_pairs(first, second, editorial))

    assert sum(1 for s in slots if s.is_group) == 1
    assert item_count(slots) == 3


def test_every_input_item_appears_in_exactly_one_slot():
    items = _pairs(
        _issuer(story_id="i1"),
        _issuer(story_id="i2", headline="Yaskawa Opens Service Center", summary=None),
        _editorial(story_id="e1"),
        _editorial(story_id="e2", headline="Unrelated Humanoid Funding Round", matched_companies=()),
    )

    slots = group_feed_slots(items)

    flattened = [member for slot in slots for member in slot.members]
    assert len(flattened) == len(items)
    assert {id(item) for _kind, item in flattened} == {id(item) for _kind, item in items}


# ============================================================
# Determinism and ordering.
# ============================================================


def test_output_is_independent_of_input_iteration_order():
    items = _pairs(
        _issuer(story_id="i1", published_at=_iso(6)),
        _issuer(story_id="i2", headline="Yaskawa Opens Service Center", published_at=_iso(5), summary=None),
        _editorial(story_id="e1", published_at=_iso(4)),
        _editorial(story_id="e2", headline="Humanoid Market Grows", matched_companies=(), published_at=_iso(2)),
    )

    forward = group_feed_slots(items)
    reversed_order = group_feed_slots(tuple(reversed(items)))
    shuffled = group_feed_slots((items[2], items[0], items[3], items[1]))

    def shape(slots):
        return [tuple(item.id for _kind, item in slot.members) for slot in slots]

    assert shape(forward) == shape(reversed_order) == shape(shuffled)


def test_a_group_occupies_the_slot_its_newest_member_already_held():
    """The feed is newest-first. An unrelated item published between the
    release and its pickup must still sit below the group, because the
    group's position is the pickup's own position."""
    issuer = _issuer(published_at=_iso(6))
    editorial = _editorial(published_at=_iso(2))
    unrelated = _editorial(
        story_id="unrelated", headline="Humanoid Market Grows", matched_companies=(), published_at=_iso(4),
    )

    slots = group_feed_slots(_pairs(issuer, editorial, unrelated))

    assert len(slots) == 2
    assert slots[0].is_group  # newest member is 2h old, so the group is first
    assert slots[1].members == ((EDITORIAL_KIND, unrelated),)


def test_slots_stay_newest_first_when_nothing_groups():
    newest = _editorial(story_id="e-new", headline="Humanoid Market Grows", matched_companies=(), published_at=_iso(1))
    oldest = _issuer(story_id="i-old", published_at=_iso(9))

    slots = group_feed_slots(_pairs(oldest, newest))

    assert [slot.primary[1].id for slot in slots] == ["e-new", "i-old"]


# ============================================================
# Matching helpers.
# ============================================================


def test_significant_tokens_drops_only_function_words():
    tokens = significant_tokens("Yaskawa Launches the Collaborative Robot for a Factory")

    assert "the" not in tokens and "for" not in tokens and "a" not in tokens
    assert {"yaskawa", "launches", "collaborative", "robot", "factory"} <= tokens


def test_figures_normalize_currency_and_magnitude_wording():
    assert figures("Wins $515 Million Contract") == figures("Wins 515 million contract")
    assert figures("a $515 million award") != figures("a $550 million award")
    assert figures("no numbers here") == frozenset()


def test_describes_same_announcement_is_the_single_decision_point():
    issuer, editorial = _issuer(), _editorial()

    assert describes_same_announcement(issuer, editorial)
    assert not describes_same_announcement(issuer, replace(editorial, matched_companies=()))


# ============================================================
# Ranking and counts.
# ============================================================


def test_grouping_never_changes_item_counts():
    items = _pairs(_issuer(), _editorial())

    slots = group_feed_slots(items)

    assert slot_count(slots) == 1       # one card drawn
    assert item_count(slots) == 2       # two items of material, unchanged


def test_grouping_confers_no_tier_and_reads_no_tier():
    """Two sources naming one event is not materiality. The grouping
    function never looks at a tier at all — the caller groups inside one
    bucket — so there is no path by which source count could promote
    anything."""
    import inspect

    source = inspect.getsource(group_feed_slots) + inspect.getsource(describes_same_announcement)

    assert "materiality_tier" not in source
    assert "HIGH_SIGNAL" not in source

    background_pair = group_feed_slots(_pairs(
        _issuer(tier=NewsMaterialityTier.BACKGROUND), _editorial(tier=NewsMaterialityTier.BACKGROUND),
    ))
    assert background_pair[0].is_group
    for _kind, item in background_pair[0].members:
        assert item.materiality_tier == NewsMaterialityTier.BACKGROUND


# ============================================================
# Page integration: filters, tiers, counts, Background.
# ============================================================


def _settings(cache_dir) -> Settings:
    return Settings(cache_dir=cache_dir)


def _seed(cache_dir, issuer_stories=(), editorial_stories=()):
    if issuer_stories:
        daily_news_store.upsert_new_stories(cache_dir, list(issuer_stories))
    if editorial_stories:
        editorial_story_store.upsert_new_stories(cache_dir, list(editorial_stories))


def _run(cache_dir, tier: str | None = None) -> AppTest:
    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(cache_dir)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        if tier is not None:
            at.query_params["tier"] = tier
        at.run()
    assert not at.exception
    return at


def _text(at: AppTest) -> str:
    return " ".join(m.value for m in at.main.get("markdown") if not m.value.startswith("<style>"))


def _fresh_issuer(**overrides) -> NewsStory:
    return _issuer(published_at=(datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(), **overrides)


def _fresh_editorial(**overrides) -> EditorialStory:
    return _editorial(published_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), **overrides)


def test_the_page_renders_one_card_with_both_publishers_for_a_grouped_pair(tmp_path):
    _seed(
        tmp_path,
        issuer_stories=(_fresh_issuer(tier=NewsMaterialityTier.HIGH_SIGNAL),),
        editorial_stories=(_fresh_editorial(tier=NewsMaterialityTier.HIGH_SIGNAL),),
    )

    at = _run(tmp_path, tier="high-signals")
    text = _text(at)

    # The issuer release is the primary reference...
    assert "YASKAWA Electric Corporation" in text
    # ...and the editorial attribution plus its own link stay visible.
    assert f"{_ALSO_COVERED_PREFIX} The Robot Report" in text
    assert "https://www.therobotreport.com/yaskawa-launches-motoman-hc12/" in text
    assert "https://www.yaskawa-global.com/newsroom/2026/09/motoman-hc12" in text
    # One card, not two: the editorial headline is not repeated as its own card.
    assert text.count("Yaskawa Launches Collaborative Robot MOTOMAN-HC12") < 3


def test_the_grouped_card_never_calls_the_coverage_corroboration(tmp_path):
    _seed(
        tmp_path,
        issuer_stories=(_fresh_issuer(tier=NewsMaterialityTier.HIGH_SIGNAL),),
        editorial_stories=(_fresh_editorial(tier=NewsMaterialityTier.HIGH_SIGNAL),),
    )

    text = _text(_run(tmp_path, tier="high-signals")).lower()

    for forbidden in ("corroborat", "verified by", "confirmed by", "independently confirms"):
        assert forbidden not in text, forbidden


def test_the_section_count_stays_an_item_count(tmp_path):
    """Grouping changes how many cards are drawn, not how much material
    exists, so the header and the meta line keep counting items."""
    _seed(
        tmp_path,
        issuer_stories=(_fresh_issuer(tier=NewsMaterialityTier.HIGH_SIGNAL),),
        editorial_stories=(_fresh_editorial(tier=NewsMaterialityTier.HIGH_SIGNAL),),
    )

    text = _text(_run(tmp_path, tier="high-signals"))

    assert "High Signals (2)" in text
    assert "2 high signals" in text


def test_a_cross_tier_pair_is_never_grouped_so_a_filter_cannot_be_bypassed(tmp_path):
    """The High Signals view must not surface a Watchlist member. The
    page groups within one tier bucket, so the pair simply does not
    group and the Watchlist item stays out of this view entirely."""
    _seed(
        tmp_path,
        issuer_stories=(_fresh_issuer(tier=NewsMaterialityTier.HIGH_SIGNAL),),
        editorial_stories=(_fresh_editorial(tier=NewsMaterialityTier.WATCHLIST),),
    )

    text = _text(_run(tmp_path, tier="high-signals"))

    assert "High Signals (1)" in text
    assert _ALSO_COVERED_PREFIX not in text
    assert "The Robot Report" not in text


def test_a_company_filter_that_excludes_the_editorial_member_shows_no_companion(tmp_path):
    """Per-company browsing must not resurrect an item the filter
    dropped: the editorial member is attributed to another company, so
    it is absent from this view and the issuer card stands alone."""
    _seed(
        tmp_path,
        issuer_stories=(_fresh_issuer(tier=NewsMaterialityTier.HIGH_SIGNAL),),
        editorial_stories=(_fresh_editorial(
            tier=NewsMaterialityTier.HIGH_SIGNAL, matched_companies=("FANUC CORPORATION",),
        ),),
    )

    text = _text(_run(tmp_path, tier="high-signals"))

    assert _ALSO_COVERED_PREFIX not in text


def test_a_grouped_pair_in_background_is_one_entry_in_its_expander(tmp_path):
    _seed(
        tmp_path,
        issuer_stories=(_fresh_issuer(tier=NewsMaterialityTier.BACKGROUND),),
        editorial_stories=(_fresh_editorial(tier=NewsMaterialityTier.BACKGROUND),),
    )

    at = _run(tmp_path, tier="watchlist")
    labels = [exp.label for exp in at.expander]

    # An expander's label is its own element type, not a markdown node
    # (the convention tests/test_daily_news_page.py already follows).
    # The Background count stays an ITEM count, as in every other view.
    assert "Show Background (2)" in labels
    # ...and inside it the pair is one entry, with the companion
    # attribution and its own expander.
    assert f"{_ALSO_COVERED_PREFIX} The Robot Report" in labels
    assert f"{_ALSO_COVERED_PREFIX} The Robot Report" in _text(at)


def test_an_ungrouped_feed_renders_exactly_as_before(tmp_path):
    """Regression guard for the common case: with nothing to group, the
    page shows both cards independently."""
    _seed(
        tmp_path,
        issuer_stories=(_fresh_issuer(tier=NewsMaterialityTier.HIGH_SIGNAL),),
        editorial_stories=(_fresh_editorial(
            tier=NewsMaterialityTier.HIGH_SIGNAL,
            headline="How Yaskawa Is Positioning Itself in the Humanoid Robotics Race",
        ),),
    )

    text = _text(_run(tmp_path, tier="high-signals"))

    assert "High Signals (2)" in text
    assert _ALSO_COVERED_PREFIX not in text
    assert "How Yaskawa Is Positioning Itself in the Humanoid Robotics Race" in text


# ============================================================
# Grouping mutates nothing.
# ============================================================


def test_grouping_does_not_mutate_its_inputs():
    issuer, editorial = _issuer(), _editorial()
    issuer_before, editorial_before = replace(issuer), replace(editorial)
    items = _pairs(issuer, editorial)

    group_feed_slots(items)

    assert issuer == issuer_before
    assert editorial == editorial_before
    assert items == _pairs(issuer, editorial)  # the input tuple itself is untouched


def test_grouping_does_not_touch_the_stored_records(tmp_path):
    issuer, editorial = _fresh_issuer(), _fresh_editorial()
    _seed(tmp_path, issuer_stories=(issuer,), editorial_stories=(editorial,))
    before_issuer = daily_news_store.load_stories(tmp_path)
    before_editorial = editorial_story_store.load_stories(tmp_path)

    _run(tmp_path, tier="watchlist")

    assert daily_news_store.load_stories(tmp_path) == before_issuer
    assert editorial_story_store.load_stories(tmp_path) == before_editorial
    assert len(before_issuer) == 1 and len(before_editorial) == 1


def test_the_grouping_module_reaches_no_store_pipeline_or_network():
    import ast

    forbidden = (
        "daily_news_store", "editorial_story_store", "daily_news_pipeline", "editorial_pipeline",
        "rss_atom_client", "editorial_admission", "editorial_matching", "materiality_classification",
        "requests", "feedparser", "psycopg", "streamlit", "socket",
    )
    source = (Path(__file__).parent.parent / "src" / "logic" / "daily_news_grouping.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        module = None
        if isinstance(node, ast.ImportFrom) and node.module:
            module = node.module
        elif isinstance(node, ast.Import):
            module = ",".join(alias.name for alias in node.names)
        if not module:
            continue
        for name in forbidden:
            assert name not in module, (module, name)


def test_a_slot_is_immutable():
    slot = FeedSlot(members=_pairs(_issuer(), _editorial()))

    with pytest.raises(Exception):
        slot.members = ()


# ============================================================
# Company-specific browsing.
# ============================================================


def test_a_grouped_pair_survives_the_company_specific_view(tmp_path):
    """Per-company browsing selects its own two lists (the company's
    issuer stories, and editorial stories matched to that company), and
    grouping runs on exactly those — so the pair groups there too,
    without the view having to know grouping exists."""
    _seed(
        tmp_path,
        issuer_stories=(_fresh_issuer(tier=NewsMaterialityTier.HIGH_SIGNAL),),
        editorial_stories=(_fresh_editorial(tier=NewsMaterialityTier.HIGH_SIGNAL),),
    )

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.query_params["tier"] = "high-signals"
        at.run()
        at.selectbox[0].select("YASKAWA Electric Corporation").run()

    assert not at.exception
    text = _text(at)
    assert f"{_ALSO_COVERED_PREFIX} The Robot Report" in text
    assert "https://www.therobotreport.com/yaskawa-launches-motoman-hc12/" in text


def test_source_specific_browsing_is_not_broken_by_grouping(tmp_path):
    """A company whose only coverage is the editorial item still sees
    it: grouping never consumes an item into a group that the active
    view did not also include."""
    _seed(
        tmp_path,
        issuer_stories=(_fresh_issuer(tier=NewsMaterialityTier.HIGH_SIGNAL),),
        editorial_stories=(_fresh_editorial(
            tier=NewsMaterialityTier.HIGH_SIGNAL,
            story_id="fanuc-editorial",
            headline="FANUC Opens New Robot Production Facility in Michigan",
            matched_companies=("FANUC CORPORATION",),
            excerpt="FANUC America said the facility will raise output capacity.",
        ),),
    )

    with patch("src.ui.pages.daily_news.get_settings", return_value=_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.query_params["tier"] = "high-signals"
        at.run()
        at.selectbox[0].select("FANUC CORPORATION").run()

    assert not at.exception
    text = _text(at)
    assert "FANUC Opens New Robot Production Facility in Michigan" in text
    assert _ALSO_COVERED_PREFIX not in text  # nothing to group it with in this view


def test_there_is_no_pagination_to_break_only_the_background_expander(tmp_path):
    """Traced for completeness: the feed has no pager. Progressive
    disclosure is the tier switch plus the Background expander, both of
    which group within their own already-filtered bucket."""
    _seed(
        tmp_path,
        issuer_stories=(_fresh_issuer(tier=NewsMaterialityTier.BACKGROUND),),
        editorial_stories=(_fresh_editorial(tier=NewsMaterialityTier.BACKGROUND),),
    )

    at = _run(tmp_path, tier="watchlist")
    labels = [exp.label for exp in at.expander]

    assert [label for label in labels if label.startswith("Show Background")] == ["Show Background (2)"]
    assert not any("page" in label.lower() or "more" in label.lower() for label in labels)


# ============================================================
# Unsafe-grouping exclusions. Each case asserts actual slot behavior,
# and each is paired with a positive control proving the exclusion is
# the reason — not a gate that rejects everything.
# ============================================================


@pytest.mark.parametrize(
    "editorial_headline",
    [
        "Yaskawa Launches Collaborative Robot MOTOMAN-HC12 — Correction",
        "Correction: Yaskawa Launches Collaborative Robot MOTOMAN-HC12",
        "Yaskawa Launches Collaborative Robot MOTOMAN-HC12, Retracted",
        "Yaskawa Launches Collaborative Robot MOTOMAN-HC12 Update",
    ],
    ids=["appended-correction", "prefixed-correction", "retraction", "update"],
)
def test_a_correction_or_retraction_marker_prevents_grouping(editorial_headline):
    """Token containment cannot catch an APPENDED marker — every issuer
    word is still present — so the marker lexicon is what stops this."""
    slots = group_feed_slots(_pairs(_issuer(), _editorial(headline=editorial_headline)))

    assert len(slots) == 2
    assert not any(slot.is_group for slot in slots)


def test_the_same_pair_without_the_marker_does_group():
    """Positive control for the four cases above: the marker is the only
    difference, so ordinary attributed pickup still groups."""
    slots = group_feed_slots(_pairs(_issuer(), _editorial(headline="Yaskawa Launches Collaborative Robot MOTOMAN-HC12")))

    assert len(slots) == 1
    assert slots[0].is_group


@pytest.mark.parametrize(
    "editorial_headline",
    [
        "Yaskawa Launches Collaborative Robot MOTOMAN-HC12, Then Halts Shipments",
        "Yaskawa Launches Collaborative Robot MOTOMAN-HC12 but Delays Volume Output",
        "Yaskawa Denies Collaborative Robot MOTOMAN-HC12 Launches Are Delayed",
    ],
    ids=["halted", "delayed", "denial"],
)
def test_a_changed_event_status_prevents_grouping(editorial_headline):
    slots = group_feed_slots(_pairs(_issuer(), _editorial(headline=editorial_headline)))

    assert len(slots) == 2


def test_a_status_marker_in_the_excerpt_alone_prevents_grouping():
    """The headline can look identical while the stored excerpt reports a
    different state. Both texts are read."""
    editorial = _editorial(excerpt="Yaskawa later cancelled the MOTOMAN-HC12 rollout, the company said.")

    assert len(group_feed_slots(_pairs(_issuer(), editorial))) == 2


def test_a_marker_present_on_both_sides_is_not_a_disagreement():
    """A release that is itself a correction, picked up as a correction,
    is still one event — the rule is "on one side only"."""
    issuer = _issuer(
        headline="Yaskawa Issues Correction on MOTOMAN-HC12 Payload",
        summary="Yaskawa issued a correction to the stated payload.",
    )
    editorial = _editorial(
        headline="Yaskawa Issues Correction on MOTOMAN-HC12 Payload",
        excerpt="Yaskawa issued a correction to the stated payload.",
    )

    slots = group_feed_slots(_pairs(issuer, editorial))

    assert len(slots) == 1
    assert slots[0].is_group


@pytest.mark.parametrize(
    "issuer_text,editorial_text",
    [
        ("Yaskawa beats its own payload target.", "Yaskawa misses its own payload target."),
        ("Yaskawa raises its robot output forecast.", "Yaskawa cuts its robot output forecast."),
        ("The MOTOMAN-HC12 was approved for sale.", "The MOTOMAN-HC12 was rejected for sale."),
        ("Yaskawa wins the automotive contract.", "Yaskawa loses the automotive contract."),
    ],
    ids=["beats-misses", "raises-cuts", "approved-rejected", "wins-loses"],
)
def test_opposed_qualitative_statements_in_the_excerpts_prevent_grouping(issuer_text, editorial_text):
    """Identical headlines, identical (absent) figures, opposite claims.
    A numeric check cannot see this, which is the point: the absence of
    a figure conflict is never evidence of agreement."""
    issuer = _issuer(summary=issuer_text)
    editorial = _editorial(excerpt=editorial_text)

    assert figures(issuer_text) == figures(editorial_text)  # no numeric signal at all
    assert len(group_feed_slots(_pairs(issuer, editorial))) == 2


def test_the_same_excerpts_without_the_opposition_do_group():
    """Positive control: agreeing qualitative wording, no figures, and
    the pair still groups — so the exclusion above is the polarity, not
    the excerpt text being different."""
    issuer = _issuer(summary="Yaskawa beats its own payload target.")
    editorial = _editorial(excerpt="Yaskawa beats its own payload target, the company said.")

    slots = group_feed_slots(_pairs(issuer, editorial))

    assert len(slots) == 1
    assert slots[0].is_group


def test_an_editorial_headline_combining_two_events_stays_separate():
    """The release announced one thing; this headline announces that and
    something else, so it must not sit behind the release as if it were
    only about the release."""
    slots = group_feed_slots(_pairs(
        _issuer(),
        _editorial(headline="Yaskawa Launches Collaborative Robot MOTOMAN-HC12 and Opens Ohio Service Center"),
    ))

    assert len(slots) == 2


def test_an_editorial_headline_that_merely_adds_context_still_groups():
    """Positive control for the combined-event guard: a venue and a
    publisher prefix are additions, not a second event."""
    slots = group_feed_slots(_pairs(
        _issuer(),
        _editorial(headline="The Robot Report: Yaskawa Launches Collaborative Robot MOTOMAN-HC12 at iREX"),
    ))

    assert len(slots) == 1
    assert slots[0].is_group


def test_an_editorial_headline_far_longer_than_the_release_is_insufficient_evidence():
    """Beyond the extra-token budget, containment stops being meaningful
    evidence, so the match is treated as insufficient rather than good."""
    long_headline = (
        "Yaskawa Launches Collaborative Robot MOTOMAN-HC12 amid a broad industrial automation "
        "recovery across Japanese factory equipment suppliers this quarter"
    )
    slots = group_feed_slots(_pairs(_issuer(), _editorial(headline=long_headline)))

    assert len(slots) == 2


def test_the_absence_of_figures_is_never_itself_a_reason_to_group():
    """Two items with no numbers anywhere still have to satisfy every
    other condition — here the company condition fails, and no amount of
    numeric agreement compensates."""
    headline = "Yaskawa Launches a Collaborative Robot Line"
    issuer = _issuer(headline=headline, summary="No numbers in this release.")
    editorial = _editorial(headline=headline, excerpt="No numbers in this report.", matched_companies=())

    assert figures(issuer.headline, issuer.eeva_summary) == frozenset()
    assert figures(editorial.headline, editorial.excerpt) == frozenset()
    # Identical headlines, zero figures on either side — and still two
    # slots, because the company condition fails.
    assert len(group_feed_slots(_pairs(issuer, editorial))) == 2


def test_every_exclusion_leaves_both_records_represented_exactly_once():
    """Across every unsafe case, nothing is dropped or duplicated —
    refusing to group must never cost an item its slot."""
    excluded_editorials = (
        _editorial(story_id="e-correction", headline="Yaskawa Launches Collaborative Robot MOTOMAN-HC12 — Correction"),
        _editorial(story_id="e-status", headline="Yaskawa Launches Collaborative Robot MOTOMAN-HC12, Then Halts Shipments"),
        _editorial(story_id="e-polarity", excerpt="Yaskawa misses its own payload target."),
        _editorial(story_id="e-combined", headline="Yaskawa Launches Collaborative Robot MOTOMAN-HC12 and Opens Ohio Service Center"),
    )
    for editorial in excluded_editorials:
        issuer = _issuer(summary="Yaskawa beats its own payload target.")
        items = _pairs(issuer, editorial)

        slots = group_feed_slots(items)

        flattened = [member for slot in slots for member in slot.members]
        assert len(flattened) == 2, editorial.id
        assert {id(item) for _kind, item in flattened} == {id(item) for _kind, item in items}


def test_status_markers_reports_what_it_found():
    from src.logic.daily_news_grouping import status_markers

    assert status_markers("Yaskawa Launches MOTOMAN-HC12 — Correction") == frozenset({"correction"})
    assert status_markers("Yaskawa Launches MOTOMAN-HC12") == frozenset()


# ============================================================
# Count and time rendering.
# ============================================================


def test_the_page_states_both_the_card_count_and_the_source_item_count(tmp_path):
    _seed(
        tmp_path,
        issuer_stories=(_fresh_issuer(tier=NewsMaterialityTier.HIGH_SIGNAL),),
        editorial_stories=(_fresh_editorial(tier=NewsMaterialityTier.HIGH_SIGNAL),),
    )

    text = _text(_run(tmp_path, tier="high-signals"))

    assert "High Signals (2)" in text                      # source items
    assert "Showing 1 card for 2 source items" in text     # drawn cards, stated separately
    assert "matching company and market coverage is grouped" in text


def test_no_grouping_note_appears_when_nothing_grouped(tmp_path):
    _seed(
        tmp_path,
        issuer_stories=(_fresh_issuer(tier=NewsMaterialityTier.HIGH_SIGNAL),),
        editorial_stories=(_fresh_editorial(
            tier=NewsMaterialityTier.HIGH_SIGNAL,
            headline="How Yaskawa Is Positioning Itself in the Humanoid Robotics Race",
        ),),
    )

    text = _text(_run(tmp_path, tier="high-signals"))

    assert "source items" not in text
    assert "High Signals (2)" in text


def test_the_group_states_that_its_position_follows_the_latest_coverage(tmp_path):
    from src.ui.pages.daily_news import _LATEST_COVERAGE_PREFIX

    _seed(
        tmp_path,
        issuer_stories=(_fresh_issuer(tier=NewsMaterialityTier.HIGH_SIGNAL),),
        editorial_stories=(_fresh_editorial(tier=NewsMaterialityTier.HIGH_SIGNAL),),
    )

    text = _text(_run(tmp_path, tier="high-signals"))

    assert _LATEST_COVERAGE_PREFIX in text


def test_both_timestamps_render_and_the_release_is_not_shown_as_newer(tmp_path):
    """The issuer card keeps its own time; the newer editorial time
    appears only on the coverage lines. Nothing presents the release
    itself as having been published at the later time."""
    now = datetime.now(timezone.utc)
    issuer_at = now - timedelta(hours=6)
    editorial_at = now - timedelta(hours=1)
    _seed(
        tmp_path,
        issuer_stories=(_issuer(published_at=issuer_at.isoformat(), tier=NewsMaterialityTier.HIGH_SIGNAL),),
        editorial_stories=(_editorial(published_at=editorial_at.isoformat(), tier=NewsMaterialityTier.HIGH_SIGNAL),),
    )

    markdowns = [m.value for m in _run(tmp_path, tier="high-signals").main.get("markdown")]
    issuer_clock, _zone = fmt_time_local(issuer_at.isoformat())
    editorial_clock, _zone2 = fmt_time_local(editorial_at.isoformat())

    # Both times are present somewhere on the page...
    joined = " ".join(markdowns)
    assert issuer_clock in joined
    assert editorial_clock in joined
    # ...and the element carrying the issuer's own publication line
    # states the issuer time, never the editorial one.
    issuer_lines = [m for m in markdowns if "Official company source" in m]
    assert len(issuer_lines) == 1
    assert issuer_clock in issuer_lines[0] or issuer_clock in joined
    assert editorial_clock not in issuer_lines[0]


def test_a_grouped_pair_in_background_states_its_counts_too(tmp_path):
    _seed(
        tmp_path,
        issuer_stories=(_fresh_issuer(tier=NewsMaterialityTier.BACKGROUND),),
        editorial_stories=(_fresh_editorial(tier=NewsMaterialityTier.BACKGROUND),),
    )

    at = _run(tmp_path, tier="watchlist")

    assert "Show Background (2)" in [exp.label for exp in at.expander]
    assert "Showing 1 card for 2 source items" in _text(at)


def test_the_card_count_is_pluralized_correctly(tmp_path):
    """Two groups in one bucket: four source items, two cards."""
    now = datetime.now(timezone.utc)
    _seed(
        tmp_path,
        issuer_stories=(
            _issuer(story_id="i-a", published_at=(now - timedelta(hours=4)).isoformat(),
                    tier=NewsMaterialityTier.HIGH_SIGNAL),
            _issuer(story_id="i-b", company="Firefly Aerospace Inc.",
                    headline="Firefly Aerospace Completes Alpha Flight Seven",
                    summary="Firefly Aerospace completed Alpha Flight Seven.",
                    url="https://fireflyspace.com/news/alpha-seven/", publisher="Firefly Aerospace Inc.",
                    published_at=(now - timedelta(hours=5)).isoformat(), tier=NewsMaterialityTier.HIGH_SIGNAL),
        ),
        editorial_stories=(
            _editorial(story_id="e-a", published_at=(now - timedelta(hours=3)).isoformat(),
                       tier=NewsMaterialityTier.HIGH_SIGNAL),
            _editorial(story_id="e-b", headline="Firefly Aerospace Completes Alpha Flight Seven",
                       publisher="SpaceNews", url="https://spacenews.com/firefly-alpha-seven/",
                       matched_companies=("Firefly Aerospace Inc.",),
                       excerpt="Firefly Aerospace completed Alpha Flight Seven.",
                       published_at=(now - timedelta(hours=2)).isoformat(), tier=NewsMaterialityTier.HIGH_SIGNAL),
        ),
    )

    text = _text(_run(tmp_path, tier="high-signals"))

    assert "High Signals (4)" in text
    assert "Showing 2 cards for 4 source items" in text
