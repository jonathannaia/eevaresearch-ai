"""Provider scan-status/cursor persistence (Durable-State Phase 4M-0) —
one row per provider (per-provider granularity, not per-issuer, by
explicit decision), read and written only by scripts/radar_worker.py.
No existing pipeline, page, or component reads or writes this table —
Radar Inbox reads it read-only once wired in a later, separately
reviewed step of this same phase.

`ProviderScanStatus` combines the cursor (`cursor_value` — the most
recent filing-window boundary a scan actually persisted through) and
scan-run bookkeeping (`started_at`/`completed_at`/`last_successful_at`/
counts/`failure_code`) into a single record, since a worker tick always
reads and writes both together for one provider. `failure_code` is a
short, sanitized internal reason string — never a raw exception message
or traceback, matching this codebase's existing
`BackendConfigurationError` discipline."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from src.data_access.state_db.connection import transaction


@dataclass(frozen=True)
class ProviderScanStatus:
    provider: str
    cursor_value: str | None
    started_at: str | None
    completed_at: str | None
    last_successful_at: str | None
    items_discovered: int
    candidates_created: int
    skipped_unresolved_count: int
    failure_code: str | None
    updated_at: str


def _row_to_status(row: sqlite3.Row) -> ProviderScanStatus:
    return ProviderScanStatus(
        provider=row["provider"],
        cursor_value=row["cursor_value"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        last_successful_at=row["last_successful_at"],
        items_discovered=row["items_discovered"],
        candidates_created=row["candidates_created"],
        skipped_unresolved_count=row["skipped_unresolved_count"],
        failure_code=row["failure_code"],
        updated_at=row["updated_at"],
    )


def get_scan_status(conn: sqlite3.Connection, provider: str) -> ProviderScanStatus | None:
    row = conn.execute(
        "SELECT * FROM provider_scan_status WHERE provider = ?", (provider,),
    ).fetchone()
    return _row_to_status(row) if row is not None else None


def get_all_scan_statuses(conn: sqlite3.Connection) -> dict[str, ProviderScanStatus]:
    rows = conn.execute("SELECT * FROM provider_scan_status").fetchall()
    return {row["provider"]: _row_to_status(row) for row in rows}


def upsert_scan_status(conn: sqlite3.Connection, status: ProviderScanStatus) -> None:
    """Insert-or-replace by `provider` — the worker always writes the
    complete, current record for that provider in one call; there is no
    partial-field update."""
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO provider_scan_status (
                provider, cursor_value, started_at, completed_at, last_successful_at,
                items_discovered, candidates_created, skipped_unresolved_count, failure_code, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (provider) DO UPDATE SET
                cursor_value = excluded.cursor_value,
                started_at = excluded.started_at,
                completed_at = excluded.completed_at,
                last_successful_at = excluded.last_successful_at,
                items_discovered = excluded.items_discovered,
                candidates_created = excluded.candidates_created,
                skipped_unresolved_count = excluded.skipped_unresolved_count,
                failure_code = excluded.failure_code,
                updated_at = excluded.updated_at
            """,
            (
                status.provider, status.cursor_value, status.started_at, status.completed_at,
                status.last_successful_at, status.items_discovered, status.candidates_created,
                status.skipped_unresolved_count, status.failure_code, status.updated_at,
            ),
        )
