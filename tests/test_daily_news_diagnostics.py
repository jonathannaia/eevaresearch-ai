"""diagnostics.py — Daily News worker observability, Part A (design/
DECISIONS.md). Two guarantees are tested separately:

  - classify_feed_entries() is genuinely pure — proven by an AST/import
    guard (structural, not just behavioral), a byte-for-byte store-
    unchanged test, a mocked-write-method-must-not-be-called test, and a
    parity test against a real, isolated daily_news_pipeline.run_
    discovery() call for the identical fixtures.
  - diagnose_source_duplicates() is the one-feed, source_id-required,
    20-entry-capped, exactly-one-request wrapper — its own network call
    is real (mocked here) but it must never write anything, must never
    guess a URL for an unknown source_id, and must never make a second
    request of any kind.

Zero real network calls anywhere in this file."""
from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.data_access.daily_news import daily_news_pipeline, daily_news_store, diagnostics, rss_atom_client
from src.data_access.daily_news.feed_registry import DailyNewsFeedSource
from src.data_access.daily_news.rss_atom_client import FeedFetchResult, RawFeedEntry
from src.models.daily_news_models import NewsSourceReference, NewsStateTransition, NewsStory, NewsStoryStatus, SourceClass

_DIAGNOSTICS_PATH = Path(__file__).parent.parent / "src" / "data_access" / "daily_news" / "diagnostics.py"

_NVDA_SOURCE = DailyNewsFeedSource(
    company_name="NVIDIA", feed_url="https://nvidianews.nvidia.com/releases.xml",
    feed_format="rss", canonical_domains=("nvidianews.nvidia.com",),
    source_id="nvidia-newsroom-rss",
)


def _entry(
    title: str, link: str, summary: str | None = "A short description.", published_at: str | None = None,
) -> RawFeedEntry:
    # Issuer-ingestion freshness/cap policy: a fresh, execution-relative
    # published_at default (never a fixed past literal) — mirrors
    # test_daily_news_pipeline.py's own _entry() fixture, see there for
    # why. Deliberately NOT applied to _story()'s own hardcoded date
    # below: that helper represents an ALREADY-PERSISTED story, which
    # classify_feed_entries() never freshness-checks in the first place
    # (only incoming `entries` go through the freshness gate).
    if published_at is None:
        published_at = datetime.now(timezone.utc).isoformat()
    return RawFeedEntry(title=title, link=link, published_at=published_at, summary=summary)


def _ago(**kwargs) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kwargs)).isoformat()


def _story(story_id: str, company_name: str, headline: str, url: str) -> NewsStory:
    source = NewsSourceReference(
        publisher=company_name, source_class=SourceClass.OFFICIAL_COMPANY, url=url,
        title=headline, published_at="2026-08-24T12:00:00+00:00", retrieved_at="2026-08-24T12:05:00+00:00",
        original_language="English",
    )
    return NewsStory(
        id=story_id, company_name=company_name, ticker="NVDA", theme_slug="ai-buildout",
        headline=headline, eeva_summary=None, is_fallback_summary=False, translation_unavailable=False,
        original_title=None, sources=(source,), status=NewsStoryStatus.PUBLISHED,
        state_history=[NewsStateTransition(status=NewsStoryStatus.PUBLISHED, at="2026-08-24T12:05:00+00:00")],
    )


# ============================================================
# Structural guarantee: classify_feed_entries() is genuinely pure
# ============================================================


def test_module_never_imports_a_write_capable_symbol():
    """AST-level guard, not just a behavioral test — catches even a
    future accidental edit that would introduce a write capability, not
    just today's version. Every existing name in this module's own
    import list must be free of daily_news_store/daily_news_backend/
    daily_news_pipeline/any state-db repository."""
    forbidden_modules = (
        "src.data_access.daily_news.daily_news_store",
        "src.data_access.daily_news.daily_news_backend",
        "src.data_access.daily_news.daily_news_pipeline",
        "src.data_access.state_db",
        "src.data_access.postgres_state_db",
    )
    forbidden_name_fragments = ("upsert", "save_", "update_story", "transition")

    tree = ast.parse(_DIAGNOSTICS_PATH.read_text(encoding="utf-8"), filename=str(_DIAGNOSTICS_PATH))
    offenders = []
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for module in modules:
            if any(module == forbidden or module.startswith(forbidden + ".") for forbidden in forbidden_modules):
                offenders.append(f"import: {module}")
        if isinstance(node, ast.Name):
            for fragment in forbidden_name_fragments:
                if fragment in node.id.lower():
                    offenders.append(f"name reference: {node.id}")
        if isinstance(node, ast.Attribute):
            for fragment in forbidden_name_fragments:
                if fragment in node.attr.lower():
                    offenders.append(f"attribute reference: {node.attr}")
    assert not offenders, offenders


