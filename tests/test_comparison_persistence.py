"""Radar evidence-packet foundation, Phase 3, Step 2 (design/DECISIONS.md)
— append-only persistence for ComparisonRecord across JSON, SQLite, and
Postgres backends. Every fixture is synthetic and local; no source is
fetched, no scan runs, no pipeline/UI code is invoked anywhere in this
file. Postgres tests use the shared, fail-soft local-only fixtures from
tests/_postgres_test_support.py and skip cleanly when no local
disposable Postgres instance is available."""
from __future__ import annotations

import ast
import copy
import dataclasses
import subprocess
from pathlib import Path

import pytest

from src.data_access import comparison_store
from src.data_access.comparison_store import (
    ComparisonRecord,
    append_comparison_record,
    build_comparison_record,
    build_comparison_record_id,
    latest_comparison_record_for_candidate,
    load_comparison_records,
)
from src.data_access.state_db import comparison_repository as sqlite_comparison_repository
from src.data_access.state_db import connection as sqlite_connection
from src.data_access.state_db import schema as sqlite_schema
from src.logic.prior_disclosure_comparison import ComparisonResult, ComparisonStatus

from tests._postgres_test_support import pg_conn, pg_isolated_connection  # noqa: F401

try:
    from src.data_access.postgres_state_db import comparison_repository as postgres_comparison_repository
except ImportError:  # pragma: no cover - psycopg always installed in this repo
    postgres_comparison_repository = None


def _result(**overrides) -> ComparisonResult:
    defaults = dict(
        comparison_status=ComparisonStatus.CHANGE_DETECTED.value,
        comparison_basis="matched_rules_set_diff:v1",
        computed_at="2026-08-20T00:00:00+00:00",
        prior_document_id="acc-1",
        prior_filed_at=None,
        added_categories=("governance_or_management_change",),
        removed_categories=(),
        prior_excerpt="Prior excerpt text.",
        current_excerpt="Current excerpt text.",
        limitations=("Comparable reporting period is not available in current metadata.",),
    )
    defaults.update(overrides)
    return ComparisonResult(**defaults)


def _record(**overrides) -> ComparisonRecord:
    result = overrides.pop("result", _result())
    record = build_comparison_record(
        result,
        current_candidate_id=overrides.pop("current_candidate_id", "cur-1"),
        current_source_name=overrides.pop("current_source_name", "SEC EDGAR"),
        current_corp_code=overrides.pop("current_corp_code", "0000320193"),
        current_document_id=overrides.pop("current_document_id", "acc-2"),
        prior_candidate_id=overrides.pop("prior_candidate_id", "prior-1"),
    )
    if overrides:
        record = dataclasses.replace(record, **overrides)
    return record


# ============================================================
# Part A — stable ID strategy
# ============================================================


def test_stable_id_is_deterministic_from_inputs():
    a = build_comparison_record_id("cur-1", "2026-08-20T00:00:00+00:00", "matched_rules_set_diff:v1")
    b = build_comparison_record_id("cur-1", "2026-08-20T00:00:00+00:00", "matched_rules_set_diff:v1")
    assert a == b


def test_stable_id_differs_for_a_later_computed_at():
    a = build_comparison_record_id("cur-1", "2026-08-20T00:00:00+00:00", "matched_rules_set_diff:v1")
    b = build_comparison_record_id("cur-1", "2026-08-21T00:00:00+00:00", "matched_rules_set_diff:v1")
    assert a != b


def test_build_comparison_record_copies_result_fields_verbatim():
    result = _result(added_categories=("b", "a"), removed_categories=("z",), limitations=("note 1.", "note 2."))
    record = build_comparison_record(
        result, current_candidate_id="cur-1", current_source_name="SEC EDGAR",
        current_corp_code="0000320193", current_document_id="acc-2", prior_candidate_id="prior-1",
    )
    assert record.comparison_status == result.comparison_status
    assert record.comparison_basis == result.comparison_basis
    assert record.computed_at == result.computed_at
    assert record.prior_document_id == result.prior_document_id
    assert record.prior_filed_at == result.prior_filed_at
    assert record.added_categories == ("b", "a")  # order preserved exactly as computed, never re-sorted here
    assert record.removed_categories == ("z",)
    assert record.prior_excerpt == result.prior_excerpt
    assert record.current_excerpt == result.current_excerpt
    assert record.limitations == ("note 1.", "note 2.")
    assert record.id == build_comparison_record_id("cur-1", result.computed_at, result.comparison_basis)


