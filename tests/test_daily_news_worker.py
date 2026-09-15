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
    # Issuer-ingestion freshness/cap policy: a fresh, execution-relative
    # published_at (never a fixed past literal), so every existing test
    # in this file that doesn't care about freshness keeps its original
    # intent unchanged now that run_discovery() has a 7x24h freshness
    # gate — mirrors test_daily_news_pipeline.py's own _entry() fixture.
    return RawFeedEntry(title=title, link=link, published_at=datetime.now(timezone.utc).isoformat(), summary=summary, image_url=None, image_alt=None)


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
    # Observability fix: this is the exact "healthy idempotent rerun"
    # case the persisted counter now makes distinguishable from a real
    # suppression bug — items_already_seen_last_run must be 1, not just
    # stories_published_last_run being 0.
    assert feed_status.items_already_seen_last_run == 1
    assert feed_status.items_deduplicated_last_run == 0
    assert feed_status.items_suppressed_no_url_last_run == 0


def test_deduplicated_items_are_persisted_in_the_feed_status(tmp_path, monkeypatch):
    """Two entries in the same tick, same normalized title, different
    links — the second is title-deduplicated against the first (which
    just published), never persisted as a separate story."""
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(
                _entry("NVIDIA Announces Something", "https://nvidianews.nvidia.com/news/announces-something"),
                _entry("NVIDIA Announces Something", "https://nvidianews.nvidia.com/news/announces-something-2"),
            ),
            failure_code=None,
        ),
    }, monkeypatch)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    feed_status = scan_status_repository.get_feed_status("NVIDIA")
    assert feed_status.items_discovered_last_run == 2
    assert feed_status.stories_published_last_run == 1
    assert feed_status.items_already_seen_last_run == 0
    assert feed_status.items_deduplicated_last_run == 1
    assert feed_status.items_suppressed_no_url_last_run == 0

    repository = daily_news_backend.get_daily_news_repository(worker_settings)
    assert len(repository.load_stories()) == 1


def test_items_suppressed_for_no_valid_canonical_url_are_persisted_in_the_feed_status(tmp_path, monkeypatch):
    """A bare-homepage link (no path segment) fails canonical_url.
    validate_canonical_url() — suppressed, never published."""
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("NVIDIA Announces Something", "https://nvidianews.nvidia.com"),),
            failure_code=None,
        ),
    }, monkeypatch)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    feed_status = scan_status_repository.get_feed_status("NVIDIA")
    assert feed_status.items_discovered_last_run == 1
    assert feed_status.stories_published_last_run == 0
    assert feed_status.items_already_seen_last_run == 0
    assert feed_status.items_deduplicated_last_run == 0
    assert feed_status.items_suppressed_no_url_last_run == 1

    repository = daily_news_backend.get_daily_news_repository(worker_settings)
    assert len(repository.load_stories()) == 0


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
    # An exception means no DailyNewsScanReport was ever produced for this
    # tick — the three new counters must reset to 0, same convention as
    # items_discovered_last_run/stories_published_last_run.
    assert nvda_status.items_already_seen_last_run == 0
    assert nvda_status.items_deduplicated_last_run == 0
    assert nvda_status.items_suppressed_no_url_last_run == 0
    intel_status = scan_status_repository.get_feed_status("Intel Corp.")
    assert intel_status.stories_published_last_run == 1


# --- Worker observability: source_id in per-feed log lines (design/DECISIONS.md) ---

_META_IR_SOURCE = DailyNewsFeedSource(
    company_name="Meta Platforms, Inc.", feed_url="https://investor.atmeta.com/rss/pressrelease.aspx",
    feed_format="rss", canonical_domains=("investor.atmeta.com",), source_id="meta-ir-rss",
)
_META_NEWSROOM_SOURCE = DailyNewsFeedSource(
    company_name="Meta Platforms, Inc.", feed_url="https://about.fb.com/feed/",
    feed_format="rss", canonical_domains=("about.fb.com",), source_id="meta-newsroom-rss",
)


def test_successful_tick_log_line_includes_source_id_and_every_counter(tmp_path, monkeypatch, capsys):
    _mock_fetch({
        _META_NEWSROOM_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("Meta Update", "https://about.fb.com/news/update"),), failure_code=None,
        ),
    }, monkeypatch)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_META_NEWSROOM_SOURCE,))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    out = capsys.readouterr().out
    assert "meta-newsroom-rss" in out
    assert "Meta Platforms, Inc." in out
    assert "items_discovered=1" in out
    assert "stories_published=1" in out
    assert "items_already_seen=0" in out
    assert "items_deduplicated=0" in out
    assert "items_suppressed_no_url=0" in out


