"""EevaResearch — Evidence-First Themes MVP (design/DECISIONS.md).
SQLite and Postgres repository tests for the ResearchTheme model
family. Every fixture is synthetic and locally constructed; Postgres
tests use the shared, fail-soft local-only fixtures from
tests/_postgres_test_support.py and skip cleanly when no local
disposable Postgres instance is available."""
from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock

import pytest

from src.data_access import theme_store
from src.data_access.state_db import connection as sqlite_connection
from src.data_access.state_db import schema as sqlite_schema
from src.data_access.state_db import theme_repository as sqlite_themes
from src.models.theme_research import (
    CompanyRole,
    EvidenceDirection,
    ResearchTheme,
    ThemeCategory,
    ThemeCompanyMapEntry,
    ThemeEvidenceItem,
    ThemeStatus,
    ThemeVisibility,
)

from tests._postgres_test_support import pg_conn, pg_isolated_connection  # noqa: F401

try:
    from src.data_access.postgres_state_db import theme_repository as postgres_themes
except ImportError:  # pragma: no cover - psycopg always installed in this repo
    postgres_themes = None


def _theme(title="Test theme", created_at="2026-08-20T00:00:00+00:00", **overrides):
    defaults = dict(
        id=theme_store.build_theme_id(title, created_at),
        category=ThemeCategory.BOTTLENECK, status=ThemeStatus.NEW, visibility=ThemeVisibility.INTERNAL,
        title=title, key_question="q", hypothesis="h", working_thesis="w", why_it_matters="y",
        what_could_change_the_view="c", what_to_watch_next="n", created_at=created_at, updated_at=created_at,
    )
    defaults.update(overrides)
    return ResearchTheme(**defaults)


def _evidence(theme_id="theme-x", source_url="https://example.com/a", date="2026-08-15", **overrides):
    defaults = dict(
        id=theme_store.build_theme_evidence_id(theme_id, source_url, date),
        theme_id=theme_id, date=date, company="Acme Corp", source_name="SEC EDGAR",
        source_url=source_url, fact="f", relevance="r", direction=EvidenceDirection.SUPPORTS,
    )
    defaults.update(overrides)
    return ThemeEvidenceItem(**defaults)


def _company_map_entry(theme_id="theme-x", company_name="Acme Corp", role=CompanyRole.EXPOSED, **overrides):
    defaults = dict(
        id=theme_store.build_theme_company_map_id(theme_id, company_name, role),
        theme_id=theme_id, company_name=company_name, role=role, note=None,
    )
    defaults.update(overrides)
    return ThemeCompanyMapEntry(**defaults)


def _sqlite_conn():
    conn = sqlite_connection.connect_in_memory()
    sqlite_schema.migrate(conn)
    return conn


# ============================================================
# SQLite
# ============================================================


def test_sqlite_theme_round_trip_and_duplicate_rejection():
    conn = _sqlite_conn()
    theme = _theme()
    assert sqlite_themes.insert_theme(conn, theme) is True
    assert sqlite_themes.get_theme(conn, theme.id) == theme
    tampered = dataclasses.replace(theme, title="TAMPERED")
    assert sqlite_themes.insert_theme(conn, tampered) is False
    assert sqlite_themes.get_theme(conn, theme.id) == theme


def test_sqlite_internal_theme_not_returned_by_published_lookup():
    conn = _sqlite_conn()
    theme = _theme(visibility=ThemeVisibility.INTERNAL)
    sqlite_themes.insert_theme(conn, theme)
    assert sqlite_themes.get_published_theme(conn, theme.id) is None


def test_sqlite_set_theme_visibility_transitions():
    conn = _sqlite_conn()
    theme = _theme()
    sqlite_themes.insert_theme(conn, theme)
    updated = sqlite_themes.set_theme_visibility(conn, theme.id, ThemeVisibility.PUBLISHED, "2026-08-25T00:00:00+00:00")
    assert updated.visibility == ThemeVisibility.PUBLISHED
    assert sqlite_themes.get_published_theme(conn, theme.id) == updated


def test_sqlite_set_theme_visibility_missing_theme_is_a_safe_no_op():
    conn = _sqlite_conn()
    assert sqlite_themes.set_theme_visibility(conn, "theme-missing", ThemeVisibility.PUBLISHED, "2026-08-25T00:00:00+00:00") is None


def test_sqlite_list_published_themes_excludes_non_published_and_orders_deterministically():
    conn = _sqlite_conn()
    old = _theme(title="Old", created_at="2026-08-01T00:00:00+00:00", visibility=ThemeVisibility.PUBLISHED, updated_at="2026-08-01T00:00:00+00:00")
    new = _theme(title="New", created_at="2026-08-05T00:00:00+00:00", visibility=ThemeVisibility.PUBLISHED, updated_at="2026-08-05T00:00:00+00:00")
    internal = _theme(title="Internal", created_at="2026-08-10T00:00:00+00:00")
    for theme in (old, new, internal):
        sqlite_themes.insert_theme(conn, theme)
    result = sqlite_themes.list_published_themes(conn)
    assert [t.id for t in result] == [new.id, old.id]


