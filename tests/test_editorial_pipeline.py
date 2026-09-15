"""editorial_pipeline.run_editorial_discovery / select_visible_editorial_stories
— mocked HTTP via monkeypatching rss_atom_client.fetch_entries, zero real
network calls, mirroring tests/test_daily_news_rss_atom_client.py's own
convention. Uses a real temp cache_dir (pytest's tmp_path), never the
production data/cache/ directory."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.data_access.daily_news import editorial_pipeline
from src.data_access.daily_news.editorial_story_store import load_stories
from src.data_access.daily_news.rss_atom_client import FeedFetchResult, RawFeedEntry
from src.data_access.daily_news.source_registry import DailyNewsSourceEntry, SourceCategory, SourceFormat, SourceHealthState
from src.models.daily_news_models import EditorialStory, NewsMaterialityTier

_LICENSING = "Independent journalism test fixture."


def _source(source_id: str = "test-cnbc-rss", attribution_label: str = "CNBC") -> DailyNewsSourceEntry:
    return DailyNewsSourceEntry(
        source_id=source_id, category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=1",
        domains=("www.cnbc.com",), jurisdiction="United States", enabled=True,
        health_state=SourceHealthState.VERIFIED, attribution_label=attribution_label,
        licensing_classification=_LICENSING, priority=1, issuer_agnostic=True, allowlisted=True,
    )


def _entry(
    title: str = "Oracle Corporation reports strong AI cloud demand",
    link: str = "https://www.cnbc.com/2026/09/11/oracle-ai-cloud.html",
    published_at: str | None = None,
    summary: str | None = "Oracle Corporation said AI cloud demand drove revenue higher this quarter.",
) -> RawFeedEntry:
    if published_at is None:
        published_at = datetime.now(timezone.utc).isoformat()
    return RawFeedEntry(title=title, link=link, published_at=published_at, summary=summary)


def _patch_fetch(monkeypatch, results_by_url: dict[str, FeedFetchResult]) -> None:
    def fake_fetch_entries(feed_url: str) -> FeedFetchResult:
        return results_by_url.get(feed_url, FeedFetchResult(entries=(), failure_code=None))

    monkeypatch.setattr(editorial_pipeline.rss_atom_client, "fetch_entries", fake_fetch_entries)


# --- Basic publish path -------------------------------------------------


def test_qualifying_item_is_published(tmp_path, monkeypatch):
    source = _source()
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=(_entry(),), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert report.stories_published == 1
    assert report.source_failures == {}
    stories = load_stories(tmp_path)
    assert len(stories) == 1
    story = next(iter(stories.values()))
    assert story.headline == "Oracle Corporation reports strong AI cloud demand"
    assert story.matched_companies == ("Oracle Corporation",)
    assert story.matched_themes == ("ai-buildout",)
    assert story.publisher == "CNBC"
    assert story.source_url == "https://www.cnbc.com/2026/09/11/oracle-ai-cloud.html"


# --- Fail-closed / no-match ------------------------------------------


def test_no_match_item_is_not_published(tmp_path, monkeypatch):
    source = _source()
    off_topic = _entry(title="Record U.S. cyclosporiasis outbreak is over, CDC says", summary=None)
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=(off_topic,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert report.stories_published == 0
    assert report.items_no_match == 1
    assert load_stories(tmp_path) == {}


# --- URL gate -------------------------------------------------------


def test_off_domain_link_is_rejected(tmp_path, monkeypatch):
    source = _source()
    off_domain = _entry(link="https://www.businesswire.com/news/oracle-ai")
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=(off_domain,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert report.stories_published == 0
    assert report.items_no_valid_url == 1


# --- Freshness (72h) --------------------------------------------------


def test_stale_item_beyond_72_hours_is_excluded(tmp_path, monkeypatch):
    source = _source()
    stale = _entry(published_at=(datetime.now(timezone.utc) - timedelta(hours=73)).isoformat())
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=(stale,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert report.stories_published == 0
    assert report.items_stale == 1


def test_item_within_72_hours_is_included(tmp_path, monkeypatch):
    source = _source()
    fresh = _entry(published_at=(datetime.now(timezone.utc) - timedelta(hours=71)).isoformat())
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=(fresh,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert report.stories_published == 1


# --- Dedup: canonical URL and normalized title + publisher --------------


def test_duplicate_canonical_url_within_one_run_is_deduplicated(tmp_path, monkeypatch):
    source = _source()
    same_url_twice = (_entry(title="Oracle Corporation AI news A"), _entry(title="Oracle Corporation AI news A"))
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=same_url_twice, failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert report.stories_published == 1
    # Second occurrence, same run: caught by the in-run normalized-URL
    # dedup set (the store itself isn't updated mid-run, so
    # items_already_seen — the cross-run story_id-in-store idempotency
    # check — is 0 here; that path is proven separately below).
    assert report.items_duplicate == 1
    assert report.items_already_seen == 0


def test_rediscovering_the_same_item_on_a_second_run_is_idempotent(tmp_path, monkeypatch):
    source = _source()
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=(_entry(),), failure_code=None)})

    first = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))
    second = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert first.stories_published == 1
    assert second.stories_published == 0
    assert second.items_already_seen == 1
    assert len(load_stories(tmp_path)) == 1


def test_duplicate_title_and_publisher_different_url_is_deduplicated(tmp_path, monkeypatch):
    source = _source()
    first = _entry(link="https://www.cnbc.com/2026/09/11/oracle-a.html", title="Oracle Corporation posts AI cloud growth")
    duplicate = _entry(link="https://www.cnbc.com/2026/09/11/oracle-a-amp.html", title="Oracle Corporation posts AI cloud growth")
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=(first, duplicate), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert report.stories_published == 1
    assert report.items_duplicate == 1


def test_same_title_different_publisher_is_not_a_duplicate(tmp_path, monkeypatch):
    cnbc = _source(source_id="cnbc-x", attribution_label="CNBC")
    korea_herald = DailyNewsSourceEntry(
        source_id="kh-x", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.koreaherald.com/rss/kh_Business", domains=("www.koreaherald.com",),
        jurisdiction="South Korea", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="The Korea Herald", licensing_classification=_LICENSING, priority=1,
        issuer_agnostic=True, allowlisted=True,
    )
    shared_title = "Oracle Corporation posts AI cloud growth"
    _patch_fetch(monkeypatch, {
        cnbc.canonical_url: FeedFetchResult(
            entries=(_entry(link="https://www.cnbc.com/a.html", title=shared_title),), failure_code=None,
        ),
        korea_herald.canonical_url: FeedFetchResult(
            entries=(_entry(link="https://www.koreaherald.com/article/1", title=shared_title),), failure_code=None,
        ),
    })

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(cnbc, korea_herald))

    assert report.stories_published == 2
    assert report.items_duplicate == 0


# --- Per-feed persistence cap (max 5 new stories per feed per run) ------


def test_no_more_than_five_stories_persist_from_one_feed_in_one_run(tmp_path, monkeypatch):
    source = _source()
    entries = tuple(
        _entry(
            title=f"Oracle Corporation update {i}",
            link=f"https://www.cnbc.com/2026/09/11/oracle-update-{i}.html",
            published_at=(datetime.now(timezone.utc) - timedelta(hours=i)).isoformat(),
        )
        for i in range(8)  # 8 qualifying entries, one feed, one run
    )
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=entries, failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert report.stories_published == 5
    assert report.items_capped == 3
    stored = load_stories(tmp_path)
    assert len(stored) == 5


def test_per_feed_cap_keeps_the_five_newest_not_an_arbitrary_five(tmp_path, monkeypatch):
    source = _source()
    entries = tuple(
        _entry(
            title=f"Oracle Corporation update {i}",
            link=f"https://www.cnbc.com/2026/09/11/oracle-update-{i}.html",
            published_at=(datetime.now(timezone.utc) - timedelta(hours=i)).isoformat(),
        )
        for i in range(8)  # i=0 is newest (0 hours ago), i=7 is oldest (7 hours ago)
    )
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=entries, failure_code=None)})

    editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    kept_titles = {s.headline for s in load_stories(tmp_path).values()}
    assert kept_titles == {f"Oracle Corporation update {i}" for i in range(5)}  # the 5 newest (i=0..4)


def test_per_feed_cap_is_independent_per_feed_not_shared_across_feeds(tmp_path, monkeypatch):
    cnbc = _source(source_id="cnbc-x", attribution_label="CNBC")
    korea_herald = DailyNewsSourceEntry(
        source_id="kh-x", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.koreaherald.com/rss/kh_Business", domains=("www.koreaherald.com",),
        jurisdiction="South Korea", enabled=True, health_state=SourceHealthState.VERIFIED,
        attribution_label="The Korea Herald", licensing_classification=_LICENSING, priority=1,
        issuer_agnostic=True, allowlisted=True,
    )
    cnbc_entries = tuple(
        _entry(title=f"Oracle Corporation CNBC update {i}", link=f"https://www.cnbc.com/cnbc-{i}.html")
        for i in range(6)
    )
    kh_entries = tuple(
        _entry(title=f"Samsung Electronics update {i}", link=f"https://www.koreaherald.com/kh-{i}")
        for i in range(6)
    )
    _patch_fetch(monkeypatch, {
        cnbc.canonical_url: FeedFetchResult(entries=cnbc_entries, failure_code=None),
        korea_herald.canonical_url: FeedFetchResult(entries=kh_entries, failure_code=None),
    })

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(cnbc, korea_herald))

    assert report.stories_published == 10  # 5 + 5, each feed capped independently
    assert report.items_capped == 2  # 1 dropped from each feed's own 6th entry
    stored = load_stories(tmp_path)
    cnbc_count = sum(1 for s in stored.values() if s.source_feed_id == "cnbc-x")
    kh_count = sum(1 for s in stored.values() if s.source_feed_id == "kh-x")
    assert cnbc_count == 5
    assert kh_count == 5


def test_five_or_fewer_qualifying_entries_are_all_persisted_uncapped(tmp_path, monkeypatch):
    source = _source()
    entries = tuple(
        _entry(title=f"Oracle Corporation update {i}", link=f"https://www.cnbc.com/oracle-{i}.html")
        for i in range(4)
    )
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=entries, failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert report.stories_published == 4
    assert report.items_capped == 0


# --- Failure isolation --------------------------------------------------


def test_one_source_failure_does_not_block_another_source(tmp_path, monkeypatch):
    good = _source(source_id="good-rss")
    bad = _source(source_id="bad-rss")
    bad_url = bad.canonical_url.replace("id=1", "id=2")
    bad_entry = DailyNewsSourceEntry(
        source_id="bad-rss", category=SourceCategory.INDEPENDENT_NEWS, format=SourceFormat.RSS_ATOM,
        canonical_url=bad_url, domains=("www.cnbc.com",), jurisdiction="United States", enabled=True,
        health_state=SourceHealthState.VERIFIED, attribution_label="CNBC", licensing_classification=_LICENSING,
        priority=1, issuer_agnostic=True, allowlisted=True,
    )
    _patch_fetch(monkeypatch, {
        good.canonical_url: FeedFetchResult(entries=(_entry(),), failure_code=None),
        bad_url: FeedFetchResult(entries=(), failure_code="ConnectionError"),
    })

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(good, bad_entry))

    assert report.stories_published == 1
    assert report.source_failures == {"bad-rss": "ConnectionError"}


# --- Excerpt / no fabricated fallback ------------------------------------


def test_missing_description_omits_excerpt_entirely_no_fallback(tmp_path, monkeypatch):
    source = _source()
    no_desc = _entry(summary=None)
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=(no_desc,), failure_code=None)})

    editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    story = next(iter(load_stories(tmp_path).values()))
    assert story.excerpt is None


def test_unusable_html_only_description_omits_excerpt(tmp_path, monkeypatch):
    source = _source()
    html_only = _entry(summary="<div></div>")
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=(html_only,), failure_code=None)})

    editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    story = next(iter(load_stories(tmp_path).values()))
    assert story.excerpt is None


def test_present_description_produces_a_real_extractive_excerpt(tmp_path, monkeypatch):
    source = _source()
    with_desc = _entry(summary="<p>Oracle Corporation said AI cloud demand drove revenue higher.</p>")
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=(with_desc,), failure_code=None)})

    editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    story = next(iter(load_stories(tmp_path).values()))
    assert story.excerpt == "Oracle Corporation said AI cloud demand drove revenue higher."
    assert "<p>" not in story.excerpt


# --- select_visible_editorial_stories: freshness/caps ---------------------


def _story(
    story_id: str, source_feed_id: str, hours_ago: float, headline: str = "Oracle Corporation AI news",
    matched_companies: tuple[str, ...] = ("Oracle Corporation",),
) -> EditorialStory:
    published_at = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return EditorialStory(
        id=story_id, headline=headline, publisher="CNBC", source_url=f"https://www.cnbc.com/{story_id}.html",
        published_at=published_at, retrieved_at=published_at, excerpt=None,
        matched_companies=matched_companies, matched_themes=(), source_feed_id=source_feed_id,
    )


def test_select_visible_excludes_stories_older_than_72_hours():
    stories = {
        "a": _story("a", "src-1", hours_ago=1),
        "b": _story("b", "src-1", hours_ago=73),
    }
    visible = editorial_pipeline.select_visible_editorial_stories(stories)
    assert [s.id for s in visible] == ["a"]


def test_select_visible_caps_at_five_per_source():
    stories = {f"s{i}": _story(f"s{i}", "src-1", hours_ago=i) for i in range(8)}
    visible = editorial_pipeline.select_visible_editorial_stories(stories)
    assert len(visible) == 5
    assert [s.id for s in visible] == ["s0", "s1", "s2", "s3", "s4"]  # newest (smallest hours_ago) first


def test_select_visible_caps_at_twenty_total_across_sources():
    stories = {}
    for source_n in range(6):  # 6 sources * 5 each = 30 candidates, capped to 20 total
        for i in range(5):
            key = f"src{source_n}-{i}"
            stories[key] = _story(key, f"src-{source_n}", hours_ago=source_n * 10 + i)
    visible = editorial_pipeline.select_visible_editorial_stories(stories)
    assert len(visible) == 20


def test_select_visible_sorts_newest_first():
    stories = {
        "old": _story("old", "src-1", hours_ago=10),
        "new": _story("new", "src-1", hours_ago=1),
    }
    visible = editorial_pipeline.select_visible_editorial_stories(stories)
    assert [s.id for s in visible] == ["new", "old"]


def test_select_visible_empty_store_returns_empty_tuple():
    assert editorial_pipeline.select_visible_editorial_stories({}) == ()


# ============================================================
# System-wide company-matched-news fix (design/DECISIONS.md) —
# select_visible_editorial_stories_for_company(): the per-company
# counterpart, queried against the FULL persisted store, never the
# cross-company-capped select_visible_editorial_stories() output above.
# Generic — no company-specific test setup beyond the company_name
# argument itself.
# ============================================================


def test_for_company_filters_by_matched_companies_membership():
    stories = {
        "a": _story("a", "src-1", hours_ago=1, matched_companies=("Oracle Corporation",)),
        "b": _story("b", "src-1", hours_ago=1, matched_companies=("Intel Corp.",)),
    }
    visible = editorial_pipeline.select_visible_editorial_stories_for_company(stories, "Oracle Corporation")
    assert [s.id for s in visible] == ["a"]


def test_for_company_excludes_stories_older_than_72_hours():
    stories = {
        "a": _story("a", "src-1", hours_ago=1),
        "b": _story("b", "src-1", hours_ago=73),
    }
    visible = editorial_pipeline.select_visible_editorial_stories_for_company(stories, "Oracle Corporation")
    assert [s.id for s in visible] == ["a"]


def test_for_company_still_caps_at_five_per_source():
    stories = {
        f"s{i}": _story(f"s{i}", "src-1", hours_ago=i, matched_companies=("Oracle Corporation",))
        for i in range(8)
    }
    visible = editorial_pipeline.select_visible_editorial_stories_for_company(stories, "Oracle Corporation")
    assert len(visible) == 5
    assert [s.id for s in visible] == ["s0", "s1", "s2", "s3", "s4"]  # newest (smallest hours_ago) first


def test_for_company_sorts_newest_first():
    stories = {
        "old": _story("old", "src-1", hours_ago=10),
        "new": _story("new", "src-1", hours_ago=1),
    }
    visible = editorial_pipeline.select_visible_editorial_stories_for_company(stories, "Oracle Corporation")
    assert [s.id for s in visible] == ["new", "old"]


def test_for_company_never_applies_the_cross_company_total_cap():
    # The core fix (requirement 1): a story matched to "Oracle
    # Corporation" ranked outside the shared top-20 (25 unrelated,
    # newer stories from 5 other companies/sources crowd it out of
    # select_visible_editorial_stories()) must still appear for its own
    # company via select_visible_editorial_stories_for_company().
    stories = {
        "oracle-story": _story(
            "oracle-story", "src-oracle", hours_ago=50, matched_companies=("Oracle Corporation",),
        ),
    }
    for source_n in range(5):
        for i in range(5):  # 5 sources * 5 each = 25 newer, unrelated stories
            key = f"crowd-{source_n}-{i}"
            stories[key] = _story(
                key, f"src-crowd-{source_n}", hours_ago=i, matched_companies=("Intel Corp.",),
            )

    # Confirm the crowding actually happens against the unmodified,
    # cross-company-capped selection — Oracle's story is excluded there.
    global_visible = editorial_pipeline.select_visible_editorial_stories(stories)
    assert len(global_visible) == 20
    assert "oracle-story" not in {s.id for s in global_visible}

    # But it still appears for its own company, un-crowded-out.
    company_visible = editorial_pipeline.select_visible_editorial_stories_for_company(stories, "Oracle Corporation")
    assert [s.id for s in company_visible] == ["oracle-story"]


def test_for_company_multi_company_story_appears_for_every_matched_company():
    stories = {
        "joint": _story(
            "joint", "src-1", hours_ago=1, headline="Amazon and Google both announced new AI infrastructure",
            matched_companies=("Amazon.com, Inc.", "Alphabet Inc."),
        ),
    }
    amazon_visible = editorial_pipeline.select_visible_editorial_stories_for_company(stories, "Amazon.com, Inc.")
    google_visible = editorial_pipeline.select_visible_editorial_stories_for_company(stories, "Alphabet Inc.")
    assert [s.id for s in amazon_visible] == ["joint"]
    assert [s.id for s in google_visible] == ["joint"]
    unrelated_visible = editorial_pipeline.select_visible_editorial_stories_for_company(stories, "Intel Corp.")
    assert unrelated_visible == ()


def test_for_company_empty_store_returns_empty_tuple():
    assert editorial_pipeline.select_visible_editorial_stories_for_company({}, "Oracle Corporation") == ()


def test_for_company_unmatched_company_returns_empty_tuple_not_an_error():
    stories = {"a": _story("a", "src-1", hours_ago=1, matched_companies=("Oracle Corporation",))}
    assert editorial_pipeline.select_visible_editorial_stories_for_company(stories, "Intel Corp.") == ()


def test_global_select_visible_is_unaffected_by_the_new_per_company_function():
    # Regression: select_visible_editorial_stories() itself (the "All
    # companies" path) must stay byte-for-byte unchanged — same 72h
    # freshness, same 5-per-source cap, same 20-total cap — proven again
    # here alongside the new function's own tests for direct comparison.
    stories = {}
    for source_n in range(6):
        for i in range(5):
            key = f"src{source_n}-{i}"
            stories[key] = _story(key, f"src-{source_n}", hours_ago=source_n * 10 + i)
    visible = editorial_pipeline.select_visible_editorial_stories(stories)
    assert len(visible) == 20


# ============================================================
# Government / Public Sector Daily News lane (design/DECISIONS.md) —
# _SPACEFORCE_SOURCE_ID bypasses the company/theme gate entirely;
# _NIST_SOURCE_ID is gated by _matches_nist_allow_list() instead of
# matched_companies_and_themes(). Every other source_id (including a
# plain CNBC/Korea Herald one) keeps the exact existing gate — proven
# by test_no_match_item_is_not_published above, which is untouched by
# this batch, plus the mixed-run test below.
# ============================================================

_GOVERNMENT_LICENSING = "U.S. federal government work test fixture."


def _spaceforce_source(source_id: str = "spaceforce-news-rss") -> DailyNewsSourceEntry:
    return DailyNewsSourceEntry(
        source_id=source_id, category=SourceCategory.GOVERNMENT_POLICY, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.spaceforce.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=1060&max=10",
        domains=("www.spaceforce.mil",), jurisdiction="United States", enabled=True,
        health_state=SourceHealthState.VERIFIED, attribution_label="U.S. Space Force",
        licensing_classification=_GOVERNMENT_LICENSING, priority=1, issuer_agnostic=True,
    )


def _nist_source(source_id: str = "nist-news-rss") -> DailyNewsSourceEntry:
    return DailyNewsSourceEntry(
        source_id=source_id, category=SourceCategory.GOVERNMENT_POLICY, format=SourceFormat.RSS_ATOM,
        canonical_url="https://www.nist.gov/news-events/news/rss.xml",
        domains=("www.nist.gov",), jurisdiction="United States", enabled=True,
        health_state=SourceHealthState.VERIFIED,
        attribution_label="National Institute of Standards and Technology (NIST)",
        licensing_classification=_GOVERNMENT_LICENSING, priority=1, issuer_agnostic=True,
    )


def test_spaceforce_item_with_no_company_or_theme_match_still_publishes(tmp_path, monkeypatch):
    source = _spaceforce_source()
    no_match_item = _entry(
        title="US Space Force selects Texas as preferred location for third DARC site",
        link="https://www.spaceforce.mil/News/Article-Display/Article/4592096/darc-texas/",
        summary="The USSF has approved a preferred alternative for its third Deep Space Advanced Radar Capability site.",
    )
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=(no_match_item,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert report.stories_published == 1
    assert report.items_no_match == 0
    story = next(iter(load_stories(tmp_path).values()))
    assert story.matched_companies == ()
    assert story.matched_themes == ()
    assert story.source_feed_id == "spaceforce-news-rss"


def test_spaceforce_stale_item_is_still_excluded(tmp_path, monkeypatch):
    source = _spaceforce_source()
    stale = _entry(
        title="US Space Force selects Texas as preferred location for third DARC site",
        link="https://www.spaceforce.mil/News/Article-Display/Article/4592096/darc-texas/",
        published_at=(datetime.now(timezone.utc) - timedelta(hours=73)).isoformat(),
    )
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=(stale,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert report.stories_published == 0
    assert report.items_stale == 1


def test_spaceforce_off_domain_link_is_still_rejected(tmp_path, monkeypatch):
    source = _spaceforce_source()
    off_domain = _entry(
        title="US Space Force selects Texas as preferred location for third DARC site",
        link="https://example.com/not-spaceforce",
    )
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=(off_domain,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert report.stories_published == 0
    assert report.items_no_valid_url == 1


def test_spaceforce_duplicate_within_one_run_is_still_deduplicated(tmp_path, monkeypatch):
    source = _spaceforce_source()
    item = _entry(
        title="US Space Force selects Texas as preferred location for third DARC site",
        link="https://www.spaceforce.mil/News/Article-Display/Article/4592096/darc-texas/",
    )
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=(item, item), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert report.stories_published == 1
    assert report.items_duplicate == 1


def test_spaceforce_failed_fetch_is_recorded_and_never_published(tmp_path, monkeypatch):
    source = _spaceforce_source()
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=(), failure_code="HTTPError:503")})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert report.stories_published == 0
    assert report.source_failures == {"spaceforce-news-rss": "HTTPError:503"}


def test_nist_item_matching_allow_list_publishes(tmp_path, monkeypatch):
    source = _nist_source()
    matching = _entry(
        title="NIST Awards Funding to Advance Domestic Semiconductor Manufacturing",
        link="https://www.nist.gov/news-events/news/2026/09/chips-funding",
        summary="The award, made under the CHIPS Act, supports new semiconductor fabrication capacity.",
    )
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=(matching,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert report.stories_published == 1
    assert report.items_no_match == 0
    story = next(iter(load_stories(tmp_path).values()))
    assert story.source_feed_id == "nist-news-rss"


def test_nist_off_topic_item_does_not_publish(tmp_path, monkeypatch):
    source = _nist_source()
    off_topic = _entry(
        title="NIST-Developed Quantum Sensors Improve Nuclear Monitoring",
        link="https://www.nist.gov/news-events/news/2026/09/quantum-sensors",
        summary="New X-ray measurements will allow scientists to more accurately monitor nuclear material.",
    )
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=(off_topic,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert report.stories_published == 0
    assert report.items_no_match == 1
    assert load_stories(tmp_path) == {}


def test_nist_stale_matching_item_is_still_excluded(tmp_path, monkeypatch):
    source = _nist_source()
    stale = _entry(
        title="NIST Awards Funding to Advance Domestic Semiconductor Manufacturing",
        link="https://www.nist.gov/news-events/news/2026/09/chips-funding",
        published_at=(datetime.now(timezone.utc) - timedelta(hours=73)).isoformat(),
    )
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=(stale,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert report.stories_published == 0
    assert report.items_stale == 1


def test_nist_off_domain_matching_item_is_still_rejected(tmp_path, monkeypatch):
    source = _nist_source()
    off_domain = _entry(
        title="NIST Awards Funding to Advance Domestic Semiconductor Manufacturing",
        link="https://example.com/not-nist",
    )
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=(off_domain,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert report.stories_published == 0
    assert report.items_no_valid_url == 1


def test_nist_duplicate_within_one_run_is_still_deduplicated(tmp_path, monkeypatch):
    source = _nist_source()
    item = _entry(
        title="NIST Awards Funding to Advance Domestic Semiconductor Manufacturing",
        link="https://www.nist.gov/news-events/news/2026/09/chips-funding",
    )
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=(item, item), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert report.stories_published == 1
    assert report.items_duplicate == 1


def test_mixed_run_cnbc_gate_is_unaffected_by_government_sources(tmp_path, monkeypatch):
    # Proves the CNBC (INDEPENDENT_NEWS) source_id still goes through
    # the exact, unmodified company-or-theme gate in the same run that
    # also processes Space Force (always-eligible) and NIST (allow-list
    # gated) sources — the three eligibility paths never interfere.
    cnbc = _source()
    spaceforce = _spaceforce_source()
    nist = _nist_source()

    cnbc_off_topic = _entry(title="Record U.S. cyclosporiasis outbreak is over, CDC says", summary=None)
    spaceforce_item = _entry(
        title="US Space Force selects Texas as preferred location for third DARC site",
        link="https://www.spaceforce.mil/News/Article-Display/Article/4592096/darc-texas/",
    )
    nist_off_topic = _entry(
        title="NIST-Developed Quantum Sensors Improve Nuclear Monitoring",
        link="https://www.nist.gov/news-events/news/2026/09/quantum-sensors",
    )
    _patch_fetch(monkeypatch, {
        cnbc.canonical_url: FeedFetchResult(entries=(cnbc_off_topic,), failure_code=None),
        spaceforce.canonical_url: FeedFetchResult(entries=(spaceforce_item,), failure_code=None),
        nist.canonical_url: FeedFetchResult(entries=(nist_off_topic,), failure_code=None),
    })

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(cnbc, spaceforce, nist))

    assert report.stories_published == 1  # only the Space Force item
    assert report.items_no_match == 2  # the CNBC and NIST off-topic items
    stories = load_stories(tmp_path)
    assert len(stories) == 1
    assert next(iter(stories.values())).source_feed_id == "spaceforce-news-rss"


# ============================================================
# Daily News source-expansion batch 2, editorial lane (2026-09-13) — 23
# more issuer_agnostic=True editorial sources (US independent/trade
# press, The Register's shared-attribution pair, PR Newswire's
# shared-attribution pair, US federal regulators, Japan, South Korea).
# These grouped smoke tests use the REAL, registry-derived
# EDITORIAL_SOURCE_REGISTRY_BATCH_2 entries (not synthetic fixtures) so
# a mistake in the registry's own fields (wrong domain, wrong category,
# wrong attribution_label) is caught here too — the pipeline gate logic
# itself is unchanged and already exhaustively proven above; one
# representative smoke test per new lane/publisher pattern is enough to
# prove each pattern wires through it correctly, matching this file's
# own existing CNBC/Korea Herald/Space Force/NIST precedent.
# ============================================================

from src.data_access.daily_news.source_registry import EDITORIAL_SOURCE_REGISTRY_BATCH_2

_MATCHING_TITLE = "Oracle Corporation reports strong AI cloud demand"
_MATCHING_SUMMARY = "Oracle Corporation said AI cloud demand drove revenue higher this quarter."


def _batch_2_source(source_id: str) -> DailyNewsSourceEntry:
    return next(e for e in EDITORIAL_SOURCE_REGISTRY_BATCH_2 if e.source_id == source_id)


def test_techcrunch_matching_item_publishes_with_correct_attribution_and_domain(tmp_path, monkeypatch):
    # Representative of the US independent/trade-press pattern shared by
    # techcrunch-rss, ars-technica-rss, the-verge-rss,
    # semiconductor-engineering-rss, ieee-spectrum-rss,
    # data-center-frontier-rss, toms-hardware-rss, supply-chain-dive-rss,
    # utility-dive-rss — every one an INDEPENDENT_NEWS, issuer_agnostic,
    # allowlisted source subject to the same fail-closed matching gate.
    source = _batch_2_source("techcrunch-rss")
    entry = _entry(
        title=_MATCHING_TITLE, link="https://techcrunch.com/2026/09/13/oracle-ai-cloud/", summary=_MATCHING_SUMMARY,
    )
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert report.stories_published == 1
    story = next(iter(load_stories(tmp_path).values()))
    assert story.publisher == "TechCrunch"
    assert story.source_feed_id == "techcrunch-rss"


def test_techcrunch_off_topic_item_does_not_publish(tmp_path, monkeypatch):
    source = _batch_2_source("techcrunch-rss")
    entry = _entry(title="A roundup of the week's best deals", link="https://techcrunch.com/deals", summary=None)
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert report.stories_published == 0
    assert report.items_no_match == 1


def test_the_register_pair_shares_attribution_and_cross_feed_deduplicates(tmp_path, monkeypatch):
    # The Register's two feeds (headlines + On Prem) share one
    # attribution_label ("The Register") specifically so a story synced
    # to both sections dedups as one publisher, not two — same proof
    # test_same_title_different_publisher_is_not_a_duplicate above gives
    # for two genuinely DIFFERENT publishers, mirrored here for the
    # SAME publisher across two feeds.
    headlines = _batch_2_source("the-register-headlines-rss")
    on_prem = _batch_2_source("the-register-on-prem-rss")
    assert headlines.attribution_label == on_prem.attribution_label == "The Register"

    shared_title = "Oracle Corporation expands AI cloud datacenter footprint"
    _patch_fetch(monkeypatch, {
        headlines.canonical_url: FeedFetchResult(
            entries=(_entry(title=shared_title, link="https://www.theregister.com/2026/09/13/oracle_a/", summary=_MATCHING_SUMMARY),),
            failure_code=None,
        ),
        on_prem.canonical_url: FeedFetchResult(
            entries=(_entry(title=shared_title, link="https://www.theregister.com/on_prem/2026/09/13/oracle_b/", summary=_MATCHING_SUMMARY),),
            failure_code=None,
        ),
    })

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(headlines, on_prem))

    assert report.stories_published == 1
    assert report.items_duplicate == 1
    story = next(iter(load_stories(tmp_path).values()))
    assert story.publisher == "The Register"


def test_pr_newswire_pair_shares_attribution_and_cross_feed_deduplicates(tmp_path, monkeypatch):
    # PR Newswire's two feeds (general + financial-services) share one
    # attribution_label ("PR Newswire") for the same reason as The
    # Register pair above.
    general = _batch_2_source("pr-newswire-general-rss")
    financial = _batch_2_source("pr-newswire-financial-services-rss")
    assert general.attribution_label == financial.attribution_label == "PR Newswire"

    shared_title = "Oracle Corporation Announces Strategic AI Cloud Partnership"
    _patch_fetch(monkeypatch, {
        general.canonical_url: FeedFetchResult(
            entries=(_entry(title=shared_title, link="https://www.prnewswire.com/news-releases/oracle-a-302000001.html", summary=_MATCHING_SUMMARY),),
            failure_code=None,
        ),
        financial.canonical_url: FeedFetchResult(
            entries=(_entry(title=shared_title, link="https://www.prnewswire.com/news-releases/oracle-b-302000002.html", summary=_MATCHING_SUMMARY),),
            failure_code=None,
        ),
    })

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(general, financial))

    assert report.stories_published == 1
    assert report.items_duplicate == 1
    story = next(iter(load_stories(tmp_path).values()))
    assert story.publisher == "PR Newswire"


def test_sec_regulator_matching_item_publishes_with_correct_attribution(tmp_path, monkeypatch):
    # Representative of the US-federal-regulator pattern shared by
    # sec-press-releases-rss, federal-reserve-press-rss,
    # ftc-press-releases-rss — SourceCategory.REGULATOR, issuer_agnostic,
    # no allowlisted flag, same fail-closed matching gate.
    source = _batch_2_source("sec-press-releases-rss")
    assert source.allowlisted is False
    entry = _entry(
        title=_MATCHING_TITLE, link="https://www.sec.gov/newsroom/press-releases/2026-100-oracle", summary=_MATCHING_SUMMARY,
    )
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert report.stories_published == 1
    story = next(iter(load_stories(tmp_path).values()))
    assert story.publisher == "U.S. Securities and Exchange Commission (SEC)"
    assert story.source_feed_id == "sec-press-releases-rss"


def test_japan_times_matching_item_publishes_with_correct_attribution(tmp_path, monkeypatch):
    # Representative of the Japan lane, shared with jpx-market-news-rss
    # (SourceCategory.EXCHANGE) and fsa-japan-news-rss
    # (SourceCategory.REGULATOR) — same fail-closed matching gate.
    source = _batch_2_source("japan-times-rss")
    entry = _entry(
        title=_MATCHING_TITLE, link="https://www.japantimes.co.jp/business/2026/09/13/oracle-ai-cloud/",
        summary=_MATCHING_SUMMARY,
    )
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert report.stories_published == 1
    story = next(iter(load_stories(tmp_path).values()))
    assert story.publisher == "The Japan Times"
    assert story.source_feed_id == "japan-times-rss"


def test_jpx_exchange_matching_item_publishes_with_correct_attribution(tmp_path, monkeypatch):
    source = _batch_2_source("jpx-market-news-rss")
    assert source.category == SourceCategory.EXCHANGE
    assert source.allowlisted is False
    entry = _entry(
        title=_MATCHING_TITLE, link="https://www.jpx.co.jp/english/news/oracle", summary=_MATCHING_SUMMARY,
    )
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert report.stories_published == 1
    story = next(iter(load_stories(tmp_path).values()))
    assert story.publisher == "Japan Exchange Group (JPX)"


def test_yonhap_matching_item_publishes_with_correct_attribution(tmp_path, monkeypatch):
    # Representative of the South Korea lane, shared with
    # korea-times-rss, korea-it-times-rss, thelec-rss.
    source = _batch_2_source("yonhap-news-rss")
    entry = _entry(
        title=_MATCHING_TITLE, link="https://en.yna.co.kr/view/AEN20260913001500999", summary=_MATCHING_SUMMARY,
    )
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert report.stories_published == 1
    story = next(iter(load_stories(tmp_path).values()))
    assert story.publisher == "Yonhap News Agency"
    assert story.source_feed_id == "yonhap-news-rss"


def test_thelec_off_domain_item_is_still_rejected(tmp_path, monkeypatch):
    # Proves the existing canonical_url gate applies unchanged to a new
    # source too, even one whose own feed <language> tag misreports
    # "ko" for genuinely English content (see this source's own registry
    # notes) — ingestion never filters on that tag either way.
    source = _batch_2_source("thelec-rss")
    entry = _entry(title=_MATCHING_TITLE, link="https://example.com/not-thelec", summary=_MATCHING_SUMMARY)
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert report.stories_published == 0
    assert report.items_no_valid_url == 1


def test_batch_2_sources_are_never_leaked_into_runtime_source_registry():
    from src.data_access.daily_news.source_registry import RUNTIME_SOURCE_REGISTRY

    batch_2_ids = {e.source_id for e in EDITORIAL_SOURCE_REGISTRY_BATCH_2}
    runtime_ids = {e.source_id for e in RUNTIME_SOURCE_REGISTRY}
    assert not (batch_2_ids & runtime_ids)


# --- Signals materiality classification (design/DECISIONS.md) ---


def test_published_editorial_story_is_classified_at_construction_time(tmp_path, monkeypatch):
    """The default _entry() fixture ("Oracle Corporation reports strong
    AI cloud demand" / "...AI cloud demand drove revenue higher this
    quarter") is on-taxonomy (AI cloud) with a materiality anchor
    ("revenue") — High Signal via the taxonomy-anchored-consequence
    gate. Proves classify_editorial_story() is actually wired into
    run_editorial_discovery(), not just present and untested."""
    source = _source()
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=(_entry(),), failure_code=None)})

    editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    story = next(iter(load_stories(tmp_path).values()))
    assert story.materiality_tier == NewsMaterialityTier.HIGH_SIGNAL
    assert any(r.startswith("taxonomy_anchored_consequence:") for r in story.materiality_reasons)


def test_incidental_ambiguous_alias_backer_mention_is_not_admitted(tmp_path, monkeypatch):
    """Superseded by the precision-first admission gate (design/DECISIONS.md,
    the Nintendo/Amazon false-positive audit): this test previously asserted
    that a company-matched, off-taxonomy, zero-anchor item always publishes
    regardless of classification — i.e. that admission and tiering are fully
    independent. That was exactly the design flaw behind the Nintendo/Amazon
    bug. "Oracle" here is an ambiguous mechanical alias (see
    editorial_admission.py's _AMBIGUOUS_ALIAS_COMPANIES) matched only as an
    incidental backer mention, with no company-action language and no hard
    material evidence — the item must now fail admission entirely rather
    than publish to Background."""
    source = _source()
    entry = _entry(
        title="Oracle-linked startup begins limited electric truck pilot in California",
        summary="A small logistics startup backed partly by Oracle is testing a handful of electric delivery trucks.",
    )
    _patch_fetch(monkeypatch, {source.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert report.stories_published == 0
    assert report.items_not_subject_relevant == 1
    assert load_stories(tmp_path) == {}


# ============================================================
# Daily News source-expansion batch 3, Phase 1 activation (2026-09-15) —
# Data Center Dynamics, added from the read-only US/Japan/Korea source
# audit. Uses the REAL, registry-derived EDITORIAL_SOURCE_REGISTRY_
# BATCH_3 entry (not a synthetic fixture) so a mistake in the registry's
# own fields is caught here too — same discipline as the batch 2 section
# above. METI's Japan Atom feed (the audit's other candidate) was
# re-verified and excluded this same batch for staleness (see
# source_registry.py's own EDITORIAL_SOURCE_REGISTRY_BATCH_3 comment) —
# not added, so no test exists for it.
# ============================================================

from src.data_access.daily_news.source_registry import EDITORIAL_SOURCE_REGISTRY_BATCH_3

_dcd = next(e for e in EDITORIAL_SOURCE_REGISTRY_BATCH_3 if e.source_id == "data-center-dynamics-rss")


def test_dcd_registry_entry_has_the_expected_fields():
    """Parsing/fetch prerequisites and source/terms standards, verified
    against the actual registry entry the pipeline reads — not a
    duplicate literal that could silently drift from it."""
    assert _dcd.canonical_url == "https://www.datacenterdynamics.com/en/rss/"
    assert _dcd.domains == ("www.datacenterdynamics.com",)
    assert _dcd.format == SourceFormat.RSS_ATOM
    assert _dcd.category == SourceCategory.INDEPENDENT_NEWS
    assert _dcd.jurisdiction == "United Kingdom"
    assert _dcd.issuer_agnostic is True
    assert _dcd.allowlisted is True  # required for INDEPENDENT_NEWS — see validate_source_entry
    assert _dcd.attribution_label == "Data Center Dynamics"
    assert _dcd.licensing_classification  # non-empty; independent-journalism, no-full-reproduction policy


def test_dcd_matching_item_publishes_with_correct_attribution_and_canonical_link(tmp_path, monkeypatch):
    """Parsing + source attribution + canonical original link, using a
    realistic DCD-shaped item — a hyperscaler capex story naming a
    tracked company, matching DCD's real editorial beat."""
    entry = _entry(
        title="Amazon announces new AWS data center campus expansion",
        link="https://www.datacenterdynamics.com/en/news/amazon-announces-new-aws-data-center-campus-expansion/",
        summary="Amazon confirmed a new multi-billion-dollar AWS data center campus, adding capacity for AI workloads.",
    )
    _patch_fetch(monkeypatch, {_dcd.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_dcd,))

    assert report.stories_published == 1
    story = next(iter(load_stories(tmp_path).values()))
    assert story.publisher == "Data Center Dynamics"
    assert story.source_feed_id == "data-center-dynamics-rss"
    assert story.source_url == entry.link  # canonical original link preserved verbatim, never rewritten


def test_dcd_off_domain_link_is_rejected(tmp_path, monkeypatch):
    """Canonical-link validation: an item whose link resolves off DCD's
    own declared domain must never publish, regardless of content."""
    entry = _entry(
        title="Amazon announces new AWS data center campus expansion",
        link="https://not-datacenterdynamics.example.com/amazon-aws",
        summary="Amazon confirmed a new multi-billion-dollar AWS data center campus.",
    )
    _patch_fetch(monkeypatch, {_dcd.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_dcd,))

    assert report.stories_published == 0
    assert report.items_no_valid_url == 1


def test_dcd_stale_item_beyond_72_hours_is_excluded(tmp_path, monkeypatch):
    """Update-timestamp handling: an item published outside the 72-hour
    freshness window must never publish, even if otherwise qualifying —
    this is the exact gate that disqualified METI's own feed this same
    batch (see source_registry.py's own rejection note)."""
    stale_at = (datetime.now(timezone.utc) - timedelta(hours=200)).isoformat()
    entry = _entry(
        title="Amazon announces new AWS data center campus expansion",
        link="https://www.datacenterdynamics.com/en/news/amazon-announces-new-aws-data-center-campus-expansion/",
        published_at=stale_at,
        summary="Amazon confirmed a new multi-billion-dollar AWS data center campus.",
    )
    _patch_fetch(monkeypatch, {_dcd.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_dcd,))

    assert report.stories_published == 0
    assert report.items_stale == 1


def test_dcd_item_within_72_hours_is_included(tmp_path, monkeypatch):
    fresh_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    entry = _entry(
        title="Amazon announces new AWS data center campus expansion",
        link="https://www.datacenterdynamics.com/en/news/amazon-announces-new-aws-data-center-campus-expansion/",
        published_at=fresh_at,
        summary="Amazon confirmed a new multi-billion-dollar AWS data center campus.",
    )
    _patch_fetch(monkeypatch, {_dcd.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_dcd,))

    assert report.stories_published == 1


def test_dcd_duplicate_within_one_run_is_deduplicated(tmp_path, monkeypatch):
    entry = _entry(
        title="Amazon announces new AWS data center campus expansion",
        link="https://www.datacenterdynamics.com/en/news/amazon-announces-new-aws-data-center-campus-expansion/",
        summary="Amazon confirmed a new multi-billion-dollar AWS data center campus.",
    )
    _patch_fetch(monkeypatch, {_dcd.canonical_url: FeedFetchResult(entries=(entry, entry), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_dcd,))

    assert report.stories_published == 1
    assert report.items_duplicate == 1


def test_dcd_rediscovering_the_same_item_on_a_second_run_is_idempotent(tmp_path, monkeypatch):
    entry = _entry(
        title="Amazon announces new AWS data center campus expansion",
        link="https://www.datacenterdynamics.com/en/news/amazon-announces-new-aws-data-center-campus-expansion/",
        summary="Amazon confirmed a new multi-billion-dollar AWS data center campus.",
    )
    _patch_fetch(monkeypatch, {_dcd.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})
    first = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_dcd,))
    second = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_dcd,))

    assert first.stories_published == 1
    assert second.stories_published == 0
    assert second.items_already_seen == 1


