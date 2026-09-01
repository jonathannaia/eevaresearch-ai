"""EevaResearch Phase 4, Step 1 (design/DECISIONS.md) — immutable Research
Case models and append-only persistence across JSON, SQLite, and
Postgres. Every fixture is synthetic and locally constructed; no real
filing, news fetch, scan, LLM call, or network call anywhere in this
file. Postgres tests use the shared, fail-soft local-only fixtures from
tests/_postgres_test_support.py and skip cleanly when no local
disposable Postgres instance is available."""
from __future__ import annotations

import ast
import dataclasses
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.data_access import research_store
from src.data_access.research_store import (
    append_assertion,
    append_evidence_item,
    append_research_case,
    assertions_for_case_ids,
    build_case_id,
    build_dependency_assertion_id,
    build_evidence_id,
    build_relationship_assertion_id,
    evidence_items_for_case_ids,
    get_research_case,
    load_assertions,
    load_evidence_items,
    load_research_cases,
)
from src.data_access.state_db import connection as sqlite_connection
from src.data_access.state_db import research_repository as sqlite_research_repository
from src.data_access.state_db import schema as sqlite_schema
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

from tests._postgres_test_support import pg_conn, pg_isolated_connection  # noqa: F401

try:
    from src.data_access.postgres_state_db import research_repository as postgres_research_repository
except ImportError:  # pragma: no cover - psycopg always installed in this repo
    postgres_research_repository = None


# ============================================================
# Fixtures
# ============================================================


def _case(trigger_source_type="radar", trigger_source_id="cand-1", created_at="2026-08-20T00:00:00+00:00", **overrides):
    defaults = dict(
        id=build_case_id(trigger_source_type, trigger_source_id, created_at),
        trigger_source_type=trigger_source_type, trigger_source_id=trigger_source_id,
        trigger_source_name="Example Corp", trigger_summary="Filed a material event.",
        title="Example research case", research_question="What is the supply-chain exposure?",
        status=ResearchCaseStatus.OPEN, created_at=created_at, version=1,
    )
    defaults.update(overrides)
    return ResearchCase(**defaults)


def _evidence_item(case_id="case-x", source_type="radar", source_id="cand-1", added_at="2026-08-20T00:00:00+00:00", **overrides):
    defaults = dict(
        id=build_evidence_id(case_id, source_type, source_id, added_at),
        case_id=case_id, source_type=source_type, source_id=source_id,
        source_url="https://example.com/filing", source_publisher_or_system="SEC EDGAR",
        source_date="2026-08-15", retrieved_at="2026-08-15T01:00:00+00:00",
        excerpt_original="The company disclosed a supply agreement with Acme Corp.",
        original_language="English", added_at=added_at,
    )
    defaults.update(overrides)
    return ResearchEvidenceItem(**defaults)


def _relationship_assertion(
    case_id="case-x", subject_entity="Example Corp", object_entity="Acme Corp",
    role=RelationshipRole.SUPPLIER, created_at="2026-08-20T00:00:00+00:00", **overrides,
):
    defaults = dict(
        id=build_relationship_assertion_id(case_id, subject_entity, object_entity, role, created_at),
        case_id=case_id, subject_entity=subject_entity, object_entity=object_entity, role=role,
        assertion_status=AssertionStatus.DIRECTLY_SUPPORTED, evidence_ids=("evidence-1",),
        confidence=AssertionConfidence.HIGH, created_at=created_at,
    )
    defaults.update(overrides)
    return RelationshipAssertion(**defaults)


def _dependency_assertion(
    case_id="case-x", affected_entity="Example Corp", bottleneck_type=BottleneckType.COMPONENT_SUPPLY,
    created_at="2026-08-20T00:00:00+00:00", **overrides,
):
    defaults = dict(
        id=build_dependency_assertion_id(case_id, affected_entity, bottleneck_type, created_at),
        case_id=case_id, affected_entity=affected_entity, bottleneck_type=bottleneck_type,
        supply_chain_layer="compute-hardware", transmission_path=("Acme Corp", "Example Corp"),
        assertion_status=AssertionStatus.HYPOTHESIS, evidence_ids=("evidence-1",),
        confidence=AssertionConfidence.MEDIUM, created_at=created_at,
        reasoning="Acme Corp is Example Corp's sole qualified supplier for this component.",
        limitations=("Not yet independently confirmed by a second source.",),
    )
    defaults.update(overrides)
    return DependencyAssertion(**defaults)


