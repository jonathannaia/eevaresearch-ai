"""Postgres-backed EditorialStory storage — Editorial Daily News
production-readiness fix (design/DECISIONS.md). Mirrors
daily_news_repository.py's own idempotent upsert/load pattern, simplified
to a single flat table: EditorialStory has no nested 1-to-many
collections the way NewsStory has sources/state_history, so no child
tables are needed here. matched_companies/matched_themes are stored as
JSON-TEXT columns, the same convention already used by
candidates.matched_rules_json.

Deliberately never imports src.models.models — only
src.models.daily_news_models, matching daily_news_repository.py's own
discipline.

Functions here never catch psycopg errors — a database failure
propagates as a real exception rather than becoming an empty result."""
from __future__ import annotations

import json

import psycopg

from src.data_access.postgres_state_db.connection import transaction
from src.models.daily_news_models import EditorialStory


def _row_to_story(row) -> EditorialStory:
    return EditorialStory(
        id=row["id"], headline=row["headline"], publisher=row["publisher"],
        source_url=row["source_url"], published_at=row["published_at"], retrieved_at=row["retrieved_at"],
        excerpt=row["excerpt"], matched_companies=tuple(json.loads(row["matched_companies_json"])),
        matched_themes=tuple(json.loads(row["matched_themes_json"])), source_feed_id=row["source_feed_id"],
    )


def load_stories(conn: psycopg.Connection) -> dict[str, EditorialStory]:
    rows = conn.execute("SELECT * FROM editorial_stories").fetchall()
    return {row["id"]: _row_to_story(row) for row in rows}


def _insert_story(conn: psycopg.Connection, story: EditorialStory) -> None:
    conn.execute(
        """
        INSERT INTO editorial_stories (
            id, headline, publisher, source_url, published_at, retrieved_at, excerpt,
            matched_companies_json, matched_themes_json, source_feed_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            story.id, story.headline, story.publisher, story.source_url, story.published_at,
            story.retrieved_at, story.excerpt, json.dumps(list(story.matched_companies)),
            json.dumps(list(story.matched_themes)), story.source_feed_id,
        ),
    )


def upsert_new_stories(conn: psycopg.Connection, new_stories: list[EditorialStory]) -> dict[str, EditorialStory]:
    """Same idempotent contract as editorial_story_store.py's own
    upsert_new_stories and daily_news_repository.py's own
    upsert_new_stories — skip if id already exists, insert if not."""
    with transaction(conn):
        for story in new_stories:
            exists = conn.execute("SELECT 1 FROM editorial_stories WHERE id = %s", (story.id,)).fetchone()
            if exists is not None:
                continue
            _insert_story(conn, story)
    return load_stories(conn)
