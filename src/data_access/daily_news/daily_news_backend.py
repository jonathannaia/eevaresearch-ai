"""Daily News's own backend-selection seam (durability workstream,
design/DECISIONS.md) — the Daily-News-owned counterpart to
src.data_access.backend_factory.py's own get_X_repository() functions,
deliberately NOT added to backend_factory.py itself:
tests/test_comparison_bulk_retrieval.py::
test_backend_factory_does_not_import_ui_or_pipeline_modules already
enforces that backend_factory.py never imports anything from
src.data_access.daily_news — a real, pre-existing architectural
boundary keeping Daily News decoupled from Radar's own composition
root, not merely a naming convention. This module reproduces just
enough of backend_factory.py's own connection-acquisition pattern
(`_require_sqlite_connection`/`_require_postgres_connection`) to select
a real SQLite/Postgres connection when configured, independently
implemented — the same "no shared dialect abstraction" choice already
made between src/data_access/state_db/ and
src/data_access/postgres_state_db/ themselves.

"json" (the default, whenever `EDGE_DB_BACKEND` is unset/blank/
unrecognized) wraps daily_news_store.py exactly as-is — no new write
path, no format change, and the JSON file remains fully readable
regardless of which backend is selected elsewhere. "sqlite"/"postgres"
open (and migrate) a real connection, reusing the exact same shared
schema-migration machinery (src.data_access.state_db.schema /
src.data_access.postgres_state_db.schema) every other repository in
this app already does — that machinery is generic infrastructure, not
Radar-specific, the same way Research Case's and Themes' own
repositories already reuse it without being "Radar."
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import psycopg

from src.config.settings import Settings
from src.data_access.backend_factory import BackendConfigurationError
from src.data_access.daily_news import daily_news_store
from src.data_access.postgres_state_db import connection as postgres_state_db_connection
from src.data_access.postgres_state_db import daily_news_repository as postgres_daily_news
from src.data_access.postgres_state_db import daily_news_scan_status_repository as postgres_daily_news_scan_status
from src.data_access.postgres_state_db import schema as postgres_schema
from src.data_access.state_db import connection as state_db_connection
from src.data_access.state_db import daily_news_repository as sqlite_daily_news
from src.data_access.state_db import daily_news_scan_status_repository as sqlite_daily_news_scan_status
from src.data_access.state_db import schema as state_db_schema
from src.data_access.state_db.daily_news_scan_status_repository import DailyNewsFeedScanStatus, DailyNewsWorkerStatus
from src.models.daily_news_models import NewsStory


def _normalized_backend(settings: Settings) -> str:
    return (settings.db_backend or "json").strip().lower()


def _require_sqlite_connection(settings: Settings) -> sqlite3.Connection:
    path_value = settings.state_db_path
    if not path_value or not str(path_value).strip():
        raise BackendConfigurationError(
            "EDGE_DB_BACKEND=sqlite requires an explicit, non-empty EDGE_STATE_DB_PATH — "
            "none was configured. Refusing to silently use the JSON backend instead."
        )
    conn = state_db_connection.connect(Path(path_value))
    state_db_schema.migrate(conn)
    return conn


def _require_postgres_connection(settings: Settings) -> psycopg.Connection:
    """Same fail-closed discipline as _require_sqlite_connection above —
    see backend_factory._require_postgres_connection's own docstring for
    why a connection failure is reported with only the exception's class
    name, never str(exc)."""
    dsn = settings.state_db_url
    if not dsn or not str(dsn).strip():
        raise BackendConfigurationError(
            "EDGE_DB_BACKEND=postgres requires an explicit, non-empty EDGE_STATE_DB_URL — "
            "none was configured. Refusing to silently use the JSON backend instead."
        )
    try:
        conn = postgres_state_db_connection.connect(dsn)
    except psycopg.Error as exc:
        raise BackendConfigurationError(
            f"EDGE_DB_BACKEND=postgres connection attempt failed ({type(exc).__name__}). "
            "No connection information is included in this message."
        ) from None
    postgres_schema.migrate(conn)
    return conn


@dataclass(frozen=True)
class DailyNewsUpdateOutcome:
    status: str  # "updated" | "conflict" | "not_found" — see each adapter's own docstring
    current: NewsStory | None


class DailyNewsRepositoryProtocol(Protocol):
    def load_stories(self) -> dict[str, NewsStory]: ...
    def get_story(self, story_id: str) -> NewsStory | None: ...
    def get_story_version(self, story_id: str) -> int | None: ...
    def upsert_new_stories(self, new_stories: list[NewsStory]) -> dict[str, NewsStory]: ...
    def update_story(self, story: NewsStory, expected_version: int | None = None) -> DailyNewsUpdateOutcome: ...


@dataclass(frozen=True)
class JsonDailyNewsRepository:
    """Wraps daily_news_store.py exactly as-is — no new write path, no
    format change. update_story() always succeeds (JSON has no
    optimistic-concurrency concept, matching backend_factory.
    JsonCandidateRepository's own documented rationale for the identical
    situation); `expected_version` is accepted for Protocol compatibility
    and ignored."""

    cache_dir: Path

    def load_stories(self) -> dict[str, NewsStory]:
        return daily_news_store.load_stories(self.cache_dir)

    def get_story(self, story_id: str) -> NewsStory | None:
        return self.load_stories().get(story_id)

    def get_story_version(self, story_id: str) -> int | None:
        return None

    def upsert_new_stories(self, new_stories: list[NewsStory]) -> dict[str, NewsStory]:
        return daily_news_store.upsert_new_stories(self.cache_dir, new_stories)

    def update_story(self, story: NewsStory, expected_version: int | None = None) -> DailyNewsUpdateOutcome:
        daily_news_store.update_story(self.cache_dir, story)
        return DailyNewsUpdateOutcome(status="updated", current=story)


@dataclass(frozen=True)
class SqliteDailyNewsRepository:
    """`update_story`'s `expected_version` follows the exact same
    re-read-if-omitted convenience/no-real-conflict-protection caveat as
    backend_factory.SqliteCandidateRepository.update_candidate — see
    that class's own docstring."""

    conn: sqlite3.Connection

    def load_stories(self) -> dict[str, NewsStory]:
        return sqlite_daily_news.load_stories(self.conn)

    def get_story(self, story_id: str) -> NewsStory | None:
        return sqlite_daily_news.get_story(self.conn, story_id)

    def get_story_version(self, story_id: str) -> int | None:
        return sqlite_daily_news.get_story_version(self.conn, story_id)

    def upsert_new_stories(self, new_stories: list[NewsStory]) -> dict[str, NewsStory]:
        return sqlite_daily_news.upsert_new_stories(self.conn, new_stories)

    def update_story(self, story: NewsStory, expected_version: int | None = None) -> DailyNewsUpdateOutcome:
        if expected_version is None:
            expected_version = sqlite_daily_news.get_story_version(self.conn, story.id) or 1
        outcome = sqlite_daily_news.update_story(self.conn, story, expected_version)
        return DailyNewsUpdateOutcome(status=outcome.status, current=outcome.current)


