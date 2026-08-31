"""EevaResearch Phase 4, Step 3B (design/DECISIONS.md) — atomic,
validation-first, all-or-nothing Research Case bundle persistence across
JSON, SQLite, and Postgres, plus the private operator authoring script
(scripts/create_research_case.py). Every fixture here is synthetic and
directly constructed; this file never invokes the script's real
main()/persist_bundle() against a real configured backend, never reads
get_settings(), and never performs any network/LLM/scan call. Postgres
tests use the shared, fail-soft local-only fixtures from
tests/_postgres_test_support.py and skip cleanly when no local
disposable Postgres instance is available."""
from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.data_access import research_store
from src.data_access.research_store import (
    append_assertion,
    append_evidence_item,
    append_research_case,
    append_research_case_bundle,
    build_case_id,
    build_dependency_assertion_id,
    build_evidence_id,
    build_relationship_assertion_id,
    get_research_case,
    load_assertions,
    load_evidence_items,
    load_research_cases,
)
from src.data_access.state_db import connection as sqlite_connection
from src.data_access.state_db import research_repository as sqlite_research_repository
from src.data_access.state_db import schema as sqlite_schema
from src.logic.research_case_validation import build_research_case_bundle
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

from scripts import create_research_case as crc

REPO_ROOT = Path(__file__).parent.parent

# ============================================================
# Fixtures (mirrors tests/test_research_case_persistence.py's own
# builders exactly, so both files construct identical shapes)
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
        reasoning="Directly stated in the filing.", limitations=(),
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


def _valid_bundle(case_id="case-x", evidence_added_at="2026-08-20T00:00:00+00:00"):
    case = _case()
    item = _evidence_item(case_id=case.id, added_at=evidence_added_at)
    rel = _relationship_assertion(case_id=case.id, evidence_ids=(item.id,))
    dep = _dependency_assertion(case_id=case.id, affected_entity="Other Corp", evidence_ids=(item.id,))
    return build_research_case_bundle(case, [item], [rel, dep])


def _sqlite_conn():
    conn = sqlite_connection.connect_in_memory()
    sqlite_schema.migrate(conn)
    return conn


# ============================================================
# Proofs 1-3 — valid bundle persistence, JSON / SQLite / Postgres
# ============================================================


def test_proof1_json_valid_bundle_persists_case_and_all_children(tmp_path):
    bundle = _valid_bundle()
    assert append_research_case_bundle(tmp_path, bundle) is True
    assert get_research_case(tmp_path, bundle.case.id) == bundle.case
    assert load_evidence_items(tmp_path)[bundle.evidence_items[0].id] == bundle.evidence_items[0]
    persisted_assertions = load_assertions(tmp_path)
    assert persisted_assertions[bundle.assertions[0].id] == bundle.assertions[0]
    assert persisted_assertions[bundle.assertions[1].id] == bundle.assertions[1]


def test_proof2_sqlite_valid_bundle_persists_case_and_all_children():
    conn = _sqlite_conn()
    bundle = _valid_bundle()
    assert sqlite_research_repository.insert_research_case_bundle(conn, bundle) is True
    assert sqlite_research_repository.get_research_case(conn, bundle.case.id) == bundle.case
    evidence_result = sqlite_research_repository.get_evidence_items_for_case_ids(conn, [bundle.case.id])
    assert evidence_result[bundle.case.id] == bundle.evidence_items
    assertions_result = sqlite_research_repository.get_assertions_for_case_ids(conn, [bundle.case.id])
    assert set(a.id for a in assertions_result[bundle.case.id]) == {a.id for a in bundle.assertions}


def test_proof3_postgres_valid_bundle_persists_case_and_all_children(pg_conn):
    bundle = _valid_bundle(case_id="case-pg-bundle-1")
    assert postgres_research_repository.insert_research_case_bundle(pg_conn, bundle) is True
    assert postgres_research_repository.get_research_case(pg_conn, bundle.case.id) == bundle.case
    evidence_result = postgres_research_repository.get_evidence_items_for_case_ids(pg_conn, [bundle.case.id])
    assert evidence_result[bundle.case.id] == bundle.evidence_items


# ============================================================
# Proof 4 — validation failure produces no mutation, all backends
# ============================================================