def test_dcd_off_topic_item_does_not_publish(tmp_path, monkeypatch):
    """No company/theme match at all — the pre-existing fail-closed gate,
    distinct from the admission gate below."""
    entry = _entry(
        title="Five conference talks worth catching this year",
        link="https://www.datacenterdynamics.com/en/opinions/five-conference-talks-worth-catching/",
        summary="Our picks for the most interesting sessions at this year's industry conferences.",
    )
    _patch_fetch(monkeypatch, {_dcd.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_dcd,))

    assert report.stories_published == 0
    assert report.items_no_match == 1


def test_dcd_consumer_format_item_naming_a_company_fails_the_admission_gate(tmp_path, monkeypatch):
    """Rejection under the precision-first admission gate specifically
    (editorial_admission.py), distinct from items_no_match: this item
    DOES match a tracked company (NVIDIA) but is a consumer buying-guide
    format, not a genuine corporate development — must fail admission,
    not merely be demoted."""
    entry = _entry(
        title="Best NVIDIA GPUs Under $500 for Your Home Lab",
        link="https://www.datacenterdynamics.com/en/opinions/best-nvidia-gpus-under-500-for-your-home-lab/",
        summary="We rounded up the best NVIDIA graphics cards you can buy for under $500 right now.",
    )
    _patch_fetch(monkeypatch, {_dcd.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_dcd,))

    assert report.stories_published == 0
    assert report.items_not_subject_relevant == 1
    assert load_stories(tmp_path) == {}


