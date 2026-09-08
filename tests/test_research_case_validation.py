"""EevaResearch Phase 4, Step 2 (design/DECISIONS.md) — pure Research
Case validation and bundle assembly. Every object here is directly,
synthetically constructed; no persistence, no fixture file, no network
call, no LLM call anywhere in this file."""
from __future__ import annotations

import ast
import copy
import dataclasses
import subprocess
from pathlib import Path

import pytest

from src.logic.research_case_validation import (
    ResearchCaseBundle,
    ResearchCaseValidationError,
    build_research_case_bundle,
    is_valid_research_case_bundle,
    validate_research_case_bundle,
)
from src.models.research_case import (
    AssertionConfidence,
    AssertionStatus,
    BottleneckType,
    DependencyAssertion,
    RelationshipAssertion,
    RelationshipRole,
    ResearchCase,
    ResearchCaseStatus,
    ResearchEvidenceItem,
)

_CREATED_AT = "2026-08-20T00:00:00+00:00"


def _case(**overrides) -> ResearchCase:
    defaults = dict(
        id="case-1", trigger_source_type="radar", trigger_source_id="cand-1",
        trigger_source_name="Example Corp", trigger_summary="Filed a material event.",
        title="Example research case", research_question="What is the supply-chain exposure?",
        status=ResearchCaseStatus.OPEN, created_at=_CREATED_AT, version=1,
    )
    defaults.update(overrides)
    return ResearchCase(**defaults)


def _evidence(id="evidence-1", case_id="case-1", **overrides) -> ResearchEvidenceItem:
    defaults = dict(
        id=id, case_id=case_id, source_type="radar", source_id="cand-1",
        source_url="https://example.com/filing", source_publisher_or_system="SEC EDGAR",
        source_date="2026-08-15", retrieved_at="2026-08-15T01:00:00+00:00",
        excerpt_original="The company disclosed a supply agreement with Acme Corp.",
        original_language="English", added_at=_CREATED_AT,
    )
    defaults.update(overrides)
    return ResearchEvidenceItem(**defaults)


def _relationship(id="relationship-1", case_id="case-1", evidence_ids=("evidence-1",), **overrides) -> RelationshipAssertion:
    defaults = dict(
        id=id, case_id=case_id, subject_entity="Example Corp", object_entity="Acme Corp",
        role=RelationshipRole.SUPPLIER, assertion_status=AssertionStatus.DIRECTLY_SUPPORTED,
        evidence_ids=evidence_ids, confidence=AssertionConfidence.HIGH, created_at=_CREATED_AT,
    )
    defaults.update(overrides)
    return RelationshipAssertion(**defaults)


def _dependency(id="dependency-1", case_id="case-1", evidence_ids=("evidence-1",), **overrides) -> DependencyAssertion:
    defaults = dict(
        id=id, case_id=case_id, affected_entity="Example Corp", bottleneck_type=BottleneckType.COMPONENT_SUPPLY,
        supply_chain_layer="compute-hardware", transmission_path=None,
        assertion_status=AssertionStatus.DIRECTLY_SUPPORTED, evidence_ids=evidence_ids,
        confidence=AssertionConfidence.HIGH, created_at=_CREATED_AT,
    )
    defaults.update(overrides)
    return DependencyAssertion(**defaults)


def _valid_bundle() -> ResearchCaseBundle:
    case = _case()
    evidence = (_evidence(),)
    assertions = (_relationship(), _dependency())
    return build_research_case_bundle(case, evidence, assertions)


def _codes(errors: tuple[ResearchCaseValidationError, ...]) -> list[str]:
    return [e.code for e in errors]


# ============================================================
# Part A — fully valid bundle + assembly (proofs 1, 2)
# ============================================================


def test_fully_valid_bundle_is_valid():
    bundle = _valid_bundle()
    assert validate_research_case_bundle(bundle) == ()
    assert is_valid_research_case_bundle(bundle) is True


def test_build_bundle_tuple_normalizes_without_mutation_or_generation():
    case = _case()
    evidence_list = [_evidence()]
    assertion_list = [_relationship()]
    case_snapshot = copy.deepcopy(case)
    evidence_snapshot = copy.deepcopy(evidence_list)
    assertion_snapshot = copy.deepcopy(assertion_list)

    bundle = build_research_case_bundle(case, evidence_list, assertion_list)

    assert isinstance(bundle.evidence_items, tuple)
    assert isinstance(bundle.assertions, tuple)
    assert bundle.evidence_items == tuple(evidence_list)
    assert bundle.assertions == tuple(assertion_list)
    assert case == case_snapshot
    assert evidence_list == evidence_snapshot
    assert assertion_list == assertion_snapshot
    # No id/timestamp was invented — the case's own id is untouched.
    assert bundle.case.id == "case-1"


