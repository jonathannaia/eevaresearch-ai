"""SQLite-backed Research Case persistence — the transactional,
insert-only counterpart to src.data_access.research_store's JSON
backend, mirroring state_db/comparison_repository.py's own shape and
conventions. EevaResearch Phase 4, Step 1 (design/DECISIONS.md).

Deliberate divergence from the JSON store's tolerant-load behavior,
matching comparison_repository.py's own module docstring convention:
functions here never catch sqlite3 errors beyond the one explicitly-
handled duplicate-id case in each insert function — a genuine database
failure propagates as a real exception rather than becoming a silently-
empty result.

No update/replace/upsert/delete function exists anywhere in this module
for any of the three tables — insert and read only.

Phase 4, Step 3B (design/DECISIONS.md) additionally adds
insert_research_case_bundle() — a single atomic, validation-first,
all-or-nothing write of one ResearchCaseBundle (its case, every evidence
item, every assertion) inside one transaction. A genuine ACID
transaction, unlike the JSON backend's own append_research_case_bundle()
— see that function's docstring for the crash-consistency limitation
this backend does not share."""
from __future__ import annotations

import json
import sqlite3
from typing import Sequence

from src.data_access.state_db.connection import transaction
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


def _row_to_case(row: sqlite3.Row) -> ResearchCase:
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


def get_research_case(conn: sqlite3.Connection, case_id: str) -> ResearchCase | None:
    """Read-only single-case lookup. Never triggers creation."""
    row = conn.execute("SELECT * FROM research_cases WHERE id = ?", (case_id,)).fetchone()
    return _row_to_case(row) if row is not None else None


def list_recent_cases(conn: sqlite3.Connection, limit: int) -> tuple[ResearchCase, ...]:
    """EevaResearch Phase 4, Step 3C (design/DECISIONS.md) — bounded,
    read-only, most-recent-first case list for the tester-facing Research
    Cases page. One parameterized query, deterministically ordered by
    (created_at DESC, id DESC); `limit <= 0` returns an empty tuple
    immediately, executing no SQL at all."""
    if limit <= 0:
        return ()
    rows = conn.execute(
        "SELECT * FROM research_cases ORDER BY created_at DESC, id DESC LIMIT ?", (limit,),
    ).fetchall()
    return tuple(_row_to_case(row) for row in rows)


def get_existing_case_ids(conn: sqlite3.Connection, case_ids: Sequence[str]) -> frozenset[str]:
    """EevaResearch Phase 4, Step 4B-1 (design/DECISIONS.md) — bounded,
    read-only bulk membership check: which of the supplied ids already
    exist in `research_cases`. Exactly one parameterized query for a
    non-empty request (never one query per id, never a full-table scan);
    empty input returns `frozenset()` immediately, executing no SQL at
    all. Duplicate ids in the input are harmless — the result is a set,
    so repeats collapse naturally. Placeholders are built only from the
    number of supplied ids; no id value is ever interpolated into the
    SQL text itself."""
    if not case_ids:
        return frozenset()
    placeholders = ", ".join("?" for _ in case_ids)
    rows = conn.execute(
        f"SELECT id FROM research_cases WHERE id IN ({placeholders})", tuple(case_ids),
    ).fetchall()
    return frozenset(row["id"] for row in rows)


def insert_research_case(conn: sqlite3.Connection, case: ResearchCase) -> bool:
    """INSERT-only: no update/replace/upsert path exists for this table
    anywhere in this codebase. Returns True when newly inserted; False
    when a row with this exact stable id already existed — nothing is
    written or changed (rejected by the table's own PRIMARY KEY
    constraint, caught here rather than propagated)."""
    with transaction(conn):
        try:
            conn.execute(
                """
                INSERT INTO research_cases (
                    id, trigger_source_type, trigger_source_id, trigger_source_name, trigger_summary,
                    title, research_question, status, created_at, version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case.id, case.trigger_source_type, case.trigger_source_id, case.trigger_source_name,
                    case.trigger_summary, case.title, case.research_question, case.status.value,
                    case.created_at, case.version,
                ),
            )
        except sqlite3.IntegrityError:
            return False
    return True


def _row_to_evidence_item(row: sqlite3.Row) -> ResearchEvidenceItem:
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


def insert_evidence_item(conn: sqlite3.Connection, item: ResearchEvidenceItem) -> bool:
    with transaction(conn):
        try:
            conn.execute(
                """
                INSERT INTO research_evidence_items (
                    id, case_id, source_type, source_id, source_url, source_publisher_or_system,
                    source_date, retrieved_at, excerpt_original, original_language, added_at,
                    excerpt_translated, translation_provider
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id, item.case_id, item.source_type, item.source_id, item.source_url,
                    item.source_publisher_or_system, item.source_date, item.retrieved_at,
                    item.excerpt_original, item.original_language, item.added_at,
                    item.excerpt_translated, item.translation_provider,
                ),
            )
        except sqlite3.IntegrityError:
            return False
    return True


