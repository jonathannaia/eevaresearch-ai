"""daily_news_pipeline.run_discovery — the bounded, idempotent
orchestration entry point. Fully mocked rss_atom_client.fetch_entries,
zero network calls, no live feed access.

Issuer-ingestion freshness/cap policy (design/DECISIONS.md): _entry()'s
own default `published_at` is a fresh, `datetime.now()`-relative value
(never a fixed past literal) so every existing test in this file that
doesn't care about freshness keeps its original intent unchanged now
that a 7x24h freshness gate exists — mirrors editorial pipeline test
file's own equivalent fixture convention."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
    title: str, link: str, summary: str | None = "A short description.", published_at: str | None = None,
    image_url: str | None = None, image_alt: str | None = None,
) -> RawFeedEntry:
    if published_at is None:
        published_at = datetime.now(timezone.utc).isoformat()
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


# --- Daily News worker observability, Part A ---

_NVDA_SOURCE_WITH_ID = DailyNewsFeedSource(
    company_name="NVIDIA", feed_url="https://nvidianews.nvidia.com/releases.xml",
    feed_format="rss", canonical_domains=("nvidianews.nvidia.com",),
    source_id="nvidia-newsroom-rss",
)


def test_first_discovered_at_is_set_on_first_publish(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("NVIDIA Announces Something", "https://nvidianews.nvidia.com/news/announces-something"),),
            failure_code=None,
        ),
    }, monkeypatch)

    daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    stories = daily_news_store.load_stories(tmp_path)
    story = next(iter(stories.values()))
    assert story.sources[0].first_discovered_at is not None
    assert story.sources[0].first_discovered_at == story.sources[0].retrieved_at  # same moment, first run


def test_first_discovered_at_is_never_overwritten_on_a_later_rediscovery(tmp_path, monkeypatch):
    """The set-once invariant: a second discovery run for the exact same
    item (same story_id) must leave the originally-persisted
    first_discovered_at value untouched, even though a later run's own
    retrieved_at would be a different, later timestamp."""
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("NVIDIA Announces Something", "https://nvidianews.nvidia.com/news/announces-something"),),
            failure_code=None,
        ),
    }, monkeypatch)

    daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))
    first_story = next(iter(daily_news_store.load_stories(tmp_path).values()))
    original_first_discovered_at = first_story.sources[0].first_discovered_at
    assert original_first_discovered_at is not None

    # Re-run discovery for the identical entry (same canonical link ->
    # same deterministic story_id) — already_seen short-circuit fires,
    # so no NewsSourceReference is ever reconstructed for it.
    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))
    assert report.items_already_seen == 1
    assert report.stories_published == 0

    reloaded_story = next(iter(daily_news_store.load_stories(tmp_path).values()))
    assert reloaded_story.sources[0].first_discovered_at == original_first_discovered_at


def test_fetch_results_populated_per_source_id(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE_WITH_ID.feed_url: FeedFetchResult(
            entries=(_entry("NVIDIA Announces Something", "https://nvidianews.nvidia.com/news/announces-something"),),
            failure_code=None, duration_ms=123.4, http_status=200,
        ),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE_WITH_ID,))

    assert "nvidia-newsroom-rss" in report.fetch_results
    fetch_result = report.fetch_results["nvidia-newsroom-rss"]
    assert fetch_result.duration_ms == 123.4
    assert fetch_result.http_status == 200
    assert fetch_result.failure_code is None
    # Existing fields completely unaffected by this additive dict.
    assert report.items_discovered == 1
    assert report.stories_published == 1


def test_fetch_results_populated_even_on_source_failure(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE_WITH_ID.feed_url: FeedFetchResult(
            entries=(), failure_code="HTTPError:403", duration_ms=88.0, http_status=403,
        ),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE_WITH_ID,))

    assert report.source_failures == {"NVIDIA": "HTTPError:403"}
    fetch_result = report.fetch_results["nvidia-newsroom-rss"]
    assert fetch_result.http_status == 403
    assert fetch_result.duration_ms == 88.0


# ============================================================
# Issuer-ingestion freshness/cap policy (design/DECISIONS.md) — a
# uniform gate applied identically to every issuer source. Applies to
# _NVDA_SOURCE/_INTEL_SOURCE and a second NVIDIA-company feed
# (_NVDA_SOURCE_2, mirroring the real registry's own Meta dual-feed
# shape) purely as fixtures — no source-specific behavior is under test
# anywhere below.
# ============================================================

_NVDA_SOURCE_2 = DailyNewsFeedSource(
    company_name="NVIDIA", feed_url="https://nvidianews.nvidia.com/second-feed.xml",
    feed_format="rss", canonical_domains=("nvidianews.nvidia.com",),
)


def _ago(**kwargs) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kwargs)).isoformat()


# --- Freshness: fresh / boundary / stale ---------------------------------


def test_fresh_publication_publishes(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("NVIDIA Fresh Item", "https://nvidianews.nvidia.com/news/fresh", published_at=_ago(days=1)),),
            failure_code=None,
        ),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    assert report.stories_published == 1
    assert report.items_stale == 0


def test_is_fresh_boundary_is_inclusive_unit_level():
    # A true wall-clock integration test can't hit exactly 604800.000000
    # seconds (real execution time between the test's own `now` and
    # run_discovery()'s own `datetime.now()` call always adds a few
    # milliseconds) — this direct unit-level call to the pure helper is
    # the precise proof of the inclusive boundary itself; the
    # integration test below proves the same gate wired correctly into
    # run_discovery() using a safely-inside-the-window value instead.
    now = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)
    exactly_at_boundary = now - timedelta(seconds=7 * 24 * 3600)
    one_second_past_boundary = now - timedelta(seconds=7 * 24 * 3600 + 1)

    assert daily_news_pipeline._is_fresh(exactly_at_boundary, now) is True
    assert daily_news_pipeline._is_fresh(one_second_past_boundary, now) is False


def test_publication_near_the_boundary_still_within_window_publishes(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry(
                "NVIDIA Boundary Item", "https://nvidianews.nvidia.com/news/boundary",
                published_at=_ago(seconds=7 * 24 * 3600 - 5),  # safely inside, allowing for real test execution time
            ),),
            failure_code=None,
        ),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    assert report.stories_published == 1
    assert report.items_stale == 0


def test_publication_older_than_seven_times_twentyfour_hours_is_suppressed(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry(
                "NVIDIA Stale Item", "https://nvidianews.nvidia.com/news/stale",
                published_at=_ago(seconds=7 * 24 * 3600 + 1),
            ),),
            failure_code=None,
        ),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    assert report.stories_published == 0
    assert report.items_stale == 1
    assert daily_news_store.load_stories(tmp_path) == {}


# --- Missing vs. invalid timestamps, tracked separately -------------------


def test_missing_timestamp_suppressed_and_counted_separately_from_invalid(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("NVIDIA No Date", "https://nvidianews.nvidia.com/news/no-date", published_at=""),),
            failure_code=None,
        ),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    assert report.stories_published == 0
    assert report.items_missing_published_at == 1
    assert report.items_invalid_published_at == 0


def test_malformed_timestamp_suppressed_and_counted_separately_from_missing(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry(
                "NVIDIA Bad Date", "https://nvidianews.nvidia.com/news/bad-date", published_at="not-a-real-timestamp",
            ),),
            failure_code=None,
        ),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    assert report.stories_published == 0
    assert report.items_invalid_published_at == 1
    assert report.items_missing_published_at == 0


def test_mixed_stale_missing_invalid_and_fresh_entries(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(
                _entry("Fresh One", "https://nvidianews.nvidia.com/news/fresh-1", published_at=_ago(hours=1)),
                _entry("Fresh Two", "https://nvidianews.nvidia.com/news/fresh-2", published_at=_ago(hours=2)),
                _entry("Stale One", "https://nvidianews.nvidia.com/news/stale-1", published_at=_ago(days=8)),
                _entry("Missing Date", "https://nvidianews.nvidia.com/news/missing-1", published_at=""),
                _entry("Invalid Date", "https://nvidianews.nvidia.com/news/invalid-1", published_at="garbage"),
            ),
            failure_code=None,
        ),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    assert report.stories_published == 2
    assert report.items_stale == 1
    assert report.items_missing_published_at == 1
    assert report.items_invalid_published_at == 1


# --- Per-source cap: newest five, deterministic tie-break -----------------


def test_cap_selects_newest_five_of_six_qualifying_entries(tmp_path, monkeypatch):
    entries = tuple(
        _entry(f"Item {i}", f"https://nvidianews.nvidia.com/news/item-{i}", published_at=_ago(hours=i))
        for i in range(6)  # Item 0 is newest, Item 5 is oldest
    )
    _mock_fetch({_NVDA_SOURCE.feed_url: FeedFetchResult(entries=entries, failure_code=None)}, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    assert report.stories_published == 5
    assert report.items_capped == 1
    headlines = {s.headline for s in daily_news_store.load_stories(tmp_path).values()}
    assert headlines == {"Item 0", "Item 1", "Item 2", "Item 3", "Item 4"}
    assert "Item 5" not in headlines  # the oldest of the six is the one dropped


def test_cap_tie_break_is_deterministic_by_original_feed_order(tmp_path, monkeypatch):
    # 4 clearly-distinct, strictly newer entries occupy 4 of the 5 cap
    # slots; the tied pair (identical published_at) competes for the
    # single remaining slot — this is the only arrangement where the
    # tie-break actually decides an outcome, rather than both tied
    # entries fitting within the cap regardless.
    tied_time = _ago(hours=5)
    entries = (
        _entry("Rank 1", "https://nvidianews.nvidia.com/news/rank-1", published_at=_ago(hours=1)),
        _entry("Rank 2", "https://nvidianews.nvidia.com/news/rank-2", published_at=_ago(hours=2)),
        _entry("Rank 3", "https://nvidianews.nvidia.com/news/rank-3", published_at=_ago(hours=3)),
        _entry("Rank 4", "https://nvidianews.nvidia.com/news/rank-4", published_at=_ago(hours=4)),
        _entry("Tie First In Feed", "https://nvidianews.nvidia.com/news/tie-first", published_at=tied_time),
        _entry("Tie Second In Feed", "https://nvidianews.nvidia.com/news/tie-second", published_at=tied_time),
    )
    _mock_fetch({_NVDA_SOURCE.feed_url: FeedFetchResult(entries=entries, failure_code=None)}, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    assert report.stories_published == 5
    assert report.items_capped == 1
    headlines = {s.headline for s in daily_news_store.load_stories(tmp_path).values()}
    assert "Tie First In Feed" in headlines   # earlier original_index wins the tie
    assert "Tie Second In Feed" not in headlines


# --- Rejected categories never consume a cap slot --------------------------


def test_stale_entry_does_not_consume_cap_slot(tmp_path, monkeypatch):
    fresh = [_entry(f"Fresh {i}", f"https://nvidianews.nvidia.com/news/fresh-{i}", published_at=_ago(hours=i)) for i in range(5)]
    stale = _entry("Stale Extra", "https://nvidianews.nvidia.com/news/stale-extra", published_at=_ago(days=8))
    _mock_fetch({_NVDA_SOURCE.feed_url: FeedFetchResult(entries=tuple(fresh) + (stale,), failure_code=None)}, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    assert report.stories_published == 5
    assert report.items_capped == 0
    assert report.items_stale == 1


def test_invalid_timestamp_entry_does_not_consume_cap_slot(tmp_path, monkeypatch):
    fresh = [_entry(f"Fresh {i}", f"https://nvidianews.nvidia.com/news/fresh-{i}", published_at=_ago(hours=i)) for i in range(5)]
    invalid = _entry("Invalid Extra", "https://nvidianews.nvidia.com/news/invalid-extra", published_at="nonsense")
    _mock_fetch({_NVDA_SOURCE.feed_url: FeedFetchResult(entries=tuple(fresh) + (invalid,), failure_code=None)}, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    assert report.stories_published == 5
    assert report.items_capped == 0
    assert report.items_invalid_published_at == 1


def test_missing_timestamp_entry_does_not_consume_cap_slot(tmp_path, monkeypatch):
    fresh = [_entry(f"Fresh {i}", f"https://nvidianews.nvidia.com/news/fresh-{i}", published_at=_ago(hours=i)) for i in range(5)]
    missing = _entry("Missing Extra", "https://nvidianews.nvidia.com/news/missing-extra", published_at="")
    _mock_fetch({_NVDA_SOURCE.feed_url: FeedFetchResult(entries=tuple(fresh) + (missing,), failure_code=None)}, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    assert report.stories_published == 5
    assert report.items_capped == 0
    assert report.items_missing_published_at == 1


def test_already_seen_entry_does_not_consume_cap_slot(tmp_path, monkeypatch):
    fresh = [_entry(f"Fresh {i}", f"https://nvidianews.nvidia.com/news/fresh-{i}", published_at=_ago(hours=i)) for i in range(5)]
    _mock_fetch({_NVDA_SOURCE.feed_url: FeedFetchResult(entries=tuple(fresh), failure_code=None)}, monkeypatch)
    daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))  # first run: all 5 published

    already_seen_entry = fresh[0]
    new_fresh = _entry("Fresh New", "https://nvidianews.nvidia.com/news/fresh-new", published_at=_ago(minutes=1))
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(entries=(already_seen_entry, new_fresh), failure_code=None),
    }, monkeypatch)
    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    assert report.stories_published == 1  # only "Fresh New" — the already-seen one never competes for the cap
    assert report.items_capped == 0
    assert report.items_already_seen == 1


def test_invalid_url_entry_does_not_consume_cap_slot(tmp_path, monkeypatch):
    fresh = [_entry(f"Fresh {i}", f"https://nvidianews.nvidia.com/news/fresh-{i}", published_at=_ago(hours=i)) for i in range(5)]
    off_domain = _entry("Off Domain Extra", "https://example.com/not-nvidia", published_at=_ago(minutes=1))
    _mock_fetch({_NVDA_SOURCE.feed_url: FeedFetchResult(entries=tuple(fresh) + (off_domain,), failure_code=None)}, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    assert report.stories_published == 5
    assert report.items_capped == 0
    assert report.items_suppressed_no_url == 1


def test_duplicate_title_entry_does_not_consume_cap_slot(tmp_path, monkeypatch):
    fresh = [_entry(f"Fresh {i}", f"https://nvidianews.nvidia.com/news/fresh-{i}", published_at=_ago(hours=i)) for i in range(5)]
    duplicate = _entry("Fresh 0", "https://nvidianews.nvidia.com/news/fresh-0-duplicate", published_at=_ago(minutes=1))
    _mock_fetch({_NVDA_SOURCE.feed_url: FeedFetchResult(entries=tuple(fresh) + (duplicate,), failure_code=None)}, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    assert report.stories_published == 5
    assert report.items_capped == 0
    assert report.items_deduplicated == 1


# --- Capped-out candidates never poison dedupe state -----------------------


def test_capped_out_candidate_does_not_poison_global_dedupe_in_the_same_run(tmp_path, monkeypatch):
    # NVDA_SOURCE and NVDA_SOURCE_2 share company_name="NVIDIA" (mirroring
    # the real registry's own Meta dual-feed shape) — the only real-world
    # shape where a cross-source duplicate check can ever fire at all.
    source_1_entries = [
        _entry(f"Fresh {i}", f"https://nvidianews.nvidia.com/news/fresh-{i}", published_at=_ago(hours=i)) for i in range(5)
    ]
    capped_out = _entry("Capped Title", "https://nvidianews.nvidia.com/news/capped-title", published_at=_ago(hours=10))
    source_2_entry = _entry("Capped Title", "https://nvidianews.nvidia.com/second-feed/capped-title-again", published_at=_ago(minutes=1))
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(entries=tuple(source_1_entries) + (capped_out,), failure_code=None),
        _NVDA_SOURCE_2.feed_url: FeedFetchResult(entries=(source_2_entry,), failure_code=None),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE, _NVDA_SOURCE_2))

    assert report.items_capped == 1
    # Source 2's "Capped Title" must still publish — the capped-out
    # candidate from source 1 never wrote into existing_headlines.
    assert report.stories_published == 6
    headlines = [s.headline for s in daily_news_store.load_stories(tmp_path).values()]
    assert headlines.count("Capped Title") == 1  # exactly one story with this title actually persisted
    assert any(s.headline == "Capped Title" and s.sources[0].url == source_2_entry.link for s in daily_news_store.load_stories(tmp_path).values())


def test_capped_out_candidate_does_not_poison_dedupe_on_a_later_run(tmp_path, monkeypatch):
    source_1_entries = [
        _entry(f"Fresh {i}", f"https://nvidianews.nvidia.com/news/fresh-{i}", published_at=_ago(hours=i)) for i in range(5)
    ]
    capped_out = _entry("Capped Title", "https://nvidianews.nvidia.com/news/capped-title", published_at=_ago(hours=10))
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(entries=tuple(source_1_entries) + (capped_out,), failure_code=None),
    }, monkeypatch)
    first = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))
    assert first.items_capped == 1
    assert "Capped Title" not in {s.headline for s in daily_news_store.load_stories(tmp_path).values()}

    # A LATER run where "Capped Title" is now the only (fresh) entry —
    # must publish, proving run 1's capped-out candidate never poisoned
    # existing_headlines for a subsequent run either.
    _mock_fetch({_NVDA_SOURCE.feed_url: FeedFetchResult(entries=(capped_out,), failure_code=None)}, monkeypatch)
    second = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    assert second.stories_published == 1
    assert "Capped Title" in {s.headline for s in daily_news_store.load_stories(tmp_path).values()}


# --- Same-source duplicate-title behavior stays deterministic -------------


def test_two_same_title_fresh_candidates_in_one_fetch_first_in_feed_order_wins(tmp_path, monkeypatch):
    first_in_feed = _entry("Same Title", "https://nvidianews.nvidia.com/news/same-title-a", published_at=_ago(hours=1))
    second_in_feed = _entry("Same Title", "https://nvidianews.nvidia.com/news/same-title-b", published_at=_ago(minutes=1))
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(entries=(first_in_feed, second_in_feed), failure_code=None),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    assert report.stories_published == 1
    assert report.items_deduplicated == 1
    story = next(iter(daily_news_store.load_stories(tmp_path).values()))
    assert story.sources[0].url == first_in_feed.link  # first-in-feed-order wins, regardless of relative freshness


def test_local_intra_fetch_duplicate_does_not_affect_a_different_companys_source(tmp_path, monkeypatch):
    nvda_entry = _entry("Same Title Different Company", "https://nvidianews.nvidia.com/news/same-title", published_at=_ago(hours=1))
    intel_entry = _entry("Same Title Different Company", "https://newsroom.intel.com/news/same-title", published_at=_ago(hours=1))
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(entries=(nvda_entry,), failure_code=None),
        _INTEL_SOURCE.feed_url: FeedFetchResult(entries=(intel_entry,), failure_code=None),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE, _INTEL_SOURCE))

    assert report.stories_published == 2  # different company_name — never treated as a duplicate of each other
    assert report.items_deduplicated == 0


# --- Idempotency and ordinary-fixture regression ---------------------------


def test_repeated_tick_with_capped_source_remains_idempotent(tmp_path, monkeypatch):
    # The cap is evaluated fresh per run_discovery() call, not
    # cumulatively — a candidate capped out on run 1 is not deleted from
    # the feed and is correctly reconsidered (and, here, published) on
    # run 2 once it's the only remaining qualifying candidate. True
    # idempotency means: no run ever re-publishes something already in
    # the store — proven here by run 3, where all 6 are now already_seen
    # and nothing new is published.
    entries = tuple(
        _entry(f"Item {i}", f"https://nvidianews.nvidia.com/news/item-{i}", published_at=_ago(hours=i)) for i in range(6)
    )
    _mock_fetch({_NVDA_SOURCE.feed_url: FeedFetchResult(entries=entries, failure_code=None)}, monkeypatch)

    first = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))
    second = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))
    third = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    assert first.stories_published == 5
    assert first.items_capped == 1
    assert second.stories_published == 1  # the previously-capped "Item 5" catches up
    assert second.items_already_seen == 5
    assert third.stories_published == 0   # now fully idempotent — nothing left to publish
    assert third.items_already_seen == 6
    assert len(daily_news_store.load_stories(tmp_path)) == 6


def test_ordinary_existing_fixture_behavior_unchanged_all_new_counters_zero(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("NVIDIA Announces Something", "https://nvidianews.nvidia.com/news/announces-something"),),
            failure_code=None,
        ),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    assert report.stories_published == 1
    assert report.items_stale == 0
    assert report.items_missing_published_at == 0
    assert report.items_invalid_published_at == 0
    assert report.items_capped == 0


# ============================================================
# Daily News source-expansion batch 4 (2026-09-13) — Samsung Electronics,
# Murata Manufacturing, Microchip Technology. These use the real,
# registry-derived feed_registry.PILOT_FEEDS entries (not synthetic
# fixtures) so a change to the registry's own fields is caught here too
# — the pipeline logic itself is completely generic/unchanged; these
# tests prove the three new sources exercise it identically to every
# existing issuer source (NVIDIA, Intel, etc. above).
# ============================================================

from src.data_access.daily_news.feed_registry import PILOT_FEEDS

_SAMSUNG_SOURCE = next(f for f in PILOT_FEEDS if f.company_name == "Samsung Electronics")
_MURATA_SOURCE = next(f for f in PILOT_FEEDS if f.company_name == "Murata Manufacturing Co., Ltd.")
_MICROCHIP_SOURCE = next(f for f in PILOT_FEEDS if f.company_name == "Microchip Technology Incorporated")


def test_samsung_fresh_valid_entry_publishes_with_correct_attribution_and_direct_url(tmp_path, monkeypatch):
    article_url = "https://news.samsung.com/global/samsung-and-mistral-ai-announce-strategic-partnership"
    _mock_fetch({
        _SAMSUNG_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("Samsung and Mistral AI Announce Strategic Partnership", article_url),),
            failure_code=None,
        ),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_SAMSUNG_SOURCE,))

    assert report.stories_published == 1
    story = next(iter(daily_news_store.load_stories(tmp_path).values()))
    assert story.company_name == "Samsung Electronics"
    assert story.status == NewsStoryStatus.PUBLISHED
    assert story.sources[0].url == article_url  # direct publisher link preserved, unmodified


def test_murata_fresh_valid_entry_publishes_with_correct_attribution_and_direct_url(tmp_path, monkeypatch):
    article_url = "https://www.murata.com/en-global/news/emc/emifil/2026/0910"
    _mock_fetch({
        _MURATA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("Murata Launches Common Mode Choke Coils", article_url),),
            failure_code=None,
        ),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_MURATA_SOURCE,))

    assert report.stories_published == 1
    story = next(iter(daily_news_store.load_stories(tmp_path).values()))
    assert story.company_name == "Murata Manufacturing Co., Ltd."
    assert story.status == NewsStoryStatus.PUBLISHED
    assert story.sources[0].url == article_url


def test_microchip_fresh_valid_entry_publishes_with_correct_attribution_and_direct_url(tmp_path, monkeypatch):
    article_url = "https://www.microchip.com/en-us/about/news-releases/corporate/microchip-and-marelli-pioneer"
    _mock_fetch({
        _MICROCHIP_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("Microchip and Marelli Pioneer Open-Standard Display Connectivity", article_url),),
            failure_code=None,
        ),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_MICROCHIP_SOURCE,))

    assert report.stories_published == 1
    story = next(iter(daily_news_store.load_stories(tmp_path).values()))
    assert story.company_name == "Microchip Technology Incorporated"
    assert story.status == NewsStoryStatus.PUBLISHED
    assert story.sources[0].url == article_url


# --- Negative tests: existing protections apply unchanged to each new source ---


def test_samsung_off_domain_entry_is_suppressed(tmp_path, monkeypatch):
    _mock_fetch({
        _SAMSUNG_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("Samsung Announces Something", "https://example.com/not-samsung"),), failure_code=None,
        ),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_SAMSUNG_SOURCE,))

    assert report.stories_published == 0
    assert report.items_suppressed_no_url == 1
    assert daily_news_store.load_stories(tmp_path) == {}


def test_murata_duplicate_title_entries_within_one_run_are_deduplicated(tmp_path, monkeypatch):
    # The issuer pipeline (daily_news_pipeline.run_discovery) has no
    # ingestion-time staleness/freshness gate of its own — freshness
    # filtering for issuer stories is a display-time concern in
    # src/ui/pages/daily_news.py's own 7-day rolling window, not
    # something run_discovery() enforces. Duplicate-title detection,
    # verified here instead, is a real ingestion-time protection this
    # pipeline does enforce for every source, including this one.
    _mock_fetch({
        _MURATA_SOURCE.feed_url: FeedFetchResult(
            entries=(
                _entry("Murata Launches Something", "https://www.murata.com/en-global/news/a/2026/0910"),
                _entry("Murata Launches Something", "https://www.murata.com/en-global/news/b/2026/0910"),
            ),
            failure_code=None,
        ),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_MURATA_SOURCE,))

    assert report.stories_published == 1
    assert report.items_deduplicated == 1
    assert len(daily_news_store.load_stories(tmp_path)) == 1


def test_microchip_missing_url_entry_is_suppressed_and_never_persisted(tmp_path, monkeypatch):
    _mock_fetch({
        _MICROCHIP_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("Microchip Announces Something", ""),), failure_code=None,
        ),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_MICROCHIP_SOURCE,))

    assert report.stories_published == 0
    assert report.items_suppressed_no_url == 1
    assert daily_news_store.load_stories(tmp_path) == {}


def test_microchip_idempotent_rerun_creates_no_duplicate_already_seen(tmp_path, monkeypatch):
    article_url = "https://www.microchip.com/en-us/about/news-releases/corporate/microchip-and-marelli-pioneer"
    _mock_fetch({
        _MICROCHIP_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("Microchip and Marelli Pioneer Open-Standard Display Connectivity", article_url),),
            failure_code=None,
        ),
    }, monkeypatch)

    first = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_MICROCHIP_SOURCE,))
    second = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_MICROCHIP_SOURCE,))

    assert first.stories_published == 1
    assert second.stories_published == 0
    assert second.items_already_seen == 1
    assert len(daily_news_store.load_stories(tmp_path)) == 1


def test_samsung_murata_microchip_one_source_failure_isolated_from_the_others(tmp_path, monkeypatch):
    article_url = "https://www.murata.com/en-global/news/emc/emifil/2026/0910"
    _mock_fetch({
        _SAMSUNG_SOURCE.feed_url: FeedFetchResult(entries=(), failure_code="HTTPError:503"),
        _MURATA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("Murata Launches Common Mode Choke Coils", article_url),), failure_code=None,
        ),
        # Microchip left unmocked -> zero entries, zero failure, same as _mock_fetch's own default.
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(
        tmp_path, feed_sources=(_SAMSUNG_SOURCE, _MURATA_SOURCE, _MICROCHIP_SOURCE),
    )

    assert report.source_failures == {"Samsung Electronics": "HTTPError:503"}
    assert report.stories_published == 1  # Murata's item still published despite Samsung's failure


# ============================================================
# Daily News source-expansion batch 5 (2026-09-13) — Hewlett Packard
# Enterprise Company. Uses the real, registry-derived
# feed_registry.PILOT_FEEDS entry (not a synthetic fixture) so a change
# to the registry's own fields is caught here too — the pipeline logic
# itself is completely generic/unchanged; this test proves the new
# source exercises it identically to every existing issuer source
# (NVIDIA, Intel, Samsung, etc. above).
# ============================================================

_HPE_SOURCE = next(f for f in PILOT_FEEDS if f.company_name == "Hewlett Packard Enterprise Company")


def test_hpe_fresh_valid_entry_publishes_with_correct_attribution_and_direct_url(tmp_path, monkeypatch):
    article_url = "https://investors.hpe.com/news-and-events/news/news-details/2026/HPE-Announces-Something"
    _mock_fetch({
        _HPE_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("HPE Announces Something", article_url),), failure_code=None,
        ),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_HPE_SOURCE,))

    assert report.stories_published == 1
    story = next(iter(daily_news_store.load_stories(tmp_path).values()))
    assert story.company_name == "Hewlett Packard Enterprise Company"
    assert story.status == NewsStoryStatus.PUBLISHED
    assert story.sources[0].url == article_url


def test_hpe_off_domain_entry_is_suppressed(tmp_path, monkeypatch):
    _mock_fetch({
        _HPE_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("HPE Announces Something", "https://example.com/not-hpe"),), failure_code=None,
        ),
    }, monkeypatch)

    report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_HPE_SOURCE,))

    assert report.stories_published == 0
    assert report.items_suppressed_no_url == 1
    assert daily_news_store.load_stories(tmp_path) == {}
