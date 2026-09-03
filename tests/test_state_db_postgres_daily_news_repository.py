"""Postgres-backed NewsStory storage — the isolated Postgres counterpart
to tests/test_state_db_daily_news_repository.py, against the real local
disposable Postgres test container. Every test uses pg_conn (an
isolated, already-migrated schema) and skips cleanly when no local
disposable Postgres instance is available — see
tests/_postgres_test_support.py."""
from __future__ import annotations

import psycopg
import pytest

from src.data_access.postgres_state_db import daily_news_repository
from src.models.daily_news_models import (
    NewsSourceReference,
    NewsStateTransition,
    NewsStory,
    NewsStoryStatus,
    SourceClass,
)

from tests._postgres_test_support import pg_conn, pg_isolated_connection  # noqa: F401


def _source(url: str = "https://nvidianews.nvidia.com/releases/one", **overrides) -> NewsSourceReference:
    defaults = dict(
        publisher="NVIDIA", source_class=SourceClass.OFFICIAL_COMPANY, url=url,
        title="NVIDIA announces new platform", published_at="2026-09-01T12:00:00+00:00",
        retrieved_at="2026-09-01T12:05:00+00:00", original_language="English",
        excerpt_original="NVIDIA today announced a new platform.",
    )
    defaults.update(overrides)
    return NewsSourceReference(**defaults)


def _story(story_id: str = "newsitem-nvidia-abc123", **overrides) -> NewsStory:
    defaults = dict(
        id=story_id, company_name="NVIDIA", ticker="NVDA", theme_slug="ai-buildout",
        headline="NVIDIA announces new platform", eeva_summary="NVIDIA today announced a new platform.",
        is_fallback_summary=False, translation_unavailable=False, original_title=None,
        sources=(_source(),), status=NewsStoryStatus.PUBLISHED,
        state_history=[NewsStateTransition(status=NewsStoryStatus.PUBLISHED, at="2026-09-01T12:05:00+00:00", detail="Discovered from official company feed.")],
    )
    defaults.update(overrides)
    return NewsStory(**defaults)


def test_fresh_schema_has_no_stories(pg_conn):
    assert daily_news_repository.load_stories(pg_conn) == {}
    assert daily_news_repository.get_story(pg_conn, "does-not-exist") is None


def test_upsert_new_stories_inserts_and_load_round_trips(pg_conn):
    story = _story()
    result = daily_news_repository.upsert_new_stories(pg_conn, [story])
    assert set(result.keys()) == {story.id}

    loaded = daily_news_repository.get_story(pg_conn, story.id)
    assert loaded is not None
    assert loaded.company_name == "NVIDIA"
    assert loaded.headline == "NVIDIA announces new platform"
    assert loaded.status == NewsStoryStatus.PUBLISHED


def test_upsert_new_stories_is_idempotent_on_repeated_id(pg_conn):
    story = _story()
    daily_news_repository.upsert_new_stories(pg_conn, [story])
    daily_news_repository.upsert_new_stories(pg_conn, [story])
    assert len(daily_news_repository.load_stories(pg_conn)) == 1


def test_upsert_new_stories_leaves_existing_entry_untouched(pg_conn):
    daily_news_repository.upsert_new_stories(pg_conn, [_story()])
    changed = _story(headline="A different headline entirely")
    daily_news_repository.upsert_new_stories(pg_conn, [changed])
    loaded = daily_news_repository.get_story(pg_conn, "newsitem-nvidia-abc123")
    assert loaded.headline == "NVIDIA announces new platform"


def test_source_and_summary_fields_round_trip_exactly(pg_conn):
    source = _source(
        url="https://nvidianews.nvidia.com/releases/full",
        title="Full headline text", excerpt_original="A concise extractive excerpt.",
        image_url="https://iprsoftwaremedia.com/image.jpg", image_alt="Product photo",
    )
    story = _story("newsitem-nvidia-full", sources=(source,), eeva_summary="A concise extractive excerpt.")
    daily_news_repository.upsert_new_stories(pg_conn, [story])

    reloaded = daily_news_repository.get_story(pg_conn, "newsitem-nvidia-full")
    assert len(reloaded.sources) == 1
    reloaded_source = reloaded.sources[0]
    assert reloaded_source.url == "https://nvidianews.nvidia.com/releases/full"
    assert reloaded_source.title == "Full headline text"
    assert reloaded_source.excerpt_original == "A concise extractive excerpt."
    assert reloaded_source.image_url == "https://iprsoftwaremedia.com/image.jpg"
    assert reloaded_source.image_alt == "Product photo"


def test_translation_unavailable_and_original_title_round_trip(pg_conn):
    story = _story(
        "newsitem-skhynix-1", company_name="SK Hynix", ticker="000660",
        eeva_summary=None, translation_unavailable=True, original_title="삼성전자 발표",
        sources=(_source(url="https://news.skhynix.com/en/feed/item-1", publisher="SK Hynix", original_language="Non-Latin script"),),
    )
    daily_news_repository.upsert_new_stories(pg_conn, [story])
    reloaded = daily_news_repository.get_story(pg_conn, "newsitem-skhynix-1")
    assert reloaded.translation_unavailable is True
    assert reloaded.original_title == "삼성전자 발표"
    assert reloaded.eeva_summary is None