def test_classify_feed_entries_leaves_the_persistent_store_byte_for_byte_unchanged(tmp_path):
    existing_story = _story("newsitem-nvidia-existing", "NVIDIA", "Existing Headline", "https://nvidianews.nvidia.com/news/existing")
    daily_news_store.upsert_new_stories(tmp_path, [existing_story])
    store_file = tmp_path / "daily_news_stories.json"
    bytes_before = store_file.read_bytes()

    stories = daily_news_store.load_stories(tmp_path)
    entries = (
        _entry("Existing Headline", "https://nvidianews.nvidia.com/news/existing-but-different-url"),
        _entry("Brand New Headline", "https://nvidianews.nvidia.com/news/brand-new"),
    )
    diagnostics.classify_feed_entries(
        source_id="nvidia-newsroom-rss", feed_url=_NVDA_SOURCE.feed_url, company_name="NVIDIA",
        canonical_domains=("nvidianews.nvidia.com",), entries=entries, stories=stories,
    )

    bytes_after = store_file.read_bytes()
    assert bytes_before == bytes_after


def test_mocked_write_methods_are_never_called_by_classify_feed_entries(monkeypatch, tmp_path):
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("a write-capable method was called by a supposedly pure function")

    monkeypatch.setattr(daily_news_store, "upsert_new_stories", _fail_if_called)
    monkeypatch.setattr(daily_news_store, "update_story", _fail_if_called)
    monkeypatch.setattr(daily_news_store, "save_stories", _fail_if_called)

    stories = {}
    entries = (_entry("Brand New Headline", "https://nvidianews.nvidia.com/news/brand-new"),)
    result = diagnostics.classify_feed_entries(
        source_id="nvidia-newsroom-rss", feed_url=_NVDA_SOURCE.feed_url, company_name="NVIDIA",
        canonical_domains=("nvidianews.nvidia.com",), entries=entries, stories=stories,
    )
    assert len(result) == 1  # ran to completion; the monkeypatched methods were simply never reached


def test_classification_parity_with_a_real_isolated_run_discovery_call(tmp_path, monkeypatch):
    """Proves classify_feed_entries() is not just inert but ACCURATE — its
    classifications must match what a real, isolated run_discovery() call
    actually decides for the identical fixtures."""
    already_seen_entry = _entry("Already Seen Headline", "https://nvidianews.nvidia.com/news/already-seen")
    original_entry = _entry("Duplicate Title Headline", "https://nvidianews.nvidia.com/news/original-url")

    def _first_run_fetch(feed_url):
        return FeedFetchResult(entries=(already_seen_entry, original_entry), failure_code=None)

    monkeypatch.setattr(daily_news_pipeline.rss_atom_client, "fetch_entries", _first_run_fetch)

    # First real run: publishes both entries for real, establishing the
    # store state the second (diagnostic-only) round then classifies
    # against.
    first_report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))
    assert first_report.stories_published == 2
    stories = daily_news_store.load_stories(tmp_path)

    # A same-titled entry under a NEW url (title-level duplicate, not an
    # id-level repeat) plus a genuinely new entry, a missing-title entry,
    # and an off-domain entry.
    duplicate_url_variant_entry = _entry("Duplicate Title Headline", "https://nvidianews.nvidia.com/news/new-url-same-title")
    new_entry = _entry("Brand New Headline", "https://nvidianews.nvidia.com/news/brand-new")
    no_title_entry = _entry("", "https://nvidianews.nvidia.com/news/no-title")
    no_url_entry = _entry("Off-domain Headline", "https://not-nvidia.example.com/news/off-domain")
    candidate_entries = (already_seen_entry, duplicate_url_variant_entry, new_entry, no_title_entry, no_url_entry)

    def _second_run_fetch(feed_url):
        return FeedFetchResult(entries=candidate_entries, failure_code=None)

    monkeypatch.setattr(daily_news_pipeline.rss_atom_client, "fetch_entries", _second_run_fetch)
    real_report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    diagnostic_result = diagnostics.classify_feed_entries(
        source_id="nvidia-newsroom-rss", feed_url=_NVDA_SOURCE.feed_url, company_name="NVIDIA",
        canonical_domains=("nvidianews.nvidia.com",), entries=candidate_entries, stories=stories,
    )
    by_title = {c.title: c.classification for c in diagnostic_result}

    assert by_title["Already Seen Headline"] == "already_seen"
    assert by_title["Duplicate Title Headline"] == "would_deduplicate"
    assert by_title["Brand New Headline"] == "would_publish"
    assert by_title["(no title)"] == "suppressed_no_title"
    assert by_title["Off-domain Headline"] == "suppressed_no_url"

    # The real pipeline agrees exactly, for the same reasons, on the
    # same candidate_entries batch.
    assert real_report.items_already_seen == 1
    assert real_report.items_deduplicated == 1
    assert real_report.stories_published == 1  # only "Brand New Headline"


