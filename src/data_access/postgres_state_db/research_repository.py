"""Postgres-backed Research Case persistence — the isolated Postgres
counterpart to src/data_access/state_db/research_repository.py.
EevaResearch Phase 4, Step 1 (design/DECISIONS.md).

Deliberate Postgres-specific divergence from the SQLite module: a failed
INSERT (a PRIMARY KEY violation) leaves a Postgres transaction in an
aborted state until an explicit ROLLBACK — unlike SQLite, where a single
failed statement doesn't poison the rest of the transaction — so every
insert function below manages its own commit/rollback directly around
the one statement, exactly mirroring
postgres_state_db/comparison_repository.py's own established, verified
behavior (not assumed to transfer for free from the SQLite version).

No update/replace/upsert/delete function exists anywhere in this module
for any of the three tables — insert and read only.

Phase 4, Step 3B (design/DECISIONS.md) additionally adds
insert_research_case_bundle() — a single atomic, validation-first,
all-or-nothing write of one ResearchCaseBundle inside one explicit
commit/rollback boundary. A genuine transaction, like the SQLite
counterpart — no crash-consistency caveat here, unlike the JSON
backend's own append_research_case_bundle()."""
from __future__ import annotations

import json
from typing import Sequence

import psycopg

from src.logic.research_case_validation import ResearchCaseBundle, validate_research_case_bundle
from src.models.research_case import (
    AssertionConfidence,
    AssertionStatus,
    BottleneckType,
    DependencyAssertion,
    RelationshipAssertion,
    RelationshipRole,
    ResearchCase,
    ResearchCaseStatus,
    ResearchEvidenceItem,
)


def _row_to_case(row) -> ResearchCase:
    return ResearchCase(
        id=row["id"],
        trigger_source_type=row["trigger_source_type"],
        trigger_source_id=row["trigger_source_id"],
        trigger_source_name=row["trigger_source_name"],
        trigger_summary=row["trigger_summary"],
        title=row["title"],
        research_question=row["research_question"],
        status=ResearchCaseStatus(row["status"]),
        created_at=row["created_at"],
        version=row["version"],
    )


def get_research_case(conn: psycopg.Connection, case_id: str) -> ResearchCase | None:
    row = conn.execute("SELECT * FROM research_cases WHERE id = %s", (case_id,)).fetchone()
    return _row_to_case(row) if row is not None else None


def list_recent_cases(conn: psycopg.Connection, limit: int) -> tuple[ResearchCase, ...]:
    """EevaResearch Phase 4, Step 3C (design/DECISIONS.md) — bounded,
    read-only, most-recent-first case list, same contract as the SQLite
    counterpart: one parameterized query, ordered by
    (created_at DESC, id DESC); `limit <= 0` returns an empty tuple
    immediately, executing no query at all."""
    if limit <= 0:
        return ()
    rows = conn.execute(
        "SELECT * FROM research_cases ORDER BY created_at DESC, id DESC LIMIT %s", (limit,),
    ).fetchall()
    return tuple(_row_to_case(row) for row in rows)


def get_existing_case_ids(conn: psycopg.Connection, case_ids: Sequence[str]) -> frozenset[str]:
    """EevaResearch Phase 4, Step 4B-1 (design/DECISIONS.md) — bounded,
    read-only bulk membership check, same contract as the SQLite
    counterpart: exactly one parameterized query for a non-empty
    request (never one query per id); empty input returns
    `frozenset()` immediately, executing no query at all. Uses
    `= ANY(%s)` with a single list parameter — this package's existing
    convention for a variable-length id match (see
    get_evidence_items_for_case_ids above)."""
    if not case_ids:
        return frozenset()
    rows = conn.execute(
        "SELECT id FROM research_cases WHERE id = ANY(%s)", (list(case_ids),),
    ).fetchall()
    return frozenset(row["id"] for row in rows)