def test_fetch_failed_log_line_includes_source_id_company_and_failure_code(tmp_path, monkeypatch, capsys):
    _mock_fetch({
        _META_IR_SOURCE.feed_url: FeedFetchResult(entries=(), failure_code="HTTPError:403"),
    }, monkeypatch)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_META_IR_SOURCE,))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    out = capsys.readouterr().out
    assert "meta-ir-rss" in out
    assert "Meta Platforms, Inc." in out
    assert "HTTPError:403" in out


def test_tick_failed_log_line_includes_source_id_and_company(tmp_path, monkeypatch, capsys):
    def _raise(cache_dir, feed_sources=(), daily_news_repository=None):
        raise ConnectionError("boom")

    monkeypatch.setattr(daily_news_worker.daily_news_pipeline, "run_discovery", _raise)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_META_IR_SOURCE,))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    out = capsys.readouterr().out
    assert "meta-ir-rss" in out
    assert "Meta Platforms, Inc." in out
    assert "ConnectionError" in out


def test_two_feeds_sharing_a_company_name_are_distinguishable_by_source_id_in_logs(tmp_path, monkeypatch, capsys):
    """The exact real-world case this workstream exists to fix: two
    registered sources (meta-ir-rss, blocked; meta-newsroom-rss, healthy)
    share the identical company_name "Meta Platforms, Inc." — before this
    change their log lines were textually identical; now each line names
    its own source_id, so the two are unambiguous even though the
    persisted DailyNewsFeedScanStatus is still keyed by company_name only
    (unchanged — see the second assertion block)."""
    _mock_fetch({
        _META_IR_SOURCE.feed_url: FeedFetchResult(entries=(), failure_code="HTTPError:403"),
        _META_NEWSROOM_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("Meta Update", "https://about.fb.com/news/update"),), failure_code=None,
        ),
    }, monkeypatch)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_META_IR_SOURCE, _META_NEWSROOM_SOURCE))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    lines = [line for line in capsys.readouterr().out.splitlines() if "Meta Platforms, Inc." in line]
    assert len(lines) == 2
    ir_lines = [line for line in lines if line.startswith("meta-ir-rss ")]
    newsroom_lines = [line for line in lines if line.startswith("meta-newsroom-rss ")]
    assert len(ir_lines) == 1 and "HTTPError:403" in ir_lines[0]
    assert len(newsroom_lines) == 1 and "ok — items_discovered=1" in newsroom_lines[0]

    # Persistence keying is unchanged by this observability-only batch —
    # both sources still collapse onto the one company_name-keyed status
    # row, last write wins (PILOT_FEEDS order: meta-ir-rss then
    # meta-newsroom-rss), same pre-existing behavior as any other
    # same-company-name pair.
    status = scan_status_repository.get_feed_status("Meta Platforms, Inc.")
    assert status is not None


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


# --- Daily News worker observability, Part A ---


def test_tick_completion_log_line_includes_duration(tmp_path, monkeypatch, capsys):
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

    lines = [line for line in capsys.readouterr().out.splitlines() if "tick completed in" in line]
    assert len(lines) == 1
    assert "s (started" in lines[0] and "completed" in lines[0]


def test_source_status_written_on_successful_fetch(tmp_path, monkeypatch):
    _mock_fetch({
        _META_IR_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("Meta Update", "https://investor.atmeta.com/news/update"),),
            failure_code=None, duration_ms=145.2, http_status=200,
        ),
    }, monkeypatch)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_META_IR_SOURCE,))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    source_status = scan_status_repository.get_source_status("meta-ir-rss")
    assert source_status is not None
    assert source_status.company_name == "Meta Platforms, Inc."
    assert source_status.last_http_status == 200
    assert source_status.last_request_duration_ms == 145.2
    assert source_status.last_failure_code is None
    assert source_status.last_fetch_success_at is not None
    assert source_status.last_story_published_at is not None
    assert source_status.items_discovered_last_run == 1
    assert source_status.stories_published_last_run == 1
    assert source_status.last_result_at is not None


def test_source_status_written_on_http_failure(tmp_path, monkeypatch):
    _mock_fetch({
        _META_IR_SOURCE.feed_url: FeedFetchResult(entries=(), failure_code="HTTPError:403", duration_ms=88.0, http_status=403),
    }, monkeypatch)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_META_IR_SOURCE,))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    source_status = scan_status_repository.get_source_status("meta-ir-rss")
    assert source_status.last_failure_code == "HTTPError:403"
    assert source_status.last_http_status == 403
    assert source_status.last_request_duration_ms == 88.0
    assert source_status.last_fetch_success_at is None
    assert source_status.last_story_published_at is None


def test_source_status_written_on_unexpected_exception(tmp_path, monkeypatch):
    def _raise(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(daily_news_pipeline, "run_discovery", _raise)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_META_IR_SOURCE,))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    source_status = scan_status_repository.get_source_status("meta-ir-rss")
    assert source_status is not None
    assert source_status.last_failure_code == "RuntimeError"
    assert source_status.last_http_status is None
    assert source_status.last_request_duration_ms is None
    assert source_status.items_discovered_last_run == 0


