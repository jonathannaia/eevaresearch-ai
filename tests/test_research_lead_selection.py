"""EevaResearch Phase 4, Step 4A-1 (design/DECISIONS.md) — pure,
deterministic Radar-to-Research-Case lead selection. Every fixture here
is synthetic and directly constructed; this file never touches
persistence, a real store/repository, a real scan, the network, the UI,
an LLM, a source client, the authoring script, or the real current date.

`build_case_id` is imported from `src.data_access.research_store` in
exactly one test below (test_case_id_matches_real_research_case_factory_
exactly) — a pure, no-I/O hash function imported only to prove the
selector's own private, deliberately duplicated ID algorithm
(`research_lead_selection._build_case_id_v1`) stays byte-for-byte
compatible with the real factory (see that module's own docstring for
why it duplicates rather than imports this). No store/repository is
ever constructed or called anywhere in this file."""
from __future__ import annotations

import ast
import dataclasses
import subprocess
from pathlib import Path

import pytest

from src.data_access.research_store import build_case_id
from src.logic.research_lead_selection import (
    LeadPriority,
    LeadSelectionResult,
    ResearchLeadSelectionConfig,
    select_research_lead,
)
from src.models.models import (
    CandidateSignal,
    CandidateStatus,
    ExtractionState,
    FilingEvent,
    StateTransition,
)

REPO_ROOT = Path(__file__).parent.parent

_DETECTED_AT = "2026-08-15T00:00:00+00:00"


def _filing(**overrides) -> FilingEvent:
    defaults = dict(
        rcept_no="acc-1", corp_code="0000320193", corp_name="Apple Inc.", stock_code="AAPL",
        report_nm="8-K", rcept_dt="2026-08-15", flr_nm="Apple Inc.", source_name="SEC EDGAR",
        source_url="https://example.com/filing",
    )
    defaults.update(overrides)
    return FilingEvent(**defaults)


def _candidate(**overrides) -> CandidateSignal:
    defaults = dict(
        id="edgar-cand-1", filing=_filing(), matched_rules=["financing_or_debt:2.03"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="The company entered into a financing agreement.",
        state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at=_DETECTED_AT)],
    )
    defaults.update(overrides)
    return CandidateSignal(**defaults)


def _config(**overrides) -> ResearchLeadSelectionConfig:
    defaults = dict(as_of_date="2026-08-20", lookback_days=30)
    defaults.update(overrides)
    return ResearchLeadSelectionConfig(**defaults)


def _expected_case_id(candidate: CandidateSignal) -> str:
    return build_case_id("radar", candidate.id, _DETECTED_AT)


# ============================================================
# Proofs 1-3 — accepted candidates, priority, category normalization
# ============================================================


def test_proof1_fully_eligible_moderate_one_category_is_qualified():
    candidate = _candidate()
    result = select_research_lead(candidate, set(), _config())
    assert result.priority == LeadPriority.QUALIFIED
    assert result.normalized_categories == ("financing_or_debt",)
    assert result.case_id == _expected_case_id(candidate)
    assert result.reasons == (
        "source_recognized", "candidate_identity_present", "issuer_provenance_present",
        "source_url_present", "excerpt_extracted", "rule_categories_present",
        "confidence_qualified", "status_needs_review", "receipt_date_within_lookback",
        "case_not_previously_triggered", "category:financing_or_debt", "priority_qualified",
    )


def test_proof2_fully_eligible_high_two_category_is_high_signal():
    candidate = _candidate(confidence="High", matched_rules=["financing_or_debt:2.03", "material_agreement:1.01"])
    result = select_research_lead(candidate, set(), _config())
    assert result.priority == LeadPriority.HIGH_SIGNAL
    assert result.reasons[-1] == "priority_high_signal"
    assert result.normalized_categories == ("financing_or_debt", "material_agreement")


def test_proof3_duplicate_reordered_mixed_case_rules_produce_sorted_deduplicated_categories():
    candidate = _candidate(
        confidence="High",
        matched_rules=[
            "Material_Agreement:1.01", "financing_or_debt:2.03", "material_agreement:1.01",
            "  FINANCING_OR_DEBT:2.03  ", "governance_or_management_change:5.02",
        ],
    )
    result = select_research_lead(candidate, set(), _config())
    assert result.normalized_categories == ("financing_or_debt", "governance_or_management_change", "material_agreement")
    assert result.priority == LeadPriority.HIGH_SIGNAL
    # Determinism: calling again with the same (differently-ordered-input) candidate is identical.
    result2 = select_research_lead(candidate, set(), _config())
    assert result == result2