def test_dcd_failed_fetch_is_recorded_and_never_published(tmp_path, monkeypatch):
    """Fail-closed behavior on an unavailable feed: a fetch failure is
    recorded in source_failures, zero stories publish, and the run
    completes without raising."""
    _patch_fetch(monkeypatch, {_dcd.canonical_url: FeedFetchResult(entries=(), failure_code="HTTPError:503")})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_dcd,))

    assert report.stories_published == 0
    assert report.source_failures == {"data-center-dynamics-rss": "HTTPError:503"}


def test_dcd_malformed_entry_missing_title_is_rejected_not_published(tmp_path, monkeypatch):
    """Fail-closed behavior on a malformed entry within an otherwise-
    available feed: an entry with no title must never publish."""
    entry = _entry(
        title="",
        link="https://www.datacenterdynamics.com/en/news/untitled/",
        summary="Amazon confirmed a new multi-billion-dollar AWS data center campus.",
    )
    _patch_fetch(monkeypatch, {_dcd.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_dcd,))

    assert report.stories_published == 0
    assert report.items_no_valid_url == 1


# ============================================================
# Gated market-news source expansion (design/DECISIONS.md) — Light
# Reading. Uses the REAL, registry-derived GATED_MARKET_NEWS_SOURCE_
# REGISTRY entry (not a synthetic fixture), same discipline as the
# batch 2/3 sections above. Never present in EDITORIAL_SOURCE_REGISTRY
# — passed explicitly as source_entries in every test below, exactly
# as scripts/daily_news_worker.py's own gated call site does only when
# the source is allow-listed.
# ============================================================