# ============================================================
# Issuer-ingestion freshness/cap policy parity
# ============================================================


def test_diagnostics_is_fresh_boundary_is_inclusive_unit_level():
    """Direct, pure unit-level proof of THIS module's own duplicated
    _is_fresh() — mirrors test_daily_news_pipeline.py's own
    test_is_fresh_boundary_is_inclusive_unit_level exactly, so
    diagnostics.py's copy of the inclusive-boundary guarantee is proven
    independently rather than only claimed by comment. No
    datetime.now() call anywhere in this test."""
    now = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)
    exactly_at_boundary = now - timedelta(seconds=7 * 24 * 3600)
    one_second_past_boundary = now - timedelta(seconds=7 * 24 * 3600 + 1)

    assert diagnostics._is_fresh(exactly_at_boundary, now) is True
    assert diagnostics._is_fresh(one_second_past_boundary, now) is False


def test_future_timestamp_parity_with_a_real_isolated_run_discovery_call(tmp_path, monkeypatch):
    """A future-dated entry is fresh (negative elapsed time is never
    specifically rejected — see _is_fresh()'s own docstring in both
    modules) and must publish for real, with every freshness counter at
    zero, and classify identically as would_publish in diagnostics.
    `_ago(hours=-1)` reuses the existing _ago() helper unmodified with a
    negative delta: `now - timedelta(hours=-1)` is exactly `now +
    timedelta(hours=1)`, a safe one-hour-future margin with no new
    helper needed."""
    future_entry = _entry("Future Dated Item", "https://nvidianews.nvidia.com/news/future", published_at=_ago(hours=-1))

    monkeypatch.setattr(daily_news_pipeline.rss_atom_client, "fetch_entries", lambda feed_url: FeedFetchResult(entries=(future_entry,), failure_code=None))
    real_report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    assert real_report.stories_published == 1
    assert real_report.items_stale == 0
    assert real_report.items_missing_published_at == 0
    assert real_report.items_invalid_published_at == 0

    diagnostic_result = diagnostics.classify_feed_entries(
        source_id="nvidia-newsroom-rss", feed_url=_NVDA_SOURCE.feed_url, company_name="NVIDIA",
        canonical_domains=("nvidianews.nvidia.com",), entries=(future_entry,), stories={},
    )
    assert diagnostic_result[0].classification == "would_publish"


def test_missing_invalid_stale_and_fresh_parity_with_a_real_isolated_run_discovery_call(tmp_path, monkeypatch):
    """Proves classify_feed_entries() agrees with a real, isolated
    run_discovery() call on all four of the new gates: missing, invalid,
    stale, and fresh."""
    missing_entry = _entry("Missing Timestamp", "https://nvidianews.nvidia.com/news/missing", published_at="")
    invalid_entry = _entry("Invalid Timestamp", "https://nvidianews.nvidia.com/news/invalid", published_at="not-a-real-timestamp")
    stale_entry = _entry("Stale Item", "https://nvidianews.nvidia.com/news/stale", published_at=_ago(days=8))
    fresh_entry = _entry("Fresh Item", "https://nvidianews.nvidia.com/news/fresh", published_at=_ago(hours=1))
    candidate_entries = (missing_entry, invalid_entry, stale_entry, fresh_entry)

    monkeypatch.setattr(daily_news_pipeline.rss_atom_client, "fetch_entries", lambda feed_url: FeedFetchResult(entries=candidate_entries, failure_code=None))
    real_report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    diagnostic_result = diagnostics.classify_feed_entries(
        source_id="nvidia-newsroom-rss", feed_url=_NVDA_SOURCE.feed_url, company_name="NVIDIA",
        canonical_domains=("nvidianews.nvidia.com",), entries=candidate_entries, stories={},
    )
    by_title = {c.title: c.classification for c in diagnostic_result}

    assert by_title["Missing Timestamp"] == "suppressed_missing_published_at"
    assert by_title["Invalid Timestamp"] == "suppressed_invalid_published_at"
    assert by_title["Stale Item"] == "suppressed_stale"
    assert by_title["Fresh Item"] == "would_publish"

    assert real_report.items_missing_published_at == 1
    assert real_report.items_invalid_published_at == 1
    assert real_report.items_stale == 1
    assert real_report.stories_published == 1