@dataclass(frozen=True)
class PostgresDailyNewsRepository:
    conn: psycopg.Connection

    def load_stories(self) -> dict[str, NewsStory]:
        return postgres_daily_news.load_stories(self.conn)

    def get_story(self, story_id: str) -> NewsStory | None:
        return postgres_daily_news.get_story(self.conn, story_id)

    def get_story_version(self, story_id: str) -> int | None:
        return postgres_daily_news.get_story_version(self.conn, story_id)

    def upsert_new_stories(self, new_stories: list[NewsStory]) -> dict[str, NewsStory]:
        return postgres_daily_news.upsert_new_stories(self.conn, new_stories)

    def update_story(self, story: NewsStory, expected_version: int | None = None) -> DailyNewsUpdateOutcome:
        if expected_version is None:
            expected_version = postgres_daily_news.get_story_version(self.conn, story.id) or 1
        outcome = postgres_daily_news.update_story(self.conn, story, expected_version)
        return DailyNewsUpdateOutcome(status=outcome.status, current=outcome.current)


def get_daily_news_repository(settings: Settings) -> DailyNewsRepositoryProtocol:
    """Same `settings.db_backend` selection convention as backend_factory
    module's own get_X_repository functions. No real Daily News service
    entry point selects "sqlite"/"postgres" by default — "json" is what
    `settings.db_backend` resolves to unless an operator explicitly sets
    EDGE_DB_BACKEND — the standalone scripts/daily_news_worker.py is the
    one caller that constructs its own explicit worker Settings
    (db_backend="postgres" only, in live mode) before calling this."""
    backend = _normalized_backend(settings)
    if backend == "sqlite":
        return SqliteDailyNewsRepository(conn=_require_sqlite_connection(settings))
    if backend == "postgres":
        return PostgresDailyNewsRepository(conn=_require_postgres_connection(settings))
    return JsonDailyNewsRepository(cache_dir=settings.cache_dir)


