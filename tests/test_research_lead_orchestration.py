"""EevaResearch Phase 4, Step 4B-1 (design/DECISIONS.md) — pure batch
orchestration coordinating select_research_lead() /
build_research_case_bundle_from_lead() / validate_research_case_bundle()
over a caller-supplied candidate set. Every fixture here is synthetic
and directly constructed; this file never touches persistence, a real
store/repository, a real scan, the network, the UI, an LLM, a source
client, the authoring script, or the real current date. No backend
connection or worker tick is ever created."""
from __future__ import annotations

import ast
import dataclasses
import subprocess
from pathlib import Path

import pytest

from src.logic.research_case_validation import validate_research_case_bundle
from src.logic.research_lead_orchestration import (
    ResearchLeadOrchestrationConfig,
    ResearchLeadOrchestrationResult,
    prepare_research_case_bundles,
)
from src.logic.research_lead_selection import LeadPriority
from src.models.models import (
    CandidateSignal,
    CandidateStatus,
    ExtractionState,
    FilingEvent,
    StateTransition,
)

REPO_ROOT = Path(__file__).parent.parent


def _filing(**overrides) -> FilingEvent:
    defaults = dict(
        rcept_no="acc-1", corp_code="0000320193", corp_name="Apple Inc.", stock_code="AAPL",
        report_nm="8-K", rcept_dt="2026-08-15", flr_nm="Apple Inc.", source_name="SEC EDGAR",
        source_url="https://example.com/filing", retrieved_at="2026-08-15T01:00:00+00:00",
        original_language="English",
    )
    defaults.update(overrides)
    return FilingEvent(**defaults)


def _candidate(**overrides) -> CandidateSignal:
    defaults = dict(
        id="edgar-cand-1", filing=_filing(), matched_rules=["financing_or_debt:2.03", "material_agreement:1.01"],
        confidence="High", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="The company entered into a financing agreement.",
        state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at="2026-08-15T00:00:00+00:00")],
    )
    defaults.update(overrides)
    return CandidateSignal(**defaults)


def _config(**overrides) -> ResearchLeadOrchestrationConfig:
    defaults = dict(as_of_date="2026-08-20", lookback_days=30, max_candidates=5)
    defaults.update(overrides)
    return ResearchLeadOrchestrationConfig(**defaults)


def _no_existing(_ids):
    return set()


class _RecordingChecker:
    def __init__(self, existing=()):
        self.calls: list[list[str]] = []
        self._existing = set(existing)

    def __call__(self, ids):
        self.calls.append(list(ids))
        return self._existing


class _RaisingChecker:
    def __call__(self, _ids):
        raise RuntimeError("boom")


# ============================================================
# Proof 1 — invalid orchestration config fails closed
# ============================================================


@pytest.mark.parametrize("max_candidates", [0, -1, 11, True, False, 1.5, "5", None])
def test_proof1_invalid_max_candidates_fails_closed(max_candidates):
    checker = _RecordingChecker()
    result = prepare_research_case_bundles([_candidate()], checker, _config(max_candidates=max_candidates))
    assert result.config_valid is False
    assert result.bundles == ()
    assert result.evaluated_count == 0
    assert checker.calls == []


@pytest.mark.parametrize("allowed", [(), [], ("",), ("   ",), None, "SEC EDGAR", (123,)])
def test_proof1_invalid_allowed_source_names_fails_closed(allowed):
    checker = _RecordingChecker()
    result = prepare_research_case_bundles([_candidate()], checker, _config(allowed_source_names=allowed))
    assert result.config_valid is False
    assert result.bundles == ()
    assert checker.calls == []


@pytest.mark.parametrize("as_of_date", [None, ""])
def test_proof1_blank_as_of_date_fails_closed_at_orchestration_level(as_of_date):
    checker = _RecordingChecker()
    result = prepare_research_case_bundles([_candidate()], checker, _config(as_of_date=as_of_date))
    assert result.config_valid is False
    assert result.bundles == ()
    assert checker.calls == []


def test_proof1_nonblank_but_malformed_as_of_date_is_orchestration_valid_but_selector_rejects():
    """The orchestration layer only checks as_of_date for blankness —
    format strictness (exact YYYY-MM-DD) is select_research_lead()'s own
    job, per-candidate. A syntactically invalid but nonblank date still
    safely produces zero bundles: config_valid stays True (nothing wrong
    at the orchestration level), and the candidate is rejected via the
    selector's own invalid_as_of_date gate, counted as not_qualified."""
    checker = _RecordingChecker()
    result = prepare_research_case_bundles([_candidate()], checker, _config(as_of_date="not-a-date"))
    assert result.config_valid is True
    assert result.bundles == ()
    assert result.not_qualified_count == 1
    assert checker.calls == []


