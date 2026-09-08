"""Postgres-backed resolved-identifier storage — the isolated Postgres
counterpart to src/data_access/state_db/identifier_repository.py,
mirrored symbol-for-symbol. Its `ON CONFLICT` upsert clause is
syntactically identical to the SQLite version's (Postgres's own upsert
syntax is what SQLite's was modeled on) — only the `%s` placeholders
differ."""
from __future__ import annotations

from dataclasses import dataclass

import psycopg

from src.data_access.postgres_state_db.connection import transaction


@dataclass(frozen=True)
class ResolvedIdentifierRecord:
    identifier: str  # CIK (EDGAR) or corp_code (DART) — zero-padded/verbatim as the source resolver produced it
    display_name: str  # company_name (EDGAR) or corp_name (DART)
    resolution_method: str  # e.g. "SEC company_tickers.json + submissions cross-check", "OpenDART corpCode.xml"
    retrieved_at: str  # ISO 8601


def _row_to_record(row) -> ResolvedIdentifierRecord:
    return ResolvedIdentifierRecord(
        identifier=row["identifier"], display_name=row["display_name"],
        resolution_method=row["resolution_method"], retrieved_at=row["retrieved_at"],
    )


def load_resolved_identifiers(conn: psycopg.Connection, source: str) -> dict[str, ResolvedIdentifierRecord]:
    """Keyed by `lookup_key` — a ticker for `source="SEC EDGAR"`, a KRX
    stock code for `source="OpenDART / DART"` — matching exactly what
    the SQLite-backed identifier repository (and cik_resolver.py/
    corp_code_resolver.py) key their own dicts by."""
    rows = conn.execute(
        "SELECT lookup_key, identifier, display_name, resolution_method, retrieved_at "
        "FROM resolved_identifiers WHERE source = %s",
        (source,),
    ).fetchall()
    return {row["lookup_key"]: _row_to_record(row) for row in rows}


def get_resolved_identifier(conn: psycopg.Connection, source: str, lookup_key: str) -> ResolvedIdentifierRecord | None:
    row = conn.execute(
        "SELECT lookup_key, identifier, display_name, resolution_method, retrieved_at "
        "FROM resolved_identifiers WHERE source = %s AND lookup_key = %s",
        (source, lookup_key),
    ).fetchone()
    return _row_to_record(row) if row is not None else None


def upsert_resolved_identifier(
    conn: psycopg.Connection, source: str, lookup_key: str, record: ResolvedIdentifierRecord,
) -> None:
    """Insert-or-replace by (source, lookup_key) — matches the existing
    resolvers' own "re-resolving the same ticker/code just refreshes it"
    behavior."""
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO resolved_identifiers (source, lookup_key, identifier, display_name, resolution_method, retrieved_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (source, lookup_key) DO UPDATE SET
                identifier = EXCLUDED.identifier,
                display_name = EXCLUDED.display_name,
                resolution_method = EXCLUDED.resolution_method,
                retrieved_at = EXCLUDED.retrieved_at
            """,
            (source, lookup_key, record.identifier, record.display_name, record.resolution_method, record.retrieved_at),
        )