def _sqlite_conn():
    conn = sqlite_connection.connect_in_memory()
    sqlite_schema.migrate(conn)
    return conn


# ============================================================
# Part A — frozen immutability (proof 1)
# ============================================================


def test_all_four_models_are_frozen():
    case = _case()
    with pytest.raises(dataclasses.FrozenInstanceError):
        case.title = "TAMPERED"

    item = _evidence_item()
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.excerpt_original = "TAMPERED"

    rel = _relationship_assertion()
    with pytest.raises(dataclasses.FrozenInstanceError):
        rel.confidence = AssertionConfidence.LOW

    dep = _dependency_assertion()
    with pytest.raises(dataclasses.FrozenInstanceError):
        dep.bottleneck_type = BottleneckType.OTHER


# ============================================================
# Part B — ID factory determinism (proof 2)
# ============================================================


def test_case_id_deterministic_and_changes_with_input():
    a = build_case_id("radar", "cand-1", "2026-08-20T00:00:00+00:00")
    b = build_case_id("radar", "cand-1", "2026-08-20T00:00:00+00:00")
    assert a == b
    assert a.startswith("case-")
    assert build_case_id("radar", "cand-1", "2026-08-21T00:00:00+00:00") != a
    assert build_case_id("daily_news", "cand-1", "2026-08-20T00:00:00+00:00") != a


def test_evidence_id_deterministic_and_changes_with_input():
    a = build_evidence_id("case-x", "radar", "cand-1", "2026-08-20T00:00:00+00:00")
    b = build_evidence_id("case-x", "radar", "cand-1", "2026-08-20T00:00:00+00:00")
    assert a == b
    assert a.startswith("evidence-")
    assert build_evidence_id("case-y", "radar", "cand-1", "2026-08-20T00:00:00+00:00") != a


def test_relationship_assertion_id_deterministic_and_changes_with_role():
    a = build_relationship_assertion_id("case-x", "A", "B", RelationshipRole.SUPPLIER, "2026-08-20T00:00:00+00:00")
    b = build_relationship_assertion_id("case-x", "A", "B", RelationshipRole.SUPPLIER, "2026-08-20T00:00:00+00:00")
    assert a == b
    assert a.startswith("relationship-")
    assert build_relationship_assertion_id("case-x", "A", "B", RelationshipRole.CUSTOMER, "2026-08-20T00:00:00+00:00") != a


def test_dependency_assertion_id_deterministic_and_changes_with_bottleneck_type():
    a = build_dependency_assertion_id("case-x", "A", BottleneckType.COMPONENT_SUPPLY, "2026-08-20T00:00:00+00:00")
    b = build_dependency_assertion_id("case-x", "A", BottleneckType.COMPONENT_SUPPLY, "2026-08-20T00:00:00+00:00")
    assert a == b
    assert a.startswith("dependency-")
    assert build_dependency_assertion_id("case-x", "A", BottleneckType.POWER_GRID, "2026-08-20T00:00:00+00:00") != a


# ============================================================
# Part C — JSON round-trip, duplicates, backward-compat, bulk (proofs 3, 6, 8, 10, 11)
# ============================================================


def test_json_case_round_trip_preserves_every_field(tmp_path):
    case = _case()
    assert append_research_case(tmp_path, case) is True
    assert get_research_case(tmp_path, case.id) == case


def test_json_evidence_round_trip_preserves_every_field_including_translation(tmp_path):
    item = _evidence_item(excerpt_translated="Translated text.", translation_provider="DeepL")
    assert append_evidence_item(tmp_path, item) is True
    assert load_evidence_items(tmp_path)[item.id] == item