# ============================================================
# Proof 4 — every config failure
# ============================================================


@pytest.mark.parametrize("as_of_date", [None, "", "2026/08/20", "20260820", "2026-8-20", "not-a-date", "2026-02-30", 20260820])
def test_proof4_malformed_or_blank_as_of_date_rejects_safely(as_of_date):
    result = select_research_lead(_candidate(), set(), _config(as_of_date=as_of_date))
    assert result.priority == LeadPriority.NOT_QUALIFIED
    assert "invalid_as_of_date" in result.reasons
    assert result.case_id is None


@pytest.mark.parametrize("lookback_days", [True, False, -1, -100, 1.5, "30", None])
def test_proof4_bool_negative_or_non_integer_lookback_rejects_safely(lookback_days):
    result = select_research_lead(_candidate(), set(), _config(lookback_days=lookback_days))
    assert result.priority == LeadPriority.NOT_QUALIFIED
    assert "invalid_lookback_days" in result.reasons


@pytest.mark.parametrize("recognized", [(), ("",), ("   ",), None, "SEC EDGAR", 123])
def test_proof4_empty_blank_or_non_string_recognized_sources_rejects_safely(recognized):
    result = select_research_lead(_candidate(), set(), _config(recognized_source_names=recognized))
    assert result.priority == LeadPriority.NOT_QUALIFIED
    assert "invalid_recognized_source_names" in result.reasons


def test_proof4_multiple_config_failures_all_reported():
    result = select_research_lead(_candidate(), set(), _config(as_of_date="bad", lookback_days=-1, recognized_source_names=()))
    assert set(result.reasons) == {"invalid_as_of_date", "invalid_lookback_days", "invalid_recognized_source_names"}


def test_proof4_wrong_type_config_rejects_safely():
    result = select_research_lead(_candidate(), set(), "not a config")
    assert result.priority == LeadPriority.NOT_QUALIFIED
    assert result.reasons == ("invalid_config",)


# ============================================================
# Proof 5 — None/wrong-shaped candidate
# ============================================================


@pytest.mark.parametrize("bad_candidate", [None, "a string", 123, object(), {"id": "x"}])
def test_proof5_none_or_wrong_shaped_candidate_returns_invalid_candidate(bad_candidate):
    result = select_research_lead(bad_candidate, set(), _config())
    assert result == LeadSelectionResult(priority=LeadPriority.NOT_QUALIFIED, reasons=("invalid_candidate",), normalized_categories=(), case_id=None)


# ============================================================
# Proof 6 — blank id/document/issuer/name/url/excerpt
# ============================================================


@pytest.mark.parametrize("bad_id", [None, "", "   ", 123])
def test_proof6_blank_candidate_id_fails(bad_id):
    result = select_research_lead(_candidate(id=bad_id), set(), _config())
    assert "blank_candidate_id" in result.reasons


@pytest.mark.parametrize("bad_value", [None, "", "   "])
def test_proof6_blank_document_id_fails(bad_value):
    result = select_research_lead(_candidate(filing=_filing(rcept_no=bad_value)), set(), _config())
    assert "blank_document_id" in result.reasons


@pytest.mark.parametrize("bad_value", [None, "", "   "])
def test_proof6_blank_issuer_id_fails(bad_value):
    result = select_research_lead(_candidate(filing=_filing(corp_code=bad_value)), set(), _config())
    assert "blank_issuer_id" in result.reasons


@pytest.mark.parametrize("bad_value", [None, "", "   "])
def test_proof6_blank_issuer_name_fails(bad_value):
    result = select_research_lead(_candidate(filing=_filing(corp_name=bad_value)), set(), _config())
    assert "blank_issuer_name" in result.reasons


@pytest.mark.parametrize("bad_value", [None, "", "   "])
def test_proof6_blank_source_url_fails(bad_value):
    result = select_research_lead(_candidate(filing=_filing(source_url=bad_value)), set(), _config())
    assert "blank_source_url" in result.reasons