from src.data_access.daily_news.source_registry import GATED_MARKET_NEWS_SOURCE_REGISTRY

_light_reading = next(e for e in GATED_MARKET_NEWS_SOURCE_REGISTRY if e.source_id == "light-reading-rss")


def test_light_reading_matching_item_publishes_with_correct_attribution_and_canonical_link(tmp_path, monkeypatch):
    """Parsing + source attribution + canonical original link, using a
    realistic Light Reading-shaped item — a real event/partnership
    pattern naming a tracked company (NVIDIA), matching this source's
    actual editorial beat (confirmed via this session's own read-only
    dry run against the live feed)."""
    entry = _entry(
        title="AWS, Nvidia among new recruits for Verizon's 6G Innovation Forum",
        link="https://www.lightreading.com/6g/aws-nvidia-among-new-recruits-for-verizon-s-6g-innovation-forum",
        summary="Nvidia and AWS have joined Verizon's 6G Innovation Forum to help shape the network's future architecture.",
    )
    _patch_fetch(monkeypatch, {_light_reading.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_light_reading,))

    assert report.stories_published == 1
    story = next(iter(load_stories(tmp_path).values()))
    assert story.publisher == "Light Reading"
    assert story.source_feed_id == "light-reading-rss"
    assert story.source_url == entry.link


