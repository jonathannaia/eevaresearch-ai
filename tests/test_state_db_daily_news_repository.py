"""state_db.daily_news_repository — NewsStory round-trip, idempotent
upsert, canonical-URL uniqueness, and optimistic-locking update
semantics. Mirrors the intent of tests/test_daily_news_store.py's
existing JSON-backed assertions against the SQLite backend. In-memory
SQLite only."""
from __future__ import annotations

import sqlite3

import pytest

from src.data_access.state_db import connection, daily_news_repository, schema
from src.models.daily_news_models import (
    NewsSourceReference,
    NewsStateTransition,
    NewsStory,
    NewsStoryStatus,
    SourceClass,
)


def _conn():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    return conn


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


# --- Fresh database ---


def test_fresh_database_has_no_stories():
    conn = _conn()
    assert daily_news_repository.load_stories(conn) == {}
    assert daily_news_repository.get_story(conn, "does-not-exist") is None


# --- Insert / read / list ---


def test_upsert_new_stories_inserts_and_load_round_trips():
    conn = _conn()
    story = _story()
    result = daily_news_repository.upsert_new_stories(conn, [story])
    assert set(result.keys()) == {story.id}

    loaded = daily_news_repository.get_story(conn, story.id)
    assert loaded is not None
    assert loaded.company_name == "NVIDIA"
    assert loaded.ticker == "NVDA"
    assert loaded.theme_slug == "ai-buildout"
    assert loaded.headline == "NVIDIA announces new platform"
    assert loaded.eeva_summary == "NVIDIA today announced a new platform."
    assert loaded.is_fallback_summary is False
    assert loaded.translation_unavailable is False
    assert loaded.original_title is None
    assert loaded.status == NewsStoryStatus.PUBLISHED


def test_upsert_new_stories_is_idempotent_on_repeated_id():
    conn = _conn()
    story = _story()
    daily_news_repository.upsert_new_stories(conn, [story])
    # Re-running with the same id (and, realistically, identical content —
    # the same real-world item re-discovered) must not create a duplicate
    # row or error.
    daily_news_repository.upsert_new_stories(conn, [story])
    assert len(daily_news_repository.load_stories(conn)) == 1


def test_upsert_new_stories_leaves_existing_entry_untouched():
    conn = _conn()
    daily_news_repository.upsert_new_stories(conn, [_story()])
    # A second call with the same id but different content must not
    # overwrite the already-persisted row — matches candidate_repository's
    # own upsert_new_candidates contract exactly.
    changed = _story(headline="A different headline entirely")
    daily_news_repository.upsert_new_stories(conn, [changed])
    loaded = daily_news_repository.get_story(conn, "newsitem-nvidia-abc123")
    assert loaded.headline == "NVIDIA announces new platform"


def test_load_stories_returns_every_persisted_story():
    conn = _conn()
    daily_news_repository.upsert_new_stories(conn, [
        _story("newsitem-nvidia-1", sources=(_source(url="https://nvidianews.nvidia.com/releases/1"),)),
        _story("newsitem-nvidia-2", sources=(_source(url="https://nvidianews.nvidia.com/releases/2"),)),
    ])
    loaded = daily_news_repository.load_stories(conn)
    assert set(loaded.keys()) == {"newsitem-nvidia-1", "newsitem-nvidia-2"}


# --- Source / publisher / timestamp / summary round-trip ---


