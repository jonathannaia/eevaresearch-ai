"""Internal, rollback-safe import of a small, explicitly-approved set of
already-known FilingEvent/CandidateSignal records into the real DART/EDGAR
production caches — never a scan, never a network call, never a generic
"import anything" surface. Built to replace an earlier ad hoc direct-JSON-
mutation approach with a tested, transactional helper.

Every record must already be a fully-formed, bare, CANDIDATE_DETECTED
CandidateSignal (no excerpt, no reviewer decision) — this module creates
records, it never processes, extracts, reviews, or promotes them. Reuses
existing, unmodified APIs wherever one exists: candidate_store.py for the
candidate stores, and the exact temp-file + os.replace atomic-write
pattern already established by src/data_access/edinet/scan_service.py's
own _save_cache_atomic for the filing-event caches.

Transaction model: every file that could be touched (based on which
sources are present in the call) has its exact original bytes captured
before any write. If validation fails, nothing is touched at all — no
rollback is needed. If a write or post-write verification fails after the
operation has begun, every captured file is restored to its exact
original bytes (or removed, if it did not exist before), and a
CandidateBackfillError chained from the real failure is raised."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from src.data_access.dart import candidate_store
from src.data_access.edgar.scan_service import dedup_key as edgar_dedup_key
from src.models.models import CandidateSignal, CandidateStatus, FilingEvent

_DART_SOURCE = "OpenDART / DART"
_EDGAR_SOURCE = "SEC EDGAR"
_SUPPORTED_SOURCES = frozenset({_DART_SOURCE, _EDGAR_SOURCE})

_DART_FILING_EVENTS_FILENAME = "dart_filing_events.json"
_DART_CANDIDATES_FILENAME = "dart_candidates.json"
_EDGAR_FILING_EVENTS_FILENAME = "edgar_filing_events.json"
_EDGAR_CANDIDATES_FILENAME = "edgar_candidates.json"

_DART_CANDIDATE_ID_PREFIX = "cand-"
_EDGAR_CANDIDATE_ID_PREFIX = "edgar-cand-"


@dataclass(frozen=True)
class BackfillRecord:
    source: str
    candidate: CandidateSignal


@dataclass(frozen=True)
class BackfillResult:
    created_candidate_ids: tuple[str, ...]
    already_present_candidate_ids: tuple[str, ...]


class CandidateBackfillError(RuntimeError):
    """Raised only for a failure occurring after the operation has begun
    writing — always chained (`__cause__`) from the real underlying
    failure. Pre-write validation failures raise plain ValueError instead
    (see backfill_candidates' own docstring) since nothing has been
    touched yet and no rollback is involved."""


def _validate_record(record: BackfillRecord) -> None:
    if record.source not in _SUPPORTED_SOURCES:
        raise ValueError(
            f"backfill_candidates only supports {sorted(_SUPPORTED_SOURCES)!r} — got {record.source!r}."
        )
    candidate = record.candidate
    if record.source != candidate.filing.source_name:
        raise ValueError(
            f"Record source {record.source!r} does not match its filing's source_name "
            f"{candidate.filing.source_name!r} (candidate {candidate.id!r})."
        )
    if candidate.status != CandidateStatus.CANDIDATE_DETECTED:
        raise ValueError(
            f"Candidate {candidate.id!r} must be CANDIDATE_DETECTED to backfill — got {candidate.status!r}."
        )
    if candidate.reviewed_at:
        raise ValueError(f"Candidate {candidate.id!r} must not have reviewed_at set.")
    if candidate.reviewed_note:
        raise ValueError(f"Candidate {candidate.id!r} must not have a reviewed_note.")
    if candidate.excerpt_original is not None:
        raise ValueError(f"Candidate {candidate.id!r} must not have excerpt_original populated.")

    expected_prefix = _DART_CANDIDATE_ID_PREFIX if record.source == _DART_SOURCE else _EDGAR_CANDIDATE_ID_PREFIX
    if not candidate.id.startswith(expected_prefix):
        raise ValueError(
            f"Candidate id {candidate.id!r} does not match the expected {record.source!r} prefix {expected_prefix!r}."
        )
    if not candidate.filing.rcept_no:
        raise ValueError(f"Candidate {candidate.id!r} has no filing identifier (rcept_no) to dedup on.")
    if record.source == _EDGAR_SOURCE and not candidate.filing.corp_code:
        raise ValueError(f"EDGAR candidate {candidate.id!r} has no filing.corp_code (CIK) needed for its dedup key.")


def _dart_dedup_key(filing: FilingEvent) -> str:
    return filing.rcept_no


def _edgar_dedup_key(filing: FilingEvent) -> str:
    return edgar_dedup_key(filing.corp_code, filing.rcept_no)


def _empty_filing_events_cache(seen_field: str) -> dict:
    return {seen_field: [], "filing_events": [], "candidate_signals": []}


def _load_filing_events_cache(path: Path, seen_field: str) -> dict:
    if not path.exists():
        return _empty_filing_events_cache(seen_field)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return _empty_filing_events_cache(seen_field)
    for key, default in _empty_filing_events_cache(seen_field).items():
        raw.setdefault(key, default)
    return raw


def _build_source_update(
    cache_dir: Path, filing_events_filename: str, seen_field: str,
    dedup_key_fn: Callable[[FilingEvent], str], source_records: list[BackfillRecord],
) -> tuple[dict, list[str], list[str]]:
    """Pure — builds the updated filing-event cache in memory only, never
    writes. Returns (updated_cache, newly_created_ids, already_present_ids)."""
    path = cache_dir / filing_events_filename
    cache = _load_filing_events_cache(path, seen_field)
    seen = set(cache[seen_field])

    created: list[str] = []
    already_present: list[str] = []
    for record in source_records:
        candidate = record.candidate
        key = dedup_key_fn(candidate.filing)
        if key in seen:
            already_present.append(candidate.id)
            continue
        cache["filing_events"].append(asdict(candidate.filing))
        cache["candidate_signals"].append(asdict(candidate))
        seen.add(key)
        created.append(candidate.id)
    cache[seen_field] = sorted(seen)
    return cache, created, already_present


def _write_json_atomic(path: Path, payload: dict) -> None:
    """Exact temp-file + os.replace pattern already established by
    src/data_access/edinet/scan_service.py's _save_cache_atomic — reused,
    not reinvented, so a crash mid-write can never leave a partially-
    written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, indent=2))
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def _restore_files(original_bytes: dict[Path, bytes | None]) -> None:
    for path, content in original_bytes.items():
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(content)


