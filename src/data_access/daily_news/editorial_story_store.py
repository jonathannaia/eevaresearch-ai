"""Editorial Daily News v1 (design/DECISIONS.md) — persisted store of
every EditorialStory ever discovered. A separate cache file from
daily_news_store.py's daily_news_stories.json, mirroring that module's
own read-modify-write, single-authoritative-copy-per-id shape exactly —
zero change to daily_news_store.py or the issuer NewsStory it persists.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

from src.models.daily_news_models import EditorialStory

_CACHE_FILENAME = "daily_news_editorial_stories.json"


class EditorialStoryPersistence(Protocol):
    """Narrow, source-neutral collaborator shape — mirrors
    daily_news_store.NewsStoryPersistence's own structural-typing seam."""

    def load_stories(self) -> dict[str, EditorialStory]: ...
    def upsert_new_stories(self, new_stories: list[EditorialStory]) -> dict[str, EditorialStory]: ...


def _cache_path(cache_dir: Path, filename: str = _CACHE_FILENAME) -> Path:
    return cache_dir / filename


def _story_from_dict(data: dict) -> EditorialStory:
    return EditorialStory(
        id=data["id"], headline=data["headline"], publisher=data["publisher"],
        source_url=data["source_url"], published_at=data["published_at"], retrieved_at=data["retrieved_at"],
        excerpt=data.get("excerpt"), matched_companies=tuple(data.get("matched_companies", ())),
        matched_themes=tuple(data.get("matched_themes", ())), source_feed_id=data["source_feed_id"],
    )


def load_stories(cache_dir: Path, filename: str = _CACHE_FILENAME) -> dict[str, EditorialStory]:
    path = _cache_path(cache_dir, filename)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {story_id: _story_from_dict(data) for story_id, data in raw.items()}


def save_stories(cache_dir: Path, stories: dict[str, EditorialStory], filename: str = _CACHE_FILENAME) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {story_id: asdict(story) for story_id, story in stories.items()}
    _cache_path(cache_dir, filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def upsert_new_stories(
    cache_dir: Path, new_stories: list[EditorialStory], filename: str = _CACHE_FILENAME,
) -> dict[str, EditorialStory]:
    """Adds any id not already present; leaves an existing entry
    untouched — mirrors daily_news_store.upsert_new_stories' own
    contract exactly."""
    store = load_stories(cache_dir, filename)
    changed = False
    for story in new_stories:
        if story.id not in store:
            store[story.id] = story
            changed = True
    if changed:
        save_stories(cache_dir, store, filename)
    return store
