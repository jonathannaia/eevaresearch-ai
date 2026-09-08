"""Postgres-backed NewsStory storage — the isolated Postgres counterpart
to src/data_access/state_db/daily_news_repository.py, mirrored
symbol-for-symbol. See that module's own docstring for the full
read-modify-write and optimistic-concurrency rationale; only genuine
Postgres differences are noted here: `%s` placeholders and dict-row
access (via connection.py's row_factory).

Deliberately never imports src.models.models (see daily_news_models.py's
own module docstring) — only src.models.daily_news_models.

Functions here never catch psycopg errors — a database failure
propagates as a real exception rather than becoming an empty result.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import psycopg

from src.data_access.postgres_state_db.connection import transaction
from src.models.daily_news_models import (
    NewsSourceReference,
    NewsStateTransition,
    NewsStory,
    NewsStoryStatus,
    SourceClass,
)


@dataclass(frozen=True)
class UpdateOutcome:
    """Same shape as state_db/daily_news_repository.UpdateOutcome — see
    that module's own docstring.

    status is one of "updated" | "conflict" | "not_found"."""

    status: str
    current: NewsStory | None


def _row_to_story(conn: psycopg.Connection, row) -> NewsStory:
    source_rows = conn.execute(
        "SELECT publisher, source_class, url, title, published_at, retrieved_at, original_language, "
        "excerpt_original, image_url, image_alt FROM daily_news_sources WHERE story_id = %s ORDER BY id ASC",
        (row["id"],),
    ).fetchall()
    sources = tuple(
        NewsSourceReference(
            publisher=s["publisher"], source_class=SourceClass(s["source_class"]), url=s["url"],
            title=s["title"], published_at=s["published_at"], retrieved_at=s["retrieved_at"],
            original_language=s["original_language"], excerpt_original=s["excerpt_original"],
            image_url=s["image_url"], image_alt=s["image_alt"],
        )
        for s in source_rows
    )
    history_rows = conn.execute(
        "SELECT status, at, detail FROM daily_news_state_transitions WHERE story_id = %s ORDER BY id ASC",
        (row["id"],),
    ).fetchall()
    state_history = [
        NewsStateTransition(status=NewsStoryStatus(h["status"]), at=h["at"], detail=h["detail"])
        for h in history_rows
    ]
    return NewsStory(
        id=row["id"], company_name=row["company_name"], ticker=row["ticker"],
        theme_slug=row["theme_slug"], headline=row["headline"], eeva_summary=row["eeva_summary"],
        is_fallback_summary=bool(row["is_fallback_summary"]),
        translation_unavailable=bool(row["translation_unavailable"]),
        original_title=row["original_title"], sources=sources,
        status=NewsStoryStatus(row["status"]), state_history=state_history,
    )


def get_story(conn: psycopg.Connection, story_id: str) -> NewsStory | None:
    row = conn.execute("SELECT * FROM daily_news_stories WHERE id = %s", (story_id,)).fetchone()
    return _row_to_story(conn, row) if row is not None else None


def get_story_version(conn: psycopg.Connection, story_id: str) -> int | None:
    row = conn.execute("SELECT version FROM daily_news_stories WHERE id = %s", (story_id,)).fetchone()
    return row["version"] if row is not None else None


def load_stories(conn: psycopg.Connection) -> dict[str, NewsStory]:
    rows = conn.execute("SELECT id FROM daily_news_stories").fetchall()
    return {row["id"]: get_story(conn, row["id"]) for row in rows}


def _insert_source(conn: psycopg.Connection, story_id: str, source: NewsSourceReference) -> None:
    conn.execute(
        """
        INSERT INTO daily_news_sources (
            story_id, publisher, source_class, url, title, published_at, retrieved_at,
            original_language, excerpt_original, image_url, image_alt
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            story_id, source.publisher, source.source_class.value, source.url, source.title,
            source.published_at, source.retrieved_at, source.original_language,
            source.excerpt_original, source.image_url, source.image_alt,
        ),
    )


def _insert_story(conn: psycopg.Connection, story: NewsStory, now: str) -> None:
    conn.execute(
        """
        INSERT INTO daily_news_stories (
            id, company_name, ticker, theme_slug, headline, eeva_summary, is_fallback_summary,
            translation_unavailable, original_title, status, version, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)
        """,
        (
            story.id, story.company_name, story.ticker, story.theme_slug, story.headline,
            story.eeva_summary, int(story.is_fallback_summary), int(story.translation_unavailable),
            story.original_title, story.status.value, now, now,
        ),
    )
    for source in story.sources:
        _insert_source(conn, story.id, source)
    for transition in story.state_history:
        conn.execute(
            "INSERT INTO daily_news_state_transitions (story_id, status, at, detail) VALUES (%s, %s, %s, %s)",
            (story.id, transition.status.value, transition.at, transition.detail),
        )


def upsert_new_stories(conn: psycopg.Connection, new_stories: list[NewsStory]) -> dict[str, NewsStory]:
    """Same idempotent contract as state_db/daily_news_repository.py's
    own upsert_new_stories — see that module's docstring."""
    now = datetime.now(timezone.utc).isoformat()
    with transaction(conn):
        for story in new_stories:
            exists = conn.execute("SELECT 1 FROM daily_news_stories WHERE id = %s", (story.id,)).fetchone()
            if exists is not None:
                continue
            _insert_story(conn, story, now)
    return load_stories(conn)


def update_story(conn: psycopg.Connection, story: NewsStory, expected_version: int) -> UpdateOutcome:
    """Same optimistic-locking/append-only contract as state_db/
    daily_news_repository.py's own update_story — see that module's
    docstring."""
    now = datetime.now(timezone.utc).isoformat()
    with transaction(conn):
        current_row = conn.execute("SELECT version FROM daily_news_stories WHERE id = %s", (story.id,)).fetchone()
        if current_row is None:
            return UpdateOutcome(status="not_found", current=None)

        cursor = conn.execute(
            """
            UPDATE daily_news_stories SET
                company_name = %s, ticker = %s, theme_slug = %s, headline = %s, eeva_summary = %s,
                is_fallback_summary = %s, translation_unavailable = %s, original_title = %s, status = %s,
                version = version + 1, updated_at = %s
            WHERE id = %s AND version = %s
            """,
            (
                story.company_name, story.ticker, story.theme_slug, story.headline, story.eeva_summary,
                int(story.is_fallback_summary), int(story.translation_unavailable), story.original_title,
                story.status.value, now, story.id, expected_version,
            ),
        )
        if cursor.rowcount == 0:
            conflict_current = get_story(conn, story.id)
            return UpdateOutcome(status="conflict", current=conflict_current)

        existing_urls = {
            s["url"] for s in conn.execute("SELECT url FROM daily_news_sources WHERE story_id = %s", (story.id,)).fetchall()
        }
        for source in story.sources:
            if source.url not in existing_urls:
                _insert_source(conn, story.id, source)

        existing_history_keys = {
            (h["status"], h["at"], h["detail"])
            for h in conn.execute(
                "SELECT status, at, detail FROM daily_news_state_transitions WHERE story_id = %s", (story.id,)
            ).fetchall()
        }
        for transition in story.state_history:
            key = (transition.status.value, transition.at, transition.detail)
            if key not in existing_history_keys:
                conn.execute(
                    "INSERT INTO daily_news_state_transitions (story_id, status, at, detail) VALUES (%s, %s, %s, %s)",
                    (story.id, transition.status.value, transition.at, transition.detail),
                )

        return UpdateOutcome(status="updated", current=get_story(conn, story.id))