@pytest.mark.parametrize("lookback_days", [True, False, -1, 1.5, "30"])
def test_proof1_invalid_lookback_days_fails_closed(lookback_days):
    checker = _RecordingChecker()
    result = prepare_research_case_bundles([_candidate()], checker, _config(lookback_days=lookback_days))
    assert result.config_valid is False


def test_proof1_wrong_type_config_fails_closed():
    checker = _RecordingChecker()
    result = prepare_research_case_bundles([_candidate()], checker, "not a config")
    assert result.config_valid is False
    assert result.bundles == ()
    assert result.evaluated_count == 0


def test_proof1_valid_config_reports_config_valid_true():
    result = prepare_research_case_bundles([], _no_existing, _config())
    assert result.config_valid is True


# ============================================================
# Proof 2 — source/status filtering happens before sorting/capping
# ============================================================


def test_proof2_wrong_source_and_wrong_status_never_reach_selector():
    wrong_source = _candidate(id="edgar-cand-2", filing=_filing(rcept_no="acc-2", source_name="OpenDART / DART"))
    wrong_status = _candidate(id="edgar-cand-3", filing=_filing(rcept_no="acc-3"), status=CandidateStatus.MONITORING)
    good = _candidate()
    checker = _RecordingChecker()
    result = prepare_research_case_bundles([wrong_source, wrong_status, good], checker, _config())
    assert result.evaluated_count == 1
    assert result.skipped_count == 2
    assert len(result.bundles) == 1
    assert result.bundles[0].case.trigger_source_id == good.id


def test_proof2_wrong_shaped_candidates_counted_as_skipped_not_not_qualified():
    checker = _RecordingChecker()
    result = prepare_research_case_bundles([None, "not a candidate", 123, _candidate()], checker, _config())
    assert result.skipped_count == 3
    assert result.not_qualified_count == 0
    assert result.evaluated_count == 1


# ============================================================
# Proof 3 — deterministic sort and cap
# ============================================================


def test_proof3_sort_and_cap_select_exact_expected_candidates_from_a_set_larger_than_ten():
    candidates = []
    for i in range(15):
        rcept_no = f"acc-{i:02d}"
        rcept_dt = f"2026-08-{(i % 28) + 1:02d}"
        candidates.append(_candidate(
            id=f"edgar-cand-{rcept_no}", filing=_filing(rcept_no=rcept_no, rcept_dt=rcept_dt),
            state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at=f"{rcept_dt}T00:00:00+00:00")],
        ))
    expected_order = sorted(candidates, key=lambda c: (c.filing.rcept_dt, c.filing.rcept_no, c.id))
    expected_capped_ids = {c.id for c in expected_order[:5]}

    checker = _RecordingChecker()
    result = prepare_research_case_bundles(candidates, checker, _config(max_candidates=5, lookback_days=60))
    assert result.evaluated_count == 5
    assert {b.case.trigger_source_id for b in result.bundles} == expected_capped_ids


def test_proof3_sort_handles_malformed_fields_with_safe_fallback():
    malformed = dataclasses.replace(_candidate(id="edgar-cand-bad"), filing=dataclasses.replace(_filing(rcept_no="acc-bad", rcept_dt=None)))
    good = _candidate()
    checker = _RecordingChecker()
    result = prepare_research_case_bundles([malformed, good], checker, _config())
    # Must not raise; malformed rcept_dt sorts as "" (before any real date).
    assert result.evaluated_count == 2


# ============================================================
# Proof 4 — selector called exactly once per evaluated candidate, empty frozenset
# ============================================================


def test_proof4_selector_called_once_per_candidate_with_empty_frozenset(monkeypatch):
    from src.logic import research_lead_orchestration

    calls = []
    real_select = research_lead_orchestration.select_research_lead

    def _tracking(candidate, already_triggered, config):
        calls.append((candidate.id, already_triggered))
        return real_select(candidate, already_triggered, config)

    monkeypatch.setattr(research_lead_orchestration, "select_research_lead", _tracking)
    prepare_research_case_bundles([_candidate()], _no_existing, _config())
    assert len(calls) == 1
    assert calls[0][1] == frozenset()


# ============================================================
# Proof 5 — HIGH_SIGNAL and QUALIFIED both survive; NOT_QUALIFIED does not
# ============================================================