def test_json_evidence_round_trip_preserves_none_nullable_fields(tmp_path):
    item = _evidence_item()
    append_evidence_item(tmp_path, item)
    reloaded = load_evidence_items(tmp_path)[item.id]
    assert reloaded.excerpt_translated is None
    assert reloaded.translation_provider is None


def test_json_relationship_assertion_round_trip_preserves_every_field(tmp_path):
    rel = _relationship_assertion(evidence_ids=("evidence-1", "evidence-2"), limitations=("caveat one.", "caveat two."))
    append_assertion(tmp_path, rel)
    reloaded = load_assertions(tmp_path)[rel.id]
    assert reloaded == rel
    assert reloaded.evidence_ids == ("evidence-1", "evidence-2")
    assert reloaded.limitations == ("caveat one.", "caveat two.")


def test_json_dependency_assertion_round_trip_preserves_every_field(tmp_path):
    dep = _dependency_assertion()
    append_assertion(tmp_path, dep)
    reloaded = load_assertions(tmp_path)[dep.id]
    assert reloaded == dep
    assert reloaded.transmission_path == ("Acme Corp", "Example Corp")


def test_json_dependency_assertion_preserves_none_transmission_path_and_layer(tmp_path):
    dep = _dependency_assertion(transmission_path=None, supply_chain_layer=None)
    append_assertion(tmp_path, dep)
    reloaded = load_assertions(tmp_path)[dep.id]
    assert reloaded.transmission_path is None
    assert reloaded.supply_chain_layer is None


def test_json_duplicate_case_id_never_overwrites(tmp_path):
    case = _case()
    tampered = dataclasses.replace(case, title="TAMPERED")
    assert append_research_case(tmp_path, case) is True
    assert append_research_case(tmp_path, tampered) is False
    assert get_research_case(tmp_path, case.id) == case


def test_json_duplicate_evidence_id_never_overwrites(tmp_path):
    item = _evidence_item()
    tampered = dataclasses.replace(item, excerpt_original="TAMPERED")
    assert append_evidence_item(tmp_path, item) is True
    assert append_evidence_item(tmp_path, tampered) is False
    assert load_evidence_items(tmp_path)[item.id] == item


def test_json_duplicate_assertion_id_never_overwrites(tmp_path):
    rel = _relationship_assertion()
    tampered = dataclasses.replace(rel, confidence=AssertionConfidence.LOW)
    assert append_assertion(tmp_path, rel) is True
    assert append_assertion(tmp_path, tampered) is False
    assert load_assertions(tmp_path)[rel.id] == rel


def test_json_missing_files_load_empty(tmp_path):
    assert load_research_cases(tmp_path) == {}
    assert load_evidence_items(tmp_path) == {}
    assert load_assertions(tmp_path) == {}
    assert get_research_case(tmp_path, "case-does-not-exist") is None


