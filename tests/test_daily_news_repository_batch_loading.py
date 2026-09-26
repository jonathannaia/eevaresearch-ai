"""load_stories() reads each table once instead of once per story.

Offline only: a temporary SQLite file and, for the Postgres mirror, a
recording fake connection. No server, no network, no credentials, no
production data.

Two things are proven here, and they are different claims:

  1. the query count is CONSTANT in the number of stories (it was
     1 + 3N), which is the entire point of the change; and
  2. the stories returned are IDENTICAL to what the previous 1+3N
     implementation returned — same keys, same key order, same source
     order, same state-history order, same field values.

Claim 2 is checked against a local re-implementation of the old loader
(_legacy_load_stories below), built only from get_story(), which the
change deliberately leaves alone. If that reference ever drifts from
what the repository actually did, the parity tests stop meaning
anything — so it is kept to a literal transcription of the two lines it
replaces.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.data_access.postgres_state_db import daily_news_repository as pg_repo
from src.data_access.state_db import connection as state_connection
from src.data_access.state_db import daily_news_repository as repo
from src.data_access.state_db import schema as state_schema
from src.models.daily_news_models import (
    NewsMaterialityTier,
    NewsSourceReference,
    NewsStateTransition,
    NewsStory,
    NewsStoryStatus,
    SourceClass,
)


def _legacy_load_stories(conn) -> dict:
    """Exactly what load_stories() was before this change."""
    rows = conn.execute("SELECT id FROM daily_news_stories").fetchall()
    return {row["id"]: repo.get_story(conn, row["id"]) for row in rows}


def _source(url: str, published_at: str, language: str = "English") -> NewsSourceReference:
    return NewsSourceReference(
        publisher="Fictional Publisher", source_class=SourceClass.OFFICIAL_COMPANY, url=url,
        title="Source title", published_at=published_at, retrieved_at=published_at,
        original_language=language, excerpt_original="Excerpt text.",
        image_url=None, image_alt=None, first_discovered_at=published_at,
    )


def _story(
    story_id: str, company: str = "Fictional Co", *, sources=None, history=None,
    tier: NewsMaterialityTier | None = None, reasons: tuple = (),
    status: NewsStoryStatus = NewsStoryStatus.PUBLISHED, headline: str = "A headline",
) -> NewsStory:
    at = "2026-09-20T12:00:00+00:00"
    return NewsStory(
        id=story_id, company_name=company, ticker="TCK", theme_slug="ai-buildout",
        headline=headline, eeva_summary="Summary text.", is_fallback_summary=False,
        translation_unavailable=False, original_title=None,
        sources=tuple(sources) if sources is not None else (_source(f"https://example.test/{story_id}", at),),
        status=status,
        state_history=list(history) if history is not None else [
            NewsStateTransition(status=NewsStoryStatus.DISCOVERED, at=at),
            NewsStateTransition(status=status, at=at),
        ],
        materiality_tier=tier, materiality_reasons=reasons,
    )


@pytest.fixture
def conn(tmp_path):
    connection = state_connection.connect(tmp_path / "state.db")
    state_schema.migrate(connection)
    return connection


class _QueryCounter:
    """Counts statements executed on a real sqlite3 connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection
        self.statements: list[str] = []

    def __enter__(self) -> "_QueryCounter":
        self._conn.set_trace_callback(self.statements.append)
        return self

    def __exit__(self, *exc) -> None:
        self._conn.set_trace_callback(None)

    @property
    def count(self) -> int:
        return len(self.statements)


# --- 1. the query count no longer grows with the number of stories -------

@pytest.mark.parametrize("story_count", [1, 5, 20, 75])
def test_load_stories_issues_a_constant_number_of_queries(conn, story_count):
    repo.upsert_new_stories(conn, [_story(f"newsitem-{i:04d}") for i in range(story_count)])

    with _QueryCounter(conn) as counter:
        stories = repo.load_stories(conn)

    assert len(stories) == story_count
    assert counter.count == 4, counter.statements