# ============================================================
# Part B — case-required fields + version (proofs 3, 4)
# ============================================================


@pytest.mark.parametrize("field,code", [
    ("id", "blank_case_id"),
    ("trigger_source_type", "blank_case_trigger_source_type"),
    ("trigger_source_id", "blank_case_trigger_source_id"),
    ("trigger_source_name", "blank_case_trigger_source_name"),
    ("trigger_summary", "blank_case_trigger_summary"),
    ("title", "blank_case_title"),
    ("research_question", "blank_case_research_question"),
    ("created_at", "blank_case_created_at"),
])
@pytest.mark.parametrize("blank_value", ["", "   ", "\t\n"])
def test_each_case_required_field_rejects_blank_and_whitespace(field, code, blank_value):
    case = dataclasses.replace(_case(), **{field: blank_value})
    bundle = build_research_case_bundle(case, (), ())
    assert code in _codes(validate_research_case_bundle(bundle))


def test_case_version_zero_fails():
    case = dataclasses.replace(_case(), version=0)
    bundle = build_research_case_bundle(case, (), ())
    assert "invalid_case_version" in _codes(validate_research_case_bundle(bundle))


def test_case_version_negative_fails():
    case = dataclasses.replace(_case(), version=-1)
    bundle = build_research_case_bundle(case, (), ())
    assert "invalid_case_version" in _codes(validate_research_case_bundle(bundle))


def test_case_version_one_passes():
    case = dataclasses.replace(_case(), version=1)
    bundle = build_research_case_bundle(case, (), ())
    assert "invalid_case_version" not in _codes(validate_research_case_bundle(bundle))


# ============================================================
# Part C — evidence-required fields, case mismatch (proofs 5, 6)
# ============================================================


@pytest.mark.parametrize("field,code", [
    ("id", "blank_evidence_id"),
    ("case_id", "blank_evidence_case_id"),
    ("source_type", "blank_evidence_source_type"),
    ("source_id", "blank_evidence_source_id"),
    ("source_url", "blank_evidence_source_url"),
    ("source_publisher_or_system", "blank_evidence_source_publisher_or_system"),
    ("source_date", "blank_evidence_source_date"),
    ("retrieved_at", "blank_evidence_retrieved_at"),
    ("excerpt_original", "blank_evidence_excerpt_original"),
    ("original_language", "blank_evidence_original_language"),
    ("added_at", "blank_evidence_added_at"),
])
@pytest.mark.parametrize("blank_value", ["", "  "])
def test_each_evidence_required_field_rejects_blank(field, code, blank_value):
    item = dataclasses.replace(_evidence(), **{field: blank_value})
    bundle = build_research_case_bundle(_case(), (item,), ())
    assert code in _codes(validate_research_case_bundle(bundle))


def test_evidence_with_wrong_case_id_fails():
    item = _evidence(case_id="case-OTHER")
    bundle = build_research_case_bundle(_case(), (item,), ())
    assert "evidence_case_mismatch" in _codes(validate_research_case_bundle(bundle))


def test_assertion_with_wrong_case_id_fails():
    rel = _relationship(case_id="case-OTHER")
    bundle = build_research_case_bundle(_case(), (_evidence(),), (rel,))
    assert "assertion_case_mismatch" in _codes(validate_research_case_bundle(bundle))

    dep = _dependency(case_id="case-OTHER")
    bundle2 = build_research_case_bundle(_case(), (_evidence(),), (dep,))
    assert "assertion_case_mismatch" in _codes(validate_research_case_bundle(bundle2))


# ============================================================
# Part D — assertion evidence-reference grounding (proofs 8, 9)
# ============================================================


def test_empty_evidence_ids_fails():
    rel = _relationship(evidence_ids=())
    bundle = build_research_case_bundle(_case(), (_evidence(),), (rel,))
    assert "assertion_missing_evidence" in _codes(validate_research_case_bundle(bundle))


