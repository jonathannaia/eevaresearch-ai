"""Radar evidence-packet foundation, Phase 3, Step 2 (design/DECISIONS.md)
— append-only persistence for the immutable ComparisonResult produced by
src.logic.prior_disclosure_comparison (Phase 3, Step 1). Defines the one
shared ComparisonRecord type (imported from here by both the SQLite and
Postgres comparison repositories, mirroring how src.models.models.
CandidateSignal is the one shared type every backend already persists)
plus this module's own JSON-file backend.

Pure persistence only: no pipeline hook, no scan/scheduler wiring, no UI
import, no EDGAR/DART/EDINET client import, no network call. This module
never calls the comparison algorithm itself — it only stores/loads
already-computed ComparisonResult values, verbatim, exactly as Phase 3
Step 1 produced them. Never mutates a ComparisonResult, never
recomputes/reinterprets a category, never truncates or translates an
excerpt.

INSERT-only by construction: there is deliberately no update/replace/
upsert/delete function anywhere in this module for a comparison record.
A recomputation is persisted as a brand-new record (a new, different
`computed_at` produces a different stable id — see
build_comparison_record_id); an attempt to append a record whose stable
id already exists is a safe no-op that leaves the existing record
completely untouched, never overwritten."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from src.logic.prior_disclosure_comparison import ComparisonResult

_CACHE_FILENAME = "comparison_records.json"


@dataclass(frozen=True)
class ComparisonRecord:
    """One append-only, immutable persisted record of a single computed
    prior-disclosure comparison. Wraps an already-computed, already-
    immutable ComparisonResult with the identity fields needed to store
    and query it — every field below is copied verbatim from the
    ComparisonResult that produced it (or from caller-supplied current-
    candidate identity), never recomputed or reinterpreted here.

    `current_source_name`/`current_corp_code`/`current_document_id` are a
    deliberate denormalized COPY of the current candidate's own filing
    identity (same "copy at write time, never independently updated"
    convention `candidates.source` already uses in state_db/schema.py) —
    kept only for query/display efficiency, never a replacement for
    `current_candidate_id` as the actual reference.

    `prior_candidate_id` is optional and caller-supplied when known (the
    comparison algorithm itself only returns `prior_document_id`, a
    FilingEvent identifier, not a CandidateSignal id) — None is a fully
    valid, honest value, not a missing-data error."""

    id: str
    current_candidate_id: str
    current_source_name: str
    current_corp_code: str
    current_document_id: str
    prior_candidate_id: str | None
    prior_document_id: str | None
    prior_filed_at: str | None
    comparison_status: str
    comparison_basis: str
    added_categories: tuple[str, ...]
    removed_categories: tuple[str, ...]
    prior_excerpt: str | None
    current_excerpt: str | None
    limitations: tuple[str, ...]
    computed_at: str


def build_comparison_record_id(current_candidate_id: str, computed_at: str, comparison_basis: str) -> str:
    """Deterministic, content-addressed stable id: the same (current
    candidate id, computed_at, comparison basis) triple always produces
    the same id, and a genuinely later recomputation (a different
    caller-supplied computed_at) always produces a different id. Never
    reads wall-clock time or any other ambient/unpredictable state —
    every input is caller-supplied. Uses the same sha256-based technique
    already established for a content-derived cache key elsewhere in
    this codebase (src.data_access.translation.translation_service's own
    excerpt-hash cache key) — not imported from there, to keep this
    module's own import surface self-contained."""
    digest = hashlib.sha256(f"{current_candidate_id}|{computed_at}|{comparison_basis}".encode("utf-8")).hexdigest()
    return f"cmp-{digest[:24]}"


def build_comparison_record(
    result: ComparisonResult,
    current_candidate_id: str,
    current_source_name: str,
    current_corp_code: str,
    current_document_id: str,
    prior_candidate_id: str | None = None,
) -> ComparisonRecord:
    """The one safe way to turn an already-computed ComparisonResult into
    a persistable ComparisonRecord — every ComparisonResult field is
    copied verbatim (never recomputed/reinterpreted); the stable id is
    derived deterministically via build_comparison_record_id, never
    generated from wall-clock time or a random value."""
    record_id = build_comparison_record_id(current_candidate_id, result.computed_at, result.comparison_basis)
    return ComparisonRecord(
        id=record_id,
        current_candidate_id=current_candidate_id,
        current_source_name=current_source_name,
        current_corp_code=current_corp_code,
        current_document_id=current_document_id,
        prior_candidate_id=prior_candidate_id,
        prior_document_id=result.prior_document_id,
        prior_filed_at=result.prior_filed_at,
        comparison_status=result.comparison_status,
        comparison_basis=result.comparison_basis,
        added_categories=tuple(result.added_categories),
        removed_categories=tuple(result.removed_categories),
        prior_excerpt=result.prior_excerpt,
        current_excerpt=result.current_excerpt,
        limitations=tuple(result.limitations),
        computed_at=result.computed_at,
    )