def test_json_bulk_empty_input_never_loads(tmp_path, monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("must not load when case_ids is empty")

    monkeypatch.setattr(research_store, "load_evidence_items", _boom)
    assert research_store.evidence_items_for_case_ids(tmp_path, []) == {}
    monkeypatch.setattr(research_store, "load_assertions", _boom)
    assert research_store.assertions_for_case_ids(tmp_path, []) == {}


def test_json_bulk_evidence_makes_exactly_one_load_for_non_empty_request(tmp_path, monkeypatch):
    item = _evidence_item()
    research_store.append_evidence_item(tmp_path, item)
    calls = []
    real_load = research_store.load_evidence_items

    def _counting(*args, **kwargs):
        calls.append(1)
        return real_load(*args, **kwargs)

    monkeypatch.setattr(research_store, "load_evidence_items", _counting)
    research_store.evidence_items_for_case_ids(tmp_path, [item.case_id, "case-other"])
    assert len(calls) == 1


def test_json_bulk_evidence_returns_correct_items_deterministically_ordered_with_partial_matches(tmp_path):
    case_a_older = _evidence_item(case_id="case-a", source_id="s1", added_at="2026-08-01T00:00:00+00:00")
    case_a_newer = _evidence_item(case_id="case-a", source_id="s2", added_at="2026-08-05T00:00:00+00:00")
    case_b_item = _evidence_item(case_id="case-b", source_id="s3", added_at="2026-08-03T00:00:00+00:00")
    for item in (case_a_older, case_a_newer, case_b_item):
        research_store.append_evidence_item(tmp_path, item)

    result = research_store.evidence_items_for_case_ids(tmp_path, ["case-a", "case-b", "case-missing"])
    assert set(result.keys()) == {"case-a", "case-b"}
    assert [i.id for i in result["case-a"]] == [case_a_older.id, case_a_newer.id]
    assert result["case-b"] == (case_b_item,)


def test_json_bulk_assertions_returns_correct_items_deterministically_ordered_with_partial_matches(tmp_path):
    case_a_older = _relationship_assertion(case_id="case-a", created_at="2026-08-01T00:00:00+00:00")
    case_a_newer = _dependency_assertion(case_id="case-a", created_at="2026-08-05T00:00:00+00:00")
    case_b_item = _relationship_assertion(case_id="case-b", subject_entity="X", object_entity="Y", created_at="2026-08-03T00:00:00+00:00")
    for a in (case_a_older, case_a_newer, case_b_item):
        research_store.append_assertion(tmp_path, a)

    result = research_store.assertions_for_case_ids(tmp_path, ["case-a", "case-b", "case-missing"])
    assert set(result.keys()) == {"case-a", "case-b"}
    assert [a.id for a in result["case-a"]] == [case_a_older.id, case_a_newer.id]
    assert result["case-b"] == (case_b_item,)


# ============================================================
# Part D — SQLite round-trip, duplicates, bulk, migration safety (proofs 4, 6, 9, 10, 11)
# ============================================================


def test_sqlite_case_round_trip():
    conn = _sqlite_conn()
    case = _case()
    assert sqlite_research_repository.insert_research_case(conn, case) is True
    assert sqlite_research_repository.get_research_case(conn, case.id) == case


def test_sqlite_evidence_round_trip_including_translation():
    conn = _sqlite_conn()
    item = _evidence_item(excerpt_translated="Translated text.", translation_provider="DeepL")
    assert sqlite_research_repository.insert_evidence_item(conn, item) is True
    result = sqlite_research_repository.get_evidence_items_for_case_ids(conn, [item.case_id])
    assert result[item.case_id] == (item,)


def test_sqlite_relationship_assertion_round_trip():
    conn = _sqlite_conn()
    rel = _relationship_assertion(evidence_ids=("evidence-1", "evidence-2"), limitations=("caveat one.", "caveat two."))
    assert sqlite_research_repository.insert_assertion(conn, rel) is True
    result = sqlite_research_repository.get_assertions_for_case_ids(conn, [rel.case_id])
    assert result[rel.case_id] == (rel,)


def test_sqlite_dependency_assertion_round_trip_including_none_fields():
    conn = _sqlite_conn()
    dep = _dependency_assertion(transmission_path=None, supply_chain_layer=None)
    assert sqlite_research_repository.insert_assertion(conn, dep) is True
    result = sqlite_research_repository.get_assertions_for_case_ids(conn, [dep.case_id])
    assert result[dep.case_id] == (dep,)
    assert result[dep.case_id][0].transmission_path is None


def test_sqlite_duplicate_case_id_fails_safely_never_overwrites():
    conn = _sqlite_conn()
    case = _case()
    tampered = dataclasses.replace(case, title="TAMPERED")
    assert sqlite_research_repository.insert_research_case(conn, case) is True
    assert sqlite_research_repository.insert_research_case(conn, tampered) is False
    assert sqlite_research_repository.get_research_case(conn, case.id) == case


def test_sqlite_duplicate_evidence_id_fails_safely_never_overwrites():
    conn = _sqlite_conn()
    item = _evidence_item()
    tampered = dataclasses.replace(item, excerpt_original="TAMPERED")
    assert sqlite_research_repository.insert_evidence_item(conn, item) is True
    assert sqlite_research_repository.insert_evidence_item(conn, tampered) is False
    result = sqlite_research_repository.get_evidence_items_for_case_ids(conn, [item.case_id])
    assert result[item.case_id] == (item,)


def test_sqlite_duplicate_assertion_id_fails_safely_never_overwrites():
    conn = _sqlite_conn()
    rel = _relationship_assertion()
    tampered = dataclasses.replace(rel, confidence=AssertionConfidence.LOW)
    assert sqlite_research_repository.insert_assertion(conn, rel) is True
    assert sqlite_research_repository.insert_assertion(conn, tampered) is False
    result = sqlite_research_repository.get_assertions_for_case_ids(conn, [rel.case_id])
    assert result[rel.case_id] == (rel,)


def test_sqlite_bulk_evidence_empty_input_executes_no_sql():
    conn = MagicMock()
    result = sqlite_research_repository.get_evidence_items_for_case_ids(conn, [])
    assert result == {}
    conn.execute.assert_not_called()


def test_sqlite_bulk_assertions_empty_input_executes_no_sql():
    conn = MagicMock()
    result = sqlite_research_repository.get_assertions_for_case_ids(conn, [])
    assert result == {}
    conn.execute.assert_not_called()


def test_sqlite_bulk_evidence_query_construction_is_one_parameterized_call():
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = []
    sqlite_research_repository.get_evidence_items_for_case_ids(conn, ["case-a", "case-b", "case-c"])
    assert conn.execute.call_count == 1
    sql_text, params = conn.execute.call_args[0]
    assert sql_text.count("?") == 3
    assert params == ("case-a", "case-b", "case-c")
    assert "case-a" not in sql_text and "case-b" not in sql_text and "case-c" not in sql_text


def test_sqlite_migration_v5_to_v6_does_not_touch_existing_candidate_or_comparison_rows():
    from src.data_access.comparison_store import build_comparison_record
    from src.data_access.state_db import candidate_repository as sqlite_candidate_repository
    from src.data_access.state_db import comparison_repository as sqlite_comparison_repository
    from src.logic.prior_disclosure_comparison import ComparisonResult, ComparisonStatus
    from src.models.models import CandidateSignal, CandidateStatus, FilingEvent

    conn = sqlite_connection.connect_in_memory()
    # Manually replay only the v1..v5 migrations, simulating a real
    # pre-Phase-4 database — never assume a fresh connect_in_memory() +
    # full migrate() proves anything about an UPGRADE path specifically.
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (0)")
    for target_version, statements in sqlite_schema._MIGRATIONS:
        if target_version > 5:
            continue
        for statement in statements:
            conn.execute(statement)
        conn.execute("UPDATE schema_version SET version = ?", (target_version,))
    conn.commit()
    assert sqlite_schema.get_schema_version(conn) == 5

    filing = FilingEvent(
        rcept_no="acc-1", corp_code="0000320193", corp_name="Apple Inc.", stock_code="AAPL",
        report_nm="8-K", rcept_dt="2026-08-01", flr_nm="Apple Inc.", source_name="SEC EDGAR",
    )
    candidate = CandidateSignal(
        id="edgar-cand-1", filing=filing, matched_rules=["financing_or_debt:8-K item 2.03"],
        confidence="Moderate", status=CandidateStatus.CANDIDATE_DETECTED,
    )
    sqlite_candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [candidate])

    comparison_result = ComparisonResult(
        comparison_status=ComparisonStatus.NOT_AVAILABLE.value, comparison_basis="matched_rules_set_diff:v1",
        computed_at="2026-08-20T00:00:00+00:00",
    )
    comparison_record = build_comparison_record(
        comparison_result, current_candidate_id="edgar-cand-1", current_source_name="SEC EDGAR",
        current_corp_code="0000320193", current_document_id="acc-1",
    )
    sqlite_comparison_repository.insert_comparison_record(conn, comparison_record)

    before_candidate = sqlite_candidate_repository.get_candidate(conn, "edgar-cand-1")
    before_comparison = sqlite_comparison_repository.get_comparison_record(conn, comparison_record.id)

    result_version = sqlite_schema.migrate(conn)
    # Tracks the current latest schema version (9, after the Citrini-
    # style research-workspace vertical slice's V9 addition) — update
    # alongside any future migration bump; the point of this test is
    # migration safety, not this exact number.
    assert result_version == 9

    after_candidate = sqlite_candidate_repository.get_candidate(conn, "edgar-cand-1")
    after_comparison = sqlite_comparison_repository.get_comparison_record(conn, comparison_record.id)
    assert before_candidate == after_candidate
    assert before_comparison == after_comparison

    # The new tables exist and are usable, and start empty.
    assert sqlite_research_repository.get_research_case(conn, "case-does-not-exist") is None
    assert sqlite_research_repository.get_evidence_items_for_case_ids(conn, ["edgar-cand-1"]) == {}