def test_state_history_round_trips_in_append_order(pg_conn):
    story = _story(state_history=[
        NewsStateTransition(status=NewsStoryStatus.DISCOVERED, at="2026-09-01T12:00:00+00:00"),
        NewsStateTransition(status=NewsStoryStatus.SUMMARIZED, at="2026-09-01T12:01:00+00:00"),
        NewsStateTransition(status=NewsStoryStatus.PUBLISHED, at="2026-09-01T12:02:00+00:00", detail="Discovered from official company feed."),
    ])
    daily_news_repository.upsert_new_stories(pg_conn, [story])
    reloaded = daily_news_repository.get_story(pg_conn, story.id)
    assert [t.status for t in reloaded.state_history] == [
        NewsStoryStatus.DISCOVERED, NewsStoryStatus.SUMMARIZED, NewsStoryStatus.PUBLISHED,
    ]


def test_duplicate_canonical_url_across_different_story_ids_is_rejected(pg_conn):
    daily_news_repository.upsert_new_stories(pg_conn, [_story("newsitem-a", sources=(_source(url="https://nvidianews.nvidia.com/releases/dup"),))])
    pg_conn.rollback()  # clear the aborted-transaction state a failed statement leaves behind
    with pytest.raises(psycopg.errors.UniqueViolation):
        daily_news_repository.upsert_new_stories(pg_conn, [_story("newsitem-b", sources=(_source(url="https://nvidianews.nvidia.com/releases/dup"),))])
    pg_conn.rollback()


def test_same_story_id_with_same_url_is_a_no_op_not_a_constraint_violation(pg_conn):
    story = _story()
    daily_news_repository.upsert_new_stories(pg_conn, [story])
    daily_news_repository.upsert_new_stories(pg_conn, [story])  # must not raise
    assert len(daily_news_repository.load_stories(pg_conn)) == 1


def test_update_story_returns_not_found_for_unknown_id(pg_conn):
    outcome = daily_news_repository.update_story(pg_conn, _story("does-not-exist"), expected_version=1)
    assert outcome.status == "not_found"
    assert outcome.current is None


def test_update_story_succeeds_and_increments_version(pg_conn):
    daily_news_repository.upsert_new_stories(pg_conn, [_story()])
    assert daily_news_repository.get_story_version(pg_conn, "newsitem-nvidia-abc123") == 1

    stored = daily_news_repository.get_story(pg_conn, "newsitem-nvidia-abc123")
    stored.status = NewsStoryStatus.SUPPRESSED
    outcome = daily_news_repository.update_story(pg_conn, stored, expected_version=1)

    assert outcome.status == "updated"
    assert outcome.current.status == NewsStoryStatus.SUPPRESSED
    assert daily_news_repository.get_story_version(pg_conn, "newsitem-nvidia-abc123") == 2


def test_stale_expected_version_is_rejected_and_newer_record_preserved(pg_conn):
    daily_news_repository.upsert_new_stories(pg_conn, [_story()])
    stored = daily_news_repository.get_story(pg_conn, "newsitem-nvidia-abc123")
    stored.status = NewsStoryStatus.SUPPRESSED
    daily_news_repository.update_story(pg_conn, stored, expected_version=1)

    stale = daily_news_repository.get_story(pg_conn, "newsitem-nvidia-abc123")
    stale.headline = "A stale write attempt"
    outcome = daily_news_repository.update_story(pg_conn, stale, expected_version=1)

    assert outcome.status == "conflict"
    assert outcome.current.status == NewsStoryStatus.SUPPRESSED
    assert outcome.current.headline == "NVIDIA announces new platform"


def test_update_story_appends_new_sources_not_duplicate_existing(pg_conn):
    daily_news_repository.upsert_new_stories(pg_conn, [_story()])
    stored = daily_news_repository.get_story(pg_conn, "newsitem-nvidia-abc123")
    stored.sources = stored.sources + (_source(),)
    outcome = daily_news_repository.update_story(pg_conn, stored, expected_version=1)
    assert len(outcome.current.sources) == 1

    stored2 = daily_news_repository.get_story(pg_conn, "newsitem-nvidia-abc123")
    stored2.sources = stored2.sources + (_source(url="https://nvidianews.nvidia.com/releases/second"),)
    outcome2 = daily_news_repository.update_story(pg_conn, stored2, expected_version=2)
    assert len(outcome2.current.sources) == 2


def test_concurrent_upsert_of_the_same_new_story_id_is_safe(pg_conn, monkeypatch):
    """Same rationale as the SQLite suite's own equivalent test — proven
    serially (one connection here), confirming a second 'concurrent'
    upsert attempt for an already-inserted story id is a safe no-op."""
    story = _story()
    from src.data_access.postgres_state_db import daily_news_repository as repo_module

    real_insert = repo_module._insert_story
    call_count = {"n": 0}

    def _racy_insert(conn_, story_, now_):
        call_count["n"] += 1
        real_insert(conn_, story_, now_)

    monkeypatch.setattr(repo_module, "_insert_story", _racy_insert)

    daily_news_repository.upsert_new_stories(pg_conn, [story])
    assert call_count["n"] == 1
    daily_news_repository.upsert_new_stories(pg_conn, [story])
    assert call_count["n"] == 1
    assert len(daily_news_repository.load_stories(pg_conn)) == 1
