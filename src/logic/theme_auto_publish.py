"""EevaResearch — autonomous Theme candidate detection, Phase 2 (design/
DECISIONS.md). A pure, deterministic engine evaluating whether one
internal `ResearchTheme` meets every explicit gate required for
autonomous publication. Used identically by both
scripts/radar_worker.py's own auto-publish step and
src/ui/pages/theme_workspace.py's live "auto-publish eligibility"
display — one source of truth for the gate logic, never duplicated.

This module performs no I/O of any kind: no persistence call, no
network/source fetch, no LLM/model call, no UI call, no random value,
no system-clock read. It never mutates its `theme`/`evidence`/`notes`
inputs and never decides *whether* to actually publish — it only
evaluates and reports; the caller (the worker's own auto-publish step)
is the one place that acts on an eligible result, and only when
`settings.theme_auto_publish_enabled` is explicitly True.

Gate (a) — distinct companies — counts every distinct company that
contributed SUPPORTING official evidence anywhere across the relevant
supply-chain chain (upstream supplier, constraint owner, capacity
expander, downstream customer, infrastructure provider, or any other
role) without requiring they share a role — see this module's own
`_distinct_supporting_companies` docstring."""
from __future__ import annotations

from dataclasses import dataclass

from src.models.theme_research import (
    EvidenceDirection,
    HypothesisConfidence,
    ResearchTheme,
    ThemeEvidenceItem,
    ThemeNoteType,
    ThemeResearchNote,
)

_MIN_DISTINCT_COMPANIES = 3
_MIN_SUPPORTING_EVIDENCE = 3

# Deterministic, multi-word phrases only — deliberately avoids single
# common words (e.g. bare "buy") that would false-positive on ordinary
# prose ("the buyer disclosed...", "a buyout offer..."). Case-insensitive.
_FORBIDDEN_OUTPUT_PHRASES: tuple[str, ...] = (
    "price target", "price objective", "buy rating", "sell rating", "hold rating",
    "strong buy", "strong sell", "overweight rating", "underweight rating",
    "buy recommendation", "sell recommendation", "recommend buying", "recommend selling",
    "trade instruction", "investment recommendation", "12-month target", "month price target",
)

_THEME_TEXT_FIELDS: tuple[str, ...] = (
    "title", "key_question", "hypothesis", "working_thesis", "why_it_matters",
    "what_could_change_the_view", "what_to_watch_next",
)


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    detail: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class AutoPublishEvaluation:
    eligible: bool
    gates: tuple[GateResult, ...]
    audit_summary: str


def _nonblank(value: object) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _distinct_supporting_companies(evidence: tuple[ThemeEvidenceItem, ...]) -> tuple[str, ...]:
    """Every distinct company with at least one SUPPORTS-direction
    evidence item, regardless of role — a company may be an upstream
    supplier, the constraint owner, a capacity expander, a downstream
    customer, or an infrastructure provider; this gate never requires
    they share a role, and this module never assigns or reads a role at
    all (roles live on ThemeCompanyMapEntry, a separate, human-curated
    record this module does not consult)."""
    return tuple(sorted({
        item.company for item in evidence
        if item.direction is EvidenceDirection.SUPPORTS and _nonblank(item.company)
    }))


def _supporting_evidence(evidence: tuple[ThemeEvidenceItem, ...]) -> tuple[ThemeEvidenceItem, ...]:
    return tuple(item for item in evidence if item.direction is EvidenceDirection.SUPPORTS)


def _contradicting_evidence(evidence: tuple[ThemeEvidenceItem, ...]) -> tuple[ThemeEvidenceItem, ...]:
    return tuple(item for item in evidence if item.direction is EvidenceDirection.CONTRADICTS)


def _hypothesis_notes(notes: tuple[ThemeResearchNote, ...]) -> tuple[ThemeResearchNote, ...]:
    return tuple(n for n in notes if n.note_type is ThemeNoteType.HYPOTHESIS)


