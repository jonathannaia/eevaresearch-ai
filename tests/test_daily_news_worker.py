"""Daily News autonomous worker (scripts/daily_news_worker.py) —
configuration safety (live mode requires Postgres, never JSON/SQLite),
per-feed tick success/failure recording via the durable scan-status
repository, per-feed isolation (one feed's exception never stops
another's tick in the same round), the daily reconciliation health pass
(stale-fetch flagging using last_fetch_success_at only, never
last_story_published_at), the worker-level Postgres advisory lock
(contention + crash-release), and the default-off safety gate. No real
network call, no real RSS/Atom fetch — every test calls
run_one_tick()/the internal tick functions directly, never main()'s own
while-loop. daily_news_pipeline.run_discovery() and feed_registry.py are
both reused entirely unchanged by this workstream — nothing here mocks
or alters their own behavior, only the network-facing
rss_atom_client.fetch_entries() call they make internally."""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from scripts import daily_news_worker
from src.config.settings import Settings
from src.data_access.daily_news import daily_news_backend, daily_news_pipeline, rss_atom_client
from src.data_access.daily_news.feed_registry import DailyNewsFeedSource
from src.data_access.daily_news.rss_atom_client import FeedFetchResult, RawFeedEntry
from src.data_access.state_db.daily_news_scan_status_repository import (
    DailyNewsFeedScanStatus,
    DailyNewsWorkerStatus,
)

from tests._postgres_test_support import pg_isolated_dsn  # noqa: F401

_NVDA_SOURCE = DailyNewsFeedSource(
    company_name="NVIDIA", feed_url="https://nvidianews.nvidia.com/releases.xml",
    feed_format="rss", canonical_domains=("nvidianews.nvidia.com",),
    image_host="iprsoftwaremedia.com",
)
_INTEL_SOURCE = DailyNewsFeedSource(
    company_name="Intel Corp.", feed_url="https://newsroom.intel.com/feed",
    feed_format="rss", canonical_domains=("newsroom.intel.com",),
)


def _entry(title: str, link: str, summary: str | None = "A short description.") -> RawFeedEntry:
    return RawFeedEntry(title=title, link=link, published_at="2026-08-24T12:00:00+00:00", summary=summary, image_url=None, image_alt=None)


def _mock_fetch(entries_by_url: dict[str, FeedFetchResult], monkeypatch) -> None:
    def _fake_fetch_entries(feed_url: str) -> FeedFetchResult:
        return entries_by_url.get(feed_url, FeedFetchResult(entries=(), failure_code=None))

    monkeypatch.setattr(rss_atom_client, "fetch_entries", _fake_fetch_entries)
    monkeypatch.setattr(daily_news_pipeline.rss_atom_client, "fetch_entries", _fake_fetch_entries)


def _sqlite_worker_settings(tmp_path, **overrides) -> Settings:
    fields = dict(db_backend="sqlite", state_db_path=str(tmp_path / "state.db"), cache_dir=tmp_path / "cache")
    fields.update(overrides)
    return Settings(**fields)


def _ambient_settings(**overrides) -> Settings:
    fields = dict(
        daily_news_live_scan_enabled=True,
        daily_news_worker_db_backend="postgres",
        daily_news_worker_state_db_url="postgresql://example-not-actually-connected-to",
    )
    fields.update(overrides)
    return Settings(**fields)


def _second_raw_connection(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, row_factory=dict_row)


# --- _build_worker_settings: live mode requires Postgres only ---


@pytest.mark.parametrize("backend_value", [None, "json", "sqlite", "not-a-real-backend"])
def test_build_worker_settings_rejects_every_non_postgres_backend(backend_value):
    ambient = _ambient_settings(daily_news_worker_db_backend=backend_value)
    with pytest.raises(daily_news_worker.WorkerConfigurationError):
        daily_news_worker._build_worker_settings(ambient)


def test_build_worker_settings_rejects_postgres_without_url():
    ambient = _ambient_settings(daily_news_worker_db_backend="postgres", daily_news_worker_state_db_url=None)
    with pytest.raises(daily_news_worker.WorkerConfigurationError):
        daily_news_worker._build_worker_settings(ambient)