def test_two_sources_sharing_a_company_get_independent_source_status_rows(tmp_path, monkeypatch):
    """The entire point of this table: meta-ir-rss (blocked) and
    meta-newsroom-rss (healthy) must never collide, unlike the legacy
    company_name-keyed daily_news_scan_status row they both still also
    write to (last-write-wins there, unchanged)."""
    _mock_fetch({
        _META_IR_SOURCE.feed_url: FeedFetchResult(entries=(), failure_code="HTTPError:403", http_status=403),
        _META_NEWSROOM_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("Meta Update", "https://about.fb.com/news/update"),), failure_code=None, http_status=200,
        ),
    }, monkeypatch)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_META_IR_SOURCE, _META_NEWSROOM_SOURCE))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    ir_status = scan_status_repository.get_source_status("meta-ir-rss")
    newsroom_status = scan_status_repository.get_source_status("meta-newsroom-rss")
    assert ir_status.last_http_status == 403
    assert newsroom_status.last_http_status == 200
    assert newsroom_status.stories_published_last_run == 1
    assert ir_status.stories_published_last_run == 0

    all_source_statuses = scan_status_repository.get_all_source_statuses()
    assert set(all_source_statuses) == {"meta-ir-rss", "meta-newsroom-rss"}


def test_source_with_no_source_id_never_gets_a_source_status_row(tmp_path, monkeypatch):
    """A feed_registry entry with an empty source_id (the pre-existing
    default, still valid) must not produce a spurious row keyed by the
    sentinel "(no source_id)" label used only for log lines."""
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("NVIDIA Announces Something", "https://nvidianews.nvidia.com/news/announces-something"),),
            failure_code=None,
        ),
    }, monkeypatch)
    assert _NVDA_SOURCE.source_id == ""
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    assert scan_status_repository.get_all_source_statuses() == {}
    # The legacy company-keyed table is completely unaffected either way.
    assert scan_status_repository.get_feed_status("NVIDIA") is not None


# ============================================================
# Editorial Daily News autonomy (design/DECISIONS.md) — the worker now
# runs editorial_pipeline.run_editorial_discovery() once, second, inside
# the same acquired-lock tick, after every issuer feed above. These
# tests exercise the REAL EDITORIAL_SOURCE_REGISTRY (unmodified by this
# workstream) via its own real canonical_url values, mocked through the
# same rss_atom_client.fetch_entries seam _mock_fetch() already patches
# — any real editorial source not explicitly given an entry in the mock
# dict simply returns zero entries (no failure), exactly like every
# other unmocked feed in this file's existing tests.
# ============================================================

from src.data_access.daily_news import editorial_pipeline
from src.data_access.daily_news.source_registry import EDITORIAL_SOURCE_REGISTRY

_CNBC_TOP_NEWS_URL = "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"
_KOREA_HERALD_URL = "https://www.koreaherald.com/rss/kh_Business"
_SPACEFORCE_URL = "https://www.spaceforce.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=1060&max=10"
_NIST_URL = "https://www.nist.gov/news-events/news/rss.xml"


def _editorial_entry(title: str, link: str, summary: str | None = None) -> RawFeedEntry:
    return RawFeedEntry(
        title=title, link=link, published_at=datetime.now(timezone.utc).isoformat(),
        summary=summary, image_url=None, image_alt=None,
    )


# --- 1/3/4: exactly-once, issuer-first ordering, correctly-scoped repository ---


def test_editorial_discovery_runs_exactly_once_per_acquired_tick(tmp_path, monkeypatch):
    _mock_fetch({_NVDA_SOURCE.feed_url: FeedFetchResult(entries=(), failure_code=None)}, monkeypatch)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))
    call_count = 0
    real_run_editorial_discovery = editorial_pipeline.run_editorial_discovery

    def _spy(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return real_run_editorial_discovery(*args, **kwargs)

    monkeypatch.setattr(daily_news_worker.editorial_pipeline, "run_editorial_discovery", _spy)
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    assert call_count == 1


def test_issuer_feed_is_fetched_before_any_editorial_source(tmp_path, monkeypatch):
    call_order: list[str] = []

    def _fake_fetch_entries(feed_url: str) -> FeedFetchResult:
        call_order.append(feed_url)
        return FeedFetchResult(entries=(), failure_code=None)

    monkeypatch.setattr(rss_atom_client, "fetch_entries", _fake_fetch_entries)
    monkeypatch.setattr(daily_news_pipeline.rss_atom_client, "fetch_entries", _fake_fetch_entries)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    assert _NVDA_SOURCE.feed_url in call_order
    issuer_index = call_order.index(_NVDA_SOURCE.feed_url)
    editorial_urls_seen = [url for url in call_order if url != _NVDA_SOURCE.feed_url]
    assert editorial_urls_seen, "expected at least one real editorial source URL to be attempted"
    assert issuer_index < min(call_order.index(url) for url in editorial_urls_seen)


def test_editorial_repository_receives_the_exact_worker_scoped_settings(tmp_path, monkeypatch):
    _mock_fetch({_NVDA_SOURCE.feed_url: FeedFetchResult(entries=(), failure_code=None)}, monkeypatch)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))
    received_settings: list[Settings] = []
    real_get_editorial_story_repository = daily_news_backend.get_editorial_story_repository

    def _spy(settings):
        received_settings.append(settings)
        return real_get_editorial_story_repository(settings)

    monkeypatch.setattr(daily_news_backend, "get_editorial_story_repository", _spy)
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    assert len(received_settings) == 1
    assert received_settings[0] is worker_settings


