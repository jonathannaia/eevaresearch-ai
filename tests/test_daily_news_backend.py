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
from src.models.daily_news_models import EditorialStory, NewsSourceReference, NewsStory, NewsStoryStatus, SourceClass

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


# --- Daily News autonomous worker: scan-status repository factory ---
# No JSON branch — mirrors backend_factory.get_scan_status_repository's
# own deliberate JSON-rejection discipline exactly.


def test_scan_status_json_or_unrecognized_backend_raises_configuration_error(tmp_path):
    from src.data_access.daily_news.daily_news_backend import get_daily_news_scan_status_repository

    for backend_value in ("json", "not-a-real-backend"):
        settings = _settings(backend_value, cache_dir=tmp_path)
        with pytest.raises(BackendConfigurationError):
            get_daily_news_scan_status_repository(settings)


def test_scan_status_sqlite_requires_configured_path():
    from src.data_access.daily_news.daily_news_backend import get_daily_news_scan_status_repository

    settings = _settings("sqlite")
    with pytest.raises(BackendConfigurationError):
        get_daily_news_scan_status_repository(settings)


def test_scan_status_sqlite_repository_round_trips_through_protocol_methods(tmp_path):
    from src.data_access.daily_news.daily_news_backend import (
        SqliteDailyNewsScanStatusRepository,
        get_daily_news_scan_status_repository,
    )
    from src.data_access.state_db.daily_news_scan_status_repository import DailyNewsFeedScanStatus

    settings = _settings("sqlite", state_db_path=str(tmp_path / "state.db"))
    repo = get_daily_news_scan_status_repository(settings)
    assert isinstance(repo, SqliteDailyNewsScanStatusRepository)
    assert repo.get_feed_status("NVIDIA") is None

    status = DailyNewsFeedScanStatus(
        company_name="NVIDIA", last_attempt_at="2026-01-01T00:00:00+00:00",
        last_fetch_success_at="2026-01-01T00:00:05+00:00", last_story_published_at=None,
        last_failure_code=None, items_discovered_last_run=1, stories_published_last_run=0,
        updated_at="2026-01-01T00:00:05+00:00",
    )
    repo.upsert_feed_status(status)
    assert repo.get_feed_status("NVIDIA") == status
    assert repo.get_all_feed_statuses() == {"NVIDIA": status}


def test_scan_status_postgres_repository_round_trips_through_protocol_methods(pg_conn):
    from src.data_access.daily_news.daily_news_backend import PostgresDailyNewsScanStatusRepository
    from src.data_access.state_db.daily_news_scan_status_repository import DailyNewsWorkerStatus

    repo = PostgresDailyNewsScanStatusRepository(conn=pg_conn)
    assert repo.get_worker_status() is None

    status = DailyNewsWorkerStatus(
        worker_key="daily_news", last_tick_started_at="2026-01-01T00:00:00+00:00",
        last_tick_completed_at="2026-01-01T00:00:10+00:00", last_reconciliation_at=None,
        last_failure_code=None, updated_at="2026-01-01T00:00:10+00:00",
    )
    repo.upsert_worker_status(status)
    assert repo.get_worker_status() == status


# --- Editorial Daily News — Postgres persistence fix: repository
# factory selection (design/DECISIONS.md). Storage only — no feed
# fetching, no matching/dedup/cap logic is exercised here.


def _editorial_story(story_id: str = "editorial-abc") -> EditorialStory:
    now = "2026-09-11T12:00:00+00:00"
    return EditorialStory(
        id=story_id, headline="Oracle Corporation reports strong AI cloud demand", publisher="CNBC",
        source_url="https://www.cnbc.com/2026/09/11/oracle-ai-cloud.html", published_at=now, retrieved_at=now,
        excerpt="Oracle Corporation said AI cloud demand drove revenue higher.",
        matched_companies=("Oracle Corporation",), matched_themes=("ai-buildout",),
        source_feed_id="cnbc-technology-rss",
    )


def test_editorial_backend_factory_json_returns_json_repository(tmp_path):
    from src.data_access.daily_news.daily_news_backend import JsonEditorialStoryRepository, get_editorial_story_repository

    settings = _settings("json", cache_dir=tmp_path)
    repo = get_editorial_story_repository(settings)
    assert isinstance(repo, JsonEditorialStoryRepository)
    assert repo.cache_dir == tmp_path


