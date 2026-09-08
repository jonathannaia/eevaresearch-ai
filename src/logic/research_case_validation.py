"""EevaResearch Phase 4, Step 2 (design/DECISIONS.md) — pure structural
validation and bundle assembly for the immutable Research Case model
family (src.models.research_case). No I/O, no persistence call, no
network/HTTP/subprocess/environment/time/random call, no LLM/model-
provider call, no UI/Radar/Daily News/source-client/pipeline import.

This module checks *structure*, never *substance*: it confirms every
required field is present, every id reference resolves within the
supplied bundle, and every enum-typed field actually holds a member of
its declared enum — it never reads an excerpt's text to decide whether
it "really" supports an assertion, never resolves an entity name, never
infers a bottleneck type, and never judges whether a hypothesis is
plausible. "Directly supported" here means only "this assertion links
to at least one evidence item in this bundle" — a structural fact, not
a semantic verification that the linked excerpt actually says what the
assertion claims.

Every function is pure: given the same bundle, `validate_research_case_
bundle` always returns the same tuple of errors in the same order.
Validation never mutates its input and never raises for ordinary
invalid *content* (a blank field, an unknown reference, a malformed
enum value) — those are reported as `ResearchCaseValidationError`
values, not exceptions. An exception is raised only when a supplied
`assertions` entry is neither a `RelationshipAssertion` nor a
`DependencyAssertion` at all — that is a caller-side type-contract
violation with no valid record shape to report structured issues about,
not a validatable content problem."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

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


@dataclass(frozen=True)
class ResearchCaseValidationError:
    """One structural validation issue. `record_type`/`record_id` are
    optional context for a caller that wants to group/display issues per
    record — `None` when an issue is about the bundle as a whole (or
    about a record whose own id is itself blank/malformed, in which case
    there is no usable id to attach)."""

    code: str
    message: str
    record_type: str | None = None
    record_id: str | None = None


@dataclass(frozen=True)
class ResearchCaseBundle:
    """One case plus its evidence and assertions, exactly as the caller
    supplied them — tuple-normalized only, nothing generated, nothing
    reordered."""

    case: ResearchCase
    evidence_items: tuple[ResearchEvidenceItem, ...]
    assertions: tuple[RelationshipAssertion | DependencyAssertion, ...]


def build_research_case_bundle(
    case: ResearchCase,
    evidence_items: Sequence[ResearchEvidenceItem],
    assertions: Sequence[RelationshipAssertion | DependencyAssertion],
) -> ResearchCaseBundle:
    """Tuple-normalizes the supplied collections only — never generates
    an id, a timestamp, a record, an evidence item, or an assertion, and
    never mutates any input. A bundle always holds exactly one case by
    construction (the field is a single `ResearchCase`, not a
    collection) — there is no separate "exactly one case" runtime check
    to write, since the type itself makes more or less than one
    impossible for a caller who respects this function's signature."""
    return ResearchCaseBundle(case=case, evidence_items=tuple(evidence_items), assertions=tuple(assertions))


def is_valid_research_case_bundle(bundle: ResearchCaseBundle) -> bool:
    return len(validate_research_case_bundle(bundle)) == 0


def _is_blank(value: object) -> bool:
    """True for anything that isn't a non-empty, non-whitespace-only
    string — including `None` or a wrong-typed value entirely. This is
    the one place "blank" is defined for every field-presence check in
    this module, so a caller-supplied non-string value is always safely
    treated as invalid/blank rather than raising `AttributeError` on
    `.strip()`."""
    return not isinstance(value, str) or not value.strip()


