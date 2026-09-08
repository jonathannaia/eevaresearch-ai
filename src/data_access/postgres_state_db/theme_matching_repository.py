"""EevaResearch — Phase A1 (design/DECISIONS.md). Postgres-backed
persistence for the internal theme-matching model family — the
isolated Postgres counterpart to state_db/theme_matching_repository.py.

Deliberate Postgres-specific divergence from the SQLite module: a
failed INSERT leaves a Postgres transaction in an aborted state until
an explicit ROLLBACK — unlike SQLite — so every insert function below
manages its own commit/rollback directly around the one statement,
exactly mirroring postgres_state_db/theme_repository.py's own
established, verified behavior.

Insert-only for every record type in this phase — see
theme_matching_store.py's own module docstring for the full rationale."""
from __future__ import annotations

import json
from typing import Sequence

import psycopg

from src.models.theme_matching import (
    MatchConfidence,
    MatchReviewStatus,
    ResearchCaseThemeMatch,
    ThemeMatchingScope,
    ThemeMatchReviewDecision,
)
from src.models.theme_research import EvidenceDirection, ThemeVisibility


def _row_to_scope(row) -> ThemeMatchingScope:
    return ThemeMatchingScope(
        theme_id=row["theme_id"],
        sector_tags=tuple(json.loads(row["sector_tags_json"])),
        sector_subtags=tuple(json.loads(row["sector_subtags_json"])),
        allowed_matched_rule_categories=tuple(json.loads(row["allowed_matched_rule_categories_json"])),
        required_keywords=tuple(json.loads(row["required_keywords_json"])),
        excluded_keywords=tuple(json.loads(row["excluded_keywords_json"])),
    )


def get_scope(conn: psycopg.Connection, theme_id: str) -> ThemeMatchingScope | None:
    row = conn.execute("SELECT * FROM theme_matching_scopes WHERE theme_id = %s", (theme_id,)).fetchone()
    return _row_to_scope(row) if row is not None else None


def insert_scope(conn: psycopg.Connection, scope: ThemeMatchingScope) -> bool:
    """INSERT-only. Returns True when newly inserted; False when a
    scope for this exact theme_id already existed — the failed INSERT
    is rolled back explicitly so the connection is left usable for the
    caller's next statement."""
    try:
        conn.execute(
            """
            INSERT INTO theme_matching_scopes (
                theme_id, sector_tags_json, sector_subtags_json,
                allowed_matched_rule_categories_json, required_keywords_json, excluded_keywords_json
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                scope.theme_id, json.dumps(list(scope.sector_tags)), json.dumps(list(scope.sector_subtags)),
                json.dumps(list(scope.allowed_matched_rule_categories)), json.dumps(list(scope.required_keywords)),
                json.dumps(list(scope.excluded_keywords)),
            ),
        )
    except psycopg.errors.UniqueViolation:
        conn.rollback()
        return False
    conn.commit()
    return True


def list_active_scopes(conn: psycopg.Connection) -> tuple[ThemeMatchingScope, ...]:
    """Same contract as the SQLite counterpart — a scope is active
    exactly when its referenced ResearchTheme's own visibility is
    anything other than ARCHIVED."""
    rows = conn.execute(
        """
        SELECT s.* FROM theme_matching_scopes s
        JOIN research_themes t ON t.id = s.theme_id
        WHERE t.visibility != %s
        ORDER BY s.theme_id
        """,
        (ThemeVisibility.ARCHIVED.value,),
    ).fetchall()
    return tuple(_row_to_scope(row) for row in rows)


def _row_to_match(row) -> ResearchCaseThemeMatch:
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


def insert_match(conn: psycopg.Connection, match: ResearchCaseThemeMatch) -> bool:
    """INSERT-only. `match.id` is already deterministically derived
    from (case_id, theme_id) — this single id-existence check (enforced
    by the primary key) also fully protects against a duplicate
    (case_id, theme_id) pair."""
    try:
        conn.execute(
            """
            INSERT INTO research_case_theme_matches (
                id, case_id, theme_id, confidence, direction, matched_sector_tag,
                matched_rule_categories_json, matched_keywords_json, rationale, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                match.id, match.case_id, match.theme_id, match.confidence.value, match.direction.value,
                match.matched_sector_tag, json.dumps(list(match.matched_rule_categories)),
                json.dumps(list(match.matched_keywords)), match.rationale, match.created_at,
            ),
        )
    except psycopg.errors.UniqueViolation:
        conn.rollback()
        return False
    conn.commit()
    return True


def get_match(conn: psycopg.Connection, match_id: str) -> ResearchCaseThemeMatch | None:
    """Read-only single-match lookup — see the SQLite counterpart's own
    docstring for why this exists."""
    row = conn.execute("SELECT * FROM research_case_theme_matches WHERE id = %s", (match_id,)).fetchone()
    return _row_to_match(row) if row is not None else None


def existing_match_ids_for_case_ids(conn: psycopg.Connection, case_ids: Sequence[str]) -> frozenset[str]:
    """Bulk, read-only: the ids of every persisted match whose case_id
    is one of the supplied `case_ids`. Uses `= ANY(%s)` with a single
    list parameter — this package's existing convention for a
    variable-length id match. Empty input returns `frozenset()`
    immediately, executing no query at all."""
    if not case_ids:
        return frozenset()
    rows = conn.execute(
        "SELECT id FROM research_case_theme_matches WHERE case_id = ANY(%s)", (list(case_ids),),
    ).fetchall()
    return frozenset(row["id"] for row in rows)


def list_pending_matches(conn: psycopg.Connection) -> tuple[ResearchCaseThemeMatch, ...]:
    """Every match with zero rows in theme_match_review_decisions. One
    anti-join query, deterministically ordered by (created_at, id)."""
    rows = conn.execute(
        """
        SELECT m.* FROM research_case_theme_matches m
        WHERE NOT EXISTS (SELECT 1 FROM theme_match_review_decisions d WHERE d.match_id = m.id)
        ORDER BY m.created_at, m.id
        """
    ).fetchall()
    return tuple(_row_to_match(row) for row in rows)


def _row_to_review_decision(row) -> ThemeMatchReviewDecision:
    return ThemeMatchReviewDecision(
        id=row["id"],
        match_id=row["match_id"],
        decision=MatchReviewStatus(row["decision"]),
        reviewer_note=row["reviewer_note"],
        reviewed_at=row["reviewed_at"],
    )


def insert_review_decision(conn: psycopg.Connection, decision: ThemeMatchReviewDecision) -> bool:
    """INSERT-only. Decisions are never updated in place."""
    try:
        conn.execute(
            """
            INSERT INTO theme_match_review_decisions (id, match_id, decision, reviewer_note, reviewed_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (decision.id, decision.match_id, decision.decision.value, decision.reviewer_note, decision.reviewed_at),
        )
    except psycopg.errors.UniqueViolation:
        conn.rollback()
        return False
    conn.commit()
    return True


def list_review_decisions_for_match(conn: psycopg.Connection, match_id: str) -> tuple[ThemeMatchReviewDecision, ...]:
    """Every decision recorded against one match, one query,
    deterministically ordered by (reviewed_at, id)."""
    rows = conn.execute(
        "SELECT * FROM theme_match_review_decisions WHERE match_id = %s ORDER BY reviewed_at, id", (match_id,),
    ).fetchall()
    return tuple(_row_to_review_decision(row) for row in rows)
