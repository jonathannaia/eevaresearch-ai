"""SQLite-backed NewsStory storage — the durable counterpart to
src/data_access/daily_news/daily_news_store.py's own JSON store. Narrow
and independent of Radar's own candidate_repository.py — no shared
table, no shared code — mirrors only its general read-modify-write
shape (one child table per nested collection, optimistic-concurrency
version column, append-only history).

Deliberately never imports src.models.models (see daily_news_models.py's
own module docstring on why Daily News stays decoupled from Radar's
domain types at the type level, not just the storage level) — only
src.models.daily_news_models.

Functions here never catch sqlite3 errors — a database failure
propagates as a real exception rather than becoming an empty result,
the same discipline src/data_access/state_db/candidate_repository.py
already established.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from src.data_access.state_db.connection import transaction
from src.models.daily_news_models import (
    NewsSourceReference,
    NewsStateTransition,
    NewsStory,
    NewsStoryStatus,
    SourceClass,
)


@dataclass(frozen=True)
class UpdateOutcome:
    """Result of an optimistic-locking update attempt — always returned,
    never raised, matching candidate_repository.UpdateOutcome's own
    shape exactly, just NewsStory-typed instead of CandidateSignal-typed
    (see this module's own docstring on why the two stay separate types).

    status is one of:
      "updated"   — the write succeeded; `current` is the new record.
      "conflict"  — `expected_version` didn't match the stored version;
                    the newer stored record is returned UNCHANGED in
                    `current` — the caller's update was never applied.
      "not_found" — no story with this id exists; `current` is None.
    """

    status: str
    current: NewsStory | None


def _row_to_story(conn: sqlite3.Connection, row: sqlite3.Row) -> NewsStory:
    source_rows = conn.execute(
        "SELECT publisher, source_class, url, title, published_at, retrieved_at, original_language, "
        "excerpt_original, image_url, image_alt FROM daily_news_sources WHERE story_id = ? ORDER BY id ASC",
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
        "SELECT status, at, detail FROM daily_news_state_transitions WHERE story_id = ? ORDER BY id ASC",
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


def get_story(conn: sqlite3.Connection, story_id: str) -> NewsStory | None:
    row = conn.execute("SELECT * FROM daily_news_stories WHERE id = ?", (story_id,)).fetchone()
    return _row_to_story(conn, row) if row is not None else None


def get_story_version(conn: sqlite3.Connection, story_id: str) -> int | None:
    row = conn.execute("SELECT version FROM daily_news_stories WHERE id = ?", (story_id,)).fetchone()
    return row["version"] if row is not None else None


def load_stories(conn: sqlite3.Connection) -> dict[str, NewsStory]:
    rows = conn.execute("SELECT id FROM daily_news_stories").fetchall()
    return {row["id"]: get_story(conn, row["id"]) for row in rows}


def _insert_source(conn: sqlite3.Connection, story_id: str, source: NewsSourceReference) -> None:
    conn.execute(
        """
        INSERT INTO daily_news_sources (
            story_id, publisher, source_class, url, title, published_at, retrieved_at,
            original_language, excerpt_original, image_url, image_alt
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            story_id, source.publisher, source.source_class.value, source.url, source.title,
            source.published_at, source.retrieved_at, source.original_language,
            source.excerpt_original, source.image_url, source.image_alt,
        ),
    )


def _insert_story(conn: sqlite3.Connection, story: NewsStory, now: str) -> None:
    conn.execute(
        """
        INSERT INTO daily_news_stories (
            id, company_name, ticker, theme_slug, headline, eeva_summary, is_fallback_summary,
            translation_unavailable, original_title, status, version, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
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
            "INSERT INTO daily_news_state_transitions (story_id, status, at, detail) VALUES (?, ?, ?, ?)",
            (story.id, transition.status.value, transition.at, transition.detail),
        )


def upsert_new_stories(conn: sqlite3.Connection, new_stories: list[NewsStory]) -> dict[str, NewsStory]:
    """Adds any story id not already present (checked, then inserted,
    inside one transaction covering the whole batch); leaves an existing
    entry untouched — same idempotent contract as daily_news_store.py's
    own upsert_new_stories and candidate_repository's own
    upsert_new_candidates. A story's id is already deterministic per
    (company, canonical URL) — see daily_news_pipeline._story_id() —
    so re-discovering the same item on a later run is a no-op here, not
    a duplicate; `daily_news_sources.url`'s own UNIQUE index is a second,
    DB-enforced guarantee against the same canonical link ever being
    stored under two different story rows."""
    now = datetime.now(timezone.utc).isoformat()
    with transaction(conn):
        for story in new_stories:
            exists = conn.execute("SELECT 1 FROM daily_news_stories WHERE id = ?", (story.id,)).fetchone()
            if exists is not None:
                continue
            _insert_story(conn, story, now)
    return load_stories(conn)


def update_story(conn: sqlite3.Connection, story: NewsStory, expected_version: int) -> UpdateOutcome:
    """Optimistic-locking update of one story's fields. Succeeds only if
    `expected_version` matches the version currently stored. Appends any
    NewsSourceReference not already present (matched by its own `url` —
    a source's natural identity) and any NewsStateTransition not already
    present (matched by (status, at, detail), identical to
    candidate_repository.update_candidate's own convention) as new rows
    — existing source/history rows are never rewritten or removed."""
    now = datetime.now(timezone.utc).isoformat()
    with transaction(conn):
        current_row = conn.execute("SELECT version FROM daily_news_stories WHERE id = ?", (story.id,)).fetchone()
        if current_row is None:
            return UpdateOutcome(status="not_found", current=None)

        cursor = conn.execute(
            """
            UPDATE daily_news_stories SET
                company_name = ?, ticker = ?, theme_slug = ?, headline = ?, eeva_summary = ?,
                is_fallback_summary = ?, translation_unavailable = ?, original_title = ?, status = ?,
                version = version + 1, updated_at = ?
            WHERE id = ? AND version = ?
            """,
            (
                story.company_name, story.ticker, story.theme_slug, story.headline, story.eeva_summary,
                int(story.is_fallback_summary), int(story.translation_unavailable), story.original_title,
                story.status.value, now, story.id, expected_version,
            ),
        )
        if cursor.rowcount == 0:
            # id existed (checked above) but the version didn't match —
            # a genuine conflict, not a missing row.
            conflict_current = get_story(conn, story.id)
            return UpdateOutcome(status="conflict", current=conflict_current)

        existing_urls = {
            s["url"] for s in conn.execute("SELECT url FROM daily_news_sources WHERE story_id = ?", (story.id,)).fetchall()
        }
        for source in story.sources:
            if source.url not in existing_urls:
                _insert_source(conn, story.id, source)

        existing_history_keys = {
            (h["status"], h["at"], h["detail"])
            for h in conn.execute(
                "SELECT status, at, detail FROM daily_news_state_transitions WHERE story_id = ?", (story.id,)
            ).fetchall()
        }
        for transition in story.state_history:
            key = (transition.status.value, transition.at, transition.detail)
            if key not in existing_history_keys:
                conn.execute(
                    "INSERT INTO daily_news_state_transitions (story_id, status, at, detail) VALUES (?, ?, ?, ?)",
                    (story.id, transition.status.value, transition.at, transition.detail),
                )

        return UpdateOutcome(status="updated", current=get_story(conn, story.id))