def test_build_worker_settings_accepts_postgres_and_uses_dedicated_worker_fields():
    ambient = _ambient_settings(
        daily_news_worker_db_backend="postgres", daily_news_worker_state_db_url="postgresql://real-dsn",
        state_db_url="postgresql://ambient-dashboard-dsn-must-never-be-used",
    )
    worker_settings = daily_news_worker._build_worker_settings(ambient)
    assert worker_settings.db_backend == "postgres"
    assert worker_settings.state_db_url == "postgresql://real-dsn"


# --- main(): default-off safety ---


def test_main_is_a_noop_returning_zero_when_master_switch_disabled(monkeypatch):
    monkeypatch.setattr(daily_news_worker, "get_settings", lambda: _ambient_settings(daily_news_live_scan_enabled=False))

    def _fail(*args, **kwargs):
        raise AssertionError("must never be called when the master switch is disabled")

    monkeypatch.setattr(daily_news_backend, "get_daily_news_scan_status_repository", _fail)
    monkeypatch.setattr(daily_news_worker, "run_one_tick", _fail)
    assert daily_news_worker.main() == 0


def test_main_returns_one_on_invalid_worker_backend_without_touching_anything(monkeypatch, capsys):
    monkeypatch.setattr(
        daily_news_worker, "get_settings",
        lambda: _ambient_settings(daily_news_worker_db_backend="sqlite"),
    )

    def _fail(*args, **kwargs):
        raise AssertionError("must never be called when worker configuration is invalid")

    monkeypatch.setattr(daily_news_backend, "get_daily_news_scan_status_repository", _fail)
    monkeypatch.setattr(daily_news_worker, "run_one_tick", _fail)
    assert daily_news_worker.main() == 1
    assert "postgres" in capsys.readouterr().err.lower()


# --- run_one_tick / _run_tick_body: pipeline call + status persistence (SQLite, direct tick tests) ---


def test_run_one_tick_persists_feed_and_worker_status_via_durable_repository(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("NVIDIA Announces Something", "https://nvidianews.nvidia.com/news/announces-something"),),
            failure_code=None,
        ),
    }, monkeypatch)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    feed_status = scan_status_repository.get_feed_status("NVIDIA")
    assert feed_status.items_discovered_last_run == 1
    assert feed_status.stories_published_last_run == 1
    assert feed_status.last_failure_code is None
    assert feed_status.last_fetch_success_at is not None
    assert feed_status.last_story_published_at is not None

    worker_status = scan_status_repository.get_worker_status()
    assert worker_status.last_tick_started_at is not None
    assert worker_status.last_tick_completed_at is not None
    assert worker_status.last_reconciliation_at is not None  # first-ever tick is always due

    repository = daily_news_backend.get_daily_news_repository(worker_settings)
    assert len(repository.load_stories()) == 1


def test_double_tick_with_same_feed_content_creates_no_duplicate_story(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("NVIDIA Announces Something", "https://nvidianews.nvidia.com/news/announces-something"),),
            failure_code=None,
        ),
    }, monkeypatch)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)
    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    repository = daily_news_backend.get_daily_news_repository(worker_settings)
    assert len(repository.load_stories()) == 1

    feed_status = scan_status_repository.get_feed_status("NVIDIA")
    assert feed_status.items_discovered_last_run == 1  # the same raw entry is still seen
    assert feed_status.stories_published_last_run == 0  # but nothing new was published this tick


def test_run_one_tick_does_not_rerun_reconciliation_before_interval_elapses(tmp_path, monkeypatch):
    _mock_fetch({_NVDA_SOURCE.feed_url: FeedFetchResult(entries=(), failure_code=None)}, monkeypatch)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)
    first_reconciliation_at = scan_status_repository.get_worker_status().last_reconciliation_at
    assert first_reconciliation_at is not None

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)
    second_reconciliation_at = scan_status_repository.get_worker_status().last_reconciliation_at
    assert second_reconciliation_at == first_reconciliation_at


# --- per-feed isolation ---


