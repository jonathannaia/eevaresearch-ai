"""SQLite-backed resolved-identifier storage — one shared table for
EDGAR's ResolvedCik cache (on disk as edgar_ciks.json) and DART's
ResolvedCorpCode cache (on disk as dart_corp_codes.json), which have an
identical shape once cik/corp_code -> identifier and company_name/
corp_name -> display_name (verified by reading both dataclasses
directly — see schema.py's module docstring for the full rationale, and
for why EDINET's own code-resolver cache is out of scope this phase).

Deliberately defined with its own ResolvedIdentifierRecord dataclass
rather than importing ResolvedCik/ResolvedCorpCode from the edgar/dart
packages — keeps this module self-contained and narrow; converting to/
from those source-specific dataclasses is a future wiring-phase concern,
not this phase's."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from src.data_access.state_db.connection import transaction


@dataclass(frozen=True)
class ResolvedIdentifierRecord:
    identifier: str  # CIK (EDGAR) or corp_code (DART) — zero-padded/verbatim as the source resolver produced it
    display_name: str  # company_name (EDGAR) or corp_name (DART)
    resolution_method: str  # e.g. "SEC company_tickers.json + submissions cross-check", "OpenDART corpCode.xml"
    retrieved_at: str  # ISO 8601


def _row_to_record(row: sqlite3.Row) -> ResolvedIdentifierRecord:
    return ResolvedIdentifierRecord(
        identifier=row["identifier"], display_name=row["display_name"],
        resolution_method=row["resolution_method"], retrieved_at=row["retrieved_at"],
    )


def load_resolved_identifiers(conn: sqlite3.Connection, source: str) -> dict[str, ResolvedIdentifierRecord]:
    """Keyed by `lookup_key` — a ticker for `source="SEC EDGAR"`, a KRX
    stock code for `source="OpenDART / DART"` — matching exactly what
    cik_resolver.load_cached_ciks()/corp_code_resolver.load_cached_corp_codes()
    key their own dicts by."""
    rows = conn.execute(
        "SELECT lookup_key, identifier, display_name, resolution_method, retrieved_at "
        "FROM resolved_identifiers WHERE source = ?",
        (source,),
    ).fetchall()
    return {row["lookup_key"]: _row_to_record(row) for row in rows}


def get_resolved_identifier(conn: sqlite3.Connection, source: str, lookup_key: str) -> ResolvedIdentifierRecord | None:
    row = conn.execute(
        "SELECT lookup_key, identifier, display_name, resolution_method, retrieved_at "
        "FROM resolved_identifiers WHERE source = ? AND lookup_key = ?",
        (source, lookup_key),
    ).fetchone()
    return _row_to_record(row) if row is not None else None


def upsert_resolved_identifier(
    conn: sqlite3.Connection, source: str, lookup_key: str, record: ResolvedIdentifierRecord,
) -> None:
    """Insert-or-replace by (source, lookup_key) — matches the existing
    resolvers' own "re-resolving the same ticker/code just refreshes it"
    behavior (resolve_and_cache always overwrites with the latest live
    result for any ticker it successfully re-resolves)."""
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO resolved_identifiers (source, lookup_key, identifier, display_name, resolution_method, retrieved_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (source, lookup_key) DO UPDATE SET
                identifier = excluded.identifier,
                display_name = excluded.display_name,
                resolution_method = excluded.resolution_method,
                retrieved_at = excluded.retrieved_at
            """,
            (source, lookup_key, record.identifier, record.display_name, record.resolution_method, record.retrieved_at),
        )