def test_light_reading_off_domain_link_is_rejected(tmp_path, monkeypatch):
    entry = _entry(
        title="AWS, Nvidia among new recruits for Verizon's 6G Innovation Forum",
        link="https://not-lightreading.example.com/aws-nvidia-verizon",
        summary="Nvidia and AWS have joined Verizon's 6G Innovation Forum.",
    )
    _patch_fetch(monkeypatch, {_light_reading.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_light_reading,))

    assert report.stories_published == 0
    assert report.items_no_valid_url == 1


def test_light_reading_stale_item_beyond_72_hours_is_excluded(tmp_path, monkeypatch):
    stale_at = (datetime.now(timezone.utc) - timedelta(hours=200)).isoformat()
    entry = _entry(
        title="AWS, Nvidia among new recruits for Verizon's 6G Innovation Forum",
        link="https://www.lightreading.com/6g/aws-nvidia-among-new-recruits-for-verizon-s-6g-innovation-forum",
        published_at=stale_at,
        summary="Nvidia and AWS have joined Verizon's 6G Innovation Forum.",
    )
    _patch_fetch(monkeypatch, {_light_reading.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_light_reading,))

    assert report.stories_published == 0
    assert report.items_stale == 1


def test_light_reading_off_topic_item_does_not_publish(tmp_path, monkeypatch):
    """No company/theme match at all — a real Light Reading pattern
    (a routine personnel/staffing announcement naming no tracked
    company) confirmed via this session's own live dry run."""
    entry = _entry(
        title="Technetix taps SVP of global operations",
        link="https://www.lightreading.com/cable-technology/technetix-taps-svp-of-global-operations",
        summary="Cable technology vendor Technetix has appointed a new senior vice president of global operations.",
    )
    _patch_fetch(monkeypatch, {_light_reading.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_light_reading,))

    assert report.stories_published == 0
    assert report.items_no_match == 1


