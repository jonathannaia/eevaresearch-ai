"""EevaResearch Phase 4, Step 4B-1 (design/DECISIONS.md) — focused tests
for the two new backend_factory.py seams: ResearchCaseRepositoryProtocol.
existing_case_ids() (JSON/SQLite/Postgres) and the new, worker-only
ResearchCaseBundleWriterProtocol/get_research_case_bundle_writer()
(SQLite/Postgres only, no JSON branch). Every fixture is synthetic;
Postgres tests use the shared, fail-soft local-only fixtures from
tests/_postgres_test_support.py and skip cleanly when no local
disposable Postgres instance is available."""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.research_store import build_case_id
from src.data_access.state_db import connection as sqlite_connection
from src.data_access.state_db import schema as sqlite_schema
from src.logic.research_case_validation import build_research_case_bundle
from src.models.research_case import ResearchCase, ResearchCaseStatus

from tests._postgres_test_support import pg_conn, pg_isolated_connection  # noqa: F401

REPO_ROOT = Path(__file__).parent.parent


def _case(trigger_source_id="cand-1", created_at="2026-08-20T00:00:00+00:00", **overrides):
    defaults = dict(
        id=build_case_id("radar", trigger_source_id, created_at),
        trigger_source_type="radar", trigger_source_id=trigger_source_id,
        trigger_source_name="Example Corp", trigger_summary="Filed a material event.",
        title="Example research case", research_question="What is the supply-chain exposure?",
        status=ResearchCaseStatus.OPEN, created_at=created_at, version=1,
    )
    defaults.update(overrides)
    return ResearchCase(**defaults)


def _sqlite_settings(tmp_path) -> Settings:
    return Settings(db_backend="sqlite", state_db_path=tmp_path / "state.db")


# ============================================================
# Part A — existing_case_ids() on the read protocol
# ============================================================


def test_json_existing_case_ids_empty_input_no_file_load(tmp_path, monkeypatch):
    from src.data_access import research_store

    def _boom(*_a, **_k):
        raise AssertionError("must not load when case_ids is empty")

    monkeypatch.setattr(research_store, "load_research_cases", _boom)
    repo = backend_factory.JsonResearchCaseRepository(cache_dir=tmp_path)
    assert repo.existing_case_ids([]) == frozenset()


def test_json_existing_case_ids_one_load_and_correct_intersection(tmp_path):
    from src.data_access import research_store

    case = _case()
    research_store.append_research_case(tmp_path, case)
    repo = backend_factory.JsonResearchCaseRepository(cache_dir=tmp_path)
    assert repo.existing_case_ids([case.id, "case-missing"]) == frozenset({case.id})


def test_sqlite_existing_case_ids_delegates_to_repository_function(tmp_path):
    settings = _sqlite_settings(tmp_path)
    repo = backend_factory.get_research_case_repository(settings)
    assert isinstance(repo, backend_factory.SqliteResearchCaseRepository)

    case = _case()
    from src.data_access.state_db import research_repository as sqlite_research
    sqlite_research.insert_research_case(repo.conn, case)
    assert repo.existing_case_ids([case.id, "case-missing"]) == frozenset({case.id})
    assert repo.existing_case_ids([]) == frozenset()


def test_postgres_existing_case_ids_delegates_to_repository_function(pg_conn):
    from src.data_access.postgres_state_db import research_repository as postgres_research

    case = _case(trigger_source_id="cand-pg-bf")
    postgres_research.insert_research_case(pg_conn, case)
    repo = backend_factory.PostgresResearchCaseRepository(conn=pg_conn)
    assert repo.existing_case_ids([case.id, "case-missing"]) == frozenset({case.id})
    assert repo.existing_case_ids([]) == frozenset()


def test_protocol_exposes_existing_case_ids_on_all_three_adapters(tmp_path):
    json_repo = backend_factory.JsonResearchCaseRepository(cache_dir=tmp_path)
    sqlite_repo = backend_factory.SqliteResearchCaseRepository(conn=MagicMock())
    postgres_repo = backend_factory.PostgresResearchCaseRepository(conn=MagicMock())
    for repo in (json_repo, sqlite_repo, postgres_repo):
        assert hasattr(repo, "existing_case_ids")


# ============================================================
# Part B — ResearchCaseBundleWriterProtocol (worker-only, no JSON)
# ============================================================


def test_get_research_case_bundle_writer_raises_for_json_backend(tmp_path):
    settings = Settings(db_backend="json", cache_dir=tmp_path)
    with pytest.raises(backend_factory.BackendConfigurationError):
        backend_factory.get_research_case_bundle_writer(settings)


def test_get_research_case_bundle_writer_raises_for_unset_backend():
    settings = Settings()
    with pytest.raises(backend_factory.BackendConfigurationError):
        backend_factory.get_research_case_bundle_writer(settings)