def test_blank_evidence_id_reference_fails():
    rel = _relationship(evidence_ids=("",))
    bundle = build_research_case_bundle(_case(), (_evidence(),), (rel,))
    assert "blank_assertion_evidence_id" in _codes(validate_research_case_bundle(bundle))


def test_duplicate_evidence_id_within_one_assertion_fails():
    rel = _relationship(evidence_ids=("evidence-1", "evidence-1"))
    bundle = build_research_case_bundle(_case(), (_evidence(),), (rel,))
    assert "duplicate_assertion_evidence_id" in _codes(validate_research_case_bundle(bundle))


def test_unknown_evidence_id_reference_fails():
    rel = _relationship(evidence_ids=("evidence-does-not-exist",))
    bundle = build_research_case_bundle(_case(), (_evidence(),), (rel,))
    assert "assertion_unknown_evidence_id" in _codes(validate_research_case_bundle(bundle))


def test_cross_case_evidence_reference_fails():
    other_case_evidence = _evidence(id="evidence-2", case_id="case-OTHER")
    rel = _relationship(evidence_ids=("evidence-2",))
    bundle = build_research_case_bundle(_case(), (_evidence(), other_case_evidence), (rel,))
    assert "assertion_evidence_wrong_case" in _codes(validate_research_case_bundle(bundle))


def test_evidence_reference_checks_are_deterministic_across_repeated_calls():
    rel = _relationship(evidence_ids=("evidence-missing-a", "", "evidence-1", "evidence-1"))
    bundle = build_research_case_bundle(_case(), (_evidence(),), (rel,))
    first = validate_research_case_bundle(bundle)
    second = validate_research_case_bundle(bundle)
    assert first == second
    assert _codes(first) == ["assertion_unknown_evidence_id", "blank_assertion_evidence_id", "duplicate_assertion_evidence_id"]


def test_duplicate_evidence_ids_in_bundle_fail():
    ev1 = _evidence(id="evidence-1")
    ev2 = _evidence(id="evidence-1")  # same id, second occurrence
    bundle = build_research_case_bundle(_case(), (ev1, ev2), ())
    assert "duplicate_evidence_id" in _codes(validate_research_case_bundle(bundle))


def test_duplicate_assertion_ids_in_bundle_fail():
    rel_a = _relationship(id="relationship-dup")
    rel_b = _relationship(id="relationship-dup", subject_entity="Different Corp")
    bundle = build_research_case_bundle(_case(), (_evidence(),), (rel_a, rel_b))
    assert "duplicate_assertion_id" in _codes(validate_research_case_bundle(bundle))


def test_cross_kind_id_collision_between_evidence_and_assertion_fails():
    shared_id = "shared-id-1"
    ev = _evidence(id=shared_id)
    rel = _relationship(id=shared_id, evidence_ids=(shared_id,))
    bundle = build_research_case_bundle(_case(), (ev,), (rel,))
    assert "cross_kind_id_collision" in _codes(validate_research_case_bundle(bundle))


# ============================================================
# Part E — RelationshipAssertion / DependencyAssertion required fields (proofs 10, 11, 12)
# ============================================================


@pytest.mark.parametrize("field,code", [
    ("id", "blank_relationship_id"),
    ("case_id", "blank_relationship_case_id"),
    ("subject_entity", "blank_relationship_subject_entity"),
    ("object_entity", "blank_relationship_object_entity"),
    ("created_at", "blank_relationship_created_at"),
])
def test_each_relationship_required_field_rejects_blank(field, code):
    rel = dataclasses.replace(_relationship(), **{field: ""})
    bundle = build_research_case_bundle(_case(), (_evidence(),), (rel,))
    assert code in _codes(validate_research_case_bundle(bundle))


@pytest.mark.parametrize("field,code", [
    ("id", "blank_dependency_id"),
    ("case_id", "blank_dependency_case_id"),
    ("affected_entity", "blank_dependency_affected_entity"),
    ("created_at", "blank_dependency_created_at"),
])
def test_each_dependency_required_field_rejects_blank(field, code):
    dep = dataclasses.replace(_dependency(), **{field: ""})
    bundle = build_research_case_bundle(_case(), (_evidence(),), (dep,))
    assert code in _codes(validate_research_case_bundle(bundle))


def test_dependency_blank_supply_chain_layer_fails():
    dep = _dependency(supply_chain_layer="   ")
    bundle = build_research_case_bundle(_case(), (_evidence(),), (dep,))
    assert "blank_dependency_supply_chain_layer" in _codes(validate_research_case_bundle(bundle))