# --- 5: zero-result editorial run leaves issuer/tick behavior normal ---


def test_zero_result_editorial_run_leaves_issuer_status_and_tick_completion_normal(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("NVIDIA Announces Something", "https://nvidianews.nvidia.com/news/announces-something"),),
            failure_code=None,
        ),
    }, monkeypatch)  # every real editorial source URL is left unmocked -> zero entries, zero failures
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    feed_status = scan_status_repository.get_feed_status("NVIDIA")
    assert feed_status.stories_published_last_run == 1
    assert feed_status.last_failure_code is None
    worker_status = scan_status_repository.get_worker_status()
    assert worker_status.last_tick_started_at is not None
    assert worker_status.last_tick_completed_at is not None


# --- 6: unexpected whole-pipeline exception -> explicit degraded FAILED log, no propagation ---


def test_unexpected_editorial_exception_logs_degraded_failed_line_and_does_not_propagate(tmp_path, monkeypatch, capsys):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("NVIDIA Announces Something", "https://nvidianews.nvidia.com/news/announces-something"),),
            failure_code=None,
        ),
    }, monkeypatch)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))

    def _boom(*args, **kwargs):
        raise RuntimeError("connection boom - must never propagate")

    monkeypatch.setattr(daily_news_worker.editorial_pipeline, "run_editorial_discovery", _boom)
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)  # must not raise

    output = capsys.readouterr().out
    assert (
        "Daily News worker: editorial discovery FAILED unexpectedly — RuntimeError; "
        "issuer coverage completed, editorial coverage degraded for this tick."
    ) in output
    assert "connection boom" not in output

    # Issuer work already completed above the try/except is unaffected.
    feed_status = scan_status_repository.get_feed_status("NVIDIA")
    assert feed_status.stories_published_last_run == 1
    # Reconciliation and the worker-status/tick-completion write still ran.
    worker_status = scan_status_repository.get_worker_status()
    assert worker_status.last_tick_completed_at is not None
    assert worker_status.last_reconciliation_at is not None


# --- 7: per-source editorial failures -> aggregate "completed with source failures" ---


def test_one_editorial_source_failure_reported_with_correct_aggregate_and_does_not_block_others(
    tmp_path, monkeypatch, capsys,
):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(entries=(), failure_code=None),
        _CNBC_TOP_NEWS_URL: FeedFetchResult(entries=(), failure_code="HTTPError:503"),
        _SPACEFORCE_URL: FeedFetchResult(
            entries=(_editorial_entry(
                "US Space Force selects Texas as preferred location for third DARC site",
                "https://www.spaceforce.mil/News/Article-Display/Article/4592096/darc-texas/",
            ),), failure_code=None,
        ),
    }, monkeypatch)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    output = capsys.readouterr().out
    assert "Daily News worker: editorial discovery completed with source failures" in output
    assert f"sources_polled={len(EDITORIAL_SOURCE_REGISTRY)}" in output
    assert "source_failures=1" in output
    assert "Daily News worker: editorial source failed — cnbc-top-news-rss (CNBC): HTTPError:503" in output
    # The Space Force item still published despite CNBC's failure.
    editorial_repository = daily_news_backend.get_editorial_story_repository(worker_settings)
    stories = editorial_repository.load_stories()
    assert any(s.source_feed_id == "spaceforce-news-rss" for s in stories.values())


def test_zero_editorial_source_failures_reported_as_plain_completed(tmp_path, monkeypatch, capsys):
    _mock_fetch({_NVDA_SOURCE.feed_url: FeedFetchResult(entries=(), failure_code=None)}, monkeypatch)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    output = capsys.readouterr().out
    assert "Daily News worker: editorial discovery completed —" in output
    assert "completed with source failures" not in output
    assert "source_failures=0" in output


# --- 8: existing editorial eligibility rules unchanged through the worker ---