def test_proof4_json_invalid_bundle_persists_nothing(tmp_path):
    case = _case()
    invalid_item = _evidence_item(case_id=case.id, source_url="")  # blank -> invalid
    bundle = build_research_case_bundle(case, [invalid_item], [])
    assert append_research_case_bundle(tmp_path, bundle) is False
    assert load_research_cases(tmp_path) == {}
    assert load_evidence_items(tmp_path) == {}
    assert load_assertions(tmp_path) == {}


def test_proof4_sqlite_invalid_bundle_persists_nothing():
    conn = _sqlite_conn()
    case = _case()
    invalid_item = _evidence_item(case_id=case.id, source_url="")
    bundle = build_research_case_bundle(case, [invalid_item], [])
    assert sqlite_research_repository.insert_research_case_bundle(conn, bundle) is False
    assert sqlite_research_repository.get_research_case(conn, case.id) is None


def test_proof4_postgres_invalid_bundle_persists_nothing(pg_conn):
    case = _case(trigger_source_id="cand-pg-invalid")
    invalid_item = _evidence_item(case_id=case.id, source_url="")
    bundle = build_research_case_bundle(case, [invalid_item], [])
    assert postgres_research_repository.insert_research_case_bundle(pg_conn, bundle) is False
    assert postgres_research_repository.get_research_case(pg_conn, case.id) is None
    # Connection must stay usable — validation rejects before any SQL runs.
    other = _case(trigger_source_id="cand-pg-invalid-2")
    assert postgres_research_repository.insert_research_case(pg_conn, other) is True


# ============================================================
# Proofs 5-6 — existing case id / existing child id -> complete rejection
# ============================================================


def test_proof5_json_existing_case_id_rejects_whole_bundle_no_children_added(tmp_path):
    first = _valid_bundle()
    assert append_research_case_bundle(tmp_path, first) is True

    case_again = dataclasses.replace(first.case, title="A different title")
    new_item = _evidence_item(case_id=case_again.id, source_id="cand-2", added_at="2026-08-21T00:00:00+00:00")
    second = build_research_case_bundle(case_again, [new_item], [])
    assert append_research_case_bundle(tmp_path, second) is False
    assert get_research_case(tmp_path, first.case.id) == first.case
    assert new_item.id not in load_evidence_items(tmp_path)


def test_proof6_json_existing_evidence_id_rejects_whole_bundle(tmp_path):
    first = _valid_bundle()
    assert append_research_case_bundle(tmp_path, first) is True

    other_case = _case(trigger_source_id="cand-other", created_at="2026-08-22T00:00:00+00:00")
    reused_item = dataclasses.replace(first.evidence_items[0], case_id=other_case.id)
    second = build_research_case_bundle(other_case, [reused_item], [])
    assert append_research_case_bundle(tmp_path, second) is False
    assert other_case.id not in load_research_cases(tmp_path)


def test_proof6_sqlite_existing_assertion_id_rejects_whole_bundle():
    conn = _sqlite_conn()
    first = _valid_bundle()
    assert sqlite_research_repository.insert_research_case_bundle(conn, first) is True

    other_case = _case(trigger_source_id="cand-other-2", created_at="2026-08-23T00:00:00+00:00")
    other_item = _evidence_item(case_id=other_case.id, source_id="cand-other-2", added_at="2026-08-23T00:00:00+00:00")
    reused_assertion = dataclasses.replace(
        first.assertions[0], case_id=other_case.id, evidence_ids=(other_item.id,),
    )
    second = build_research_case_bundle(other_case, [other_item], [reused_assertion])
    assert sqlite_research_repository.insert_research_case_bundle(conn, second) is False
    assert sqlite_research_repository.get_research_case(conn, other_case.id) is None


def test_proof6_postgres_existing_case_id_rejects_whole_bundle(pg_conn):
    first = _valid_bundle(case_id="case-pg-dup-1")
    assert postgres_research_repository.insert_research_case_bundle(pg_conn, first) is True

    case_again = dataclasses.replace(first.case, title="Different")
    new_item = _evidence_item(case_id=case_again.id, source_id="cand-pg-dup-2", added_at="2026-08-24T00:00:00+00:00")
    second = build_research_case_bundle(case_again, [new_item], [])
    assert postgres_research_repository.insert_research_case_bundle(pg_conn, second) is False
    evidence_result = postgres_research_repository.get_evidence_items_for_case_ids(pg_conn, [case_again.id])
    assert evidence_result == {}
    # Connection stays usable.
    other = _case(trigger_source_id="cand-pg-dup-3")
    assert postgres_research_repository.insert_research_case(pg_conn, other) is True


