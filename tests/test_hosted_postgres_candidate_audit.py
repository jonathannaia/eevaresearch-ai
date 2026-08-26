"""Durable-State Phase 4L-1 — scripts/hosted_postgres_candidate_audit.py
tests.

Every test here uses mocks/monkeypatching and fake values only. No real
DSN, database, Docker, network call, source-provider request, or secret
is ever used — backend_factory.get_candidate_repository is patched at
its call boundary, so no Postgres connection is ever attempted. Fake
candidate objects use the real declared CandidateSignal/FilingEvent
attribute shapes (a plain-string `confidence`, an enum-or-plain-string
`status` with no `.value` in the fake case) — never guessed attributes
like `.version`, `.filed_at`, or `.form_type`. This file never imports
or calls app.py, container.get_repositories(), review_actions,
signal_promotion, a SignalRepository, any source client/service/
pipeline, scripts.run_scan, scripts.hosted_postgres_candidate_ingest, or
scripts.hosted_signals_preview.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from scripts import hosted_postgres_candidate_audit as audit
from src.config.settings import Settings
from src.data_access import backend_factory

_LEAK_MARKERS = ("SHOULD_NOT_LEAK_HOST", "SHOULD_NOT_LEAK_DSN", "SHOULD_NOT_LEAK_PASSWORD")
_FAKE_DSN = "postgresql://fake-only-for-this-test/db"
_DSN_VAR_NAME = "TEST_ONLY_ONE_SHOT_READ_DSN_VAR"


def _fake_filing(**overrides):
    defaults = dict(rcept_dt="2026-01-01", pblntf_ty="8-K", stock_code="RKLB", corp_name="Rocket Lab")
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_candidate(candidate_id="cand-1", **overrides):
    filing = overrides.pop("filing", _fake_filing())
    defaults = dict(
        id=candidate_id, filing=filing, status="Needs review", confidence="Moderate",
        matched_rules=["earnings:8-K item 2.02"],
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _exec_module_fresh(module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, audit.__file__)
    fresh_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh_module)
    return fresh_module


# --- Import-time safety: nothing real happens merely by importing ---

def test_importing_module_invokes_no_repository_or_load_call():
    with patch.object(backend_factory, "get_candidate_repository") as mock_get_repo:
        _exec_module_fresh("hosted_postgres_candidate_audit_fresh_import_test")

    mock_get_repo.assert_not_called()


# --- Missing/invalid required arguments fail before any call ---

@pytest.mark.parametrize(
    "argv",
    [
        ["--dsn-env-var", _DSN_VAR_NAME],  # missing --source
        ["--source", "edgar"],  # missing --dsn-env-var
        ["--source", "bogus-source", "--dsn-env-var", _DSN_VAR_NAME],  # unsupported source
    ],
)
def test_missing_or_invalid_required_args_exit_before_any_call(argv, capsys):
    with patch.object(backend_factory, "get_candidate_repository") as mock_get_repo:
        with pytest.raises(SystemExit) as exc_info:
            audit.main(argv)

    assert exc_info.value.code != 0
    mock_get_repo.assert_not_called()
    capsys.readouterr()  # drain argparse's own usage output, not asserted on


# --- Missing named DSN environment variable fails safely ---

def test_missing_named_dsn_env_var_fails_safely_no_repo_call(monkeypatch, capsys):
    monkeypatch.delenv(_DSN_VAR_NAME, raising=False)

    with patch.object(backend_factory, "get_candidate_repository") as mock_get_repo:
        result = audit.main(["--source", "edgar", "--dsn-env-var", _DSN_VAR_NAME])

    assert result != 0
    mock_get_repo.assert_not_called()
    err = capsys.readouterr().err
    assert _DSN_VAR_NAME in err  # the NAME is safe and expected to appear
    for marker in _LEAK_MARKERS:
        assert marker not in err


# --- Successful mocked invocation ---

def test_successful_mocked_invocation_reads_named_dsn_and_calls_only_load(monkeypatch):
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

    with patch.object(backend_factory, "get_candidate_repository", side_effect=_fake_get_candidate_repository) as mock_get_repo:
        result = audit.main(["--source", "edgar", "--dsn-env-var", _DSN_VAR_NAME])

    assert result == 0
    mock_get_repo.assert_called_once()
    settings_used = captured["settings"]
    assert isinstance(settings_used, Settings)
    assert settings_used.db_backend == "postgres"
    assert settings_used.state_db_url == _FAKE_DSN
    assert captured["source"] == "SEC EDGAR"

    fake_repo.load_candidates.assert_called_once()
    fake_repo.update_candidate.assert_not_called()
    fake_repo.upsert_new_candidates.assert_not_called()
    fake_repo.get_candidate.assert_not_called()
    fake_repo.get_candidate_version.assert_not_called()


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

    with patch.object(backend_factory, "get_candidate_repository", side_effect=_fake_get_candidate_repository):
        result = audit.main(["--source", "edgar", "--dsn-env-var", _DSN_VAR_NAME])

    assert result == 0
    assert captured["settings"].db_backend == "postgres"
    assert captured["settings"].state_db_url == _FAKE_DSN


def test_hard_coded_dsn_env_var_name_cannot_substitute_for_the_named_one(monkeypatch):
    monkeypatch.setenv("EEVA_HOSTED_SIGNALS_PREVIEW_DSN", _FAKE_DSN)  # a different script's own variable
    monkeypatch.delenv(_DSN_VAR_NAME, raising=False)

    with patch.object(backend_factory, "get_candidate_repository") as mock_get_repo:
        result = audit.main(["--source", "edgar", "--dsn-env-var", _DSN_VAR_NAME])

    assert result != 0
    mock_get_repo.assert_not_called()


# --- Output schema, sort order, and safe field normalization ---

def test_output_follows_exact_compact_schema(monkeypatch, capsys):
    monkeypatch.setenv(_DSN_VAR_NAME, _FAKE_DSN)
    candidate = _fake_candidate(
        candidate_id="cand-1",
        status="Needs review",
        confidence="Moderate",
        matched_rules=["earnings:8-K item 2.02", "material_agreement:8-K item 1.01"],
        filing=_fake_filing(rcept_dt="2026-01-05", pblntf_ty="8-K", stock_code="RKLB"),
    )
    fake_repo = MagicMock()
    fake_repo.load_candidates.return_value = {"cand-1": candidate}

    with patch.object(backend_factory, "get_candidate_repository", return_value=fake_repo):
        result = audit.main(["--source", "edgar", "--dsn-env-var", _DSN_VAR_NAME])

    assert result == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "CANDIDATES=1"
    assert out[1] == (
        "id=cand-1 status=Needs review form=8-K filed=2026-01-05 "
        "ticker=RKLB confidence=Moderate rules=earnings:8-K item 2.02,material_agreement:8-K item 1.01"
    )


def test_status_and_confidence_rendered_without_using_dot_value():
    """The exact regression this phase's own requirements guard
    against: a fake status/confidence with no .value attribute at all
    must not raise AttributeError, and a real CandidateStatus enum
    instance must render its plain value, not the ugly qualified
    Enum.__str__ form."""
    from src.models.models import CandidateStatus

    class _NoValueStr(str):
        """A str subclass that raises if .value is ever accessed —
        proves the renderer never touches it."""
        @property
        def value(self):
            raise AssertionError(".value should never be accessed")

    fake_status = _NoValueStr("Needs review")
    fake_confidence = _NoValueStr("Moderate")
    candidate = _fake_candidate(status=fake_status, confidence=fake_confidence)
    line = audit._format_candidate_line(candidate)
    assert "status=Needs review" in line
    assert "confidence=Moderate" in line

    real_status_candidate = _fake_candidate(status=CandidateStatus.PUBLISHED, confidence="High")
    real_line = audit._format_candidate_line(real_status_candidate)
    assert "status=Published" in real_line
    assert "CandidateStatus" not in real_line


def test_deterministic_sort_order_by_rcept_dt_then_id(monkeypatch, capsys):
    monkeypatch.setenv(_DSN_VAR_NAME, _FAKE_DSN)
    c_later_a = _fake_candidate("cand-b", filing=_fake_filing(rcept_dt="2026-01-10"))
    c_later_b = _fake_candidate("cand-a", filing=_fake_filing(rcept_dt="2026-01-10"))
    c_earlier = _fake_candidate("cand-z", filing=_fake_filing(rcept_dt="2026-01-01"))
    fake_repo = MagicMock()
    fake_repo.load_candidates.return_value = {c.id: c for c in [c_later_a, c_later_b, c_earlier]}

    with patch.object(backend_factory, "get_candidate_repository", return_value=fake_repo):
        result = audit.main(["--source", "edgar", "--dsn-env-var", _DSN_VAR_NAME])

    assert result == 0
    out = capsys.readouterr().out.splitlines()
    ids_in_order = [line.split()[0] for line in out[1:]]
    assert ids_in_order == ["id=cand-z", "id=cand-a", "id=cand-b"]


def test_ticker_filter_matches_stock_code_case_insensitively_only(monkeypatch, capsys):
    monkeypatch.setenv(_DSN_VAR_NAME, _FAKE_DSN)
    matching = _fake_candidate("cand-match", filing=_fake_filing(stock_code="RKLB", corp_name="Unrelated Co"))
    non_matching_by_stock_code = _fake_candidate(
        "cand-no-match", filing=_fake_filing(stock_code="NVDA", corp_name="Contains RKLB in name only"),
    )
    fake_repo = MagicMock()
    fake_repo.load_candidates.return_value = {c.id: c for c in [matching, non_matching_by_stock_code]}

    with patch.object(backend_factory, "get_candidate_repository", return_value=fake_repo):
        result = audit.main(["--source", "edgar", "--dsn-env-var", _DSN_VAR_NAME, "--ticker", "rklb"])

    assert result == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "CANDIDATES=1"
    assert "id=cand-match" in out[1]


def test_missing_empty_fields_render_as_unknown_or_none_without_exception(monkeypatch, capsys):
    monkeypatch.setenv(_DSN_VAR_NAME, _FAKE_DSN)
    candidate = _fake_candidate(
        status=None, confidence="", matched_rules=[],
        filing=_fake_filing(pblntf_ty="", stock_code=None),
    )
    fake_repo = MagicMock()
    fake_repo.load_candidates.return_value = {candidate.id: candidate}

    with patch.object(backend_factory, "get_candidate_repository", return_value=fake_repo):
        result = audit.main(["--source", "edgar", "--dsn-env-var", _DSN_VAR_NAME])

    assert result == 0
    out = capsys.readouterr().out.splitlines()
    line = out[1]
    assert "status=unknown" in line
    assert "confidence=unknown" in line
    assert "form=unknown" in line
    assert "ticker=unknown" in line
    assert "rules=none" in line


# --- Repository failure: generic message only, no leakage ---

def test_repository_failure_produces_only_generic_message_no_leakage(monkeypatch, capsys):
    monkeypatch.setenv(_DSN_VAR_NAME, _FAKE_DSN)
    leaky_exc = RuntimeError(
        "connection failed: SHOULD_NOT_LEAK_HOST=x SHOULD_NOT_LEAK_DSN=y SHOULD_NOT_LEAK_PASSWORD=z"
    )

    with patch.object(backend_factory, "get_candidate_repository", side_effect=leaky_exc):
        result = audit.main(["--source", "edgar", "--dsn-env-var", _DSN_VAR_NAME])

    assert result != 0
    err = capsys.readouterr().err
    assert err.strip() == "AUDIT_STOP: hosted candidate repository could not be read."
    assert _FAKE_DSN not in err
    for marker in _LEAK_MARKERS:
        assert marker not in err
    assert "RuntimeError" not in err  # not even the exception class name — a fixed generic message only


def test_load_candidates_failure_also_produces_only_generic_message(monkeypatch, capsys):
    monkeypatch.setenv(_DSN_VAR_NAME, _FAKE_DSN)
    fake_repo = MagicMock()
    fake_repo.load_candidates.side_effect = RuntimeError("SHOULD_NOT_LEAK_PASSWORD=z")

    with patch.object(backend_factory, "get_candidate_repository", return_value=fake_repo):
        result = audit.main(["--source", "edgar", "--dsn-env-var", _DSN_VAR_NAME])

    assert result != 0
    err = capsys.readouterr().err
    assert err.strip() == "AUDIT_STOP: hosted candidate repository could not be read."
    for marker in _LEAK_MARKERS:
        assert marker not in err


# --- Structural guards: no review/publish/signal/scan/other-script path ---

def test_harness_never_imports_forbidden_modules():
    source = Path(audit.__file__).read_text(encoding="utf-8")
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
    assert not any("hosted_postgres_candidate_ingest" in name for name in imported)
    assert not any("hosted_signals_preview" in name for name in imported)
    assert not any("data_access.container" in name for name in imported)
    assert not any(name.endswith(".get_repositories") for name in imported)
    assert not any(name.endswith(".get_settings") for name in imported)
    assert not any(name.endswith(".record_review_decision") for name in imported)
    assert not any("cik_resolver" in name for name in imported)
    assert not any("edgar_service" in name for name in imported)
    assert not any("edgar_pipeline" in name for name in imported)
    assert not any("dart" in name.lower() and "radar_service" in name for name in imported)
    assert not any("edinet_service" in name for name in imported)


def test_harness_imports_no_streamlit_docker_subprocess_or_scheduler_module():
    """AST-based, not text-based, so this isn't confused by the module's
    own docstring prose describing what it deliberately does NOT do."""
    source = Path(audit.__file__).read_text(encoding="utf-8")
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