def test_capped_candidate_parity_with_a_real_isolated_run_discovery_call(tmp_path, monkeypatch):
    """Six fresh, newest-first-ordered candidates: the pipeline publishes
    only the newest five and counts the sixth as capped. Diagnostics must
    classify the identical sixth entry as would_be_capped, not
    would_publish."""
    candidate_entries = tuple(
        _entry(f"Item {i}", f"https://nvidianews.nvidia.com/news/item-{i}", published_at=_ago(hours=i))
        for i in range(6)
    )

    monkeypatch.setattr(daily_news_pipeline.rss_atom_client, "fetch_entries", lambda feed_url: FeedFetchResult(entries=candidate_entries, failure_code=None))
    real_report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    diagnostic_result = diagnostics.classify_feed_entries(
        source_id="nvidia-newsroom-rss", feed_url=_NVDA_SOURCE.feed_url, company_name="NVIDIA",
        canonical_domains=("nvidianews.nvidia.com",), entries=candidate_entries, stories={},
    )
    by_title = {c.title: c.classification for c in diagnostic_result}

    # "Item 0" is _ago(hours=0) — the newest — through "Item 4"; "Item 5"
    # (_ago(hours=5), the oldest of the six) is the one the cap drops.
    for i in range(5):
        assert by_title[f"Item {i}"] == "would_publish"
    assert by_title["Item 5"] == "would_be_capped"

    assert real_report.items_capped == 1
    assert real_report.stories_published == 5


def test_capped_candidate_does_not_read_as_a_persisted_story_on_a_later_diagnostic_call(tmp_path, monkeypatch):
    """A capped-out candidate is never persisted by run_discovery() (see
    test_daily_news_pipeline.py's own dedupe-poisoning tests). A later
    diagnostic call against the real post-run store must therefore still
    classify that exact same entry as would_publish (now the newest
    available, cap no longer binding at 1-of-1), never as already_seen or
    would_deduplicate — proving diagnostics does not privately remember
    anything about it either."""
    six_entries = tuple(
        _entry(f"Item {i}", f"https://nvidianews.nvidia.com/news/item-{i}", published_at=_ago(hours=i))
        for i in range(6)
    )
    monkeypatch.setattr(daily_news_pipeline.rss_atom_client, "fetch_entries", lambda feed_url: FeedFetchResult(entries=six_entries, failure_code=None))
    daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))
    stories = daily_news_store.load_stories(tmp_path)
    assert not any(s.headline == "Item 5" for s in stories.values())  # confirmed never persisted

    capped_entry_only = (six_entries[5],)
    diagnostic_result = diagnostics.classify_feed_entries(
        source_id="nvidia-newsroom-rss", feed_url=_NVDA_SOURCE.feed_url, company_name="NVIDIA",
        canonical_domains=("nvidianews.nvidia.com",), entries=capped_entry_only, stories=stories,
    )
    assert diagnostic_result[0].classification == "would_publish"