def test_cnbc_item_with_no_company_or_theme_match_is_not_published_through_the_worker(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(entries=(), failure_code=None),
        _CNBC_TOP_NEWS_URL: FeedFetchResult(
            entries=(_editorial_entry("Record U.S. cyclosporiasis outbreak is over, CDC says", "https://www.cnbc.com/off-topic"),),
            failure_code=None,
        ),
    }, monkeypatch)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    editorial_repository = daily_news_backend.get_editorial_story_repository(worker_settings)
    assert editorial_repository.load_stories() == {}


def test_spaceforce_item_with_no_match_still_publishes_through_the_worker(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(entries=(), failure_code=None),
        _SPACEFORCE_URL: FeedFetchResult(
            entries=(_editorial_entry(
                "US Space Force selects Texas as preferred location for third DARC site",
                "https://www.spaceforce.mil/News/Article-Display/Article/4592096/darc-texas/",
            ),), failure_code=None,
        ),
    }, monkeypatch)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    editorial_repository = daily_news_backend.get_editorial_story_repository(worker_settings)
    stories = editorial_repository.load_stories()
    assert len(stories) == 1
    story = next(iter(stories.values()))
    assert story.matched_companies == ()
    assert story.source_feed_id == "spaceforce-news-rss"


def test_nist_off_topic_item_is_not_published_and_on_topic_item_is_through_the_worker(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(entries=(), failure_code=None),
        _NIST_URL: FeedFetchResult(
            entries=(
                _editorial_entry(
                    "NIST-Developed Quantum Sensors Improve Nuclear Monitoring",
                    "https://www.nist.gov/news-events/news/2026/09/quantum-sensors",
                ),
                _editorial_entry(
                    "NIST Awards Funding to Advance Domestic Semiconductor Manufacturing",
                    "https://www.nist.gov/news-events/news/2026/09/chips-funding",
                    summary="The award, made under the CHIPS Act, supports new semiconductor fabrication capacity.",
                ),
            ),
            failure_code=None,
        ),
    }, monkeypatch)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    editorial_repository = daily_news_backend.get_editorial_story_repository(worker_settings)
    stories = editorial_repository.load_stories()
    assert len(stories) == 1
    story = next(iter(stories.values()))
    assert story.headline == "NIST Awards Funding to Advance Domestic Semiconductor Manufacturing"
    assert story.source_feed_id == "nist-news-rss"


def test_korea_herald_matching_item_still_publishes_through_the_worker(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(entries=(), failure_code=None),
        _KOREA_HERALD_URL: FeedFetchResult(
            entries=(_editorial_entry(
                "Samsung Electronics posts strong memory chip demand", "https://www.koreaherald.com/article/1",
            ),), failure_code=None,
        ),
    }, monkeypatch)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    editorial_repository = daily_news_backend.get_editorial_story_repository(worker_settings)
    stories = editorial_repository.load_stories()
    assert len(stories) == 1
    assert next(iter(stories.values())).source_feed_id == "korea-herald-business-rss"


# --- 9: repeated identical editorial entries do not duplicate persisted stories ---


def test_repeated_ticks_with_the_same_editorial_item_do_not_duplicate(tmp_path, monkeypatch):
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(entries=(), failure_code=None),
        _SPACEFORCE_URL: FeedFetchResult(
            entries=(_editorial_entry(
                "US Space Force selects Texas as preferred location for third DARC site",
                "https://www.spaceforce.mil/News/Article-Display/Article/4592096/darc-texas/",
            ),), failure_code=None,
        ),
    }, monkeypatch)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)
    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    editorial_repository = daily_news_backend.get_editorial_story_repository(worker_settings)
    assert len(editorial_repository.load_stories()) == 1


# --- 2/11: Postgres — no editorial work at all on lock miss; real Postgres repository used ---


def test_no_editorial_pipeline_or_repository_work_occurs_on_lock_miss(pg_isolated_dsn, monkeypatch):
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

        def _fail_if_called(*args, **kwargs):
            raise AssertionError("editorial pipeline/repository must not be touched when the lock is held")

        monkeypatch.setattr(daily_news_worker.editorial_pipeline, "run_editorial_discovery", _fail_if_called)
        monkeypatch.setattr(daily_news_backend, "get_editorial_story_repository", _fail_if_called)
        monkeypatch.setattr(daily_news_backend, "get_daily_news_repository", _fail_if_called)

        daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

        assert scan_status_repository.get_feed_status("NVIDIA") is None
        assert scan_status_repository.get_worker_status() is None
    finally:
        second_conn.execute("SELECT pg_advisory_unlock(%s)", (daily_news_worker._DAILY_NEWS_WORKER_ADVISORY_LOCK_KEY,))
        second_conn.commit()
        second_conn.close()