# ============================================================
# Part B — JSON store (proofs 1, 4, 6, 7, 8, 9, 10, 11, 12, 13)
# ============================================================


def test_json_store_appends_and_round_trips_every_field(tmp_path):
    record = _record()
    appended = append_comparison_record(tmp_path, record)
    assert appended is True

    reloaded = load_comparison_records(tmp_path)[record.id]
    assert reloaded == record


def test_json_store_missing_file_loads_as_empty(tmp_path):
    assert load_comparison_records(tmp_path) == {}
    assert latest_comparison_record_for_candidate(tmp_path, "cur-1") is None


def test_json_store_optional_null_fields_reconstruct_safely(tmp_path):
    result = _result(prior_document_id=None, prior_filed_at=None, prior_excerpt=None, current_excerpt=None, limitations=())
    record = build_comparison_record(
        result, current_candidate_id="cur-1", current_source_name="EDINET",
        current_corp_code="E02778", current_document_id="S100BBBB", prior_candidate_id=None,
    )
    append_comparison_record(tmp_path, record)
    reloaded = load_comparison_records(tmp_path)[record.id]
    assert reloaded.prior_candidate_id is None
    assert reloaded.prior_document_id is None
    assert reloaded.prior_filed_at is None
    assert reloaded.prior_excerpt is None
    assert reloaded.current_excerpt is None
    assert reloaded.limitations == ()


def test_json_store_preserves_tuple_ordering_deterministically(tmp_path):
    result = _result(added_categories=("zeta", "alpha", "mu"), removed_categories=("gamma", "beta"), limitations=("first.", "second.", "third."))
    record = build_comparison_record(
        result, current_candidate_id="cur-1", current_source_name="SEC EDGAR",
        current_corp_code="0000320193", current_document_id="acc-2",
    )
    append_comparison_record(tmp_path, record)
    reloaded = load_comparison_records(tmp_path)[record.id]
    assert reloaded.added_categories == ("zeta", "alpha", "mu")
    assert reloaded.removed_categories == ("gamma", "beta")
    assert reloaded.limitations == ("first.", "second.", "third.")


def test_json_store_two_records_for_same_candidate_both_persist_earlier_unchanged(tmp_path):
    first_result = _result(computed_at="2026-08-20T00:00:00+00:00", added_categories=("financing_or_debt",))
    second_result = _result(computed_at="2026-08-25T00:00:00+00:00", added_categories=("governance_or_management_change",))
    first = build_comparison_record(first_result, current_candidate_id="cur-1", current_source_name="SEC EDGAR", current_corp_code="0000320193", current_document_id="acc-2")
    second = build_comparison_record(second_result, current_candidate_id="cur-1", current_source_name="SEC EDGAR", current_corp_code="0000320193", current_document_id="acc-2")
    assert first.id != second.id

    append_comparison_record(tmp_path, first)
    append_comparison_record(tmp_path, second)

    records = load_comparison_records(tmp_path)
    assert len(records) == 2
    assert records[first.id] == first  # untouched by the second insert
    assert records[second.id] == second


def test_json_store_latest_record_returns_newest_computed_at(tmp_path):
    older = build_comparison_record(_result(computed_at="2026-08-20T00:00:00+00:00"), current_candidate_id="cur-1", current_source_name="SEC EDGAR", current_corp_code="0000320193", current_document_id="acc-2")
    newer = build_comparison_record(_result(computed_at="2026-08-25T00:00:00+00:00"), current_candidate_id="cur-1", current_source_name="SEC EDGAR", current_corp_code="0000320193", current_document_id="acc-2")
    append_comparison_record(tmp_path, older)
    append_comparison_record(tmp_path, newer)
    latest = latest_comparison_record_for_candidate(tmp_path, "cur-1")
    assert latest.id == newer.id
    assert latest.computed_at == "2026-08-25T00:00:00+00:00"