# ============================================================
# Part E — Postgres round-trip, duplicates, rollback, bulk (proofs 5, 6, 7, 9, 10, 11)
# ============================================================


def test_postgres_case_round_trip(pg_conn):
    case = _case(trigger_source_id="cand-pg-1")
    assert postgres_research_repository.insert_research_case(pg_conn, case) is True
    assert postgres_research_repository.get_research_case(pg_conn, case.id) == case


def test_postgres_evidence_round_trip_including_translation(pg_conn):
    item = _evidence_item(case_id="case-pg-1", excerpt_translated="Translated text.", translation_provider="DeepL")
    assert postgres_research_repository.insert_evidence_item(pg_conn, item) is True
    result = postgres_research_repository.get_evidence_items_for_case_ids(pg_conn, [item.case_id])
    assert result[item.case_id] == (item,)


def test_postgres_relationship_assertion_round_trip(pg_conn):
    rel = _relationship_assertion(case_id="case-pg-2", evidence_ids=("evidence-1", "evidence-2"))
    assert postgres_research_repository.insert_assertion(pg_conn, rel) is True
    result = postgres_research_repository.get_assertions_for_case_ids(pg_conn, [rel.case_id])
    assert result[rel.case_id] == (rel,)


def test_postgres_dependency_assertion_round_trip_including_none_fields(pg_conn):
    dep = _dependency_assertion(case_id="case-pg-3", transmission_path=None, supply_chain_layer=None)
    assert postgres_research_repository.insert_assertion(pg_conn, dep) is True
    result = postgres_research_repository.get_assertions_for_case_ids(pg_conn, [dep.case_id])
    assert result[dep.case_id][0].transmission_path is None