def test_worker_mode_editorial_repository_is_real_postgres_not_local_json_fallback(pg_isolated_dsn, monkeypatch):
    from src.data_access.daily_news.daily_news_backend import PostgresEditorialStoryRepository

    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))
    _mock_fetch({
        _NVDA_SOURCE.feed_url: FeedFetchResult(entries=(), failure_code=None),
        _SPACEFORCE_URL: FeedFetchResult(
            entries=(_editorial_entry(
                "US Space Force selects Texas as preferred location for third DARC site",
                "https://www.spaceforce.mil/News/Article-Display/Article/4592096/darc-texas/",
            ),), failure_code=None,
        ),
    }, monkeypatch)
    worker_settings = Settings(db_backend="postgres", state_db_url=pg_isolated_dsn)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    constructed_repositories = []
    real_get_editorial_story_repository = daily_news_backend.get_editorial_story_repository

    def _spy(settings):
        repo = real_get_editorial_story_repository(settings)
        constructed_repositories.append(repo)
        return repo

    monkeypatch.setattr(daily_news_backend, "get_editorial_story_repository", _spy)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    assert len(constructed_repositories) == 1
    assert isinstance(constructed_repositories[0], PostgresEditorialStoryRepository)
    stories = constructed_repositories[0].load_stories()
    assert any(s.source_feed_id == "spaceforce-news-rss" for s in stories.values())


# ============================================================
# Daily News source-expansion batch 4 (2026-09-13) — Samsung Electronics,
# Murata Manufacturing, Microchip Technology. scripts/daily_news_worker.py
# itself is completely unmodified by this batch: it already iterates
# feed_registry.PILOT_FEEDS generically, so the three new real registry
# entries participate in the normal worker tick without any worker code
# change. These tests prove that participation directly, using the real
# PILOT_FEEDS entries (not synthetic ones), and that per-source failure
# isolation still applies to them exactly as it does to every existing
# issuer source.
# ============================================================

from src.data_access.daily_news.feed_registry import PILOT_FEEDS as _REAL_PILOT_FEEDS

_SAMSUNG_SOURCE = next(f for f in _REAL_PILOT_FEEDS if f.company_name == "Samsung Electronics")
_MURATA_SOURCE = next(f for f in _REAL_PILOT_FEEDS if f.company_name == "Murata Manufacturing Co., Ltd.")
_MICROCHIP_SOURCE = next(f for f in _REAL_PILOT_FEEDS if f.company_name == "Microchip Technology Incorporated")


def test_samsung_murata_microchip_participate_in_the_normal_issuer_loop_with_failure_isolation(
    tmp_path, monkeypatch,
):
    article_url = "https://www.murata.com/en-global/news/emc/emifil/2026/0910"
    _mock_fetch({
        _SAMSUNG_SOURCE.feed_url: FeedFetchResult(entries=(), failure_code="HTTPError:503"),
        _MURATA_SOURCE.feed_url: FeedFetchResult(
            entries=(_entry("Murata Launches Common Mode Choke Coils", article_url),), failure_code=None,
        ),
        # Microchip left unmocked -> zero entries, zero failure (same _mock_fetch default every other test uses).
    }, monkeypatch)
    monkeypatch.setattr(
        daily_news_worker, "PILOT_FEEDS", (_SAMSUNG_SOURCE, _MURATA_SOURCE, _MICROCHIP_SOURCE),
    )
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    samsung_status = scan_status_repository.get_feed_status("Samsung Electronics")
    assert samsung_status.last_failure_code == "HTTPError:503"
    assert samsung_status.stories_published_last_run == 0

    murata_status = scan_status_repository.get_feed_status("Murata Manufacturing Co., Ltd.")
    assert murata_status.last_failure_code is None
    assert murata_status.stories_published_last_run == 1

    microchip_status = scan_status_repository.get_feed_status("Microchip Technology Incorporated")
    assert microchip_status.last_failure_code is None
    assert microchip_status.stories_published_last_run == 0

    # Samsung's failure never blocked Murata's or Microchip's own attempt.
    worker_status = scan_status_repository.get_worker_status()
    assert worker_status.last_tick_completed_at is not None


def test_issuer_source_additions_do_not_change_the_registry_derived_editorial_aggregate(tmp_path, monkeypatch, capsys):
    # Proves the editorial aggregate's sources_polled is derived from
    # EDITORIAL_SOURCE_REGISTRY alone (unchanged by this batch), not from
    # PILOT_FEEDS/RUNTIME_SOURCE_REGISTRY (which grew by 3 in this batch).
    from src.data_access.daily_news.source_registry import EDITORIAL_SOURCE_REGISTRY

    _mock_fetch({}, monkeypatch)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_SAMSUNG_SOURCE, _MURATA_SOURCE, _MICROCHIP_SOURCE))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    output = capsys.readouterr().out
    assert f"sources_polled={len(EDITORIAL_SOURCE_REGISTRY)}" in output

# ============================================================
# Gated market-news source expansion (design/DECISIONS.md) — the
# EDGE_DAILY_NEWS_ENABLED_SOURCES allow-list, combined at the worker's
# own editorial-tick call site with EDITORIAL_SOURCE_REGISTRY's own
# always-on 36 sources. See src/data_access/daily_news/
# market_news_sources.py's own docstring for the full design.
# ============================================================

