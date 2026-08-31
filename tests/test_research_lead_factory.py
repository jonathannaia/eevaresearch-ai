"""EevaResearch Phase 4, Step 4A-2 (design/DECISIONS.md) — pure,
deterministic factory mapping a selected Radar lead into a
ResearchCaseBundle. Every fixture here is synthetic and directly
constructed; this file never touches persistence, a real store/
repository, a real scan, the network, the UI, an LLM, a source client,
the authoring script, or the real current date.

`build_case_id`/`build_evidence_id` are imported from
`src.data_access.research_store` in exactly the compatibility tests
below (test_case_id_matches_real_research_case_factory_exactly and
test_evidence_id_matches_real_research_case_factory_exactly) — pure,
no-I/O hash functions imported only to prove the factory's own private,
deliberately duplicated ID algorithms stay byte-for-byte compatible
with the real factories (see research_lead_factory.py's own docstring
for why it duplicates rather than imports). No store/repository is ever
constructed or called anywhere in this file."""
from __future__ import annotations

import ast
import dataclasses
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from src.data_access.research_store import build_case_id, build_evidence_id
from src.logic import research_lead_factory
from src.logic.research_case_validation import validate_research_case_bundle
from src.logic.research_lead_factory import build_research_case_bundle_from_lead
from src.logic.research_lead_selection import LeadPriority, LeadSelectionResult, select_research_lead
from src.models.models import (
    CandidateSignal,
    CandidateStatus,
    ExtractionState,
    FilingEvent,
    StateTransition,
    Translation,
)
from src.models.research_case import ResearchCaseStatus

REPO_ROOT = Path(__file__).parent.parent

_DETECTED_AT = "2026-08-15T00:00:00+00:00"


def _filing(**overrides) -> FilingEvent:
    defaults = dict(
        rcept_no="acc-1", corp_code="0000320193", corp_name="Apple Inc.", stock_code="AAPL",
        report_nm="8-K", rcept_dt="2026-08-15", flr_nm="Apple Inc.", source_name="SEC EDGAR",
        source_url="https://example.com/filing", retrieved_at="2026-08-15T01:00:00+00:00",
        original_language="English",
    )
    defaults.update(overrides)
    return FilingEvent(**defaults)


def _translation(**overrides) -> Translation:
    defaults = dict(
        translated_text="Translated text.", provider="DeepL", source_lang="en", target_lang="en",
        translated_at="2026-08-15T01:00:00+00:00",
    )
    defaults.update(overrides)
    return Translation(**defaults)


def _candidate(**overrides) -> CandidateSignal:
    defaults = dict(
        id="edgar-cand-1", filing=_filing(), matched_rules=["financing_or_debt:2.03"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="The company entered into a financing agreement.",
        state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at=_DETECTED_AT)],
    )
    defaults.update(overrides)
    return CandidateSignal(**defaults)


def _expected_case_id(candidate_id: str, detected_at: str = _DETECTED_AT) -> str:
    return build_case_id("radar", candidate_id, detected_at)


def _selection_for(candidate: CandidateSignal, **overrides) -> LeadSelectionResult:
    """A directly-constructed, already-qualified LeadSelectionResult —
    never produced by calling select_research_lead() (this file proves
    that function is never invoked by the factory; using it here to
    *build* fixtures would undercut that proof's own test isolation for
    the tests that intentionally supply a stale/mismatched case_id)."""
    defaults = dict(
        priority=LeadPriority.QUALIFIED,
        reasons=("source_recognized", "priority_qualified"),
        normalized_categories=("financing_or_debt",),
        case_id=_expected_case_id(candidate.id),
    )
    defaults.update(overrides)
    return LeadSelectionResult(**defaults)


# ============================================================
# Proofs 1-2 — valid QUALIFIED / HIGH_SIGNAL selections
# ============================================================


def test_proof1_valid_qualified_selection_returns_bundle_that_passes_validation():
    candidate = _candidate()
    selection = _selection_for(candidate)
    bundle = build_research_case_bundle_from_lead(candidate, selection)
    assert bundle is not None
    assert len(bundle.evidence_items) == 1
    assert bundle.assertions == ()
    assert validate_research_case_bundle(bundle) == ()


def test_proof2_valid_high_signal_selection_behaves_identically():
    candidate = _candidate(confidence="High", matched_rules=["financing_or_debt:2.03", "material_agreement:1.01"])
    selection = _selection_for(candidate, priority=LeadPriority.HIGH_SIGNAL, normalized_categories=("financing_or_debt", "material_agreement"))
    bundle = build_research_case_bundle_from_lead(candidate, selection)
    assert bundle is not None
    assert len(bundle.evidence_items) == 1
    assert bundle.assertions == ()
    assert validate_research_case_bundle(bundle) == ()


