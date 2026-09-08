"""EevaResearch — Phase A1 (design/DECISIONS.md). SQLite-backed
persistence for the internal theme-matching model family — the
transactional counterpart to src.data_access.theme_matching_store's
JSON backend, mirroring state_db/theme_repository.py's own shape.

Insert-only for every record type in this phase — no update/replace/
upsert/delete function exists anywhere in this module. See
theme_matching_store.py's own module docstring for the full rationale
(scopes are keyed by theme_id with no revisioning yet; matches/
decisions are immutable; "pending review" is derived from the absence
of a decision row, never a mutable status column)."""
from __future__ import annotations

import json
import sqlite3
from typing import Sequence

from src.data_access.state_db.connection import transaction
from src.models.theme_matching import (
    MatchConfidence,
    MatchReviewStatus,
    ResearchCaseThemeMatch,
    ThemeMatchingScope,
    ThemeMatchReviewDecision,
)
from src.models.theme_research import EvidenceDirection, ThemeVisibility


def _row_to_scope(row: sqlite3.Row) -> ThemeMatchingScope:
    return ThemeMatchingScope(
        theme_id=row["theme_id"],
        sector_tags=tuple(json.loads(row["sector_tags_json"])),
        sector_subtags=tuple(json.loads(row["sector_subtags_json"])),
        allowed_matched_rule_categories=tuple(json.loads(row["allowed_matched_rule_categories_json"])),
        required_keywords=tuple(json.loads(row["required_keywords_json"])),
        excluded_keywords=tuple(json.loads(row["excluded_keywords_json"])),
    )


def get_scope(conn: sqlite3.Connection, theme_id: str) -> ThemeMatchingScope | None:
    row = conn.execute("SELECT * FROM theme_matching_scopes WHERE theme_id = ?", (theme_id,)).fetchone()
    return _row_to_scope(row) if row is not None else None


def insert_scope(conn: sqlite3.Connection, scope: ThemeMatchingScope) -> bool:
    """INSERT-only: True when newly inserted, False when a scope for
    this exact theme_id already existed — nothing written or changed."""
    with transaction(conn):
        try:
            conn.execute(
                """
                INSERT INTO theme_matching_scopes (
                    theme_id, sector_tags_json, sector_subtags_json,
                    allowed_matched_rule_categories_json, required_keywords_json, excluded_keywords_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    scope.theme_id, json.dumps(list(scope.sector_tags)), json.dumps(list(scope.sector_subtags)),
                    json.dumps(list(scope.allowed_matched_rule_categories)), json.dumps(list(scope.required_keywords)),
                    json.dumps(list(scope.excluded_keywords)),
                ),
            )
        except sqlite3.IntegrityError:
            return False
    return True


def list_active_scopes(conn: sqlite3.Connection) -> tuple[ThemeMatchingScope, ...]:
    """A scope is active exactly when its referenced ResearchTheme's
    own `visibility` is anything other than ARCHIVED — see
    theme_matching_store.list_active_scopes()'s own docstring for the
    full rationale. One join query, deterministically ordered by
    theme_id."""
    rows = conn.execute(
        """
        SELECT s.* FROM theme_matching_scopes s
        JOIN research_themes t ON t.id = s.theme_id
        WHERE t.visibility != ?
        ORDER BY s.theme_id
        """,
        (ThemeVisibility.ARCHIVED.value,),
    ).fetchall()
    return tuple(_row_to_scope(row) for row in rows)


def _row_to_match(row: sqlite3.Row) -> ResearchCaseThemeMatch:
    return ResearchCaseThemeMatch(
        id=row["id"],
        case_id=row["case_id"],
        theme_id=row["theme_id"],
        confidence=MatchConfidence(row["confidence"]),
        direction=EvidenceDirection(row["direction"]),
        matched_sector_tag=row["matched_sector_tag"],
        matched_rule_categories=tuple(json.loads(row["matched_rule_categories_json"])),
        matched_keywords=tuple(json.loads(row["matched_keywords_json"])),
        rationale=row["rationale"],
        created_at=row["created_at"],
    )


def insert_match(conn: sqlite3.Connection, match: ResearchCaseThemeMatch) -> bool:
    """INSERT-only. `match.id` is already deterministically derived
    from (case_id, theme_id) — see src.logic.research_case_theme_matching
    — so this single id-existence check (enforced by the primary key)
    also fully protects against a duplicate (case_id, theme_id) pair;
    the additional unique index on (case_id, theme_id) is belt-and-
    suspenders, not a second independent check this function performs."""
    with transaction(conn):
        try:
            conn.execute(
                """
                INSERT INTO research_case_theme_matches (
                    id, case_id, theme_id, confidence, direction, matched_sector_tag,
                    matched_rule_categories_json, matched_keywords_json, rationale, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    match.id, match.case_id, match.theme_id, match.confidence.value, match.direction.value,
                    match.matched_sector_tag, json.dumps(list(match.matched_rule_categories)),
                    json.dumps(list(match.matched_keywords)), match.rationale, match.created_at,
                ),
            )
        except sqlite3.IntegrityError:
            return False
    return True


