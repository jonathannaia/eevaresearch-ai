"""scripts/company_discovery_worker.py — configuration safety (live mode
requires Postgres, never JSON/SQLite), disabled-by-default no-op,
worker-status recording via the durable repository, and zero-network
I/O. No real network call anywhere — every test calls run_one_tick()/
main() against SQLite-backed fixtures directly, never a live worker
loop."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import company_discovery_worker
from src.config.settings import Settings
from src.data_access.company_discovery.company_discovery_backend import SqliteCandidateIssuerRepository
from src.data_access.state_db import connection, schema
from src.data_access.state_db.candidate_issuer_repository import get_worker_status


def _settings(tmp_path, **overrides) -> Settings:
    fields = dict(
        company_discovery_live_enabled=False,
        company_discovery_worker_db_backend=None,
        company_discovery_worker_state_db_url=None,
        cache_dir=tmp_path,
    )
    fields.update(overrides)
    return Settings(**fields)


# --- Configuration safety --------------------------------------------------


def test_build_worker_settings_rejects_json_backend():
    ambient = Settings(company_discovery_worker_db_backend=None)
    with pytest.raises(company_discovery_worker.WorkerConfigurationError):
        company_discovery_worker._build_worker_settings(ambient)


def test_build_worker_settings_rejects_sqlite_backend_for_live_mode():
    ambient = Settings(company_discovery_worker_db_backend="sqlite")
    with pytest.raises(company_discovery_worker.WorkerConfigurationError):
        company_discovery_worker._build_worker_settings(ambient)


def test_build_worker_settings_rejects_postgres_without_dsn():
    ambient = Settings(company_discovery_worker_db_backend="postgres", company_discovery_worker_state_db_url=None)
    with pytest.raises(company_discovery_worker.WorkerConfigurationError):
        company_discovery_worker._build_worker_settings(ambient)


def test_build_worker_settings_accepts_postgres_with_dsn():
    ambient = Settings(
        company_discovery_worker_db_backend="postgres",
        company_discovery_worker_state_db_url="postgresql://example/test",
    )
    worker_settings = company_discovery_worker._build_worker_settings(ambient)
    assert worker_settings.db_backend == "postgres"
    assert worker_settings.state_db_url == "postgresql://example/test"


# --- Disabled-by-default no-op ----------------------------------------------


def test_main_is_a_no_op_when_live_flag_disabled(tmp_path, capsys):
    with patch("scripts.company_discovery_worker.get_settings", return_value=_settings(tmp_path, company_discovery_live_enabled=False)):
        result = company_discovery_worker.main()
    assert result == 0
    assert "not enabled" in capsys.readouterr().out


def test_main_fails_closed_when_live_enabled_but_backend_not_postgres(tmp_path, capsys):
    with patch(
        "scripts.company_discovery_worker.get_settings",
        return_value=_settings(tmp_path, company_discovery_live_enabled=True, company_discovery_worker_db_backend="sqlite"),
    ):
        result = company_discovery_worker.main()
    assert result == 1
    assert "postgres" in capsys.readouterr().err.lower()


# --- Zero-network-call proof, tick status recording (SQLite fixture) -------


def test_run_one_tick_makes_no_network_call_and_records_worker_status(tmp_path, monkeypatch):
    def _forbidden(*_args, **_kwargs):
        raise AssertionError("Company Discovery worker attempted a live network call — must stay network-free.")

    monkeypatch.setattr("requests.get", _forbidden, raising=False)
    monkeypatch.setattr("requests.post", _forbidden, raising=False)

    db_path = tmp_path / "test.db"
    conn = connection.connect(db_path)
    schema.migrate(conn)
    worker_settings = Settings(db_backend="sqlite", state_db_path=db_path, cache_dir=tmp_path)
    repository = SqliteCandidateIssuerRepository(conn=conn)

    company_discovery_worker.run_one_tick(worker_settings, repository)

    status = get_worker_status(conn)
    assert status is not None
    assert status.last_failure_code is None


def test_run_one_tick_skips_advisory_locking_entirely_for_sqlite(tmp_path):
    """SQLite-backed repositories (direct tick-level tests only — never a
    live worker) skip advisory locking entirely: pg_try_advisory_lock has
    no SQLite equivalent, and a single-process test needs no concurrency
    guard — mirrors scripts/daily_news_worker.py's own identical
    _lock_connection() contract."""
    db_path = tmp_path / "test.db"
    conn = connection.connect(db_path)
    schema.migrate(conn)
    repository = SqliteCandidateIssuerRepository(conn=conn)
    assert company_discovery_worker._lock_connection(repository) is None


def test_tick_failure_is_recorded_and_never_raised(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    conn = connection.connect(db_path)
    schema.migrate(conn)
    worker_settings = Settings(db_backend="sqlite", state_db_path=db_path, cache_dir=tmp_path)
    repository = SqliteCandidateIssuerRepository(conn=conn)

    def _raise(*_args, **_kwargs):
        raise RuntimeError("simulated pipeline failure")

    monkeypatch.setattr(
        "src.data_access.company_discovery.candidate_pipeline.run_candidate_discovery_tick", _raise,
    )
    monkeypatch.setattr(company_discovery_worker.candidate_pipeline, "run_candidate_discovery_tick", _raise)

    company_discovery_worker.run_one_tick(worker_settings, repository)  # must not raise

    status = get_worker_status(conn)
    assert status.last_failure_code == "RuntimeError"


def test_advisory_lock_key_is_distinct_from_daily_news_workers_key():
    from scripts import daily_news_worker

    assert (
        company_discovery_worker._COMPANY_DISCOVERY_WORKER_ADVISORY_LOCK_KEY
        != daily_news_worker._DAILY_NEWS_WORKER_ADVISORY_LOCK_KEY
    )
