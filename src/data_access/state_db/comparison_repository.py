"""SQLite-backed ComparisonRecord persistence — the transactional,
insert-only counterpart to src.data_access.comparison_store's JSON
backend, mirroring state_db/candidate_repository.py's own shape and
conventions. Radar evidence-packet foundation, Phase 3, Step 2
(design/DECISIONS.md).

Deliberate divergence from the JSON store's tolerant-load behavior,
matching state_db/candidate_repository.py's own module docstring
convention: functions here never catch sqlite3 errors beyond the one
explicitly-handled duplicate-id case in insert_comparison_record — a
genuine database failure propagates as a real exception rather than
becoming a silently-empty result.

No update/replace/upsert/delete function exists anywhere in this module
for a comparison record — insert and read only."""
from __future__ import annotations

import json
import sqlite3
from typing import Sequence

from src.data_access.comparison_store import ComparisonRecord
from src.data_access.state_db.connection import transaction


def _row_to_record(row: sqlite3.Row) -> ComparisonRecord:
    return ComparisonRecord(
        id=row["id"],
        current_candidate_id=row["current_candidate_id"],
        current_source_name=row["current_source_name"],
        current_corp_code=row["current_corp_code"],
        current_document_id=row["current_document_id"],
        prior_candidate_id=row["prior_candidate_id"],
        prior_document_id=row["prior_document_id"],
        prior_filed_at=row["prior_filed_at"],
        comparison_status=row["comparison_status"],
        comparison_basis=row["comparison_basis"],
        added_categories=tuple(json.loads(row["added_categories_json"])),
        removed_categories=tuple(json.loads(row["removed_categories_json"])),
        prior_excerpt=row["prior_excerpt"],
        current_excerpt=row["current_excerpt"],
        limitations=tuple(json.loads(row["limitations_json"])),
        computed_at=row["computed_at"],
    )


def get_comparison_record(conn: sqlite3.Connection, record_id: str) -> ComparisonRecord | None:
    row = conn.execute("SELECT * FROM comparison_results WHERE id = ?", (record_id,)).fetchone()
    return _row_to_record(row) if row is not None else None


def load_comparison_records_for_candidate(
    conn: sqlite3.Connection, current_candidate_id: str,
) -> tuple[ComparisonRecord, ...]:
    """Every persisted record for one current-candidate id, oldest
    first — the full immutable history, not just the latest."""
    rows = conn.execute(
        "SELECT * FROM comparison_results WHERE current_candidate_id = ? ORDER BY computed_at ASC",
        (current_candidate_id,),
    ).fetchall()
    return tuple(_row_to_record(row) for row in rows)


def get_latest_comparison_record(conn: sqlite3.Connection, current_candidate_id: str) -> ComparisonRecord | None:
    """Read-only query: the most recently computed_at record for one
    current-candidate id, or None when none exists — `id` descending is
    an explicit secondary sort key for an exact `computed_at` tie (Phase
    3, Step 3A: aligned with get_latest_comparison_records_for_candidate_ids'
    own tie rule below), never SQLite's own unspecified tie order. Never
    triggers a comparison computation itself — a pure repository read
    only."""
    row = conn.execute(
        "SELECT * FROM comparison_results WHERE current_candidate_id = ? ORDER BY computed_at DESC, id DESC LIMIT 1",
        (current_candidate_id,),
    ).fetchone()
    return _row_to_record(row) if row is not None else None


def get_latest_comparison_records_for_candidate_ids(
    conn: sqlite3.Connection, candidate_ids: Sequence[str],
) -> dict[str, ComparisonRecord]:
    """Bulk counterpart to get_latest_comparison_record() — Phase 3, Step
    3A. Exactly one parameterized SQL query for a non-empty request
    (never one query per id); empty input returns `{}` immediately
    without executing any SQL at all. Deterministic latest-per-candidate
    selection via a window function partitioned by `current_candidate_id`
    and ordered `computed_at DESC, id DESC` — never SQLite's physical row
    order. Placeholders are built only from the number of supplied ids
    (`?` repeated); no id value is ever interpolated into the SQL text
    itself, only bound as a parameter. Unrequested/unknown candidate ids
    are simply absent from the result. A pure read — never computes,
    inserts, or updates anything."""
    if not candidate_ids:
        return {}
    placeholders = ", ".join("?" for _ in candidate_ids)
    rows = conn.execute(
        f"""
        SELECT * FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY current_candidate_id
                ORDER BY computed_at DESC, id DESC
            ) AS rn
            FROM comparison_results
            WHERE current_candidate_id IN ({placeholders})
        ) sub
        WHERE rn = 1
        """,
        tuple(candidate_ids),
    ).fetchall()
    return {row["current_candidate_id"]: _row_to_record(row) for row in rows}


def insert_comparison_record(conn: sqlite3.Connection, record: ComparisonRecord) -> bool:
    """INSERT-only: no update/replace/upsert path exists for this table
    anywhere in this codebase. Returns True when the record was newly
    inserted; False when a row with this exact stable id already
    existed — in which case nothing is written or changed (the INSERT is
    rejected by the table's own PRIMARY KEY constraint and caught here
    rather than propagated, matching this codebase's established
    "duplicate insert is a safe no-op" convention)."""
    with transaction(conn):
        try:
            conn.execute(
                """
                INSERT INTO comparison_results (
                    id, current_candidate_id, current_source_name, current_corp_code, current_document_id,
                    prior_candidate_id, prior_document_id, prior_filed_at, comparison_status, comparison_basis,
                    added_categories_json, removed_categories_json, prior_excerpt, current_excerpt,
                    limitations_json, computed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id, record.current_candidate_id, record.current_source_name, record.current_corp_code,
                    record.current_document_id, record.prior_candidate_id, record.prior_document_id,
                    record.prior_filed_at, record.comparison_status, record.comparison_basis,
                    json.dumps(list(record.added_categories)), json.dumps(list(record.removed_categories)),
                    record.prior_excerpt, record.current_excerpt, json.dumps(list(record.limitations)),
                    record.computed_at,
                ),
            )
        except sqlite3.IntegrityError:
            return False
    return True