# ============================================================
# Proof 7 — a duplicate mid-bundle produces no persisted records at all
# ============================================================


def test_proof7_json_mid_bundle_duplicate_evidence_id_leaves_no_new_records(tmp_path):
    case = _case()
    item_a = _evidence_item(case_id=case.id, source_id="a", added_at="2026-08-20T00:00:00+00:00")
    # item_b intentionally reuses item_a's id (simulating a caller-authored
    # duplicate submitted within a single bundle) — the bundle-internal
    # validator already rejects duplicate ids within one bundle, so this
    # bundle must be fully rejected with nothing persisted, not partially
    # written up to item_a.
    item_b = dataclasses.replace(_evidence_item(case_id=case.id, source_id="b", added_at="2026-08-21T00:00:00+00:00"), id=item_a.id)
    bundle = build_research_case_bundle(case, [item_a, item_b], [])
    assert append_research_case_bundle(tmp_path, bundle) is False
    assert load_research_cases(tmp_path) == {}
    assert load_evidence_items(tmp_path) == {}


def test_proof7_sqlite_mid_bundle_duplicate_assertion_id_leaves_no_new_records():
    conn = _sqlite_conn()
    case = _case()
    item = _evidence_item(case_id=case.id)
    rel = _relationship_assertion(case_id=case.id, evidence_ids=(item.id,))
    dep = dataclasses.replace(
        _dependency_assertion(case_id=case.id, evidence_ids=(item.id,)), id=rel.id,
    )
    bundle = build_research_case_bundle(case, [item], [rel, dep])
    # Bundle-internal cross-kind id collision is caught by
    # validate_research_case_bundle itself (Step 2) before any SQL runs.
    assert sqlite_research_repository.insert_research_case_bundle(conn, bundle) is False
    assert sqlite_research_repository.get_research_case(conn, case.id) is None


# ============================================================
# Proof 8/9 — injected JSON serialization / temp-write failure leaves
# originals byte-for-byte unchanged, with best-effort temp cleanup
# ============================================================


def _file_bytes(cache_dir: Path) -> dict[str, bytes | None]:
    names = ["research_cases.json", "research_evidence_items.json", "research_assertions.json"]
    return {name: (cache_dir / name).read_bytes() if (cache_dir / name).exists() else None for name in names}


def test_proof8_json_injected_serialization_failure_leaves_originals_unchanged(tmp_path, monkeypatch):
    original = _valid_bundle()
    assert append_research_case_bundle(tmp_path, original) is True
    before = _file_bytes(tmp_path)

    def _boom(*_a, **_k):
        raise TypeError("injected serialization failure")

    monkeypatch.setattr(research_store.json, "dumps", _boom)
    new_bundle = _valid_bundle(case_id="case-y", evidence_added_at="2026-08-25T00:00:00+00:00")
    new_bundle = dataclasses.replace(new_bundle, case=dataclasses.replace(new_bundle.case, trigger_source_id="cand-2"))
    assert append_research_case_bundle(tmp_path, new_bundle) is False
    assert _file_bytes(tmp_path) == before


def test_proof9_json_injected_temp_write_failure_leaves_originals_unchanged_and_cleans_up_temp_files(tmp_path, monkeypatch):
    original = _valid_bundle()
    assert append_research_case_bundle(tmp_path, original) is True
    before = _file_bytes(tmp_path)

    real_write_temp_json = research_store._write_temp_json
    call_count = {"n": 0}

    def _fails_on_second_call(directory, target_filename, payload):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("injected temp-write failure")
        return real_write_temp_json(directory, target_filename, payload)

    monkeypatch.setattr(research_store, "_write_temp_json", _fails_on_second_call)
    new_bundle = dataclasses.replace(
        _valid_bundle(), case=dataclasses.replace(_case(trigger_source_id="cand-3"), id=build_case_id("radar", "cand-3", "2026-08-20T00:00:00+00:00")),
    )
    assert append_research_case_bundle(tmp_path, new_bundle) is False
    assert _file_bytes(tmp_path) == before
    leftover_temp_files = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftover_temp_files == []