def _contains_forbidden_phrase(theme: ResearchTheme) -> str | None:
    for field_name in _THEME_TEXT_FIELDS:
        value = getattr(theme, field_name, None)
        if not isinstance(value, str):
            continue
        lowered = value.lower()
        for phrase in _FORBIDDEN_OUTPUT_PHRASES:
            if phrase in lowered:
                return f"{field_name} contains forbidden phrase {phrase!r}"
    return None


def evaluate_auto_publish_gates(
    theme: ResearchTheme,
    evidence: tuple[ThemeEvidenceItem, ...],
    notes: tuple[ThemeResearchNote, ...],
) -> AutoPublishEvaluation:
    """Pure, deterministic, no I/O, no clock read. Never raises for
    malformed input. Evaluates all seven gates independently (every
    gate is always evaluated and reported, even if an earlier one
    already failed) so a caller/UI can show every gate's own status,
    not just the first failure."""
    gates: list[GateResult] = []

    supporting = _supporting_evidence(evidence)
    contradicting = _contradicting_evidence(evidence)
    distinct_companies = _distinct_supporting_companies(evidence)
    hypotheses = _hypothesis_notes(notes)
    high_confidence_hypotheses = tuple(n for n in hypotheses if n.confidence is HypothesisConfidence.HIGH)
    disconfirming_hypotheses = tuple(n for n in hypotheses if _nonblank(n.disconfirming_condition))

    gates.append(GateResult(
        name="distinct_companies",
        passed=len(distinct_companies) >= _MIN_DISTINCT_COMPANIES,
        detail=f"{len(distinct_companies)} distinct companies with supporting evidence (need >= {_MIN_DISTINCT_COMPANIES}): {', '.join(distinct_companies) or 'none'}.",
        evidence_ids=tuple(sorted(item.id for item in supporting)),
    ))

    gates.append(GateResult(
        name="supporting_evidence_count",
        passed=len(supporting) >= _MIN_SUPPORTING_EVIDENCE,
        detail=f"{len(supporting)} supporting evidence items (need >= {_MIN_SUPPORTING_EVIDENCE}).",
        evidence_ids=tuple(sorted(item.id for item in supporting)),
    ))

    testable_question = _nonblank(theme.key_question) and theme.key_question.strip().endswith("?")
    gates.append(GateResult(
        name="testable_research_question",
        passed=testable_question,
        detail="key_question is non-blank and phrased as a question." if testable_question else "key_question is blank or not phrased as a testable question.",
    ))

    gates.append(GateResult(
        name="disconfirming_condition",
        passed=len(disconfirming_hypotheses) > 0,
        detail=f"{len(disconfirming_hypotheses)} hypothesis note(s) with an explicit disconfirming condition.",
    ))

    gates.append(GateResult(
        name="no_unresolved_contradiction",
        passed=len(contradicting) == 0,
        detail="No CONTRADICTS-direction evidence." if not contradicting else f"{len(contradicting)} unresolved CONTRADICTS-direction evidence item(s) block auto-publication.",
        evidence_ids=tuple(sorted(item.id for item in contradicting)),
    ))

    gates.append(GateResult(
        name="high_confidence_hypothesis",
        passed=len(high_confidence_hypotheses) > 0,
        detail=f"{len(high_confidence_hypotheses)} HIGH-confidence hypothesis note(s).",
    ))

    forbidden_reason = _contains_forbidden_phrase(theme)
    gates.append(GateResult(
        name="safe_output",
        passed=forbidden_reason is None,
        detail="No investment-recommendation language detected." if forbidden_reason is None else forbidden_reason,
    ))

    eligible = all(g.passed for g in gates)
    audit_lines = [f"Auto-publish evaluation ({'ELIGIBLE' if eligible else 'not eligible'}):"]
    for gate in gates:
        audit_lines.append(f"  [{'PASS' if gate.passed else 'FAIL'}] {gate.name}: {gate.detail}")
    audit_summary = "\n".join(audit_lines)

    return AutoPublishEvaluation(eligible=eligible, gates=tuple(gates), audit_summary=audit_summary)