def test_postgres_duplicate_case_id_fails_safely_and_connection_stays_usable(pg_conn):
    case = _case(trigger_source_id="cand-pg-4")
    tampered = dataclasses.replace(case, title="TAMPERED")
    assert postgres_research_repository.insert_research_case(pg_conn, case) is True
    assert postgres_research_repository.insert_research_case(pg_conn, tampered) is False
    assert postgres_research_repository.get_research_case(pg_conn, case.id) == case

    # The connection must remain usable after the rejected duplicate
    # insert — Postgres aborts a transaction on constraint violation
    # until an explicit rollback (see module docstring).
    other_case = _case(trigger_source_id="cand-pg-5")
    assert postgres_research_repository.insert_research_case(pg_conn, other_case) is True


def test_postgres_duplicate_evidence_and_assertion_fail_safely(pg_conn):
    item = _evidence_item(case_id="case-pg-6")
    tampered_item = dataclasses.replace(item, excerpt_original="TAMPERED")
    assert postgres_research_repository.insert_evidence_item(pg_conn, item) is True
    assert postgres_research_repository.insert_evidence_item(pg_conn, tampered_item) is False

    rel = _relationship_assertion(case_id="case-pg-6")
    tampered_rel = dataclasses.replace(rel, confidence=AssertionConfidence.LOW)
    assert postgres_research_repository.insert_assertion(pg_conn, rel) is True
    assert postgres_research_repository.insert_assertion(pg_conn, tampered_rel) is False

    result = postgres_research_repository.get_evidence_items_for_case_ids(pg_conn, ["case-pg-6"])
    assert result["case-pg-6"] == (item,)