def test_sqlite_evidence_round_trip_and_bulk_read():
    conn = _sqlite_conn()
    theme = _theme()
    sqlite_themes.insert_theme(conn, theme)
    item = _evidence(theme_id=theme.id)
    assert sqlite_themes.insert_theme_evidence_item(conn, item) is True
    result = sqlite_themes.evidence_for_theme_ids(conn, [theme.id])
    assert result[theme.id] == (item,)


def test_sqlite_evidence_bulk_empty_input_executes_no_sql():
    conn = MagicMock()
    result = sqlite_themes.evidence_for_theme_ids(conn, [])
    assert result == {}
    conn.execute.assert_not_called()


def test_sqlite_company_map_round_trip_and_bulk_read():
    conn = _sqlite_conn()
    theme = _theme()
    sqlite_themes.insert_theme(conn, theme)
    entry = _company_map_entry(theme_id=theme.id)
    assert sqlite_themes.insert_theme_company_map_entry(conn, entry) is True
    result = sqlite_themes.company_map_for_theme_ids(conn, [theme.id])
    assert result[theme.id] == (entry,)


def test_sqlite_company_map_bulk_empty_input_executes_no_sql():
    conn = MagicMock()
    result = sqlite_themes.company_map_for_theme_ids(conn, [])
    assert result == {}
    conn.execute.assert_not_called()


def test_sqlite_migration_creates_theme_tables_starting_empty():
    conn = _sqlite_conn()
    assert sqlite_schema.get_schema_version(conn) == 8
    assert sqlite_themes.get_theme(conn, "theme-does-not-exist") is None
    assert sqlite_themes.list_published_themes(conn) == ()


# ============================================================
# Postgres
# ============================================================


def test_postgres_theme_round_trip_and_duplicate_rejection(pg_conn):
    theme = _theme(title="PG theme")
    assert postgres_themes.insert_theme(pg_conn, theme) is True
    assert postgres_themes.get_theme(pg_conn, theme.id) == theme
    tampered = dataclasses.replace(theme, title="TAMPERED")
    assert postgres_themes.insert_theme(pg_conn, tampered) is False
    assert postgres_themes.get_theme(pg_conn, theme.id) == theme
    # Connection remains usable after the rejected duplicate.
    other = _theme(title="Other PG theme")
    assert postgres_themes.insert_theme(pg_conn, other) is True


def test_postgres_internal_theme_not_returned_by_published_lookup(pg_conn):
    theme = _theme(title="PG internal")
    postgres_themes.insert_theme(pg_conn, theme)
    assert postgres_themes.get_published_theme(pg_conn, theme.id) is None


def test_postgres_set_theme_visibility_transitions(pg_conn):
    theme = _theme(title="PG publish")
    postgres_themes.insert_theme(pg_conn, theme)
    updated = postgres_themes.set_theme_visibility(pg_conn, theme.id, ThemeVisibility.PUBLISHED, "2026-08-25T00:00:00+00:00")
    assert updated.visibility == ThemeVisibility.PUBLISHED
    assert postgres_themes.get_published_theme(pg_conn, theme.id) == updated


def test_postgres_evidence_round_trip_and_bulk_read(pg_conn):
    theme = _theme(title="PG evidence")
    postgres_themes.insert_theme(pg_conn, theme)
    item = _evidence(theme_id=theme.id)
    assert postgres_themes.insert_theme_evidence_item(pg_conn, item) is True
    result = postgres_themes.evidence_for_theme_ids(pg_conn, [theme.id])
    assert result[theme.id] == (item,)


def test_postgres_company_map_round_trip_and_bulk_read(pg_conn):
    theme = _theme(title="PG company map")
    postgres_themes.insert_theme(pg_conn, theme)
    entry = _company_map_entry(theme_id=theme.id)
    assert postgres_themes.insert_theme_company_map_entry(pg_conn, entry) is True
    result = postgres_themes.company_map_for_theme_ids(pg_conn, [theme.id])
    assert result[theme.id] == (entry,)


def test_postgres_bulk_empty_input_executes_no_query():
    if postgres_themes is None:
        pytest.skip("psycopg not available")
    conn = MagicMock()
    assert postgres_themes.evidence_for_theme_ids(conn, []) == {}
    assert postgres_themes.company_map_for_theme_ids(conn, []) == {}
    conn.execute.assert_not_called()


def test_postgres_bulk_query_construction_uses_any():
    if postgres_themes is None:
        pytest.skip("psycopg not available")
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = []
    postgres_themes.evidence_for_theme_ids(conn, ["theme-a", "theme-b"])
    assert conn.execute.call_count == 1
    sql_text, params = conn.execute.call_args[0]
    assert "= ANY(%s)" in sql_text
    assert params == (["theme-a", "theme-b"],)


def test_postgres_migration_creates_theme_tables(pg_isolated_connection):
    from src.data_access.postgres_state_db import schema as postgres_schema

    conn = pg_isolated_connection
    version = postgres_schema.migrate(conn)
    assert version == 8
    assert postgres_themes.get_theme(conn, "theme-does-not-exist") is None
    assert postgres_themes.list_published_themes(conn) == ()