def _assert_preserved(before_cache: dict, after_cache: dict, seen_field: str) -> None:
    """Every pre-existing filing_events/candidate_signals entry and every
    pre-existing dedup key must still be present, unchanged, after the
    write — a real preservation check, not just a count comparison."""
    before_filing_ids = {f["rcept_no"] for f in before_cache["filing_events"]}
    after_filing_ids = {f["rcept_no"] for f in after_cache["filing_events"]}
    if not before_filing_ids <= after_filing_ids:
        raise RuntimeError("Existing filing_events entries were lost during backfill.")

    before_by_id = {f["rcept_no"]: f for f in before_cache["filing_events"]}
    after_by_id = {f["rcept_no"]: f for f in after_cache["filing_events"]}
    for rcept_no, before_entry in before_by_id.items():
        if after_by_id[rcept_no] != before_entry:
            raise RuntimeError(f"Existing filing_events entry {rcept_no!r} was altered during backfill.")

    before_cs_ids = {c["id"] for c in before_cache["candidate_signals"]}
    after_cs_ids = {c["id"] for c in after_cache["candidate_signals"]}
    if not before_cs_ids <= after_cs_ids:
        raise RuntimeError("Existing embedded candidate_signals entries were lost during backfill.")

    before_seen = set(before_cache[seen_field])
    after_seen = set(after_cache[seen_field])
    if not before_seen <= after_seen:
        raise RuntimeError(f"Existing {seen_field} dedup entries were lost during backfill.")


def _verify_source_candidates(
    cache_dir: Path, candidates_filename: str, source_records: list[BackfillRecord], created_ids: set[str],
) -> None:
    store = candidate_store.load_candidates(cache_dir, candidates_filename)
    for record in source_records:
        candidate_id = record.candidate.id
        if candidate_id not in created_ids:
            continue  # already present before this call — nothing new to verify
        if candidate_id not in store:
            raise RuntimeError(f"Candidate {candidate_id!r} missing from store after write.")
        stored = store[candidate_id]
        if stored.status != CandidateStatus.CANDIDATE_DETECTED:
            raise RuntimeError(f"Candidate {candidate_id!r} has unexpected status after write: {stored.status!r}.")
        if stored.reviewed_at:
            raise RuntimeError(f"Candidate {candidate_id!r} unexpectedly has reviewed_at set after write.")
        if stored.reviewed_note:
            raise RuntimeError(f"Candidate {candidate_id!r} unexpectedly has a reviewed_note after write.")
        if stored.excerpt_original is not None:
            raise RuntimeError(f"Candidate {candidate_id!r} unexpectedly has excerpt_original after write.")