def test_proof5_high_signal_and_qualified_survive_not_qualified_does_not():
    high_signal = _candidate(id="edgar-cand-hs", confidence="High", filing=_filing(rcept_no="acc-hs"),
                              state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at="2026-08-15T00:00:00+00:00")])
    qualified = _candidate(id="edgar-cand-q", confidence="Moderate", matched_rules=["financing_or_debt:2.03"],
                            filing=_filing(rcept_no="acc-q"),
                            state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at="2026-08-15T00:00:00+00:00")])
    not_qualified = _candidate(id="edgar-cand-nq", status=CandidateStatus.MONITORING, filing=_filing(rcept_no="acc-nq"))

    checker = _RecordingChecker()
    result = prepare_research_case_bundles([high_signal, qualified, not_qualified], checker, _config())
    # not_qualified is filtered by status before reaching the selector -> skipped, not not_qualified.
    assert result.skipped_count == 1
    assert result.evaluated_count == 2
    assert len(result.bundles) == 2
    assert result.not_qualified_count == 0


def test_proof5_selector_not_qualified_result_excluded():
    stale = _candidate(filing=_filing(rcept_dt="2026-01-01"))
    checker = _RecordingChecker()
    result = prepare_research_case_bundles([stale], checker, _config())
    assert result.evaluated_count == 1
    assert result.not_qualified_count == 1
    assert result.bundles == ()
    assert checker.calls == []


# ============================================================
# Proof 6 — membership callable called exactly once, ordered case IDs
# ============================================================


def test_proof6_membership_callable_called_once_with_ordered_case_ids():
    a = _candidate(id="edgar-cand-a", filing=_filing(rcept_no="acc-a", rcept_dt="2026-08-10"),
                   state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at="2026-08-10T00:00:00+00:00")])
    b = _candidate(id="edgar-cand-b", filing=_filing(rcept_no="acc-b", rcept_dt="2026-08-12"),
                   state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at="2026-08-12T00:00:00+00:00")])
    checker = _RecordingChecker()
    result = prepare_research_case_bundles([b, a], checker, _config())
    assert len(checker.calls) == 1
    assert len(checker.calls[0]) == 2
    # Order matches candidate evaluation order (a before b, since a's rcept_dt is earlier).
    assert checker.calls[0] == [bundle.case.id for bundle in result.bundles]


def test_proof6_zero_calls_when_no_survivors():
    checker = _RecordingChecker()
    stale = _candidate(filing=_filing(rcept_dt="2026-01-01"))
    prepare_research_case_bundles([stale], checker, _config())
    assert checker.calls == []


# ============================================================
# Proof 7 — existing IDs excluded, order preserved for the rest
# ============================================================


def test_proof7_existing_ids_excluded_others_preserve_order_and_count():
    a = _candidate(id="edgar-cand-a", filing=_filing(rcept_no="acc-a", rcept_dt="2026-08-10"),
                   state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at="2026-08-10T00:00:00+00:00")])
    b = _candidate(id="edgar-cand-b", filing=_filing(rcept_no="acc-b", rcept_dt="2026-08-12"),
                   state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at="2026-08-12T00:00:00+00:00")])

    from src.logic.research_lead_selection import ResearchLeadSelectionConfig, select_research_lead
    selector_config = ResearchLeadSelectionConfig(as_of_date="2026-08-20", lookback_days=30, recognized_source_names=("SEC EDGAR",))
    a_case_id = select_research_lead(a, frozenset(), selector_config).case_id

    checker = _RecordingChecker(existing={a_case_id})
    result = prepare_research_case_bundles([a, b], checker, _config())
    assert result.already_existing_count == 1
    assert len(result.bundles) == 1
    assert result.bundles[0].case.trigger_source_id == b.id


# ============================================================
# Proof 8 — membership failure fails closed
# ============================================================


def test_proof8_membership_callable_exception_fails_closed():
    result = prepare_research_case_bundles([_candidate()], _RaisingChecker(), _config())
    assert result.bundles == ()
    assert result.membership_check_failed_count == 1
    assert result.already_existing_count == 0


@pytest.mark.parametrize("malformed_result", [None, 123, "a string", [123, 456], {1: "a"}])
def test_proof8_malformed_membership_result_fails_closed(malformed_result):
    result = prepare_research_case_bundles([_candidate()], lambda ids: malformed_result, _config())
    assert result.bundles == ()
    assert result.membership_check_failed_count == 1