def test_json_store_latest_record_breaks_exact_computed_at_tie_by_greatest_id(tmp_path):
    # Phase 3, Step 3A: two records sharing the exact same computed_at
    # (a different comparison_basis gives them different stable ids) —
    # the tie must resolve to the greatest id, deterministically, never
    # file/dict iteration order.
    tied_at = "2026-08-20T00:00:00+00:00"
    record_a = build_comparison_record(_result(computed_at=tied_at, comparison_basis="matched_rules_set_diff:v1"), current_candidate_id="cur-tie", current_source_name="SEC EDGAR", current_corp_code="0000320193", current_document_id="acc-2")
    record_b = build_comparison_record(_result(computed_at=tied_at, comparison_basis="matched_rules_set_diff:v2"), current_candidate_id="cur-tie", current_source_name="SEC EDGAR", current_corp_code="0000320193", current_document_id="acc-2")
    assert record_a.id != record_b.id

    append_comparison_record(tmp_path, record_a)
    append_comparison_record(tmp_path, record_b)
    latest = latest_comparison_record_for_candidate(tmp_path, "cur-tie")
    assert latest.id == max(record_a.id, record_b.id)

    # Order of insertion must not affect the outcome.
    tmp_path_2 = tmp_path / "reversed"
    tmp_path_2.mkdir()
    append_comparison_record(tmp_path_2, record_b)
    append_comparison_record(tmp_path_2, record_a)
    latest_reversed = latest_comparison_record_for_candidate(tmp_path_2, "cur-tie")
    assert latest_reversed.id == max(record_a.id, record_b.id)


def test_json_store_duplicate_stable_id_never_overwrites(tmp_path):
    record = _record()
    tampered = dataclasses.replace(record, comparison_status=ComparisonStatus.NO_MATERIAL_CHANGE.value, added_categories=())

    assert append_comparison_record(tmp_path, record) is True
    assert append_comparison_record(tmp_path, tampered) is False  # same id, different payload — rejected

    reloaded = load_comparison_records(tmp_path)[record.id]
    assert reloaded == record  # the original, not the tampered attempt


def test_json_store_module_has_no_update_or_delete_function():
    exported = {name for name in dir(comparison_store) if not name.startswith("_")}
    forbidden_substrings = ("update", "delete", "replace", "upsert", "overwrite", "merge")
    offenders = [name for name in exported if any(f in name.lower() for f in forbidden_substrings)]
    assert not offenders, offenders


def test_json_store_does_not_mutate_inputs(tmp_path):
    result = _result()
    result_snapshot = copy.deepcopy(result)
    record = build_comparison_record(result, current_candidate_id="cur-1", current_source_name="SEC EDGAR", current_corp_code="0000320193", current_document_id="acc-2")
    record_snapshot = copy.deepcopy(record)

    append_comparison_record(tmp_path, record)

    assert result == result_snapshot
    assert record == record_snapshot


# ============================================================
# Part C — SQLite (proofs 2, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14)
# ============================================================


def _sqlite_conn():
    conn = sqlite_connection.connect_in_memory()
    sqlite_schema.migrate(conn)
    return conn


def test_sqlite_inserts_and_round_trips_every_field():
    conn = _sqlite_conn()
    record = _record()
    inserted = sqlite_comparison_repository.insert_comparison_record(conn, record)
    assert inserted is True

    reloaded = sqlite_comparison_repository.get_comparison_record(conn, record.id)
    assert reloaded == record


def test_sqlite_migration_adds_table_without_touching_existing_candidate_rows():
    conn = sqlite_connection.connect_in_memory()
    sqlite_schema.migrate(conn)  # brand-new database, migrates straight to CURRENT_SCHEMA_VERSION

    from src.data_access.state_db import candidate_repository
    from src.models.models import CandidateSignal, CandidateStatus, FilingEvent

    filing = FilingEvent(
        rcept_no="0001193125-26-354029", corp_code="0000002488", corp_name="Advanced Micro Devices",
        stock_code="AMD", report_nm="8-K", rcept_dt="2026-08-17", flr_nm="Advanced Micro Devices",
        source_name="SEC EDGAR",
    )
    candidate = CandidateSignal(id="edgar-cand-1", filing=filing, matched_rules=["financing_or_debt:8-K item 2.03"], confidence="Moderate", status=CandidateStatus.CANDIDATE_DETECTED)
    candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [candidate])

    before = candidate_repository.get_candidate(conn, "edgar-cand-1")

    record = _record()
    sqlite_comparison_repository.insert_comparison_record(conn, record)

    after = candidate_repository.get_candidate(conn, "edgar-cand-1")
    assert before == after  # untouched by the comparison-table insert
    assert sqlite_schema.get_schema_version(conn) == sqlite_schema.CURRENT_SCHEMA_VERSION


