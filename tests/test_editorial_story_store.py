"""editorial_story_store — real file I/O against a pytest tmp_path,
never the production data/cache/ directory. Mirrors
tests/test_daily_news_store.py's own conventions for NewsStory."""
from __future__ import annotations

import json

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


# ============================================================
# identified_companies (design/DAILY_NEWS_IDENTIFIED_VS_MATCHED_
# COMPANIES_DISCOVERY_2026_09_16.md, design/DAILY_NEWS_COMPANY_
# ATTRIBUTION_IMPLEMENTATION_READINESS_2026_09_16.md) — additive
# raw-recognition field, round-trip and old-record backward
# compatibility.
# ============================================================


def test_identified_companies_round_trips_exactly(tmp_path):
    # A story where the raw-recognized set is a genuine superset of the
    # admission-vetted set — proves the two fields are stored and
    # reloaded independently, never conflated.
    story = EditorialStory(**{
        **_story().__dict__,
        "identified_companies": ("Oracle Corporation", "Amazon.com, Inc."),
    })
    editorial_story_store.upsert_new_stories(tmp_path, [story])

    loaded = editorial_story_store.load_stories(tmp_path)

    reloaded = loaded["editorial-abc123"]
    assert reloaded.identified_companies == ("Oracle Corporation", "Amazon.com, Inc.")
    assert reloaded.matched_companies == ("Oracle Corporation",)


def test_old_record_missing_identified_companies_deserializes_with_safe_empty_default(tmp_path):
    # Simulates a genuinely pre-migration on-disk record — written
    # directly as raw JSON, bypassing save_stories(), with no
    # "identified_companies" key at all (the shape every record had
    # before this field existed). Must deserialize successfully, with
    # identified_companies defaulting to (), and matched_companies
    # completely unchanged from whatever was already stored — no
    # migration, backfill, or rewrite of the legacy value.
    legacy_record = {
        "id": "editorial-legacy-001",
        "headline": "Oracle Corporation reports strong AI cloud demand",
        "publisher": "CNBC",
        "source_url": "https://www.cnbc.com/2026/09/11/oracle-ai-cloud.html",
        "published_at": "2026-09-11T12:00:00+00:00",
        "retrieved_at": "2026-09-11T12:05:00+00:00",
        "excerpt": "Oracle Corporation said AI cloud demand drove revenue higher.",
        "matched_companies": ["Oracle Corporation"],
        "matched_themes": ["ai-buildout"],
        "source_feed_id": "cnbc-technology-rss",
        # Deliberately no "identified_companies" key — the legacy shape.
    }
    cache_dir = tmp_path
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "daily_news_editorial_stories.json").write_text(
        json.dumps({"editorial-legacy-001": legacy_record}), encoding="utf-8",
    )

    loaded = editorial_story_store.load_stories(cache_dir)

    assert len(loaded) == 1
    story = loaded["editorial-legacy-001"]
    assert story.identified_companies == ()
    assert story.matched_companies == ("Oracle Corporation",)