# ============================================================
# Proof 3 — exact ResearchCase field mapping
# ============================================================


def test_proof3_case_fields_match_exact_specified_mapping():
    candidate = _candidate()
    selection = _selection_for(candidate)
    bundle = build_research_case_bundle_from_lead(candidate, selection)
    case = bundle.case
    assert case.id == selection.case_id
    assert case.trigger_source_type == "radar"
    assert case.trigger_source_id == candidate.id
    assert case.trigger_source_name == candidate.filing.corp_name
    assert case.trigger_summary == candidate.filing.report_nm
    assert case.title == f"{candidate.filing.corp_name} — {candidate.filing.report_nm}"
    assert case.research_question == (
        f"What are the evidence-backed dependencies, relationships, or "
        f"second-order effects connected to this {candidate.filing.source_name} filing?"
    )
    assert case.status == ResearchCaseStatus.OPEN
    assert case.created_at == _DETECTED_AT
    assert case.version == 1


# ============================================================
# Proof 4 — exact ResearchEvidenceItem field mapping
# ============================================================


def test_proof4_evidence_fields_match_exact_specified_mapping():
    candidate = _candidate(excerpt_translation=_translation())
    selection = _selection_for(candidate)
    bundle = build_research_case_bundle_from_lead(candidate, selection)
    item = bundle.evidence_items[0]
    filing = candidate.filing
    assert item.case_id == selection.case_id
    assert item.source_type == filing.source_name
    assert item.source_id == filing.rcept_no
    assert item.source_url == filing.source_url
    assert item.source_publisher_or_system == filing.source_name
    assert item.source_date == filing.rcept_dt
    assert item.retrieved_at == filing.retrieved_at
    assert item.excerpt_original == candidate.excerpt_original
    assert item.original_language == filing.original_language
    assert item.added_at == _DETECTED_AT
    assert item.excerpt_translated == "Translated text."
    assert item.translation_provider == "DeepL"


# ============================================================
# Proofs 5-6 — exact ID compatibility with the real factories
# ============================================================


def test_proof5_case_id_matches_selection_case_id_and_real_factory():
    candidate = _candidate()
    selection = _selection_for(candidate)
    bundle = build_research_case_bundle_from_lead(candidate, selection)
    assert bundle.case.id == selection.case_id
    assert bundle.case.id == build_case_id("radar", candidate.id, _DETECTED_AT)


def test_case_id_matches_real_research_case_factory_exactly():
    candidate = _candidate(id="edgar-cand-99")
    selection = _selection_for(candidate)
    bundle = build_research_case_bundle_from_lead(candidate, selection)
    assert bundle.case.id == build_case_id("radar", "edgar-cand-99", _DETECTED_AT)


def test_proof6_evidence_id_matches_real_factory():
    candidate = _candidate()
    selection = _selection_for(candidate)
    bundle = build_research_case_bundle_from_lead(candidate, selection)
    expected = build_evidence_id(selection.case_id, candidate.filing.source_name, candidate.filing.rcept_no, _DETECTED_AT)
    assert bundle.evidence_items[0].id == expected


def test_evidence_id_matches_real_research_case_factory_exactly():
    candidate = _candidate(filing=_filing(rcept_no="acc-777"))
    selection = _selection_for(candidate)
    bundle = build_research_case_bundle_from_lead(candidate, selection)
    expected = build_evidence_id(selection.case_id, "SEC EDGAR", "acc-777", _DETECTED_AT)
    assert bundle.evidence_items[0].id == expected


# ============================================================
# Proof 7 — first CANDIDATE_DETECTED transition by supplied order
# ============================================================


def test_proof7_first_candidate_detected_transition_by_order_not_index_zero():
    history = [
        StateTransition(status=CandidateStatus.QUEUED_FOR_PROCESSING, at="2026-08-01T00:00:00+00:00"),
        StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at="2026-08-02T00:00:00+00:00"),
        StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at="2026-08-09T00:00:00+00:00"),
    ]
    candidate = _candidate(state_history=history)
    selection = _selection_for(candidate, case_id=_expected_case_id(candidate.id, "2026-08-02T00:00:00+00:00"))
    bundle = build_research_case_bundle_from_lead(candidate, selection)
    assert bundle is not None
    assert bundle.case.created_at == "2026-08-02T00:00:00+00:00"
    assert bundle.evidence_items[0].added_at == "2026-08-02T00:00:00+00:00"


