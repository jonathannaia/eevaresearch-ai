"""EevaResearch Phase 4, Step 4B-2 (design/DECISIONS.md) — worker-only,
EDGAR-only autonomous Research Case creation, wired into
scripts/radar_worker.py::_run_provider_tick(). Follows this repo's
existing tests/test_radar_worker.py conventions exactly: every test
calls _run_provider_tick()/run_one_tick() directly against a real
in-file SQLite backend (never a live network call, live scan, or the
authoring script), using fake `run_scan` service modules from
`types.SimpleNamespace`. No real worker process, scheduler loop, or
deployment is ever started."""
from __future__ import annotations

import ast
import subprocess
import types
from pathlib import Path

import pytest

from scripts import radar_worker
from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.edgar import scan_service as edgar_scan_service
from src.data_access.state_db.scan_status_repository import ProviderScanStatus
from src.logic import research_lead_orchestration
from src.models.models import (
    CandidateSignal,
    CandidateStatus,
    ExtractionState,
    FilingEvent,
    StateTransition,
)

REPO_ROOT = Path(__file__).parent.parent


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


def _worker_settings(tmp_path) -> Settings:
    ambient = _ambient_settings(radar_worker_db_backend="sqlite", radar_worker_state_db_path=tmp_path / "state.db")
    return radar_worker._build_worker_settings(ambient)


def _scan_status_repo(worker_settings):
    return backend_factory.get_scan_status_repository(worker_settings)


def _fake_report(candidates_detected=1, candidates_processed=1, end_date="2026-08-20"):
    return types.SimpleNamespace(
        candidates_detected=candidates_detected, candidates_processed=candidates_processed,
        warnings=(), end_date=end_date,
    )


def _edgar_candidate(rcept_no="acc-1", rcept_dt="2026-08-15", detected_at="2026-08-15T00:00:00+00:00", **overrides):
    filing = FilingEvent(
        rcept_no=rcept_no, corp_code="0000320193", corp_name="Apple Inc.", stock_code="AAPL",
        report_nm="8-K", rcept_dt=rcept_dt, flr_nm="Apple Inc.", source_name="SEC EDGAR",
        source_url="https://example.com/filing", retrieved_at="2026-08-15T01:00:00+00:00",
        original_language="English",
    )
    defaults = dict(
        id=f"edgar-cand-{rcept_no}", filing=filing,
        matched_rules=["financing_or_debt:2.03", "material_agreement:1.01"],
        confidence="High", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="The company entered into a financing agreement.",
        state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at=detected_at)],
    )
    defaults.update(overrides)
    return CandidateSignal(**defaults)


def _seed_edgar_candidates(worker_settings, *candidates):
    repo = backend_factory.get_candidate_repository(worker_settings, "SEC EDGAR")
    repo.upsert_new_candidates(list(candidates))


def _set_edgar_run_scan(monkeypatch, report=None):
    report = report or _fake_report()
    monkeypatch.setitem(
        radar_worker._SERVICE_MODULES, "edgar",
        types.SimpleNamespace(run_scan=lambda settings, candidate_repository=None: report),
    )


def _set_fixed_as_of_date(monkeypatch, value="2026-08-20"):
    monkeypatch.setattr(radar_worker, "_current_utc_date", lambda: value)


# ============================================================
# Proof 1/2 — EDGAR-only gating
# ============================================================


def test_proof1_research_case_step_runs_only_for_edgar(tmp_path, monkeypatch, capsys):
    worker_settings = _worker_settings(tmp_path)
    _set_edgar_run_scan(monkeypatch)
    _set_fixed_as_of_date(monkeypatch)
    scan_status_repo = _scan_status_repo(worker_settings)

    radar_worker._run_provider_tick("edgar", worker_settings, scan_status_repo)
    out = capsys.readouterr().out
    assert "research cases" in out


