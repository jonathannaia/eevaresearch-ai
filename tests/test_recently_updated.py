"""Phase 2D — Recently Updated's Dashboard-facing preload seam.

Offline only: the Daily News repository factory, the reconciliation
pass, and the editorial visibility helper are all replaced with
counting fakes, so no database, connection, credential, or network is
involved. The counts below are the measurement that justifies the
hoist — they are asserted, not assumed.

Recently Updated consumes CANONICAL (already-reconciled) stories. The
raw representation Theme Activity consumes is a different thing and
must never be substituted here; test_dashboard_source_reads.py holds
the cross-section proof.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.config.settings import Settings
from src.data_access.daily_news import daily_news_backend, daily_news_pipeline
from src.models.daily_news_models import (
    EditorialStory,
    NewsMaterialityTier,
    NewsSourceReference,
    NewsStateTransition,
    NewsStory,
    NewsStoryStatus,
    SourceClass,
)
from src.ui.components import editorial_coverage, recently_updated

PUBLISHED_AT = "2026-09-20T12:00:00+00:00"
NOW = datetime(2026, 9, 20, 18, 0, tzinfo=timezone.utc)


def _settings(tmp_path) -> Settings:
    return Settings(db_backend="json", cache_dir=tmp_path)


def _story(story_id: str, company_name: str, headline: str, url: str) -> NewsStory:
    return NewsStory(
        id=story_id, company_name=company_name, ticker="TCK", theme_slug="ai-buildout",
        headline=headline, eeva_summary="Summary text.", is_fallback_summary=False,
        translation_unavailable=False, original_title=None,
        sources=(
            NewsSourceReference(
                publisher=company_name, source_class=SourceClass.OFFICIAL_COMPANY, url=url,
                title=headline, published_at=PUBLISHED_AT, retrieved_at=PUBLISHED_AT,
                original_language="English", excerpt_original="Summary text.",
            ),
        ),
        status=NewsStoryStatus.PUBLISHED,
        state_history=[NewsStateTransition(status=NewsStoryStatus.PUBLISHED, at=PUBLISHED_AT)],
        # Explicit tier: a legacy None is tiered at read time and would
        # land in Background, which this default preview excludes — the
        # fixture would then pass vacuously on an empty row list.
        materiality_tier=NewsMaterialityTier.HIGH_SIGNAL,
    )


def _editorial(story_id: str, headline: str, company: str) -> EditorialStory:
    return EditorialStory(
        id=story_id, headline=headline, publisher="Fictional Wire",
        source_url=f"https://example.test/{story_id}", published_at=PUBLISHED_AT,
        retrieved_at=PUBLISHED_AT, excerpt=None, matched_companies=(company,),
        matched_themes=("ai-buildout",), source_feed_id="feed-1",
        materiality_tier=NewsMaterialityTier.HIGH_SIGNAL,
    )


class _Counter:
    def __init__(self) -> None:
        self.repository_constructions = 0
        self.story_loads = 0
        self.reconciliations = 0
        self.editorial_visibility_calls = 0


@pytest.fixture
def counting_daily_news(monkeypatch):
    """Counts every construction/load/reconcile Recently Updated could
    perform. A preloaded render must leave all four counters at zero."""
    counter = _Counter()
    stored = {"s-loaded": _story("s-loaded", "Loaded Co", "Loaded from the repository", "https://example.test/loaded")}

    class _Repo:
        def load_stories(self):
            counter.story_loads += 1
            return dict(stored)

    def _get_repo(settings):
        counter.repository_constructions += 1
        return _Repo()

    def _select_canonical(stories, cache_dir):
        counter.reconciliations += 1
        return dict(stories)

    def _visible_editorial(settings):
        counter.editorial_visibility_calls += 1
        return (_editorial("e-loaded", "Editorial from the helper", "Loaded Co"),)

    monkeypatch.setattr(daily_news_backend, "get_daily_news_repository", _get_repo)
    monkeypatch.setattr(daily_news_pipeline, "select_canonical_stories", _select_canonical)
    monkeypatch.setattr(editorial_coverage, "get_visible_editorial_stories", _visible_editorial)
    monkeypatch.setattr(recently_updated, "get_visible_editorial_stories", _visible_editorial)
    return counter


def _titles(rows) -> list[str]:
    return [r.title for r in rows]


# --- preloaded: nothing is constructed, nothing is loaded ----------------

def test_preloaded_daily_news_stories_construct_no_repository(tmp_path, counting_daily_news):
    preloaded = {"s-pre": _story("s-pre", "Preloaded Co", "Preloaded headline", "https://example.test/pre")}

    rows = recently_updated._load_daily_news_rows(_settings(tmp_path), NOW, preloaded)

    assert counting_daily_news.repository_constructions == 0
    assert counting_daily_news.story_loads == 0
    assert _titles(rows) == ["Preloaded headline"]


def test_preloaded_stories_are_not_reconciled_a_second_time(tmp_path, counting_daily_news):
    """The caller already ran select_canonical_stories(); running it
    again here would be a redundant pass over an already-canonical set."""
    preloaded = {"s-pre": _story("s-pre", "Preloaded Co", "Preloaded headline", "https://example.test/pre")}

    recently_updated._load_daily_news_rows(_settings(tmp_path), NOW, preloaded)

    assert counting_daily_news.reconciliations == 0


def test_preloaded_editorial_stories_call_no_visibility_helper(tmp_path, counting_daily_news):
    preloaded = (_editorial("e-pre", "Preloaded editorial headline", "Preloaded Co"),)

    rows = recently_updated._load_editorial_rows(_settings(tmp_path), NOW, preloaded)

    assert counting_daily_news.editorial_visibility_calls == 0
    assert counting_daily_news.repository_constructions == 0
    assert _titles(rows) == ["Preloaded editorial headline"]


def test_both_preloaded_through_the_public_selection_reads_nothing(tmp_path, counting_daily_news):
    rows = recently_updated._select_recently_updated_rows(
        _settings(tmp_path), now=NOW,
        preloaded_daily_news_stories={"s-pre": _story("s-pre", "Preloaded Co", "Preloaded headline", "https://example.test/pre")},
        preloaded_editorial_stories=(_editorial("e-pre", "Preloaded editorial headline", "Preloaded Co"),),
    )

    assert counting_daily_news.repository_constructions == 0
    assert counting_daily_news.story_loads == 0
    assert counting_daily_news.reconciliations == 0
    assert counting_daily_news.editorial_visibility_calls == 0
    assert sorted(_titles(rows)) == ["Preloaded editorial headline", "Preloaded headline"]


# --- unpreloaded: the existing path is byte-for-byte unchanged -----------

def test_without_preloaded_stories_it_loads_and_reconciles_exactly_as_before(tmp_path, counting_daily_news):
    rows = recently_updated._load_daily_news_rows(_settings(tmp_path), NOW)

    assert counting_daily_news.repository_constructions == 1
    assert counting_daily_news.story_loads == 1
    assert counting_daily_news.reconciliations == 1
    assert _titles(rows) == ["Loaded from the repository"]


def test_without_preloaded_editorial_it_calls_the_visibility_helper_as_before(tmp_path, counting_daily_news):
    rows = recently_updated._load_editorial_rows(_settings(tmp_path), NOW)

    assert counting_daily_news.editorial_visibility_calls == 1
    assert _titles(rows) == ["Editorial from the helper"]


def test_an_empty_preloaded_mapping_is_honored_not_treated_as_absent(tmp_path, counting_daily_news):
    """{} is a real answer ("no stories"), distinct from None ("load
    them yourself") — conflating them would silently restore the load."""
    rows = recently_updated._load_daily_news_rows(_settings(tmp_path), NOW, {})

    assert rows == []
    assert counting_daily_news.repository_constructions == 0
    assert counting_daily_news.story_loads == 0


def test_an_empty_preloaded_editorial_tuple_is_honored_not_treated_as_absent(tmp_path, counting_daily_news):
    rows = recently_updated._load_editorial_rows(_settings(tmp_path), NOW, ())

    assert rows == []
    assert counting_daily_news.editorial_visibility_calls == 0


# --- preloaded and unpreloaded select the same rows ----------------------

def test_preloaded_and_unpreloaded_paths_select_identical_daily_news_rows(tmp_path, counting_daily_news):
    settings = _settings(tmp_path)
    loaded = recently_updated._load_daily_news_rows(settings, NOW)

    same_input = {"s-loaded": _story("s-loaded", "Loaded Co", "Loaded from the repository", "https://example.test/loaded")}
    preloaded = recently_updated._load_daily_news_rows(settings, NOW, same_input)

    assert [(r.title, r.company_name, r.source_url) for r in loaded] \
        == [(r.title, r.company_name, r.source_url) for r in preloaded]


def test_preloaded_and_unpreloaded_paths_select_identical_editorial_rows(tmp_path, counting_daily_news):
    settings = _settings(tmp_path)
    loaded = recently_updated._load_editorial_rows(settings, NOW)
    preloaded = recently_updated._load_editorial_rows(
        settings, NOW, (_editorial("e-loaded", "Editorial from the helper", "Loaded Co"),)
    )

    assert [(r.title, r.company_name, r.source_url) for r in loaded] \
        == [(r.title, r.company_name, r.source_url) for r in preloaded]


# --- existing gates still apply to preloaded input -----------------------

def test_a_background_tier_preloaded_editorial_story_is_still_excluded(tmp_path, counting_daily_news):
    background = EditorialStory(
        id="e-bg", headline="Background editorial headline", publisher="Fictional Wire",
        source_url="https://example.test/bg", published_at=PUBLISHED_AT, retrieved_at=PUBLISHED_AT,
        excerpt=None, matched_companies=("Preloaded Co",), matched_themes=("ai-buildout",),
        source_feed_id="feed-1", materiality_tier=NewsMaterialityTier.BACKGROUND,
    )

    rows = recently_updated._load_editorial_rows(_settings(tmp_path), NOW, (background,))

    assert rows == []


def test_a_non_published_preloaded_story_is_still_excluded(tmp_path, counting_daily_news):
    from dataclasses import replace

    draft = replace(
        _story("s-draft", "Draft Co", "Draft headline", "https://example.test/draft"),
        status=NewsStoryStatus.DISCOVERED,
    )

    rows = recently_updated._load_daily_news_rows(_settings(tmp_path), NOW, {"s-draft": draft})

    assert rows == []
