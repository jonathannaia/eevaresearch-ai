"""Manual, one-shot import of Daily News's existing JSON-held stories
into the durable SQLite/Postgres store (Daily News durability
workstream, design/DECISIONS.md). NOT an autonomous worker and NOT
invoked automatically by anything — an operator runs this exactly once
when first pointing a deployment's EDGE_DB_BACKEND at "sqlite"/
"postgres", so stories already discovered under the JSON backend are
not silently left behind (requirement: never silently discard existing
stories). The JSON file itself is never modified or deleted by this
script — it stays fully readable/authoritative for any deployment still
on the JSON backend, or as a fallback if the import is never run.

Invoke as:
    .venv/bin/python -m scripts.import_daily_news_json_to_db

Safe to run more than once: every story's id is already deterministic
per (company, canonical URL) — see
daily_news_pipeline._story_id() — so re-running this import after some
stories already exist in the target backend just skips them (the same
idempotent upsert_new_stories contract every other repository in this
app already provides). Nothing is ever duplicated, overwritten, or
deleted, in either the source JSON file or the target backend.

Never imports anything from src.data_access.dart/edgar/edinet or
scripts/radar_worker.py. Only prints safe, already-sanitized summary
counts — never a raw exception, story content, or credential.
"""
from __future__ import annotations

from pathlib import Path

from src.config.settings import get_settings
from src.data_access.daily_news import daily_news_backend, daily_news_store


def import_json_stories(cache_dir: Path, repository) -> dict[str, int]:
    """Reads every story currently in the JSON store at `cache_dir` and
    upserts it into `repository` (a DailyNewsRepositoryProtocol
    instance) — the JSON file itself is never read-modify-written, only
    read. Returns a small, safe summary: how many stories exist in JSON,
    how many of those were already present in the target backend before
    this call (skipped, not duplicated), and how many were newly
    imported."""
    json_stories = daily_news_store.load_stories(cache_dir)
    already_in_target = set(repository.load_stories().keys())
    if json_stories:
        repository.upsert_new_stories(list(json_stories.values()))
    already_present_count = len(already_in_target & set(json_stories.keys()))
    imported_count = len(json_stories) - already_present_count
    return {
        "json_story_count": len(json_stories),
        "already_present_count": already_present_count,
        "imported_count": imported_count,
    }


def main() -> int:
    settings = get_settings()
    backend = (settings.db_backend or "json").strip().lower()
    if backend not in ("sqlite", "postgres"):
        print(
            'EDGE_DB_BACKEND is not "sqlite" or "postgres" (or is unset) — there is no '
            "durable target to import into; the JSON store is already this deployment's "
            "own backend. Exiting without changing anything."
        )
        return 0

    repository = daily_news_backend.get_daily_news_repository(settings)
    summary = import_json_stories(settings.cache_dir, repository)

    print(f"Daily News JSON import — target backend: {backend}")
    print(f"  stories in JSON:        {summary['json_story_count']}")
    print(f"  already in target:      {summary['already_present_count']}")
    print(f"  newly imported:         {summary['imported_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