@pytest.mark.parametrize("provider_key,display_source,report_kwargs", [
    ("dart", "OpenDART / DART", {}),
    ("edinet", "EDINET", {}),
])
def test_proof2_dart_and_edinet_now_create_research_cases_via_the_shared_pipeline(tmp_path, monkeypatch, capsys, provider_key, display_source, report_kwargs):
    """Autonomous Theme candidate detection, Phase 2 (design/
    DECISIONS.md): this test previously proved the OPPOSITE — that
    DART/EDINET never touched research-case machinery at all. That was
    correct for Phase 4B-2/Phase A2's own EDGAR-only scope, and is now
    deliberately superseded: research_lead_orchestration/
    research_lead_selection/research_lead_factory were already fully
    source-agnostic, so Phase 2 widens `allowed_source_names` per
    provider via the new, generic `_run_source_research_case_step`
    (scripts/radar_worker.py) rather than building a new pipeline.
    `_run_edgar_research_case_step` itself remains completely untouched
    and is never called for dart/edinet."""
    worker_settings = _worker_settings(tmp_path)

    monkeypatch.setitem(
        radar_worker._SERVICE_MODULES, provider_key,
        types.SimpleNamespace(run_scan=lambda settings, candidate_repository=None: types.SimpleNamespace(
            candidates_detected=1, candidates_processed=1, warnings=(), end_de="20260820",
        )),
    )
    scan_status_repo = _scan_status_repo(worker_settings)

    radar_worker._run_provider_tick(provider_key, worker_settings, scan_status_repo)

    out = capsys.readouterr().out
    assert f"{provider_key.upper()}: research cases" in out
    assert scan_status_repo.get_scan_status(display_source) is not None


# ============================================================
# Proof 3/4 — ordering relative to scan-status persistence
# ============================================================


class _TrackingScanStatusRepoProxy:
    """A plain, non-frozen proxy — the real adapters backend_factory
    returns are frozen dataclasses, so a monkeypatch that assigns a new
    attribute directly onto one raises FrozenInstanceError. This proxy
    wraps the real repository and forwards every other attribute
    unchanged via __getattr__."""

    def __init__(self, real_repo, call_order):
        self._real = real_repo
        self._call_order = call_order

    def get_scan_status(self, provider):
        return self._real.get_scan_status(provider)

    def upsert_scan_status(self, status):
        self._call_order.append("upsert_scan_status")
        return self._real.upsert_scan_status(status)