def _record_from_dict(data: dict) -> ComparisonRecord:
    return ComparisonRecord(
        id=data["id"],
        current_candidate_id=data["current_candidate_id"],
        current_source_name=data["current_source_name"],
        current_corp_code=data["current_corp_code"],
        current_document_id=data["current_document_id"],
        prior_candidate_id=data.get("prior_candidate_id"),
        prior_document_id=data.get("prior_document_id"),
        prior_filed_at=data.get("prior_filed_at"),
        comparison_status=data["comparison_status"],
        comparison_basis=data["comparison_basis"],
        added_categories=tuple(data.get("added_categories", ())),
        removed_categories=tuple(data.get("removed_categories", ())),
        prior_excerpt=data.get("prior_excerpt"),
        current_excerpt=data.get("current_excerpt"),
        limitations=tuple(data.get("limitations", ())),
        computed_at=data["computed_at"],
    )


def _cache_path(cache_dir: Path, filename: str = _CACHE_FILENAME) -> Path:
    return cache_dir / filename


def load_comparison_records(cache_dir: Path, filename: str = _CACHE_FILENAME) -> dict[str, ComparisonRecord]:
    """Never raises — a missing file (every existing installation before
    this phase) or a corrupt one is treated as an empty collection,
    matching candidate_store.load_candidates' own tolerant-load
    convention. An individual entry that fails to reconstruct (a
    genuinely malformed row) is skipped rather than aborting the whole
    load."""
    path = _cache_path(cache_dir, filename)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, ComparisonRecord] = {}
    for record_id, data in raw.items():
        try:
            result[record_id] = _record_from_dict(data)
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _save_comparison_records(cache_dir: Path, records: dict[str, ComparisonRecord], filename: str = _CACHE_FILENAME) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {record_id: asdict(record) for record_id, record in records.items()}
    _cache_path(cache_dir, filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_comparison_record(cache_dir: Path, record: ComparisonRecord, filename: str = _CACHE_FILENAME) -> bool:
    """INSERT-only: writes `record` under its own stable id. Returns True
    when this was a new id (the record was written), or False when a
    record with this exact id already existed — in that case the
    already-persisted record is left completely untouched, never
    overwritten/merged, matching this codebase's established "duplicate
    insert is a safe no-op" convention (see
    dart.candidate_store.upsert_new_candidates)."""
    records = load_comparison_records(cache_dir, filename)
    if record.id in records:
        return False
    records[record.id] = record
    _save_comparison_records(cache_dir, records, filename)
    return True


def latest_comparison_record_for_candidate(
    cache_dir: Path, current_candidate_id: str, filename: str = _CACHE_FILENAME,
) -> ComparisonRecord | None:
    """Read-only query: the most recently computed record for one
    current-candidate id, ranked by `computed_at` (a caller-supplied ISO
    8601 string — lexicographic comparison is chronological order for a
    consistently-formatted ISO 8601 string, the same assumption this
    codebase's other stored-timestamp fields already rely on), with a
    deterministic `id` (stable record id) descending tiebreak for an
    exact `computed_at` tie — Phase 3, Step 3A: aligned with
    latest_comparison_records_for_candidate_ids' own tie rule below, so
    both functions agree on "latest" whenever both apply to the same
    data (see design/DECISIONS.md for the regression tests covering this
    alignment). Returns None when no record exists for that candidate.
    Never triggers a comparison computation itself — a pure repository
    read only."""
    matching = [r for r in load_comparison_records(cache_dir, filename).values() if r.current_candidate_id == current_candidate_id]
    if not matching:
        return None
    return max(matching, key=lambda r: (r.computed_at, r.id))


def latest_comparison_records_for_candidate_ids(
    cache_dir: Path, candidate_ids: Sequence[str], filename: str = _CACHE_FILENAME,
) -> dict[str, ComparisonRecord]:
    """Bulk counterpart to latest_comparison_record_for_candidate() —
    Phase 3, Step 3A. Exactly one load_comparison_records() call for a
    non-empty request (never one load per requested id); empty input
    returns `{}` immediately without touching the filesystem at all.
    Deterministic latest-per-candidate selection: `computed_at`
    descending, then stable record `id` descending on an exact tie — the
    same rule latest_comparison_record_for_candidate uses, never file/
    dict iteration order. Unrequested or unknown candidate ids are
    simply absent from the result, never fabricated. A pure read — never
    mutates any loaded record, never computes/inserts/updates anything."""
    if not candidate_ids:
        return {}
    requested = set(candidate_ids)
    latest_by_candidate: dict[str, ComparisonRecord] = {}
    for record in load_comparison_records(cache_dir, filename).values():
        if record.current_candidate_id not in requested:
            continue
        current_best = latest_by_candidate.get(record.current_candidate_id)
        if current_best is None or (record.computed_at, record.id) > (current_best.computed_at, current_best.id):
            latest_by_candidate[record.current_candidate_id] = record
    return latest_by_candidate