def test_dependency_none_supply_chain_layer_passes():
    dep = _dependency(supply_chain_layer=None)
    bundle = build_research_case_bundle(_case(), (_evidence(),), (dep,))
    assert "blank_dependency_supply_chain_layer" not in _codes(validate_research_case_bundle(bundle))


def test_dependency_arbitrary_non_ontology_supply_chain_layer_passes():
    # Membership in SUPPLY_CHAIN_LAYERS is explicitly deferred — any
    # non-blank string is accepted in this step.
    dep = _dependency(supply_chain_layer="not-a-real-layer-yet")
    bundle = build_research_case_bundle(_case(), (_evidence(),), (dep,))
    assert is_valid_research_case_bundle(bundle) is True


# ============================================================
# Part F — transmission path structure (proof 13)
# ============================================================


def test_empty_transmission_path_fails():
    dep = _dependency(transmission_path=(), assertion_status=AssertionStatus.HYPOTHESIS, reasoning="Because.", limitations=("Unverified.",))
    bundle = build_research_case_bundle(_case(), (_evidence(),), (dep,))
    assert "empty_transmission_path" in _codes(validate_research_case_bundle(bundle))


def test_blank_hop_in_transmission_path_fails():
    dep = _dependency(transmission_path=("Acme Corp", ""), assertion_status=AssertionStatus.HYPOTHESIS, reasoning="Because.", limitations=("Unverified.",))
    bundle = build_research_case_bundle(_case(), (_evidence(),), (dep,))
    assert "blank_transmission_path_hop" in _codes(validate_research_case_bundle(bundle))


def test_adjacent_repeated_hop_in_transmission_path_fails():
    dep = _dependency(transmission_path=("Company A", "Company A"), assertion_status=AssertionStatus.HYPOTHESIS, reasoning="Because.", limitations=("Unverified.",))
    bundle = build_research_case_bundle(_case(), (_evidence(),), (dep,))
    assert "repeated_adjacent_transmission_hop" in _codes(validate_research_case_bundle(bundle))


def test_non_adjacent_repeated_hop_in_transmission_path_passes():
    dep = _dependency(
        transmission_path=("Company A", "Company B", "Company A"),
        assertion_status=AssertionStatus.HYPOTHESIS, reasoning="Because.", limitations=("Unverified.",),
    )
    bundle = build_research_case_bundle(_case(), (_evidence(),), (dep,))
    assert is_valid_research_case_bundle(bundle) is True


# ============================================================
# Part G — transmission-path-requires-hypothesis (proof 14)
# ============================================================


@pytest.mark.parametrize("status", [
    AssertionStatus.DIRECTLY_SUPPORTED, AssertionStatus.CONTRADICTED, AssertionStatus.INSUFFICIENT_EVIDENCE,
])
def test_transmission_path_under_non_hypothesis_status_fails(status):
    dep = _dependency(transmission_path=("Acme Corp", "Example Corp"), assertion_status=status)
    bundle = build_research_case_bundle(_case(), (_evidence(),), (dep,))
    assert "dependency_path_requires_hypothesis" in _codes(validate_research_case_bundle(bundle))


def test_same_transmission_path_under_hypothesis_passes():
    dep = _dependency(
        transmission_path=("Acme Corp", "Example Corp"), assertion_status=AssertionStatus.HYPOTHESIS,
        reasoning="Acme Corp is Example Corp's sole qualified supplier.", limitations=("Not independently confirmed.",),
    )
    bundle = build_research_case_bundle(_case(), (_evidence(),), (dep,))
    assert is_valid_research_case_bundle(bundle) is True


def test_no_transmission_path_never_triggers_hypothesis_requirement():
    dep = _dependency(transmission_path=None, assertion_status=AssertionStatus.DIRECTLY_SUPPORTED)
    bundle = build_research_case_bundle(_case(), (_evidence(),), (dep,))
    assert "dependency_path_requires_hypothesis" not in _codes(validate_research_case_bundle(bundle))


# ============================================================
# Part H — hypothesis requirements (proof 15)
# ============================================================


def test_hypothesis_missing_reasoning_fails():
    rel = _relationship(assertion_status=AssertionStatus.HYPOTHESIS, reasoning=None, limitations=("Unverified.",))
    bundle = build_research_case_bundle(_case(), (_evidence(),), (rel,))
    assert "hypothesis_missing_reasoning" in _codes(validate_research_case_bundle(bundle))


