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
from src.models.daily_news_models import EditorialStory

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
) -> EditorialStory:
    published_at = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return EditorialStory(
        id=story_id, headline=headline, publisher="CNBC", source_url=f"https://www.cnbc.com/{story_id}.html",
        published_at=published_at, retrieved_at=published_at, excerpt=None,
        matched_companies=("Oracle Corporation",), matched_themes=(), source_feed_id=source_feed_id,
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