def test_postgres_bulk_empty_input_executes_no_query():
    if postgres_research_repository is None:
        pytest.skip("psycopg not available")
    conn = MagicMock()
    assert postgres_research_repository.get_evidence_items_for_case_ids(conn, []) == {}
    assert postgres_research_repository.get_assertions_for_case_ids(conn, []) == {}
    conn.execute.assert_not_called()


def test_postgres_bulk_query_construction_uses_any():
    if postgres_research_repository is None:
        pytest.skip("psycopg not available")
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = []
    postgres_research_repository.get_evidence_items_for_case_ids(conn, ["case-a", "case-b"])
    assert conn.execute.call_count == 1
    sql_text, params = conn.execute.call_args[0]
    assert "= ANY(%s)" in sql_text
    assert params == (["case-a", "case-b"],)


def test_postgres_migration_v5_to_v6_does_not_touch_existing_candidate_row(pg_isolated_connection):
    from src.data_access.postgres_state_db import candidate_repository as postgres_candidate_repository
    from src.data_access.postgres_state_db import schema as postgres_schema
    from src.models.models import CandidateSignal, CandidateStatus, FilingEvent

    conn = pg_isolated_connection
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) SELECT 0 WHERE NOT EXISTS (SELECT 1 FROM schema_version)")
    conn.commit()
    for target_version, statements in postgres_schema._MIGRATIONS:
        if target_version > 5:
            continue
        for statement in statements:
            conn.execute(statement)
        conn.execute("UPDATE schema_version SET version = %s", (target_version,))
    conn.commit()
    assert postgres_schema.get_schema_version(conn) == 5

    filing = FilingEvent(
        rcept_no="acc-pg-1", corp_code="0000320194", corp_name="Example Corp", stock_code="EX",
        report_nm="8-K", rcept_dt="2026-08-01", flr_nm="Example Corp", source_name="SEC EDGAR",
    )
    candidate = CandidateSignal(
        id="edgar-cand-pg-mig", filing=filing, matched_rules=["financing_or_debt:8-K item 2.03"],
        confidence="Moderate", status=CandidateStatus.CANDIDATE_DETECTED,
    )
    postgres_candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [candidate])
    before = postgres_candidate_repository.get_candidate(conn, "edgar-cand-pg-mig")

    result_version = postgres_schema.migrate(conn)
    # See the SQLite counterpart's own comment — tracks the current
    # latest schema version.
    assert result_version == 9

    after = postgres_candidate_repository.get_candidate(conn, "edgar-cand-pg-mig")
    assert before == after
    assert postgres_research_repository.get_research_case(conn, "case-does-not-exist") is None


# ============================================================
# Part F — no update/upsert/replace/delete path anywhere (proof 12)
# ============================================================


def test_no_update_upsert_replace_or_delete_functions_anywhere_in_research_persistence():
    modules = [research_store, sqlite_research_repository]
    if postgres_research_repository is not None:
        modules.append(postgres_research_repository)
    forbidden_substrings = ("update", "delete", "replace", "upsert", "overwrite", "merge")
    offenders = []
    for module in modules:
        exported = {name for name in dir(module) if not name.startswith("_")}
        offenders.extend(f"{module.__name__}.{name}" for name in exported if any(f in name.lower() for f in forbidden_substrings))
    assert not offenders, offenders


# ============================================================
# Part G — import isolation (proof 13)
# ============================================================