def _display_id(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _blank_error(value: object, code: str, field_label: str, record_type: str, record_id: str | None) -> ResearchCaseValidationError | None:
    if _is_blank(value):
        return ResearchCaseValidationError(code=code, message=f"{field_label} must not be blank.", record_type=record_type, record_id=record_id)
    return None


def _enum_error(
    value: object, expected_enum: type, code: str, field_label: str, record_type: str, record_id: str | None,
) -> ResearchCaseValidationError | None:
    """Never raises for a malformed enum-like value — a plain string, a
    None, a different enum class, or any other object is simply reported
    as invalid, never coerced or repaired."""
    if isinstance(value, expected_enum):
        return None
    return ResearchCaseValidationError(
        code=code, message=f"{field_label} is not a valid {expected_enum.__name__} value.",
        record_type=record_type, record_id=record_id,
    )


def _validate_case(case: ResearchCase) -> list[ResearchCaseValidationError]:
    record_id = _display_id(getattr(case, "id", None))
    errors: list[ResearchCaseValidationError] = []
    for value, code, label in (
        (case.id, "blank_case_id", "case.id"),
        (case.trigger_source_type, "blank_case_trigger_source_type", "case.trigger_source_type"),
        (case.trigger_source_id, "blank_case_trigger_source_id", "case.trigger_source_id"),
        (case.trigger_source_name, "blank_case_trigger_source_name", "case.trigger_source_name"),
        (case.trigger_summary, "blank_case_trigger_summary", "case.trigger_summary"),
        (case.title, "blank_case_title", "case.title"),
        (case.research_question, "blank_case_research_question", "case.research_question"),
        (case.created_at, "blank_case_created_at", "case.created_at"),
    ):
        error = _blank_error(value, code, label, "ResearchCase", record_id)
        if error is not None:
            errors.append(error)

    if not isinstance(case.version, int) or isinstance(case.version, bool) or case.version < 1:
        errors.append(ResearchCaseValidationError(
            code="invalid_case_version", message="case.version must be an integer >= 1.",
            record_type="ResearchCase", record_id=record_id,
        ))

    status_error = _enum_error(case.status, ResearchCaseStatus, "invalid_case_status", "case.status", "ResearchCase", record_id)
    if status_error is not None:
        errors.append(status_error)

    return errors


def _validate_evidence_items(
    case: ResearchCase, evidence_items: tuple[ResearchEvidenceItem, ...],
) -> tuple[list[ResearchCaseValidationError], dict[str, str]]:
    """Returns (errors, seen_ids) — seen_ids maps every non-blank
    evidence id encountered to "evidence", threaded into assertion
    validation below so a cross-kind id collision (an assertion reusing
    an evidence id) can be detected without a second bundle-wide pass."""
    errors: list[ResearchCaseValidationError] = []
    seen_ids: dict[str, str] = {}

    for item in evidence_items:
        record_id = _display_id(getattr(item, "id", None))
        for value, code, label in (
            (item.id, "blank_evidence_id", "evidence.id"),
            (item.case_id, "blank_evidence_case_id", "evidence.case_id"),
            (item.source_type, "blank_evidence_source_type", "evidence.source_type"),
            (item.source_id, "blank_evidence_source_id", "evidence.source_id"),
            (item.source_url, "blank_evidence_source_url", "evidence.source_url"),
            (item.source_publisher_or_system, "blank_evidence_source_publisher_or_system", "evidence.source_publisher_or_system"),
            (item.source_date, "blank_evidence_source_date", "evidence.source_date"),
            (item.retrieved_at, "blank_evidence_retrieved_at", "evidence.retrieved_at"),
            (item.excerpt_original, "blank_evidence_excerpt_original", "evidence.excerpt_original"),
            (item.original_language, "blank_evidence_original_language", "evidence.original_language"),
            (item.added_at, "blank_evidence_added_at", "evidence.added_at"),
        ):
            error = _blank_error(value, code, label, "ResearchEvidenceItem", record_id)
            if error is not None:
                errors.append(error)

        if not _is_blank(item.case_id) and item.case_id != case.id:
            errors.append(ResearchCaseValidationError(
                code="evidence_case_mismatch",
                message=f"evidence.case_id {item.case_id!r} does not match the bundle's case id.",
                record_type="ResearchEvidenceItem", record_id=record_id,
            ))

        if not _is_blank(item.id):
            if item.id in seen_ids:
                errors.append(ResearchCaseValidationError(
                    code="duplicate_evidence_id", message=f"Evidence id {item.id!r} is used by more than one evidence item.",
                    record_type="ResearchEvidenceItem", record_id=record_id,
                ))
            else:
                seen_ids[item.id] = "evidence"

    return errors, seen_ids


def _validate_evidence_ids_reference(
    evidence_ids: tuple[str, ...],
    evidence_by_id: dict[str, ResearchEvidenceItem],
    case: ResearchCase,
    record_type: str,
    record_id: str | None,
) -> list[ResearchCaseValidationError]:
    errors: list[ResearchCaseValidationError] = []
    if len(evidence_ids) == 0:
        errors.append(ResearchCaseValidationError(
            code="assertion_missing_evidence", message="assertion.evidence_ids must be non-empty.",
            record_type=record_type, record_id=record_id,
        ))

    seen_in_this_assertion: set[str] = set()
    for evidence_id in evidence_ids:
        if _is_blank(evidence_id):
            errors.append(ResearchCaseValidationError(
                code="blank_assertion_evidence_id", message="assertion.evidence_ids must not contain a blank id.",
                record_type=record_type, record_id=record_id,
            ))
            continue
        if evidence_id in seen_in_this_assertion:
            errors.append(ResearchCaseValidationError(
                code="duplicate_assertion_evidence_id",
                message=f"assertion.evidence_ids contains {evidence_id!r} more than once.",
                record_type=record_type, record_id=record_id,
            ))
            continue
        seen_in_this_assertion.add(evidence_id)

        referenced = evidence_by_id.get(evidence_id)
        if referenced is None:
            errors.append(ResearchCaseValidationError(
                code="assertion_unknown_evidence_id",
                message=f"assertion.evidence_ids references {evidence_id!r}, which is not present in this bundle.",
                record_type=record_type, record_id=record_id,
            ))
            continue
        if referenced.case_id != case.id:
            errors.append(ResearchCaseValidationError(
                code="assertion_evidence_wrong_case",
                message=f"assertion.evidence_ids references {evidence_id!r}, which belongs to a different case.",
                record_type=record_type, record_id=record_id,
            ))

    return errors


def _validate_hypothesis_requirements(
    assertion_status: object, reasoning: object, limitations: object, record_type: str, record_id: str | None,
) -> list[ResearchCaseValidationError]:
    if assertion_status is not AssertionStatus.HYPOTHESIS:
        return []
    errors: list[ResearchCaseValidationError] = []
    if _is_blank(reasoning):
        errors.append(ResearchCaseValidationError(
            code="hypothesis_missing_reasoning", message="A HYPOTHESIS assertion must include non-blank reasoning.",
            record_type=record_type, record_id=record_id,
        ))
    if not isinstance(limitations, tuple) or len(limitations) == 0:
        errors.append(ResearchCaseValidationError(
            code="hypothesis_missing_limitations", message="A HYPOTHESIS assertion must include at least one limitation.",
            record_type=record_type, record_id=record_id,
        ))
    elif any(_is_blank(item) for item in limitations):
        errors.append(ResearchCaseValidationError(
            code="blank_hypothesis_limitation", message="A HYPOTHESIS assertion's limitations must not contain a blank entry.",
            record_type=record_type, record_id=record_id,
        ))
    return errors


def _validate_relationship_assertion(
    assertion: RelationshipAssertion, case: ResearchCase, evidence_by_id: dict[str, ResearchEvidenceItem], seen_ids: dict[str, str],
) -> list[ResearchCaseValidationError]:
    record_type = "RelationshipAssertion"
    record_id = _display_id(getattr(assertion, "id", None))
    errors: list[ResearchCaseValidationError] = []

    id_error = _blank_error(assertion.id, "blank_relationship_id", "relationship.id", record_type, record_id)
    if id_error is not None:
        errors.append(id_error)
    elif assertion.id in seen_ids:
        origin = seen_ids[assertion.id]
        code = "duplicate_assertion_id" if origin == "assertion" else "cross_kind_id_collision"
        errors.append(ResearchCaseValidationError(
            code=code, message=f"Assertion id {assertion.id!r} is already used by another {origin} record.",
            record_type=record_type, record_id=record_id,
        ))
    else:
        seen_ids[assertion.id] = "assertion"

    case_id_error = _blank_error(assertion.case_id, "blank_relationship_case_id", "relationship.case_id", record_type, record_id)
    if case_id_error is not None:
        errors.append(case_id_error)
    elif assertion.case_id != case.id:
        errors.append(ResearchCaseValidationError(
            code="assertion_case_mismatch", message=f"relationship.case_id {assertion.case_id!r} does not match the bundle's case id.",
            record_type=record_type, record_id=record_id,
        ))

    for value, code, label in (
        (assertion.subject_entity, "blank_relationship_subject_entity", "relationship.subject_entity"),
        (assertion.object_entity, "blank_relationship_object_entity", "relationship.object_entity"),
        (assertion.created_at, "blank_relationship_created_at", "relationship.created_at"),
    ):
        error = _blank_error(value, code, label, record_type, record_id)
        if error is not None:
            errors.append(error)

    role_error = _enum_error(assertion.role, RelationshipRole, "invalid_relationship_role", "relationship.role", record_type, record_id)
    if role_error is not None:
        errors.append(role_error)

    status_error = _enum_error(assertion.assertion_status, AssertionStatus, "invalid_assertion_status", "relationship.assertion_status", record_type, record_id)
    if status_error is not None:
        errors.append(status_error)

    confidence_error = _enum_error(assertion.confidence, AssertionConfidence, "invalid_assertion_confidence", "relationship.confidence", record_type, record_id)
    if confidence_error is not None:
        errors.append(confidence_error)

    if isinstance(assertion.evidence_ids, tuple):
        errors.extend(_validate_evidence_ids_reference(assertion.evidence_ids, evidence_by_id, case, record_type, record_id))
    else:
        errors.append(ResearchCaseValidationError(
            code="assertion_missing_evidence", message="relationship.evidence_ids must be a tuple of ids.",
            record_type=record_type, record_id=record_id,
        ))

    if isinstance(assertion.assertion_status, AssertionStatus):
        errors.extend(_validate_hypothesis_requirements(assertion.assertion_status, assertion.reasoning, assertion.limitations, record_type, record_id))

    return errors


def _validate_dependency_assertion(
    assertion: DependencyAssertion, case: ResearchCase, evidence_by_id: dict[str, ResearchEvidenceItem], seen_ids: dict[str, str],
) -> list[ResearchCaseValidationError]:
    record_type = "DependencyAssertion"
    record_id = _display_id(getattr(assertion, "id", None))
    errors: list[ResearchCaseValidationError] = []

    id_error = _blank_error(assertion.id, "blank_dependency_id", "dependency.id", record_type, record_id)
    if id_error is not None:
        errors.append(id_error)
    elif assertion.id in seen_ids:
        origin = seen_ids[assertion.id]
        code = "duplicate_assertion_id" if origin == "assertion" else "cross_kind_id_collision"
        errors.append(ResearchCaseValidationError(
            code=code, message=f"Assertion id {assertion.id!r} is already used by another {origin} record.",
            record_type=record_type, record_id=record_id,
        ))
    else:
        seen_ids[assertion.id] = "assertion"

    case_id_error = _blank_error(assertion.case_id, "blank_dependency_case_id", "dependency.case_id", record_type, record_id)
    if case_id_error is not None:
        errors.append(case_id_error)
    elif assertion.case_id != case.id:
        errors.append(ResearchCaseValidationError(
            code="assertion_case_mismatch", message=f"dependency.case_id {assertion.case_id!r} does not match the bundle's case id.",
            record_type=record_type, record_id=record_id,
        ))

    for value, code, label in (
        (assertion.affected_entity, "blank_dependency_affected_entity", "dependency.affected_entity"),
        (assertion.created_at, "blank_dependency_created_at", "dependency.created_at"),
    ):
        error = _blank_error(value, code, label, record_type, record_id)
        if error is not None:
            errors.append(error)

    if assertion.supply_chain_layer is not None and _is_blank(assertion.supply_chain_layer):
        errors.append(ResearchCaseValidationError(
            code="blank_dependency_supply_chain_layer",
            message="dependency.supply_chain_layer, when supplied, must not be blank.",
            record_type=record_type, record_id=record_id,
        ))

    transmission_path = assertion.transmission_path
    if transmission_path is not None:
        if not isinstance(transmission_path, tuple) or len(transmission_path) == 0:
            errors.append(ResearchCaseValidationError(
                code="empty_transmission_path", message="dependency.transmission_path, when supplied, must not be empty.",
                record_type=record_type, record_id=record_id,
            ))
        else:
            previous_hop: object = None
            has_previous = False
            for hop in transmission_path:
                if _is_blank(hop):
                    errors.append(ResearchCaseValidationError(
                        code="blank_transmission_path_hop", message="dependency.transmission_path must not contain a blank hop.",
                        record_type=record_type, record_id=record_id,
                    ))
                elif has_previous and hop == previous_hop:
                    errors.append(ResearchCaseValidationError(
                        code="repeated_adjacent_transmission_hop",
                        message=f"dependency.transmission_path repeats {hop!r} in two adjacent hops.",
                        record_type=record_type, record_id=record_id,
                    ))
                previous_hop = hop
                has_previous = True

    bottleneck_error = _enum_error(assertion.bottleneck_type, BottleneckType, "invalid_bottleneck_type", "dependency.bottleneck_type", record_type, record_id)
    if bottleneck_error is not None:
        errors.append(bottleneck_error)

    status_error = _enum_error(assertion.assertion_status, AssertionStatus, "invalid_assertion_status", "dependency.assertion_status", record_type, record_id)
    if status_error is not None:
        errors.append(status_error)

    confidence_error = _enum_error(assertion.confidence, AssertionConfidence, "invalid_assertion_confidence", "dependency.confidence", record_type, record_id)
    if confidence_error is not None:
        errors.append(confidence_error)

    if transmission_path is not None and isinstance(assertion.assertion_status, AssertionStatus) and assertion.assertion_status is not AssertionStatus.HYPOTHESIS:
        errors.append(ResearchCaseValidationError(
            code="dependency_path_requires_hypothesis",
            message="A DependencyAssertion with a transmission_path must have assertion_status == HYPOTHESIS.",
            record_type=record_type, record_id=record_id,
        ))

    if isinstance(assertion.evidence_ids, tuple):
        errors.extend(_validate_evidence_ids_reference(assertion.evidence_ids, evidence_by_id, case, record_type, record_id))
    else:
        errors.append(ResearchCaseValidationError(
            code="assertion_missing_evidence", message="dependency.evidence_ids must be a tuple of ids.",
            record_type=record_type, record_id=record_id,
        ))

    if isinstance(assertion.assertion_status, AssertionStatus):
        errors.extend(_validate_hypothesis_requirements(assertion.assertion_status, assertion.reasoning, assertion.limitations, record_type, record_id))

    return errors


def validate_research_case_bundle(bundle: ResearchCaseBundle) -> tuple[ResearchCaseValidationError, ...]:
    """Deterministic: the same bundle always produces the same error
    tuple in the same order. Order is: case-level errors; then every
    evidence item's own errors, in supplied tuple order; then every
    assertion's own errors, in supplied tuple order; within one record,
    fields are checked in the fixed order documented on each helper
    above — never sorted by id, never based on set/dict iteration.

    Raises `TypeError` only when `bundle.assertions` contains a value
    that is neither a `RelationshipAssertion` nor a `DependencyAssertion`
    — a caller-side type-contract violation with no valid record shape
    to report a structured issue about, not an ordinary content
    problem."""
    errors: list[ResearchCaseValidationError] = []
    errors.extend(_validate_case(bundle.case))

    evidence_errors, seen_ids = _validate_evidence_items(bundle.case, bundle.evidence_items)
    errors.extend(evidence_errors)
    evidence_by_id = {item.id: item for item in bundle.evidence_items if not _is_blank(item.id)}

    for assertion in bundle.assertions:
        if isinstance(assertion, RelationshipAssertion):
            errors.extend(_validate_relationship_assertion(assertion, bundle.case, evidence_by_id, seen_ids))
        elif isinstance(assertion, DependencyAssertion):
            errors.extend(_validate_dependency_assertion(assertion, bundle.case, evidence_by_id, seen_ids))
        else:
            raise TypeError(f"Unsupported research-assertion type in bundle.assertions: {type(assertion)!r}")

    return tuple(errors)