def test_the_query_count_is_identical_for_one_story_and_for_many(conn):
    repo.upsert_new_stories(conn, [_story("newsitem-0000")])
    with _QueryCounter(conn) as one:
        repo.load_stories(conn)

    repo.upsert_new_stories(conn, [_story(f"newsitem-{i:04d}") for i in range(1, 60)])
    with _QueryCounter(conn) as many:
        repo.load_stories(conn)

    assert one.count == many.count == 4


def test_the_previous_implementation_really_did_grow_with_story_count(conn):
    """Non-vacuity guard: without it, the assertions above would still
    pass if load_stories() had always been constant-query."""
    repo.upsert_new_stories(conn, [_story(f"newsitem-{i:04d}") for i in range(20)])

    with _QueryCounter(conn) as legacy:
        _legacy_load_stories(conn)
    with _QueryCounter(conn) as batched:
        repo.load_stories(conn)

    assert legacy.count == 1 + 3 * 20 == 61
    assert batched.count == 4
    assert batched.count < legacy.count


def test_each_table_is_read_exactly_once(conn):
    repo.upsert_new_stories(conn, [_story(f"newsitem-{i:04d}") for i in range(10)])

    with _QueryCounter(conn) as counter:
        repo.load_stories(conn)

    joined = " ".join(counter.statements)
    assert joined.count("FROM daily_news_sources") == 1
    assert joined.count("FROM daily_news_state_transitions") == 1
    assert joined.count("FROM daily_news_stories") == 2  # the id query, then the row query


# --- 2. the result is identical to the previous implementation's ---------

def _assert_identical(batched: dict, legacy: dict) -> None:
    assert list(batched.keys()) == list(legacy.keys())  # key ORDER, not just membership
    assert batched == legacy
    for story_id, story in batched.items():
        other = legacy[story_id]
        assert [s.url for s in story.sources] == [s.url for s in other.sources]
        assert [(t.status, t.at, t.detail) for t in story.state_history] \
            == [(t.status, t.at, t.detail) for t in other.state_history]


def test_batched_and_legacy_loaders_return_identical_stories(conn):
    repo.upsert_new_stories(conn, [
        _story("newsitem-0001"),
        _story("newsitem-0002", company="Another Co", tier=NewsMaterialityTier.HIGH_SIGNAL,
               reasons=("definitive_transaction", "amount_above_floor")),
        _story("newsitem-0003", status=NewsStoryStatus.DISCOVERED),
    ])

    _assert_identical(repo.load_stories(conn), _legacy_load_stories(conn))


def test_multi_source_story_keeps_its_source_order(conn):
    at = "2026-09-20T12:00:00+00:00"
    repo.upsert_new_stories(conn, [
        _story("newsitem-a", sources=[
            _source("https://example.test/a-first", at),
            _source("https://example.test/a-second", at, language="Japanese"),
            _source("https://example.test/a-third", at),
        ]),
        _story("newsitem-b", sources=[_source("https://example.test/b-only", at)]),
    ])

    batched = repo.load_stories(conn)

    assert [s.url for s in batched["newsitem-a"].sources] == [
        "https://example.test/a-first", "https://example.test/a-second", "https://example.test/a-third",
    ]
    _assert_identical(batched, _legacy_load_stories(conn))


def test_interleaved_sources_across_stories_are_grouped_correctly(conn):
    """The batched query reads every source row in one pass, so rows
    belonging to different stories arrive interleaved by insert order —
    exactly the case a per-story WHERE clause never had to handle."""
    at = "2026-09-20T12:00:00+00:00"
    repo.upsert_new_stories(conn, [
        _story("newsitem-x", sources=[_source("https://example.test/x1", at), _source("https://example.test/x2", at)]),
        _story("newsitem-y", sources=[_source("https://example.test/y1", at)]),
        _story("newsitem-z", sources=[_source("https://example.test/z1", at), _source("https://example.test/z2", at)]),
    ])

    batched = repo.load_stories(conn)

    assert [s.url for s in batched["newsitem-x"].sources] == ["https://example.test/x1", "https://example.test/x2"]
    assert [s.url for s in batched["newsitem-y"].sources] == ["https://example.test/y1"]
    assert [s.url for s in batched["newsitem-z"].sources] == ["https://example.test/z1", "https://example.test/z2"]
    _assert_identical(batched, _legacy_load_stories(conn))


