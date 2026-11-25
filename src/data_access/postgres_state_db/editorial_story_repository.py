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
from datetime import datetime, timezone

import psycopg

from src.data_access.postgres_state_db.connection import transaction
from src.models.daily_news_models import EditorialStory, NewsMaterialityTier

#: Mirrors editorial_reclassification.REVISION_SOURCE. Duplicated as a
#: literal rather than imported, so this repository keeps its existing
#: rule of importing nothing from the daily_news package.
REVISION_SOURCE = "window_reclassification"


def _row_to_story(row) -> EditorialStory:
    materiality_tier_raw = row["materiality_tier"]
    return EditorialStory(
        id=row["id"], headline=row["headline"], publisher=row["publisher"],
        source_url=row["source_url"], published_at=row["published_at"], retrieved_at=row["retrieved_at"],
        excerpt=row["excerpt"], matched_companies=tuple(json.loads(row["matched_companies_json"])),
        matched_themes=tuple(json.loads(row["matched_themes_json"])), source_feed_id=row["source_feed_id"],
        # Materiality classification (design/DECISIONS.md) — additive,
        # safe-default: NULL for every row persisted before this column
        # existed (never backfilled — see NewsMaterialityTier's own
        # docstring).
        materiality_tier=NewsMaterialityTier(materiality_tier_raw) if materiality_tier_raw else None,
        materiality_reasons=tuple(json.loads(row["materiality_reasons"])) if row["materiality_reasons"] else (),
        # Raw company recognition (design/DAILY_NEWS_IDENTIFIED_VS_
        # MATCHED_COMPANIES_DISCOVERY_2026_09_16.md) — column is
        # NOT NULL DEFAULT '[]' (schema.py V21), so an unconditional
        # read is safe, same convention as matched_companies_json above.
        identified_companies=tuple(json.loads(row["identified_companies_json"])),
        # Related coverage (schema V30) — NULL for a survivor, which is
        # every row persisted before this column existed.
        duplicate_of_story_id=row["duplicate_of_story_id"],
    )


def load_stories(conn: psycopg.Connection) -> dict[str, EditorialStory]:
    rows = conn.execute("SELECT * FROM editorial_stories").fetchall()
    return {row["id"]: _row_to_story(row) for row in rows}


def _insert_story(conn: psycopg.Connection, story: EditorialStory) -> None:
    conn.execute(
        """
        INSERT INTO editorial_stories (
            id, headline, publisher, source_url, published_at, retrieved_at, excerpt,
            matched_companies_json, matched_themes_json, source_feed_id,
            materiality_tier, materiality_reasons, identified_companies_json,
            duplicate_of_story_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            story.id, story.headline, story.publisher, story.source_url, story.published_at,
            story.retrieved_at, story.excerpt, json.dumps(list(story.matched_companies)),
            json.dumps(list(story.matched_themes)), story.source_feed_id,
            story.materiality_tier.value if story.materiality_tier else None,
            json.dumps(list(story.materiality_reasons)) if story.materiality_reasons else None,
            json.dumps(list(story.identified_companies)),
            story.duplicate_of_story_id,
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


def apply_reclassification(conn: psycopg.Connection, changes) -> int:
    """Apply window re-classification corrections (see
    editorial_reclassification.py), writing one append-only revision
    row per change in the SAME transaction as the story update.

    The pairing is the point: a story's tier and companies can only
    change here alongside a record of what they were and why. If the
    revision insert fails the story update rolls back with it, so the
    feed can never hold a corrected row with no explanation for it.

    `changes` is an iterable of ReclassificationChange; this module
    reads only its public fields and imports nothing from the
    daily_news package, same convention as the rest of this file.
    Returns the number of stories updated."""
    updated = 0
    with transaction(conn):
        for change in changes:
            revised_at = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT INTO editorial_story_revisions (
                    story_id, revised_at, old_tier, new_tier,
                    old_companies_json, new_companies_json, reason, revision_source
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    change.story_id, revised_at,
                    change.old_tier.value if change.old_tier else None,
                    change.new_tier.value,
                    json.dumps(list(change.old_companies)),
                    json.dumps(list(change.new_companies)),
                    change.reason,
                    REVISION_SOURCE,
                ),
            )
            conn.execute(
                """
                UPDATE editorial_stories
                SET materiality_tier = %s, materiality_reasons = %s,
                    matched_companies_json = %s, duplicate_of_story_id = %s
                WHERE id = %s
                """,
                (
                    change.new_tier.value,
                    json.dumps(list(change.new_reasons)),
                    json.dumps(list(change.new_companies)),
                    change.duplicate_of_story_id,
                    change.story_id,
                ),
            )
            updated += 1
    return updated


def load_revisions(conn: psycopg.Connection, story_id: str) -> list[dict]:
    """Every recorded correction for one story, oldest first. Read-only
    — nothing in this module ever updates or deletes a revision row."""
    rows = conn.execute(
        "SELECT * FROM editorial_story_revisions WHERE story_id = %s ORDER BY revised_at",
        (story_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def load_related_coverage(conn: psycopg.Connection, story_id: str) -> list[EditorialStory]:
    """Every story linked to this one as a duplicate, oldest first —
    the "+N related reports" a card shows beneath itself. Returns []
    for a story nothing duplicates, which is almost every story."""
    rows = conn.execute(
        "SELECT * FROM editorial_stories WHERE duplicate_of_story_id = %s ORDER BY published_at",
        (story_id,),
    ).fetchall()
    return [_row_to_story(row) for row in rows]