def insert_research_case(conn: psycopg.Connection, case: ResearchCase) -> bool:
    """INSERT-only. Returns True when newly inserted; False when a row
    with this exact stable id already existed — the failed INSERT is
    rolled back explicitly (see module docstring) so the connection is
    left usable for the caller's next statement."""
    try:
        conn.execute(
            """
            INSERT INTO research_cases (
                id, trigger_source_type, trigger_source_id, trigger_source_name, trigger_summary,
                title, research_question, status, created_at, version
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                case.id, case.trigger_source_type, case.trigger_source_id, case.trigger_source_name,
                case.trigger_summary, case.title, case.research_question, case.status.value,
                case.created_at, case.version,
            ),
        )
    except psycopg.errors.UniqueViolation:
        conn.rollback()
        return False
    conn.commit()
    return True


def _row_to_evidence_item(row) -> ResearchEvidenceItem:
    return ResearchEvidenceItem(
        id=row["id"],
        case_id=row["case_id"],
        source_type=row["source_type"],
        source_id=row["source_id"],
        source_url=row["source_url"],
        source_publisher_or_system=row["source_publisher_or_system"],
        source_date=row["source_date"],
        retrieved_at=row["retrieved_at"],
        excerpt_original=row["excerpt_original"],
        original_language=row["original_language"],
        added_at=row["added_at"],
        excerpt_translated=row["excerpt_translated"],
        translation_provider=row["translation_provider"],
    )


def insert_evidence_item(conn: psycopg.Connection, item: ResearchEvidenceItem) -> bool:
    try:
        conn.execute(
            """
            INSERT INTO research_evidence_items (
                id, case_id, source_type, source_id, source_url, source_publisher_or_system,
                source_date, retrieved_at, excerpt_original, original_language, added_at,
                excerpt_translated, translation_provider
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                item.id, item.case_id, item.source_type, item.source_id, item.source_url,
                item.source_publisher_or_system, item.source_date, item.retrieved_at,
                item.excerpt_original, item.original_language, item.added_at,
                item.excerpt_translated, item.translation_provider,
            ),
        )
    except psycopg.errors.UniqueViolation:
        conn.rollback()
        return False
    conn.commit()
    return True


def get_evidence_items_for_case_ids(
    conn: psycopg.Connection, case_ids: Sequence[str],
) -> dict[str, tuple[ResearchEvidenceItem, ...]]:
    """Bulk, read-only: exactly one parameterized query for a non-empty
    request (never one query per case id); empty input returns `{}`
    immediately without executing any SQL at all. Uses `= ANY(%s)` with
    a single list parameter — this package's existing convention for a
    variable-length id match (see postgres_state_db/comparison_
    repository.py's own bulk read). Deterministically ordered by
    (case_id, added_at, id)."""
    if not case_ids:
        return {}
    rows = conn.execute(
        "SELECT * FROM research_evidence_items WHERE case_id = ANY(%s) ORDER BY case_id, added_at, id",
        (list(case_ids),),
    ).fetchall()
    by_case: dict[str, list[ResearchEvidenceItem]] = {}
    for row in rows:
        by_case.setdefault(row["case_id"], []).append(_row_to_evidence_item(row))
    return {case_id: tuple(items) for case_id, items in by_case.items()}


def _row_to_assertion(row) -> RelationshipAssertion | DependencyAssertion:
    kind = row["kind"]
    if kind == "relationship":
        return RelationshipAssertion(
            id=row["id"],
            case_id=row["case_id"],
            subject_entity=row["subject_entity"],
            object_entity=row["object_entity"],
            role=RelationshipRole(row["role"]),
            assertion_status=AssertionStatus(row["assertion_status"]),
            evidence_ids=tuple(json.loads(row["evidence_ids_json"])),
            confidence=AssertionConfidence(row["confidence"]),
            created_at=row["created_at"],
            reasoning=row["reasoning"],
            limitations=tuple(json.loads(row["limitations_json"])),
        )
    if kind == "dependency":
        transmission_path_json = row["transmission_path_json"]
        return DependencyAssertion(
            id=row["id"],
            case_id=row["case_id"],
            affected_entity=row["affected_entity"],
            bottleneck_type=BottleneckType(row["bottleneck_type"]),
            supply_chain_layer=row["supply_chain_layer"],
            transmission_path=tuple(json.loads(transmission_path_json)) if transmission_path_json is not None else None,
            assertion_status=AssertionStatus(row["assertion_status"]),
            evidence_ids=tuple(json.loads(row["evidence_ids_json"])),
            confidence=AssertionConfidence(row["confidence"]),
            created_at=row["created_at"],
            reasoning=row["reasoning"],
            limitations=tuple(json.loads(row["limitations_json"])),
        )
    raise ValueError(f"Unknown or missing research-assertion kind in stored row: {kind!r}")


_INSERT_ASSERTION_SQL = """
    INSERT INTO research_assertions (
        id, case_id, kind,
        subject_entity, object_entity, role,
        affected_entity, bottleneck_type, supply_chain_layer, transmission_path_json,
        assertion_status, evidence_ids_json, confidence, created_at, reasoning, limitations_json
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """


def _assertion_insert_params(assertion: RelationshipAssertion | DependencyAssertion) -> tuple:
    """Shared by insert_assertion() and insert_research_case_bundle() so
    the two never drift — the exact same column mapping either way."""
    if isinstance(assertion, RelationshipAssertion):
        return (
            assertion.id, assertion.case_id, "relationship",
            assertion.subject_entity, assertion.object_entity, assertion.role.value,
            None, None, None, None,
            assertion.assertion_status.value, json.dumps(list(assertion.evidence_ids)),
            assertion.confidence.value, assertion.created_at, assertion.reasoning,
            json.dumps(list(assertion.limitations)),
        )
    if isinstance(assertion, DependencyAssertion):
        transmission_path_json = json.dumps(list(assertion.transmission_path)) if assertion.transmission_path is not None else None
        return (
            assertion.id, assertion.case_id, "dependency",
            None, None, None,
            assertion.affected_entity, assertion.bottleneck_type.value, assertion.supply_chain_layer, transmission_path_json,
            assertion.assertion_status.value, json.dumps(list(assertion.evidence_ids)),
            assertion.confidence.value, assertion.created_at, assertion.reasoning,
            json.dumps(list(assertion.limitations)),
        )
    raise TypeError(f"Unsupported research-assertion type: {type(assertion)!r}")


def insert_assertion(conn: psycopg.Connection, assertion: RelationshipAssertion | DependencyAssertion) -> bool:
    """INSERT-only. Accepts either a RelationshipAssertion or a
    DependencyAssertion; both persist into the one shared
    research_assertions table, discriminated by the `kind` column."""
    params = _assertion_insert_params(assertion)
    try:
        conn.execute(_INSERT_ASSERTION_SQL, params)
    except psycopg.errors.UniqueViolation:
        conn.rollback()
        return False
    conn.commit()
    return True


def get_assertions_for_case_ids(
    conn: psycopg.Connection, case_ids: Sequence[str],
) -> dict[str, tuple[RelationshipAssertion | DependencyAssertion, ...]]:
    """Bulk, read-only counterpart to get_evidence_items_for_case_ids()."""
    if not case_ids:
        return {}
    rows = conn.execute(
        "SELECT * FROM research_assertions WHERE case_id = ANY(%s) ORDER BY case_id, created_at, id",
        (list(case_ids),),
    ).fetchall()
    by_case: dict[str, list[RelationshipAssertion | DependencyAssertion]] = {}
    for row in rows:
        by_case.setdefault(row["case_id"], []).append(_row_to_assertion(row))
    return {case_id: tuple(assertions) for case_id, assertions in by_case.items()}


# --- Atomic bundle persistence (Phase 4, Step 3B) --------------------------


def insert_research_case_bundle(conn: psycopg.Connection, bundle: ResearchCaseBundle) -> bool:
    """Atomic, validation-first, all-or-nothing persistence of one
    ResearchCaseBundle — Phase 4, Step 3B. Never creates or modifies a
    model object, id, timestamp, quote, URL, entity, assertion, or
    validation result.

    Contract:
      1. validate_research_case_bundle(bundle) must return no errors, or
         this function returns False immediately with no SQL executed
         at all.
      2. The case insert, every evidence-item insert, and every
         assertion insert execute as one implicit Postgres transaction
         (psycopg's default autocommit=False) — exactly one
         conn.commit() call, only after every statement has succeeded.
      3. A duplicate id (the case, any evidence item, or any assertion)
         raises psycopg.errors.UniqueViolation from that one INSERT
         statement — caught here, followed by an explicit conn.rollback()
         (undoing every statement already executed earlier in this same
         call) and returning False, never a partial commit.
      4. Any other exception during the sequence is caught the same way
         — rollback, then False — "on any unexpected persistence error,
         roll back the complete bundle," not only the duplicate-id case.
      5. The rollback leaves the connection immediately usable for the
         caller's next statement, exactly like every other Postgres
         insert function in this module (a caller must not need to
         reconnect after a rejected bundle)."""
    if validate_research_case_bundle(bundle):
        return False

    try:
        conn.execute(
            """
            INSERT INTO research_cases (
                id, trigger_source_type, trigger_source_id, trigger_source_name, trigger_summary,
                title, research_question, status, created_at, version
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                bundle.case.id, bundle.case.trigger_source_type, bundle.case.trigger_source_id,
                bundle.case.trigger_source_name, bundle.case.trigger_summary, bundle.case.title,
                bundle.case.research_question, bundle.case.status.value, bundle.case.created_at,
                bundle.case.version,
            ),
        )
        for item in bundle.evidence_items:
            conn.execute(
                """
                INSERT INTO research_evidence_items (
                    id, case_id, source_type, source_id, source_url, source_publisher_or_system,
                    source_date, retrieved_at, excerpt_original, original_language, added_at,
                    excerpt_translated, translation_provider
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    item.id, item.case_id, item.source_type, item.source_id, item.source_url,
                    item.source_publisher_or_system, item.source_date, item.retrieved_at,
                    item.excerpt_original, item.original_language, item.added_at,
                    item.excerpt_translated, item.translation_provider,
                ),
            )
        for assertion in bundle.assertions:
            conn.execute(_INSERT_ASSERTION_SQL, _assertion_insert_params(assertion))
    except Exception:
        conn.rollback()
        return False
    conn.commit()
    return True