# ============================================================
# Proof 8 — missing/malformed inputs return None, never raise
# ============================================================


@pytest.mark.parametrize("bad_candidate", [None, "a string", 123, object(), {"id": "x"}])
def test_proof8_invalid_candidate_returns_none(bad_candidate):
    selection = _selection_for(_candidate())
    assert build_research_case_bundle_from_lead(bad_candidate, selection) is None


@pytest.mark.parametrize("bad_selection", [None, "a string", 123, object()])
def test_proof8_invalid_selection_returns_none(bad_selection):
    assert build_research_case_bundle_from_lead(_candidate(), bad_selection) is None


def test_proof8_invalid_priority_type_returns_none():
    candidate = _candidate()
    selection = dataclasses.replace(_selection_for(candidate), priority="HIGH_SIGNAL")
    assert build_research_case_bundle_from_lead(candidate, selection) is None


@pytest.mark.parametrize("bad_case_id", [None, "", "   ", 123])
def test_proof8_blank_case_id_returns_none(bad_case_id):
    candidate = _candidate()
    selection = _selection_for(candidate, case_id=bad_case_id)
    assert build_research_case_bundle_from_lead(candidate, selection) is None


@pytest.mark.parametrize("bad_filing", [None, "not a filing", 123, {"rcept_no": "x"}])
def test_proof8_invalid_filing_returns_none(bad_filing):
    candidate = dataclasses.replace(_candidate(), filing=bad_filing)
    selection = _selection_for(_candidate())
    assert build_research_case_bundle_from_lead(candidate, selection) is None


@pytest.mark.parametrize("bad_id", [None, "", "   ", 123])
def test_proof8_blank_candidate_id_returns_none(bad_id):
    candidate = _candidate(id=bad_id)
    selection = _selection_for(_candidate())
    assert build_research_case_bundle_from_lead(candidate, selection) is None


@pytest.mark.parametrize("field", ["source_name", "rcept_no", "corp_code", "corp_name", "report_nm", "source_url", "rcept_dt", "retrieved_at", "original_language"])
@pytest.mark.parametrize("bad_value", [None, "", "   "])
def test_proof8_blank_required_filing_field_returns_none(field, bad_value):
    candidate = _candidate(filing=_filing(**{field: bad_value}))
    selection = _selection_for(candidate)
    assert build_research_case_bundle_from_lead(candidate, selection) is None


def test_proof8_missing_detection_transition_returns_none():
    candidate = _candidate(state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at="2026-08-01T00:00:00+00:00")])
    selection = _selection_for(_candidate())
    assert build_research_case_bundle_from_lead(candidate, selection) is None


@pytest.mark.parametrize("bad_history", [None, "not a list", 123, [], [{"status": "Candidate detected", "at": "x"}]])
def test_proof8_malformed_state_history_returns_none(bad_history):
    candidate = dataclasses.replace(_candidate(), state_history=bad_history)
    selection = _selection_for(_candidate())
    assert build_research_case_bundle_from_lead(candidate, selection) is None


def test_proof8_blank_detection_timestamp_returns_none():
    candidate = _candidate(state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at="")])
    selection = _selection_for(_candidate())
    assert build_research_case_bundle_from_lead(candidate, selection) is None


@pytest.mark.parametrize("bad_state", [None, "Extracted", 123, ExtractionState.PENDING, ExtractionState.PARSE_FAILED])
def test_proof8_invalid_or_non_extracted_extraction_state_returns_none(bad_state):
    candidate = dataclasses.replace(_candidate(), extraction_state=bad_state)
    selection = _selection_for(_candidate())
    assert build_research_case_bundle_from_lead(candidate, selection) is None


@pytest.mark.parametrize("bad_excerpt", [None, "", "   ", 123])
def test_proof8_blank_excerpt_returns_none(bad_excerpt):
    candidate = _candidate(excerpt_original=bad_excerpt)
    selection = _selection_for(_candidate())
    assert build_research_case_bundle_from_lead(candidate, selection) is None


@pytest.mark.parametrize("bad_status", [None, "Needs review", 123, CandidateStatus.MONITORING, CandidateStatus.DISMISSED])
def test_proof8_invalid_or_non_needs_review_status_returns_none(bad_status):
    candidate = dataclasses.replace(_candidate(), status=bad_status)
    selection = _selection_for(_candidate())
    assert build_research_case_bundle_from_lead(candidate, selection) is None


# ============================================================
# Proof 9-10 — NOT_QUALIFIED and stale/mismatched case_id
# ============================================================


