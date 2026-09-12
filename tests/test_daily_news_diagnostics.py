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


def _entry(title: str, link: str, summary: str | None = "A short description.") -> RawFeedEntry:
    return RawFeedEntry(title=title, link=link, published_at="2026-08-24T12:00:00+00:00", summary=summary)


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