def get_evidence_items_for_case_ids(
    conn: sqlite3.Connection, case_ids: Sequence[str],
) -> dict[str, tuple[ResearchEvidenceItem, ...]]:
    """Bulk, read-only: exactly one parameterized query for a non-empty
    request (never one query per case id); empty input returns `{}`
    immediately without executing any SQL at all. Deterministically
    ordered by (case_id, added_at, id) — never SQLite's physical row
    order. Placeholders are built only from the number of supplied ids;
    no id value is ever interpolated into the SQL text itself."""
    if not case_ids:
        return {}
    placeholders = ", ".join("?" for _ in case_ids)
    rows = conn.execute(
        f"SELECT * FROM research_evidence_items WHERE case_id IN ({placeholders}) ORDER BY case_id, added_at, id",
        tuple(case_ids),
    ).fetchall()
    by_case: dict[str, list[ResearchEvidenceItem]] = {}
    for row in rows:
        by_case.setdefault(row["case_id"], []).append(_row_to_evidence_item(row))
    return {case_id: tuple(items) for case_id, items in by_case.items()}


def _row_to_assertion(row: sqlite3.Row) -> RelationshipAssertion | DependencyAssertion:
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
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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


def insert_assertion(conn: sqlite3.Connection, assertion: RelationshipAssertion | DependencyAssertion) -> bool:
    """INSERT-only. Accepts either a RelationshipAssertion or a
    DependencyAssertion; both persist into the one shared
    research_assertions table, discriminated by the `kind` column."""
    params = _assertion_insert_params(assertion)
    with transaction(conn):
        try:
            conn.execute(_INSERT_ASSERTION_SQL, params)
        except sqlite3.IntegrityError:
            return False
    return True


def get_assertions_for_case_ids(
    conn: sqlite3.Connection, case_ids: Sequence[str],
) -> dict[str, tuple[RelationshipAssertion | DependencyAssertion, ...]]:
    """Bulk, read-only counterpart to get_evidence_items_for_case_ids() —
    same one-query, empty-input-executes-no-SQL, and deterministic
    (case_id, created_at, id) ordering."""
    if not case_ids:
        return {}
    placeholders = ", ".join("?" for _ in case_ids)
    rows = conn.execute(
        f"SELECT * FROM research_assertions WHERE case_id IN ({placeholders}) ORDER BY case_id, created_at, id",
        tuple(case_ids),
    ).fetchall()
    by_case: dict[str, list[RelationshipAssertion | DependencyAssertion]] = {}
    for row in rows:
        by_case.setdefault(row["case_id"], []).append(_row_to_assertion(row))
    return {case_id: tuple(assertions) for case_id, assertions in by_case.items()}


# --- Atomic bundle persistence (Phase 4, Step 3B) --------------------------


def insert_research_case_bundle(conn: sqlite3.Connection, bundle: ResearchCaseBundle) -> bool:
    """Atomic, validation-first, all-or-nothing persistence of one
    ResearchCaseBundle — Phase 4, Step 3B. Never creates or modifies a
    model object, id, timestamp, quote, URL, entity, assertion, or
    validation result.

    Contract:
      1. validate_research_case_bundle(bundle) must return no errors, or
         this function returns False immediately with no SQL executed
         at all.
      2. The case insert, every evidence-item insert, and every
         assertion insert happen inside exactly one
         state_db.connection.transaction(conn) block — one commit only,
         after every statement has succeeded.
      3. A duplicate id (the case, any evidence item, or any assertion)
         raises sqlite3.IntegrityError from that one INSERT statement;
         transaction() rolls back the entire transaction (every
         statement already executed earlier in this same call,
         included) and re-raises, which this function catches and turns
         into a plain `False` — never a partial commit, never a
         propagated exception for this expected case.
      4. Any other exception during the transaction is caught the same
         way and also returns False, with the same full rollback —
         "on every exception, roll back the entire transaction," not
         only the specific duplicate-id case.

    This is a genuine ACID transaction: unlike the JSON backend's own
    append_research_case_bundle(), there is no crash-consistency window
    to document here — SQLite's own transaction durability applies."""
    if validate_research_case_bundle(bundle):
        return False

    try:
        with transaction(conn):
            conn.execute(
                """
                INSERT INTO research_cases (
                    id, trigger_source_type, trigger_source_id, trigger_source_name, trigger_summary,
                    title, research_question, status, created_at, version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        return False
    return True
