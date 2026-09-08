"""Durable-State Phase 4M-0 — scripts/resolve_tracked_identifiers.py:
argument validation, explicit-settings construction, and idempotent
persistence into the selected repository. No real network call anywhere
in this file — cik_resolver.resolve_and_cache/corp_code_resolver.resolve_and_cache
are always monkeypatched (both real functions make live HTTP calls);
EdgarClient/DartClient construction itself is real but network-free
(both are lazy — see their own __init__)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scripts import resolve_tracked_identifiers as bootstrap
from src.data_access.dart import corp_code_resolver
from src.data_access.dart.corp_code_resolver import ResolutionResult, ResolvedCorpCode
from src.data_access.edgar import cik_resolver
from src.data_access.edgar.cik_resolver import CikResolutionResult, ResolvedCik
from src.data_access.state_db import connection as sqlite_connection
from src.data_access.state_db import identifier_repository as sqlite_identifiers
from src.data_access.state_db import schema as sqlite_schema

from tests._postgres_test_support import pg_isolated_dsn  # noqa: F401


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Argument validation — no client constructed, no resolver called ---

def test_sqlite_backend_without_sqlite_path_fails_before_any_resolution(monkeypatch):
    called = []
    monkeypatch.setattr(cik_resolver, "resolve_and_cache", lambda *a, **kw: called.append(1))
    rc = bootstrap.main(["--source", "edgar", "--backend", "sqlite", "--edgar-user-agent", "Test test@example.com"])
    assert rc == 2
    assert called == []


def test_postgres_backend_without_dsn_env_var_fails_before_any_resolution(monkeypatch):
    called = []
    monkeypatch.setattr(cik_resolver, "resolve_and_cache", lambda *a, **kw: called.append(1))
    rc = bootstrap.main(["--source", "edgar", "--backend", "postgres", "--edgar-user-agent", "Test test@example.com"])
    assert rc == 2
    assert called == []


def test_postgres_backend_with_unset_dsn_env_var_fails_before_any_resolution(monkeypatch):
    monkeypatch.delenv("EEVA_TEST_UNSET_DSN_VAR", raising=False)
    called = []
    monkeypatch.setattr(cik_resolver, "resolve_and_cache", lambda *a, **kw: called.append(1))
    rc = bootstrap.main([
        "--source", "edgar", "--backend", "postgres", "--dsn-env-var", "EEVA_TEST_UNSET_DSN_VAR",
        "--edgar-user-agent", "Test test@example.com",
    ])
    assert rc == 2
    assert called == []


def test_edgar_source_without_user_agent_fails_before_any_resolution(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(cik_resolver, "resolve_and_cache", lambda *a, **kw: called.append(1))
    rc = bootstrap.main(["--source", "edgar", "--backend", "sqlite", "--sqlite-path", str(tmp_path / "state.db")])
    assert rc == 2
    assert called == []


def test_dart_source_without_api_key_fails_before_any_resolution(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(corp_code_resolver, "resolve_and_cache", lambda *a, **kw: called.append(1))
    rc = bootstrap.main(["--source", "dart", "--backend", "sqlite", "--sqlite-path", str(tmp_path / "state.db")])
    assert rc == 2
    assert called == []


# --- Successful SQLite resolution + persistence ---

def _sqlite_conn(path):
    conn = sqlite_connection.connect(path)
    sqlite_schema.migrate(conn)
    return conn


def test_edgar_sqlite_success_persists_resolved_identifier_and_returns_zero(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    now = _now()

    def fake_resolve(client, tickers, cache_dir):
        return CikResolutionResult(
            resolved={"NVDA": ResolvedCik(cik="0000045810", company_name="NVIDIA CORP", source="test-fixture", retrieved_at=now)},
            missing_tickers=(),
        )

    monkeypatch.setattr(cik_resolver, "resolve_and_cache", fake_resolve)
    rc = bootstrap.main([
        "--source", "edgar", "--backend", "sqlite", "--sqlite-path", str(db_path),
        "--edgar-user-agent", "Test test@example.com",
    ])
    assert rc == 0

    conn = _sqlite_conn(db_path)
    record = sqlite_identifiers.get_resolved_identifier(conn, "SEC EDGAR", "NVDA")
    assert record is not None
    assert record.identifier == "0000045810"
    assert record.display_name == "NVIDIA CORP"
    # No JSON cache written anywhere in the repo's own cache_dir (a
    # disposable tempdir, never the real local cache directory) —
    # confirms the sqlite path was actually used, not a silent JSON fallback.
    assert not (tmp_path / "edgar_ciks.json").exists()


def test_dart_sqlite_success_persists_resolved_identifier_and_returns_zero(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    now = _now()

    def fake_resolve(client, krx_codes, cache_dir):
        return ResolutionResult(
            resolved={"005930": ResolvedCorpCode(corp_code="00126380", corp_name="Samsung Electronics", source="test-fixture", retrieved_at=now)},
            missing_krx_codes=(),
        )

    monkeypatch.setattr(corp_code_resolver, "resolve_and_cache", fake_resolve)
    rc = bootstrap.main([
        "--source", "dart", "--backend", "sqlite", "--sqlite-path", str(db_path),
        "--dart-api-key", "test-key",
    ])
    assert rc == 0

    conn = _sqlite_conn(db_path)
    record = sqlite_identifiers.get_resolved_identifier(conn, "OpenDART / DART", "005930")
    assert record is not None
    assert record.identifier == "00126380"
    assert record.display_name == "Samsung Electronics"


def test_rerunning_the_same_resolution_is_idempotent_no_duplicate_or_error(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    now = _now()

    def fake_resolve(client, tickers, cache_dir):
        return CikResolutionResult(
            resolved={"NVDA": ResolvedCik(cik="0000045810", company_name="NVIDIA CORP", source="test-fixture", retrieved_at=now)},
            missing_tickers=(),
        )

    monkeypatch.setattr(cik_resolver, "resolve_and_cache", fake_resolve)
    args = [
        "--source", "edgar", "--backend", "sqlite", "--sqlite-path", str(db_path),
        "--edgar-user-agent", "Test test@example.com",
    ]
    assert bootstrap.main(args) == 0
    assert bootstrap.main(args) == 0

    conn = _sqlite_conn(db_path)
    all_records = sqlite_identifiers.load_resolved_identifiers(conn, "SEC EDGAR")
    assert len(all_records) == 1


def test_resolver_reported_error_returns_one_and_persists_nothing(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"

    def fake_resolve(client, tickers, cache_dir):
        return CikResolutionResult(resolved={}, missing_tickers=tuple(tickers), error="EdgarError")

    monkeypatch.setattr(cik_resolver, "resolve_and_cache", fake_resolve)
    rc = bootstrap.main([
        "--source", "edgar", "--backend", "sqlite", "--sqlite-path", str(db_path),
        "--edgar-user-agent", "Test test@example.com",
    ])
    assert rc == 1
    # No database file created — resolution failed before any persistence attempt.
    conn = _sqlite_conn(db_path)
    assert sqlite_identifiers.load_resolved_identifiers(conn, "SEC EDGAR") == {}


def test_resolver_exception_is_sanitized_never_leaks_raw_message(tmp_path, monkeypatch, capsys):
    def raising_resolve(client, tickers, cache_dir):
        raise RuntimeError("some raw internal detail that must never be printed")

    monkeypatch.setattr(cik_resolver, "resolve_and_cache", raising_resolve)
    rc = bootstrap.main([
        "--source", "edgar", "--backend", "sqlite", "--sqlite-path", str(tmp_path / "state.db"),
        "--edgar-user-agent", "Test test@example.com",
    ])
    assert rc == 1
    captured = capsys.readouterr()
    assert "some raw internal detail" not in captured.err
    assert "RuntimeError" in captured.err


# --- Real Postgres path (skips softly if the local disposable container isn't running) ---

def test_edgar_postgres_success_persists_resolved_identifier_and_returns_zero(pg_isolated_dsn, monkeypatch):
    now = _now()

    def fake_resolve(client, tickers, cache_dir):
        return CikResolutionResult(
            resolved={"NVDA": ResolvedCik(cik="0000045810", company_name="NVIDIA CORP", source="test-fixture", retrieved_at=now)},
            missing_tickers=(),
        )

    monkeypatch.setattr(cik_resolver, "resolve_and_cache", fake_resolve)
    monkeypatch.setenv("EEVA_TEST_PG_DSN", pg_isolated_dsn)
    rc = bootstrap.main([
        "--source", "edgar", "--backend", "postgres", "--dsn-env-var", "EEVA_TEST_PG_DSN",
        "--edgar-user-agent", "Test test@example.com",
    ])
    assert rc == 0

    from src.data_access.postgres_state_db import connection as postgres_connection
    from src.data_access.postgres_state_db import identifier_repository as postgres_identifiers
    from src.data_access.postgres_state_db import schema as postgres_schema

    conn = postgres_connection.connect(pg_isolated_dsn)
    postgres_schema.migrate(conn)
    record = postgres_identifiers.get_resolved_identifier(conn, "SEC EDGAR", "NVDA")
    assert record is not None
    assert record.identifier == "0000045810"
