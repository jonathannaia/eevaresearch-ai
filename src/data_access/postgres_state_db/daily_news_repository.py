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

import json
from dataclasses import dataclass
from datetime import datetime, timezone

import psycopg

from src.data_access.postgres_state_db.connection import transaction
from src.models.daily_news_models import (
    NewsMaterialityTier,
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


_SOURCE_COLUMNS = (
    "publisher, source_class, url, title, published_at, retrieved_at, original_language, "
    "excerpt_original, image_url, image_alt, first_discovered_at"
)
_TRANSITION_COLUMNS = "status, at, detail"


def _row_to_story(conn: psycopg.Connection, row) -> NewsStory:
    """One story, two extra queries — unchanged, and still what
    get_story() uses. load_stories() below does NOT call this: it reads
    the same three tables in three queries instead of 1+3N (see its own
    docstring) and assembles via _build_story()."""
    source_rows = conn.execute(
        f"SELECT {_SOURCE_COLUMNS} FROM daily_news_sources WHERE story_id = %s ORDER BY id ASC",
        (row["id"],),
    ).fetchall()
    history_rows = conn.execute(
        f"SELECT {_TRANSITION_COLUMNS} FROM daily_news_state_transitions WHERE story_id = %s ORDER BY id ASC",
        (row["id"],),
    ).fetchall()
    return _build_story(row, source_rows, history_rows)


def _build_story(row, source_rows, history_rows) -> NewsStory:
    """Pure assembly from rows already fetched — byte-for-byte the
    construction _row_to_story always performed, with the two per-story
    queries lifted out so a batched caller can supply the same rows."""
    sources = tuple(
        NewsSourceReference(
            publisher=s["publisher"], source_class=SourceClass(s["source_class"]), url=s["url"],
            title=s["title"], published_at=s["published_at"], retrieved_at=s["retrieved_at"],
            original_language=s["original_language"], excerpt_original=s["excerpt_original"],
            image_url=s["image_url"], image_alt=s["image_alt"],
            first_discovered_at=s["first_discovered_at"],
        )
        for s in source_rows
    )
    state_history = [
        NewsStateTransition(status=NewsStoryStatus(h["status"]), at=h["at"], detail=h["detail"])
        for h in history_rows
    ]
    materiality_tier_raw = row["materiality_tier"]
    return NewsStory(
        id=row["id"], company_name=row["company_name"], ticker=row["ticker"],
        theme_slug=row["theme_slug"], headline=row["headline"], eeva_summary=row["eeva_summary"],
        is_fallback_summary=bool(row["is_fallback_summary"]),
        translation_unavailable=bool(row["translation_unavailable"]),
        original_title=row["original_title"], sources=sources,
        status=NewsStoryStatus(row["status"]), state_history=state_history,
        # Materiality classification (design/DECISIONS.md) — same
        # additive, safe-default contract as state_db/
        # daily_news_repository.py's own _row_to_story.
        materiality_tier=NewsMaterialityTier(materiality_tier_raw) if materiality_tier_raw else None,
        materiality_reasons=tuple(json.loads(row["materiality_reasons"])) if row["materiality_reasons"] else (),
    )


def get_story(conn: psycopg.Connection, story_id: str) -> NewsStory | None:
    row = conn.execute("SELECT * FROM daily_news_stories WHERE id = %s", (story_id,)).fetchone()
    return _row_to_story(conn, row) if row is not None else None


def get_story_version(conn: psycopg.Connection, story_id: str) -> int | None:
    row = conn.execute("SELECT version FROM daily_news_stories WHERE id = %s", (story_id,)).fetchone()
    return row["version"] if row is not None else None


def load_stories(conn: psycopg.Connection) -> dict[str, NewsStory]:
    """Every persisted story, in four queries regardless of how many
    stories there are.

    This used to issue 1 + 3N queries: one for the ids, then per story a
    row fetch plus a sources fetch plus a state-transitions fetch. The
    rows themselves are small and assembling them costs single-digit
    milliseconds; against a networked database the cost was almost
    entirely per-query round trips, which is why one call measured
    ~2.4 s in production while the same work over a local store took
    ~14 ms. Reading each table once removes the round trips without
    touching the schema, the SQL semantics, or the objects returned.

    Deliberately identical to the previous implementation in three ways
    that callers depend on:

      key order        still the order the id query returns, because the
                       dict is still built by iterating that result.
                       daily_news_pipeline.select_canonical_stories()
                       walks this dict pairwise and its outcome depends
                       on that order, so it is preserved rather than
                       taken from the row query.
      source order     per story, ascending id — one global ORDER BY id
                       grouped in encounter order yields exactly what
                       the per-story ORDER BY id yielded.
      missing rows     an id returned by the first query whose row is
                       absent from the second maps to None, exactly as
                       get_story() returning None did before."""
    id_rows = conn.execute("SELECT id FROM daily_news_stories").fetchall()
    if not id_rows:
        return {}
    rows_by_id = {row["id"]: row for row in conn.execute("SELECT * FROM daily_news_stories").fetchall()}
    sources_by_story: dict[str, list] = {}
    for source_row in conn.execute(
        f"SELECT story_id, {_SOURCE_COLUMNS} FROM daily_news_sources ORDER BY id ASC"
    ).fetchall():
        sources_by_story.setdefault(source_row["story_id"], []).append(source_row)
    history_by_story: dict[str, list] = {}
    for history_row in conn.execute(
        f"SELECT story_id, {_TRANSITION_COLUMNS} FROM daily_news_state_transitions ORDER BY id ASC"
    ).fetchall():
        history_by_story.setdefault(history_row["story_id"], []).append(history_row)
    stories: dict[str, NewsStory] = {}
    for id_row in id_rows:
        story_id = id_row["id"]
        row = rows_by_id.get(story_id)
        stories[story_id] = None if row is None else _build_story(
            row, sources_by_story.get(story_id, ()), history_by_story.get(story_id, ()),
        )
    return stories


def _insert_source(conn: psycopg.Connection, story_id: str, source: NewsSourceReference) -> None:
    conn.execute(
        """
        INSERT INTO daily_news_sources (
            story_id, publisher, source_class, url, title, published_at, retrieved_at,
            original_language, excerpt_original, image_url, image_alt, first_discovered_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            story_id, source.publisher, source.source_class.value, source.url, source.title,
            source.published_at, source.retrieved_at, source.original_language,
            source.excerpt_original, source.image_url, source.image_alt, source.first_discovered_at,
        ),
    )


def _insert_story(conn: psycopg.Connection, story: NewsStory, now: str) -> None:
    conn.execute(
        """
        INSERT INTO daily_news_stories (
            id, company_name, ticker, theme_slug, headline, eeva_summary, is_fallback_summary,
            translation_unavailable, original_title, status, version, created_at, updated_at,
            materiality_tier, materiality_reasons
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s, %s, %s)
        """,
        (
            story.id, story.company_name, story.ticker, story.theme_slug, story.headline,
            story.eeva_summary, int(story.is_fallback_summary), int(story.translation_unavailable),
            story.original_title, story.status.value, now, now,
            story.materiality_tier.value if story.materiality_tier else None,
            json.dumps(list(story.materiality_reasons)) if story.materiality_reasons else None,
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
                materiality_tier = %s, materiality_reasons = %s,
                version = version + 1, updated_at = %s
            WHERE id = %s AND version = %s
            """,
            (
                story.company_name, story.ticker, story.theme_slug, story.headline, story.eeva_summary,
                int(story.is_fallback_summary), int(story.translation_unavailable), story.original_title,
                story.status.value,
                story.materiality_tier.value if story.materiality_tier else None,
                json.dumps(list(story.materiality_reasons)) if story.materiality_reasons else None,
                now, story.id, expected_version,
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