def test_light_reading_generic_best_x_roundup_naming_a_company_fails_the_admission_gate(tmp_path, monkeypatch):
    """Rejection under the precision-first admission gate specifically
    (editorial_admission.py), distinct from items_no_match: matches a
    tracked company (NVIDIA) but is a consumer buying-guide format, not
    a genuine corporate development."""
    entry = _entry(
        title="Best NVIDIA GPUs Under $500 for Home Networking Rigs",
        link="https://www.lightreading.com/reviews/best-nvidia-gpus-under-500-for-home-networking-rigs",
        summary="We rounded up the best NVIDIA graphics cards you can buy for under $500 right now.",
    )
    _patch_fetch(monkeypatch, {_light_reading.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_light_reading,))

    assert report.stories_published == 0
    assert report.items_not_subject_relevant == 1
    assert load_stories(tmp_path) == {}


def test_light_reading_failed_fetch_is_recorded_and_never_published(tmp_path, monkeypatch):
    _patch_fetch(monkeypatch, {_light_reading.canonical_url: FeedFetchResult(entries=(), failure_code="HTTPError:503")})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_light_reading,))

    assert report.stories_published == 0
    assert report.source_failures == {"light-reading-rss": "HTTPError:503"}


def test_light_reading_duplicate_within_one_run_is_deduplicated(tmp_path, monkeypatch):
    entry = _entry(
        title="AWS, Nvidia among new recruits for Verizon's 6G Innovation Forum",
        link="https://www.lightreading.com/6g/aws-nvidia-among-new-recruits-for-verizon-s-6g-innovation-forum",
        summary="Nvidia and AWS have joined Verizon's 6G Innovation Forum.",
    )
    _patch_fetch(monkeypatch, {_light_reading.canonical_url: FeedFetchResult(entries=(entry, entry), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_light_reading,))

    assert report.stories_published == 1
    assert report.items_duplicate == 1


# ============================================================
# Controlled dry-run harness (design/DECISIONS.md) —
# run_editorial_discovery(dry_run=True). Reuses the exact same gate
# sequence proven above; these tests cover only the dry-run-specific
# behavior (no persistence, sample population, default unaffected).
# ============================================================


def test_dry_run_never_persists_an_admitted_story(tmp_path, monkeypatch):
    entry = _entry(
        title="AWS, Nvidia among new recruits for Verizon's 6G Innovation Forum",
        link="https://www.lightreading.com/6g/aws-nvidia-among-new-recruits-for-verizon-s-6g-innovation-forum",
        summary="Nvidia and AWS have joined Verizon's 6G Innovation Forum.",
    )
    _patch_fetch(monkeypatch, {_light_reading.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_light_reading,), dry_run=True)

    assert report.stories_published == 1  # would-have-been-admitted count
    assert load_stories(tmp_path) == {}  # but nothing was actually written


def test_dry_run_second_run_does_not_see_the_first_runs_non_persisted_story(tmp_path, monkeypatch):
    """Proves dry-run truly never writes: a second dry run against the
    same feed/cache_dir sees the same item as new again, not as
    already-seen — real persistence would have made it a duplicate."""
    entry = _entry(
        title="AWS, Nvidia among new recruits for Verizon's 6G Innovation Forum",
        link="https://www.lightreading.com/6g/aws-nvidia-among-new-recruits-for-verizon-s-6g-innovation-forum",
        summary="Nvidia and AWS have joined Verizon's 6G Innovation Forum.",
    )
    _patch_fetch(monkeypatch, {_light_reading.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    first = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_light_reading,), dry_run=True)
    second = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_light_reading,), dry_run=True)

    assert first.stories_published == 1
    assert second.stories_published == 1
    assert second.items_already_seen == 0


