"""scripts.import_daily_news_json_to_db — the manual, explicitly-invoked
JSON-to-durable-backend import path (Daily News durability workstream).
Never invoked automatically by anything else in this repo; every test
here calls it directly, the same way an operator would run the script."""
from __future__ import annotations

from src.data_access.daily_news import daily_news_store
from src.data_access.state_db import connection, daily_news_repository, schema
from src.models.daily_news_models import NewsSourceReference, NewsStory, NewsStoryStatus, SourceClass
from scripts.import_daily_news_json_to_db import import_json_stories


def _story(story_id: str, url: str = "https://nvidianews.nvidia.com/releases/one") -> NewsStory:
    source = NewsSourceReference(
        publisher="NVIDIA", source_class=SourceClass.OFFICIAL_COMPANY, url=url,
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


class _SqliteRepoAdapter:
    def __init__(self, conn):
        self.conn = conn

    def load_stories(self):
        return daily_news_repository.load_stories(self.conn)

    def upsert_new_stories(self, new_stories):
        return daily_news_repository.upsert_new_stories(self.conn, new_stories)


def test_import_moves_every_json_story_into_the_target_repository(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [
        _story("newsitem-a", url="https://nvidianews.nvidia.com/releases/a"),
        _story("newsitem-b", url="https://nvidianews.nvidia.com/releases/b"),
    ])
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    repository = _SqliteRepoAdapter(conn)

    summary = import_json_stories(tmp_path, repository)

    assert summary == {"json_story_count": 2, "already_present_count": 0, "imported_count": 2}
    assert set(daily_news_repository.load_stories(conn).keys()) == {"newsitem-a", "newsitem-b"}


def test_import_never_modifies_or_deletes_the_json_file(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_story("newsitem-a")])
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    repository = _SqliteRepoAdapter(conn)

    import_json_stories(tmp_path, repository)

    # The JSON store is untouched — still readable, still has the story.
    assert (tmp_path / "daily_news_stories.json").exists()
    assert set(daily_news_store.load_stories(tmp_path).keys()) == {"newsitem-a"}


def test_import_is_idempotent_when_run_twice():
    import tempfile
    from pathlib import Path

    tmp_path = Path(tempfile.mkdtemp())
    daily_news_store.upsert_new_stories(tmp_path, [_story("newsitem-a")])
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    repository = _SqliteRepoAdapter(conn)

    first = import_json_stories(tmp_path, repository)
    second = import_json_stories(tmp_path, repository)

    assert first["imported_count"] == 1
    assert second["imported_count"] == 0
    assert second["already_present_count"] == 1
    assert len(daily_news_repository.load_stories(conn)) == 1


def test_import_skips_stories_already_present_in_the_target_but_imports_new_ones(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [
        _story("newsitem-a", url="https://nvidianews.nvidia.com/releases/a"),
        _story("newsitem-b", url="https://nvidianews.nvidia.com/releases/b"),
    ])
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    # newsitem-a already exists in the target backend (e.g. discovered
    # there directly by an admin trigger already pointed at this backend).
    daily_news_repository.upsert_new_stories(conn, [_story("newsitem-a", url="https://nvidianews.nvidia.com/releases/a")])
    repository = _SqliteRepoAdapter(conn)

    summary = import_json_stories(tmp_path, repository)

    assert summary == {"json_story_count": 2, "already_present_count": 1, "imported_count": 1}
    assert set(daily_news_repository.load_stories(conn).keys()) == {"newsitem-a", "newsitem-b"}


def test_import_with_empty_json_store_is_a_safe_no_op(tmp_path):
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    repository = _SqliteRepoAdapter(conn)

    summary = import_json_stories(tmp_path, repository)

    assert summary == {"json_story_count": 0, "already_present_count": 0, "imported_count": 0}
    assert daily_news_repository.load_stories(conn) == {}


def test_main_is_a_safe_no_op_when_backend_is_json(monkeypatch, tmp_path, capsys):
    from scripts import import_daily_news_json_to_db
    from src.config.settings import Settings

    settings = Settings(db_backend="json", cache_dir=tmp_path)
    monkeypatch.setattr(import_daily_news_json_to_db, "get_settings", lambda: settings)

    result = import_daily_news_json_to_db.main()

    assert result == 0
    assert "no durable target to import into" in capsys.readouterr().out.lower()