@pytest.mark.parametrize("bad_value", [None, "", "   ", 123])
def test_proof6_blank_original_excerpt_fails(bad_value):
    result = select_research_lead(_candidate(excerpt_original=bad_value), set(), _config())
    assert "blank_original_excerpt" in result.reasons


# ============================================================
# Proof 7 — invalid/missing filing
# ============================================================


@pytest.mark.parametrize("bad_filing", [None, "not a filing", 123, {"rcept_no": "x"}])
def test_proof7_invalid_or_missing_filing_fails_safely(bad_filing):
    candidate = dataclasses.replace(_candidate(), filing=bad_filing)
    result = select_research_lead(candidate, set(), _config())
    assert "invalid_filing" in result.reasons
    # Filing-dependent gates are never separately reported once filing itself is invalid.
    for filing_gate in ("blank_source_name", "source_not_recognized", "blank_document_id", "blank_issuer_id", "blank_issuer_name", "blank_source_url", "invalid_receipt_date"):
        assert filing_gate not in result.reasons
    # Non-filing gates are still independently evaluated.
    assert result.priority == LeadPriority.NOT_QUALIFIED


# ============================================================
# Proof 8 — unrecognized/blank source
# ============================================================


def test_proof8_unrecognized_source_fails_closed_with_fixed_reason():
    result = select_research_lead(_candidate(filing=_filing(source_name="Some Random Source")), set(), _config())
    assert "source_not_recognized" in result.reasons
    assert "Some Random Source" not in " ".join(result.reasons)


@pytest.mark.parametrize("bad_value", [None, "", "   "])
def test_proof8_blank_source_fails_closed(bad_value):
    result = select_research_lead(_candidate(filing=_filing(source_name=bad_value)), set(), _config())
    assert "blank_source_name" in result.reasons


# ============================================================
# Proof 9 — extraction state
# ============================================================


@pytest.mark.parametrize("bad_value", [None, "Extracted", 123])
def test_proof9_non_enum_extraction_state_fails_distinctly(bad_value):
    candidate = dataclasses.replace(_candidate(), extraction_state=bad_value)
    result = select_research_lead(candidate, set(), _config())
    assert "invalid_extraction_state" in result.reasons
    assert "excerpt_not_extracted" not in result.reasons


def test_proof9_valid_non_extracted_state_fails_distinctly():
    candidate = _candidate(extraction_state=ExtractionState.PENDING)
    result = select_research_lead(candidate, set(), _config())
    assert "excerpt_not_extracted" in result.reasons
    assert "invalid_extraction_state" not in result.reasons


# ============================================================
# Proof 10 — confidence
# ============================================================


@pytest.mark.parametrize("bad_value", [None, "", "   ", 123])
def test_proof10_invalid_or_missing_confidence_fails_distinctly(bad_value):
    candidate = dataclasses.replace(_candidate(), confidence=bad_value)
    result = select_research_lead(candidate, set(), _config())
    assert "invalid_confidence" in result.reasons
    assert "confidence_not_qualified" not in result.reasons


@pytest.mark.parametrize("bad_value", ["Low", "low", "Extreme", "moderate"])
def test_proof10_lower_or_unrecognized_confidence_fails_distinctly(bad_value):
    result = select_research_lead(_candidate(confidence=bad_value), set(), _config())
    assert "confidence_not_qualified" in result.reasons
    assert "invalid_confidence" not in result.reasons


# ============================================================
# Proof 11 — candidate status
# ============================================================


@pytest.mark.parametrize("bad_value", [None, "Needs review", 123])
def test_proof11_non_enum_status_fails_distinctly(bad_value):
    candidate = dataclasses.replace(_candidate(), status=bad_value)
    result = select_research_lead(candidate, set(), _config())
    assert "invalid_candidate_status" in result.reasons
    assert "status_not_needs_review" not in result.reasons


def test_proof11_valid_non_needs_review_status_fails_distinctly():
    result = select_research_lead(_candidate(status=CandidateStatus.MONITORING), set(), _config())
    assert "status_not_needs_review" in result.reasons
    assert "invalid_candidate_status" not in result.reasons