from src.data_access.daily_news.source_registry import GATED_MARKET_NEWS_SOURCE_REGISTRY


def test_gated_market_news_source_dormant_by_default(tmp_path, monkeypatch, capsys):
    """Default (empty) allow-list: sources_polled stays exactly
    len(EDITORIAL_SOURCE_REGISTRY) — no gated source is ever added, and
    no network call to the gated source's own URL occurs."""
    call_urls: list[str] = []

    def _fake_fetch_entries(feed_url: str) -> FeedFetchResult:
        call_urls.append(feed_url)
        return FeedFetchResult(entries=(), failure_code=None)

    monkeypatch.setattr(rss_atom_client, "fetch_entries", _fake_fetch_entries)
    monkeypatch.setattr(daily_news_pipeline.rss_atom_client, "fetch_entries", _fake_fetch_entries)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    output = capsys.readouterr().out
    assert f"sources_polled={len(EDITORIAL_SOURCE_REGISTRY)}" in output
    gated_urls = {e.canonical_url for e in GATED_MARKET_NEWS_SOURCE_REGISTRY}
    assert not (gated_urls & set(call_urls))


def test_gated_market_news_source_included_when_explicitly_allow_listed(tmp_path, monkeypatch, capsys):
    """With the gated source's own source_id present in the allow-list,
    sources_polled grows by exactly the number of gated sources enabled,
    and a real fetch attempt is made against its own canonical_url."""
    call_urls: list[str] = []

    def _fake_fetch_entries(feed_url: str) -> FeedFetchResult:
        call_urls.append(feed_url)
        return FeedFetchResult(entries=(), failure_code=None)

    monkeypatch.setattr(rss_atom_client, "fetch_entries", _fake_fetch_entries)
    monkeypatch.setattr(daily_news_pipeline.rss_atom_client, "fetch_entries", _fake_fetch_entries)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))
    worker_settings = _sqlite_worker_settings(
        tmp_path, daily_news_enabled_market_news_sources=frozenset({"light-reading-rss"}),
    )
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    output = capsys.readouterr().out
    assert f"sources_polled={len(EDITORIAL_SOURCE_REGISTRY) + 1}" in output
    light_reading = next(e for e in GATED_MARKET_NEWS_SOURCE_REGISTRY if e.source_id == "light-reading-rss")
    assert light_reading.canonical_url in call_urls


def test_gated_market_news_allow_list_does_not_affect_unlisted_gated_sources(tmp_path, monkeypatch, capsys):
    """An allow-list naming an unknown/unrelated source_id enables
    nothing — never falls back to "enable everything"."""
    _mock_fetch({}, monkeypatch)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))
    worker_settings = _sqlite_worker_settings(
        tmp_path, daily_news_enabled_market_news_sources=frozenset({"some-other-source-not-in-the-gated-registry"}),
    )
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    output = capsys.readouterr().out
    assert f"sources_polled={len(EDITORIAL_SOURCE_REGISTRY)}" in output


# ============================================================
# Gated Japan/Korea source expansion (design/DECISIONS.md) — the
# EDGE_DAILY_NEWS_ENABLED_SOURCES_JP_KR allow-list, combined at the
# worker's own editorial-tick call site alongside EDITORIAL_SOURCE_
# REGISTRY and the (separate) gated market-news allow-list. See
# src/data_access/daily_news/jp_kr_sources.py's own docstring for the
# full design.
# ============================================================

from src.data_access.daily_news.source_registry import GATED_JP_KR_SOURCE_REGISTRY


def test_gated_jp_kr_source_dormant_by_default(tmp_path, monkeypatch, capsys):
    """Default (empty) allow-list: sources_polled stays exactly
    len(EDITORIAL_SOURCE_REGISTRY) — no gated JP/KR source is ever
    added, and no network call to any of their URLs occurs."""
    call_urls: list[str] = []

    def _fake_fetch_entries(feed_url: str) -> FeedFetchResult:
        call_urls.append(feed_url)
        return FeedFetchResult(entries=(), failure_code=None)

    monkeypatch.setattr(rss_atom_client, "fetch_entries", _fake_fetch_entries)
    monkeypatch.setattr(daily_news_pipeline.rss_atom_client, "fetch_entries", _fake_fetch_entries)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))
    worker_settings = _sqlite_worker_settings(tmp_path)
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    output = capsys.readouterr().out
    assert f"sources_polled={len(EDITORIAL_SOURCE_REGISTRY)}" in output
    gated_urls = {e.canonical_url for e in GATED_JP_KR_SOURCE_REGISTRY}
    assert not (gated_urls & set(call_urls))


