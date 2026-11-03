"""Daily News Cohort 1 — behavioral acceptance tests for the six sources
added by that batch (Equinix IR, L3Harris newsroom, Firefly Aerospace,
Yaskawa newsroom on Lane A; SpaceNews and The Robot Report on Lane B).

Covers the cases from §5.3 of design/DAILY_NEWS_SOURCE_CENSUS_AND_
COHORT1_DESIGN_2026_09_15.md that no existing test already covers. The
implementation report for this branch carries the full requirement →
coverage mapping; the short version is that the registry-shape, URL-
domain, dedup-unit, render-time-translation-purity and
HOLD-candidate-not-added requirements were already covered elsewhere and
are deliberately NOT duplicated here.

**Requirement 11 is not satisfied BY THIS FILE.**
`localized_duplicate_yaskawa_newsroom_vs_robot_report` is addressed by
presentation grouping, whose behaviour is asserted in
tests/test_daily_news_cross_lane_grouping.py. What this file pins is the
persistent half: both lanes keep their own source record for the same
event. See that section at the bottom.

Discipline, matching the suites this builds on:
  * Every Lane A test drives the REAL `feed_registry.PILOT_FEEDS` entry
    for its source, not a synthetic look-alike — the same convention
    tests/test_daily_news_pipeline.py already uses for Samsung, Murata,
    Microchip and HPE — so a change to the registry's own fields (feed
    URL, canonical domain, company name, declared language) fails here.
  * Every Lane B test drives the real `EDITORIAL_SOURCE_REGISTRY` entry
    and runs the real matching → classification → admission path, via
    the same three-call sequence editorial_pipeline.py itself performs.
  * Retrieval is mocked at `rss_atom_client.fetch_entries`, and every
    test additionally installs tests/no_network.py's socket blocker, so
    an outbound call added anywhere beneath these paths fails loudly
    instead of reaching a real feed.
  * Every case carries a negative control that would catch the failure
    the requirement is about, not only an assertion that the happy path
    works.

Fixture provenance: the feed items below are synthetic, written for this
file. Each is a plausible shape for its source, and none is presented as
a literal production pull — the same disclosure tests/
test_editorial_admission.py makes about its own reconstructions.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from src.data_access.daily_news import (
    daily_news_pipeline,
    daily_news_store,
    editorial_pipeline,
    editorial_story_store,
    rss_atom_client,
)
from src.data_access.daily_news.editorial_admission import assess_admission
from src.data_access.daily_news.editorial_matching import matched_companies_and_themes
from src.data_access.daily_news.feed_registry import PILOT_FEEDS
from src.data_access.daily_news.materiality_classification import classify_editorial_story
from src.data_access.daily_news.rss_atom_client import FeedFetchResult, RawFeedEntry
from src.data_access.daily_news.source_registry import (
    EDITORIAL_SOURCE_REGISTRY,
    SourceCategory,
    SourceRegistryValidationError,
    assert_valid_source_entry,
    validate_source_entry,
)
from src.models.daily_news_models import NewsMaterialityTier, NewsStoryStatus
from tests.no_network import block_network


# --- the real registry entries under test ---------------------------

def _lane_a(company_fragment: str):
    return next(f for f in PILOT_FEEDS if company_fragment.lower() in f.company_name.lower())


def _lane_b(source_id: str):
    return next(e for e in EDITORIAL_SOURCE_REGISTRY if e.source_id == source_id)


_EQUINIX = _lane_a("Equinix")
_L3HARRIS = _lane_a("L3Harris")
_FIREFLY = _lane_a("Firefly")
_YASKAWA = _lane_a("Yaskawa")

_SPACENEWS = _lane_b("spacenews-rss")
_ROBOT_REPORT = _lane_b("robot-report-rss")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Every test in this file, without exception."""
    block_network(monkeypatch)


def _entry(title: str, link: str, summary: str, published_at: str | None = None) -> RawFeedEntry:
    return RawFeedEntry(
        title=title,
        link=link,
        published_at=published_at or datetime.now(timezone.utc).isoformat(),
        summary=summary,
    )