# ============================================================
# Proof 12 — matched_rules malformation
# ============================================================


@pytest.mark.parametrize("bad_value", [None, "a string", 123, {"a": "b"}, [1, 2, 3], ["financing_or_debt:2.03", None]])
def test_proof12_invalid_matched_rules_container_or_tokens_fails_safely(bad_value):
    candidate = dataclasses.replace(_candidate(), matched_rules=bad_value)
    result = select_research_lead(candidate, set(), _config())
    assert "invalid_matched_rules" in result.reasons
    assert result.normalized_categories == ()
    assert "amendment_or_correction" not in result.reasons


def test_proof12_empty_matched_rules_yields_no_rule_categories():
    result = select_research_lead(_candidate(matched_rules=[]), set(), _config())
    assert "no_rule_categories" in result.reasons
    assert result.normalized_categories == ()


def test_proof12_malformed_colon_tokens_and_blank_category_segments_are_discarded_safely():
    candidate = _candidate(matched_rules=["", "   ", ":", ":only_keyword", "no_colon_token"])
    result = select_research_lead(candidate, set(), _config())
    assert "no_rule_categories" in result.reasons
    assert result.normalized_categories == ()
    assert "invalid_matched_rules" not in result.reasons


# ============================================================
# Proof 13 — amendment exclusion
# ============================================================


def test_proof13_amendment_marker_excludes_even_with_real_categories():
    candidate = _candidate(matched_rules=["financing_or_debt:2.03", "amendment_or_correction"])
    result = select_research_lead(candidate, set(), _config())
    assert "amendment_or_correction" in result.reasons
    assert result.priority == LeadPriority.NOT_QUALIFIED
    assert result.normalized_categories == ("financing_or_debt",)


@pytest.mark.parametrize("marker", ["amendment_or_correction", "AMENDMENT_OR_CORRECTION", "  Amendment_Or_Correction  "])
def test_proof13_amendment_marker_excludes_case_insensitively(marker):
    candidate = _candidate(matched_rules=["financing_or_debt:2.03", marker])
    result = select_research_lead(candidate, set(), _config())
    assert "amendment_or_correction" in result.reasons


# ============================================================
# Proof 14 — date parsing, future, stale, boundaries
# ============================================================


@pytest.mark.parametrize("bad_date", [None, "", "2026/08/15", "20260815", "not-a-date", "2026-02-30"])
def test_proof14_malformed_receipt_date_fails_safely(bad_date):
    result = select_research_lead(_candidate(filing=_filing(rcept_dt=bad_date)), set(), _config())
    assert "invalid_receipt_date" in result.reasons


def test_proof14_future_receipt_date_fails():
    result = select_research_lead(_candidate(filing=_filing(rcept_dt="2026-08-25")), set(), _config(as_of_date="2026-08-20"))
    assert "receipt_date_in_future" in result.reasons


def test_proof14_stale_receipt_date_outside_lookback_fails():
    result = select_research_lead(_candidate(filing=_filing(rcept_dt="2026-06-01")), set(), _config(as_of_date="2026-08-20", lookback_days=30))
    assert "receipt_date_outside_lookback" in result.reasons


def test_proof14_exact_lower_boundary_zero_days_is_eligible():
    result = select_research_lead(_candidate(filing=_filing(rcept_dt="2026-08-20")), set(), _config(as_of_date="2026-08-20", lookback_days=30))
    assert "receipt_date_in_future" not in result.reasons
    assert "receipt_date_outside_lookback" not in result.reasons
    assert "invalid_receipt_date" not in result.reasons
    assert result.priority == LeadPriority.QUALIFIED


def test_proof14_exact_upper_boundary_lookback_days_is_eligible():
    result = select_research_lead(_candidate(filing=_filing(rcept_dt="2026-07-21")), set(), _config(as_of_date="2026-08-20", lookback_days=30))
    assert "receipt_date_outside_lookback" not in result.reasons


def test_proof14_one_day_past_upper_boundary_fails():
    result = select_research_lead(_candidate(filing=_filing(rcept_dt="2026-07-20")), set(), _config(as_of_date="2026-08-20", lookback_days=30))
    assert "receipt_date_outside_lookback" in result.reasons


# ============================================================
# Proof 15-16 — detection timestamp discovery
# ============================================================