def test_long_state_history_keeps_its_transition_order(conn):
    at = "2026-09-20T12:00:00+00:00"
    history = [
        NewsStateTransition(status=NewsStoryStatus.DISCOVERED, at=at, detail="found"),
        NewsStateTransition(status=NewsStoryStatus.SUMMARIZED, at=at, detail=""),
        NewsStateTransition(status=NewsStoryStatus.PUBLISHED, at=at, detail="published"),
    ]
    repo.upsert_new_stories(conn, [_story("newsitem-h", history=history), _story("newsitem-other")])

    batched = repo.load_stories(conn)

    assert [(t.status, t.detail) for t in batched["newsitem-h"].state_history] == [
        (NewsStoryStatus.DISCOVERED, "found"),
        (NewsStoryStatus.SUMMARIZED, ""),
        (NewsStoryStatus.PUBLISHED, "published"),
    ]
    _assert_identical(batched, _legacy_load_stories(conn))


def test_non_ascii_content_survives_the_batched_read(conn):
    at = "2026-09-20T12:00:00+00:00"
    repo.upsert_new_stories(conn, [
        _story("newsitem-jp", company="フィクション株式会社", headline="日本語の見出し",
               sources=[_source("https://example.test/jp", at, language="Japanese")]),
    ])

    batched = repo.load_stories(conn)

    assert batched["newsitem-jp"].company_name == "フィクション株式会社"
    assert batched["newsitem-jp"].headline == "日本語の見出し"
    _assert_identical(batched, _legacy_load_stories(conn))


def test_an_empty_store_returns_an_empty_mapping(conn):
    with _QueryCounter(conn) as counter:
        assert repo.load_stories(conn) == {}
    assert counter.count == 1  # the id query alone; nothing else is read


def test_key_order_matches_the_id_query_order(conn):
    """select_canonical_stories() walks this mapping pairwise and its
    outcome depends on the order, so key order is load-bearing."""
    repo.upsert_new_stories(conn, [_story(f"newsitem-{i:04d}") for i in range(12)])

    expected = [row["id"] for row in conn.execute("SELECT id FROM daily_news_stories").fetchall()]

    assert list(repo.load_stories(conn).keys()) == expected
    assert list(repo.load_stories(conn).keys()) == list(_legacy_load_stories(conn).keys())


# --- 3. the untouched single-story API still behaves as before -----------

def test_get_story_is_unchanged_and_still_reads_one_story(conn):
    repo.upsert_new_stories(conn, [_story("newsitem-0001"), _story("newsitem-0002")])

    with _QueryCounter(conn) as counter:
        story = repo.get_story(conn, "newsitem-0001")

    assert story is not None and story.id == "newsitem-0001"
    assert counter.count == 3  # row + sources + transitions, exactly as before
    assert story == repo.load_stories(conn)["newsitem-0001"]


def test_get_story_still_returns_none_for_an_unknown_id(conn):
    repo.upsert_new_stories(conn, [_story("newsitem-0001")])

    assert repo.get_story(conn, "newsitem-does-not-exist") is None


def test_upsert_new_stories_still_returns_the_full_store(conn):
    repo.upsert_new_stories(conn, [_story("newsitem-0001")])
    result = repo.upsert_new_stories(conn, [_story("newsitem-0002")])

    assert set(result) == {"newsitem-0001", "newsitem-0002"}
    assert result == repo.load_stories(conn)


# --- 4. the Postgres mirror issues the same four statements --------------