def test_tie_break_ordering_matches_the_pipelines_own_original_feed_order_rule(tmp_path, monkeypatch):
    """Mirrors test_daily_news_pipeline.py's own
    test_cap_tie_break_is_deterministic_by_original_feed_order: 4 clearly
    newer entries plus 2 exactly-tied entries — the earlier-in-feed-order
    one of the tied pair must win the cap's last slot, in both the real
    pipeline and diagnostics."""
    tied_time = _ago(hours=5)
    candidate_entries = (
        _entry("Rank 1", "https://nvidianews.nvidia.com/news/rank-1", published_at=_ago(hours=1)),
        _entry("Rank 2", "https://nvidianews.nvidia.com/news/rank-2", published_at=_ago(hours=2)),
        _entry("Rank 3", "https://nvidianews.nvidia.com/news/rank-3", published_at=_ago(hours=3)),
        _entry("Rank 4", "https://nvidianews.nvidia.com/news/rank-4", published_at=_ago(hours=4)),
        _entry("Tied First", "https://nvidianews.nvidia.com/news/tied-first", published_at=tied_time),
        _entry("Tied Second", "https://nvidianews.nvidia.com/news/tied-second", published_at=tied_time),
    )

    monkeypatch.setattr(daily_news_pipeline.rss_atom_client, "fetch_entries", lambda feed_url: FeedFetchResult(entries=candidate_entries, failure_code=None))
    real_report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))
    stories = daily_news_store.load_stories(tmp_path)

    diagnostic_result = diagnostics.classify_feed_entries(
        source_id="nvidia-newsroom-rss", feed_url=_NVDA_SOURCE.feed_url, company_name="NVIDIA",
        canonical_domains=("nvidianews.nvidia.com",), entries=candidate_entries, stories={},
    )
    by_title = {c.title: c.classification for c in diagnostic_result}

    assert by_title["Tied First"] == "would_publish"
    assert by_title["Tied Second"] == "would_be_capped"
    assert real_report.stories_published == 5
    assert any(s.headline == "Tied First" for s in stories.values())
    assert not any(s.headline == "Tied Second" for s in stories.values())


def test_intra_batch_duplicate_title_is_classified_as_would_deduplicate_not_would_publish(tmp_path, monkeypatch):
    """A gap the earlier (pre-freshness-policy) version of this module
    had: two fresh entries sharing a title within the SAME batch, neither
    already in the store, must still resolve to exactly one would_publish
    and one would_deduplicate — matching run_discovery()'s own
    queued_headlines_this_source behavior — not two would_publish."""
    first = _entry("Duplicate Title Same Batch", "https://nvidianews.nvidia.com/news/first-url", published_at=_ago(hours=1))
    second = _entry("Duplicate Title Same Batch", "https://nvidianews.nvidia.com/news/second-url", published_at=_ago(hours=2))
    candidate_entries = (first, second)

    monkeypatch.setattr(daily_news_pipeline.rss_atom_client, "fetch_entries", lambda feed_url: FeedFetchResult(entries=candidate_entries, failure_code=None))
    real_report = daily_news_pipeline.run_discovery(tmp_path, feed_sources=(_NVDA_SOURCE,))

    diagnostic_result = diagnostics.classify_feed_entries(
        source_id="nvidia-newsroom-rss", feed_url=_NVDA_SOURCE.feed_url, company_name="NVIDIA",
        canonical_domains=("nvidianews.nvidia.com",), entries=candidate_entries, stories={},
    )

    assert diagnostic_result[0].classification == "would_publish"
    assert diagnostic_result[1].classification == "would_deduplicate"
    assert diagnostic_result[1].matched_story_title == "Duplicate Title Same Batch"
    assert diagnostic_result[1].matched_story_url == first.link

    assert real_report.items_deduplicated == 1
    assert real_report.stories_published == 1


def test_would_deduplicate_reports_the_matched_stored_title_and_url(tmp_path):
    existing_story = _story("newsitem-nvidia-existing", "NVIDIA", "Existing Headline", "https://nvidianews.nvidia.com/news/existing")
    stories = {existing_story.id: existing_story}
    entries = (_entry("Existing Headline", "https://nvidianews.nvidia.com/news/existing-different-url"),)

    result = diagnostics.classify_feed_entries(
        source_id="nvidia-newsroom-rss", feed_url=_NVDA_SOURCE.feed_url, company_name="NVIDIA",
        canonical_domains=("nvidianews.nvidia.com",), entries=entries, stories=stories,
    )

    assert len(result) == 1
    assert result[0].classification == "would_deduplicate"
    assert result[0].matched_story_title == "Existing Headline"
    assert result[0].matched_story_url == "https://nvidianews.nvidia.com/news/existing"


