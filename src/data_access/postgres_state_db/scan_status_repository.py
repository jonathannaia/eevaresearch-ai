"""Provider scan-status/cursor persistence (Durable-State Phase 4M-0) —
the isolated Postgres counterpart to
state_db/scan_status_repository.py, identical shape, independently
implemented (no shared code, per this package's existing
no-dialect-abstraction constraint). See that module's own docstring for
the full design rationale."""
from __future__ import annotations

from dataclasses import dataclass

import psycopg

from src.data_access.postgres_state_db.connection import transaction


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


def _row_to_status(row: dict) -> ProviderScanStatus:
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


def get_scan_status(conn: psycopg.Connection, provider: str) -> ProviderScanStatus | None:
    row = conn.execute(
        "SELECT * FROM provider_scan_status WHERE provider = %s", (provider,),
    ).fetchone()
    return _row_to_status(row) if row is not None else None


def get_all_scan_statuses(conn: psycopg.Connection) -> dict[str, ProviderScanStatus]:
    rows = conn.execute("SELECT * FROM provider_scan_status").fetchall()
    return {row["provider"]: _row_to_status(row) for row in rows}


def upsert_scan_status(conn: psycopg.Connection, status: ProviderScanStatus) -> None:
    """Insert-or-replace by `provider` — the worker always writes the
    complete, current record for that provider in one call; there is no
    partial-field update."""
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO provider_scan_status (
                provider, cursor_value, started_at, completed_at, last_successful_at,
                items_discovered, candidates_created, skipped_unresolved_count, failure_code, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