def _mock_fetch(entries_by_url: dict[str, FeedFetchResult], monkeypatch) -> None:
    def _fake_fetch_entries(feed_url: str) -> FeedFetchResult:
        return entries_by_url.get(feed_url, FeedFetchResult(entries=(), failure_code=None))

    monkeypatch.setattr(rss_atom_client, "fetch_entries", _fake_fetch_entries)
    monkeypatch.setattr(daily_news_pipeline.rss_atom_client, "fetch_entries", _fake_fetch_entries)
    monkeypatch.setattr(editorial_pipeline.rss_atom_client, "fetch_entries", _fake_fetch_entries)


def _only_story(cache_dir):
    stories = daily_news_store.load_stories(cache_dir)
    assert len(stories) == 1, stories
    return next(iter(stories.values()))


def _assess(title: str, description: str, category: SourceCategory = SourceCategory.INDEPENDENT_NEWS):
    """The exact three-call sequence editorial_pipeline.py performs, so
    these tests exercise the real integration shape rather than
    assess_admission() with hand-picked inputs."""
    companies, themes = matched_companies_and_themes(title, description)
    tier, reasons = classify_editorial_story(title, description, category)
    return companies, themes, tier, assess_admission(title, description, companies, themes, reasons)


# ============================================================
# §5.3 equinix_ir_rss_publishes_real_story
# ============================================================


def test_equinix_real_registry_entry_publishes_with_attribution_and_direct_url(tmp_path, monkeypatch):
    """Lane A end-to-end against the real `equinix-ir-rss` entry: fetch →
    URL/domain validation → admission → persistence."""
    article_url = "https://investor.equinix.com/news-events/press-releases/detail/equinix-expands-tokyo-campus"
    _mock_fetch({
        _EQUINIX.feed_url: FeedFetchResult(
            entries=(_entry(
                "Equinix Expands Tokyo Data Center Campus With $380 Million Investment",
                article_url,
                "Equinix, Inc. said it will invest $380 million to add capacity at its Tokyo campus.",
            ),),
            failure_code=None,
        ),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_EQUINIX,))

    assert report.stories_published == 1
    story = _only_story(tmp_path)
    assert story.company_name == "Equinix, Inc."
    assert story.status == NewsStoryStatus.PUBLISHED
    assert story.sources[0].url == article_url  # the publisher's own link, unmodified
    assert story.sources[0].publisher == "Equinix, Inc."


def test_equinix_off_domain_item_is_suppressed_and_never_persisted(tmp_path, monkeypatch):
    """Negative control for the test above: the same feed carrying an
    item that links off its registered canonical domain must publish
    nothing. Without this, that test would pass even if the domain
    allowlist stopped being applied to this entry."""
    _mock_fetch({
        _EQUINIX.feed_url: FeedFetchResult(
            entries=(_entry(
                "Equinix Expands Tokyo Data Center Campus With $380 Million Investment",
                "https://press-release-aggregator.example.com/equinix-tokyo",
                "Equinix, Inc. said it will invest $380 million to add capacity at its Tokyo campus.",
            ),),
            failure_code=None,
        ),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_EQUINIX,))

    assert report.stories_published == 0
    assert daily_news_store.load_stories(tmp_path) == {}


# ============================================================
# §5.3 l3harris_newsroom_rss_publishes_real_story
# ============================================================


def test_l3harris_real_registry_entry_publishes_with_attribution_and_direct_url(tmp_path, monkeypatch):
    """Same end-to-end path for the mixed editorial/press-release
    newsroom feed shape §5.3 calls out."""
    article_url = "https://www.l3harris.com/newsroom/press-release/2026/09/l3harris-delivers-first-tranche-2-vehicle"
    _mock_fetch({
        _L3HARRIS.feed_url: FeedFetchResult(
            entries=(_entry(
                "L3Harris Delivers First Tranche 2 Transport Layer Space Vehicle",
                article_url,
                "L3Harris Technologies delivered the first of 16 Tranche 2 Transport Layer satellites.",
            ),),
            failure_code=None,
        ),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_L3HARRIS,))

    assert report.stories_published == 1
    story = _only_story(tmp_path)
    assert story.company_name == "L3Harris Technologies, Inc."
    assert story.status == NewsStoryStatus.PUBLISHED
    assert story.sources[0].url == article_url