def test_one_failed_feed_does_not_block_others_and_records_failure_code(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(entries=(), failure_code="ConnectionError"),
        _INTEL_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("Intel Update", "https://newsroom.intel.com/news/update"),), failure_code=None,
        ),
    }, monkeypatch)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE, _INTEL_SOURCE))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    nvda_status = scan_status_repository.get_feed_status("NVIDIA")
    assert nvda_status.last_failure_code == "ConnectionError"
    assert nvda_status.stories_published_last_run == 0

    intel_status = scan_status_repository.get_feed_status("Intel Corp.")
    assert intel_status.last_failure_code is None
    assert intel_status.stories_published_last_run == 1


def test_one_feed_raising_unexpectedly_does_not_block_others(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE, _INTEL_SOURCE))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    original_run_discovery = daily_news_pipeline.run_discovery

    def _flaky_run_discovery(cache_dir, feed_sources=(), daily_news_repository=None):
        if feed_sources and feed_sources[0].company_name == "NVIDIA":
            raise ConnectionError("boom")
        return original_run_discovery(cache_dir, feed_sources=feed_sources, daily_news_repository=daily_news_repository)

    monkeypatch.setattr(daily_news_worker.daily_news_pipeline, "run_discovery", _flaky_run_discovery)
    _mock_fetch({
        _INTEL_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("Intel Update", "https://newsroom.intel.com/news/update2"),), failure_code=None,
        ),
    }, monkeypatch)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    nvda_status = scan_status_repository.get_feed_status("NVIDIA")
    assert nvda_status.last_failure_code == "ConnectionError"
    intel_status = scan_status_repository.get_feed_status("Intel Corp.")
    assert intel_status.stories_published_last_run == 1


def test_main_loop_survives_an_unexpected_tick_failure(monkeypatch):
    """A tick failure must not kill future ticks — main()'s own loop
    catches whatever run_one_tick() itself might raise (e.g. the shared
    connection dying mid-tick) and continues rather than propagating."""
    monkeypatch.setattr(daily_news_worker, "get_settings", lambda: _ambient_settings())
    monkeypatch.setattr(
        daily_news_backend, "get_daily_news_scan_status_repository", lambda settings: object(),
    )

    call_count = {"n": 0}

    def _raise_then_shutdown(worker_settings, scan_status_repository):
        call_count["n"] += 1
        daily_news_worker._shutdown_requested = True
        raise RuntimeError("simulated tick failure")

    monkeypatch.setattr(daily_news_worker, "run_one_tick", _raise_then_shutdown)
    daily_news_worker._shutdown_requested = False
    try:
        result = daily_news_worker.main()
    finally:
        daily_news_worker._shutdown_requested = False
    assert result == 0
    assert call_count["n"] == 1


# --- reconciliation: staleness detection, using last_fetch_success_at only ---


def test_reconciliation_flags_a_feed_with_no_successful_fetch_in_72_hours(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)
    stale_at = (datetime.now(timezone.utc) - timedelta(hours=73)).isoformat()
    scan_status_repository.upsert_feed_status(DailyNewsFeedScanStatus(
        company_name="NVIDIA", last_attempt_at=stale_at, last_fetch_success_at=stale_at,
        last_story_published_at=None, last_failure_code=None,
        items_discovered_last_run=0, stories_published_last_run=0, updated_at=stale_at,
    ))

    daily_news_worker._run_reconciliation_pass(worker_settings, scan_status_repository)

    output = capsys.readouterr().out
    assert "RECONCILIATION WARNING" in output
    assert "NVIDIA" in output


def test_reconciliation_does_not_flag_a_healthy_feed_that_published_nothing_new(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)
    recent = datetime.now(timezone.utc).isoformat()
    scan_status_repository.upsert_feed_status(DailyNewsFeedScanStatus(
        company_name="NVIDIA", last_attempt_at=recent, last_fetch_success_at=recent,
        last_story_published_at=None,  # fetching fine, simply nothing new — ever
        last_failure_code=None, items_discovered_last_run=0, stories_published_last_run=0, updated_at=recent,
    ))

    daily_news_worker._run_reconciliation_pass(worker_settings, scan_status_repository)

    assert "RECONCILIATION WARNING" not in capsys.readouterr().out


