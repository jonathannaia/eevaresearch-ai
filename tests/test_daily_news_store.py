"""daily_news_store — plain JSON persistence round-trip, no network."""
from __future__ import annotations

from src.data_access.daily_news import daily_news_store
from src.models.daily_news_models import (
    NewsSourceReference,
    NewsStateTransition,
    NewsStory,
    NewsStoryStatus,
    SourceClass,
)


def _story(story_id: str = "newsitem-nvidia-abc123") -> NewsStory:
    source = NewsSourceReference(
        publisher="NVIDIA", source_class=SourceClass.OFFICIAL_COMPANY,
        url="https://nvidianews.nvidia.com/news/example", title="Example headline",
        published_at="2026-08-24T12:00:00+00:00", retrieved_at="2026-08-24T12:05:00+00:00",
        original_language="English", excerpt_original="Example did a thing.",
    )
    return NewsStory(
        id=story_id, company_name="NVIDIA", ticker="NVDA", theme_slug="ai-buildout",
        headline="Example headline", eeva_summary="Example did a thing.",
        is_fallback_summary=False, translation_unavailable=False, original_title=None,
        sources=(source,), status=NewsStoryStatus.PUBLISHED,
        state_history=[NewsStateTransition(status=NewsStoryStatus.PUBLISHED, at="2026-08-24T12:05:00+00:00")],
    )


def test_load_from_nonexistent_file_returns_empty_dict(tmp_path):
    assert daily_news_store.load_stories(tmp_path) == {}


def test_upsert_and_load_round_trips_a_story(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_story()])

    loaded = daily_news_store.load_stories(tmp_path)

    assert set(loaded) == {"newsitem-nvidia-abc123"}
    story = loaded["newsitem-nvidia-abc123"]
    assert story.headline == "Example headline"
    assert story.sources[0].url == "https://nvidianews.nvidia.com/news/example"
    assert story.status == NewsStoryStatus.PUBLISHED


def test_upsert_never_overwrites_an_existing_id(tmp_path):
    original = _story()
    daily_news_store.upsert_new_stories(tmp_path, [original])
    changed = _story()
    changed.headline = "Different headline"

    daily_news_store.upsert_new_stories(tmp_path, [changed])

    loaded = daily_news_store.load_stories(tmp_path)
    assert loaded["newsitem-nvidia-abc123"].headline == "Example headline"


def test_update_story_overwrites_the_existing_entry(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [_story()])
    story = daily_news_store.load_stories(tmp_path)["newsitem-nvidia-abc123"]
    story.status = NewsStoryStatus.SUPPRESSED

    daily_news_store.update_story(tmp_path, story)

    assert daily_news_store.load_stories(tmp_path)["newsitem-nvidia-abc123"].status == NewsStoryStatus.SUPPRESSED


def test_handles_corrupt_cache_file_without_raising(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "daily_news_stories.json").write_text("{not valid json", encoding="utf-8")

    assert daily_news_store.load_stories(tmp_path) == {}


def test_image_url_and_alt_round_trip(tmp_path):
    story = _story()
    story.sources = (
        NewsSourceReference(
            publisher="NVIDIA", source_class=SourceClass.OFFICIAL_COMPANY,
            url="https://nvidianews.nvidia.com/news/example", title="Example headline",
            published_at="2026-08-24T12:00:00+00:00", retrieved_at="2026-08-24T12:05:00+00:00",
            original_language="English", excerpt_original="Example did a thing.",
            image_url="https://iprsoftwaremedia.com/photo.jpg", image_alt="A photo",
        ),
    )
    daily_news_store.upsert_new_stories(tmp_path, [story])

    loaded = daily_news_store.load_stories(tmp_path)

    assert loaded["newsitem-nvidia-abc123"].sources[0].image_url == "https://iprsoftwaremedia.com/photo.jpg"
    assert loaded["newsitem-nvidia-abc123"].sources[0].image_alt == "A photo"


def test_pre_existing_persisted_story_without_image_fields_still_loads(tmp_path):
    # Backward compatibility: a story written to disk before image_url/
    # image_alt existed has no such keys in its source dict at all — must
    # still load cleanly with both fields defaulting to None, not raise.
    import json

    tmp_path.mkdir(exist_ok=True)
    legacy_payload = {
        "newsitem-legacy-1": {
            "id": "newsitem-legacy-1", "company_name": "NVIDIA", "ticker": "NVDA", "theme_slug": "ai-buildout",
            "headline": "Legacy headline", "eeva_summary": "Legacy summary.", "is_fallback_summary": False,
            "translation_unavailable": False, "original_title": None,
            "sources": [{
                "publisher": "NVIDIA", "source_class": "Official company source",
                "url": "https://nvidianews.nvidia.com/news/legacy", "title": "Legacy headline",
                "published_at": "2026-08-24T12:00:00+00:00", "retrieved_at": "2026-08-24T12:05:00+00:00",
                "original_language": "English", "excerpt_original": "Legacy summary.",
            }],
            "status": "Published", "state_history": [],
        }
    }
    (tmp_path / "daily_news_stories.json").write_text(json.dumps(legacy_payload), encoding="utf-8")

    loaded = daily_news_store.load_stories(tmp_path)

    assert loaded["newsitem-legacy-1"].sources[0].image_url is None
    assert loaded["newsitem-legacy-1"].sources[0].image_alt is None