def test_sqlite_optional_null_fields_reconstruct_safely():
    conn = _sqlite_conn()
    result = _result(prior_document_id=None, prior_filed_at=None, prior_excerpt=None, current_excerpt=None, limitations=())
    record = build_comparison_record(result, current_candidate_id="cur-1", current_source_name="EDINET", current_corp_code="E02778", current_document_id="S100BBBB", prior_candidate_id=None)
    sqlite_comparison_repository.insert_comparison_record(conn, record)
    reloaded = sqlite_comparison_repository.get_comparison_record(conn, record.id)
    assert reloaded.prior_candidate_id is None
    assert reloaded.prior_document_id is None
    assert reloaded.prior_filed_at is None
    assert reloaded.prior_excerpt is None
    assert reloaded.current_excerpt is None
    assert reloaded.limitations == ()


def test_sqlite_preserves_tuple_ordering_deterministically():
    conn = _sqlite_conn()
    result = _result(added_categories=("zeta", "alpha", "mu"), removed_categories=("gamma", "beta"))
    record = build_comparison_record(result, current_candidate_id="cur-1", current_source_name="SEC EDGAR", current_corp_code="0000320193", current_document_id="acc-2")
    sqlite_comparison_repository.insert_comparison_record(conn, record)
    reloaded = sqlite_comparison_repository.get_comparison_record(conn, record.id)
    assert reloaded.added_categories == ("zeta", "alpha", "mu")
    assert reloaded.removed_categories == ("gamma", "beta")


def test_sqlite_two_records_for_same_candidate_both_persist_earlier_unchanged():
    conn = _sqlite_conn()
    older = build_comparison_record(_result(computed_at="2026-08-20T00:00:00+00:00"), current_candidate_id="cur-1", current_source_name="SEC EDGAR", current_corp_code="0000320193", current_document_id="acc-2")
    newer = build_comparison_record(_result(computed_at="2026-08-25T00:00:00+00:00"), current_candidate_id="cur-1", current_source_name="SEC EDGAR", current_corp_code="0000320193", current_document_id="acc-2")
    sqlite_comparison_repository.insert_comparison_record(conn, older)
    sqlite_comparison_repository.insert_comparison_record(conn, newer)

    all_records = sqlite_comparison_repository.load_comparison_records_for_candidate(conn, "cur-1")
    assert len(all_records) == 2
    reloaded_older = sqlite_comparison_repository.get_comparison_record(conn, older.id)
    assert reloaded_older == older


def test_sqlite_latest_record_returns_newest_computed_at():
    conn = _sqlite_conn()
    older = build_comparison_record(_result(computed_at="2026-08-20T00:00:00+00:00"), current_candidate_id="cur-1", current_source_name="SEC EDGAR", current_corp_code="0000320193", current_document_id="acc-2")
    newer = build_comparison_record(_result(computed_at="2026-08-25T00:00:00+00:00"), current_candidate_id="cur-1", current_source_name="SEC EDGAR", current_corp_code="0000320193", current_document_id="acc-2")
    sqlite_comparison_repository.insert_comparison_record(conn, older)
    sqlite_comparison_repository.insert_comparison_record(conn, newer)
    latest = sqlite_comparison_repository.get_latest_comparison_record(conn, "cur-1")
    assert latest.id == newer.id


def test_sqlite_latest_record_breaks_exact_computed_at_tie_by_greatest_id():
    conn = _sqlite_conn()
    tied_at = "2026-08-20T00:00:00+00:00"
    record_a = build_comparison_record(_result(computed_at=tied_at, comparison_basis="matched_rules_set_diff:v1"), current_candidate_id="cur-tie", current_source_name="SEC EDGAR", current_corp_code="0000320193", current_document_id="acc-2")
    record_b = build_comparison_record(_result(computed_at=tied_at, comparison_basis="matched_rules_set_diff:v2"), current_candidate_id="cur-tie", current_source_name="SEC EDGAR", current_corp_code="0000320193", current_document_id="acc-2")
    assert record_a.id != record_b.id
    sqlite_comparison_repository.insert_comparison_record(conn, record_a)
    sqlite_comparison_repository.insert_comparison_record(conn, record_b)
    latest = sqlite_comparison_repository.get_latest_comparison_record(conn, "cur-tie")
    assert latest.id == max(record_a.id, record_b.id)


