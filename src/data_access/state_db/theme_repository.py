"""EevaResearch — Evidence-First Themes MVP (design/DECISIONS.md).
SQLite-backed persistence for the public ResearchTheme model family —
the transactional counterpart to src.data_access.theme_store's JSON
backend, mirroring state_db/research_repository.py's own shape.

Insert-only for evidence and company-map entries — no update/replace/
upsert/delete function exists for either. `set_theme_visibility()` is
the one deliberate exception (see theme_store.py's own module
docstring for why) — a real `UPDATE research_themes SET visibility =
..., updated_at = ... WHERE id = ...` statement, never exposed to the
public read protocol in backend_factory.py."""
from __future__ import annotations

import sqlite3
from typing import Sequence

from src.data_access.state_db.connection import transaction
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


def _row_to_theme(row: sqlite3.Row) -> ResearchTheme:
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


def get_theme(conn: sqlite3.Connection, theme_id: str) -> ResearchTheme | None:
    """Read-only single-theme lookup, any visibility — curator/private
    use only. See get_published_theme() for the public-safe equivalent."""
    row = conn.execute("SELECT * FROM research_themes WHERE id = ?", (theme_id,)).fetchone()
    return _row_to_theme(row) if row is not None else None


def get_published_theme(conn: sqlite3.Connection, theme_id: str) -> ResearchTheme | None:
    row = conn.execute(
        "SELECT * FROM research_themes WHERE id = ? AND visibility = ?", (theme_id, ThemeVisibility.PUBLISHED.value),
    ).fetchone()
    return _row_to_theme(row) if row is not None else None


def list_published_themes(conn: sqlite3.Connection) -> tuple[ResearchTheme, ...]:
    rows = conn.execute(
        "SELECT * FROM research_themes WHERE visibility = ? ORDER BY updated_at DESC, id DESC",
        (ThemeVisibility.PUBLISHED.value,),
    ).fetchall()
    return tuple(_row_to_theme(row) for row in rows)


