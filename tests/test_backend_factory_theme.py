"""EevaResearch — Evidence-First Themes MVP (design/DECISIONS.md).
Focused tests for backend_factory.py's two new Theme seams:
ThemeRepositoryProtocol (public, published-only reads,
get_theme_repository) and ThemeCuratorRepositoryProtocol (private,
insert/update, get_theme_curator_repository). Every fixture is
synthetic; Postgres tests use the shared, fail-soft local-only fixtures
from tests/_postgres_test_support.py."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.config.settings import Settings
from src.data_access import backend_factory, theme_store
from src.models.theme_research import ResearchTheme, ThemeCategory, ThemeStatus, ThemeVisibility

from tests._postgres_test_support import pg_conn, pg_isolated_connection  # noqa: F401

REPO_ROOT = Path(__file__).parent.parent


def _theme(title="Test theme", created_at="2026-08-20T00:00:00+00:00", **overrides):
    defaults = dict(
        id=theme_store.build_theme_id(title, created_at),
        category=ThemeCategory.BOTTLENECK, status=ThemeStatus.NEW, visibility=ThemeVisibility.INTERNAL,
        title=title, key_question="q", hypothesis="h", working_thesis="w", why_it_matters="y",
        what_could_change_the_view="c", what_to_watch_next="n", created_at=created_at, updated_at=created_at,
    )
    defaults.update(overrides)
    return ResearchTheme(**defaults)


def _sqlite_settings(tmp_path) -> Settings:
    return Settings(db_backend="sqlite", state_db_path=tmp_path / "state.db")


# ============================================================
# Public protocol: JSON/SQLite/Postgres, published-only
# ============================================================


def test_json_repository_published_only(tmp_path):
    repo = backend_factory.get_theme_repository(Settings(db_backend="json", cache_dir=tmp_path))
    internal = _theme(visibility=ThemeVisibility.INTERNAL)
    theme_store.append_theme(tmp_path, internal)
    assert repo.list_published_themes() == ()
    assert repo.get_published_theme(internal.id) is None

    theme_store.set_theme_visibility(tmp_path, internal.id, ThemeVisibility.PUBLISHED, "2026-08-21T00:00:00+00:00")
    assert len(repo.list_published_themes()) == 1
    assert repo.get_published_theme(internal.id) is not None


def test_sqlite_repository_published_only(tmp_path):
    settings = _sqlite_settings(tmp_path)
    curator = backend_factory.get_theme_curator_repository(settings)
    theme = _theme()
    curator.insert_theme(theme)

    repo = backend_factory.get_theme_repository(settings)
    assert repo.list_published_themes() == ()
    assert repo.get_published_theme(theme.id) is None

    curator.set_visibility(theme.id, ThemeVisibility.PUBLISHED, "2026-08-21T00:00:00+00:00")
    assert len(repo.list_published_themes()) == 1
    assert repo.get_published_theme(theme.id) is not None


def test_postgres_repository_published_only(pg_conn):
    from src.data_access.postgres_state_db import theme_repository as postgres_themes

    theme = _theme(title="PG BF theme")
    postgres_themes.insert_theme(pg_conn, theme)
    repo = backend_factory.PostgresThemeRepository(conn=pg_conn)
    assert repo.get_published_theme(theme.id) is None
    postgres_themes.set_theme_visibility(pg_conn, theme.id, ThemeVisibility.PUBLISHED, "2026-08-21T00:00:00+00:00")
    assert repo.get_published_theme(theme.id) is not None


def test_ready_to_publish_and_archived_themes_behave_as_not_found(tmp_path):
    settings = _sqlite_settings(tmp_path)
    curator = backend_factory.get_theme_curator_repository(settings)
    ready = _theme(title="Ready")
    curator.insert_theme(ready)
    curator.set_visibility(ready.id, ThemeVisibility.READY_TO_PUBLISH, "2026-08-21T00:00:00+00:00")

    archived_source = _theme(title="Archived source")
    curator.insert_theme(archived_source)
    curator.set_visibility(archived_source.id, ThemeVisibility.READY_TO_PUBLISH, "2026-08-21T00:00:00+00:00")
    curator.set_visibility(archived_source.id, ThemeVisibility.PUBLISHED, "2026-08-22T00:00:00+00:00")
    curator.set_visibility(archived_source.id, ThemeVisibility.ARCHIVED, "2026-08-23T00:00:00+00:00")

    repo = backend_factory.get_theme_repository(settings)
    assert repo.get_published_theme(ready.id) is None
    assert repo.get_published_theme(archived_source.id) is None
    assert repo.list_published_themes() == ()


def test_evidence_and_company_map_for_theme_wrap_bulk_functions_for_one_id(tmp_path):
    from src.models.theme_research import CompanyRole, EvidenceDirection, ThemeCompanyMapEntry, ThemeEvidenceItem

    settings = _sqlite_settings(tmp_path)
    curator = backend_factory.get_theme_curator_repository(settings)
    theme = _theme()
    curator.insert_theme(theme)
    curator.set_visibility(theme.id, ThemeVisibility.PUBLISHED, "2026-08-21T00:00:00+00:00")

    item = ThemeEvidenceItem(
        id=theme_store.build_theme_evidence_id(theme.id, "https://example.com/x", "2026-08-15"),
        theme_id=theme.id, date="2026-08-15", company="Acme", source_name="SEC EDGAR",
        source_url="https://example.com/x", fact="f", relevance="r", direction=EvidenceDirection.SUPPORTS,
    )
    curator.insert_evidence_item(item)
    entry = ThemeCompanyMapEntry(
        id=theme_store.build_theme_company_map_id(theme.id, "Acme", CompanyRole.EXPOSED),
        theme_id=theme.id, company_name="Acme", role=CompanyRole.EXPOSED, note=None,
    )
    curator.insert_company_map_entry(entry)

    repo = backend_factory.get_theme_repository(settings)
    assert repo.evidence_for_theme(theme.id) == (item,)
    assert repo.company_map_for_theme(theme.id) == (entry,)
    assert repo.evidence_for_theme("theme-missing") == ()


# ============================================================
# Curator (private) seam
# ============================================================


def test_curator_get_theme_returns_any_visibility(tmp_path):
    settings = _sqlite_settings(tmp_path)
    curator = backend_factory.get_theme_curator_repository(settings)
    theme = _theme(visibility=ThemeVisibility.INTERNAL)
    curator.insert_theme(theme)
    assert curator.get_theme(theme.id) == theme


def test_curator_insert_theme_duplicate_rejected(tmp_path):
    settings = _sqlite_settings(tmp_path)
    curator = backend_factory.get_theme_curator_repository(settings)
    theme = _theme()
    assert curator.insert_theme(theme) is True
    assert curator.insert_theme(theme) is False


def test_curator_set_visibility_missing_theme_is_none(tmp_path):
    settings = _sqlite_settings(tmp_path)
    curator = backend_factory.get_theme_curator_repository(settings)
    assert curator.set_visibility("theme-missing", ThemeVisibility.PUBLISHED, "2026-08-21T00:00:00+00:00") is None


def test_curator_repository_supports_json_backend_unlike_bundle_writer(tmp_path):
    """Unlike get_research_case_bundle_writer() (worker-only, no JSON
    branch), the theme curator seam must support JSON — the curator
    script's own --backend json option requires it."""
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    curator = backend_factory.get_theme_curator_repository(settings)
    assert isinstance(curator, backend_factory.JsonThemeCuratorRepository)
    theme = _theme()
    assert curator.insert_theme(theme) is True


# ============================================================
# Protocol shape / no forbidden methods on the public protocol
# ============================================================


def test_public_protocol_exposes_no_write_method():
    for cls in (backend_factory.JsonThemeRepository, backend_factory.SqliteThemeRepository, backend_factory.PostgresThemeRepository):
        exported = {name for name in dir(cls) if not name.startswith("_")}
        forbidden_substrings = ("insert", "update", "delete", "replace", "upsert", "set_visibility")
        offenders = [name for name in exported if any(f in name.lower() for f in forbidden_substrings)]
        assert not offenders, (cls, offenders)


def test_all_three_public_adapters_expose_the_same_four_methods():
    expected = {"list_published_themes", "get_published_theme", "evidence_for_theme", "company_map_for_theme"}
    for cls in (backend_factory.JsonThemeRepository, backend_factory.SqliteThemeRepository, backend_factory.PostgresThemeRepository):
        exported = {name for name in dir(cls) if not name.startswith("_")}
        assert expected <= exported


# ============================================================
# Scope guard
# ============================================================


def test_no_new_dependency_added_to_requirements():
    result = subprocess.run(["git", "diff", "HEAD", "--", "requirements.txt"], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    assert result.stdout.strip() == "", f"requirements.txt was modified: {result.stdout}"
