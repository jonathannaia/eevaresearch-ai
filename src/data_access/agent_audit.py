"""Append-only audit event stream for the Autonomous Research Agent
(design §8.2). Distinct from CandidateSignal.state_history (the
candidate's own lifecycle narrative, rendered in-product): this is the
full, backend-only reproducibility log of every tool call and decision a
session made, including calls that never affected the final status.

Storage is one JSON-Lines file per cache_dir, appended with flush+fsync
and never rewritten — the same first-write-only/append-only discipline
research_store.py follows, chosen over a SQL table because this repo's
own precedent for research-packet persistence (research_store.py) is a
JSON store, and an event stream only ever needs append + sequential read.

Every event carries input_hash — a deterministic hash of the exact tool
inputs — so a session can be replayed and compared byte-for-byte (§13).
output_summary is capped so the stream stays small; full content lives
in the packet store, never here.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AUDIT_FILENAME = "agent_audit_events.jsonl"
OUTPUT_SUMMARY_MAX_CHARS = 500


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    session_id: str
    candidate_id: str
    event_type: str
    at: str
    input_hash: str
    output_summary: str
    case_id: str | None = None
    tool_name: str | None = None


def hash_inputs(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_audit_event(
    *,
    session_id: str,
    candidate_id: str,
    event_type: str,
    inputs: Any,
    output_summary: str,
    case_id: str | None = None,
    tool_name: str | None = None,
    at: str | None = None,
) -> AuditEvent:
    at = at or datetime.now(timezone.utc).isoformat()
    input_hash = hash_inputs(inputs)
    event_id = "ae-" + hashlib.sha256(f"{session_id}|{event_type}|{at}|{input_hash}".encode("utf-8")).hexdigest()[:16]
    return AuditEvent(
        event_id=event_id, session_id=session_id, candidate_id=candidate_id, event_type=event_type, at=at,
        input_hash=input_hash, output_summary=output_summary[:OUTPUT_SUMMARY_MAX_CHARS],
        case_id=case_id, tool_name=tool_name,
    )


def _path(cache_dir: Path) -> Path:
    return cache_dir / AUDIT_FILENAME


def append_audit_event(cache_dir: Path, event: AuditEvent) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    line = json.dumps(asdict(event), sort_keys=True, ensure_ascii=False)
    with open(_path(cache_dir), "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def load_all_audit_events(cache_dir: Path) -> tuple[AuditEvent, ...]:
    """File order == append order. A corrupt line is skipped, never fatal."""
    path = _path(cache_dir)
    if not path.exists():
        return ()
    events: list[AuditEvent] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            events.append(AuditEvent(**json.loads(raw)))
        except (ValueError, TypeError):
            continue
    return tuple(events)


def load_audit_events_for_session(cache_dir: Path, session_id: str) -> tuple[AuditEvent, ...]:
    return tuple(e for e in load_all_audit_events(cache_dir) if e.session_id == session_id)