def test_l3harris_item_on_a_different_real_subpath_of_the_same_domain_also_publishes(tmp_path, monkeypatch):
    """The newsroom feed carries both press releases and editorial
    stories under different paths on one domain. Both must pass the
    domain check — a path-sensitive allowlist would silently drop half
    the feed."""
    article_url = "https://www.l3harris.com/newsroom/editorial/2026/09/inside-the-resilient-gps-program"
    _mock_fetch({
        _L3HARRIS.feed_url: FeedFetchResult(
            entries=(_entry(
                "Inside the Resilient GPS Program at L3Harris",
                article_url,
                "L3Harris Technologies engineers describe the Resilient GPS satellite design.",
            ),),
            failure_code=None,
        ),
    }, monkeypatch)

    assert daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_L3HARRIS,)).stories_published == 1


# ============================================================
# §5.3 firefly_aerospace_feed_publishes_real_story
# ============================================================


def test_firefly_aerospace_real_registry_entry_publishes_with_attribution_and_direct_url(tmp_path, monkeypatch):
    article_url = "https://fireflyspace.com/news/firefly-aerospace-signs-contract-with-ssc-space/"
    _mock_fetch({
        _FIREFLY.feed_url: FeedFetchResult(
            entries=(_entry(
                "Firefly Aerospace Signs Contract With SSC Space for Two Alpha Launches",
                article_url,
                "Firefly Aerospace signed a contract covering two Alpha launches from Esrange.",
            ),),
            failure_code=None,
        ),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_FIREFLY,))

    assert report.stories_published == 1
    story = _only_story(tmp_path)
    assert story.company_name == "Firefly Aerospace Inc."
    assert story.status == NewsStoryStatus.PUBLISHED
    assert story.sources[0].url == article_url


# ============================================================
# §5.3 yaskawa_newsroom_rss_language_declared_english_not_feed_metadata
#
# The registry-field half of this requirement is already covered twice
# (test_daily_news_source_registry.py::test_yaskawa_newsroom_language_is_
# english_not_feed_metadata and test_daily_news_feed_registry.py::
# test_yaskawa_language_is_curated_english_not_the_feeds_own_ja_metadata).
# What was missing is the behavioral half: that a story PUBLISHED from
# this feed carries the curated declaration, and that the declaration is
# genuinely read from the entry rather than being a constant that would
# mask a future revert.
# ============================================================


def test_yaskawa_published_story_declares_the_curated_english_language(tmp_path, monkeypatch):
    article_url = "https://www.yaskawa-global.com/newsroom/2026/09/motoman-hc12"
    _mock_fetch({
        _YASKAWA.feed_url: FeedFetchResult(
            entries=(_entry(
                "Yaskawa Launches Collaborative Robot MOTOMAN-HC12",
                article_url,
                "Yaskawa Electric launched the MOTOMAN-HC12 collaborative robot with a 12 kg payload.",
            ),),
            failure_code=None,
        ),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_YASKAWA,))

    assert report.stories_published == 1
    assert _only_story(tmp_path).sources[0].original_language == "English"


