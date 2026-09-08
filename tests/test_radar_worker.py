"""Durable-State Phase 4M-0 — scripts/radar_worker.py: configuration
safety (including the critical edgar_auto_publish_enabled=False
forcing), per-provider file-lock exclusion, per-provider tick
success/failure recording, and provider-isolation (one provider's
exception never stops another's tick in the same round). No real
network call, no real EDGAR/DART/EDINET client, no real scheduler loop —
every test calls run_one_tick()/the internal tick function directly,
never main()'s own while-loop."""
from __future__ import annotations

import types
from dataclasses import dataclass
from pathlib import Path

import pytest

from scripts import radar_worker
from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.state_db.scan_status_repository import ProviderScanStatus


def _ambient_settings(**overrides) -> Settings:
    fields = dict(
        radar_live_scan_enabled=True,
        radar_worker_db_backend="sqlite",
        radar_worker_state_db_path=None,
        radar_worker_state_db_url=None,
        edgar_auto_publish_enabled=False,
    )
    fields.update(overrides)
    return Settings(**fields)


# --- _build_worker_settings: configuration validation + the auto-publish safety guarantee ---

def test_build_worker_settings_rejects_json_backend():
    ambient = _ambient_settings(radar_worker_db_backend="json")
    with pytest.raises(radar_worker.WorkerConfigurationError):
        radar_worker._build_worker_settings(ambient)


def test_build_worker_settings_rejects_missing_backend():
    ambient = _ambient_settings(radar_worker_db_backend=None)
    with pytest.raises(radar_worker.WorkerConfigurationError):
        radar_worker._build_worker_settings(ambient)


def test_build_worker_settings_rejects_sqlite_without_path():
    ambient = _ambient_settings(radar_worker_db_backend="sqlite", radar_worker_state_db_path=None)
    with pytest.raises(radar_worker.WorkerConfigurationError):
        radar_worker._build_worker_settings(ambient)


def test_build_worker_settings_rejects_postgres_without_url():
    ambient = _ambient_settings(radar_worker_db_backend="postgres", radar_worker_state_db_url=None)
    with pytest.raises(radar_worker.WorkerConfigurationError):
        radar_worker._build_worker_settings(ambient)


def test_build_worker_settings_uses_dedicated_worker_fields_not_ambient_db_fields(tmp_path):
    ambient = _ambient_settings(
        radar_worker_db_backend="sqlite",
        radar_worker_state_db_path=tmp_path / "worker-state.db",
        db_backend="postgres",
        state_db_path=tmp_path / "should-never-be-used.db",
        state_db_url="postgres://ambient-dashboard-dsn-should-never-be-used",
    )
    worker_settings = radar_worker._build_worker_settings(ambient)
    assert worker_settings.db_backend == "sqlite"
    assert worker_settings.state_db_path == tmp_path / "worker-state.db"
    assert worker_settings.state_db_url is None


def test_build_worker_settings_forces_edgar_auto_publish_disabled_even_when_ambient_enables_it(tmp_path):
    """The critical safety proof: EDGE_EDGAR_AUTO_PUBLISH_ENABLED=true in
    this worker process's own ambient environment must never let the
    worker autonomously PUBLISH a candidate. edgar_pipeline.run_pipeline's
    own auto_publish_enabled parameter (a separate, pre-existing feature)
    is the one mechanism that could otherwise do this — see this
    module's own docstring."""
    ambient = _ambient_settings(
        radar_worker_db_backend="sqlite",
        radar_worker_state_db_path=tmp_path / "state.db",
        edgar_auto_publish_enabled=True,
    )
    assert ambient.edgar_auto_publish_enabled is True  # sanity: the dangerous ambient value is really set

    worker_settings = radar_worker._build_worker_settings(ambient)
    assert worker_settings.edgar_auto_publish_enabled is False


# --- _provider_lock: exclusion + auto-release ---

def test_provider_lock_excludes_a_second_concurrent_acquire(tmp_path, monkeypatch):
    monkeypatch.setattr(radar_worker, "_LOCK_DIR", tmp_path / "locks")
    with radar_worker._provider_lock("edgar-sqlite") as first:
        assert first is True
        with radar_worker._provider_lock("edgar-sqlite") as second:
            assert second is False


def test_provider_lock_is_released_and_reacquirable_after_the_context_exits(tmp_path, monkeypatch):
    monkeypatch.setattr(radar_worker, "_LOCK_DIR", tmp_path / "locks")
    with radar_worker._provider_lock("edgar-sqlite") as first:
        assert first is True
    with radar_worker._provider_lock("edgar-sqlite") as second:
        assert second is True


def test_provider_lock_keys_are_independent_across_providers(tmp_path, monkeypatch):
    monkeypatch.setattr(radar_worker, "_LOCK_DIR", tmp_path / "locks")
    with radar_worker._provider_lock("edgar-sqlite") as edgar_lock:
        assert edgar_lock is True
        with radar_worker._provider_lock("dart-sqlite") as dart_lock:
            assert dart_lock is True


# --- _run_provider_tick / run_one_tick: success, failure, skip-on-lock, provider isolation ---