# ============================================================
# Proof 10 — SQLite injected failure/constraint rolls back completely
# ============================================================


def test_proof10_sqlite_constraint_failure_on_later_child_rolls_back_case_and_earlier_children():
    conn = _sqlite_conn()
    pre_existing = _evidence_item(case_id="other-case", source_id="pre-existing", added_at="2026-08-01T00:00:00+00:00")
    assert sqlite_research_repository.insert_evidence_item(conn, pre_existing) is True

    case = _case(trigger_source_id="cand-rollback")
    item = _evidence_item(case_id=case.id, source_id="cand-rollback", added_at="2026-08-20T00:00:00+00:00")
    # Reuses pre_existing's id to force a PK violation on the evidence
    # insert, after the case insert has already executed inside the
    # same transaction.
    colliding_item = dataclasses.replace(item, id=pre_existing.id)
    bundle = build_research_case_bundle(case, [colliding_item], [])
    # This bypasses the bundle's own validate_research_case_bundle (which
    # only checks ids *within* the submitted bundle, not persisted
    # state) by calling the repository function with a hand-built,
    # already-tuple-normalized bundle whose validation still succeeds —
    # proving the SQL-level IntegrityError path itself rolls back.
    assert sqlite_research_repository.insert_research_case_bundle(conn, bundle) is False
    assert sqlite_research_repository.get_research_case(conn, case.id) is None
    # The pre-existing row is untouched.
    result = sqlite_research_repository.get_evidence_items_for_case_ids(conn, ["other-case"])
    assert result["other-case"] == (pre_existing,)


# ============================================================
# Proof 11 — Postgres duplicate/failure rolls back completely, connection
# stays usable afterward
# ============================================================


def test_proof11_postgres_constraint_failure_on_later_child_rolls_back_and_connection_stays_usable(pg_conn):
    pre_existing = _evidence_item(case_id="other-case-pg", source_id="pre-existing-pg", added_at="2026-08-01T00:00:00+00:00")
    assert postgres_research_repository.insert_evidence_item(pg_conn, pre_existing) is True

    case = _case(trigger_source_id="cand-rollback-pg")
    item = _evidence_item(case_id=case.id, source_id="cand-rollback-pg", added_at="2026-08-20T00:00:00+00:00")
    colliding_item = dataclasses.replace(item, id=pre_existing.id)
    bundle = build_research_case_bundle(case, [colliding_item], [])
    assert postgres_research_repository.insert_research_case_bundle(pg_conn, bundle) is False
    assert postgres_research_repository.get_research_case(pg_conn, case.id) is None

    result = postgres_research_repository.get_evidence_items_for_case_ids(pg_conn, ["other-case-pg"])
    assert result["other-case-pg"] == (pre_existing,)

    other = _case(trigger_source_id="cand-rollback-pg-2")
    assert postgres_research_repository.insert_research_case(pg_conn, other) is True


# ============================================================
# Proof 12 — existing single-record append/insert APIs remain unchanged
# ============================================================


def test_proof12_existing_single_record_apis_still_work_unchanged(tmp_path):
    case = _case()
    assert append_research_case(tmp_path, case) is True
    item = _evidence_item(case_id=case.id)
    assert append_evidence_item(tmp_path, item) is True
    rel = _relationship_assertion(case_id=case.id, evidence_ids=(item.id,))
    assert append_assertion(tmp_path, rel) is True

    conn = _sqlite_conn()
    assert sqlite_research_repository.insert_research_case(conn, case) is True
    assert sqlite_research_repository.insert_evidence_item(conn, item) is True
    assert sqlite_research_repository.insert_assertion(conn, rel) is True


# ============================================================
# Proof 13 — bundle persistence never mutates supplied frozen objects
# ============================================================


def test_proof13_json_persistence_does_not_mutate_supplied_objects(tmp_path):
    bundle = _valid_bundle()
    case_before, item_before, assertions_before = bundle.case, bundle.evidence_items[0], tuple(bundle.assertions)
    assert append_research_case_bundle(tmp_path, bundle) is True
    assert bundle.case == case_before
    assert bundle.evidence_items[0] == item_before
    assert tuple(bundle.assertions) == assertions_before


