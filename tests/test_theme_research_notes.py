"""EevaResearch — Citrini-style Theme research workspace vertical slice
(design/DECISIONS.md). Tests for the ThemeResearchNote persistence
family: JSON (theme_store.py), SQLite/Postgres (theme_repository.py x2),
and the backend_factory curator seam. Every fixture is synthetic and
locally constructed; no real network, worker, scan, or LLM call
anywhere in this file."""
from __future__ import annotations

import dataclasses

import pytest

from src.config.settings import Settings
from src.data_access import backend_factory, theme_store
from src.data_access.state_db import connection as sqlite_connection
from src.data_access.state_db import schema as sqlite_schema
from src.data_access.state_db import theme_repository as sqlite_themes
from src.models.theme_research import (
    HypothesisConfidence,
    ResearchTheme,
    ThemeCategory,
    ThemeNoteType,
    ThemeResearchNote,
    ThemeStatus,
    ThemeVisibility,
)

from tests._postgres_test_support import pg_conn, pg_isolated_connection  # noqa: F401

try:
    from src.data_access.postgres_state_db import theme_repository as postgres_themes
except ImportError:  # pragma: no cover - psycopg always installed in this repo
    postgres_themes = None


def _theme(theme_id="theme-x", **overrides):
    defaults = dict(
        id=theme_id, category=ThemeCategory.BOTTLENECK, status=ThemeStatus.NEW, visibility=ThemeVisibility.INTERNAL,
        title="T", key_question="q", hypothesis="h", working_thesis="w", why_it_matters="y",
        what_could_change_the_view="c", what_to_watch_next="n",
        created_at="2026-09-01T00:00:00+00:00", updated_at="2026-09-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    return ResearchTheme(**defaults)


def _hypothesis_note(theme_id="theme-x", content="H1", created_at="2026-09-01T00:00:00+00:00", **overrides):
    defaults = dict(
        id=theme_store.build_theme_research_note_id(theme_id, ThemeNoteType.HYPOTHESIS, content, created_at),
        theme_id=theme_id, note_type=ThemeNoteType.HYPOTHESIS, content=content,
        confidence=HypothesisConfidence.MEDIUM, disconfirming_condition="If X, reject.", created_at=created_at,
    )
    defaults.update(overrides)
    return ThemeResearchNote(**defaults)


def _decision_note(theme_id="theme-x", content="D1", created_at="2026-09-01T00:00:00+00:00", **overrides):
    defaults = dict(
        id=theme_store.build_theme_research_note_id(theme_id, ThemeNoteType.DECISION, content, created_at),
        theme_id=theme_id, note_type=ThemeNoteType.DECISION, content=content,
        confidence=None, disconfirming_condition=None, created_at=created_at,
    )
    defaults.update(overrides)
    return ThemeResearchNote(**defaults)


# ============================================================
# JSON backend
# ============================================================


def test_json_round_trip_and_duplicate_rejection(tmp_path):
    note = _hypothesis_note()
    assert theme_store.append_theme_research_note(tmp_path, note) is True
    tampered = dataclasses.replace(note, content="TAMPERED")
    assert theme_store.append_theme_research_note(tmp_path, tampered) is False
    assert theme_store.load_theme_research_notes(tmp_path)[note.id] == note


def test_json_bulk_empty_input_never_loads(tmp_path, monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("must not load when theme_ids is empty")

    monkeypatch.setattr(theme_store, "load_theme_research_notes", _boom)
    assert theme_store.research_notes_for_theme_ids(tmp_path, []) == {}


def test_json_bulk_read_ordered_chronologically(tmp_path):
    older = _hypothesis_note(content="older", created_at="2026-08-01T00:00:00+00:00")
    newer = _decision_note(content="newer", created_at="2026-08-05T00:00:00+00:00")
    theme_store.append_theme_research_note(tmp_path, newer)
    theme_store.append_theme_research_note(tmp_path, older)

    result = theme_store.research_notes_for_theme_ids(tmp_path, ["theme-x"])
    assert [n.id for n in result["theme-x"]] == [older.id, newer.id]


def test_json_decision_and_watch_item_have_no_confidence():
    note = _decision_note()
    assert note.confidence is None
    assert note.disconfirming_condition is None


def test_json_missing_file_loads_empty(tmp_path):
    assert theme_store.load_theme_research_notes(tmp_path) == {}


# ============================================================
# SQLite
# ============================================================


def _sqlite_conn():
    conn = sqlite_connection.connect_in_memory()
    sqlite_schema.migrate(conn)
    return conn


def test_sqlite_round_trip_and_duplicate_rejection():
    conn = _sqlite_conn()
    sqlite_themes.insert_theme(conn, _theme())
    note = _hypothesis_note()
    assert sqlite_themes.insert_theme_research_note(conn, note) is True
    tampered = dataclasses.replace(note, content="TAMPERED")
    assert sqlite_themes.insert_theme_research_note(conn, tampered) is False


def test_sqlite_insert_without_parent_theme_fails_via_fk():
    conn = _sqlite_conn()
    note = _hypothesis_note(theme_id="theme-orphan")
    assert sqlite_themes.insert_theme_research_note(conn, note) is False


def test_sqlite_bulk_read_and_confidence_round_trip():
    conn = _sqlite_conn()
    sqlite_themes.insert_theme(conn, _theme())
    note = _hypothesis_note()
    sqlite_themes.insert_theme_research_note(conn, note)
    result = sqlite_themes.research_notes_for_theme_ids(conn, ["theme-x"])
    assert result["theme-x"] == (note,)
    assert result["theme-x"][0].confidence is HypothesisConfidence.MEDIUM


def test_sqlite_bulk_empty_input_executes_no_sql():
    from unittest.mock import MagicMock
    conn = MagicMock()
    result = sqlite_themes.research_notes_for_theme_ids(conn, [])
    assert result == {}
    conn.execute.assert_not_called()


def test_sqlite_migration_reaches_version_9_with_new_table():
    conn = _sqlite_conn()
    assert sqlite_schema.get_schema_version(conn) == sqlite_schema.CURRENT_SCHEMA_VERSION
    assert sqlite_themes.research_notes_for_theme_ids(conn, ["theme-does-not-exist"]) == {}


# ============================================================
# Postgres
# ============================================================


def test_postgres_round_trip_and_duplicate_rejection(pg_conn):
    postgres_themes.insert_theme(pg_conn, _theme(theme_id="pg-theme-notes"))
    note = _hypothesis_note(theme_id="pg-theme-notes")
    assert postgres_themes.insert_theme_research_note(pg_conn, note) is True
    tampered = dataclasses.replace(note, content="TAMPERED")
    assert postgres_themes.insert_theme_research_note(pg_conn, tampered) is False
    other = _decision_note(theme_id="pg-theme-notes", content="D-other")
    assert postgres_themes.insert_theme_research_note(pg_conn, other) is True


def test_postgres_bulk_read(pg_conn):
    postgres_themes.insert_theme(pg_conn, _theme(theme_id="pg-theme-bulk"))
    note = _hypothesis_note(theme_id="pg-theme-bulk")
    postgres_themes.insert_theme_research_note(pg_conn, note)
    result = postgres_themes.research_notes_for_theme_ids(pg_conn, ["pg-theme-bulk"])
    assert result["pg-theme-bulk"] == (note,)


def test_postgres_bulk_empty_input_executes_no_query():
    if postgres_themes is None:
        pytest.skip("psycopg not available")
    from unittest.mock import MagicMock
    conn = MagicMock()
    assert postgres_themes.research_notes_for_theme_ids(conn, []) == {}
    conn.execute.assert_not_called()


def test_postgres_migration_reaches_version_9(pg_isolated_connection):
    from src.data_access.postgres_state_db import schema as postgres_schema

    conn = pg_isolated_connection
    version = postgres_schema.migrate(conn)
    assert version == postgres_schema.CURRENT_SCHEMA_VERSION


# ============================================================
# backend_factory curator seam
# ============================================================


def test_factory_json_backend_insert_and_read(tmp_path):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    curator = backend_factory.get_theme_curator_repository(settings)
    curator.insert_theme(_theme())
    note = _hypothesis_note()
    assert curator.insert_research_note(note) is True
    assert curator.research_notes_for_theme("theme-x") == (note,)


def test_factory_sqlite_backend_insert_and_read(tmp_path):
    settings = Settings(db_backend="sqlite", state_db_path=tmp_path / "state.db")
    curator = backend_factory.get_theme_curator_repository(settings)
    curator.insert_theme(_theme())
    note = _hypothesis_note()
    assert curator.insert_research_note(note) is True
    assert curator.research_notes_for_theme("theme-x") == (note,)


def test_public_theme_protocol_has_no_research_notes_method():
    """Deliberate: unlike evidence/company-map, research notes are
    never exposed through the public/UI-facing protocol at all — not
    even an always-empty method."""
    exported = {name for name in dir(backend_factory.ThemeRepositoryProtocol) if not name.startswith("_")}
    assert "research_notes_for_theme" not in exported


def test_public_adapters_have_no_research_notes_method():
    for cls in (backend_factory.JsonThemeRepository, backend_factory.SqliteThemeRepository, backend_factory.PostgresThemeRepository):
        exported = {name for name in dir(cls) if not name.startswith("_")}
        assert "research_notes_for_theme" not in exported