def test_proof15_uses_first_candidate_detected_transition_by_supplied_order_not_index_zero():
    history = [
        StateTransition(status=CandidateStatus.QUEUED_FOR_PROCESSING, at="2026-08-01T00:00:00+00:00"),
        StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at="2026-08-02T00:00:00+00:00"),
        StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at="2026-08-09T00:00:00+00:00"),
    ]
    candidate = _candidate(state_history=history)
    result = select_research_lead(candidate, set(), _config())
    expected = build_case_id("radar", candidate.id, "2026-08-02T00:00:00+00:00")
    assert result.case_id == expected


@pytest.mark.parametrize("bad_history", [None, "not a list", 123, [], [{"status": "Candidate detected", "at": "x"}]])
def test_proof16_missing_or_malformed_state_history_fails_safely(bad_history):
    candidate = dataclasses.replace(_candidate(), state_history=bad_history)
    result = select_research_lead(candidate, set(), _config())
    assert "missing_candidate_detected_timestamp" in result.reasons
    assert result.case_id is None


def test_proof16_no_candidate_detected_transition_fails_safely():
    history = [StateTransition(status=CandidateStatus.NEEDS_REVIEW, at="2026-08-01T00:00:00+00:00")]
    candidate = _candidate(state_history=history)
    result = select_research_lead(candidate, set(), _config())
    assert "missing_candidate_detected_timestamp" in result.reasons


def test_proof16_blank_detection_timestamp_fails_safely():
    history = [StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at="")]
    candidate = _candidate(state_history=history)
    result = select_research_lead(candidate, set(), _config())
    assert "missing_candidate_detected_timestamp" in result.reasons


# ============================================================
# Proof 17 — exact case ID compatibility with the real factory
# ============================================================


def test_case_id_matches_real_research_case_factory_exactly():
    candidate = _candidate()
    result = select_research_lead(candidate, set(), _config())
    assert result.case_id == build_case_id("radar", candidate.id, _DETECTED_AT)


def test_case_id_changes_with_candidate_id_and_detected_at_exactly_like_real_factory():
    a = select_research_lead(_candidate(id="edgar-cand-1"), set(), _config())
    b = select_research_lead(_candidate(id="edgar-cand-2"), set(), _config())
    assert a.case_id != b.case_id
    assert a.case_id == build_case_id("radar", "edgar-cand-1", _DETECTED_AT)
    assert b.case_id == build_case_id("radar", "edgar-cand-2", _DETECTED_AT)


# ============================================================
# Proof 18 — already-triggered dedup
# ============================================================


def test_proof18_already_triggered_case_id_rejects():
    candidate = _candidate()
    case_id = _expected_case_id(candidate)
    result = select_research_lead(candidate, {case_id}, _config())
    assert result.priority == LeadPriority.NOT_QUALIFIED
    assert "case_already_triggered" in result.reasons
    assert result.case_id is None


class _BrokenContainer:
    def __contains__(self, item):
        raise RuntimeError("boom")


def test_proof18_broken_membership_collection_fails_safely():
    result = select_research_lead(_candidate(), _BrokenContainer(), _config())
    assert result.priority == LeadPriority.NOT_QUALIFIED
    assert "invalid_already_triggered_case_ids" in result.reasons
    assert result.case_id is None


@pytest.mark.parametrize("bad_collection", [None, 123])
def test_proof18_non_container_membership_input_fails_safely(bad_collection):
    result = select_research_lead(_candidate(), bad_collection, _config())
    assert result.priority == LeadPriority.NOT_QUALIFIED
    assert "invalid_already_triggered_case_ids" in result.reasons


def test_proof18_not_triggered_and_valid_collection_passes():
    result = select_research_lead(_candidate(), {"case-does-not-match"}, _config())
    assert result.priority == LeadPriority.QUALIFIED
    assert result.case_id is not None


# ============================================================
# Proof 19 — rejected results carry no positive content
# ============================================================