def test_proof9_not_qualified_selection_returns_none():
    candidate = _candidate()
    selection = LeadSelectionResult(priority=LeadPriority.NOT_QUALIFIED, reasons=("status_not_needs_review",), normalized_categories=(), case_id=None)
    assert build_research_case_bundle_from_lead(candidate, selection) is None


def test_proof10_stale_mismatched_case_id_returns_none():
    candidate = _candidate()
    selection = _selection_for(candidate, case_id="case-0000000000000000000000")
    assert build_research_case_bundle_from_lead(candidate, selection) is None


def test_proof10_fabricated_case_id_for_different_candidate_returns_none():
    candidate = _candidate()
    other_case_id = _expected_case_id("edgar-cand-different")
    selection = _selection_for(candidate, case_id=other_case_id)
    assert build_research_case_bundle_from_lead(candidate, selection) is None


# ============================================================
# Proofs 11-13 — no selector re-invocation, no validation call, no persistence
# ============================================================


def test_proof11_factory_never_calls_select_research_lead(monkeypatch):
    def _forbidden(*_a, **_k):
        raise AssertionError("build_research_case_bundle_from_lead must never call select_research_lead")

    monkeypatch.setattr("src.logic.research_lead_selection.select_research_lead", _forbidden)
    candidate = _candidate()
    selection = _selection_for(candidate)
    bundle = build_research_case_bundle_from_lead(candidate, selection)
    assert bundle is not None


def test_proof11_module_source_never_calls_select_research_lead():
    """AST-based, not substring-based, so the module's own docstring
    prose explaining that select_research_lead() is never called can't
    false-positive this check the way a naive substring scan would."""
    source = (REPO_ROOT / "src" / "logic" / "research_lead_factory.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="research_lead_factory.py")
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and (
            (isinstance(n.func, ast.Name) and n.func.id == "select_research_lead")
            or (isinstance(n.func, ast.Attribute) and n.func.attr == "select_research_lead")
        )
    ]
    assert not calls
    imports = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and any(alias.name == "select_research_lead" for alias in n.names)
    ]
    assert not imports


def test_proof12_factory_never_calls_validate_research_case_bundle(monkeypatch):
    def _forbidden(*_a, **_k):
        raise AssertionError("build_research_case_bundle_from_lead must never call validate_research_case_bundle")

    monkeypatch.setattr("src.logic.research_case_validation.validate_research_case_bundle", _forbidden)
    candidate = _candidate()
    selection = _selection_for(candidate)
    bundle = build_research_case_bundle_from_lead(candidate, selection)
    assert bundle is not None


def test_proof12_module_source_never_calls_validate_research_case_bundle():
    """AST-based, not substring-based — see
    test_proof11_module_source_never_calls_select_research_lead's own
    docstring for why."""
    source = (REPO_ROOT / "src" / "logic" / "research_lead_factory.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="research_lead_factory.py")
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and (
            (isinstance(n.func, ast.Name) and n.func.id == "validate_research_case_bundle")
            or (isinstance(n.func, ast.Attribute) and n.func.attr == "validate_research_case_bundle")
        )
    ]
    assert not calls
    imports = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and any(alias.name == "validate_research_case_bundle" for alias in n.names)
    ]
    assert not imports


def test_proof13_factory_never_calls_any_persistence_function():
    with patch("src.data_access.research_store.append_research_case_bundle") as mock_append:
        candidate = _candidate()
        selection = _selection_for(candidate)
        build_research_case_bundle_from_lead(candidate, selection)
        mock_append.assert_not_called()


# ============================================================
# Proof 14 — translation handling
# ============================================================


def test_proof14_no_translation_yields_both_fields_none():
    candidate = _candidate(excerpt_translation=None)
    selection = _selection_for(candidate)
    bundle = build_research_case_bundle_from_lead(candidate, selection)
    assert bundle.evidence_items[0].excerpt_translated is None
    assert bundle.evidence_items[0].translation_provider is None


@pytest.mark.parametrize("bad_translation", ["not a translation", 123, {"translated_text": "x", "provider": "y"}])
def test_proof14_malformed_translation_object_yields_both_fields_none(bad_translation):
    candidate = dataclasses.replace(_candidate(), excerpt_translation=bad_translation)
    selection = _selection_for(candidate)
    bundle = build_research_case_bundle_from_lead(candidate, selection)
    assert bundle.evidence_items[0].excerpt_translated is None
    assert bundle.evidence_items[0].translation_provider is None


def test_proof14_valid_translation_copied_exactly():
    candidate = _candidate(excerpt_translation=_translation(translated_text="Exact text.", provider="DeepL"))
    selection = _selection_for(candidate)
    bundle = build_research_case_bundle_from_lead(candidate, selection)
    assert bundle.evidence_items[0].excerpt_translated == "Exact text."
    assert bundle.evidence_items[0].translation_provider == "DeepL"