def test_proof8_membership_failure_never_calls_factory_or_validation(monkeypatch):
    from src.logic import research_lead_orchestration

    def _forbidden(*_a, **_k):
        raise AssertionError("must not be called after a membership failure")

    monkeypatch.setattr(research_lead_orchestration, "build_research_case_bundle_from_lead", _forbidden)
    monkeypatch.setattr(research_lead_orchestration, "validate_research_case_bundle", _forbidden)
    result = prepare_research_case_bundles([_candidate()], _RaisingChecker(), _config())
    assert result.bundles == ()


# ============================================================
# Proof 9 — factory called once only for non-existing survivors
# ============================================================


def test_proof9_factory_called_once_per_non_existing_survivor(monkeypatch):
    from src.logic import research_lead_orchestration

    calls = []
    real_factory = research_lead_orchestration.build_research_case_bundle_from_lead

    def _tracking(candidate, selection):
        calls.append(candidate.id)
        return real_factory(candidate, selection)

    monkeypatch.setattr(research_lead_orchestration, "build_research_case_bundle_from_lead", _tracking)
    result = prepare_research_case_bundles([_candidate()], _no_existing, _config())
    assert calls == ["edgar-cand-1"]
    assert len(result.bundles) == 1


def test_proof9_factory_none_increments_factory_rejected_count(monkeypatch):
    from src.logic import research_lead_orchestration

    monkeypatch.setattr(research_lead_orchestration, "build_research_case_bundle_from_lead", lambda candidate, selection: None)
    result = prepare_research_case_bundles([_candidate()], _no_existing, _config())
    assert result.factory_rejected_count == 1
    assert result.bundles == ()


# ============================================================
# Proof 10 — validation called once only for factory-produced bundles
# ============================================================


def test_proof10_validation_called_once_per_factory_produced_bundle(monkeypatch):
    from src.logic import research_lead_orchestration

    calls = []
    real_validate = research_lead_orchestration.validate_research_case_bundle

    def _tracking(bundle):
        calls.append(bundle)
        return real_validate(bundle)

    monkeypatch.setattr(research_lead_orchestration, "validate_research_case_bundle", _tracking)
    result = prepare_research_case_bundles([_candidate()], _no_existing, _config())
    assert len(calls) == 1
    assert len(result.bundles) == 1


def test_proof10_invalid_bundle_increments_validation_rejected_and_is_not_returned(monkeypatch):
    from src.logic import research_lead_orchestration
    from src.logic.research_case_validation import ResearchCaseValidationError

    monkeypatch.setattr(
        research_lead_orchestration, "validate_research_case_bundle",
        lambda bundle: (ResearchCaseValidationError(code="fake_error", message="fake"),),
    )
    result = prepare_research_case_bundles([_candidate()], _no_existing, _config())
    assert result.validation_rejected_count == 1
    assert result.bundles == ()


# ============================================================
# Proof 11 — valid returned bundles are exact factory results, order preserved
# ============================================================


def test_proof11_valid_bundles_match_exact_factory_results_and_order():
    a = _candidate(id="edgar-cand-a", filing=_filing(rcept_no="acc-a", rcept_dt="2026-08-10"),
                   state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at="2026-08-10T00:00:00+00:00")])
    b = _candidate(id="edgar-cand-b", filing=_filing(rcept_no="acc-b", rcept_dt="2026-08-12"),
                   state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at="2026-08-12T00:00:00+00:00")])
    result = prepare_research_case_bundles([b, a], _no_existing, _config())
    assert [bundle.case.trigger_source_id for bundle in result.bundles] == [a.id, b.id]
    for bundle in result.bundles:
        assert validate_research_case_bundle(bundle) == ()
        assert bundle.assertions == ()


# ============================================================
# Proof 12 — no mutation of inputs
# ============================================================


def test_proof12_candidate_config_and_collection_unchanged_after_invocation():
    candidate = _candidate()
    candidates = [candidate]
    filing_before = dataclasses.replace(candidate.filing)
    state_history_before = list(candidate.state_history)
    config = _config()
    config_before = dataclasses.replace(config)
    checker = _RecordingChecker()

    prepare_research_case_bundles(candidates, checker, config)

    assert candidates == [candidate]
    assert candidate.filing == filing_before
    assert candidate.state_history == state_history_before
    assert config == config_before


# ============================================================
# Proof 13 — zero assertions, no relationship/dependency/bottleneck fields
# ============================================================