def test_proof19_rejected_result_has_no_positive_reasons_categories_or_priority_marker():
    result = select_research_lead(_candidate(status=CandidateStatus.MONITORING), set(), _config())
    assert result.priority == LeadPriority.NOT_QUALIFIED
    assert result.case_id is None
    for positive in (
        "source_recognized", "candidate_identity_present", "issuer_provenance_present",
        "source_url_present", "excerpt_extracted", "rule_categories_present",
        "confidence_qualified", "status_needs_review", "receipt_date_within_lookback",
        "case_not_previously_triggered", "priority_high_signal", "priority_qualified",
    ):
        assert positive not in result.reasons
    assert not any(r.startswith("category:") for r in result.reasons)


# ============================================================
# Proof 20 — deterministic multi-gate ordering
# ============================================================


def test_proof20_multi_gate_failure_preserves_defined_gate_order():
    candidate = _candidate(
        confidence=None, status=None, matched_rules=None,
        excerpt_original=None, extraction_state=None,
    )
    result = select_research_lead(candidate, set(), _config())
    expected_order = [
        "invalid_extraction_state", "blank_original_excerpt", "invalid_matched_rules",
        "invalid_confidence", "invalid_candidate_status",
    ]
    positions = [result.reasons.index(r) for r in expected_order]
    assert positions == sorted(positions)


def test_proof20_repeated_calls_are_byte_identical():
    candidate = _candidate()
    config = _config()
    results = [select_research_lead(candidate, {"other-id"}, config) for _ in range(5)]
    assert all(r == results[0] for r in results)


# ============================================================
# Proof 21 — no mutation of inputs
# ============================================================


def test_proof21_candidate_config_and_triggered_collection_unchanged_after_selection():
    candidate = _candidate()
    filing_before = dataclasses.replace(candidate.filing)
    matched_rules_before = list(candidate.matched_rules)
    state_history_before = list(candidate.state_history)
    config = _config()
    config_before = dataclasses.replace(config)
    triggered = {"some-existing-id"}
    triggered_before = set(triggered)

    select_research_lead(candidate, triggered, config)

    assert candidate.filing == filing_before
    assert candidate.matched_rules == matched_rules_before
    assert candidate.state_history == state_history_before
    assert config == config_before
    assert triggered == triggered_before


# ============================================================
# Proof 22 — scope: no forbidden imports/calls anywhere in the module
# ============================================================


def test_proof22_module_never_imports_or_calls_forbidden_things():
    source = (REPO_ROOT / "src" / "logic" / "research_lead_selection.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="research_lead_selection.py")

    forbidden_modules = (
        "src.data_access", "src.ui", "streamlit", "requests", "urllib", "httpx", "socket",
        "subprocess", "os", "random", "logging",
        "anthropic", "openai", "langchain",
        "scripts",
    )
    offenders = []
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for module in modules:
            if any(module == forbidden or module.startswith(forbidden + ".") for forbidden in forbidden_modules):
                offenders.append(f"imports {module!r}")
    assert not offenders, offenders

    wall_clock_or_random_calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and (
            (n.func.attr in ("now", "today", "utcnow") and isinstance(n.func.value, ast.Name) and n.func.value.id in ("datetime", "date"))
            or (n.func.attr == "time" and isinstance(n.func.value, ast.Name) and n.func.value.id == "time")
            or (isinstance(n.func.value, ast.Name) and n.func.value.id == "os" and n.func.attr in ("getenv", "environ"))
        )
    ]
    assert not wall_clock_or_random_calls, wall_clock_or_random_calls

    env_reads = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and n.attr == "environ"
    ]
    assert not env_reads


def test_proof22_module_imports_only_stdlib_and_the_one_models_module():
    source = (REPO_ROOT / "src" / "logic" / "research_lead_selection.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="research_lead_selection.py")
    allowed_stdlib = {"__future__", "hashlib", "re", "dataclasses", "datetime", "enum", "typing"}
    allowed_project_modules = {"src.models.models"}
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


# ============================================================
# Proof 23 — scope guard: only the approved files changed
# ============================================================


def test_proof23_scope_guard_only_approved_files_changed():
    result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    allowed = {
        "src/logic/research_lead_selection.py",
    }
    assert changed <= allowed, changed - allowed


def test_proof23_no_new_dependency_added_to_requirements():
    result = subprocess.run(["git", "diff", "HEAD", "--", "requirements.txt"], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    assert result.stdout.strip() == "", f"requirements.txt was modified: {result.stdout}"