def get_match(conn: sqlite3.Connection, match_id: str) -> ResearchCaseThemeMatch | None:
    """Read-only single-match lookup — lets a caller (e.g. a match-
    promotion tool) verify and read the actual stored match content
    before acting on it, rather than trusting a blindly recomputed id."""
    row = conn.execute("SELECT * FROM research_case_theme_matches WHERE id = ?", (match_id,)).fetchone()
    return _row_to_match(row) if row is not None else None


def existing_match_ids_for_case_ids(conn: sqlite3.Connection, case_ids: Sequence[str]) -> frozenset[str]:
    """Bulk, read-only: the ids of every persisted match whose case_id
    is one of the supplied `case_ids`. Exactly one parameterized query
    for a non-empty request; empty input returns `frozenset()`
    immediately, executing no SQL at all."""
    if not case_ids:
        return frozenset()
    placeholders = ", ".join("?" for _ in case_ids)
    rows = conn.execute(
        f"SELECT id FROM research_case_theme_matches WHERE case_id IN ({placeholders})", tuple(case_ids),
    ).fetchall()
    return frozenset(row["id"] for row in rows)


def list_pending_matches(conn: sqlite3.Connection) -> tuple[ResearchCaseThemeMatch, ...]:
    """Every match with zero rows in theme_match_review_decisions — the
    pending queue is derived entirely from this absence, never a stored
    status field. One anti-join query, deterministically ordered by
    (created_at, id)."""
    rows = conn.execute(
        """
        SELECT m.* FROM research_case_theme_matches m
        WHERE NOT EXISTS (SELECT 1 FROM theme_match_review_decisions d WHERE d.match_id = m.id)
        ORDER BY m.created_at, m.id
        """
    ).fetchall()
    return tuple(_row_to_match(row) for row in rows)


def _row_to_review_decision(row: sqlite3.Row) -> ThemeMatchReviewDecision:
    return ThemeMatchReviewDecision(
        id=row["id"],
        match_id=row["match_id"],
        decision=MatchReviewStatus(row["decision"]),
        reviewer_note=row["reviewer_note"],
        reviewed_at=row["reviewed_at"],
    )


def insert_review_decision(conn: sqlite3.Connection, decision: ThemeMatchReviewDecision) -> bool:
    """INSERT-only. Decisions are never updated in place — a corrected
    decision is a new, separately-authored record."""
    with transaction(conn):
        try:
            conn.execute(
                """
                INSERT INTO theme_match_review_decisions (id, match_id, decision, reviewer_note, reviewed_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (decision.id, decision.match_id, decision.decision.value, decision.reviewer_note, decision.reviewed_at),
            )
        except sqlite3.IntegrityError:
            return False
    return True


def list_review_decisions_for_match(conn: sqlite3.Connection, match_id: str) -> tuple[ThemeMatchReviewDecision, ...]:
    """Every decision recorded against one match, one query,
    deterministically ordered by (reviewed_at, id) — the complete,
    immutable audit trail for that match."""
    rows = conn.execute(
        "SELECT * FROM theme_match_review_decisions WHERE match_id = ? ORDER BY reviewed_at, id", (match_id,),
    ).fetchall()
    return tuple(_row_to_review_decision(row) for row in rows)