def test_the_published_language_is_read_from_the_entry_not_assumed_english(tmp_path, monkeypatch):
    """Negative control for the test above. If the pipeline hardcoded
    English, or ever started reading the feed's own (misleading) `ja`
    metadata, that test alone could not tell. Driving the same real
    entry with its declared language changed proves the published value
    follows the registry field — which is what makes Yaskawa's curated
    `English` a real, load-bearing decision."""
    article_url = "https://www.yaskawa-global.com/newsroom/2026/09/motoman-hc12"
    _mock_fetch({
        _YASKAWA.feed_url: FeedFetchResult(
            entries=(_entry(
                "Yaskawa Launches Collaborative Robot MOTOMAN-HC12",
                article_url,
                "Yaskawa Electric launched the MOTOMAN-HC12 collaborative robot with a 12 kg payload.",
            ),),
            failure_code=None,
        ),
    }, monkeypatch)

    relabelled = replace(_YASKAWA, language="Japanese")
    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(relabelled,))

    assert report.stories_published == 1
    assert _only_story(tmp_path).sources[0].original_language == "Japanese"
    # And the registry itself still declares English — the override above
    # is local to this test's own copy of the entry.
    assert _YASKAWA.language == "English"


# ============================================================
# §5.3 source_failure_isolated_equinix_down_does_not_block_other_feeds
#
# tests/test_daily_news_pipeline.py already proves this for the
# Samsung/Murata/Microchip trio. §5.3 asks for it against a Cohort 1
# entry rather than assumed to generalize, which is what this adds.
# ============================================================