def test_reconciliation_due_respects_the_configured_interval(tmp_path):
    worker_settings = _sqlite_worker_settings(tmp_path, daily_news_reconciliation_interval_hours=24)

    assert daily_news_worker._reconciliation_due(None, worker_settings) is True

    recent_status = DailyNewsWorkerStatus(
        worker_key="daily_news", last_tick_started_at="x", last_tick_completed_at="x",
        last_reconciliation_at=datetime.now(timezone.utc).isoformat(), last_failure_code=None, updated_at="x",
    )
    assert daily_news_worker._reconciliation_due(recent_status, worker_settings) is False

    stale_status = DailyNewsWorkerStatus(
        worker_key="daily_news", last_tick_started_at="x", last_tick_completed_at="x",
        last_reconciliation_at=(datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
        last_failure_code=None, updated_at="x",
    )
    assert daily_news_worker._reconciliation_due(stale_status, worker_settings) is True


# --- concurrency safety: Postgres advisory lock (real container required, skips cleanly otherwise) ---


def test_run_one_tick_skips_without_scanning_or_mutating_status_when_lock_is_held(pg_isolated_dsn, monkeypatch):
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))
    worker_settings = Settings(db_backend="postgres", state_db_url=pg_isolated_dsn)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    second_conn = _second_raw_connection(pg_isolated_dsn)
    try:
        row = second_conn.execute(
            "SELECT pg_try_advisory_lock(%s) AS acquired", (daily_news_worker._DAILY_NEWS_WORKER_ADVISORY_LOCK_KEY,),
        ).fetchone()
        second_conn.commit()
        assert row["acquired"] is True

        def _fail_if_called(settings):
            raise AssertionError("repository must not be constructed when the lock is held")

        monkeypatch.setattr(daily_news_backend, "get_daily_news_repository", _fail_if_called)

        daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

        assert scan_status_repository.get_feed_status("NVIDIA") is None
        assert scan_status_repository.get_worker_status() is None
    finally:
        second_conn.execute("SELECT pg_advisory_unlock(%s)", (daily_news_worker._DAILY_NEWS_WORKER_ADVISORY_LOCK_KEY,))
        second_conn.commit()
        second_conn.close()


def test_lock_releases_after_holder_disconnects_and_a_later_tick_can_proceed(pg_isolated_dsn, monkeypatch):
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("NVIDIA Announces Something", "https://nvidianews.nvidia.com/news/announces-something-else"),),
            failure_code=None,
        ),
    }, monkeypatch)
    worker_settings = Settings(db_backend="postgres", state_db_url=pg_isolated_dsn)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    second_conn = _second_raw_connection(pg_isolated_dsn)
    row = second_conn.execute(
        "SELECT pg_try_advisory_lock(%s) AS acquired", (daily_news_worker._DAILY_NEWS_WORKER_ADVISORY_LOCK_KEY,),
    ).fetchone()
    second_conn.commit()
    assert row["acquired"] is True
    second_conn.close()  # simulated crash/process loss — Postgres releases the session-level lock

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    assert scan_status_repository.get_feed_status("NVIDIA") is not None
    assert scan_status_repository.get_worker_status() is not None


# --- structural: no public UI exposure, no unintended import direction ---


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_public_ui_never_imports_the_worker_or_its_scan_status_modules():
    forbidden_substrings = ("daily_news_worker", "daily_news_scan_status_repository")
    repo_root = Path(__file__).resolve().parent.parent
    for relative_path in ("src/ui/pages/daily_news.py", "src/ui/pages/daily_news_admin.py"):
        offenders = [
            module for module in _imported_modules(repo_root / relative_path)
            if any(forbidden in module for forbidden in forbidden_substrings)
        ]
        assert not offenders, (relative_path, offenders)


def test_daily_news_worker_module_never_imports_ui_or_streamlit():
    path = Path(__file__).resolve().parent.parent / "scripts" / "daily_news_worker.py"
    forbidden = ("src.ui", "streamlit")
    offenders = [
        module for module in _imported_modules(path)
        if any(module == f or module.startswith(f + ".") for f in forbidden)
    ]
    assert not offenders, offenders


def test_worker_uses_the_real_registered_pilot_feeds_by_default():
    from src.data_access.daily_news import feed_registry

    assert daily_news_worker.PILOT_FEEDS == feed_registry.PILOT_FEEDS