@pytest.mark.parametrize("bad_text", [None, "", "   "])
def test_proof14_blank_translated_text_yields_both_fields_none(bad_text):
    candidate = dataclasses.replace(
        _candidate(), excerpt_translation=dataclasses.replace(_translation(), translated_text=bad_text),
    )
    selection = _selection_for(candidate)
    bundle = build_research_case_bundle_from_lead(candidate, selection)
    assert bundle.evidence_items[0].excerpt_translated is None
    assert bundle.evidence_items[0].translation_provider is None


@pytest.mark.parametrize("bad_provider", [None, "", "   "])
def test_proof14_blank_provider_keeps_text_but_provider_is_none(bad_provider):
    candidate = dataclasses.replace(
        _candidate(), excerpt_translation=dataclasses.replace(_translation(), provider=bad_provider),
    )
    selection = _selection_for(candidate)
    bundle = build_research_case_bundle_from_lead(candidate, selection)
    assert bundle.evidence_items[0].excerpt_translated == "Translated text."
    assert bundle.evidence_items[0].translation_provider is None


# ============================================================
# Proof 15-16 — no mutation, determinism
# ============================================================


def test_proof15_inputs_unchanged_after_call():
    candidate = _candidate(excerpt_translation=_translation())
    filing_before = dataclasses.replace(candidate.filing)
    matched_rules_before = list(candidate.matched_rules)
    state_history_before = list(candidate.state_history)
    selection = _selection_for(candidate)
    selection_before = dataclasses.replace(selection)

    build_research_case_bundle_from_lead(candidate, selection)

    assert candidate.filing == filing_before
    assert candidate.matched_rules == matched_rules_before
    assert candidate.state_history == state_history_before
    assert selection == selection_before


def test_proof16_repeated_calls_return_equality_identical_bundles():
    candidate = _candidate()
    selection = _selection_for(candidate)
    results = [build_research_case_bundle_from_lead(candidate, selection) for _ in range(5)]
    assert all(r == results[0] for r in results)
    assert all(r.case.id == results[0].case.id for r in results)


# ============================================================
# Proof 17 — zero unapproved content
# ============================================================


def test_proof17_zero_assertions_no_relationships_no_dependencies_no_unapproved_language():
    candidate = _candidate()
    selection = _selection_for(candidate)
    bundle = build_research_case_bundle_from_lead(candidate, selection)
    assert bundle.assertions == ()

    # "relationships"/"dependencies" are deliberately NOT forbidden here —
    # they appear verbatim in the approved, fixed research_question
    # template text itself (see the spec's exact wording), not as an
    # inferred claim about any specific relationship or dependency.
    forbidden_terms = (
        "buy", "sell", "bullish", "bearish", "materiality", "material impact", "rating", "target price",
        "bottleneck_type", "hypothesis", "limitation",
    )
    case_text = f"{bundle.case.title} {bundle.case.research_question} {bundle.case.trigger_summary}".lower()
    for term in forbidden_terms:
        assert term not in case_text


# ============================================================
# Proof 18 — AST/scope checks: no forbidden imports/calls
# ============================================================


def test_proof18_module_never_imports_forbidden_things():
    source = (REPO_ROOT / "src" / "logic" / "research_lead_factory.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="research_lead_factory.py")

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


def test_proof18_module_imports_only_stdlib_and_approved_project_modules():
    source = (REPO_ROOT / "src" / "logic" / "research_lead_factory.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="research_lead_factory.py")
    allowed_stdlib = {"__future__", "hashlib"}
    allowed_project_modules = {
        "src.logic.research_case_validation", "src.logic.research_lead_selection",
        "src.models.models", "src.models.research_case",
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


def test_proof18_module_never_calls_wall_clock_random_or_env():
    source = (REPO_ROOT / "src" / "logic" / "research_lead_factory.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="research_lead_factory.py")
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


# ============================================================
# Proof 19 — scope guard
# ============================================================


def test_proof19_scope_guard_only_approved_files_changed():
    result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    allowed = {"src/logic/research_lead_factory.py"}
    assert changed <= allowed, changed - allowed


def test_proof19_no_new_dependency_added_to_requirements():
    result = subprocess.run(["git", "diff", "HEAD", "--", "requirements.txt"], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    assert result.stdout.strip() == "", f"requirements.txt was modified: {result.stdout}"


def test_module_reference_import_works():
    assert research_lead_factory.build_research_case_bundle_from_lead is build_research_case_bundle_from_lead