class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _RecordingConnection:
    """Records SQL and answers from fixed row lists. Not a database —
    it exists only to pin the Postgres mirror's query SHAPE, since no
    Postgres server is available offline."""

    def __init__(self, story_rows, source_rows, transition_rows):
        self.statements: list[str] = []
        self._story_rows = story_rows
        self._source_rows = source_rows
        self._transition_rows = transition_rows

    def execute(self, sql, params=None):
        self.statements.append(" ".join(sql.split()))
        if "FROM daily_news_sources" in sql:
            return _FakeCursor(self._source_rows)
        if "FROM daily_news_state_transitions" in sql:
            return _FakeCursor(self._transition_rows)
        if sql.strip().startswith("SELECT id FROM daily_news_stories"):
            return _FakeCursor([{"id": r["id"]} for r in self._story_rows])
        return _FakeCursor(self._story_rows)


def _pg_story_row(story_id: str) -> dict:
    return {
        "id": story_id, "company_name": "Fictional Co", "ticker": "TCK", "theme_slug": "ai-buildout",
        "headline": "A headline", "eeva_summary": "Summary text.", "is_fallback_summary": 0,
        "translation_unavailable": 0, "original_title": None, "status": NewsStoryStatus.PUBLISHED.value,
        "materiality_tier": None, "materiality_reasons": None,
    }


def _pg_source_row(story_id: str, url: str) -> dict:
    at = "2026-09-20T12:00:00+00:00"
    return {
        "story_id": story_id, "publisher": "Fictional Publisher", "source_class": SourceClass.OFFICIAL_COMPANY.value,
        "url": url, "title": "Source title", "published_at": at, "retrieved_at": at,
        "original_language": "English", "excerpt_original": "Excerpt text.",
        "image_url": None, "image_alt": None, "first_discovered_at": at,
    }


def _pg_transition_row(story_id: str, status: str) -> dict:
    return {"story_id": story_id, "status": NewsStoryStatus(status).value, "at": "2026-09-20T12:00:00+00:00", "detail": None}


def test_postgres_load_stories_issues_four_statements_for_many_stories():
    ids = [f"newsitem-{i:04d}" for i in range(25)]
    conn = _RecordingConnection(
        [_pg_story_row(i) for i in ids],
        [_pg_source_row(i, f"https://example.test/{i}") for i in ids],
        [_pg_transition_row(i, "Published") for i in ids],
    )

    stories = pg_repo.load_stories(conn)

    assert len(conn.statements) == 4
    assert len(stories) == 25
    assert all(len(s.sources) == 1 for s in stories.values())
    assert [s.id for s in stories.values()] == ids


def test_postgres_load_stories_groups_interleaved_source_rows_by_story():
    conn = _RecordingConnection(
        [_pg_story_row("newsitem-x"), _pg_story_row("newsitem-y")],
        [
            _pg_source_row("newsitem-x", "https://example.test/x1"),
            _pg_source_row("newsitem-y", "https://example.test/y1"),
            _pg_source_row("newsitem-x", "https://example.test/x2"),
        ],
        [_pg_transition_row("newsitem-x", "Published"), _pg_transition_row("newsitem-y", "Published")],
    )

    stories = pg_repo.load_stories(conn)

    assert [s.url for s in stories["newsitem-x"].sources] == [
        "https://example.test/x1", "https://example.test/x2",
    ]
    assert [s.url for s in stories["newsitem-y"].sources] == ["https://example.test/y1"]


def test_postgres_load_stories_orders_both_child_reads_by_id():
    conn = _RecordingConnection([_pg_story_row("newsitem-x")], [], [])

    pg_repo.load_stories(conn)

    child_reads = [s for s in conn.statements if "daily_news_sources" in s or "daily_news_state_transitions" in s]
    assert len(child_reads) == 2
    assert all(s.endswith("ORDER BY id ASC") for s in child_reads), child_reads


def test_postgres_load_stories_short_circuits_on_an_empty_store():
    conn = _RecordingConnection([], [], [])

    assert pg_repo.load_stories(conn) == {}
    assert len(conn.statements) == 1


def test_postgres_story_without_sources_or_history_is_still_built():
    conn = _RecordingConnection([_pg_story_row("newsitem-bare")], [], [])

    stories = pg_repo.load_stories(conn)

    assert stories["newsitem-bare"].sources == ()
    assert stories["newsitem-bare"].state_history == []