def test_dry_run_populates_admitted_examples(tmp_path, monkeypatch):
    entry = _entry(
        title="AWS, Nvidia among new recruits for Verizon's 6G Innovation Forum",
        link="https://www.lightreading.com/6g/aws-nvidia-among-new-recruits-for-verizon-s-6g-innovation-forum",
        summary="Nvidia and AWS have joined Verizon's 6G Innovation Forum.",
    )
    _patch_fetch(monkeypatch, {_light_reading.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_light_reading,), dry_run=True)

    assert report.admitted_examples == ("AWS, Nvidia among new recruits for Verizon's 6G Innovation Forum",)


def test_dry_run_populates_rejected_examples_with_reasons(tmp_path, monkeypatch):
    no_match_entry = _entry(
        title="Technetix taps SVP of global operations",
        link="https://www.lightreading.com/cable-technology/technetix-taps-svp-of-global-operations",
        summary="Cable technology vendor Technetix has appointed a new senior vice president.",
    )
    admission_fail_entry = _entry(
        title="Best NVIDIA GPUs Under $500 for Home Networking Rigs",
        link="https://www.lightreading.com/reviews/best-nvidia-gpus-under-500-for-home-networking-rigs",
        summary="We rounded up the best NVIDIA graphics cards you can buy for under $500 right now.",
    )
    _patch_fetch(monkeypatch, {
        _light_reading.canonical_url: FeedFetchResult(entries=(no_match_entry, admission_fail_entry), failure_code=None),
    })

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_light_reading,), dry_run=True)

    reasons_by_title = dict(report.rejected_examples)
    assert reasons_by_title["Technetix taps SVP of global operations"] == "no_qualifying_company_or_theme_match"
    assert reasons_by_title["Best NVIDIA GPUs Under $500 for Home Networking Rigs"].startswith("consumer_editorial_format:")