def test_an_equinix_fetch_failure_does_not_block_the_other_cohort1_feeds(tmp_path, monkeypatch):
    article_url = "https://www.l3harris.com/newsroom/press-release/2026/09/l3harris-delivers-first-tranche-2-vehicle"
    _mock_fetch({
        _EQUINIX.feed_url: FeedFetchResult(entries=(), failure_code="HTTPError:503"),
        _L3HARRIS.feed_url: FeedFetchResult(
            entries=(_entry(
                "L3Harris Delivers First Tranche 2 Transport Layer Space Vehicle",
                article_url,
                "L3Harris Technologies delivered the first of 16 Tranche 2 Transport Layer satellites.",
            ),),
            failure_code=None,
        ),
        # Firefly deliberately left unmocked: zero entries, zero failure.
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(
        tmp_path, feed_sources=(_EQUINIX, _L3HARRIS, _FIREFLY),
    )

    assert report.source_failures == {"Equinix, Inc.": "HTTPError:503"}
    assert report.stories_published == 1
    assert _only_story(tmp_path).company_name == "L3Harris Technologies, Inc."


# ============================================================
# §5.3 spacenews_independent_news_requires_allowlisting
#
# The gate itself is already covered generically
# (test_daily_news_source_registry.py::test_independent_news_without_
# allowlisting_is_rejected), and the real entries are asserted to BE
# allowlisted. What §5.3 asks for is the gate exercised against this
# specific entry, which is the negative control below.
# ============================================================


@pytest.mark.parametrize("entry", [_SPACENEWS, _ROBOT_REPORT], ids=["spacenews", "robot-report"])
def test_a_cohort1_independent_news_entry_is_rejected_without_allowlisting(entry):
    assert validate_source_entry(entry) == ()  # the real entry is valid as registered

    stripped = replace(entry, allowlisted=False)

    assert validate_source_entry(stripped) == (
        "independent_news sources must be explicitly allowlisted (allowlisted=True)",
    )
    with pytest.raises(SourceRegistryValidationError):
        assert_valid_source_entry(stripped)


def test_allowlisting_alone_is_not_what_makes_the_entry_valid(entry=_SPACENEWS):
    """Negative control on the control: the allowlist flag must not be
    able to excuse a different violation, or the assertion above would
    pass for the wrong reason."""
    with pytest.raises(SourceRegistryValidationError):
        assert_valid_source_entry(replace(entry, allowlisted=True, licensing_classification=""))


# ============================================================
# §5.3 spacenews_matches_tracked_space_companies
#
# Firefly and L3Harris are already covered in
# tests/test_editorial_admission.py. Rocket Lab — named in the
# requirement — was not.
# ============================================================


def test_a_spacenews_item_naming_rocket_lab_is_admitted_and_attributed():
    companies, _themes, tier, decision = _assess(
        "Rocket Lab Wins $515 Million Contract for 18 Tranche 2 Satellites",
        "Rocket Lab USA said it received a $515 million award from the Space Development Agency to build "
        "18 satellites for the Tranche 2 Transport Layer.",
    )

    assert companies == ("Rocket Lab",)
    assert decision.admitted is True
    assert decision.reason == "company_subject:Rocket Lab"
    # A named contract award with a dollar figure is the documented
    # quantified-anchor shape, so this is not a Background item.
    assert tier == NewsMaterialityTier.HIGH_SIGNAL


def test_a_space_item_naming_no_tracked_company_is_not_attributed_to_one():
    """Negative control for the alias table: it must resolve the
    companies it knows without inventing one for an unrelated launch
    provider. Without this, an over-broad alias table would pass every
    attribution test above."""
    companies, _themes, _tier, decision = _assess(
        "Isar Aerospace Completes Second Spectrum Launch Attempt From Andoya",
        "Isar Aerospace said its Spectrum vehicle lifted off from Andoya before an early engine shutdown.",
    )

    assert companies == ()
    assert not decision.reason.startswith("company_subject:")


# ============================================================
# §5.3 robot_report_matches_tracked_humanoids_companies
#
# Yaskawa is already covered in tests/test_editorial_admission.py.
# Nabtesco, Rockwell and FANUC — all named in the requirement — were
# not. Nabtesco is a tracked company with no feed of its own, so this
# editorial path is the only way it can ever be attributed.
# ============================================================


@pytest.mark.parametrize(
    "title,description,expected_company",
    [
        (
            "Nabtesco Expands Precision Reduction Gear Capacity for Humanoid Robots",
            "Nabtesco Corporation will invest 12 billion yen to expand output of precision reduction gears "
            "used in humanoid robot joints.",
            "Nabtesco Corporation",
        ),
        (
            "Rockwell Automation Adds Autonomous Mobile Robot Fleet Software",
            "Rockwell Automation announced a fleet-management release for autonomous mobile robots in "
            "automotive assembly plants.",
            "Rockwell Automation",
        ),
        (
            "FANUC Opens New Robot Production Facility in Michigan",
            "FANUC America said the 650,000-square-foot facility will raise annual robot output capacity.",
            "FANUC CORPORATION",
        ),
    ],
    ids=["nabtesco", "rockwell", "fanuc"],
)
def test_a_robot_report_item_naming_a_tracked_automation_company_is_attributed(
    title, description, expected_company,
):
    companies, _themes, _tier, decision = _assess(title, description)

    assert companies == (expected_company,)
    assert decision.admitted is True
    assert decision.reason == f"company_subject:{expected_company}"


def test_a_robot_report_item_naming_an_untracked_vendor_is_not_attributed():
    """Negative control: the humanoids alias table must not attribute an
    arbitrary robotics vendor to a tracked company."""
    companies, _themes, _tier, decision = _assess(
        "Agility Robotics Ships Digit Humanoids to a Third Logistics Customer",
        "Agility Robotics said its Digit humanoid is now deployed at a third logistics site.",
    )

    assert companies == ()
    assert not decision.reason.startswith("company_subject:")


# ============================================================
# §5.3 consumer_retail_noise_suppressed_for_robot_report
#
# The retail/consumer gate has extensive coverage already, all of it
# through SourceCategory.INDEPENDENT_NEWS — the same category The Robot
# Report carries, so the mechanism is exercised. What §5.3 asks for is a
# trade-press-shaped item, which is a different false-positive shape
# from the gaming/GPU fixtures the gate was built against.
# ============================================================


def test_a_robot_report_consumer_retail_item_is_suppressed():
    _companies, _themes, _tier, decision = _assess(
        "Best Robot Vacuum Deals: Save $400 on the Roborock S8 MaxV Ultra This Week",
        "Grab a saving on top robot vacuums: the Roborock S8 MaxV Ultra drops to $1,199, down from $1,599, "
        "with more discounts on iRobot models.",
    )

    assert decision.admitted is False
    assert decision.reason.startswith("consumer_editorial_format:")


def test_a_genuine_robot_report_industrial_item_is_not_caught_by_the_retail_gate():
    """Negative control: the suppression above must come from the
    consumer-format shape, not from the source or the word "robot".
    Without this, a gate that rejected everything from this source would
    look correct."""
    _companies, _themes, _tier, decision = _assess(
        "FANUC Opens New Robot Production Facility in Michigan",
        "FANUC America said the 650,000-square-foot facility will raise annual robot output capacity.",
    )

    assert decision.admitted is True
    assert not decision.reason.startswith("consumer_editorial_format:")


# ============================================================
# §5.3 stale_entry_beyond_freshness_window_does_not_publish
#
# The 7x24h gate is a single source-independent check with its own
# boundary coverage in tests/test_daily_news_pipeline.py. One Cohort 1
# exercise is included here because the requirement names these sources
# explicitly; it is not a new gate.
# ============================================================


def test_a_stale_cohort1_item_does_not_publish(tmp_path, monkeypatch):
    stale = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
    _mock_fetch({
        _FIREFLY.feed_url: FeedFetchResult(
            entries=(_entry(
                "Firefly Aerospace Completes Alpha Flight 6",
                "https://fireflyspace.com/news/alpha-flight-6/",
                "Firefly Aerospace completed its sixth Alpha flight.",
                published_at=stale,
            ),),
            failure_code=None,
        ),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_FIREFLY,))

    assert report.stories_published == 0
    assert daily_news_store.load_stories(tmp_path) == {}


def test_the_same_cohort1_item_dated_today_does_publish(tmp_path, monkeypatch):
    """Negative control: proves the suppression above is the freshness
    gate and not something else about this item or source."""
    _mock_fetch({
        _FIREFLY.feed_url: FeedFetchResult(
            entries=(_entry(
                "Firefly Aerospace Completes Alpha Flight 6",
                "https://fireflyspace.com/news/alpha-flight-6/",
                "Firefly Aerospace completed its sixth Alpha flight.",
            ),),
            failure_code=None,
        ),
    }, monkeypatch)

    assert daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_FIREFLY,)).stories_published == 1