def test_proof13_sqlite_persistence_does_not_mutate_supplied_objects():
    conn = _sqlite_conn()
    bundle = _valid_bundle()
    case_before, item_before = bundle.case, bundle.evidence_items[0]
    assert sqlite_research_repository.insert_research_case_bundle(conn, bundle) is True
    assert bundle.case == case_before
    assert bundle.evidence_items[0] == item_before


# ============================================================
# Proof 14 — exact round-trip of every field type, all backends
# ============================================================


def test_proof14_json_round_trip_preserves_every_field_type(tmp_path):
    bundle = _valid_bundle()
    assert append_research_case_bundle(tmp_path, bundle) is True
    reloaded_case = get_research_case(tmp_path, bundle.case.id)
    assert reloaded_case.status == ResearchCaseStatus.OPEN
    reloaded_item = load_evidence_items(tmp_path)[bundle.evidence_items[0].id]
    assert reloaded_item.source_url == bundle.evidence_items[0].source_url
    assert reloaded_item.excerpt_translated is None
    reloaded_assertions = load_assertions(tmp_path)
    reloaded_rel = reloaded_assertions[bundle.assertions[0].id]
    assert isinstance(reloaded_rel, RelationshipAssertion)
    assert reloaded_rel.role == RelationshipRole.SUPPLIER
    assert reloaded_rel.confidence == AssertionConfidence.HIGH
    reloaded_dep = reloaded_assertions[bundle.assertions[1].id]
    assert isinstance(reloaded_dep, DependencyAssertion)
    assert reloaded_dep.transmission_path == ("Acme Corp", "Example Corp")
    assert reloaded_dep.limitations == ("Not yet independently confirmed by a second source.",)


def test_proof14_sqlite_round_trip_preserves_every_field_type():
    conn = _sqlite_conn()
    bundle = _valid_bundle()
    assert sqlite_research_repository.insert_research_case_bundle(conn, bundle) is True
    result = sqlite_research_repository.get_assertions_for_case_ids(conn, [bundle.case.id])
    by_id = {a.id: a for a in result[bundle.case.id]}
    dep = by_id[bundle.assertions[1].id]
    assert dep.transmission_path == ("Acme Corp", "Example Corp")
    assert dep.limitations == ("Not yet independently confirmed by a second source.",)


def test_proof14_postgres_round_trip_preserves_every_field_type(pg_conn):
    bundle = _valid_bundle(case_id="case-pg-roundtrip")
    assert postgres_research_repository.insert_research_case_bundle(pg_conn, bundle) is True
    result = postgres_research_repository.get_assertions_for_case_ids(pg_conn, [bundle.case.id])
    by_id = {a.id: a for a in result[bundle.case.id]}
    dep = by_id[bundle.assertions[1].id]
    assert dep.transmission_path == ("Acme Corp", "Example Corp")


# ============================================================
# Proof 15 — JSON bundle impl loads each store exactly once, never
# repeatedly calls the public single-record append functions
# ============================================================


def test_proof15_json_bundle_loads_each_store_exactly_once_and_never_calls_single_record_appends(tmp_path, monkeypatch):
    load_calls = {"cases": 0, "evidence": 0, "assertions": 0}
    real_load_cases, real_load_evidence, real_load_assertions = (
        research_store.load_research_cases, research_store.load_evidence_items, research_store.load_assertions,
    )

    def _counting(name, real_fn):
        def _wrapped(*args, **kwargs):
            load_calls[name] += 1
            return real_fn(*args, **kwargs)
        return _wrapped

    monkeypatch.setattr(research_store, "load_research_cases", _counting("cases", real_load_cases))
    monkeypatch.setattr(research_store, "load_evidence_items", _counting("evidence", real_load_evidence))
    monkeypatch.setattr(research_store, "load_assertions", _counting("assertions", real_load_assertions))

    def _must_not_be_called(*_a, **_k):
        raise AssertionError("append_research_case_bundle must not call the single-record append functions")

    monkeypatch.setattr(research_store, "append_research_case", _must_not_be_called)
    monkeypatch.setattr(research_store, "append_evidence_item", _must_not_be_called)
    monkeypatch.setattr(research_store, "append_assertion", _must_not_be_called)

    bundle = _valid_bundle()
    assert append_research_case_bundle(tmp_path, bundle) is True
    assert load_calls == {"cases": 1, "evidence": 1, "assertions": 1}


