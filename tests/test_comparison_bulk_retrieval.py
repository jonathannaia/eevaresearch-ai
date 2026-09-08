"""Radar evidence-packet foundation, Phase 3, Step 3A (design/DECISIONS.md)
— bulk latest-comparison-record retrieval across JSON, SQLite, and
Postgres, plus the ComparisonRepository backend-factory seam. This step
adds a read-only bulk lookup only: no comparison computation, no
persistence-record-shape change, no schema/migration change, no UI wiring.
Every fixture is synthetic and local; no source is fetched, no scan runs,
no comparison algorithm (build_comparison_result/select_prior_candidate)
is invoked anywhere in this file. Postgres tests use the shared, fail-
soft local-only fixtures from tests/_postgres_test_support.py and skip
cleanly when no local disposable Postgres instance is available; the
"exactly one query" property for Postgres is proven with a mock
connection (per this step's own approval: "test query construction/mock
interaction as appropriate" when integration testing isn't available),
independent of whether a real instance is reachable."""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.data_access import backend_factory, comparison_store
from src.data_access.backend_factory import (
    ComparisonRepositoryProtocol,
    JsonComparisonRepository,
    PostgresComparisonRepository,
    SqliteComparisonRepository,
    get_comparison_repository,
)
from src.data_access.comparison_store import build_comparison_record, latest_comparison_records_for_candidate_ids
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
        limitations=(),
    )
    defaults.update(overrides)
    return ComparisonResult(**defaults)


def _record(
    current_candidate_id: str,
    current_source_name: str = "SEC EDGAR",
    current_corp_code: str = "0000320193",
    current_document_id: str = "acc-2",
    prior_candidate_id: str | None = None,
    **result_overrides,
):
    """`result_overrides` are forwarded to _result() (e.g. computed_at,
    comparison_basis) — everything else is this record's own identity
    fields."""
    result = _result(**result_overrides)
    return build_comparison_record(
        result,
        current_candidate_id=current_candidate_id,
        current_source_name=current_source_name,
        current_corp_code=current_corp_code,
        current_document_id=current_document_id,
        prior_candidate_id=prior_candidate_id,
    )


def _sqlite_conn():
    conn = sqlite_connection.connect_in_memory()
    sqlite_schema.migrate(conn)
    return conn


# ============================================================
# Part A — JSON bulk retrieval (proofs 1, 2, 5, 6, 7, 8, 9, 11)
# ============================================================