def test_dry_run_examples_are_bounded_to_the_sample_size(tmp_path, monkeypatch):
    entries = tuple(
        _entry(
            title=f"Technetix taps new exec number {i}",
            link=f"https://www.lightreading.com/cable-technology/technetix-taps-new-exec-{i}",
            summary="A routine staffing announcement naming no tracked company.",
        )
        for i in range(editorial_pipeline._DRY_RUN_SAMPLE_SIZE + 3)
    )
    _patch_fetch(monkeypatch, {_light_reading.canonical_url: FeedFetchResult(entries=entries, failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_light_reading,), dry_run=True)

    assert report.items_no_match == editorial_pipeline._DRY_RUN_SAMPLE_SIZE + 3
    assert len(report.rejected_examples) == editorial_pipeline._DRY_RUN_SAMPLE_SIZE


def test_default_dry_run_false_persists_exactly_as_before_and_examples_stay_empty(tmp_path, monkeypatch):
    """Every existing caller (the real worker included) omits dry_run —
    proves that default path is completely unaffected: real persistence
    still happens, and the new example fields stay empty tuples."""
    entry = _entry(
        title="AWS, Nvidia among new recruits for Verizon's 6G Innovation Forum",
        link="https://www.lightreading.com/6g/aws-nvidia-among-new-recruits-for-verizon-s-6g-innovation-forum",
        summary="Nvidia and AWS have joined Verizon's 6G Innovation Forum.",
    )
    _patch_fetch(monkeypatch, {_light_reading.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_light_reading,))

    assert report.stories_published == 1
    assert len(load_stories(tmp_path)) == 1
    assert report.admitted_examples == ()
    assert report.rejected_examples == ()


# ============================================================
# Gated Japan/Korea source expansion (design/DECISIONS.md) — Business
# Korea (Industries + Science & Technology sections) and Japan Times
# Business. Uses the REAL, registry-derived GATED_JP_KR_SOURCE_REGISTRY
# entries (not synthetic fixtures), same discipline as the market-news
# section above. Never present in EDITORIAL_SOURCE_REGISTRY — passed
# explicitly as source_entries in every test below.
# ============================================================

from src.data_access.daily_news.source_registry import GATED_JP_KR_SOURCE_REGISTRY

_bk_industries = next(e for e in GATED_JP_KR_SOURCE_REGISTRY if e.source_id == "businesskorea-industries-rss")
_bk_sci_tech = next(e for e in GATED_JP_KR_SOURCE_REGISTRY if e.source_id == "businesskorea-science-tech-rss")
_jp_times_business = next(e for e in GATED_JP_KR_SOURCE_REGISTRY if e.source_id == "japan-times-business-rss")


def test_bk_science_tech_matching_item_publishes_with_correct_attribution(tmp_path, monkeypatch):
    """The exact real headline observed in this session's own live dry
    run of businesskorea-science-tech-rss — Samsung is tracked, memory
    theme."""
    entry = _entry(
        title="Samsung Unveils Processing DRAM as HBM Alternative",
        link="https://www.businesskorea.co.kr/news/articleView.html?idxno=275535",
        summary="Samsung Electronics has unveiled a new processing-in-memory DRAM technology positioned as an alternative to HBM.",
    )
    _patch_fetch(monkeypatch, {_bk_sci_tech.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_bk_sci_tech,))

    assert report.stories_published == 1
    story = next(iter(load_stories(tmp_path).values()))
    assert story.publisher == "Business Korea"
    assert story.source_feed_id == "businesskorea-science-tech-rss"
    assert story.source_url == entry.link


def test_bk_industries_matching_item_publishes(tmp_path, monkeypatch):
    entry = _entry(
        title="Samsung Electronics Expands Chip Packaging Capacity in Pyeongtaek",
        link="https://www.businesskorea.co.kr/news/articleView.html?idxno=999001",
        summary="Samsung Electronics announced an expansion of advanced chip packaging capacity at its Pyeongtaek campus.",
    )
    _patch_fetch(monkeypatch, {_bk_industries.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_bk_industries,))

    assert report.stories_published == 1
    story = next(iter(load_stories(tmp_path).values()))
    assert story.publisher == "Business Korea"
    assert story.source_feed_id == "businesskorea-industries-rss"


def test_bk_off_domain_link_is_rejected(tmp_path, monkeypatch):
    entry = _entry(
        title="Samsung Unveils Processing DRAM as HBM Alternative",
        link="https://not-businesskorea.example.com/samsung-dram",
        summary="Samsung Electronics has unveiled a new processing-in-memory DRAM technology.",
    )
    _patch_fetch(monkeypatch, {_bk_sci_tech.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_bk_sci_tech,))

    assert report.stories_published == 0
    assert report.items_no_valid_url == 1


def test_bk_stale_item_beyond_72_hours_is_excluded(tmp_path, monkeypatch):
    stale_at = (datetime.now(timezone.utc) - timedelta(hours=200)).isoformat()
    entry = _entry(
        title="Samsung Unveils Processing DRAM as HBM Alternative",
        link="https://www.businesskorea.co.kr/news/articleView.html?idxno=275535",
        published_at=stale_at,
        summary="Samsung Electronics has unveiled a new processing-in-memory DRAM technology.",
    )
    _patch_fetch(monkeypatch, {_bk_sci_tech.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_bk_sci_tech,))

    assert report.stories_published == 0
    assert report.items_stale == 1


def test_bk_off_topic_item_does_not_publish(tmp_path, monkeypatch):
    """No company/theme match at all — a routine macro-policy headline
    naming no tracked company."""
    entry = _entry(
        title="Bank of Korea Holds Rates Steady Amid Inflation Concerns",
        link="https://www.businesskorea.co.kr/news/articleView.html?idxno=999002",
        summary="The Bank of Korea's monetary policy board kept its benchmark rate unchanged amid ongoing inflation concerns.",
    )
    _patch_fetch(monkeypatch, {_bk_industries.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_bk_industries,))

    assert report.stories_published == 0
    assert report.items_no_match == 1


def test_bk_consumer_deals_item_naming_a_company_fails_the_admission_gate(tmp_path, monkeypatch):
    """Rejection under the precision-first admission gate specifically:
    matches a tracked company (Samsung) but is a consumer deals format,
    not a genuine corporate development."""
    entry = _entry(
        title="Best Samsung Electronics Galaxy Deals This Chuseok Holiday",
        link="https://www.businesskorea.co.kr/news/articleView.html?idxno=999003",
        summary="We rounded up the best Samsung Electronics Galaxy phone deals available this Chuseok holiday season.",
    )
    _patch_fetch(monkeypatch, {_bk_industries.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_bk_industries,))

    assert report.stories_published == 0
    assert report.items_not_subject_relevant == 1
    assert load_stories(tmp_path) == {}


def test_bk_disco_word_collision_fails_admission(tmp_path, monkeypatch):
    """Disco Corporation (TSE-tracked) has an ambiguous mechanical alias
    ("Disco") — a K-pop-party-shaped headline naming it incidentally
    must fail admission, exactly the same gate already proven for this
    exact alias on other sources."""
    entry = _entry(
        title="Best Disco Playlists for Your K-Pop Dance Party",
        link="https://www.businesskorea.co.kr/news/articleView.html?idxno=999004",
        summary="From retro classics to modern remixes, here is our ultimate disco playlist for your next K-pop dance party.",
    )
    _patch_fetch(monkeypatch, {_bk_industries.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_bk_industries,))

    assert report.stories_published == 0
    assert load_stories(tmp_path) == {}


def test_bk_export_control_policy_item_is_admitted(tmp_path, monkeypatch):
    """A genuine regulatory/export-control development naming a tracked
    company — the exact shape the design brief calls out."""
    entry = _entry(
        title="SK Hynix Faces New US Export Control Review on HBM Chips",
        link="https://www.businesskorea.co.kr/news/articleView.html?idxno=999005",
        summary="SK Hynix said it is reviewing new US export control measures affecting HBM chip sales to certain markets.",
    )
    _patch_fetch(monkeypatch, {_bk_sci_tech.canonical_url: FeedFetchResult(entries=(entry,), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_bk_sci_tech,))

    assert report.stories_published == 1


def test_bk_failed_fetch_is_recorded_and_never_published(tmp_path, monkeypatch):
    _patch_fetch(monkeypatch, {_bk_industries.canonical_url: FeedFetchResult(entries=(), failure_code="HTTPError:503")})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_bk_industries,))

    assert report.stories_published == 0
    assert report.source_failures == {"businesskorea-industries-rss": "HTTPError:503"}


def test_bk_duplicate_within_one_run_is_deduplicated(tmp_path, monkeypatch):
    entry = _entry(
        title="Samsung Unveils Processing DRAM as HBM Alternative",
        link="https://www.businesskorea.co.kr/news/articleView.html?idxno=275535",
        summary="Samsung Electronics has unveiled a new processing-in-memory DRAM technology.",
    )
    _patch_fetch(monkeypatch, {_bk_sci_tech.canonical_url: FeedFetchResult(entries=(entry, entry), failure_code=None)})

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(_bk_sci_tech,))

    assert report.stories_published == 1
    assert report.items_duplicate == 1


def test_japan_times_business_real_worker_fetch_signature_returns_403(tmp_path):
    """Documents this session's own real, reproduced finding: under the
    exact rss_atom_client the real worker uses, this specific feed
    returns HTTPError:403 (the already-live general japan-times-rss
    does not, under the identical header — see this source's own
    registry notes). Not mocked here on purpose — a real, live network
    call, matching how this exact failure was originally found. Skipped
    automatically if network access is unavailable in this environment,
    never treated as a hard CI dependency."""
    import requests

    try:
        response = requests.get(
            _jp_times_business.canonical_url, timeout=15, headers={"User-Agent": "EevaResearch-DailyNews/1.0"},
        )
    except requests.RequestException:
        import pytest
        pytest.skip("no network access available in this environment")
    assert response.status_code == 403
