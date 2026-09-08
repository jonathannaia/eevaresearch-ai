"""EevaResearch — Evidence-First Themes MVP (design/DECISIONS.md).
Postgres-backed persistence for the public ResearchTheme model family —
the isolated Postgres counterpart to state_db/theme_repository.py.

Deliberate Postgres-specific divergence from the SQLite module: a
failed INSERT/UPDATE leaves a Postgres transaction in an aborted state
until an explicit ROLLBACK — unlike SQLite — so every write function
below manages its own commit/rollback directly around the one
statement, exactly mirroring postgres_state_db/research_repository.py's
own established, verified behavior.

Insert-only for evidence and company-map entries. `set_theme_visibility()`
is the one deliberate update path (see theme_store.py's own module
docstring) — never exposed to the public read protocol."""
from __future__ import annotations

from typing import Sequence

import psycopg

from src.models.theme_research import (
    CompanyRole,
    EvidenceDirection,
    HypothesisConfidence,
    ResearchTheme,
    ThemeCategory,
    ThemeCompanyMapEntry,
    ThemeEvidenceItem,
    ThemeNoteType,
    ThemeResearchNote,
    ThemeStatus,
    ThemeVisibility,
)


def _row_to_theme(row) -> ResearchTheme:
    return ResearchTheme(
        id=row["id"],
        category=ThemeCategory(row["category"]),
        status=ThemeStatus(row["status"]),
        visibility=ThemeVisibility(row["visibility"]),
        title=row["title"],
        key_question=row["key_question"],
        hypothesis=row["hypothesis"],
        working_thesis=row["working_thesis"],
        why_it_matters=row["why_it_matters"],
        what_could_change_the_view=row["what_could_change_the_view"],
        what_to_watch_next=row["what_to_watch_next"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def get_theme(conn: psycopg.Connection, theme_id: str) -> ResearchTheme | None:
    row = conn.execute("SELECT * FROM research_themes WHERE id = %s", (theme_id,)).fetchone()
    return _row_to_theme(row) if row is not None else None


def get_published_theme(conn: psycopg.Connection, theme_id: str) -> ResearchTheme | None:
    row = conn.execute(
        "SELECT * FROM research_themes WHERE id = %s AND visibility = %s", (theme_id, ThemeVisibility.PUBLISHED.value),
    ).fetchone()
    return _row_to_theme(row) if row is not None else None


def list_themes(conn: psycopg.Connection) -> tuple[ResearchTheme, ...]:
    """Every theme regardless of visibility — curator/private use only."""
    rows = conn.execute("SELECT * FROM research_themes ORDER BY updated_at DESC, id DESC").fetchall()
    return tuple(_row_to_theme(row) for row in rows)


def list_published_themes(conn: psycopg.Connection) -> tuple[ResearchTheme, ...]:
    rows = conn.execute(
        "SELECT * FROM research_themes WHERE visibility = %s ORDER BY updated_at DESC, id DESC",
        (ThemeVisibility.PUBLISHED.value,),
    ).fetchall()
    return tuple(_row_to_theme(row) for row in rows)


def insert_theme(conn: psycopg.Connection, theme: ResearchTheme) -> bool:
    """INSERT-only. Returns True when newly inserted; False when a row
    with this exact id already existed — the failed INSERT is rolled
    back explicitly so the connection is left usable for the caller's
    next statement."""
    try:
        conn.execute(
            """
            INSERT INTO research_themes (
                id, category, status, visibility, title, key_question, hypothesis,
                working_thesis, why_it_matters, what_could_change_the_view, what_to_watch_next,
                created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                theme.id, theme.category.value, theme.status.value, theme.visibility.value, theme.title,
                theme.key_question, theme.hypothesis, theme.working_thesis, theme.why_it_matters,
                theme.what_could_change_the_view, theme.what_to_watch_next, theme.created_at, theme.updated_at,
            ),
        )
    except psycopg.errors.UniqueViolation:
        conn.rollback()
        return False
    conn.commit()
    return True


def set_theme_visibility(
    conn: psycopg.Connection, theme_id: str, new_visibility: ThemeVisibility, updated_at: str,
) -> ResearchTheme | None:
    """The one deliberate update path — see theme_store.py's own
    module docstring. Curator/private use only. Returns None (no-op,
    rolled back) if `theme_id` doesn't exist."""
    cursor = conn.execute(
        "UPDATE research_themes SET visibility = %s, updated_at = %s WHERE id = %s",
        (new_visibility.value, updated_at, theme_id),
    )
    if cursor.rowcount == 0:
        conn.rollback()
        return None
    conn.commit()
    return get_theme(conn, theme_id)


def _row_to_evidence_item(row) -> ThemeEvidenceItem:
    return ThemeEvidenceItem(
        id=row["id"],
        theme_id=row["theme_id"],
        date=row["date"],
        company=row["company"],
        source_name=row["source_name"],
        source_url=row["source_url"],
        fact=row["fact"],
        relevance=row["relevance"],
        direction=EvidenceDirection(row["direction"]),
    )


def insert_theme_evidence_item(conn: psycopg.Connection, item: ThemeEvidenceItem) -> bool:
    try:
        conn.execute(
            """
            INSERT INTO theme_evidence_items (
                id, theme_id, date, company, source_name, source_url, fact, relevance, direction
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                item.id, item.theme_id, item.date, item.company, item.source_name, item.source_url,
                item.fact, item.relevance, item.direction.value,
            ),
        )
    except psycopg.errors.UniqueViolation:
        conn.rollback()
        return False
    conn.commit()
    return True


def evidence_for_theme_ids(
    conn: psycopg.Connection, theme_ids: Sequence[str],
) -> dict[str, tuple[ThemeEvidenceItem, ...]]:
    """Bulk, read-only: exactly one parameterized query for a
    non-empty request; empty input returns `{}` immediately without
    executing any query. Uses `= ANY(%s)` with a single list parameter —
    this package's existing convention for a variable-length id match."""
    if not theme_ids:
        return {}
    rows = conn.execute(
        "SELECT * FROM theme_evidence_items WHERE theme_id = ANY(%s) ORDER BY theme_id, date, id",
        (list(theme_ids),),
    ).fetchall()
    by_theme: dict[str, list[ThemeEvidenceItem]] = {}
    for row in rows:
        by_theme.setdefault(row["theme_id"], []).append(_row_to_evidence_item(row))
    return {theme_id: tuple(items) for theme_id, items in by_theme.items()}


def _row_to_company_map_entry(row) -> ThemeCompanyMapEntry:
    return ThemeCompanyMapEntry(
        id=row["id"],
        theme_id=row["theme_id"],
        company_name=row["company_name"],
        role=CompanyRole(row["role"]),
        note=row["note"],
    )


def insert_theme_company_map_entry(conn: psycopg.Connection, entry: ThemeCompanyMapEntry) -> bool:
    try:
        conn.execute(
            """
            INSERT INTO theme_company_map_entries (id, theme_id, company_name, role, note)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (entry.id, entry.theme_id, entry.company_name, entry.role.value, entry.note),
        )
    except psycopg.errors.UniqueViolation:
        conn.rollback()
        return False
    conn.commit()
    return True


def company_map_for_theme_ids(
    conn: psycopg.Connection, theme_ids: Sequence[str],
) -> dict[str, tuple[ThemeCompanyMapEntry, ...]]:
    """Bulk, read-only counterpart to evidence_for_theme_ids()."""
    if not theme_ids:
        return {}
    rows = conn.execute(
        "SELECT * FROM theme_company_map_entries WHERE theme_id = ANY(%s) ORDER BY theme_id, role, company_name, id",
        (list(theme_ids),),
    ).fetchall()
    by_theme: dict[str, list[ThemeCompanyMapEntry]] = {}
    for row in rows:
        by_theme.setdefault(row["theme_id"], []).append(_row_to_company_map_entry(row))
    return {theme_id: tuple(items) for theme_id, items in by_theme.items()}


def _row_to_research_note(row) -> ThemeResearchNote:
    return ThemeResearchNote(
        id=row["id"],
        theme_id=row["theme_id"],
        note_type=ThemeNoteType(row["note_type"]),
        content=row["content"],
        confidence=HypothesisConfidence(row["confidence"]) if row["confidence"] is not None else None,
        disconfirming_condition=row["disconfirming_condition"],
        created_at=row["created_at"],
    )


def insert_theme_research_note(conn: psycopg.Connection, note: ThemeResearchNote) -> bool:
    try:
        conn.execute(
            """
            INSERT INTO theme_research_notes (
                id, theme_id, note_type, content, confidence, disconfirming_condition, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                note.id, note.theme_id, note.note_type.value, note.content,
                note.confidence.value if note.confidence is not None else None,
                note.disconfirming_condition, note.created_at,
            ),
        )
    except psycopg.errors.UniqueViolation:
        conn.rollback()
        return False
    conn.commit()
    return True


def research_notes_for_theme_ids(
    conn: psycopg.Connection, theme_ids: Sequence[str],
) -> dict[str, tuple[ThemeResearchNote, ...]]:
    if not theme_ids:
        return {}
    rows = conn.execute(
        "SELECT * FROM theme_research_notes WHERE theme_id = ANY(%s) ORDER BY theme_id, created_at, id",
        (list(theme_ids),),
    ).fetchall()
    by_theme: dict[str, list[ThemeResearchNote]] = {}
    for row in rows:
        by_theme.setdefault(row["theme_id"], []).append(_row_to_research_note(row))
    return {theme_id: tuple(items) for theme_id, items in by_theme.items()}