def test_sqlite_duplicate_stable_id_fails_safely_never_overwrites():
    conn = _sqlite_conn()
    record = _record()
    tampered = dataclasses.replace(record, comparison_status=ComparisonStatus.NO_MATERIAL_CHANGE.value)

    assert sqlite_comparison_repository.insert_comparison_record(conn, record) is True
    assert sqlite_comparison_repository.insert_comparison_record(conn, tampered) is False

    reloaded = sqlite_comparison_repository.get_comparison_record(conn, record.id)
    assert reloaded == record


def test_sqlite_repository_has_no_update_function():
    exported = {name for name in dir(sqlite_comparison_repository) if not name.startswith("_")}
    forbidden_substrings = ("update", "delete", "replace", "upsert", "overwrite", "merge")
    offenders = [name for name in exported if any(f in name.lower() for f in forbidden_substrings)]
    assert not offenders, offenders


def test_sqlite_insert_does_not_mutate_input_record():
    conn = _sqlite_conn()
    record = _record()
    snapshot = copy.deepcopy(record)
    sqlite_comparison_repository.insert_comparison_record(conn, record)
    assert record == snapshot


# ============================================================
# Part D — Postgres (proofs 3, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14)
# All skip cleanly (via pg_conn) when no local disposable Postgres
# instance is available — never fail the suite.
# ============================================================


def test_postgres_inserts_and_round_trips_every_field(pg_conn):
    record = _record()
    inserted = postgres_comparison_repository.insert_comparison_record(pg_conn, record)
    assert inserted is True
    reloaded = postgres_comparison_repository.get_comparison_record(pg_conn, record.id)
    assert reloaded == record


def test_postgres_migration_adds_table_without_touching_existing_candidate_rows(pg_isolated_connection):
    from src.data_access.postgres_state_db import candidate_repository
    from src.models.models import CandidateSignal, CandidateStatus, FilingEvent

    conn = pg_isolated_connection
    filing = FilingEvent(
        rcept_no="0001193125-26-354030", corp_code="0000002489", corp_name="Example Corp",
        stock_code="EX", report_nm="8-K", rcept_dt="2026-08-17", flr_nm="Example Corp",
        source_name="SEC EDGAR",
    )
    candidate = CandidateSignal(id="edgar-cand-pg-1", filing=filing, matched_rules=["financing_or_debt:8-K item 2.03"], confidence="Moderate", status=CandidateStatus.CANDIDATE_DETECTED)
    candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [candidate])
    before = candidate_repository.get_candidate(conn, "edgar-cand-pg-1")

    record = _record(current_candidate_id="edgar-cand-pg-1")
    postgres_comparison_repository.insert_comparison_record(conn, record)

    after = candidate_repository.get_candidate(conn, "edgar-cand-pg-1")
    assert before == after


def test_postgres_optional_null_fields_reconstruct_safely(pg_conn):
    result = _result(prior_document_id=None, prior_filed_at=None, prior_excerpt=None, current_excerpt=None, limitations=())
    record = build_comparison_record(result, current_candidate_id="cur-pg-1", current_source_name="EDINET", current_corp_code="E02778", current_document_id="S100BBBB", prior_candidate_id=None)
    postgres_comparison_repository.insert_comparison_record(pg_conn, record)
    reloaded = postgres_comparison_repository.get_comparison_record(pg_conn, record.id)
    assert reloaded.prior_candidate_id is None
    assert reloaded.prior_document_id is None
    assert reloaded.limitations == ()


def test_postgres_preserves_tuple_ordering_deterministically(pg_conn):
    result = _result(added_categories=("zeta", "alpha", "mu"), removed_categories=("gamma", "beta"))
    record = build_comparison_record(result, current_candidate_id="cur-pg-2", current_source_name="SEC EDGAR", current_corp_code="0000320193", current_document_id="acc-2")
    postgres_comparison_repository.insert_comparison_record(pg_conn, record)
    reloaded = postgres_comparison_repository.get_comparison_record(pg_conn, record.id)
    assert reloaded.added_categories == ("zeta", "alpha", "mu")
    assert reloaded.removed_categories == ("gamma", "beta")


