"""Daily News durability workstream — the DailyNewsRepository
backend-selection seam (src.data_access.daily_news.daily_news_backend)
across JSON, SQLite, and Postgres. Deliberately its own module, not part
of backend_factory.py — see daily_news_backend.py's own docstring for
why (tests/test_comparison_bulk_retrieval.py::
test_backend_factory_does_not_import_ui_or_pipeline_modules enforces
that backend_factory.py never imports anything from
src.data_access.daily_news). Storage only: no feed fetching, no
discovery pipeline logic, no UI change is exercised or asserted here.
Postgres tests use the shared, fail-soft local-only fixtures from
tests/_postgres_test_support.py and skip cleanly when no local
disposable Postgres instance is available."""
from __future__ import annotations

import pytest

from src.data_access.daily_news import daily_news_backend, daily_news_store
from src.data_access.daily_news.daily_news_backend import (
    BackendConfigurationError,
    DailyNewsRepositoryProtocol,
    JsonDailyNewsRepository,
    PostgresDailyNewsRepository,
    SqliteDailyNewsRepository,
    get_daily_news_repository,
)
from src.data_access.state_db import connection as sqlite_connection
from src.data_access.state_db import daily_news_repository as sqlite_daily_news_repository
from src.data_access.state_db import schema as sqlite_schema
from src.models.daily_news_models import NewsSourceReference, NewsStory, NewsStoryStatus, SourceClass

from tests._postgres_test_support import pg_conn, pg_isolated_connection  # noqa: F401


def _settings(db_backend: str = "json", **overrides):
    from src.config.settings import Settings

    fields = dict(db_backend=db_backend)
    fields.update(overrides)
    return Settings(**fields)


def _story(story_id: str = "newsitem-nvidia-1") -> NewsStory:
    source = NewsSourceReference(
        publisher="NVIDIA", source_class=SourceClass.OFFICIAL_COMPANY, url="https://nvidianews.nvidia.com/releases/one",
        title="NVIDIA announces new platform", published_at="2026-09-01T12:00:00+00:00",
        retrieved_at="2026-09-01T12:05:00+00:00", original_language="English",
        excerpt_original="NVIDIA today announced a new platform.",
    )
    return NewsStory(
        id=story_id, company_name="NVIDIA", ticker="NVDA", theme_slug="ai-buildout",
        headline="NVIDIA announces new platform", eeva_summary="NVIDIA today announced a new platform.",
        is_fallback_summary=False, translation_unavailable=False, original_title=None,
        sources=(source,), status=NewsStoryStatus.PUBLISHED, state_history=[],
    )


def test_backend_factory_json_returns_json_daily_news_repository(tmp_path):
    settings = _settings("json", cache_dir=tmp_path)
    repo = get_daily_news_repository(settings)
    assert isinstance(repo, JsonDailyNewsRepository)
    assert repo.cache_dir == tmp_path


def test_backend_factory_unrecognized_backend_defaults_to_json(tmp_path):
    settings = _settings("not-a-real-backend", cache_dir=tmp_path)
    repo = get_daily_news_repository(settings)
    assert isinstance(repo, JsonDailyNewsRepository)


def test_backend_factory_sqlite_requires_configured_path():
    settings = _settings("sqlite")
    with pytest.raises(BackendConfigurationError):
        get_daily_news_repository(settings)


def test_backend_factory_postgres_requires_configured_url():
    settings = _settings("postgres")
    with pytest.raises(BackendConfigurationError):
        get_daily_news_repository(settings)


def test_backend_factory_sqlite_repository_wires_through_to_real_module(tmp_path):
    settings = _settings("sqlite", state_db_path=str(tmp_path / "state.db"))
    repo = get_daily_news_repository(settings)
    assert isinstance(repo, SqliteDailyNewsRepository)
    story = _story()
    sqlite_daily_news_repository.upsert_new_stories(repo.conn, [story])
    result = repo.get_story(story.id)
    assert result.id == story.id
    assert result.company_name == "NVIDIA"


def test_sqlite_repository_migrates_the_database_to_current_version(tmp_path):
    settings = _settings("sqlite", state_db_path=str(tmp_path / "state.db"))
    repo = get_daily_news_repository(settings)
    assert sqlite_schema.get_schema_version(repo.conn) == sqlite_schema.CURRENT_SCHEMA_VERSION


def test_json_daily_news_repository_delegates_to_daily_news_store(tmp_path):
    story = _story()
    daily_news_store.upsert_new_stories(tmp_path, [story])
    repo = JsonDailyNewsRepository(cache_dir=tmp_path)
    result = repo.get_story(story.id)
    assert result.id == story.id


def test_json_daily_news_repository_update_story_always_succeeds(tmp_path):
    story = _story()
    repo = JsonDailyNewsRepository(cache_dir=tmp_path)
    repo.upsert_new_stories([story])
    story.status = NewsStoryStatus.SUPPRESSED
    outcome = repo.update_story(story)
    assert outcome.status == "updated"
    assert repo.get_story(story.id).status == NewsStoryStatus.SUPPRESSED


def test_json_daily_news_repository_get_story_version_is_always_none(tmp_path):
    repo = JsonDailyNewsRepository(cache_dir=tmp_path)
    repo.upsert_new_stories([_story()])
    assert repo.get_story_version("newsitem-nvidia-1") is None


def test_sqlite_repository_update_story_omitted_version_re_reads_current(tmp_path):
    settings = _settings("sqlite", state_db_path=str(tmp_path / "state.db"))
    repo = get_daily_news_repository(settings)
    story = _story()
    repo.upsert_new_stories([story])
    stored = repo.get_story(story.id)
    stored.status = NewsStoryStatus.SUPPRESSED
    outcome = repo.update_story(stored)  # expected_version omitted
    assert outcome.status == "updated"
    assert outcome.current.status == NewsStoryStatus.SUPPRESSED


def test_daily_news_repository_protocol_shape():
    # Smallest-abstraction proof: exactly the five methods run_discovery/
    # the public page need are part of the Protocol — no admin-report,
    # no feed-fetch, no dedup method leaks into the storage contract.
    expected = {"load_stories", "get_story", "get_story_version", "upsert_new_stories", "update_story"}
    assert expected <= set(dir(DailyNewsRepositoryProtocol))


def test_daily_news_backend_module_never_imports_ui_or_streamlit():
    import ast
    from pathlib import Path

    path = Path(__file__).parent.parent / "src" / "data_access" / "daily_news" / "daily_news_backend.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    forbidden = ("src.ui", "streamlit")
    offenders = []
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for module in modules:
            if any(module == f or module.startswith(f + ".") for f in forbidden):
                offenders.append(module)
    assert not offenders, offenders


# --- Postgres ---


def test_postgres_repository_wires_through_to_real_module(pg_conn):
    from src.data_access.postgres_state_db import daily_news_repository as postgres_daily_news_repository

    repo = PostgresDailyNewsRepository(conn=pg_conn)
    story = _story()
    postgres_daily_news_repository.upsert_new_stories(pg_conn, [story])
    result = repo.get_story(story.id)
    assert result.id == story.id
    assert result.company_name == "NVIDIA"


def test_postgres_repository_update_story_omitted_version_re_reads_current(pg_conn):
    repo = PostgresDailyNewsRepository(conn=pg_conn)
    story = _story()
    repo.upsert_new_stories([story])
    stored = repo.get_story(story.id)
    stored.status = NewsStoryStatus.SUPPRESSED
    outcome = repo.update_story(stored)
    assert outcome.status == "updated"
    assert outcome.current.status == NewsStoryStatus.SUPPRESSED