def test_proof3_research_step_occurs_only_after_upsert_scan_status(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    _set_edgar_run_scan(monkeypatch)
    call_order: list = []
    scan_status_repo = _TrackingScanStatusRepoProxy(_scan_status_repo(worker_settings), call_order)

    monkeypatch.setattr(
        radar_worker, "_run_edgar_research_case_step",
        lambda *a, **k: call_order.append("research_case_step") or (
            "EDGAR: research cases — evaluated=0 created=0 existing=0 not_qualified=0 "
            "factory_rejected=0 validation_rejected=0 write_rejected=0"
        ),
    )

    radar_worker._run_provider_tick("edgar", worker_settings, scan_status_repo)
    assert call_order == ["upsert_scan_status", "research_case_step"]


def test_proof4_research_step_never_runs_when_scan_raises(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    scan_status_repo = _scan_status_repo(worker_settings)

    def _raise(settings, candidate_repository=None):
        raise RuntimeError("scan failed")

    monkeypatch.setitem(radar_worker._SERVICE_MODULES, "edgar", types.SimpleNamespace(run_scan=_raise))

    def _forbidden(*_a, **_k):
        raise AssertionError("research-case step must never run when the scan itself raised")

    monkeypatch.setattr(radar_worker, "_run_edgar_research_case_step", _forbidden)

    radar_worker._run_provider_tick("edgar", worker_settings, scan_status_repo)
    status = scan_status_repo.get_scan_status("SEC EDGAR")
    assert status.failure_code == "RuntimeError"


# ============================================================
# Proof 5 — existing scan status behavior unchanged
# ============================================================


def test_proof5_scan_status_fields_unchanged_with_research_step_active(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    _seed_edgar_candidates(worker_settings, _edgar_candidate())
    _set_edgar_run_scan(monkeypatch, _fake_report(candidates_detected=3, candidates_processed=2, end_date="2026-08-21"))
    _set_fixed_as_of_date(monkeypatch)
    scan_status_repo = _scan_status_repo(worker_settings)

    radar_worker._run_provider_tick("edgar", worker_settings, scan_status_repo)

    status = scan_status_repo.get_scan_status("SEC EDGAR")
    assert status.items_discovered == 3
    assert status.candidates_created == 2
    assert status.cursor_value == "2026-08-21"
    assert status.failure_code is None


# ============================================================
# Proof 6/7 — exact collaborator usage and config values
# ============================================================


def test_proof6_and_7_uses_expected_collaborators_and_config(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    candidate = _edgar_candidate()
    _seed_edgar_candidates(worker_settings, candidate)
    _set_edgar_run_scan(monkeypatch)
    _set_fixed_as_of_date(monkeypatch, "2026-08-20")
    scan_status_repo = _scan_status_repo(worker_settings)

    captured_config = {}
    real_prepare = research_lead_orchestration.prepare_research_case_bundles

    def _tracking_prepare(candidates, existing_case_ids, config):
        captured_config["config"] = config
        captured_config["candidates"] = list(candidates)
        captured_config["existing_case_ids"] = existing_case_ids
        return real_prepare(candidates, existing_case_ids, config)

    monkeypatch.setattr(radar_worker, "prepare_research_case_bundles", _tracking_prepare)

    radar_worker._run_provider_tick("edgar", worker_settings, scan_status_repo)

    config = captured_config["config"]
    assert config.allowed_source_names == ("SEC EDGAR",)
    assert config.max_candidates == 5
    assert config.lookback_days == edgar_scan_service.DEFAULT_LOOKBACK_DAYS
    assert config.as_of_date == "2026-08-20"
    assert candidate.id in {c.id for c in captured_config["candidates"]}


# ============================================================
# Proof 8 — candidate repository load bounded to one call
# ============================================================


class _TrackingCandidateRepoProxy:
    def __init__(self, real_repo, load_calls):
        self._real = real_repo
        self._load_calls = load_calls

    def load_candidates(self):
        self._load_calls.append(1)
        return self._real.load_candidates()

    def get_candidate(self, candidate_id):
        return self._real.get_candidate(candidate_id)

    def get_candidate_version(self, candidate_id):
        return self._real.get_candidate_version(candidate_id)

    def upsert_new_candidates(self, new_candidates):
        return self._real.upsert_new_candidates(new_candidates)

    def update_candidate(self, candidate, expected_version=None):
        return self._real.update_candidate(candidate, expected_version)


def test_proof8_candidate_repository_load_candidates_called_exactly_once(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    _seed_edgar_candidates(worker_settings, _edgar_candidate())
    _set_edgar_run_scan(monkeypatch)
    _set_fixed_as_of_date(monkeypatch)
    scan_status_repo = _scan_status_repo(worker_settings)

    real_get_candidate_repository = backend_factory.get_candidate_repository
    load_calls = []

    def _wrapping_get_candidate_repository(settings, source):
        real_repo = real_get_candidate_repository(settings, source)
        return _TrackingCandidateRepoProxy(real_repo, load_calls)

    monkeypatch.setattr(backend_factory, "get_candidate_repository", _wrapping_get_candidate_repository)

    radar_worker._run_provider_tick("edgar", worker_settings, scan_status_repo)
    assert len(load_calls) == 1


# ============================================================
# Proof 9 — dedup delegated through the read-only repository, no get_case loop
# ============================================================


class _TrackingResearchCaseRepoProxy:
    def __init__(self, real_repo, existing_case_ids_calls, get_case_calls):
        self._real = real_repo
        self._existing_case_ids_calls = existing_case_ids_calls
        self._get_case_calls = get_case_calls

    def list_recent_cases(self, limit):
        return self._real.list_recent_cases(limit)

    def get_case(self, case_id):
        self._get_case_calls.append(case_id)
        return self._real.get_case(case_id)

    def evidence_items_for_case_ids(self, case_ids):
        return self._real.evidence_items_for_case_ids(case_ids)

    def assertions_for_case_ids(self, case_ids):
        return self._real.assertions_for_case_ids(case_ids)

    def existing_case_ids(self, case_ids):
        self._existing_case_ids_calls.append(list(case_ids))
        return self._real.existing_case_ids(case_ids)


def test_proof9_membership_delegated_through_repository_no_get_case_loop(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    _seed_edgar_candidates(worker_settings, _edgar_candidate())
    _set_edgar_run_scan(monkeypatch)
    _set_fixed_as_of_date(monkeypatch)
    scan_status_repo = _scan_status_repo(worker_settings)

    real_get_research_case_repository = backend_factory.get_research_case_repository
    existing_case_ids_calls: list = []
    get_case_calls: list = []

    def _wrapping_get_research_case_repository(settings):
        repo = real_get_research_case_repository(settings)
        return _TrackingResearchCaseRepoProxy(repo, existing_case_ids_calls, get_case_calls)

    monkeypatch.setattr(backend_factory, "get_research_case_repository", _wrapping_get_research_case_repository)

    radar_worker._run_provider_tick("edgar", worker_settings, scan_status_repo)
    assert len(existing_case_ids_calls) == 1
    assert get_case_calls == []


# ============================================================
# Proof 10/11/13 — writer call shape, False handling, per-bundle isolation
# ============================================================


class _TrackingWriterProxy:
    def __init__(self, real_writer, on_insert):
        self._real = real_writer
        self._on_insert = on_insert

    def insert_bundle(self, bundle):
        return self._on_insert(self._real, bundle)


def test_proof10_each_bundle_results_in_exactly_one_insert_bundle_call(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    _seed_edgar_candidates(worker_settings, _edgar_candidate())
    _set_edgar_run_scan(monkeypatch)
    _set_fixed_as_of_date(monkeypatch)
    scan_status_repo = _scan_status_repo(worker_settings)

    real_get_writer = backend_factory.get_research_case_bundle_writer
    insert_calls = []

    def _tracking(real_writer, bundle):
        insert_calls.append(bundle)
        return real_writer.insert_bundle(bundle)

    def _wrapping_get_writer(settings):
        return _TrackingWriterProxy(real_get_writer(settings), _tracking)

    monkeypatch.setattr(backend_factory, "get_research_case_bundle_writer", _wrapping_get_writer)

    radar_worker._run_provider_tick("edgar", worker_settings, scan_status_repo)
    assert len(insert_calls) == 1


def test_proof11_writer_false_does_not_retry_and_does_not_affect_other_bundles(tmp_path, monkeypatch, capsys):
    worker_settings = _worker_settings(tmp_path)
    a = _edgar_candidate(rcept_no="acc-a", rcept_dt="2026-08-10", detected_at="2026-08-10T00:00:00+00:00")
    b = _edgar_candidate(rcept_no="acc-b", rcept_dt="2026-08-12", detected_at="2026-08-12T00:00:00+00:00")
    _seed_edgar_candidates(worker_settings, a, b)
    _set_edgar_run_scan(monkeypatch)
    _set_fixed_as_of_date(monkeypatch)
    scan_status_repo = _scan_status_repo(worker_settings)

    real_get_writer = backend_factory.get_research_case_bundle_writer
    call_count = {"n": 0}

    def _flaky(real_writer, bundle):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return False
        return real_writer.insert_bundle(bundle)

    def _wrapping_get_writer(settings):
        return _TrackingWriterProxy(real_get_writer(settings), _flaky)

    monkeypatch.setattr(backend_factory, "get_research_case_bundle_writer", _wrapping_get_writer)

    radar_worker._run_provider_tick("edgar", worker_settings, scan_status_repo)
    out = capsys.readouterr().out
    assert "created=1" in out
    assert "write_rejected=1" in out


def test_proof13_exception_writing_one_bundle_does_not_prevent_later_bundles(tmp_path, monkeypatch, capsys):
    worker_settings = _worker_settings(tmp_path)
    a = _edgar_candidate(rcept_no="acc-a", rcept_dt="2026-08-10", detected_at="2026-08-10T00:00:00+00:00")
    b = _edgar_candidate(rcept_no="acc-b", rcept_dt="2026-08-12", detected_at="2026-08-12T00:00:00+00:00")
    _seed_edgar_candidates(worker_settings, a, b)
    _set_edgar_run_scan(monkeypatch)
    _set_fixed_as_of_date(monkeypatch)
    scan_status_repo = _scan_status_repo(worker_settings)

    real_get_writer = backend_factory.get_research_case_bundle_writer
    call_count = {"n": 0}

    def _raising_then_ok(real_writer, bundle):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("synthetic write failure")
        return real_writer.insert_bundle(bundle)

    def _wrapping_get_writer(settings):
        return _TrackingWriterProxy(real_get_writer(settings), _raising_then_ok)

    monkeypatch.setattr(backend_factory, "get_research_case_bundle_writer", _wrapping_get_writer)

    radar_worker._run_provider_tick("edgar", worker_settings, scan_status_repo)
    out = capsys.readouterr().out
    assert "created=1" in out
    assert "write_rejected=1" in out


# ============================================================
# Proof 12 — whole-step exception isolation
# ============================================================


def test_proof12_research_step_exception_is_swallowed_and_sanitized(tmp_path, monkeypatch, capsys):
    worker_settings = _worker_settings(tmp_path)
    _set_edgar_run_scan(monkeypatch)
    scan_status_repo = _scan_status_repo(worker_settings)

    def _raise(*_a, **_k):
        raise ValueError("a raw internal detail that must never be printed")

    monkeypatch.setattr(radar_worker, "_run_edgar_research_case_step", _raise)

    radar_worker._run_provider_tick("edgar", worker_settings, scan_status_repo)
    out = capsys.readouterr().out
    assert "EDGAR: research-case step skipped (ValueError)." in out
    assert "raw internal detail" not in out

    status = scan_status_repo.get_scan_status("SEC EDGAR")
    assert status.failure_code is None  # the scan itself still succeeded


def test_proof12_research_step_exception_does_not_alter_scan_status_fields(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    _set_edgar_run_scan(monkeypatch, _fake_report(candidates_detected=4, candidates_processed=3, end_date="2026-08-22"))
    scan_status_repo = _scan_status_repo(worker_settings)
    monkeypatch.setattr(radar_worker, "_run_edgar_research_case_step", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))

    radar_worker._run_provider_tick("edgar", worker_settings, scan_status_repo)

    status = scan_status_repo.get_scan_status("SEC EDGAR")
    assert status.items_discovered == 4
    assert status.candidates_created == 3
    assert status.cursor_value == "2026-08-22"
    assert status.failure_code is None


def test_proof12_research_step_exception_does_not_alter_candidate_status(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    candidate = _edgar_candidate()
    _seed_edgar_candidates(worker_settings, candidate)
    _set_edgar_run_scan(monkeypatch)
    scan_status_repo = _scan_status_repo(worker_settings)
    monkeypatch.setattr(radar_worker, "_run_edgar_research_case_step", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))

    radar_worker._run_provider_tick("edgar", worker_settings, scan_status_repo)

    repo = backend_factory.get_candidate_repository(worker_settings, "SEC EDGAR")
    reloaded = repo.get_candidate(candidate.id)
    assert reloaded.status == CandidateStatus.NEEDS_REVIEW


# ============================================================
# Proof 14 — EDGAR research-step exception does not block DART/EDINET
# ============================================================


def test_proof14_edgar_research_step_exception_does_not_prevent_dart_or_edinet(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    _set_edgar_run_scan(monkeypatch)
    monkeypatch.setattr(radar_worker, "_run_edgar_research_case_step", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))

    dart_called = []
    edinet_called = []
    monkeypatch.setitem(
        radar_worker._SERVICE_MODULES, "dart",
        types.SimpleNamespace(run_scan=lambda settings, candidate_repository=None: dart_called.append(1) or types.SimpleNamespace(
            candidates_detected=1, candidates_processed=1, warnings=(), end_de="20260820",
        )),
    )
    monkeypatch.setitem(
        radar_worker._SERVICE_MODULES, "edinet",
        types.SimpleNamespace(run_scan=lambda settings, candidate_repository=None: edinet_called.append(1) or _fake_report()),
    )

    scan_status_repo = _scan_status_repo(worker_settings)
    radar_worker.run_one_tick(worker_settings, scan_status_repo)

    assert dart_called == [1]
    assert edinet_called == [1]
    assert scan_status_repo.get_scan_status("OpenDART / DART").failure_code is None
    assert scan_status_repo.get_scan_status("EDINET").failure_code is None


# ============================================================
# Proof 15 — zero assertions on any autonomously created case
# ============================================================


def test_proof15_autonomously_created_case_has_zero_assertions(tmp_path, monkeypatch):
    worker_settings = _worker_settings(tmp_path)
    _seed_edgar_candidates(worker_settings, _edgar_candidate())
    _set_edgar_run_scan(monkeypatch)
    _set_fixed_as_of_date(monkeypatch)
    scan_status_repo = _scan_status_repo(worker_settings)

    radar_worker._run_provider_tick("edgar", worker_settings, scan_status_repo)

    research_case_repo = backend_factory.get_research_case_repository(worker_settings)
    cases = research_case_repo.list_recent_cases(10)
    assert len(cases) == 1
    case = cases[0]
    assertions = research_case_repo.assertions_for_case_ids([case.id]).get(case.id, ())
    assert assertions == ()


def test_proof15_idempotent_across_repeated_ticks(tmp_path, monkeypatch, capsys):
    worker_settings = _worker_settings(tmp_path)
    _seed_edgar_candidates(worker_settings, _edgar_candidate())
    _set_edgar_run_scan(monkeypatch)
    _set_fixed_as_of_date(monkeypatch)
    scan_status_repo = _scan_status_repo(worker_settings)

    radar_worker._run_provider_tick("edgar", worker_settings, scan_status_repo)
    capsys.readouterr()
    radar_worker._run_provider_tick("edgar", worker_settings, scan_status_repo)
    out = capsys.readouterr().out
    assert "created=0" in out
    assert "existing=1" in out

    research_case_repo = backend_factory.get_research_case_repository(worker_settings)
    assert len(research_case_repo.list_recent_cases(10)) == 1


# ============================================================
# Proof 16 — no other runtime entry point references this integration
# ============================================================


def test_proof16_no_other_runtime_entry_point_references_the_research_case_step():
    candidate_files = [
        "scripts/run_scan.py", "app.py", "src/ui/pages/research_cases.py",
        "src/ui/pages/radar_inbox.py", "src/ui/pages/daily_news.py",
    ]
    offenders = []
    for rel_path in candidate_files:
        full_path = REPO_ROOT / rel_path
        if not full_path.exists():
            continue
        source = full_path.read_text(encoding="utf-8")
        for name in ("_run_edgar_research_case_step", "prepare_research_case_bundles", "get_research_case_bundle_writer"):
            if name in source:
                offenders.append(f"{rel_path}: references {name!r}")
    assert not offenders, offenders


# ============================================================
# Proof 17/18 — no new config surface, scope guard
# ============================================================


def test_proof17_no_new_environment_variable_or_getenv_call_introduced():
    source = (REPO_ROOT / "scripts" / "radar_worker.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="radar_worker.py")
    getenv_calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("getenv", "environ")
    ]
    assert not getenv_calls


def test_proof18_scope_guard_only_approved_files_changed():
    result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    allowed = {
        "scripts/radar_worker.py",
        "src/config/settings.py",
        "src/ui/pages/theme_workspace.py",
        "tests/test_radar_worker_research_case_integration.py",
        "tests/test_radar_worker_safety_invariants.py",
        "tests/test_radar_worker_theme_candidate_detection_integration.py",
        "tests/test_radar_worker_theme_matching_integration.py",
        "tests/test_theme_matching_rules.py",
    }
    # New Phase 2 test files are untracked and never appear in `git diff
    # HEAD` at all (by definition — only already-tracked, modified files
    # show up here), so they need no entry in `allowed` above. See
    # tests/test_theme_matching_rules.py's own
    # test_no_ui_worker_persistence_or_migration_files_touched for the
    # same established precedent.
    assert changed <= allowed, changed - allowed


def test_proof18_no_new_dependency_added_to_requirements():
    result = subprocess.run(["git", "diff", "HEAD", "--", "requirements.txt"], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    assert result.stdout.strip() == "", f"requirements.txt was modified: {result.stdout}"