def test_postgres_two_records_for_same_candidate_both_persist_earlier_unchanged(pg_conn):
    older = build_comparison_record(_result(computed_at="2026-08-20T00:00:00+00:00"), current_candidate_id="cur-pg-3", current_source_name="SEC EDGAR", current_corp_code="0000320193", current_document_id="acc-2")
    newer = build_comparison_record(_result(computed_at="2026-08-25T00:00:00+00:00"), current_candidate_id="cur-pg-3", current_source_name="SEC EDGAR", current_corp_code="0000320193", current_document_id="acc-2")
    postgres_comparison_repository.insert_comparison_record(pg_conn, older)
    postgres_comparison_repository.insert_comparison_record(pg_conn, newer)
    all_records = postgres_comparison_repository.load_comparison_records_for_candidate(pg_conn, "cur-pg-3")
    assert len(all_records) == 2
    reloaded_older = postgres_comparison_repository.get_comparison_record(pg_conn, older.id)
    assert reloaded_older == older


def test_postgres_latest_record_returns_newest_computed_at(pg_conn):
    older = build_comparison_record(_result(computed_at="2026-08-20T00:00:00+00:00"), current_candidate_id="cur-pg-4", current_source_name="SEC EDGAR", current_corp_code="0000320193", current_document_id="acc-2")
    newer = build_comparison_record(_result(computed_at="2026-08-25T00:00:00+00:00"), current_candidate_id="cur-pg-4", current_source_name="SEC EDGAR", current_corp_code="0000320193", current_document_id="acc-2")
    postgres_comparison_repository.insert_comparison_record(pg_conn, older)
    postgres_comparison_repository.insert_comparison_record(pg_conn, newer)
    latest = postgres_comparison_repository.get_latest_comparison_record(pg_conn, "cur-pg-4")
    assert latest.id == newer.id


def test_postgres_latest_record_breaks_exact_computed_at_tie_by_greatest_id(pg_conn):
    tied_at = "2026-08-20T00:00:00+00:00"
    record_a = build_comparison_record(_result(computed_at=tied_at, comparison_basis="matched_rules_set_diff:v1"), current_candidate_id="cur-pg-tie", current_source_name="SEC EDGAR", current_corp_code="0000320193", current_document_id="acc-2")
    record_b = build_comparison_record(_result(computed_at=tied_at, comparison_basis="matched_rules_set_diff:v2"), current_candidate_id="cur-pg-tie", current_source_name="SEC EDGAR", current_corp_code="0000320193", current_document_id="acc-2")
    assert record_a.id != record_b.id
    postgres_comparison_repository.insert_comparison_record(pg_conn, record_a)
    postgres_comparison_repository.insert_comparison_record(pg_conn, record_b)
    latest = postgres_comparison_repository.get_latest_comparison_record(pg_conn, "cur-pg-tie")
    assert latest.id == max(record_a.id, record_b.id)


def test_postgres_duplicate_stable_id_fails_safely_never_overwrites_and_connection_stays_usable(pg_conn):
    record = _record(current_candidate_id="cur-pg-5")
    tampered = dataclasses.replace(record, comparison_status=ComparisonStatus.NO_MATERIAL_CHANGE.value)

    assert postgres_comparison_repository.insert_comparison_record(pg_conn, record) is True
    assert postgres_comparison_repository.insert_comparison_record(pg_conn, tampered) is False

    reloaded = postgres_comparison_repository.get_comparison_record(pg_conn, record.id)
    assert reloaded == record

    # The connection must remain usable after the rejected duplicate
    # insert (Postgres aborts a transaction on constraint violation until
    # an explicit rollback — proving that rollback happened correctly).
    another = build_comparison_record(_result(computed_at="2026-09-01T00:00:00+00:00"), current_candidate_id="cur-pg-5", current_source_name="SEC EDGAR", current_corp_code="0000320193", current_document_id="acc-2")
    assert postgres_comparison_repository.insert_comparison_record(pg_conn, another) is True