# --- Daily News worker scan-status/health repository — sqlite/postgres
# only (design/DECISIONS.md, Daily News autonomous worker workstream) ---
#
# Deliberately no JSON implementation and no JSON fallback, mirroring
# backend_factory.get_scan_status_repository's own "no JSON branch"
# discipline exactly: JSON is not safe shared persistence for a separate
# dashboard + continuous-worker process pair, and no existing manual/
# on-demand Daily News entry point (scripts/run_daily_news_discovery.py,
# the hidden admin page) needs this status table at all — only
# scripts/daily_news_worker.py ever calls
# get_daily_news_scan_status_repository().


class DailyNewsScanStatusRepositoryProtocol(Protocol):
    def get_feed_status(self, company_name: str) -> DailyNewsFeedScanStatus | None: ...
    def get_all_feed_statuses(self) -> dict[str, DailyNewsFeedScanStatus]: ...
    def upsert_feed_status(self, status: DailyNewsFeedScanStatus) -> None: ...
    def get_worker_status(self) -> DailyNewsWorkerStatus | None: ...
    def upsert_worker_status(self, status: DailyNewsWorkerStatus) -> None: ...


@dataclass(frozen=True)
class SqliteDailyNewsScanStatusRepository:
    conn: sqlite3.Connection

    def get_feed_status(self, company_name: str) -> DailyNewsFeedScanStatus | None:
        return sqlite_daily_news_scan_status.get_feed_status(self.conn, company_name)

    def get_all_feed_statuses(self) -> dict[str, DailyNewsFeedScanStatus]:
        return sqlite_daily_news_scan_status.get_all_feed_statuses(self.conn)

    def upsert_feed_status(self, status: DailyNewsFeedScanStatus) -> None:
        sqlite_daily_news_scan_status.upsert_feed_status(self.conn, status)

    def get_worker_status(self) -> DailyNewsWorkerStatus | None:
        return sqlite_daily_news_scan_status.get_worker_status(self.conn)

    def upsert_worker_status(self, status: DailyNewsWorkerStatus) -> None:
        sqlite_daily_news_scan_status.upsert_worker_status(self.conn, status)


@dataclass(frozen=True)
class PostgresDailyNewsScanStatusRepository:
    conn: psycopg.Connection

    def get_feed_status(self, company_name: str) -> DailyNewsFeedScanStatus | None:
        return postgres_daily_news_scan_status.get_feed_status(self.conn, company_name)

    def get_all_feed_statuses(self) -> dict[str, DailyNewsFeedScanStatus]:
        return postgres_daily_news_scan_status.get_all_feed_statuses(self.conn)

    def upsert_feed_status(self, status: DailyNewsFeedScanStatus) -> None:
        postgres_daily_news_scan_status.upsert_feed_status(self.conn, status)

    def get_worker_status(self) -> DailyNewsWorkerStatus | None:
        return postgres_daily_news_scan_status.get_worker_status(self.conn)

    def upsert_worker_status(self, status: DailyNewsWorkerStatus) -> None:
        postgres_daily_news_scan_status.upsert_worker_status(self.conn, status)


def get_daily_news_scan_status_repository(settings: Settings) -> DailyNewsScanStatusRepositoryProtocol:
    """No JSON branch — mirrors backend_factory.get_scan_status_repository
    exactly. The caller (scripts/daily_news_worker.py) is expected to
    construct its own explicit worker Settings from its dedicated
    EDGE_DAILY_NEWS_WORKER_DB_BACKEND/EDGE_DAILY_NEWS_WORKER_STATE_DB_URL
    configuration before calling this — never the ambient
    EDGE_DB_BACKEND/EDGE_STATE_DB_URL pair the dashboard uses."""
    backend = _normalized_backend(settings)
    if backend == "sqlite":
        return SqliteDailyNewsScanStatusRepository(conn=_require_sqlite_connection(settings))
    if backend == "postgres":
        return PostgresDailyNewsScanStatusRepository(conn=_require_postgres_connection(settings))
    raise BackendConfigurationError(
        "Daily News worker scan-status persistence requires an explicit "
        f'db_backend of "sqlite" or "postgres" (got {backend!r}). JSON is not supported here — '
        "this table is only ever read/written by scripts/daily_news_worker.py."
    )