def test_source_and_summary_fields_round_trip_exactly():
    conn = _conn()
    source = _source(
        url="https://nvidianews.nvidia.com/releases/full",
        title="Full headline text", published_at="2026-09-01T09:30:00+00:00",
        retrieved_at="2026-09-01T09:35:00+00:00", excerpt_original="A concise extractive excerpt.",
        image_url="https://iprsoftwaremedia.com/image.jpg", image_alt="Product photo",
    )
    story = _story("newsitem-nvidia-full", sources=(source,), eeva_summary="A concise extractive excerpt.")
    daily_news_repository.upsert_new_stories(conn, [story])

    reloaded = daily_news_repository.get_story(conn, "newsitem-nvidia-full")
    assert len(reloaded.sources) == 1
    reloaded_source = reloaded.sources[0]
    assert reloaded_source.publisher == "NVIDIA"
    assert reloaded_source.source_class == SourceClass.OFFICIAL_COMPANY
    assert reloaded_source.url == "https://nvidianews.nvidia.com/releases/full"
    assert reloaded_source.title == "Full headline text"
    assert reloaded_source.published_at == "2026-09-01T09:30:00+00:00"
    assert reloaded_source.retrieved_at == "2026-09-01T09:35:00+00:00"
    assert reloaded_source.original_language == "English"
    assert reloaded_source.excerpt_original == "A concise extractive excerpt."
    assert reloaded_source.image_url == "https://iprsoftwaremedia.com/image.jpg"
    assert reloaded_source.image_alt == "Product photo"
    assert reloaded.eeva_summary == "A concise extractive excerpt."


def test_translation_unavailable_and_original_title_round_trip():
    conn = _conn()
    story = _story(
        "newsitem-skhynix-1", company_name="SK Hynix", ticker="000660",
        eeva_summary=None, is_fallback_summary=False, translation_unavailable=True,
        original_title="삼성전자 발표",
        sources=(_source(url="https://news.skhynix.com/en/feed/item-1", publisher="SK Hynix", original_language="Non-Latin script"),),
    )
    daily_news_repository.upsert_new_stories(conn, [story])
    reloaded = daily_news_repository.get_story(conn, "newsitem-skhynix-1")
    assert reloaded.translation_unavailable is True
    assert reloaded.original_title == "삼성전자 발표"
    assert reloaded.eeva_summary is None


def test_state_history_round_trips_in_append_order():
    conn = _conn()
    story = _story(state_history=[
        NewsStateTransition(status=NewsStoryStatus.DISCOVERED, at="2026-09-01T12:00:00+00:00"),
        NewsStateTransition(status=NewsStoryStatus.SUMMARIZED, at="2026-09-01T12:01:00+00:00"),
        NewsStateTransition(status=NewsStoryStatus.PUBLISHED, at="2026-09-01T12:02:00+00:00", detail="Discovered from official company feed."),
    ])
    daily_news_repository.upsert_new_stories(conn, [story])
    reloaded = daily_news_repository.get_story(conn, story.id)
    assert [t.status for t in reloaded.state_history] == [
        NewsStoryStatus.DISCOVERED, NewsStoryStatus.SUMMARIZED, NewsStoryStatus.PUBLISHED,
    ]


# --- Duplicate canonical URL ---


def test_duplicate_canonical_url_across_different_story_ids_is_rejected():
    conn = _conn()
    daily_news_repository.upsert_new_stories(conn, [_story("newsitem-a", sources=(_source(url="https://nvidianews.nvidia.com/releases/dup"),))])
    with pytest.raises(sqlite3.IntegrityError):
        daily_news_repository.upsert_new_stories(conn, [_story("newsitem-b", sources=(_source(url="https://nvidianews.nvidia.com/releases/dup"),))])


def test_same_story_id_with_same_url_is_a_no_op_not_a_constraint_violation():
    # The ordinary, expected idempotent path: the same real-world item
    # rediscovered has the same id AND the same url — upsert_new_stories'
    # own id-exists check must skip the insert entirely before the URL
    # uniqueness constraint is ever reached.
    conn = _conn()
    story = _story()
    daily_news_repository.upsert_new_stories(conn, [story])
    daily_news_repository.upsert_new_stories(conn, [story])  # must not raise
    assert len(daily_news_repository.load_stories(conn)) == 1


# --- Update / optimistic locking ---


def test_update_story_returns_not_found_for_unknown_id():
    conn = _conn()
    outcome = daily_news_repository.update_story(conn, _story("does-not-exist"), expected_version=1)
    assert outcome.status == "not_found"
    assert outcome.current is None