def test_postgres_repository_has_no_update_function():
    if postgres_comparison_repository is None:
        pytest.skip("psycopg not available")
    exported = {name for name in dir(postgres_comparison_repository) if not name.startswith("_")}
    forbidden_substrings = ("update", "delete", "replace", "upsert", "overwrite", "merge")
    offenders = [name for name in exported if any(f in name.lower() for f in forbidden_substrings)]
    assert not offenders, offenders


# ============================================================
# Part E — scope guards (proof 15)
# ============================================================


def test_no_forbidden_imports_in_new_comparison_persistence_files():
    repo_root = Path(__file__).parent.parent
    files = [
        "src/data_access/comparison_store.py",
        "src/data_access/state_db/comparison_repository.py",
        "src/data_access/postgres_state_db/comparison_repository.py",
    ]
    forbidden_modules = (
        "streamlit", "requests", "httpx", "urllib",
        "src.ui", "src.data_access.daily_news",
        "src.data_access.edgar.client", "src.data_access.dart.client", "src.data_access.edinet.client",
        "src.data_access.edgar.scan_service", "src.data_access.dart.scan_service", "src.data_access.edinet.scan_service",
        "src.data_access.edgar.edgar_pipeline", "src.data_access.dart.radar_pipeline", "src.data_access.edinet.edinet_pipeline",
        "src.data_access.translation",
        "schedule", "apscheduler", "celery",
    )
    offenders = []
    for rel_path in files:
        path = repo_root / rel_path
        assert path.exists(), f"expected file missing: {rel_path}"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
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


def test_prior_disclosure_comparison_module_is_unmodified_except_by_diff_absence():
    """The comparison algorithm module itself must not appear in this
    step's diff at all — this step only ever imports FROM it."""
    repo_root = Path(__file__).parent.parent
    result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    assert "src/logic/prior_disclosure_comparison.py" not in changed


def test_no_new_dependency_added_to_requirements():
    repo_root = Path(__file__).parent.parent
    result = subprocess.run(["git", "diff", "HEAD", "--", "requirements.txt"], cwd=repo_root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    assert result.stdout.strip() == "", f"requirements.txt was modified: {result.stdout}"


def test_scope_guard_only_approved_persistence_files_changed():
    """Runs against `git diff HEAD` — only meaningful in a real checkout
    with this step's changes present; spuriously fires while ANY other
    legitimate uncommitted change is present and resolves once committed
    — same documented convention as this repo's other phase-scoped scope
    guards."""
    repo_root = Path(__file__).parent.parent
    result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    allowed = {
        "src/data_access/comparison_store.py",
        "src/data_access/state_db/schema.py",
        "src/data_access/state_db/comparison_repository.py",
        "src/data_access/postgres_state_db/schema.py",
        "src/data_access/postgres_state_db/comparison_repository.py",
        "tests/test_comparison_persistence.py",
    }
    assert changed <= allowed, changed - allowed


def test_no_ui_daily_news_edgar_dart_edinet_worker_or_deployment_files_touched():
    repo_root = Path(__file__).parent.parent
    result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    forbidden_prefixes = ("src/ui/", "src/data_access/daily_news/")
    forbidden_paths = {
        "scripts/radar_worker.py", "render.yaml", "design/RADAR_WORKER_DEPLOYMENT.md",
        "src/data_access/edgar/client.py", "src/data_access/edgar/scan_service.py", "src/data_access/edgar/edgar_pipeline.py",
        "src/data_access/edgar/document_extractor.py", "src/data_access/edgar/document_service.py",
        "src/data_access/dart/client.py", "src/data_access/dart/scan_service.py", "src/data_access/dart/radar_pipeline.py",
        "src/data_access/dart/document_extractor.py", "src/data_access/dart/document_service.py",
        "src/data_access/edinet/client.py", "src/data_access/edinet/scan_service.py", "src/data_access/edinet/edinet_pipeline.py",
        "src/data_access/edinet/document_extractor.py", "src/data_access/edinet/document_service.py",
        "src/data_access/translation/translation_service.py", "src/data_access/translation/deepl_provider.py",
        "src/models/models.py", "src/logic/prior_disclosure_comparison.py",
    }
    hit = {c for c in changed if c in forbidden_paths or any(c.startswith(p) for p in forbidden_prefixes)}
    assert not hit, hit
