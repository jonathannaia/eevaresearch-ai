"""daily_news_pipeline.run_discovery — the bounded, idempotent
orchestration entry point. Fully mocked rss_atom_client.fetch_entries,
zero network calls, no live feed access."""
from __future__ import annotations

from src.data_access.daily_news import daily_news_pipeline, daily_news_store, rss_atom_client
from src.data_access.daily_news.feed_registry import DailyNewsFeedSource
from src.data_access.daily_news.rss_atom_client import FeedFetchResult, RawFeedEntry
from src.models.daily_news_models import NewsStoryStatus

_NVDA_SOURCE = DailyNewsFeedSource(
    company_name="NVIDIA", feed_url="https://nvidianews.nvidia.com/releases.xml",
    feed_format="rss", canonical_domains=("nvidianews.nvidia.com",),
    image_host="iprsoftwaremedia.com",
)
_INTEL_SOURCE = DailyNewsFeedSource(
    company_name="Intel Corp.", feed_url="https://newsroom.intel.com/feed",
    feed_format="rss", canonical_domains=("newsroom.intel.com",),
)


def _entry(
    title: str, link: str, summary: str | None = "A short description.", published_at: str = "2026-08-24T12:00:00+00:00",
    image_url: str | None = None, image_alt: str | None = None,
) -> RawFeedEntry:
    return RawFeedEntry(title=title, link=link, published_at=published_at, summary=summary, image_url=image_url, image_alt=image_alt)


def _mock_fetch(entries_by_url: dict[str, FeedFetchResult], monkeypatch) -> None:
    def _fake_fetch_entries(feed_url: str) -> FeedFetchResult:
        return entries_by_url.get(feed_url, FeedFetchResult(entries=(), failure_code=None))

    monkeypatch.setattr(rss_atom_client, "fetch_entries", _fake_fetch_entries)
    monkeypatch.setattr(daily_news_pipeline.rss_atom_client, "fetch_entries", _fake_fetch_entries)


def test_full_discovery_run_publishes_valid_entries(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("NVIDIA Announces Something", "https://nvidianews.nvidia.com/news/announces-something"),),
            failure_code=None,
        ),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    assert report.items_discovered == 1
    assert report.stories_published == 1
    assert report.items_suppressed_no_url == 0
    assert report.items_already_seen == 0
    stories = daily_news_store.load_stories(tmp_path)
    assert len(stories) == 1
    story = next(iter(stories.values()))
    assert story.company_name == "NVIDIA"
    assert story.status == NewsStoryStatus.PUBLISHED
    assert story.ticker == "NVDA"  # reused from tracked_companies.py


def test_missing_url_entry_is_suppressed_and_never_persisted(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("No Link Here", ""),),
            failure_code=None,
        ),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    assert report.items_suppressed_no_url == 1
    assert report.stories_published == 0
    assert daily_news_store.load_stories(tmp_path) == {}


def test_off_domain_url_entry_is_suppressed(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("Off Domain", "https://someotherhost.com/news/x"),),
            failure_code=None,
        ),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    assert report.items_suppressed_no_url == 1
    assert daily_news_store.load_stories(tmp_path) == {}


def test_missing_excerpt_entry_publishes_with_the_fallback_sentence(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("No Description", "https://nvidianews.nvidia.com/news/no-description", summary=None),),
            failure_code=None,
        ),
    }, monkeypatch)

    daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    story = next(iter(daily_news_store.load_stories(tmp_path).values()))
    assert story.is_fallback_summary
    assert story.eeva_summary == "The company published this update through its official Investor Relations channel."


def test_one_source_failure_does_not_block_another_source(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(entries=(), failure_code="ConnectionError"),
        _INTEL_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("Intel News", "https://newsroom.intel.com/some-news"),),
            failure_code=None,
        ),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE, _INTEL_SOURCE))

    assert report.source_failures == {"NVIDIA": "ConnectionError"}
    assert report.stories_published == 1
    stories = daily_news_store.load_stories(tmp_path)
    assert next(iter(stories.values())).company_name == "Intel Corp."


