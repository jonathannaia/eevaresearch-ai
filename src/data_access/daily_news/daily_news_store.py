"""Persisted store of every NewsStory ever discovered — Daily News's own
JSON store, entirely separate from candidate_store.py's
dart_candidates.json/edgar_candidates.json/edinet_candidates.json. Same
read-modify-write, single-authoritative-copy-per-id shape as that
module, so a future SQLite/Postgres backend can be added the same way
Radar's own backend_factory.py seam was, without touching ingestion or
UI code — but no such backend exists yet this slice; this file is the
only persistence Daily News has.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

from src.models.daily_news_models import (
    NewsSourceReference,
    NewsStateTransition,
    NewsStory,
    NewsStoryStatus,
    SourceClass,
)

_CACHE_FILENAME = "daily_news_stories.json"


class NewsStoryPersistence(Protocol):
    """Narrow, source-neutral collaborator shape — mirrors
    CandidatePersistence's own structural-typing seam so a future
    backend can implement this without importing this module at all."""

    def load_stories(self) -> dict[str, NewsStory]: ...
    def upsert_new_stories(self, new_stories: list[NewsStory]) -> dict[str, NewsStory]: ...
    def update_story(self, story: NewsStory) -> object: ...


def _cache_path(cache_dir: Path, filename: str = _CACHE_FILENAME) -> Path:
    return cache_dir / filename


def _story_from_dict(data: dict) -> NewsStory:
    sources = tuple(
        NewsSourceReference(
            publisher=s["publisher"], source_class=SourceClass(s["source_class"]), url=s["url"],
            title=s["title"], published_at=s["published_at"], retrieved_at=s["retrieved_at"],
            original_language=s["original_language"], excerpt_original=s.get("excerpt_original"),
            image_url=s.get("image_url"), image_alt=s.get("image_alt"),
        )
        for s in data.get("sources", [])
    )
    history = [
        NewsStateTransition(status=NewsStoryStatus(h["status"]), at=h["at"], detail=h.get("detail", ""))
        for h in data.get("state_history", [])
    ]
    return NewsStory(
        id=data["id"], company_name=data["company_name"], ticker=data.get("ticker"),
        theme_slug=data.get("theme_slug", ""), headline=data["headline"],
        eeva_summary=data.get("eeva_summary"), is_fallback_summary=data.get("is_fallback_summary", False),
        translation_unavailable=data.get("translation_unavailable", False),
        original_title=data.get("original_title"), sources=sources,
        status=NewsStoryStatus(data["status"]), state_history=history,
    )


def load_stories(cache_dir: Path, filename: str = _CACHE_FILENAME) -> dict[str, NewsStory]:
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


def save_stories(cache_dir: Path, stories: dict[str, NewsStory], filename: str = _CACHE_FILENAME) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {story_id: asdict(story) for story_id, story in stories.items()}
    _cache_path(cache_dir, filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def upsert_new_stories(cache_dir: Path, new_stories: list[NewsStory], filename: str = _CACHE_FILENAME) -> dict[str, NewsStory]:
    """Adds any id not already present; leaves an existing entry
    untouched (matching candidate_store.upsert_new_candidates' own
    contract) — a story's id is deterministic per (company, canonical
    URL), so re-discovering the same item is a no-op here, not a
    duplicate."""
    store = load_stories(cache_dir, filename)
    changed = False
    for story in new_stories:
        if story.id not in store:
            store[story.id] = story
            changed = True
    if changed:
        save_stories(cache_dir, store, filename)
    return store


def update_story(cache_dir: Path, story: NewsStory, filename: str = _CACHE_FILENAME) -> None:
    store = load_stories(cache_dir, filename)
    store[story.id] = story
    save_stories(cache_dir, store, filename)