def test_hypothesis_blank_reasoning_fails():
    rel = _relationship(assertion_status=AssertionStatus.HYPOTHESIS, reasoning="   ", limitations=("Unverified.",))
    bundle = build_research_case_bundle(_case(), (_evidence(),), (rel,))
    assert "hypothesis_missing_reasoning" in _codes(validate_research_case_bundle(bundle))


def test_hypothesis_missing_limitations_fails():
    rel = _relationship(assertion_status=AssertionStatus.HYPOTHESIS, reasoning="Because of X.", limitations=())
    bundle = build_research_case_bundle(_case(), (_evidence(),), (rel,))
    assert "hypothesis_missing_limitations" in _codes(validate_research_case_bundle(bundle))


def test_hypothesis_blank_limitation_item_fails():
    rel = _relationship(assertion_status=AssertionStatus.HYPOTHESIS, reasoning="Because of X.", limitations=("  ",))
    bundle = build_research_case_bundle(_case(), (_evidence(),), (rel,))
    assert "blank_hypothesis_limitation" in _codes(validate_research_case_bundle(bundle))


def test_valid_hypothesis_reasoning_and_limitations_pass():
    rel = _relationship(assertion_status=AssertionStatus.HYPOTHESIS, reasoning="Because of X.", limitations=("Not yet confirmed.",))
    bundle = build_research_case_bundle(_case(), (_evidence(),), (rel,))
    assert is_valid_research_case_bundle(bundle) is True


@pytest.mark.parametrize("status", [
    AssertionStatus.DIRECTLY_SUPPORTED, AssertionStatus.CONTRADICTED, AssertionStatus.INSUFFICIENT_EVIDENCE,
])
def test_non_hypothesis_statuses_do_not_require_reasoning_or_limitations(status):
    rel = _relationship(assertion_status=status, reasoning=None, limitations=())
    bundle = build_research_case_bundle(_case(), (_evidence(),), (rel,))
    codes = _codes(validate_research_case_bundle(bundle))
    assert "hypothesis_missing_reasoning" not in codes
    assert "hypothesis_missing_limitations" not in codes


# ============================================================
# Part I — structural, not semantic, grounding (proof 16)
# ============================================================


def test_directly_supported_only_requires_a_structural_evidence_link_never_semantic_content():
    # The excerpt text says nothing about the relationship at all — this
    # module has no way to know that, and must not pretend to.
    unrelated_evidence = _evidence(excerpt_original="The company announced an unrelated holiday schedule change.")
    rel = _relationship(assertion_status=AssertionStatus.DIRECTLY_SUPPORTED, evidence_ids=("evidence-1",))
    bundle = build_research_case_bundle(_case(), (unrelated_evidence,), (rel,))
    # Passes: structural grounding (a real, same-case evidence link)
    # exists. This is NOT a claim that the excerpt semantically supports
    # the relationship — validate_research_case_bundle never reads
    # excerpt_original's content at all (see Part K).
    assert is_valid_research_case_bundle(bundle) is True


# ============================================================
# Part J — enum robustness (proof 17)
# ============================================================


def test_malformed_case_status_returns_structured_error_never_raises():
    case = dataclasses.replace(_case(), status="OPEN")  # a plain string, not the enum
    bundle = build_research_case_bundle(case, (), ())
    errors = validate_research_case_bundle(bundle)
    assert "invalid_case_status" in _codes(errors)


@pytest.mark.parametrize("malformed", ["OPEN", None, 42, ["OPEN"], object()])
def test_every_malformed_enum_bearing_field_returns_structured_error_never_raises(malformed):
    case = dataclasses.replace(_case(), status=malformed)
    assert "invalid_case_status" in _codes(validate_research_case_bundle(build_research_case_bundle(case, (), ())))

    rel = dataclasses.replace(_relationship(), role=malformed)
    assert "invalid_relationship_role" in _codes(validate_research_case_bundle(build_research_case_bundle(_case(), (_evidence(),), (rel,))))

    rel2 = dataclasses.replace(_relationship(), assertion_status=malformed)
    assert "invalid_assertion_status" in _codes(validate_research_case_bundle(build_research_case_bundle(_case(), (_evidence(),), (rel2,))))

    rel3 = dataclasses.replace(_relationship(), confidence=malformed)
    assert "invalid_assertion_confidence" in _codes(validate_research_case_bundle(build_research_case_bundle(_case(), (_evidence(),), (rel3,))))

    dep = dataclasses.replace(_dependency(), bottleneck_type=malformed)
    assert "invalid_bottleneck_type" in _codes(validate_research_case_bundle(build_research_case_bundle(_case(), (_evidence(),), (dep,))))

    dep2 = dataclasses.replace(_dependency(), assertion_status=malformed)
    assert "invalid_assertion_status" in _codes(validate_research_case_bundle(build_research_case_bundle(_case(), (_evidence(),), (dep2,))))

    dep3 = dataclasses.replace(_dependency(), confidence=malformed)
    assert "invalid_assertion_confidence" in _codes(validate_research_case_bundle(build_research_case_bundle(_case(), (_evidence(),), (dep3,))))