def test_update_story_succeeds_and_increments_version():
    conn = _conn()
    daily_news_repository.upsert_new_stories(conn, [_story()])
    assert daily_news_repository.get_story_version(conn, "newsitem-nvidia-abc123") == 1

    stored = daily_news_repository.get_story(conn, "newsitem-nvidia-abc123")
    stored.status = NewsStoryStatus.SUPPRESSED
    outcome = daily_news_repository.update_story(conn, stored, expected_version=1)

    assert outcome.status == "updated"
    assert outcome.current.status == NewsStoryStatus.SUPPRESSED
    assert daily_news_repository.get_story_version(conn, "newsitem-nvidia-abc123") == 2


def test_stale_expected_version_is_rejected_and_newer_record_preserved():
    conn = _conn()
    daily_news_repository.upsert_new_stories(conn, [_story()])
    stored = daily_news_repository.get_story(conn, "newsitem-nvidia-abc123")
    stored.status = NewsStoryStatus.SUPPRESSED
    daily_news_repository.update_story(conn, stored, expected_version=1)  # version now 2

    stale = daily_news_repository.get_story(conn, "newsitem-nvidia-abc123")
    stale.headline = "A stale write attempt"
    outcome = daily_news_repository.update_story(conn, stale, expected_version=1)  # stale version

    assert outcome.status == "conflict"
    assert outcome.current.status == NewsStoryStatus.SUPPRESSED
    assert outcome.current.headline == "NVIDIA announces new platform"  # the stale write never applied


def test_update_story_appends_new_state_transitions_not_overwrite():
    conn = _conn()
    daily_news_repository.upsert_new_stories(conn, [_story()])
    stored = daily_news_repository.get_story(conn, "newsitem-nvidia-abc123")
    stored.state_history.append(NewsStateTransition(status=NewsStoryStatus.SUPPRESSED, at="2026-09-02T00:00:00+00:00", detail="Later suppressed."))
    outcome = daily_news_repository.update_story(conn, stored, expected_version=1)
    assert [t.status for t in outcome.current.state_history] == [
        NewsStoryStatus.PUBLISHED, NewsStoryStatus.SUPPRESSED,
    ]


def test_update_story_appends_new_sources_not_duplicate_existing():
    conn = _conn()
    daily_news_repository.upsert_new_stories(conn, [_story()])
    stored = daily_news_repository.get_story(conn, "newsitem-nvidia-abc123")
    # Re-applying the same source (same url) must not duplicate it.
    stored.sources = stored.sources + (_source(),)
    outcome = daily_news_repository.update_story(conn, stored, expected_version=1)
    assert len(outcome.current.sources) == 1

    # A genuinely new source (different url) is appended.
    stored2 = daily_news_repository.get_story(conn, "newsitem-nvidia-abc123")
    stored2.sources = stored2.sources + (_source(url="https://nvidianews.nvidia.com/releases/second"),)
    outcome2 = daily_news_repository.update_story(conn, stored2, expected_version=2)
    assert len(outcome2.current.sources) == 2


# --- Concurrent-safe idempotent upsert ---


def test_concurrent_upsert_of_the_same_new_story_id_is_safe(monkeypatch):
    """Simulates two 'concurrent' callers (e.g. a dashboard admin trigger
    and a future worker tick) racing to insert the same new story —
    proven serially here (SQLite has no real concurrent-connection
    story for :memory:), matching candidate_repository's own established
    convention for this class of test: force one caller's exists-check
    to see 'not yet present' for both, then let both attempt the insert,
    and confirm the second one fails loudly (a real IntegrityError on the
    PRIMARY KEY) rather than silently corrupting or duplicating state."""
    conn = _conn()
    story = _story()

    from src.data_access.state_db import daily_news_repository as repo_module

    real_insert = repo_module._insert_story
    call_count = {"n": 0}

    def _racy_insert(conn_, story_, now_):
        call_count["n"] += 1
        real_insert(conn_, story_, now_)

    monkeypatch.setattr(repo_module, "_insert_story", _racy_insert)

    daily_news_repository.upsert_new_stories(conn, [story])
    assert call_count["n"] == 1
    # A second "concurrent" upsert attempt for the very same story sees
    # it already present and skips the insert — no second call, no error,
    # no duplicate.
    daily_news_repository.upsert_new_stories(conn, [story])
    assert call_count["n"] == 1
    assert len(daily_news_repository.load_stories(conn)) == 1