def test_proof13_no_assertion_relationship_dependency_or_bottleneck_fields():
    result = prepare_research_case_bundles([_candidate()], _no_existing, _config())
    assert len(result.bundles) == 1
    bundle = result.bundles[0]
    assert bundle.assertions == ()
    for field_name in ("relationship_assertions", "dependency_assertions", "bottleneck_type"):
        assert not hasattr(bundle.case, field_name)


# ============================================================
# Proof 14 — AST/scope: no forbidden imports/calls
# ============================================================


def test_proof14_module_imports_only_approved_stdlib_and_project_modules():
    source = (REPO_ROOT / "src" / "logic" / "research_lead_orchestration.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="research_lead_orchestration.py")
    allowed_stdlib = {"__future__", "dataclasses", "typing"}
    allowed_project_modules = {
        "src.logic.research_case_validation", "src.logic.research_lead_factory",
        "src.logic.research_lead_selection", "src.models.models",
    }
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top not in allowed_stdlib and alias.name not in allowed_project_modules:
                    offenders.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".")[0]
            if top not in allowed_stdlib and node.module not in allowed_project_modules:
                offenders.append(node.module)
    assert not offenders, offenders


def test_proof14_module_never_calls_wall_clock_random_or_persistence():
    source = (REPO_ROOT / "src" / "logic" / "research_lead_orchestration.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="research_lead_orchestration.py")
    offenders = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and (
            (n.func.attr in ("now", "today", "utcnow") and isinstance(n.func.value, ast.Name) and n.func.value.id in ("datetime", "date"))
            or (n.func.attr == "time" and isinstance(n.func.value, ast.Name) and n.func.value.id == "time")
            or (isinstance(n.func.value, ast.Name) and n.func.value.id in ("os", "random"))
        )
    ]
    assert not offenders, offenders


def test_proof14_module_never_calls_or_imports_persistence_functions():
    """AST-based, not substring-based, so the module's own docstring
    prose explaining that it never imports backend_factory can't
    false-positive this check the way a naive substring scan would."""
    source = (REPO_ROOT / "src" / "logic" / "research_lead_orchestration.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="research_lead_orchestration.py")
    forbidden_names = (
        "append_research_case_bundle", "insert_research_case_bundle", "insert_bundle",
        "backend_factory", "get_research_case_repository", "get_research_case_bundle_writer",
    )
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders.extend(a.name for a in node.names if any(f in a.name for f in forbidden_names))
        elif isinstance(node, ast.ImportFrom) and node.module:
            if any(f in node.module for f in forbidden_names):
                offenders.append(node.module)
            offenders.extend(a.name for a in node.names if any(f in a.name for f in forbidden_names))
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else None)
            if name and any(f in name for f in forbidden_names):
                offenders.append(name)
    assert not offenders, offenders


# ============================================================
# Proof 17/18 — no runtime entry point involvement, scope guard
# ============================================================


def test_proof17_no_runtime_entry_point_references_orchestration_module():
    """`scripts/radar_worker.py` is deliberately excluded from this list
    as of Phase 4, Step 4B-2 (design/DECISIONS.md) — that step wires the
    EDGAR-only autonomous Research Case step into
    _run_provider_tick(), a real, intentional, separately-approved
    reference to this module. Every other runtime entry point must
    still never reference it."""
    candidate_files = [
        "scripts/run_scan.py", "scripts/create_research_case.py", "app.py",
        "src/ui/pages/research_cases.py", "src/ui/pages/radar_inbox.py", "src/ui/pages/daily_news.py",
    ]
    offenders = []
    for rel_path in candidate_files:
        full_path = REPO_ROOT / rel_path
        if not full_path.exists():
            continue
        source = full_path.read_text(encoding="utf-8")
        if "research_lead_orchestration" in source or "prepare_research_case_bundles" in source:
            offenders.append(rel_path)
    assert not offenders, offenders


def test_proof18_scope_guard_only_approved_files_changed():
    result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    allowed = {
        "src/data_access/backend_factory.py",
        "src/data_access/state_db/research_repository.py",
        "src/data_access/postgres_state_db/research_repository.py",
    }
    assert changed <= allowed, changed - allowed


def test_config_result_repr_smoke():
    """Basic sanity that the frozen result dataclass is constructible
    and equality-comparable, used throughout this file's own asserts."""
    result_a = prepare_research_case_bundles([], _no_existing, _config())
    result_b = prepare_research_case_bundles([], _no_existing, _config())
    assert result_a == result_b
    assert isinstance(result_a, ResearchLeadOrchestrationResult)
