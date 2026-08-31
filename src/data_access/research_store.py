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
src.models.research_case, src.logic.research_case_validation, and the
standard library.

Phase 4, Step 3B (design/DECISIONS.md) additionally adds
append_research_case_bundle() — a single atomic, validation-first,
all-or-nothing write of one ResearchCaseBundle (its case, every evidence
item, every assertion) across all three JSON files at once. See that
function's own docstring for the exact crash-consistency limitation of
this local-JSON v1 implementation, which independent os.replace() calls
cannot fully eliminate across three separate files."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from src.logic.research_case_validation import ResearchCaseBundle, validate_research_case_bundle
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


def list_recent_cases(cache_dir: Path, limit: int, filename: str = _CASES_FILENAME) -> tuple[ResearchCase, ...]:
    """EevaResearch Phase 4, Step 3C (design/DECISIONS.md) — bounded,
    read-only, most-recent-first case list for the tester-facing Research
    Cases page. Exactly one load_research_cases() call for a positive
    limit (never a per-case load); `limit <= 0` returns an empty tuple
    immediately without loading anything. Deterministically ordered by
    (created_at DESC, id DESC) — never dict/file iteration order — and
    every `created_at` string is compared exactly as stored, never
    parsed, reformatted, or generated here."""
    if limit <= 0:
        return ()
    cases = load_research_cases(cache_dir, filename)
    ordered = sorted(cases.values(), key=lambda case: (case.created_at, case.id), reverse=True)
    return tuple(ordered[:limit])


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


# --- Atomic bundle persistence (Phase 4, Step 3B) --------------------------


def _best_effort_remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _write_temp_json(directory: Path, target_filename: str, payload: str) -> Path:
    """Writes `payload` to a new temp file in `directory` (the same
    directory as the eventual live file, so the later os.replace() is a
    same-filesystem atomic rename, never a cross-filesystem copy),
    flushed and fsynced before the file handle closes. Raises OSError on
    any failure, having already removed the temp file itself — the
    caller is responsible for cleaning up any *other*, earlier temp
    files from the same bundle attempt."""
    directory.mkdir(parents=True, exist_ok=True)
    fd, raw_temp_path = tempfile.mkstemp(prefix=f".{target_filename}.", suffix=".tmp", dir=directory)
    temp_path = Path(raw_temp_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        _best_effort_remove(temp_path)
        raise
    return temp_path


def append_research_case_bundle(cache_dir: Path, bundle: ResearchCaseBundle) -> bool:
    """Atomic, validation-first, all-or-nothing persistence of one
    ResearchCaseBundle across all three JSON stores at once — Phase 4,
    Step 3B. Never creates or modifies a model object, id, timestamp,
    quote, URL, entity, assertion, or validation result; it only decides
    whether, and how, to write exactly the records already present on
    `bundle`.

    Contract:
      1. validate_research_case_bundle(bundle) must return no errors, or
         this function returns False immediately with no filesystem
         access at all (not even a read).
      2. Every id already present in the currently-persisted stores
         (case id, any evidence id, any assertion id — checked against
         *both* the evidence and assertion stores, extending Step 2's
         own bundle-internal cross-kind-collision concern to already-
         persisted state) causes this function to return False with no
         write of any kind.
      3. Only once every check above passes does this function build
         complete proposed in-memory copies of all three stores, then
         serialize all three to JSON text — any serialization failure
         (a record that somehow isn't JSON-encodable) aborts with no
         filesystem write at all.
      4. Only once every proposed payload has serialized successfully
         does this function write each to a temporary sibling file
         (flushed and fsynced) in the same directory as its live
         counterpart. Any failure at this stage removes every temp file
         already created this call (best-effort) and leaves every live
         file completely untouched.
      5. Only once all three temp files exist does this function replace
         the three live files, one os.replace() call each — each
         individual replace is atomic on the same filesystem (POSIX
         rename semantics), so no live file is ever observed half-
         written.

    Explicit crash-consistency limitation (this local JSON v1 backend
    only): three independent os.replace() calls cannot be made a single
    atomic multi-file transaction — this function guarantees no partial
    state can result from an ordinary validation, duplicate-id,
    serialization, or temp-file-write failure (every one of those is
    caught before any live file is touched), but it does NOT and cannot
    guarantee durability across a process crash or power loss occurring
    *between* the first and a later os.replace() call — in that specific
    narrow window, a case row could become visible before its evidence
    and/or assertions do. This is a real, stated limitation of a local,
    dependency-free JSON backend, not a claim of ACID multi-file
    durability; SQLite/Postgres (see research_repository.py) close this
    exact gap via a real database transaction."""
    validation_errors = validate_research_case_bundle(bundle)
    if validation_errors:
        return False

    existing_cases = load_research_cases(cache_dir)
    existing_evidence = load_evidence_items(cache_dir)
    existing_assertions = load_assertions(cache_dir)

    if bundle.case.id in existing_cases:
        return False
    for item in bundle.evidence_items:
        if item.id in existing_evidence or item.id in existing_assertions:
            return False
    for assertion in bundle.assertions:
        if assertion.id in existing_assertions or assertion.id in existing_evidence:
            return False

    new_cases = dict(existing_cases)
    new_cases[bundle.case.id] = bundle.case
    new_evidence = dict(existing_evidence)
    for item in bundle.evidence_items:
        new_evidence[item.id] = item
    new_assertions = dict(existing_assertions)
    for assertion in bundle.assertions:
        new_assertions[assertion.id] = assertion

    try:
        cases_payload = json.dumps({cid: asdict(c) for cid, c in new_cases.items()}, ensure_ascii=False, indent=2)
        evidence_payload = json.dumps({iid: asdict(i) for iid, i in new_evidence.items()}, ensure_ascii=False, indent=2)
        assertions_payload = json.dumps(
            {aid: _assertion_to_dict(a) for aid, a in new_assertions.items()}, ensure_ascii=False, indent=2,
        )
    except (TypeError, ValueError):
        return False

    temp_paths: list[Path] = []
    try:
        temp_paths.append(_write_temp_json(cache_dir, _CASES_FILENAME, cases_payload))
        temp_paths.append(_write_temp_json(cache_dir, _EVIDENCE_FILENAME, evidence_payload))
        temp_paths.append(_write_temp_json(cache_dir, _ASSERTIONS_FILENAME, assertions_payload))
    except OSError:
        for temp_path in temp_paths:
            _best_effort_remove(temp_path)
        return False

    cases_temp, evidence_temp, assertions_temp = temp_paths
    try:
        os.replace(cases_temp, cache_dir / _CASES_FILENAME)
        os.replace(evidence_temp, cache_dir / _EVIDENCE_FILENAME)
        os.replace(assertions_temp, cache_dir / _ASSERTIONS_FILENAME)
    except OSError:
        # See this function's own docstring: a failure here is exactly
        # the narrow, stated, unavoidable crash-consistency window for
        # this local JSON backend — any temp file not yet replaced is
        # removed best-effort; any live file already replaced before the
        # failure remains replaced (this is the documented limitation,
        # not a bug to silently paper over).
        for temp_path in temp_paths:
            _best_effort_remove(temp_path)
        return False

    return True