def test_output_fields_are_exactly_the_eight_documented_fields():
    entries = (_entry("Brand New Headline", "https://nvidianews.nvidia.com/news/brand-new"),)
    result = diagnostics.classify_feed_entries(
        source_id="nvidia-newsroom-rss", feed_url=_NVDA_SOURCE.feed_url, company_name="NVIDIA",
        canonical_domains=("nvidianews.nvidia.com",), entries=entries, stories={},
    )
    fields = set(diagnostics.FeedEntryClassification.__dataclass_fields__)
    assert fields == {
        "source_id", "feed_url", "title", "link", "normalized_title",
        "classification", "matched_story_title", "matched_story_url",
    }
    assert result[0].source_id == "nvidia-newsroom-rss"
    assert result[0].feed_url == _NVDA_SOURCE.feed_url
    assert result[0].normalized_title == "brand new headline"


# ============================================================
# diagnose_source_duplicates() — the one-feed wrapper
# ============================================================


def test_diagnose_source_duplicates_requires_a_registered_source_id(monkeypatch):
    monkeypatch.setattr(diagnostics, "PILOT_FEEDS", (_NVDA_SOURCE,))
    with pytest.raises(ValueError):
        diagnostics.diagnose_source_duplicates("not-a-real-source-id", stories={})


def test_diagnose_source_duplicates_makes_exactly_one_requests_get_call(monkeypatch):
    """Correction 2's explicit requirement: no retry, no second
    validation request, no article-page fetch — exactly one GET to the
    exact registered feed URL for this source_id."""
    monkeypatch.setattr(diagnostics, "PILOT_FEEDS", (_NVDA_SOURCE,))
    call_log = []

    def _counted_fetch(feed_url):
        call_log.append(feed_url)
        return FeedFetchResult(entries=(_entry("Brand New Headline", "https://nvidianews.nvidia.com/news/brand-new"),), failure_code=None)

    monkeypatch.setattr(rss_atom_client, "fetch_entries", _counted_fetch)
    monkeypatch.setattr(diagnostics.rss_atom_client, "fetch_entries", _counted_fetch)

    diagnostics.diagnose_source_duplicates("nvidia-newsroom-rss", stories={})

    assert call_log == [_NVDA_SOURCE.feed_url]  # exactly one call, to exactly this URL


def test_diagnose_source_duplicates_never_writes_anything(monkeypatch, tmp_path):
    monkeypatch.setattr(diagnostics, "PILOT_FEEDS", (_NVDA_SOURCE,))
    monkeypatch.setattr(
        diagnostics.rss_atom_client, "fetch_entries",
        lambda feed_url: FeedFetchResult(entries=(_entry("Brand New Headline", "https://nvidianews.nvidia.com/news/brand-new"),), failure_code=None),
    )

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("a write-capable method was called")

    monkeypatch.setattr(daily_news_store, "upsert_new_stories", _fail_if_called)
    monkeypatch.setattr(daily_news_store, "update_story", _fail_if_called)

    result = diagnostics.diagnose_source_duplicates("nvidia-newsroom-rss", stories={})
    assert len(result) == 1
    assert not (tmp_path / "daily_news_stories.json").exists()  # nothing was ever written anywhere


def test_diagnose_source_duplicates_returns_empty_tuple_on_fetch_failure(monkeypatch):
    monkeypatch.setattr(diagnostics, "PILOT_FEEDS", (_NVDA_SOURCE,))
    monkeypatch.setattr(
        diagnostics.rss_atom_client, "fetch_entries",
        lambda feed_url: FeedFetchResult(entries=(), failure_code="HTTPError:403"),
    )
    result = diagnostics.diagnose_source_duplicates("nvidia-newsroom-rss", stories={})
    assert result == ()


def test_diagnose_source_duplicates_caps_output_at_20_entries(monkeypatch):
    monkeypatch.setattr(diagnostics, "PILOT_FEEDS", (_NVDA_SOURCE,))
    many_entries = tuple(
        _entry(f"Headline {i}", f"https://nvidianews.nvidia.com/news/item-{i}") for i in range(25)
    )
    monkeypatch.setattr(
        diagnostics.rss_atom_client, "fetch_entries",
        lambda feed_url: FeedFetchResult(entries=many_entries, failure_code=None),
    )
    result = diagnostics.diagnose_source_duplicates("nvidia-newsroom-rss", stories={})
    assert len(result) == diagnostics.MAX_DIAGNOSTIC_ENTRIES == 20