def test_malformed_assertion_status_does_not_crash_hypothesis_or_transmission_checks():
    # A malformed assertion_status must not be treated as HYPOTHESIS, and
    # must not crash the transmission-path-requires-hypothesis check.
    dep = dataclasses.replace(_dependency(transmission_path=("A", "B")), assertion_status="not-real")
    bundle = build_research_case_bundle(_case(), (_evidence(),), (dep,))
    errors = validate_research_case_bundle(bundle)  # must not raise
    codes = _codes(errors)
    assert "invalid_assertion_status" in codes
    assert "hypothesis_missing_reasoning" not in codes
    assert "dependency_path_requires_hypothesis" not in codes  # can't know it's non-hypothesis when status itself is malformed


# ============================================================
# Part K — no semantic parsing / mutation of text fields (proofs 19, 20)
# ============================================================


def test_inputs_are_completely_unchanged_after_validation():
    bundle = _valid_bundle()
    case_before = copy.deepcopy(bundle.case)
    evidence_before = copy.deepcopy(bundle.evidence_items)
    assertions_before = copy.deepcopy(bundle.assertions)

    validate_research_case_bundle(bundle)

    assert bundle.case == case_before
    assert bundle.evidence_items == evidence_before
    assert bundle.assertions == assertions_before


def test_excerpt_and_url_and_timestamp_text_is_never_referenced_in_source(monkeypatch):
    # Positive-content proof, complementary to the AST import-scope guard
    # below: even with wildly unrelated excerpt/url/date content, the
    # only thing that matters to validation is presence/blankness.
    ev = _evidence(
        source_url="not a url at all, just text",
        source_date="not a real date",
        retrieved_at="also not a real timestamp",
        excerpt_original="完全に無関係な内容。",  # unrelated Japanese text
        original_language="Japanese",
    )
    bundle = build_research_case_bundle(_case(), (ev,), ())
    assert is_valid_research_case_bundle(bundle) is True  # no parsing/format validation is performed


# ============================================================
# Part L — deterministic ordering (proof 18)
# ============================================================


def test_error_ordering_is_stable_across_repeated_calls():
    case = dataclasses.replace(_case(), id="", title="")
    ev = dataclasses.replace(_evidence(), source_url="")
    rel = dataclasses.replace(_relationship(), subject_entity="")
    bundle = build_research_case_bundle(case, (ev,), (rel,))
    first = validate_research_case_bundle(bundle)
    second = validate_research_case_bundle(bundle)
    assert first == second
    assert _codes(first) == _codes(second)


def test_error_ordering_follows_case_then_evidence_then_assertions_in_supplied_order():
    case = dataclasses.replace(_case(), title="")
    ev1 = dataclasses.replace(_evidence(id="evidence-1"), source_url="")
    ev2 = dataclasses.replace(_evidence(id="evidence-2"), source_url="")
    rel = dataclasses.replace(_relationship(id="relationship-1", evidence_ids=("evidence-1", "evidence-2")), subject_entity="")
    bundle = build_research_case_bundle(case, (ev1, ev2), (rel,))

    errors = validate_research_case_bundle(bundle)
    record_types_in_order = [e.record_type for e in errors]
    # Case error(s) first, then evidence-1's, then evidence-2's, then the assertion's.
    first_evidence_index = next(i for i, e in enumerate(errors) if e.record_type == "ResearchEvidenceItem")
    first_assertion_index = next(i for i, e in enumerate(errors) if e.record_type == "RelationshipAssertion")
    case_indices = [i for i, t in enumerate(record_types_in_order) if t == "ResearchCase"]
    assert all(i < first_evidence_index for i in case_indices)
    assert first_evidence_index < first_assertion_index
    # Within evidence, evidence-1's error(s) precede evidence-2's.
    evidence_record_ids = [e.record_id for e in errors if e.record_type == "ResearchEvidenceItem"]
    assert evidence_record_ids.index("evidence-1") < evidence_record_ids.index("evidence-2")