# ============================================================
# §5.3 localized_duplicate_yaskawa_newsroom_vs_robot_report
# ------- REQUIREMENT 11: addressed at the presentation layer -------
#
# §5.3 states that a same-event item appearing in both Yaskawa's own
# newsroom feed (Lane A) and The Robot Report (Lane B) is "correctly
# canonicalized to one story, not two". That is NOT the case today and
# this file does not make it so. The requirement stays open pending
# cross-lane story grouping, which does not exist: the editorial
# pipeline dedups only within the editorial store (by normalized URL, or
# by normalized title PLUS publisher) and never reads the issuer store,
# and the page's own _merge_feed_items() is documented as
# presentation-only.
#
# Accepted direction for the eventual fix, recorded here so the test
# below is never mistaken for an endorsement of today's output:
# **presentation-level grouping that preserves every source** — one
# grouped entry in the feed showing both the issuer's own release and
# the independent report, each keeping its own publisher attribution and
# its own outbound link. NOT destructive suppression: no mechanism
# should discard a real publisher or a real source URL, in either
# direction, to make the feed look tidier.
#
# Grouping has since landed at the presentation layer, exactly in that
# shape. The test below therefore no longer characterizes a gap: it pins
# the PERSISTENT fact that both lanes keep their own source record, which
# is precisely what makes display-level grouping safe. The display
# behaviour itself is asserted in
# tests/test_daily_news_cross_lane_grouping.py. Requirement 11 is
# addressed in the combined tree; it is not closed in the shipped
# product until that tree is merged and deployed.
# ============================================================


_SHARED_HEADLINE = "Yaskawa Launches Collaborative Robot MOTOMAN-HC12"
_SHARED_SUMMARY = "Yaskawa Electric launched the MOTOMAN-HC12 collaborative robot with a 12 kg payload."