def test_malformed_feed_isolated_as_source_failure(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(entries=(), failure_code="MalformedFeed"),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    assert report.source_failures == {"NVIDIA": "MalformedFeed"}
    assert report.stories_published == 0


def test_duplicate_title_across_two_sources_is_deduplicated(tmp_path, monkeypatch):
    duplicate_title = "Company Announces the Same Thing"
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry(duplicate_title, "https://nvidianews.nvidia.com/news/first"),), failure_code=None,
        ),
        _INTEL_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry(duplicate_title, "https://newsroom.intel.com/first"),), failure_code=None,
        ),
    }, monkeypatch)
    # Both sources are attributed to NVIDIA here on purpose, since dedup
    # is scoped per-company (see dedup.py's own docstring) — this proves
    # a same-company duplicate collapses rather than proving cross-
    # company titles are unrelated (a separate, already-covered case).
    same_company_intel = DailyNewsFeedSource(
        company_name="NVIDIA", feed_url=_INTEL_SOURCE.feed_url, feed_format="rss",
        canonical_domains=("newsroom.intel.com",),
    )

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE, same_company_intel))

    assert report.items_deduplicated == 1
    assert report.stories_published == 1


def test_idempotent_rerun_creates_no_duplicate_stories(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("NVIDIA Announces Something", "https://nvidianews.nvidia.com/news/announces-something"),),
            failure_code=None,
        ),
    }, monkeypatch)

    first = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))
    second = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    assert first.stories_published == 1
    assert first.items_already_seen == 0
    assert second.stories_published == 0
    # Observability fix (design/DECISIONS.md, Daily News operational-fix
    # workstream): the rerun's item is now counted, not silently dropped
    # with no trace anywhere in the report — this is the ordinary,
    # expected steady-state outcome once a feed's items have already been
    # published, not a dedup/suppression case.
    assert second.items_already_seen == 1
    assert second.items_deduplicated == 0
    assert second.suppressed_items == ()
    assert len(daily_news_store.load_stories(tmp_path)) == 1


def test_korean_entry_is_preserved_not_translated(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("삼성전자 신규시설투자 결정", "https://nvidianews.nvidia.com/news/korean-title", summary="설명"),),
            failure_code=None,
        ),
    }, monkeypatch)

    daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    story = next(iter(daily_news_store.load_stories(tmp_path).values()))
    assert story.translation_unavailable
    assert story.eeva_summary is None
    assert story.original_title == "삼성전자 신규시설투자 결정"


def test_valid_image_from_an_approved_host_is_persisted(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry(
                "NVIDIA Announces Something", "https://nvidianews.nvidia.com/news/announces-something",
                image_url="https://iprsoftwaremedia.com/photo.jpg", image_alt="A photo",
            ),),
            failure_code=None,
        ),
    }, monkeypatch)

    daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    story = next(iter(daily_news_store.load_stories(tmp_path).values()))
    assert story.sources[0].image_url == "https://iprsoftwaremedia.com/photo.jpg"
    assert story.sources[0].image_alt == "A photo"


def test_image_from_an_unapproved_host_is_dropped_but_story_still_publishes(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry(
                "NVIDIA Announces Something", "https://nvidianews.nvidia.com/news/announces-something",
                image_url="https://some-untrusted-host.com/photo.jpg", image_alt="A photo",
            ),),
            failure_code=None,
        ),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    assert report.stories_published == 1
    story = next(iter(daily_news_store.load_stories(tmp_path).values()))
    assert story.sources[0].image_url is None
    assert story.sources[0].image_alt is None