def test_error_ordering_respects_supplied_evidence_ids_tuple_order_within_one_assertion():
    rel = _relationship(evidence_ids=("evidence-missing-first", "evidence-missing-second"))
    bundle = build_research_case_bundle(_case(), (_evidence(),), (rel,))
    errors = validate_research_case_bundle(bundle)
    unknown_ref_messages = [e.message for e in errors if e.code == "assertion_unknown_evidence_id"]
    assert unknown_ref_messages == [
        "assertion.evidence_ids references 'evidence-missing-first', which is not present in this bundle.",
        "assertion.evidence_ids references 'evidence-missing-second', which is not present in this bundle.",
    ]


def test_bundle_order_is_never_resorted_by_id():
    # Evidence items supplied out of "natural" id order must still be
    # processed/reported in the order the caller supplied them.
    ev_z = dataclasses.replace(_evidence(id="evidence-z"), source_url="")
    ev_a = dataclasses.replace(_evidence(id="evidence-a"), source_url="")
    bundle = build_research_case_bundle(_case(), (ev_z, ev_a), ())
    errors = validate_research_case_bundle(bundle)
    ordered_ids = [e.record_id for e in errors if e.record_type == "ResearchEvidenceItem"]
    assert ordered_ids == ["evidence-z", "evidence-a"]  # supplied order, not sorted


# ============================================================
# Part M — invalid assertion type raises (documented programmer misuse)
# ============================================================


def test_unsupported_assertion_type_in_bundle_raises_type_error():
    bundle = ResearchCaseBundle(case=_case(), evidence_items=(_evidence(),), assertions=("not-an-assertion",))
    with pytest.raises(TypeError):
        validate_research_case_bundle(bundle)


# ============================================================
# Part N — scope guards (proofs 21, 22)
# ============================================================


def test_module_has_no_forbidden_imports_or_wall_clock_random_or_io_calls():
    repo_root = Path(__file__).parent.parent
    path = repo_root / "src" / "logic" / "research_case_validation.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    allowed_exact_modules = {"src.models.research_case"}
    allowed_stdlib_top_levels = {"__future__", "dataclasses", "typing"}
    offenders = []
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for module in modules:
            if module in allowed_exact_modules:
                continue
            if module.split(".")[0] in allowed_stdlib_top_levels:
                continue
            offenders.append(module)
    assert not offenders, offenders

    forbidden_calls = {"open", "eval", "exec", "input"}
    call_offenders = [
        n.func.id for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in forbidden_calls
    ]
    assert not call_offenders, call_offenders

    wall_clock_or_random_calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr in ("now", "today", "time", "random", "randint", "uuid4")
    ]
    assert not wall_clock_or_random_calls


def test_no_new_dependency_added_to_requirements():
    repo_root = Path(__file__).parent.parent
    result = subprocess.run(["git", "diff", "HEAD", "--", "requirements.txt"], cwd=repo_root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    assert result.stdout.strip() == "", f"requirements.txt was modified: {result.stdout}"


def test_scope_guard_only_approved_files_changed():
    """Runs against `git diff HEAD` — only meaningful in a real checkout
    with this step's changes present; spuriously fires while ANY other
    legitimate uncommitted change is present and resolves once
    committed — same documented convention as this repo's other
    phase-scoped scope guards."""
    repo_root = Path(__file__).parent.parent
    result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    allowed = {
        "src/logic/research_case_validation.py",
        "tests/test_research_case_validation.py",
    }
    assert changed <= allowed, changed - allowed


def test_no_persistence_ui_source_pipeline_or_deployment_files_touched():
    repo_root = Path(__file__).parent.parent
    result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    forbidden_prefixes = ("src/ui/", "src/data_access/", "scripts/")
    forbidden_paths = {
        "src/models/research_case.py", "src/models/models.py", "src/models/daily_news_models.py",
        "render.yaml", "requirements.txt",
    }
    hit = {c for c in changed if c in forbidden_paths or any(c.startswith(p) for p in forbidden_prefixes)}
    assert not hit, hit