def test_both_lanes_retain_separate_source_records_for_the_same_event(tmp_path, monkeypatch):
    """The persistent fact, which presentation grouping does not change:
    each lane keeps its OWN source record for the same event — one
    NewsStory and one EditorialStory, each with its own publisher, URL,
    excerpt and timestamp — and the editorial lane's dedup never
    consults the issuer store.

    That is deliberate and is what the grouping work relies on: slots
    are a display concern, so both records must survive untouched. The
    presentation side — that the two records occupy ONE feed slot with
    both publishers and links preserved — is covered in
    tests/test_daily_news_cross_lane_grouping.py, not here.

    Every assertion below is unchanged from when this test was written
    as a characterization of ungrouped output; only its name and this
    docstring were reconciled, because the facts it pins are permanent
    while the wording "is not grouped" described the display, which has
    since changed."""
    _mock_fetch({
        _YASKAWA.feed_url: FeedFetchResult(
            entries=(_entry(
                _SHARED_HEADLINE, "https://www.yaskawa-global.com/newsroom/2026/09/motoman-hc12", _SHARED_SUMMARY,
            ),),
            failure_code=None,
        ),
        _ROBOT_REPORT.canonical_url: FeedFetchResult(
            entries=(_entry(
                _SHARED_HEADLINE, "https://www.therobotreport.com/yaskawa-launches-motoman-hc12/", _SHARED_SUMMARY,
            ),),
            failure_code=None,
        ),
    }, monkeypatch)

    issuer_report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_YASKAWA,))
    editorial_report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_ROBOT_REPORT,))

    assert issuer_report.stories_published == 1
    assert editorial_report.stories_published == 1
    assert editorial_report.items_duplicate == 0  # the editorial lane never consulted the issuer store

    issuer_titles = {s.headline for s in daily_news_store.load_stories(tmp_path).values()}
    editorial_titles = {s.headline for s in editorial_story_store.load_stories(tmp_path).values()}
    assert issuer_titles == {_SHARED_HEADLINE}
    assert editorial_titles == {_SHARED_HEADLINE}


def test_the_editorial_lane_does_dedup_the_same_event_within_its_own_store(tmp_path, monkeypatch):
    """The contrast that makes the characterization above meaningful: the
    dedup that exists works. The same publisher re-emitting the same
    headline is suppressed, so the ungrouped cross-lane output is a
    boundary of scope rather than a broken check — which is also why the
    fix belongs at the presentation layer rather than in either lane's
    own dedup."""
    _mock_fetch({
        _ROBOT_REPORT.canonical_url: FeedFetchResult(
            entries=(_entry(
                _SHARED_HEADLINE, "https://www.therobotreport.com/yaskawa-launches-motoman-hc12/", _SHARED_SUMMARY,
            ),),
            failure_code=None,
        ),
    }, monkeypatch)

    first = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_ROBOT_REPORT,))
    second = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_ROBOT_REPORT,))

    assert first.stories_published == 1
    assert second.stories_published == 0
    assert len(editorial_story_store.load_stories(tmp_path)) == 1


# ============================================================
# Scope: this file adds tests only.
# ============================================================


def test_this_file_only_reads_the_registries_it_exercises():
    """The Cohort 1 entries must not be mutated by anything here — these
    are module-level references to the real registry tuples."""
    assert _EQUINIX in PILOT_FEEDS
    assert _L3HARRIS in PILOT_FEEDS
    assert _FIREFLY in PILOT_FEEDS
    assert _YASKAWA in PILOT_FEEDS
    assert _SPACENEWS in EDITORIAL_SOURCE_REGISTRY
    assert _ROBOT_REPORT in EDITORIAL_SOURCE_REGISTRY
    assert _YASKAWA.language == "English"
    assert _SPACENEWS.allowlisted is True
    assert _ROBOT_REPORT.allowlisted is True
