"""EevaResearch Phase 4, Step 1 (design/DECISIONS.md) — append-only JSON
persistence for the immutable Research Case model family
(src.models.research_case). Three dedicated sibling files, never mixed
into any existing candidate/comparison/Daily News/filing-event/signal
store:

    research_cases.json
    research_evidence_items.json
    research_assertions.json  (both RelationshipAssertion and
                                DependencyAssertion, discriminated by a
                                "kind" field — see _assertion_to_dict)

INSERT-only by construction: there is deliberately no update/replace/
upsert/delete function anywhere in this module. A duplicate append
(same stable id) is a safe no-op that leaves the existing record
untouched, matching dart.candidate_store.upsert_new_candidates' and
comparison_store.append_comparison_record's own established convention.

No wall-clock reads anywhere in this module — every timestamp used to
derive an id is caller-supplied. No import of CandidateSignal,
FilingEvent, NewsStory, any source client, or any UI type — only
src.models.research_case and the standard library."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
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

_CASES_FILENAME = "research_cases.json"
_EVIDENCE_FILENAME = "research_evidence_items.json"
_ASSERTIONS_FILENAME = "research_assertions.json"

_ID_DIGEST_CHARS = 24


# --- Deterministic, content-addressed ID factories ------------------------
#
# Same sha256-truncated-hex technique already established twice in this
# codebase (comparison_store.build_comparison_record_id,
# daily_news_pipeline._story_id) — never randomness, never wall-clock
# state. Every input is an already-immutable, caller-supplied field; the
# hash composition (documented per function) is the exact "stable ID
# strategy" this phase's approval requires be explained.


def build_case_id(trigger_source_type: str, trigger_source_id: str, created_at: str) -> str:
    """Derived from (trigger_source_type, trigger_source_id, created_at).
    A genuinely later case opened for the same trigger (a different
    created_at) gets a different id; re-deriving with identical inputs
    always reproduces the same id, which is what makes a duplicate
    append safely detectable."""
    digest = hashlib.sha256(f"{trigger_source_type}|{trigger_source_id}|{created_at}".encode("utf-8")).hexdigest()
    return f"case-{digest[:_ID_DIGEST_CHARS]}"


def build_evidence_id(case_id: str, source_type: str, source_id: str, added_at: str) -> str:
    """Derived from (case_id, source_type, source_id, added_at)."""
    digest = hashlib.sha256(f"{case_id}|{source_type}|{source_id}|{added_at}".encode("utf-8")).hexdigest()
    return f"evidence-{digest[:_ID_DIGEST_CHARS]}"


def build_relationship_assertion_id(
    case_id: str, subject_entity: str, object_entity: str, role: RelationshipRole, created_at: str,
) -> str:
    """Derived from (case_id, subject_entity, object_entity, role.value,
    created_at). Always hashes the enum's `.value` string, never the
    enum object itself or its repr, so the id is stable regardless of
    how a given Python version formats an Enum in an f-string."""
    digest = hashlib.sha256(
        f"{case_id}|{subject_entity}|{object_entity}|{role.value}|{created_at}".encode("utf-8")
    ).hexdigest()
    return f"relationship-{digest[:_ID_DIGEST_CHARS]}"


def build_dependency_assertion_id(
    case_id: str, affected_entity: str, bottleneck_type: BottleneckType, created_at: str,
) -> str:
    """Derived from (case_id, affected_entity, bottleneck_type.value,
    created_at)."""
    digest = hashlib.sha256(
        f"{case_id}|{affected_entity}|{bottleneck_type.value}|{created_at}".encode("utf-8")
    ).hexdigest()
    return f"dependency-{digest[:_ID_DIGEST_CHARS]}"


# --- Research cases ---------------------------------------------------------


def _case_from_dict(data: dict) -> ResearchCase:
    return ResearchCase(
        id=data["id"],
        trigger_source_type=data["trigger_source_type"],
        trigger_source_id=data["trigger_source_id"],
        trigger_source_name=data["trigger_source_name"],
        trigger_summary=data["trigger_summary"],
        title=data["title"],
        research_question=data["research_question"],
        status=ResearchCaseStatus(data["status"]),
        created_at=data["created_at"],
        version=data["version"],
    )


def load_research_cases(cache_dir: Path, filename: str = _CASES_FILENAME) -> dict[str, ResearchCase]:
    """Never raises — a missing file (every installation before this
    phase) or a corrupt one is treated as an empty collection, matching
    every other store in this codebase's tolerant-load convention. An
    individual entry that fails to reconstruct is skipped rather than
    aborting the whole load."""
    path = cache_dir / filename
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, ResearchCase] = {}
    for case_id, data in raw.items():
        try:
            result[case_id] = _case_from_dict(data)
        except (KeyError, TypeError, ValueError):
            continue
    return result


def get_research_case(cache_dir: Path, case_id: str, filename: str = _CASES_FILENAME) -> ResearchCase | None:
    """Read-only single-case lookup. Never triggers creation."""
    return load_research_cases(cache_dir, filename).get(case_id)


def _save_research_cases(cache_dir: Path, cases: dict[str, ResearchCase], filename: str = _CASES_FILENAME) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {case_id: asdict(case) for case_id, case in cases.items()}
    (cache_dir / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_research_case(cache_dir: Path, case: ResearchCase, filename: str = _CASES_FILENAME) -> bool:
    """INSERT-only: returns True when this was a new id (the case was
    written), or False when a case with this exact id already existed —
    the already-persisted case is left completely untouched, never
    overwritten."""
    cases = load_research_cases(cache_dir, filename)
    if case.id in cases:
        return False
    cases[case.id] = case
    _save_research_cases(cache_dir, cases, filename)
    return True


# --- Evidence items ----------------------------------------------------------


def _evidence_item_from_dict(data: dict) -> ResearchEvidenceItem:
    return ResearchEvidenceItem(
        id=data["id"],
        case_id=data["case_id"],
        source_type=data["source_type"],
        source_id=data["source_id"],
        source_url=data["source_url"],
        source_publisher_or_system=data["source_publisher_or_system"],
        source_date=data["source_date"],
        retrieved_at=data["retrieved_at"],
        excerpt_original=data["excerpt_original"],
        original_language=data["original_language"],
        added_at=data["added_at"],
        excerpt_translated=data.get("excerpt_translated"),
        translation_provider=data.get("translation_provider"),
    )


def load_evidence_items(cache_dir: Path, filename: str = _EVIDENCE_FILENAME) -> dict[str, ResearchEvidenceItem]:
    path = cache_dir / filename
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, ResearchEvidenceItem] = {}
    for item_id, data in raw.items():
        try:
            result[item_id] = _evidence_item_from_dict(data)
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _save_evidence_items(cache_dir: Path, items: dict[str, ResearchEvidenceItem], filename: str = _EVIDENCE_FILENAME) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {item_id: asdict(item) for item_id, item in items.items()}
    (cache_dir / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_evidence_item(cache_dir: Path, item: ResearchEvidenceItem, filename: str = _EVIDENCE_FILENAME) -> bool:
    """INSERT-only — see append_research_case's own docstring for the
    exact duplicate-id contract, identical here."""
    items = load_evidence_items(cache_dir, filename)
    if item.id in items:
        return False
    items[item.id] = item
    _save_evidence_items(cache_dir, items, filename)
    return True


def evidence_items_for_case_ids(
    cache_dir: Path, case_ids: Sequence[str], filename: str = _EVIDENCE_FILENAME,
) -> dict[str, tuple[ResearchEvidenceItem, ...]]:
    """Bulk, read-only: every evidence item for each of the supplied case
    ids, in exactly one load_evidence_items() call for a non-empty
    request (never one load per case id). Empty input returns `{}`
    immediately without touching the filesystem at all. Each case's
    items are deterministically ordered by (added_at, id) ascending —
    never file/dict iteration order. Case ids with no evidence are
    simply absent from the result."""
    if not case_ids:
        return {}
    requested = set(case_ids)
    by_case: dict[str, list[ResearchEvidenceItem]] = {}
    for item in load_evidence_items(cache_dir, filename).values():
        if item.case_id in requested:
            by_case.setdefault(item.case_id, []).append(item)
    return {
        case_id: tuple(sorted(items, key=lambda i: (i.added_at, i.id)))
        for case_id, items in by_case.items()
    }


# --- Assertions (relationship + dependency, one shared store) --------------


def _assertion_to_dict(assertion: RelationshipAssertion | DependencyAssertion) -> dict:
    if isinstance(assertion, RelationshipAssertion):
        payload = asdict(assertion)
        payload["kind"] = "relationship"
        return payload
    if isinstance(assertion, DependencyAssertion):
        payload = asdict(assertion)
        payload["kind"] = "dependency"
        return payload
    raise TypeError(f"Unsupported research-assertion type: {type(assertion)!r}")


def _assertion_from_dict(data: dict) -> RelationshipAssertion | DependencyAssertion:
    kind = data.get("kind")
    if kind == "relationship":
        return RelationshipAssertion(
            id=data["id"],
            case_id=data["case_id"],
            subject_entity=data["subject_entity"],
            object_entity=data["object_entity"],
            role=RelationshipRole(data["role"]),
            assertion_status=AssertionStatus(data["assertion_status"]),
            evidence_ids=tuple(data.get("evidence_ids", ())),
            confidence=AssertionConfidence(data["confidence"]),
            created_at=data["created_at"],
            reasoning=data.get("reasoning"),
            limitations=tuple(data.get("limitations", ())),
        )
    if kind == "dependency":
        transmission_path = data.get("transmission_path")
        return DependencyAssertion(
            id=data["id"],
            case_id=data["case_id"],
            affected_entity=data["affected_entity"],
            bottleneck_type=BottleneckType(data["bottleneck_type"]),
            supply_chain_layer=data.get("supply_chain_layer"),
            transmission_path=tuple(transmission_path) if transmission_path is not None else None,
            assertion_status=AssertionStatus(data["assertion_status"]),
            evidence_ids=tuple(data.get("evidence_ids", ())),
            confidence=AssertionConfidence(data["confidence"]),
            created_at=data["created_at"],
            reasoning=data.get("reasoning"),
            limitations=tuple(data.get("limitations", ())),
        )
    raise ValueError(f"Unknown or missing research-assertion kind: {kind!r}")


def load_assertions(
    cache_dir: Path, filename: str = _ASSERTIONS_FILENAME,
) -> dict[str, RelationshipAssertion | DependencyAssertion]:
    path = cache_dir / filename
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, RelationshipAssertion | DependencyAssertion] = {}
    for assertion_id, data in raw.items():
        try:
            result[assertion_id] = _assertion_from_dict(data)
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _save_assertions(
    cache_dir: Path, assertions: dict[str, RelationshipAssertion | DependencyAssertion], filename: str = _ASSERTIONS_FILENAME,
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {assertion_id: _assertion_to_dict(assertion) for assertion_id, assertion in assertions.items()}
    (cache_dir / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_assertion(
    cache_dir: Path, assertion: RelationshipAssertion | DependencyAssertion, filename: str = _ASSERTIONS_FILENAME,
) -> bool:
    """INSERT-only — see append_research_case's own docstring for the
    exact duplicate-id contract, identical here. Accepts either a
    RelationshipAssertion or a DependencyAssertion; both persist into the
    one shared research_assertions.json file, discriminated by an
    internal "kind" field never exposed on the dataclasses themselves."""
    assertions = load_assertions(cache_dir, filename)
    if assertion.id in assertions:
        return False
    assertions[assertion.id] = assertion
    _save_assertions(cache_dir, assertions, filename)
    return True


def assertions_for_case_ids(
    cache_dir: Path, case_ids: Sequence[str], filename: str = _ASSERTIONS_FILENAME,
) -> dict[str, tuple[RelationshipAssertion | DependencyAssertion, ...]]:
    """Bulk, read-only counterpart to evidence_items_for_case_ids() —
    same one-load, empty-input-returns-{}-with-no-filesystem-touch, and
    deterministic (created_at, id) ascending ordering per case."""
    if not case_ids:
        return {}
    requested = set(case_ids)
    by_case: dict[str, list[RelationshipAssertion | DependencyAssertion]] = {}
    for assertion in load_assertions(cache_dir, filename).values():
        if assertion.case_id in requested:
            by_case.setdefault(assertion.case_id, []).append(assertion)
    return {
        case_id: tuple(sorted(assertions, key=lambda a: (a.created_at, a.id)))
        for case_id, assertions in by_case.items()
    }