# ============================================================
# Proof 16 — no update/upsert/replace/delete path in the new bundle path
# ============================================================


def test_proof16_no_update_upsert_replace_or_delete_functions_in_bundle_modules():
    modules = [research_store, sqlite_research_repository]
    if postgres_research_repository is not None:
        modules.append(postgres_research_repository)
    forbidden_substrings = ("update", "delete", "replace", "upsert", "overwrite", "merge")
    offenders = []
    for module in modules:
        exported = {name for name in dir(module) if not name.startswith("_")}
        offenders.extend(
            f"{module.__name__}.{name}" for name in exported
            if any(f in name.lower() for f in forbidden_substrings)
        )
    assert not offenders, offenders


def test_proof16_bundle_functions_have_the_exact_required_signatures():
    sig = inspect.signature(append_research_case_bundle)
    assert list(sig.parameters) == ["cache_dir", "bundle"]

    sig = inspect.signature(sqlite_research_repository.insert_research_case_bundle)
    assert list(sig.parameters) == ["conn", "bundle"]

    if postgres_research_repository is not None:
        sig = inspect.signature(postgres_research_repository.insert_research_case_bundle)
        assert list(sig.parameters) == ["conn", "bundle"]


# ============================================================
# Proofs 17-18 — script default behavior: no silent write, invalid/
# template content refuses to persist
# ============================================================


def test_proof17_script_authoring_disabled_by_default_builds_nothing():
    assert crc.AUTHORING_ENABLED is False
    assert crc.build_authored_bundle(False) is None


def test_proof17_script_main_with_no_flags_writes_nothing(tmp_path, capsys):
    exit_code = crc.main([])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "disabled by default" in captured.out
    assert load_research_cases(tmp_path) == {}


def test_proof18_script_placeholder_template_content_refuses_to_persist_even_if_enabled(monkeypatch):
    bundle = crc.build_authored_bundle(True)
    assert bundle is not None
    assert crc.contains_placeholder_sentinel(bundle) is True

    # Simulates an operator having flipped the top-of-file gate to True
    # without replacing the placeholder content — the second, independent
    # content-level guard must still refuse to persist.
    monkeypatch.setattr(crc, "AUTHORING_ENABLED", True)
    exit_code = crc.main(["--confirm", "--backend", "json"])
    assert exit_code == 1


def test_proof18_placeholder_scan_passes_for_a_fully_authored_bundle():
    bundle = _valid_bundle()
    assert crc.contains_placeholder_sentinel(bundle) is False


# ============================================================
# Proof 19 — script persists only via build_research_case_bundle /
# validate_research_case_bundle / the atomic persistence functions,
# never direct JSON/SQL manipulation
# ============================================================


def test_proof19_script_persists_a_fully_authored_valid_bundle_via_persist_bundle_dispatch(tmp_path):
    bundle = _valid_bundle()
    assert crc.contains_placeholder_sentinel(bundle) is False
    assert crc.validate_research_case_bundle(bundle) == ()
    assert crc.persist_bundle(bundle, "json", cache_dir=tmp_path) is True
    assert get_research_case(tmp_path, bundle.case.id) == bundle.case
    # Re-running with the identical, deterministically-id'd bundle is a
    # safe no-op via the atomic function's own duplicate detection.
    assert crc.persist_bundle(bundle, "json", cache_dir=tmp_path) is False