def test_json_bulk_empty_input_returns_empty_without_loading(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(comparison_store, "load_comparison_records", lambda *a, **k: (calls.append(1), {})[1])
    result = latest_comparison_records_for_candidate_ids(tmp_path, [])
    assert result == {}
    assert calls == []  # load_comparison_records was never called


def test_json_bulk_makes_exactly_one_store_load_for_non_empty_request(tmp_path, monkeypatch):
    record = _record("cur-1")
    comparison_store.append_comparison_record(tmp_path, record)

    calls = []
    real_load = comparison_store.load_comparison_records

    def _counting_load(*args, **kwargs):
        calls.append(1)
        return real_load(*args, **kwargs)

    monkeypatch.setattr(comparison_store, "load_comparison_records", _counting_load)
    latest_comparison_records_for_candidate_ids(tmp_path, ["cur-1", "cur-2", "cur-3"])
    assert len(calls) == 1


def test_json_bulk_returns_correct_latest_per_candidate(tmp_path):
    cur1_older = _record("cur-1", computed_at="2026-08-01T00:00:00+00:00")
    cur1_newer = _record("cur-1", computed_at="2026-08-10T00:00:00+00:00")
    cur2_only = _record("cur-2", computed_at="2026-08-05T00:00:00+00:00")
    for r in (cur1_older, cur1_newer, cur2_only):
        comparison_store.append_comparison_record(tmp_path, r)

    result = latest_comparison_records_for_candidate_ids(tmp_path, ["cur-1", "cur-2"])
    assert result["cur-1"].id == cur1_newer.id
    assert result["cur-2"].id == cur2_only.id


def test_json_bulk_breaks_exact_computed_at_tie_by_greatest_id(tmp_path):
    tied_at = "2026-08-20T00:00:00+00:00"
    record_a = _record("cur-tie", computed_at=tied_at, comparison_basis="matched_rules_set_diff:v1")
    record_b = _record("cur-tie", computed_at=tied_at, comparison_basis="matched_rules_set_diff:v2")
    assert record_a.id != record_b.id
    comparison_store.append_comparison_record(tmp_path, record_a)
    comparison_store.append_comparison_record(tmp_path, record_b)

    result = latest_comparison_records_for_candidate_ids(tmp_path, ["cur-tie"])
    assert result["cur-tie"].id == max(record_a.id, record_b.id)


def test_json_bulk_omits_unrequested_and_unknown_ids(tmp_path):
    known = _record("cur-known")
    unrequested = _record("cur-unrequested")
    comparison_store.append_comparison_record(tmp_path, known)
    comparison_store.append_comparison_record(tmp_path, unrequested)

    result = latest_comparison_records_for_candidate_ids(tmp_path, ["cur-known", "cur-does-not-exist"])
    assert set(result.keys()) == {"cur-known"}


def test_json_bulk_returns_partial_results(tmp_path):
    only_one = _record("cur-1")
    comparison_store.append_comparison_record(tmp_path, only_one)
    result = latest_comparison_records_for_candidate_ids(tmp_path, ["cur-1", "cur-missing"])
    assert list(result.keys()) == ["cur-1"]


def test_json_bulk_does_not_mutate_stored_records(tmp_path):
    record = _record("cur-1")
    comparison_store.append_comparison_record(tmp_path, record)
    latest_comparison_records_for_candidate_ids(tmp_path, ["cur-1"])
    reloaded = comparison_store.load_comparison_records(tmp_path)[record.id]
    assert reloaded == record


# ============================================================
# Part B — SQLite bulk retrieval (proofs 1, 3, 5, 6, 7, 8, 9, 11)
# ============================================================


def test_sqlite_bulk_empty_input_returns_empty_without_querying():
    conn = MagicMock()
    result = sqlite_comparison_repository.get_latest_comparison_records_for_candidate_ids(conn, [])
    assert result == {}
    conn.execute.assert_not_called()


def test_sqlite_bulk_query_construction_uses_one_parameterized_call_with_placeholders_only():
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = []
    sqlite_comparison_repository.get_latest_comparison_records_for_candidate_ids(conn, ["cur-1", "cur-2", "cur-3"])
    assert conn.execute.call_count == 1
    sql_text, params = conn.execute.call_args[0]
    assert sql_text.count("?") == 3
    assert params == ("cur-1", "cur-2", "cur-3")
    # No candidate id value is ever interpolated into the SQL text itself.
    assert "cur-1" not in sql_text and "cur-2" not in sql_text and "cur-3" not in sql_text
    assert "ROW_NUMBER" in sql_text and "PARTITION BY current_candidate_id" in sql_text
    assert "ORDER BY computed_at DESC, id DESC" in sql_text


def test_sqlite_bulk_returns_correct_latest_per_candidate():
    conn = _sqlite_conn()
    cur1_older = _record("cur-1", computed_at="2026-08-01T00:00:00+00:00")
    cur1_newer = _record("cur-1", computed_at="2026-08-10T00:00:00+00:00")
    cur2_only = _record("cur-2", computed_at="2026-08-05T00:00:00+00:00")
    for r in (cur1_older, cur1_newer, cur2_only):
        sqlite_comparison_repository.insert_comparison_record(conn, r)

    result = sqlite_comparison_repository.get_latest_comparison_records_for_candidate_ids(conn, ["cur-1", "cur-2"])
    assert result["cur-1"].id == cur1_newer.id
    assert result["cur-2"].id == cur2_only.id


def test_sqlite_bulk_breaks_exact_computed_at_tie_by_greatest_id():
    conn = _sqlite_conn()
    tied_at = "2026-08-20T00:00:00+00:00"
    record_a = _record("cur-tie", computed_at=tied_at, comparison_basis="matched_rules_set_diff:v1")
    record_b = _record("cur-tie", computed_at=tied_at, comparison_basis="matched_rules_set_diff:v2")
    sqlite_comparison_repository.insert_comparison_record(conn, record_a)
    sqlite_comparison_repository.insert_comparison_record(conn, record_b)

    result = sqlite_comparison_repository.get_latest_comparison_records_for_candidate_ids(conn, ["cur-tie"])
    assert result["cur-tie"].id == max(record_a.id, record_b.id)


def test_sqlite_bulk_omits_unrequested_and_unknown_ids():
    conn = _sqlite_conn()
    known = _record("cur-known")
    unrequested = _record("cur-unrequested")
    sqlite_comparison_repository.insert_comparison_record(conn, known)
    sqlite_comparison_repository.insert_comparison_record(conn, unrequested)

    result = sqlite_comparison_repository.get_latest_comparison_records_for_candidate_ids(conn, ["cur-known", "cur-does-not-exist"])
    assert set(result.keys()) == {"cur-known"}


def test_sqlite_bulk_returns_partial_results():
    conn = _sqlite_conn()
    only_one = _record("cur-1")
    sqlite_comparison_repository.insert_comparison_record(conn, only_one)
    result = sqlite_comparison_repository.get_latest_comparison_records_for_candidate_ids(conn, ["cur-1", "cur-missing"])
    assert list(result.keys()) == ["cur-1"]


def test_sqlite_bulk_never_inserts_updates_or_deletes():
    conn = _sqlite_conn()
    record = _record("cur-1")
    sqlite_comparison_repository.insert_comparison_record(conn, record)
    before = sqlite_comparison_repository.get_comparison_record(conn, record.id)

    sqlite_comparison_repository.get_latest_comparison_records_for_candidate_ids(conn, ["cur-1"])

    after = sqlite_comparison_repository.get_comparison_record(conn, record.id)
    assert before == after


# ============================================================
# Part C — Postgres bulk retrieval (proofs 1, 4, 5, 6, 7, 8, 9, 11)
# ============================================================


def test_postgres_bulk_empty_input_returns_empty_without_querying():
    if postgres_comparison_repository is None:
        pytest.skip("psycopg not available")
    conn = MagicMock()
    result = postgres_comparison_repository.get_latest_comparison_records_for_candidate_ids(conn, [])
    assert result == {}
    conn.execute.assert_not_called()


def test_postgres_bulk_query_construction_uses_one_parameterized_call_with_any():
    if postgres_comparison_repository is None:
        pytest.skip("psycopg not available")
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = []
    postgres_comparison_repository.get_latest_comparison_records_for_candidate_ids(conn, ["cur-1", "cur-2"])
    assert conn.execute.call_count == 1
    sql_text, params = conn.execute.call_args[0]
    assert "= ANY(%s)" in sql_text
    assert params == (["cur-1", "cur-2"],)
    assert "cur-1" not in sql_text and "cur-2" not in sql_text
    assert "ROW_NUMBER" in sql_text and "PARTITION BY current_candidate_id" in sql_text
    assert "ORDER BY computed_at DESC, id DESC" in sql_text


def test_postgres_bulk_returns_correct_latest_per_candidate(pg_conn):
    cur1_older = _record("cur-pg-1", computed_at="2026-08-01T00:00:00+00:00")
    cur1_newer = _record("cur-pg-1", computed_at="2026-08-10T00:00:00+00:00")
    cur2_only = _record("cur-pg-2", computed_at="2026-08-05T00:00:00+00:00")
    for r in (cur1_older, cur1_newer, cur2_only):
        postgres_comparison_repository.insert_comparison_record(pg_conn, r)

    result = postgres_comparison_repository.get_latest_comparison_records_for_candidate_ids(pg_conn, ["cur-pg-1", "cur-pg-2"])
    assert result["cur-pg-1"].id == cur1_newer.id
    assert result["cur-pg-2"].id == cur2_only.id


def test_postgres_bulk_breaks_exact_computed_at_tie_by_greatest_id(pg_conn):
    tied_at = "2026-08-20T00:00:00+00:00"
    record_a = _record("cur-pg-tie", computed_at=tied_at, comparison_basis="matched_rules_set_diff:v1")
    record_b = _record("cur-pg-tie", computed_at=tied_at, comparison_basis="matched_rules_set_diff:v2")
    postgres_comparison_repository.insert_comparison_record(pg_conn, record_a)
    postgres_comparison_repository.insert_comparison_record(pg_conn, record_b)

    result = postgres_comparison_repository.get_latest_comparison_records_for_candidate_ids(pg_conn, ["cur-pg-tie"])
    assert result["cur-pg-tie"].id == max(record_a.id, record_b.id)


def test_postgres_bulk_omits_unrequested_and_unknown_ids(pg_conn):
    known = _record("cur-pg-known")
    unrequested = _record("cur-pg-unrequested")
    postgres_comparison_repository.insert_comparison_record(pg_conn, known)
    postgres_comparison_repository.insert_comparison_record(pg_conn, unrequested)

    result = postgres_comparison_repository.get_latest_comparison_records_for_candidate_ids(pg_conn, ["cur-pg-known", "cur-pg-does-not-exist"])
    assert set(result.keys()) == {"cur-pg-known"}


def test_postgres_bulk_never_inserts_updates_or_deletes(pg_conn):
    record = _record("cur-pg-1")
    postgres_comparison_repository.insert_comparison_record(pg_conn, record)
    before = postgres_comparison_repository.get_comparison_record(pg_conn, record.id)

    postgres_comparison_repository.get_latest_comparison_records_for_candidate_ids(pg_conn, ["cur-pg-1"])

    after = postgres_comparison_repository.get_comparison_record(pg_conn, record.id)
    assert before == after


# ============================================================
# Part D — backend factory (proof 12)
# ============================================================


def _settings(db_backend: str = "json", **overrides):
    from src.config.settings import Settings

    fields = dict(db_backend=db_backend)
    fields.update(overrides)
    return Settings(**fields)


def test_backend_factory_json_returns_json_comparison_repository(tmp_path):
    settings = _settings("json", cache_dir=tmp_path)
    repo = get_comparison_repository(settings)
    assert isinstance(repo, JsonComparisonRepository)
    assert repo.cache_dir == tmp_path


def test_backend_factory_unrecognized_backend_defaults_to_json(tmp_path):
    settings = _settings("not-a-real-backend", cache_dir=tmp_path)
    repo = get_comparison_repository(settings)
    assert isinstance(repo, JsonComparisonRepository)


def test_backend_factory_sqlite_requires_configured_path():
    settings = _settings("sqlite")
    with pytest.raises(backend_factory.BackendConfigurationError):
        get_comparison_repository(settings)


def test_backend_factory_postgres_requires_configured_url():
    settings = _settings("postgres")
    with pytest.raises(backend_factory.BackendConfigurationError):
        get_comparison_repository(settings)


def test_backend_factory_sqlite_repository_wires_through_to_real_module(tmp_path):
    settings = _settings("sqlite", state_db_path=str(tmp_path / "state.db"))
    repo = get_comparison_repository(settings)
    assert isinstance(repo, SqliteComparisonRepository)
    record = _record("cur-1")
    sqlite_comparison_repository.insert_comparison_record(repo.conn, record)
    result = repo.latest_for_candidate_ids(["cur-1"])
    assert result["cur-1"].id == record.id


def test_json_comparison_repository_delegates_to_comparison_store(tmp_path):
    record = _record("cur-1")
    comparison_store.append_comparison_record(tmp_path, record)
    repo = JsonComparisonRepository(cache_dir=tmp_path)
    result = repo.latest_for_candidate_ids(["cur-1"])
    assert result["cur-1"].id == record.id


def test_comparison_repository_protocol_only_exposes_the_bulk_read_method():
    # Smallest-abstraction proof: no update/insert/delete method is part
    # of the exposed Protocol surface.
    protocol_methods = {name for name in dir(ComparisonRepositoryProtocol) if not name.startswith("_")}
    assert protocol_methods == {"latest_for_candidate_ids"}


# ============================================================
# Part E — no comparison execution, no UI/pipeline import (proofs 13, 14)
# ============================================================


def test_no_comparison_algorithm_functions_referenced_in_new_or_changed_files():
    repo_root = Path(__file__).parent.parent
    files = [
        "src/data_access/comparison_store.py",
        "src/data_access/state_db/comparison_repository.py",
        "src/data_access/postgres_state_db/comparison_repository.py",
        "src/data_access/backend_factory.py",
    ]
    forbidden_names = {"build_comparison_result", "select_prior_candidate", "compare_matched_rules"}
    offenders = []
    for rel_path in files:
        source = (repo_root / rel_path).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=rel_path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in forbidden_names:
                offenders.append(f"{rel_path}: references {node.id!r}")
    assert not offenders, offenders


def test_backend_factory_does_not_import_ui_or_pipeline_modules():
    repo_root = Path(__file__).parent.parent
    path = repo_root / "src" / "data_access" / "backend_factory.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    forbidden_modules = ("src.ui", "streamlit", "src.data_access.daily_news")
    offenders = []
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for module in modules:
            if any(module == forbidden or module.startswith(forbidden + ".") for forbidden in forbidden_modules):
                offenders.append(module)
    assert not offenders, offenders


# ============================================================
# Part F — scope guards (proofs 15, 16)
# ============================================================


def test_no_new_dependency_added_to_requirements():
    repo_root = Path(__file__).parent.parent
    result = subprocess.run(["git", "diff", "HEAD", "--", "requirements.txt"], cwd=repo_root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    assert result.stdout.strip() == "", f"requirements.txt was modified: {result.stdout}"


def test_scope_guard_only_approved_step_3a_files_changed():
    """Runs against `git diff HEAD` — only meaningful in a real checkout
    with this step's changes present; spuriously fires while ANY other
    legitimate uncommitted change is present (including Phase 3 Step 2's
    own now-stale scope guard, which lists a narrower file set than this
    step legitimately touches) and resolves once committed — same
    documented convention as this repo's other phase-scoped scope
    guards."""
    repo_root = Path(__file__).parent.parent
    result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    allowed = {
        "src/data_access/comparison_store.py",
        "src/data_access/state_db/comparison_repository.py",
        "src/data_access/postgres_state_db/comparison_repository.py",
        "src/data_access/backend_factory.py",
        "tests/test_comparison_persistence.py",
        "tests/test_comparison_bulk_retrieval.py",
    }
    assert changed <= allowed, changed - allowed


def test_no_ui_pipeline_client_dependency_or_deployment_files_touched():
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
        "src/data_access/state_db/schema.py", "src/data_access/postgres_state_db/schema.py",
    }
    hit = {c for c in changed if c in forbidden_paths or any(c.startswith(p) for p in forbidden_prefixes)}
    assert not hit, hit
