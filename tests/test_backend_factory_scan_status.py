"""Durable-State Phase 4M-0 — backend_factory.get_scan_status_repository()
routing and its deliberate JSON-rejection behavior (no JSON
implementation exists at all for this factory — see backend_factory.py's
own docstring on why). SQLite-side routing only; the real-Postgres
routing sibling lives in test_backend_factory_postgres.py alongside its
own pg_isolated_dsn fixture, matching this codebase's existing
sqlite-tests-here / postgres-tests-there split."""
from __future__ import annotations

from dataclasses import replace

import pytest

from src.config.settings import Settings
from src.data_access import backend_factory


def _sqlite_settings(tmp_path):
    return Settings(db_backend="sqlite", state_db_path=tmp_path / "state.db")


@pytest.mark.parametrize("backend_value", [None, "", "json", "not-a-real-backend"])
def test_json_or_unrecognized_backend_raises_configuration_error_never_returns_json(backend_value, tmp_path):
    kwargs = {"cache_dir": tmp_path / "cache"}
    if backend_value is not None:
        kwargs["db_backend"] = backend_value
    settings = Settings(**kwargs)
    with pytest.raises(backend_factory.BackendConfigurationError):
        backend_factory.get_scan_status_repository(settings)


def test_explicit_sqlite_backend_selects_sqlite_scan_status_repository(tmp_path):
    settings = _sqlite_settings(tmp_path)
    repo = backend_factory.get_scan_status_repository(settings)
    assert isinstance(repo, backend_factory.SqliteScanStatusRepository)


def test_sqlite_without_state_db_path_raises_configuration_error(tmp_path):
    settings = Settings(db_backend="sqlite", state_db_path=None, cache_dir=tmp_path / "cache")
    with pytest.raises(backend_factory.BackendConfigurationError):
        backend_factory.get_scan_status_repository(settings)


def test_sqlite_backend_case_insensitive_and_trims_whitespace(tmp_path):
    settings = replace(_sqlite_settings(tmp_path), db_backend=" SQLite ")
    assert isinstance(backend_factory.get_scan_status_repository(settings), backend_factory.SqliteScanStatusRepository)


def test_sqlite_scan_status_repository_round_trips_through_the_protocol_methods(tmp_path):
    from src.data_access.state_db.scan_status_repository import ProviderScanStatus

    settings = _sqlite_settings(tmp_path)
    repo = backend_factory.get_scan_status_repository(settings)
    assert repo.get_scan_status("SEC EDGAR") is None

    status = ProviderScanStatus(
        provider="SEC EDGAR", cursor_value="20260101", started_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:01:00+00:00", last_successful_at="2026-01-01T00:01:00+00:00",
        items_discovered=1, candidates_created=1, skipped_unresolved_count=0, failure_code=None,
        updated_at="2026-01-01T00:01:00+00:00",
    )
    repo.upsert_scan_status(status)
    assert repo.get_scan_status("SEC EDGAR") == status
    assert repo.get_all_scan_statuses() == {"SEC EDGAR": status}


def test_scan_status_repository_never_creates_or_writes_the_json_cache_directory(tmp_path):
    settings = _sqlite_settings(tmp_path)
    settings = replace(settings, cache_dir=tmp_path / "should-never-be-created")
    backend_factory.get_scan_status_repository(settings)
    assert not settings.cache_dir.exists()
