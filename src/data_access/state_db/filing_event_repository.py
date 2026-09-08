"""SQLite-backed FilingEvent storage. Narrow, source-agnostic — every
function takes a plain sqlite3 connection and a FilingEvent; nothing here
imports anything DART/EDGAR/EDINET-specific, matching this repo's
existing candidate_store.py convention of being one shared module with
no source-specific logic in it."""
from __future__ import annotations

import sqlite3

from src.data_access.state_db.connection import transaction
from src.models.models import FilingEvent


def _row_to_filing_event(row: sqlite3.Row) -> FilingEvent:
    return FilingEvent(
        rcept_no=row["rcept_no"],
        corp_code=row["corp_code"],
        corp_name=row["corp_name"],
        stock_code=row["stock_code"],
        report_nm=row["report_nm"],
        rcept_dt=row["rcept_dt"],
        flr_nm=row["flr_nm"],
        pblntf_ty=row["pblntf_ty"],
        pblntf_detail_ty=row["pblntf_detail_ty"],
        theme_slug=row["theme_slug"],
        subtheme_slug=row["subtheme_slug"],
        source_url=row["source_url"],
        retrieved_at=row["retrieved_at"],
        source_name=row["source_name"],
        original_language=row["original_language"],
        is_demo=bool(row["is_demo"]),
        primary_document=row["primary_document"],
        ordinance_code=row["ordinance_code"],
        filed_at=row["filed_at"],
    )


def get_filing_event(conn: sqlite3.Connection, source_name: str, corp_code: str, rcept_no: str) -> FilingEvent | None:
    row = conn.execute(
        "SELECT * FROM filing_events WHERE source_name = ? AND corp_code = ? AND rcept_no = ?",
        (source_name, corp_code, rcept_no),
    ).fetchone()
    return _row_to_filing_event(row) if row is not None else None


def load_filing_events(conn: sqlite3.Connection, source_name: str) -> tuple[FilingEvent, ...]:
    rows = conn.execute("SELECT * FROM filing_events WHERE source_name = ?", (source_name,)).fetchall()
    return tuple(_row_to_filing_event(row) for row in rows)


def filing_event_exists(conn: sqlite3.Connection, source_name: str, corp_code: str, rcept_no: str) -> bool:
    """The SQLite-backed equivalent of checking `dedup_key(...) in seen`
    against the JSON store's seen_keys set — same three-part identity."""
    row = conn.execute(
        "SELECT 1 FROM filing_events WHERE source_name = ? AND corp_code = ? AND rcept_no = ?",
        (source_name, corp_code, rcept_no),
    ).fetchone()
    return row is not None


def _upsert_filing_event_no_transaction(conn: sqlite3.Connection, filing: FilingEvent) -> bool:
    """The raw insert-if-absent logic behind upsert_filing_event(), with
    no transaction management of its own. For use only by a caller that
    already holds an open outer transaction on `conn` — today, that's
    exclusively candidate_repository.upsert_new_candidates()'s own batch
    transaction, which must ensure each new candidate's parent
    filing_events row exists without opening a second, nested
    transaction on the same connection (connection.transaction()'s own
    docstring: "Nesting is not supported"). Calling this directly outside
    an already-open transaction leaves any INSERT it makes uncommitted
    until the caller itself commits — see upsert_filing_event() below for
    the atomic, standalone-safe public entry point."""
    existing = filing_event_exists(conn, filing.source_name, filing.corp_code, filing.rcept_no)
    if existing:
        return False
    conn.execute(
        """
        INSERT INTO filing_events (
            source_name, corp_code, rcept_no, corp_name, stock_code, report_nm, rcept_dt, flr_nm,
            pblntf_ty, pblntf_detail_ty, theme_slug, subtheme_slug, source_url, retrieved_at,
            original_language, is_demo, primary_document, ordinance_code, filed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            filing.source_name, filing.corp_code, filing.rcept_no, filing.corp_name, filing.stock_code,
            filing.report_nm, filing.rcept_dt, filing.flr_nm, filing.pblntf_ty, filing.pblntf_detail_ty,
            filing.theme_slug, filing.subtheme_slug, filing.source_url, filing.retrieved_at,
            filing.original_language, int(filing.is_demo), filing.primary_document,
            filing.ordinance_code, filing.filed_at,
        ),
    )
    return True


def upsert_filing_event(conn: sqlite3.Connection, filing: FilingEvent) -> bool:
    """Inserts `filing` if its (source, corp_code, rcept_no) isn't
    already present; leaves an existing row untouched otherwise (a
    FilingEvent is never mutated after creation in the existing JSON
    stores either). Returns True if a new row was inserted, False if it
    already existed. One transaction per call — the public, standalone-
    safe entry point. Never call this from inside another already-open
    transaction on the same connection; use
    _upsert_filing_event_no_transaction directly for that case (see its
    own docstring)."""
    with transaction(conn):
        return _upsert_filing_event_no_transaction(conn, filing)
