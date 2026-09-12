"""Postgres-backed EditorialStory storage — the isolated Postgres
counterpart to editorial_story_store.py's own JSON-file tests, against
the real local disposable Postgres test container. Every test uses
pg_conn (an isolated, already-migrated schema) and skips cleanly when no
local disposable Postgres instance is available — see
tests/_postgres_test_support.py."""
from __future__ import annotations

import datetime

from src.data_access.postgres_state_db import editorial_story_repository
from src.models.daily_news_models import EditorialStory

from tests._postgres_test_support import pg_conn, pg_isolated_connection  # noqa: F401


def _story(story_id: str = "editorial-abc", **overrides) -> EditorialStory:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    defaults = dict(
        id=story_id, headline="Oracle Corporation reports strong AI cloud demand", publisher="CNBC",
        source_url="https://www.cnbc.com/2026/09/11/oracle-ai-cloud.html", published_at=now, retrieved_at=now,
        excerpt="Oracle Corporation said AI cloud demand drove revenue higher.",
        matched_companies=("Oracle Corporation",), matched_themes=("ai-buildout",),
        source_feed_id="cnbc-technology-rss",
    )
    defaults.update(overrides)
    return EditorialStory(**defaults)


def test_fresh_schema_has_no_editorial_stories(pg_conn):
    assert editorial_story_repository.load_stories(pg_conn) == {}


def test_upsert_new_stories_inserts_and_load_round_trips(pg_conn):
    story = _story()
    result = editorial_story_repository.upsert_new_stories(pg_conn, [story])
    assert set(result.keys()) == {story.id}

    loaded = editorial_story_repository.load_stories(pg_conn)[story.id]
    assert loaded.headline == story.headline
    assert loaded.publisher == "CNBC"
    assert loaded.source_url == story.source_url
    assert loaded.published_at == story.published_at
    assert loaded.retrieved_at == story.retrieved_at
    assert loaded.excerpt == story.excerpt
    assert loaded.matched_companies == ("Oracle Corporation",)
    assert loaded.matched_themes == ("ai-buildout",)
    assert loaded.source_feed_id == "cnbc-technology-rss"


def test_excerpt_none_round_trips_as_none(pg_conn):
    story = _story(excerpt=None)
    editorial_story_repository.upsert_new_stories(pg_conn, [story])
    loaded = editorial_story_repository.load_stories(pg_conn)[story.id]
    assert loaded.excerpt is None


def test_multiple_matched_companies_and_themes_round_trip_in_order(pg_conn):
    story = _story(
        matched_companies=("Oracle Corporation", "Corning Inc."),
        matched_themes=("ai-buildout", "photonics"),
    )
    editorial_story_repository.upsert_new_stories(pg_conn, [story])
    loaded = editorial_story_repository.load_stories(pg_conn)[story.id]
    assert loaded.matched_companies == ("Oracle Corporation", "Corning Inc.")
    assert loaded.matched_themes == ("ai-buildout", "photonics")


def test_empty_matched_companies_and_themes_round_trip_as_empty_tuples(pg_conn):
    story = _story(matched_companies=(), matched_themes=("memory",))
    editorial_story_repository.upsert_new_stories(pg_conn, [story])
    loaded = editorial_story_repository.load_stories(pg_conn)[story.id]
    assert loaded.matched_companies == ()
    assert loaded.matched_themes == ("memory",)


def test_upsert_new_stories_is_idempotent_on_repeated_id(pg_conn):
    story = _story()
    editorial_story_repository.upsert_new_stories(pg_conn, [story])
    editorial_story_repository.upsert_new_stories(pg_conn, [story])
    assert len(editorial_story_repository.load_stories(pg_conn)) == 1


def test_upsert_new_stories_leaves_existing_entry_untouched(pg_conn):
    editorial_story_repository.upsert_new_stories(pg_conn, [_story()])
    changed = _story(headline="A different headline entirely")
    editorial_story_repository.upsert_new_stories(pg_conn, [changed])
    loaded = editorial_story_repository.load_stories(pg_conn)["editorial-abc"]
    assert loaded.headline == "Oracle Corporation reports strong AI cloud demand"


def test_upsert_new_stories_across_two_calls_accumulates(pg_conn):
    editorial_story_repository.upsert_new_stories(pg_conn, [_story("editorial-a")])
    editorial_story_repository.upsert_new_stories(pg_conn, [_story("editorial-b")])
    loaded = editorial_story_repository.load_stories(pg_conn)
    assert set(loaded.keys()) == {"editorial-a", "editorial-b"}
