"""Durable-State Phase 2 — unit tests for the pure 7-gate auto-publish
evaluator (src/logic/theme_auto_publish.py). Each gate is tested in
isolation (all other gates held at a passing baseline) plus the
documented boundary conditions (exactly-at-threshold, one-under,
mixed-confidence, blank/whitespace fields, forbidden-phrase casing).
No I/O, no persistence — pure dataclass construction and assertions."""
from __future__ import annotations

from src.logic.theme_auto_publish import evaluate_auto_publish_gates
from src.models.theme_research import (
    EvidenceDirection,
    HypothesisConfidence,
    ResearchTheme,
    ThemeCategory,
    ThemeEvidenceItem,
    ThemeNoteType,
    ThemeResearchNote,
    ThemeStatus,
    ThemeVisibility,
)

_THEME_ID = "theme-1"


def _theme(**overrides) -> ResearchTheme:
    defaults = dict(
        id=_THEME_ID,
        category=ThemeCategory.BOTTLENECK,
        status=ThemeStatus.NEW,
        visibility=ThemeVisibility.INTERNAL,
        title="HBM capacity constraint",
        key_question="Will HBM supply remain the binding constraint on AI accelerator output through 2026?",
        hypothesis="HBM packaging capacity, not wafer supply, is the binding constraint.",
        working_thesis="Working thesis text.",
        why_it_matters="Matters because of AI accelerator supply chains.",
        what_could_change_the_view="A capacity expansion coming online early.",
        what_to_watch_next="Watch capex guidance.",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    return ResearchTheme(**defaults)


def _evidence(company: str, direction: EvidenceDirection, item_id: str | None = None) -> ThemeEvidenceItem:
    return ThemeEvidenceItem(
        id=item_id or f"ev-{company}-{direction.value}",
        theme_id=_THEME_ID,
        date="2026-01-01",
        company=company,
        source_name="SEC EDGAR",
        source_url="https://example.com/filing",
        fact="Some official fact.",
        relevance="Directly relevant.",
        direction=direction,
    )


def _hypothesis_note(
    confidence: HypothesisConfidence | None = HypothesisConfidence.HIGH,
    disconfirming_condition: str | None = "If capacity utilization drops below 70%, thesis is disconfirmed.",
    note_id: str = "note-1",
) -> ThemeResearchNote:
    return ThemeResearchNote(
        id=note_id,
        theme_id=_THEME_ID,
        note_type=ThemeNoteType.HYPOTHESIS,
        content="Hypothesis content.",
        confidence=confidence,
        disconfirming_condition=disconfirming_condition,
        created_at="2026-01-01T00:00:00+00:00",
    )


def _passing_baseline() -> tuple[ResearchTheme, tuple[ThemeEvidenceItem, ...], tuple[ThemeResearchNote, ...]]:
    theme = _theme()
    evidence = (
        _evidence("Company A", EvidenceDirection.SUPPORTS),
        _evidence("Company B", EvidenceDirection.SUPPORTS),
        _evidence("Company C", EvidenceDirection.SUPPORTS),
    )
    notes = (_hypothesis_note(),)
    return theme, evidence, notes


def test_fully_passing_baseline_is_eligible():
    theme, evidence, notes = _passing_baseline()
    evaluation = evaluate_auto_publish_gates(theme, evidence, notes)
    assert evaluation.eligible is True
    assert all(g.passed for g in evaluation.gates)
    assert len(evaluation.gates) == 7
    assert "ELIGIBLE" in evaluation.audit_summary


def test_gate_a_fails_with_two_distinct_companies_one_under_threshold():
    theme, _, notes = _passing_baseline()
    evidence = (
        _evidence("Company A", EvidenceDirection.SUPPORTS),
        _evidence("Company B", EvidenceDirection.SUPPORTS),
        _evidence("Company B", EvidenceDirection.SUPPORTS, item_id="ev-b-2"),
    )
    evaluation = evaluate_auto_publish_gates(theme, evidence, notes)
    assert evaluation.eligible is False
    gate = next(g for g in evaluation.gates if g.name == "distinct_companies")
    assert gate.passed is False


def test_gate_a_does_not_require_shared_role_across_companies():
    """Per the user's explicit clarification: three distinct companies
    supporting from anywhere in the supply chain qualifies, regardless
    of role — this module never reads or requires a role at all."""
    theme, _, notes = _passing_baseline()
    evidence = (
        _evidence("Upstream Supplier Co", EvidenceDirection.SUPPORTS),
        _evidence("Constraint Owner Co", EvidenceDirection.SUPPORTS),
        _evidence("Downstream Customer Co", EvidenceDirection.SUPPORTS),
    )
    evaluation = evaluate_auto_publish_gates(theme, evidence, notes)
    gate = next(g for g in evaluation.gates if g.name == "distinct_companies")
    assert gate.passed is True
    assert evaluation.eligible is True


def test_gate_a_exactly_at_threshold_of_three_passes():
    theme, evidence, notes = _passing_baseline()
    assert len({e.company for e in evidence}) == 3
    evaluation = evaluate_auto_publish_gates(theme, evidence, notes)
    gate = next(g for g in evaluation.gates if g.name == "distinct_companies")
    assert gate.passed is True


def test_gate_a_blank_company_name_not_counted():
    theme, _, notes = _passing_baseline()
    evidence = (
        _evidence("Company A", EvidenceDirection.SUPPORTS),
        _evidence("Company B", EvidenceDirection.SUPPORTS),
        _evidence("   ", EvidenceDirection.SUPPORTS, item_id="ev-blank"),
    )
    evaluation = evaluate_auto_publish_gates(theme, evidence, notes)
    gate = next(g for g in evaluation.gates if g.name == "distinct_companies")
    assert gate.passed is False


def test_gate_b_fails_with_two_supporting_items_one_under_threshold():
    theme, _, notes = _passing_baseline()
    evidence = (
        _evidence("Company A", EvidenceDirection.SUPPORTS),
        _evidence("Company B", EvidenceDirection.SUPPORTS),
        _evidence("Company C", EvidenceDirection.CONTEXT),
    )
    evaluation = evaluate_auto_publish_gates(theme, evidence, notes)
    gate = next(g for g in evaluation.gates if g.name == "supporting_evidence_count")
    assert gate.passed is False
    assert evaluation.eligible is False


def test_gate_c_fails_when_key_question_blank():
    theme, evidence, notes = _passing_baseline()
    theme = _theme(key_question="")
    evaluation = evaluate_auto_publish_gates(theme, evidence, notes)
    gate = next(g for g in evaluation.gates if g.name == "testable_research_question")
    assert gate.passed is False


def test_gate_c_fails_when_key_question_not_phrased_as_question():
    theme, evidence, notes = _passing_baseline()
    theme = _theme(key_question="HBM supply is the binding constraint.")
    evaluation = evaluate_auto_publish_gates(theme, evidence, notes)
    gate = next(g for g in evaluation.gates if g.name == "testable_research_question")
    assert gate.passed is False


def test_gate_c_fails_when_key_question_whitespace_only():
    theme, evidence, notes = _passing_baseline()
    theme = _theme(key_question="   ")
    evaluation = evaluate_auto_publish_gates(theme, evidence, notes)
    gate = next(g for g in evaluation.gates if g.name == "testable_research_question")
    assert gate.passed is False


def test_gate_d_fails_with_no_disconfirming_condition():
    theme, evidence, _ = _passing_baseline()
    notes = (_hypothesis_note(disconfirming_condition=None),)
    evaluation = evaluate_auto_publish_gates(theme, evidence, notes)
    gate = next(g for g in evaluation.gates if g.name == "disconfirming_condition")
    assert gate.passed is False


def test_gate_d_fails_with_blank_disconfirming_condition():
    theme, evidence, _ = _passing_baseline()
    notes = (_hypothesis_note(disconfirming_condition="   "),)
    evaluation = evaluate_auto_publish_gates(theme, evidence, notes)
    gate = next(g for g in evaluation.gates if g.name == "disconfirming_condition")
    assert gate.passed is False


def test_gate_e_fails_with_unresolved_contradicting_evidence():
    theme, evidence, notes = _passing_baseline()
    evidence = evidence + (_evidence("Company D", EvidenceDirection.CONTRADICTS),)
    evaluation = evaluate_auto_publish_gates(theme, evidence, notes)
    gate = next(g for g in evaluation.gates if g.name == "no_unresolved_contradiction")
    assert gate.passed is False
    assert evaluation.eligible is False


def test_gate_e_mixed_and_context_evidence_do_not_block():
    theme, evidence, notes = _passing_baseline()
    evidence = evidence + (
        _evidence("Company D", EvidenceDirection.MIXED),
        _evidence("Company E", EvidenceDirection.CONTEXT),
    )
    evaluation = evaluate_auto_publish_gates(theme, evidence, notes)
    gate = next(g for g in evaluation.gates if g.name == "no_unresolved_contradiction")
    assert gate.passed is True


def test_gate_f_fails_with_only_low_and_medium_confidence_hypotheses():
    theme, evidence, _ = _passing_baseline()
    notes = (
        _hypothesis_note(confidence=HypothesisConfidence.LOW, note_id="note-low"),
        _hypothesis_note(confidence=HypothesisConfidence.MEDIUM, note_id="note-med"),
    )
    evaluation = evaluate_auto_publish_gates(theme, evidence, notes)
    gate = next(g for g in evaluation.gates if g.name == "high_confidence_hypothesis")
    assert gate.passed is False
    assert evaluation.eligible is False


def test_gate_f_passes_with_one_high_confidence_among_mixed_hypotheses():
    theme, evidence, _ = _passing_baseline()
    notes = (
        _hypothesis_note(confidence=HypothesisConfidence.LOW, note_id="note-low"),
        _hypothesis_note(confidence=HypothesisConfidence.HIGH, note_id="note-high"),
    )
    evaluation = evaluate_auto_publish_gates(theme, evidence, notes)
    gate = next(g for g in evaluation.gates if g.name == "high_confidence_hypothesis")
    assert gate.passed is True


def test_gate_f_ignores_non_hypothesis_notes():
    theme, evidence, _ = _passing_baseline()
    notes = (
        ThemeResearchNote(
            id="decision-1", theme_id=_THEME_ID, note_type=ThemeNoteType.DECISION,
            content="A decision note, not a hypothesis.", confidence=HypothesisConfidence.HIGH,
            disconfirming_condition=None, created_at="2026-01-01T00:00:00+00:00",
        ),
    )
    evaluation = evaluate_auto_publish_gates(theme, evidence, notes)
    high_conf_gate = next(g for g in evaluation.gates if g.name == "high_confidence_hypothesis")
    disconfirm_gate = next(g for g in evaluation.gates if g.name == "disconfirming_condition")
    assert high_conf_gate.passed is False
    assert disconfirm_gate.passed is False


def test_gate_g_fails_on_forbidden_phrase_case_insensitive():
    theme, evidence, notes = _passing_baseline()
    theme = _theme(why_it_matters="Our 12-Month Price Target implies significant upside.")
    evaluation = evaluate_auto_publish_gates(theme, evidence, notes)
    gate = next(g for g in evaluation.gates if g.name == "safe_output")
    assert gate.passed is False
    assert evaluation.eligible is False


def test_gate_g_does_not_false_positive_on_ordinary_prose():
    theme, evidence, notes = _passing_baseline()
    theme = _theme(why_it_matters="The buyer disclosed a buyout offer for the smaller supplier.")
    evaluation = evaluate_auto_publish_gates(theme, evidence, notes)
    gate = next(g for g in evaluation.gates if g.name == "safe_output")
    assert gate.passed is True


def test_all_gates_are_always_evaluated_even_when_multiple_fail():
    theme = _theme(key_question="not a question")
    evidence = (_evidence("Company A", EvidenceDirection.CONTRADICTS),)
    notes = ()
    evaluation = evaluate_auto_publish_gates(theme, evidence, notes)
    assert len(evaluation.gates) == 7
    failing = {g.name for g in evaluation.gates if not g.passed}
    assert failing == {
        "distinct_companies", "supporting_evidence_count", "testable_research_question",
        "disconfirming_condition", "no_unresolved_contradiction", "high_confidence_hypothesis",
    }


def test_evaluator_never_raises_on_empty_inputs():
    theme = _theme(key_question="", why_it_matters="")
    evaluation = evaluate_auto_publish_gates(theme, (), ())
    assert evaluation.eligible is False
    assert len(evaluation.gates) == 7