def test_new_modules_do_not_import_forbidden_types_or_never_read_wall_clock():
    repo_root = Path(__file__).parent.parent
    files = [
        "src/models/research_case.py",
        "src/data_access/research_store.py",
        "src/data_access/state_db/research_repository.py",
        "src/data_access/postgres_state_db/research_repository.py",
    ]
    forbidden_modules = (
        "src.models.models", "src.models.daily_news_models", "src.models.issuer", "src.models.theme_registry",
        "src.data_access.daily_news", "src.data_access.edgar", "src.data_access.dart", "src.data_access.edinet",
        "src.config.tracked_companies", "src.config.issuer_registry",
        "src.ui", "streamlit",
        "anthropic", "openai", "langchain", "chromadb", "pinecone", "weaviate",
    )
    offenders = []
    for rel_path in files:
        source = (repo_root / rel_path).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=rel_path)
        # AST-based, not substring-based, so a docstring merely *mentioning*
        # datetime.now() (to document that it's never called) can't
        # false-positive this check the way a naive substring scan would.
        wall_clock_calls = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and (
                (n.func.attr == "now" and isinstance(n.func.value, ast.Name) and n.func.value.id == "datetime")
                or (n.func.attr == "today" and isinstance(n.func.value, ast.Name) and n.func.value.id == "date")
                or (n.func.attr == "time" and isinstance(n.func.value, ast.Name) and n.func.value.id == "time")
            )
        ]
        assert not wall_clock_calls, f"{rel_path} calls wall-clock time"
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                if any(module == forbidden or module.startswith(forbidden + ".") for forbidden in forbidden_modules):
                    offenders.append(f"{rel_path}: imports {module!r}")
    assert not offenders, offenders


def test_model_module_imports_only_stdlib():
    repo_root = Path(__file__).parent.parent
    path = repo_root / "src" / "models" / "research_case.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    allowed_stdlib = {"__future__", "dataclasses", "enum"}
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders.extend(a.name for a in node.names if a.name.split(".")[0] not in allowed_stdlib)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] not in allowed_stdlib:
                offenders.append(node.module)
    assert not offenders, offenders


# ============================================================
# Part H — existing models/tables untouched (proof 14)
# ============================================================


def test_existing_models_module_is_not_modified():
    repo_root = Path(__file__).parent.parent
    result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    assert "src/models/models.py" not in changed
    assert "src/models/daily_news_models.py" not in changed
    assert "src/logic/prior_disclosure_comparison.py" not in changed


# ============================================================
# Part I — scope guards (proof 15)
# ============================================================


def test_no_new_dependency_added_to_requirements():
    repo_root = Path(__file__).parent.parent
    result = subprocess.run(["git", "diff", "HEAD", "--", "requirements.txt"], cwd=repo_root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    assert result.stdout.strip() == "", f"requirements.txt was modified: {result.stdout}"


def test_scope_guard_only_approved_files_changed():
    """Runs against `git diff HEAD` — only meaningful in a real checkout
    with this step's changes present; spuriously fires while ANY other
    legitimate uncommitted change is present and resolves once
    committed — same documented convention as this repo's other
    phase-scoped scope guards."""
    repo_root = Path(__file__).parent.parent
    result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    allowed = {
        "src/models/research_case.py",
        "src/data_access/research_store.py",
        "src/data_access/state_db/schema.py",
        "src/data_access/state_db/research_repository.py",
        "src/data_access/postgres_state_db/schema.py",
        "src/data_access/postgres_state_db/research_repository.py",
        "tests/test_research_case_persistence.py",
    }
    assert changed <= allowed, changed - allowed


def test_no_ui_source_pipeline_worker_or_deployment_files_touched():
    repo_root = Path(__file__).parent.parent
    result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    forbidden_prefixes = ("src/ui/", "src/data_access/daily_news/", "src/data_access/edgar/", "src/data_access/dart/", "src/data_access/edinet/", "scripts/")
    forbidden_paths = {
        "render.yaml", "design/RADAR_WORKER_DEPLOYMENT.md",
        "src/data_access/translation/translation_service.py", "src/data_access/translation/deepl_provider.py",
        "src/models/models.py", "src/models/daily_news_models.py", "src/models/issuer.py",
        "src/logic/prior_disclosure_comparison.py",
        "src/config/tracked_companies.py", "src/config/issuer_registry.py", "src/config/ontology.py",
        "src/data_access/comparison_store.py", "src/data_access/state_db/comparison_repository.py",
        "src/data_access/postgres_state_db/comparison_repository.py",
        "src/data_access/backend_factory.py",
        "requirements.txt",
    }
    hit = {c for c in changed if c in forbidden_paths or any(c.startswith(p) for p in forbidden_prefixes)}
    assert not hit, hit