def insert_theme(conn: sqlite3.Connection, theme: ResearchTheme) -> bool:
    """INSERT-only: True when newly inserted, False when a row with
    this exact id already existed — nothing written or changed."""
    with transaction(conn):
        try:
            conn.execute(
                """
                INSERT INTO research_themes (
                    id, category, status, visibility, title, key_question, hypothesis,
                    working_thesis, why_it_matters, what_could_change_the_view, what_to_watch_next,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    theme.id, theme.category.value, theme.status.value, theme.visibility.value, theme.title,
                    theme.key_question, theme.hypothesis, theme.working_thesis, theme.why_it_matters,
                    theme.what_could_change_the_view, theme.what_to_watch_next, theme.created_at, theme.updated_at,
                ),
            )
        except sqlite3.IntegrityError:
            return False
    return True


def set_theme_visibility(
    conn: sqlite3.Connection, theme_id: str, new_visibility: ThemeVisibility, updated_at: str,
) -> ResearchTheme | None:
    """The one deliberate update path — see theme_store.py's own
    module docstring. Curator/private use only. Returns the updated
    theme, or None if `theme_id` doesn't exist (no-op, nothing written)."""
    with transaction(conn):
        cursor = conn.execute(
            "UPDATE research_themes SET visibility = ?, updated_at = ? WHERE id = ?",
            (new_visibility.value, updated_at, theme_id),
        )
        if cursor.rowcount == 0:
            return None
    return get_theme(conn, theme_id)


def _row_to_evidence_item(row: sqlite3.Row) -> ThemeEvidenceItem:
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


def insert_theme_evidence_item(conn: sqlite3.Connection, item: ThemeEvidenceItem) -> bool:
    with transaction(conn):
        try:
            conn.execute(
                """
                INSERT INTO theme_evidence_items (
                    id, theme_id, date, company, source_name, source_url, fact, relevance, direction
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id, item.theme_id, item.date, item.company, item.source_name, item.source_url,
                    item.fact, item.relevance, item.direction.value,
                ),
            )
        except sqlite3.IntegrityError:
            return False
    return True


def evidence_for_theme_ids(
    conn: sqlite3.Connection, theme_ids: Sequence[str],
) -> dict[str, tuple[ThemeEvidenceItem, ...]]:
    """Bulk, read-only: exactly one parameterized query for a
    non-empty request; empty input returns `{}` immediately without
    executing any SQL. Deterministically ordered by (theme_id, date, id)."""
    if not theme_ids:
        return {}
    placeholders = ", ".join("?" for _ in theme_ids)
    rows = conn.execute(
        f"SELECT * FROM theme_evidence_items WHERE theme_id IN ({placeholders}) ORDER BY theme_id, date, id",
        tuple(theme_ids),
    ).fetchall()
    by_theme: dict[str, list[ThemeEvidenceItem]] = {}
    for row in rows:
        by_theme.setdefault(row["theme_id"], []).append(_row_to_evidence_item(row))
    return {theme_id: tuple(items) for theme_id, items in by_theme.items()}


def _row_to_company_map_entry(row: sqlite3.Row) -> ThemeCompanyMapEntry:
    return ThemeCompanyMapEntry(
        id=row["id"],
        theme_id=row["theme_id"],
        company_name=row["company_name"],
        role=CompanyRole(row["role"]),
        note=row["note"],
    )


def insert_theme_company_map_entry(conn: sqlite3.Connection, entry: ThemeCompanyMapEntry) -> bool:
    with transaction(conn):
        try:
            conn.execute(
                """
                INSERT INTO theme_company_map_entries (id, theme_id, company_name, role, note)
                VALUES (?, ?, ?, ?, ?)
                """,
                (entry.id, entry.theme_id, entry.company_name, entry.role.value, entry.note),
            )
        except sqlite3.IntegrityError:
            return False
    return True


def company_map_for_theme_ids(
    conn: sqlite3.Connection, theme_ids: Sequence[str],
) -> dict[str, tuple[ThemeCompanyMapEntry, ...]]:
    """Bulk, read-only counterpart to evidence_for_theme_ids() — same
    one-query, empty-input-executes-no-SQL, and deterministic
    (theme_id, role, company_name, id) ordering."""
    if not theme_ids:
        return {}
    placeholders = ", ".join("?" for _ in theme_ids)
    rows = conn.execute(
        f"SELECT * FROM theme_company_map_entries WHERE theme_id IN ({placeholders}) ORDER BY theme_id, role, company_name, id",
        tuple(theme_ids),
    ).fetchall()
    by_theme: dict[str, list[ThemeCompanyMapEntry]] = {}
    for row in rows:
        by_theme.setdefault(row["theme_id"], []).append(_row_to_company_map_entry(row))
    return {theme_id: tuple(items) for theme_id, items in by_theme.items()}


def _row_to_research_note(row: sqlite3.Row) -> ThemeResearchNote:
    return ThemeResearchNote(
        id=row["id"],
        theme_id=row["theme_id"],
        note_type=ThemeNoteType(row["note_type"]),
        content=row["content"],
        confidence=HypothesisConfidence(row["confidence"]) if row["confidence"] is not None else None,
        disconfirming_condition=row["disconfirming_condition"],
        created_at=row["created_at"],
    )


def insert_theme_research_note(conn: sqlite3.Connection, note: ThemeResearchNote) -> bool:
    """INSERT-only, exactly like evidence items and company-map
    entries."""
    with transaction(conn):
        try:
            conn.execute(
                """
                INSERT INTO theme_research_notes (
                    id, theme_id, note_type, content, confidence, disconfirming_condition, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    note.id, note.theme_id, note.note_type.value, note.content,
                    note.confidence.value if note.confidence is not None else None,
                    note.disconfirming_condition, note.created_at,
                ),
            )
        except sqlite3.IntegrityError:
            return False
    return True


def research_notes_for_theme_ids(
    conn: sqlite3.Connection, theme_ids: Sequence[str],
) -> dict[str, tuple[ThemeResearchNote, ...]]:
    """Bulk, read-only counterpart to evidence_for_theme_ids(). Ordered
    by (theme_id, created_at, id) — a chronological research log."""
    if not theme_ids:
        return {}
    placeholders = ", ".join("?" for _ in theme_ids)
    rows = conn.execute(
        f"SELECT * FROM theme_research_notes WHERE theme_id IN ({placeholders}) ORDER BY theme_id, created_at, id",
        tuple(theme_ids),
    ).fetchall()
    by_theme: dict[str, list[ThemeResearchNote]] = {}
    for row in rows:
        by_theme.setdefault(row["theme_id"], []).append(_row_to_research_note(row))
    return {theme_id: tuple(items) for theme_id, items in by_theme.items()}