def backfill_candidates(cache_dir: Path, records: list[BackfillRecord]) -> BackfillResult:
    """Imports each BackfillRecord's already-fully-formed CandidateSignal
    into the real DART/EDGAR production caches, idempotently, with full
    rollback on any failure after writing begins.

    Empty `records` is a safe no-op (no files touched). Every record is
    validated (see _validate_record) before anything is touched; a
    validation failure raises plain ValueError and never invokes
    rollback, since nothing was written. A failure after writing begins
    (a store/cache write error, or a post-write consistency check)
    restores every affected file to its exact original bytes and raises
    CandidateBackfillError chained from the real failure — including
    rollback-failure context in the rare case rollback itself fails."""
    if not records:
        return BackfillResult(created_candidate_ids=(), already_present_candidate_ids=())

    seen_ids_in_input: set[str] = set()
    for record in records:
        if record.candidate.id in seen_ids_in_input:
            raise ValueError(f"Duplicate candidate id in input: {record.candidate.id!r}")
        seen_ids_in_input.add(record.candidate.id)
        _validate_record(record)

    dart_records = [r for r in records if r.source == _DART_SOURCE]
    edgar_records = [r for r in records if r.source == _EDGAR_SOURCE]

    affected_paths: list[Path] = []
    if dart_records:
        affected_paths += [cache_dir / _DART_FILING_EVENTS_FILENAME, cache_dir / _DART_CANDIDATES_FILENAME]
    if edgar_records:
        affected_paths += [cache_dir / _EDGAR_FILING_EVENTS_FILENAME, cache_dir / _EDGAR_CANDIDATES_FILENAME]

    original_bytes: dict[Path, bytes | None] = {
        path: (path.read_bytes() if path.exists() else None) for path in affected_paths
    }

    try:
        created: list[str] = []
        already_present: list[str] = []

        if dart_records:
            before_dart_cache = _load_filing_events_cache(cache_dir / _DART_FILING_EVENTS_FILENAME, "seen_receipt_numbers")
            dart_cache, dart_created, dart_already = _build_source_update(
                cache_dir, _DART_FILING_EVENTS_FILENAME, "seen_receipt_numbers", _dart_dedup_key, dart_records,
            )
            _assert_preserved(before_dart_cache, dart_cache, "seen_receipt_numbers")
            new_dart_candidates = [r.candidate for r in dart_records if r.candidate.id in dart_created]
            candidate_store.upsert_new_candidates(cache_dir, new_dart_candidates, _DART_CANDIDATES_FILENAME)
            _write_json_atomic(cache_dir / _DART_FILING_EVENTS_FILENAME, dart_cache)
            _verify_source_candidates(cache_dir, _DART_CANDIDATES_FILENAME, dart_records, set(dart_created))
            created += dart_created
            already_present += dart_already

        if edgar_records:
            before_edgar_cache = _load_filing_events_cache(cache_dir / _EDGAR_FILING_EVENTS_FILENAME, "seen_keys")
            edgar_cache, edgar_created, edgar_already = _build_source_update(
                cache_dir, _EDGAR_FILING_EVENTS_FILENAME, "seen_keys", _edgar_dedup_key, edgar_records,
            )
            _assert_preserved(before_edgar_cache, edgar_cache, "seen_keys")
            new_edgar_candidates = [r.candidate for r in edgar_records if r.candidate.id in edgar_created]
            candidate_store.upsert_new_candidates(cache_dir, new_edgar_candidates, _EDGAR_CANDIDATES_FILENAME)
            _write_json_atomic(cache_dir / _EDGAR_FILING_EVENTS_FILENAME, edgar_cache)
            _verify_source_candidates(cache_dir, _EDGAR_CANDIDATES_FILENAME, edgar_records, set(edgar_created))
            created += edgar_created
            already_present += edgar_already

    except Exception as exc:
        try:
            _restore_files(original_bytes)
        except Exception as rollback_exc:
            raise CandidateBackfillError(
                f"Backfill failed ({exc!r}) AND rollback itself failed ({rollback_exc!r}) — "
                "production caches may be in an inconsistent state and require manual inspection."
            ) from exc
        raise CandidateBackfillError(f"Backfill failed and was rolled back to its original state: {exc!r}") from exc

    return BackfillResult(created_candidate_ids=tuple(created), already_present_candidate_ids=tuple(already_present))
