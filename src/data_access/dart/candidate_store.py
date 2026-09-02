"""Persisted store of every CandidateSignal ever detected (Korea DART
radar pilot). This is the seam a later "process this one candidate" UI
action would call into, and — more immediately — how a candidate
deferred by an earlier scan's processing budget gets found again by a
later orchestrator run: scan_service.scan() only reports genuinely *new*
receipt numbers, so a deferred candidate would never resurface on its
own without a separate persisted record of "still needs processing."

Separate from scan_service.py's own cache (which tracks the raw
FilingEvent/CandidateSignal history for scan-level idempotency) — this
store is specifically about *current processing state* per candidate,
read-modify-write, single authoritative copy per candidate ID.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

from src.models.models import (
    CandidateSignal,
    CandidateStatus,
    EvidenceLocation,
    ExcerptQuality,
    ExtractionState,
    FilingEvent,
    FlagReason,
    LocationKind,
    StateTransition,
    Translation,
    TranslationState,
)

_CACHE_FILENAME = "dart_candidates.json"


class CandidatePersistence(Protocol):
    """Durable-State Phase 3A — narrow, source-neutral candidate
    persistence collaborator: the exact operations edgar_pipeline.py/
    radar_pipeline.py/edinet_pipeline.py already perform against this
    module's own JSON functions. Defined here rather than imported from
    backend_factory.py because backend_factory.py already imports every
    one of those pipeline modules — a reverse import would be circular.
    Structural typing means any object with this shape works as-is,
    including backend_factory's own JsonCandidateRepository/
    SqliteCandidateRepository, with no import relationship required
    between the two modules.

    When a pipeline function's optional `candidate_repository` parameter
    is omitted, none of this is used — every existing call goes to this
    module's own load_candidates/upsert_new_candidates/update_candidate
    exactly as before. When supplied, every candidate-store touch within
    that one call (including the read used to pick eligible/already-
    existing candidates) routes through the same collaborator, so a
    single call's own writes are visible to its own later reads —
    filing-event, excerpt, translation, identifier, discovery, and Signal
    persistence are never touched by this collaborator.

    **Single-backend atomicity, by contract, not by accident**: one
    invocation uses exactly one candidate store for every read and write
    it performs — either this module's own JSON functions throughout, or
    the supplied collaborator throughout, never a mix within the same
    call. There is no cross-backend lookup, dedup, migration, dual-read,
    or dual-write of any kind — a JSON-mode call and a separate
    SQLite-mode call for the same candidate ID are two independent
    stores that do not see each other. That's an intentional scope
    boundary, not a gap: no real service entry point calls this phase's
    functions with an injected collaborator at all (see each pipeline
    module's own docstring), so the cross-invocation case never actually
    arises in production this phase."""

    def load_candidates(self) -> dict[str, CandidateSignal]: ...
    def upsert_new_candidates(self, new_candidates: list[CandidateSignal]) -> dict[str, CandidateSignal]: ...
    def update_candidate(self, candidate: CandidateSignal, expected_version: int | None = None) -> object: ...


def _cache_path(cache_dir: Path, filename: str = _CACHE_FILENAME) -> Path:
    return cache_dir / filename


def _flag_reason_from_dict(data: dict | None) -> FlagReason | None:
    if not data:
        return None
    return FlagReason(
        category=data.get("category", ""), matched_terms=tuple(data.get("matched_terms", ())),
        score_inputs=tuple(data.get("score_inputs", ())), human_readable_reason=data.get("human_readable_reason", ""),
        source_detail=data.get("source_detail", ""),
    )


def _evidence_location_from_dict(data: dict | None) -> EvidenceLocation | None:
    if not data:
        return None
    return EvidenceLocation(
        kind=LocationKind(data.get("kind", LocationKind.UNAVAILABLE.value)),
        page=data.get("page"), section=data.get("section"), table=data.get("table"),
        paragraph_index=data.get("paragraph_index"),
    )


def _candidate_from_dict(data: dict) -> CandidateSignal:
    filing = FilingEvent(**data["filing"])
    title_translation = Translation(**data["title_translation"]) if data.get("title_translation") else None
    excerpt_translation = Translation(**data["excerpt_translation"]) if data.get("excerpt_translation") else None
    history = [
        StateTransition(status=CandidateStatus(h["status"]), at=h["at"], detail=h.get("detail", ""))
        for h in data.get("state_history", [])
    ]
    return CandidateSignal(
        id=data["id"],
        filing=filing,
        matched_rules=list(data.get("matched_rules", [])),
        confidence=data["confidence"],
        status=CandidateStatus(data["status"]),
        extraction_state=ExtractionState(data.get("extraction_state", ExtractionState.NOT_FETCHED.value)),
        translation_state=TranslationState(data.get("translation_state", TranslationState.NOT_REQUESTED.value)),
        excerpt_quality=ExcerptQuality(data.get("excerpt_quality", ExcerptQuality.UNKNOWN.value)),
        excerpt_original=data.get("excerpt_original"),
        title_translation=title_translation,
        excerpt_translation=excerpt_translation,
        reviewed_at=data.get("reviewed_at"),
        reviewed_note=data.get("reviewed_note", ""),
        state_history=history,
        materiality_assessment=data.get("materiality_assessment", "Not assessed"),
        excerpt_supplemental=data.get("excerpt_supplemental"),
        excerpt_retrieved_at=data.get("excerpt_retrieved_at"),
        flag_reason=_flag_reason_from_dict(data.get("flag_reason")),
        evidence_location=_evidence_location_from_dict(data.get("evidence_location")),
        evidence_source_member=data.get("evidence_source_member"),
        translation_failure_category=data.get("translation_failure_category"),
        translation_failure_reason=data.get("translation_failure_reason"),
        translation_failure_at=data.get("translation_failure_at"),
        translation_retry_count=data.get("translation_retry_count", 0),
        translation_next_retry_at=data.get("translation_next_retry_at"),
    )


def load_candidates(cache_dir: Path, filename: str = _CACHE_FILENAME) -> dict[str, CandidateSignal]:
    """Never raises — a missing or corrupt store is treated as empty, and
    an individual corrupt entry is skipped rather than losing the whole
    store. `filename` defaults to DART's own cache file — the SEC EDGAR
    pilot (milestone 8) passes "edgar_candidates.json" to keep the two
    sources' candidate stores completely separate on disk, reusing this
    same module (it has no DART-specific logic in it) rather than
    duplicating it."""
    path = _cache_path(cache_dir, filename)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, CandidateSignal] = {}
    for candidate_id, data in raw.items():
        try:
            result[candidate_id] = _candidate_from_dict(data)
        except (KeyError, TypeError, ValueError):
            continue
    return result


def save_candidates(cache_dir: Path, candidates: dict[str, CandidateSignal], filename: str = _CACHE_FILENAME) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {candidate_id: asdict(candidate) for candidate_id, candidate in candidates.items()}
    _cache_path(cache_dir, filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def upsert_new_candidates(
    cache_dir: Path, new_candidates: list[CandidateSignal], filename: str = _CACHE_FILENAME,
) -> dict[str, CandidateSignal]:
    """For candidates just detected by a scan — adds any ID not already
    present, leaves existing entries untouched (this function never
    updates an existing candidate's processing state; see
    update_candidate for that). Returns the full merged store."""
    store = load_candidates(cache_dir, filename)
    changed = False
    for candidate in new_candidates:
        if candidate.id not in store:
            store[candidate.id] = candidate
            changed = True
    if changed:
        save_candidates(cache_dir, store, filename)
    return store


def update_candidate(cache_dir: Path, candidate: CandidateSignal, filename: str = _CACHE_FILENAME) -> None:
    """Persists one candidate's updated processing state. Read-modify-
    write against the full store — Streamlit's single-process model
    makes this safe without file locking at this pilot's scale."""
    store = load_candidates(cache_dir, filename)
    store[candidate.id] = candidate
    save_candidates(cache_dir, store, filename)
