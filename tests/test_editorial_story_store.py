"""editorial_story_store — real file I/O against a pytest tmp_path,
never the production data/cache/ directory. Mirrors
tests/test_daily_news_store.py's own conventions for NewsStory."""
from __future__ import annotations

from src.data_access.daily_news import editorial_story_store
from src.models.daily_news_models import EditorialStory


def _story(story_id: str = "editorial-abc123") -> EditorialStory:
    return EditorialStory(
        id=story_id, headline="Oracle Corporation reports strong AI cloud demand",
        publisher="CNBC", source_url="https://www.cnbc.com/2026/09/11/oracle-ai-cloud.html",
        published_at="2026-09-11T12:00:00+00:00", retrieved_at="2026-09-11T12:05:00+00:00",
        excerpt="Oracle Corporation said AI cloud demand drove revenue higher.",
        matched_companies=("Oracle Corporation",), matched_themes=("ai-buildout",),
        source_feed_id="cnbc-technology-rss",
    )


def test_load_stories_on_empty_cache_dir_returns_empty_dict(tmp_path):
    assert editorial_story_store.load_stories(tmp_path) == {}


def test_upsert_then_load_round_trips_exactly(tmp_path):
    editorial_story_store.upsert_new_stories(tmp_path, [_story()])

    loaded = editorial_story_store.load_stories(tmp_path)

    assert len(loaded) == 1
    story = loaded["editorial-abc123"]
    assert story.headline == "Oracle Corporation reports strong AI cloud demand"
    assert story.matched_companies == ("Oracle Corporation",)
    assert story.matched_themes == ("ai-buildout",)
    assert story.excerpt == "Oracle Corporation said AI cloud demand drove revenue higher."


def test_upsert_does_not_overwrite_an_existing_id(tmp_path):
    original = _story()
    editorial_story_store.upsert_new_stories(tmp_path, [original])
    changed = EditorialStory(**{**original.__dict__, "headline": "A different headline"})

    editorial_story_store.upsert_new_stories(tmp_path, [changed])

    loaded = editorial_story_store.load_stories(tmp_path)
    assert loaded["editorial-abc123"].headline == "Oracle Corporation reports strong AI cloud demand"


def test_upsert_adds_a_new_id_alongside_an_existing_one(tmp_path):
    editorial_story_store.upsert_new_stories(tmp_path, [_story("editorial-a")])
    editorial_story_store.upsert_new_stories(tmp_path, [_story("editorial-b")])

    loaded = editorial_story_store.load_stories(tmp_path)
    assert set(loaded) == {"editorial-a", "editorial-b"}


def test_none_excerpt_round_trips_as_none(tmp_path):
    no_excerpt = EditorialStory(**{**_story().__dict__, "excerpt": None})
    editorial_story_store.upsert_new_stories(tmp_path, [no_excerpt])

    loaded = editorial_story_store.load_stories(tmp_path)
    assert loaded["editorial-abc123"].excerpt is None


def test_uses_a_separate_cache_file_from_issuer_stories(tmp_path):
    editorial_story_store.upsert_new_stories(tmp_path, [_story()])
    assert (tmp_path / "daily_news_editorial_stories.json").exists()
    assert not (tmp_path / "daily_news_stories.json").exists()


def test_malformed_json_file_returns_empty_dict_not_a_crash(tmp_path):
    (tmp_path / "daily_news_editorial_stories.json").write_text("not valid json", encoding="utf-8")
    assert editorial_story_store.load_stories(tmp_path) == {}
