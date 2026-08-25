"""Durable-State Phase 4K-1 — scripts/hosted_postgres_candidate_ingest.py
bootstrap tests.

Every test here uses mocks/monkeypatching only. No real DSN, database,
Docker, network call, source-provider request, or secret is ever used —
backend_factory.get_candidate_repository and each source service
module's run_scan are patched at their call boundaries, so no Postgres
connection and no real scan is ever attempted. This file never imports
or calls app.py, container.get_repositories(), review_actions,
signal_promotion, a SignalRepository, or scripts.run_scan.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from scripts import hosted_postgres_candidate_ingest as ingest
from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.dart import radar_service as dart_radar_service
from src.data_access.edgar import edgar_service
from src.data_access.edinet import edinet_service
from src.models.models import CandidateStatus

_LEAK_MARKERS = ("SHOULD_NOT_LEAK_HOST", "SHOULD_NOT_LEAK_DSN", "SHOULD_NOT_LEAK_PASSWORD")
_FAKE_DSN = "postgresql://fake-only-for-this-test/db"
_DSN_VAR_NAME = "TEST_ONLY_ONE_SHOT_DSN_VAR"


def _fake_report(**overrides):
    defaults = dict(
        scan_id="scan-fake-1", bgn_date="2026-01-01", end_date="2026-01-02",
        candidates_detected=0, candidates_processed=0, candidates_deferred=0,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _exec_module_fresh(module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, ingest.__file__)
    fresh_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh_module)
    return fresh_module


# --- Import-time safety: nothing real happens merely by importing ---

def test_importing_module_invokes_no_repository_or_scan_call():
    with patch.object(backend_factory, "get_candidate_repository") as mock_get_repo, \
         patch.object(edgar_service, "run_scan") as mock_edgar_run, \
         patch.object(dart_radar_service, "run_scan") as mock_dart_run, \
         patch.object(edinet_service, "run_scan") as mock_edinet_run:
        _exec_module_fresh("hosted_postgres_candidate_ingest_fresh_import_test")

    mock_get_repo.assert_not_called()
    mock_edgar_run.assert_not_called()
    mock_dart_run.assert_not_called()
    mock_edinet_run.assert_not_called()


# --- Missing/invalid required arguments fail before any call ---

@pytest.mark.parametrize(
    "argv",
    [
        ["--max-candidates", "1", "--dsn-env-var", _DSN_VAR_NAME],  # missing --source
        ["--source", "edgar", "--dsn-env-var", _DSN_VAR_NAME],  # missing --max-candidates
        ["--source", "edgar", "--max-candidates", "1"],  # missing --dsn-env-var
        ["--source", "bogus-source", "--max-candidates", "1", "--dsn-env-var", _DSN_VAR_NAME],  # unsupported source
        ["--source", "edgar", "--max-candidates", "not-an-int", "--dsn-env-var", _DSN_VAR_NAME],  # non-int bound
    ],
)
def test_missing_or_invalid_required_args_exit_before_any_call(argv, capsys):
    with patch.object(backend_factory, "get_candidate_repository") as mock_get_repo, \
         patch.object(edgar_service, "run_scan") as mock_run_scan:
        with pytest.raises(SystemExit) as exc_info:
            ingest.main(argv)

    assert exc_info.value.code != 0
    mock_get_repo.assert_not_called()
    mock_run_scan.assert_not_called()
    capsys.readouterr()  # drain argparse's own usage output, not asserted on


@pytest.mark.parametrize("bad_bound", ["0", "-1", "-5"])
def test_non_positive_max_candidates_fails_before_any_call(bad_bound, capsys):
    with patch.object(backend_factory, "get_candidate_repository") as mock_get_repo, \
         patch.object(edgar_service, "run_scan") as mock_run_scan:
        result = ingest.main(["--source", "edgar", "--max-candidates", bad_bound, "--dsn-env-var", _DSN_VAR_NAME])

    assert result != 0
    mock_get_repo.assert_not_called()
    mock_run_scan.assert_not_called()
    assert "positive" in capsys.readouterr().err


# --- Missing named DSN environment variable fails safely ---

def test_missing_named_dsn_env_var_fails_safely_no_repo_or_scan_call(monkeypatch, capsys):
    monkeypatch.delenv(_DSN_VAR_NAME, raising=False)

    with patch.object(backend_factory, "get_candidate_repository") as mock_get_repo, \
         patch.object(edgar_service, "run_scan") as mock_run_scan:
        result = ingest.main(["--source", "edgar", "--max-candidates", "1", "--dsn-env-var", _DSN_VAR_NAME])

    assert result != 0
    mock_get_repo.assert_not_called()
    mock_run_scan.assert_not_called()
    err = capsys.readouterr().err
    assert _DSN_VAR_NAME in err  # the NAME is safe and expected to appear
    for marker in _LEAK_MARKERS:
        assert marker not in err


# --- Successful mocked invocation ---

def test_successful_mocked_invocation_uses_named_dsn_and_injects_repository(monkeypatch):
    monkeypatch.setenv(_DSN_VAR_NAME, _FAKE_DSN)
    monkeypatch.delenv("EDGE_DB_BACKEND", raising=False)
    monkeypatch.delenv("EDGE_STATE_DB_URL", raising=False)

    fake_repo = MagicMock(name="fake_postgres_candidate_repository")
    fake_repo.load_candidates.return_value = {}
    captured: dict = {}

    def _fake_get_candidate_repository(settings, source):
        captured["settings"] = settings
        captured["source"] = source
        return fake_repo

    with patch.object(backend_factory, "get_candidate_repository", side_effect=_fake_get_candidate_repository) as mock_get_repo, \
         patch.object(edgar_service, "run_scan", return_value=_fake_report(candidates_detected=1, candidates_processed=1)) as mock_edgar_run, \
         patch.object(dart_radar_service, "run_scan") as mock_dart_run, \
         patch.object(edinet_service, "run_scan") as mock_edinet_run:
        result = ingest.main(["--source", "edgar", "--max-candidates", "3", "--dsn-env-var", _DSN_VAR_NAME])

    assert result == 0
    mock_get_repo.assert_called_once()
    settings_used = captured["settings"]
    assert isinstance(settings_used, Settings)
    assert settings_used.db_backend == "postgres"
    assert settings_used.state_db_url == _FAKE_DSN
    assert captured["source"] == "SEC EDGAR"

    mock_edgar_run.assert_called_once()
    call_args, call_kwargs = mock_edgar_run.call_args
    assert call_args[0] is settings_used
    assert call_kwargs["max_candidates"] == 3
    assert call_kwargs["candidate_repository"] is fake_repo

    # Only the selected source's service module is ever touched.
    mock_dart_run.assert_not_called()
    mock_edinet_run.assert_not_called()


def test_ambient_edge_vars_cannot_affect_the_harness(monkeypatch):
    monkeypatch.setenv(_DSN_VAR_NAME, _FAKE_DSN)
    monkeypatch.setenv("EDGE_DB_BACKEND", "sqlite")
    monkeypatch.setenv("EDGE_STATE_DB_URL", "postgresql://poison-should-never-be-used/db")

    captured: dict = {}

    def _fake_get_candidate_repository(settings, source):
        captured["settings"] = settings
        fake_repo = MagicMock()
        fake_repo.load_candidates.return_value = {}
        return fake_repo

    with patch.object(backend_factory, "get_candidate_repository", side_effect=_fake_get_candidate_repository), \
         patch.object(edgar_service, "run_scan", return_value=_fake_report()):
        result = ingest.main(["--source", "edgar", "--max-candidates", "1", "--dsn-env-var", _DSN_VAR_NAME])

    assert result == 0
    assert captured["settings"].db_backend == "postgres"
    assert captured["settings"].state_db_url == _FAKE_DSN


def test_hard_coded_dsn_env_var_name_cannot_substitute_for_the_named_one(monkeypatch):
    """A value sitting under some other, unrelated variable name must
    never be picked up — only the exact name passed via --dsn-env-var."""
    monkeypatch.setenv("EEVA_HOSTED_SIGNALS_PREVIEW_DSN", _FAKE_DSN)  # a different script's own variable
    monkeypatch.delenv(_DSN_VAR_NAME, raising=False)

    with patch.object(backend_factory, "get_candidate_repository") as mock_get_repo, \
         patch.object(edgar_service, "run_scan") as mock_run_scan:
        result = ingest.main(["--source", "edgar", "--max-candidates", "1", "--dsn-env-var", _DSN_VAR_NAME])

    assert result != 0
    mock_get_repo.assert_not_called()
    mock_run_scan.assert_not_called()


# --- No DSN/secret leakage on failure ---

def test_repository_construction_failure_leaks_no_dsn_or_markers(monkeypatch, capsys):
    monkeypatch.setenv(_DSN_VAR_NAME, _FAKE_DSN)
    leaky_exc = RuntimeError(
        "connection failed: SHOULD_NOT_LEAK_HOST=x SHOULD_NOT_LEAK_DSN=y SHOULD_NOT_LEAK_PASSWORD=z"
    )

    with patch.object(backend_factory, "get_candidate_repository", side_effect=leaky_exc), \
         patch.object(edgar_service, "run_scan") as mock_run_scan:
        result = ingest.main(["--source", "edgar", "--max-candidates", "1", "--dsn-env-var", _DSN_VAR_NAME])

    assert result != 0
    mock_run_scan.assert_not_called()
    err = capsys.readouterr().err
    assert _FAKE_DSN not in err
    for marker in _LEAK_MARKERS:
        assert marker not in err
    assert "RuntimeError" in err  # only the exception class name is safe to report


# --- Unexpected PUBLISHED outcome is rejected, not silently accepted ---

def test_unexpected_published_candidate_after_ingestion_is_rejected(monkeypatch, capsys):
    monkeypatch.setenv(_DSN_VAR_NAME, _FAKE_DSN)
    fake_repo = MagicMock()
    fake_repo.load_candidates.return_value = {
        "cand-anomalous": SimpleNamespace(status=CandidateStatus.PUBLISHED),
    }

    with patch.object(backend_factory, "get_candidate_repository", return_value=fake_repo), \
         patch.object(edgar_service, "run_scan", return_value=_fake_report()) as mock_run_scan:
        result = ingest.main(["--source", "edgar", "--max-candidates", "1", "--dsn-env-var", _DSN_VAR_NAME])

    mock_run_scan.assert_called_once()  # the check happens after a real run, not instead of one
    assert result != 0
    assert "PUBLISHED" in capsys.readouterr().err


def test_non_published_candidates_after_ingestion_succeed_normally(monkeypatch):
    monkeypatch.setenv(_DSN_VAR_NAME, _FAKE_DSN)
    fake_repo = MagicMock()
    fake_repo.load_candidates.return_value = {
        "cand-1": SimpleNamespace(status=CandidateStatus.NEEDS_REVIEW),
    }

    with patch.object(backend_factory, "get_candidate_repository", return_value=fake_repo), \
         patch.object(edgar_service, "run_scan", return_value=_fake_report(candidates_detected=1, candidates_processed=1)):
        result = ingest.main(["--source", "edgar", "--max-candidates", "1", "--dsn-env-var", _DSN_VAR_NAME])

    assert result == 0


# --- Structural guards: no review/publish/signal-repository/run_scan path ---

def test_harness_never_imports_review_publish_signal_or_run_scan_module():
    source = Path(ingest.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imported.add(f"{module}.{alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)

    assert not any("review_actions" in name for name in imported)
    assert not any("signal_promotion" in name for name in imported)
    assert not any("SignalRepository" in name for name in imported)
    assert not any("scripts.run_scan" in name for name in imported)
    assert not any("data_access.container" in name for name in imported)
    assert not any(name.endswith(".get_repositories") for name in imported)
    assert not any(name.endswith(".get_settings") for name in imported)
    assert not any(name.endswith(".record_review_decision") for name in imported)


def test_harness_imports_no_streamlit_docker_subprocess_or_scheduler_module():
    """AST-based, not text-based, so this isn't confused by the module's
    own docstring prose describing what it deliberately does NOT do
    (e.g. mentioning "Streamlit" while never importing it)."""
    source = Path(ingest.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported_roots.add(alias.name.split(".")[0])

    for forbidden_root in ("streamlit", "docker", "subprocess", "schedule"):
        assert forbidden_root not in imported_roots