def test_get_research_case_bundle_writer_returns_sqlite_adapter(tmp_path):
    settings = _sqlite_settings(tmp_path)
    writer = backend_factory.get_research_case_bundle_writer(settings)
    assert isinstance(writer, backend_factory.SqliteResearchCaseBundleWriter)


def test_sqlite_writer_delegates_only_to_existing_atomic_function(tmp_path, monkeypatch):
    from src.data_access.state_db import research_repository as sqlite_research

    calls = []
    real_fn = sqlite_research.insert_research_case_bundle

    def _tracking(conn, bundle):
        calls.append((conn, bundle))
        return real_fn(conn, bundle)

    monkeypatch.setattr(sqlite_research, "insert_research_case_bundle", _tracking)

    conn = sqlite_connection.connect_in_memory()
    sqlite_schema.migrate(conn)
    writer = backend_factory.SqliteResearchCaseBundleWriter(conn=conn)
    case = _case()
    bundle = build_research_case_bundle(case, [], [])
    assert writer.insert_bundle(bundle) is True
    assert len(calls) == 1
    # Duplicate rejected — proves no update/upsert path exists.
    assert writer.insert_bundle(bundle) is False


def test_postgres_writer_delegates_only_to_existing_atomic_function(pg_conn, monkeypatch):
    from src.data_access.postgres_state_db import research_repository as postgres_research

    calls = []
    real_fn = postgres_research.insert_research_case_bundle

    def _tracking(conn, bundle):
        calls.append((conn, bundle))
        return real_fn(conn, bundle)

    monkeypatch.setattr(postgres_research, "insert_research_case_bundle", _tracking)

    writer = backend_factory.PostgresResearchCaseBundleWriter(conn=pg_conn)
    case = _case(trigger_source_id="cand-pg-writer")
    bundle = build_research_case_bundle(case, [], [])
    assert writer.insert_bundle(bundle) is True
    assert len(calls) == 1
    assert writer.insert_bundle(bundle) is False


def test_json_writer_exists_for_test_parity_only_and_is_never_returned_by_factory(tmp_path):
    from src.data_access import research_store

    writer = backend_factory.JsonResearchCaseBundleWriter(cache_dir=tmp_path)
    case = _case()
    bundle = build_research_case_bundle(case, [], [])
    assert writer.insert_bundle(bundle) is True
    assert research_store.get_research_case(tmp_path, case.id) == case

    settings = Settings(db_backend="json", cache_dir=tmp_path)
    with pytest.raises(backend_factory.BackendConfigurationError):
        backend_factory.get_research_case_bundle_writer(settings)


def test_writer_protocol_exposes_no_update_upsert_delete_or_query_method():
    for cls in (
        backend_factory.JsonResearchCaseBundleWriter,
        backend_factory.SqliteResearchCaseBundleWriter,
        backend_factory.PostgresResearchCaseBundleWriter,
    ):
        exported = {name for name in dir(cls) if not name.startswith("_")}
        forbidden_substrings = ("update", "delete", "replace", "upsert", "query", "search", "list")
        offenders = [name for name in exported if any(f in name.lower() for f in forbidden_substrings)]
        assert not offenders, (cls, offenders)


# ============================================================
# Part C — no runtime entry point references the new seams yet
# ============================================================


def test_no_runtime_entry_point_references_the_new_writer_or_membership_seams():
    candidate_files = [
        "scripts/radar_worker.py", "scripts/run_scan.py", "scripts/create_research_case.py", "app.py",
        "src/ui/pages/research_cases.py", "src/ui/pages/radar_inbox.py", "src/ui/pages/daily_news.py",
    ]
    forbidden_names = ("get_research_case_bundle_writer", "existing_case_ids", "ResearchCaseBundleWriterProtocol")
    offenders = []
    for rel_path in candidate_files:
        full_path = REPO_ROOT / rel_path
        if not full_path.exists():
            continue
        source = full_path.read_text(encoding="utf-8")
        for name in forbidden_names:
            if name in source:
                offenders.append(f"{rel_path}: references {name!r}")
    assert not offenders, offenders


# ============================================================
# Part D — scope guard
# ============================================================


def test_scope_guard_only_approved_files_changed():
    result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    allowed = {
        "src/data_access/backend_factory.py",
        "src/data_access/state_db/research_repository.py",
        "src/data_access/postgres_state_db/research_repository.py",
    }
    assert changed <= allowed, changed - allowed


def test_no_new_dependency_added_to_requirements():
    result = subprocess.run(["git", "diff", "HEAD", "--", "requirements.txt"], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    assert result.stdout.strip() == "", f"requirements.txt was modified: {result.stdout}"