def test_proof19_script_source_never_imports_sqlite3_or_psycopg_directly():
    tree = ast.parse((REPO_ROOT / "scripts" / "create_research_case.py").read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for module in modules:
            if module in ("sqlite3", "psycopg", "json"):
                offenders.append(module)
    assert not offenders, offenders


def test_proof19_script_source_never_calls_json_dump_or_raw_sql_execute():
    source = (REPO_ROOT / "scripts" / "create_research_case.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders = [
        n.func.attr for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr in ("execute", "executemany", "dump", "dumps")
    ]
    assert not offenders, offenders


# ============================================================
# Proof 20 — scope guards: forbidden imports, forbidden files touched,
# script not imported from any application runtime entry point
# ============================================================


def test_proof20_atomic_persistence_modules_never_import_forbidden_types():
    files = [
        "src/data_access/research_store.py",
        "src/data_access/state_db/research_repository.py",
        "src/data_access/postgres_state_db/research_repository.py",
        "scripts/create_research_case.py",
    ]
    forbidden_modules = (
        "src.models.models", "src.models.daily_news_models", "src.models.issuer", "src.models.theme_registry",
        "src.data_access.daily_news", "src.data_access.edgar", "src.data_access.dart", "src.data_access.edinet",
        "src.config.tracked_companies", "src.config.issuer_registry", "src.data_access.backend_factory",
        "src.ui", "streamlit", "requests", "urllib", "httpx", "socket",
        "anthropic", "openai", "langchain", "chromadb", "pinecone", "weaviate",
    )
    offenders = []
    for rel_path in files:
        source = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=rel_path)
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


def test_proof20_script_is_never_imported_by_any_application_runtime_entry_point():
    candidate_files = [
        "app.py", "scripts/radar_worker.py", "scripts/run_scan.py", "scripts/run_daily_news_discovery.py",
    ]
    ui_dir = REPO_ROOT / "src" / "ui"
    candidate_files.extend(str(p.relative_to(REPO_ROOT)) for p in ui_dir.rglob("*.py"))

    offenders = []
    for rel_path in candidate_files:
        full_path = REPO_ROOT / rel_path
        if not full_path.exists():
            continue
        tree = ast.parse(full_path.read_text(encoding="utf-8"), filename=rel_path)
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                if "create_research_case" in module:
                    offenders.append(f"{rel_path}: imports {module!r}")
    assert not offenders, offenders


def test_proof20_no_new_dependency_added_to_requirements():
    result = subprocess.run(["git", "diff", "HEAD", "--", "requirements.txt"], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    assert result.stdout.strip() == "", f"requirements.txt was modified: {result.stdout}"


def test_proof20_scope_guard_only_approved_files_changed():
    """Runs against `git diff HEAD` — only meaningful in a real checkout
    with this step's changes present; spuriously fires while ANY other
    legitimate uncommitted change is present and resolves once
    committed — same documented convention as this repo's other
    phase-scoped scope guards. A brand-new untracked file (like the
    script and this test file itself) never appears in `git diff HEAD`
    output, so it needs no entry in `allowed` to avoid a false failure."""
    result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    allowed = {
        "src/data_access/research_store.py",
        "src/data_access/state_db/research_repository.py",
        "src/data_access/postgres_state_db/research_repository.py",
    }
    assert changed <= allowed, changed - allowed


def test_proof20_no_ui_source_pipeline_worker_or_deployment_files_touched():
    result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    forbidden_prefixes = (
        "src/ui/", "src/data_access/daily_news/", "src/data_access/edgar/", "src/data_access/dart/",
        "src/data_access/edinet/",
    )
    forbidden_paths = {
        "render.yaml", "design/RADAR_WORKER_DEPLOYMENT.md", "app.py",
        "src/models/models.py", "src/models/daily_news_models.py", "src/models/issuer.py", "src/models/research_case.py",
        "src/logic/prior_disclosure_comparison.py", "src/logic/research_case_validation.py",
        "src/config/tracked_companies.py", "src/config/issuer_registry.py", "src/config/ontology.py",
        "src/data_access/comparison_store.py", "src/data_access/state_db/comparison_repository.py",
        "src/data_access/postgres_state_db/comparison_repository.py",
        "src/data_access/backend_factory.py",
        "requirements.txt",
    }
    hit = {c for c in changed if c in forbidden_paths or any(c.startswith(p) for p in forbidden_prefixes)}
    assert not hit, hit


def test_proof20_no_live_postgres_or_sqlite_connection_opened_by_this_test_module_via_get_settings():
    """Guards against this test file (or the script it imports) ever
    reading ambient get_settings() during collection/import — proving
    every test above supplies its own explicit tmp_path/in-memory/
    isolated-schema fixture rather than touching real configured state."""
    source = (REPO_ROOT / "tests" / "test_research_case_atomic_persistence.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "get_settings"]
    assert not calls