def test_gated_jp_kr_sources_included_when_explicitly_allow_listed(tmp_path, monkeypatch, capsys):
    """With both gated JP/KR source_ids present in the allow-list,
    sources_polled grows by exactly 2, and real fetch attempts are made
    against both canonical_urls."""
    call_urls: list[str] = []

    def _fake_fetch_entries(feed_url: str) -> FeedFetchResult:
        call_urls.append(feed_url)
        return FeedFetchResult(entries=(), failure_code=None)

    monkeypatch.setattr(rss_atom_client, "fetch_entries", _fake_fetch_entries)
    monkeypatch.setattr(daily_news_pipeline.rss_atom_client, "fetch_entries", _fake_fetch_entries)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))
    worker_settings = _sqlite_worker_settings(
        tmp_path,
        daily_news_enabled_jp_kr_sources=frozenset({"businesskorea-industries-rss", "businesskorea-science-tech-rss"}),
    )
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    output = capsys.readouterr().out
    assert f"sources_polled={len(EDITORIAL_SOURCE_REGISTRY) + 2}" in output
    industries = next(e for e in GATED_JP_KR_SOURCE_REGISTRY if e.source_id == "businesskorea-industries-rss")
    sci_tech = next(e for e in GATED_JP_KR_SOURCE_REGISTRY if e.source_id == "businesskorea-science-tech-rss")
    assert industries.canonical_url in call_urls
    assert sci_tech.canonical_url in call_urls


def test_gated_jp_kr_allow_list_is_independent_of_the_gated_market_news_allow_list(tmp_path, monkeypatch, capsys):
    """Enabling the market-news gated source must never also enable a
    JP/KR gated source, and vice versa — proves the two allow-lists are
    wired independently at the worker's own call site, not merged."""
    _mock_fetch({}, monkeypatch)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))
    worker_settings = _sqlite_worker_settings(
        tmp_path,
        daily_news_enabled_market_news_sources=frozenset({"light-reading-rss"}),
        daily_news_enabled_jp_kr_sources=frozenset(),
    )
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    output = capsys.readouterr().out
    # +1 for light-reading-rss only, never +1 for any JP/KR source too.
    assert f"sources_polled={len(EDITORIAL_SOURCE_REGISTRY) + 1}" in output


def test_kr_and_light_reading_rollout_combination_enables_exactly_the_three_intended_sources(
    tmp_path, monkeypatch, capsys,
):
    """The exact combination approved for the KR + Light Reading rollout
    (design/DECISIONS.md) — EDGE_DAILY_NEWS_ENABLED_SOURCES=light-
    reading-rss plus EDGE_DAILY_NEWS_ENABLED_SOURCES_JP_KR=businesskorea-
    industries-rss,businesskorea-science-tech-rss set simultaneously on
    the real worker. sources_polled must grow by exactly 3 (not 2, not
    4) and a real fetch attempt must reach all three canonical_urls and
    no others — proves the two independent allow-lists compose cleanly
    together, not just in isolation."""
    call_urls: list[str] = []

    def _fake_fetch_entries(feed_url: str) -> FeedFetchResult:
        call_urls.append(feed_url)
        return FeedFetchResult(entries=(), failure_code=None)

    monkeypatch.setattr(rss_atom_client, "fetch_entries", _fake_fetch_entries)
    monkeypatch.setattr(daily_news_pipeline.rss_atom_client, "fetch_entries", _fake_fetch_entries)
    monkeypatch.setattr(daily_news_worker, "PILOT_FEEDS", (_NVDA_SOURCE,))
    worker_settings = _sqlite_worker_settings(
        tmp_path,
        daily_news_enabled_market_news_sources=frozenset({"light-reading-rss"}),
        daily_news_enabled_jp_kr_sources=frozenset({"businesskorea-industries-rss", "businesskorea-science-tech-rss"}),
    )
    scan_status_repository = daily_news_backend.get_daily_news_scan_status_repository(worker_settings)

    daily_news_worker.run_one_tick(worker_settings, scan_status_repository)

    output = capsys.readouterr().out
    assert f"sources_polled={len(EDITORIAL_SOURCE_REGISTRY) + 3}" in output
    light_reading = next(e for e in GATED_MARKET_NEWS_SOURCE_REGISTRY if e.source_id == "light-reading-rss")
    industries = next(e for e in GATED_JP_KR_SOURCE_REGISTRY if e.source_id == "businesskorea-industries-rss")
    sci_tech = next(e for e in GATED_JP_KR_SOURCE_REGISTRY if e.source_id == "businesskorea-science-tech-rss")
    assert light_reading.canonical_url in call_urls
    assert industries.canonical_url in call_urls
    assert sci_tech.canonical_url in call_urls
    japan_times_business = next(e for e in GATED_JP_KR_SOURCE_REGISTRY if e.source_id == "japan-times-business-rss")
    assert japan_times_business.canonical_url not in call_urls  # PENDING_REVIEW, never in this allow-list