@dataclass
class _FakeReport:
    candidates_detected: int = 2
    candidates_processed: int = 1
    warnings: tuple = ()
    end_date: str = "2026-08-20"


@dataclass
class _FakeDartReport:
    """DART's real ScanReport uses end_de, not end_date — a separate
    fake class proves _run_provider_tick's cursor derivation handles
    both naming conventions."""
    candidates_detected: int = 1
    candidates_processed: int = 1
    warnings: tuple = ()
    end_de: str = "20260820"


def _worker_settings(tmp_path) -> Settings:
    ambient = _ambient_settings(radar_worker_db_backend="sqlite", radar_worker_state_db_path=tmp_path / "state.db")
    return radar_worker._build_worker_settings(ambient)


def _scan_status_repo(worker_settings):
    return backend_factory.get_scan_status_repository(worker_settings)


def test_run_provider_tick_success_persists_expected_status(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    monkeypatch.setitem(
        radar_worker._SERVICE_MODULES, "edgar",
        types.SimpleNamespace(run_scan=lambda settings, candidate_repository=None: _FakeReport()),
    )
    scan_status_repo = _scan_status_repo(worker_settings)

    radar_worker._run_provider_tick("edgar", worker_settings, scan_status_repo)

    status = scan_status_repo.get_scan_status("SEC EDGAR")
    assert status is not None
    assert status.items_discovered == 2
    assert status.candidates_created == 1
    assert status.cursor_value == "2026-08-20"
    assert status.failure_code is None
    assert status.skipped_unresolved_count == 0


def test_run_provider_tick_derives_cursor_from_end_de_for_dart_shaped_reports(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    monkeypatch.setitem(
        radar_worker._SERVICE_MODULES, "dart",
        types.SimpleNamespace(run_scan=lambda settings, candidate_repository=None: _FakeDartReport()),
    )
    scan_status_repo = _scan_status_repo(worker_settings)

    radar_worker._run_provider_tick("dart", worker_settings, scan_status_repo)

    status = scan_status_repo.get_scan_status("OpenDART / DART")
    assert status.cursor_value == "20260820"


def test_run_provider_tick_counts_skipped_unresolved_from_warnings(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    report = _FakeReport(warnings=(
        "AMD: CIK not resolved — run cik_resolver first.",
        "INTC: CIK not resolved — run cik_resolver first.",
        "some unrelated warning",
    ))
    monkeypatch.setitem(
        radar_worker._SERVICE_MODULES, "edgar",
        types.SimpleNamespace(run_scan=lambda settings, candidate_repository=None: report),
    )
    scan_status_repo = _scan_status_repo(worker_settings)

    radar_worker._run_provider_tick("edgar", worker_settings, scan_status_repo)

    status = scan_status_repo.get_scan_status("SEC EDGAR")
    assert status.skipped_unresolved_count == 2


def test_run_provider_tick_failure_records_sanitized_failure_code_and_preserves_prior_progress(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    scan_status_repo = _scan_status_repo(worker_settings)
    # Seed a prior successful status.
    scan_status_repo.upsert_scan_status(ProviderScanStatus(
        provider="SEC EDGAR", cursor_value="2026-08-19", started_at="2026-08-19T00:00:00+00:00",
        completed_at="2026-08-19T00:01:00+00:00", last_successful_at="2026-08-19T00:01:00+00:00",
        items_discovered=5, candidates_created=2, skipped_unresolved_count=0, failure_code=None,
        updated_at="2026-08-19T00:01:00+00:00",
    ))

    def _raise(settings, candidate_repository=None):
        raise RuntimeError("some raw internal detail that must never be persisted")

    monkeypatch.setitem(radar_worker._SERVICE_MODULES, "edgar", types.SimpleNamespace(run_scan=_raise))

    radar_worker._run_provider_tick("edgar", worker_settings, scan_status_repo)

    status = scan_status_repo.get_scan_status("SEC EDGAR")
    assert status.failure_code == "RuntimeError"
    assert "raw internal detail" not in (status.failure_code or "")
    # Prior successful progress is preserved, not erased by the failed attempt.
    assert status.cursor_value == "2026-08-19"
    assert status.last_successful_at == "2026-08-19T00:01:00+00:00"
    assert status.items_discovered == 5
    assert status.candidates_created == 2


def test_run_provider_tick_failure_with_no_prior_status_uses_safe_defaults(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    scan_status_repo = _scan_status_repo(worker_settings)

    def _raise(settings, candidate_repository=None):
        raise ConnectionError("boom")

    monkeypatch.setitem(radar_worker._SERVICE_MODULES, "edgar", types.SimpleNamespace(run_scan=_raise))
    radar_worker._run_provider_tick("edgar", worker_settings, scan_status_repo)

    status = scan_status_repo.get_scan_status("SEC EDGAR")
    assert status.failure_code == "ConnectionError"
    assert status.cursor_value is None
    assert status.last_successful_at is None
    assert status.items_discovered == 0
    assert status.candidates_created == 0


def test_run_provider_tick_skips_and_writes_nothing_when_lock_is_already_held(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    monkeypatch.setattr(radar_worker, "_LOCK_DIR", tmp_path / "locks")
    scan_status_repo = _scan_status_repo(worker_settings)

    called = []
    monkeypatch.setitem(
        radar_worker._SERVICE_MODULES, "edgar",
        types.SimpleNamespace(run_scan=lambda settings, candidate_repository=None: called.append(1) or _FakeReport()),
    )

    with radar_worker._provider_lock(f"edgar-{worker_settings.db_backend}"):
        radar_worker._run_provider_tick("edgar", worker_settings, scan_status_repo)

    assert called == []
    assert scan_status_repo.get_scan_status("SEC EDGAR") is None


def test_run_one_tick_provider_failure_does_not_prevent_other_providers_from_running(tmp_path, monkeypatch):
    """Provider isolation: EDGAR raising must never stop DART/EDINET
    from being attempted in the same round."""
    worker_settings = _worker_settings(tmp_path)
    scan_status_repo = _scan_status_repo(worker_settings)

    def _raise(settings, candidate_repository=None):
        raise RuntimeError("edgar down")

    dart_called = []
    edinet_called = []
    monkeypatch.setitem(radar_worker._SERVICE_MODULES, "edgar", types.SimpleNamespace(run_scan=_raise))
    monkeypatch.setitem(
        radar_worker._SERVICE_MODULES, "dart",
        types.SimpleNamespace(run_scan=lambda settings, candidate_repository=None: dart_called.append(1) or _FakeDartReport()),
    )
    monkeypatch.setitem(
        radar_worker._SERVICE_MODULES, "edinet",
        types.SimpleNamespace(run_scan=lambda settings, candidate_repository=None: edinet_called.append(1) or _FakeReport()),
    )

    radar_worker.run_one_tick(worker_settings, scan_status_repo)

    assert dart_called == [1]
    assert edinet_called == [1]
    assert scan_status_repo.get_scan_status("SEC EDGAR").failure_code == "RuntimeError"
    assert scan_status_repo.get_scan_status("OpenDART / DART").failure_code is None
    assert scan_status_repo.get_scan_status("EDINET").failure_code is None


def test_run_one_tick_repository_construction_failure_is_isolated_per_provider(tmp_path, monkeypatch):
    """A provider whose candidate-repository construction itself fails
    (not just run_scan) must be isolated exactly the same way."""
    worker_settings = _worker_settings(tmp_path)
    scan_status_repo = _scan_status_repo(worker_settings)

    real_get_candidate_repository = backend_factory.get_candidate_repository

    def _flaky_get_candidate_repository(settings, source):
        if source == "SEC EDGAR":
            raise ValueError("synthetic repository construction failure")
        return real_get_candidate_repository(settings, source)

    monkeypatch.setattr(backend_factory, "get_candidate_repository", _flaky_get_candidate_repository)
    monkeypatch.setitem(
        radar_worker._SERVICE_MODULES, "dart",
        types.SimpleNamespace(run_scan=lambda settings, candidate_repository=None: _FakeDartReport()),
    )
    monkeypatch.setitem(
        radar_worker._SERVICE_MODULES, "edinet",
        types.SimpleNamespace(run_scan=lambda settings, candidate_repository=None: _FakeReport()),
    )
    monkeypatch.setitem(
        radar_worker._SERVICE_MODULES, "edgar",
        types.SimpleNamespace(run_scan=lambda settings, candidate_repository=None: _FakeReport()),
    )

    radar_worker.run_one_tick(worker_settings, scan_status_repo)

    assert scan_status_repo.get_scan_status("SEC EDGAR").failure_code == "ValueError"
    assert scan_status_repo.get_scan_status("OpenDART / DART").failure_code is None
    assert scan_status_repo.get_scan_status("EDINET").failure_code is None


# --- main(): master switch, config-error surfacing, no infinite loop entered when disabled ---

def test_main_is_a_noop_returning_zero_when_master_switch_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(radar_worker, "get_settings", lambda: _ambient_settings(radar_live_scan_enabled=False))
    assert radar_worker.main([]) == 0


def test_main_returns_one_and_sanitized_message_on_invalid_worker_backend(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        radar_worker, "get_settings",
        lambda: _ambient_settings(radar_live_scan_enabled=True, radar_worker_db_backend="json"),
    )
    rc = radar_worker.main([])
    assert rc == 1
    captured = capsys.readouterr()
    assert "ERROR" in captured.err


def test_main_never_reaches_the_scan_loop_when_disabled(tmp_path, monkeypatch):
    """Proves main() exits before constructing any repository or
    scanning anything at all when the master switch is off — the
    dashboard-side half of the 'no recurring scan without the explicit
    flag' invariant; this test is the worker-side half."""
    monkeypatch.setattr(radar_worker, "get_settings", lambda: _ambient_settings(radar_live_scan_enabled=False))

    def _fail(*args, **kwargs):
        raise AssertionError("must not be called when the master switch is disabled")

    monkeypatch.setattr(radar_worker, "run_one_tick", _fail)
    assert radar_worker.main([]) == 0