def test_editorial_backend_factory_unrecognized_backend_defaults_to_json(tmp_path):
    from src.data_access.daily_news.daily_news_backend import JsonEditorialStoryRepository, get_editorial_story_repository

    settings = _settings("not-a-real-backend", cache_dir=tmp_path)
    repo = get_editorial_story_repository(settings)
    assert isinstance(repo, JsonEditorialStoryRepository)


def test_editorial_backend_factory_postgres_requires_configured_url():
    from src.data_access.daily_news.daily_news_backend import get_editorial_story_repository

    settings = _settings("postgres")
    with pytest.raises(BackendConfigurationError):
        get_editorial_story_repository(settings)


def test_editorial_backend_factory_postgres_selected_when_configured(pg_conn, monkeypatch):
    from src.data_access.daily_news import daily_news_backend
    from src.data_access.daily_news.daily_news_backend import PostgresEditorialStoryRepository, get_editorial_story_repository

    monkeypatch.setattr(daily_news_backend, "_require_postgres_connection", lambda settings: pg_conn)
    settings = _settings("postgres", state_db_url="postgres://unused-because-monkeypatched")
    repo = get_editorial_story_repository(settings)
    assert isinstance(repo, PostgresEditorialStoryRepository)
    assert repo.conn is pg_conn


def test_editorial_postgres_repository_migrates_the_database_to_current_version(pg_conn):
    from src.data_access.postgres_state_db import schema as postgres_schema

    assert postgres_schema.get_schema_version(pg_conn) == postgres_schema.CURRENT_SCHEMA_VERSION


def test_editorial_postgres_repository_wires_through_to_real_module(pg_conn):
    from src.data_access.daily_news.daily_news_backend import PostgresEditorialStoryRepository
    from src.data_access.postgres_state_db import editorial_story_repository as postgres_editorial_story_repository

    repo = PostgresEditorialStoryRepository(conn=pg_conn)
    story = _editorial_story()
    postgres_editorial_story_repository.upsert_new_stories(pg_conn, [story])
    loaded = repo.load_stories()
    assert loaded[story.id].headline == story.headline
    assert loaded[story.id].matched_companies == ("Oracle Corporation",)


def test_editorial_postgres_repository_upsert_is_idempotent(pg_conn):
    from src.data_access.daily_news.daily_news_backend import PostgresEditorialStoryRepository

    repo = PostgresEditorialStoryRepository(conn=pg_conn)
    story = _editorial_story()
    repo.upsert_new_stories([story])
    repo.upsert_new_stories([story])
    assert len(repo.load_stories()) == 1


def test_editorial_repository_returned_by_the_same_factory_call_the_ui_and_discovery_script_both_use_sees_persisted_rows(pg_conn, monkeypatch):
    """editorial_coverage.py's render_editorial_coverage() and
    scripts/run_daily_news_discovery.py's --editorial-only both call
    daily_news_backend.get_editorial_story_repository(get_settings())
    directly — this proves that once the factory returns a Postgres
    repository, rows persisted through it are visible through a fresh
    call to that same factory, the same way the live page's own call
    would see them."""
    from src.data_access.daily_news import daily_news_backend
    from src.data_access.daily_news.daily_news_backend import get_editorial_story_repository

    monkeypatch.setattr(daily_news_backend, "_require_postgres_connection", lambda settings: pg_conn)
    settings = _settings("postgres", state_db_url="postgres://unused-because-monkeypatched")

    writer_repo = get_editorial_story_repository(settings)
    story = _editorial_story()
    writer_repo.upsert_new_stories([story])

    reader_repo = get_editorial_story_repository(settings)
    assert story.id in reader_repo.load_stories()


def test_json_editorial_story_repository_delegates_to_editorial_story_store(tmp_path):
    from src.data_access.daily_news import editorial_story_store
    from src.data_access.daily_news.daily_news_backend import JsonEditorialStoryRepository

    story = _editorial_story()
    editorial_story_store.upsert_new_stories(tmp_path, [story])
    repo = JsonEditorialStoryRepository(cache_dir=tmp_path)
    assert story.id in repo.load_stories()