def test_missing_image_alt_falls_back_to_the_item_title(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry(
                "NVIDIA Announces Something", "https://nvidianews.nvidia.com/news/announces-something",
                image_url="https://iprsoftwaremedia.com/photo.jpg", image_alt=None,
            ),),
            failure_code=None,
        ),
    }, monkeypatch)

    daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    story = next(iter(daily_news_store.load_stories(tmp_path).values()))
    assert story.sources[0].image_alt == "NVIDIA Announces Something"


def test_source_with_no_approved_image_host_never_persists_an_image(tmp_path, monkeypatch):
    _mock_fetch({
        _INTEL_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry(
                "Intel News", "https://newsroom.intel.com/some-news",
                image_url="https://some-cdn.example.com/photo.jpg",
            ),),
            failure_code=None,
        ),
    }, monkeypatch)

    daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_INTEL_SOURCE,))

    story = next(iter(daily_news_store.load_stories(tmp_path).values()))
    assert story.sources[0].image_url is None


def test_unknown_tracked_company_source_is_skipped_with_a_warning(tmp_path, monkeypatch):
    unknown_source = DailyNewsFeedSource(
        company_name="Not A Real Tracked Company", feed_url="https://example.com/rss",
        feed_format="rss", canonical_domains=("example.com",),
    )
    _mock_fetch({}, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(unknown_source,))

    assert report.stories_published == 0
    assert any("not found in tracked_companies.py" in w for w in report.warnings)


# --- Daily News durability workstream: additive, optional repository seam ---


def test_run_discovery_omitted_repository_behaves_exactly_as_before(tmp_path, monkeypatch):
    """The default, unchanged path — every existing caller (scripts/
    run_daily_news_discovery.py's own default invocation) hits this."""
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("NVIDIA Announces Something", "https://nvidianews.nvidia.com/news/announces-something"),),
            failure_code=None,
        ),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    assert report.stories_published == 1
    # Written to the JSON store, not any other backend.
    assert len(daily_news_store.load_stories(tmp_path)) == 1


def test_run_discovery_supplied_repository_routes_every_store_touch_through_it(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("NVIDIA Announces Something", "https://nvidianews.nvidia.com/news/announces-something"),),
            failure_code=None,
        ),
    }, monkeypatch)

    from src.data_access.state_db import connection, daily_news_repository, schema

    conn = connection.connect_in_memory()
    schema.migrate(conn)

    class _SqliteRepoAdapter:
        def load_stories(self):
            return daily_news_repository.load_stories(conn)

        def upsert_new_stories(self, new_stories):
            return daily_news_repository.upsert_new_stories(conn, new_stories)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,), daily_news_repository=_SqliteRepoAdapter())

    assert report.stories_published == 1
    # Persisted through the supplied repository (SQLite), never touching
    # the JSON file at all.
    assert daily_news_repository.load_stories(conn) != {}
    assert daily_news_store.load_stories(tmp_path) == {}
    assert not (tmp_path / "daily_news_stories.json").exists()


def test_run_discovery_supplied_repository_skips_already_seen_story_ids(tmp_path, monkeypatch):
    from src.data_access.state_db import connection, daily_news_repository, schema

    conn = connection.connect_in_memory()
    schema.migrate(conn)

    class _SqliteRepoAdapter:
        def load_stories(self):
            return daily_news_repository.load_stories(conn)

        def upsert_new_stories(self, new_stories):
            return daily_news_repository.upsert_new_stories(conn, new_stories)

    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("NVIDIA Announces Something", "https://nvidianews.nvidia.com/news/announces-something"),),
            failure_code=None,
        ),
    }, monkeypatch)

    repo = _SqliteRepoAdapter()
    first = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,), daily_news_repository=repo)
    second = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,), daily_news_repository=repo)

    assert first.stories_published == 1
    assert second.stories_published == 0  # already-seen id — idempotent no-op
    assert second.items_already_seen == 1  # counted, not silently dropped — same repository-backed path
    assert len(daily_news_repository.load_stories(conn)) == 1
