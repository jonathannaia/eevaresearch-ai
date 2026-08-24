"""Durable-State Phase 4B — resolved-identifier upsert behavior for the
isolated Postgres backend
(src/data_access/postgres_state_db/identifier_repository.py), against
the real local disposable Postgres test container. Uses pg_conn (an
isolated, already-migrated schema) — see tests/_postgres_test_support.py."""
from __future__ import annotations

from src.data_access.postgres_state_db import identifier_repository

from tests._postgres_test_support import pg_conn, pg_isolated_connection  # noqa: F401


def _record(**overrides) -> identifier_repository.ResolvedIdentifierRecord:
    fields = dict(
        identifier="0000045810", display_name="NVIDIA",
        resolution_method="SEC company_tickers.json + submissions cross-check",
        retrieved_at="2026-01-01T00:00:00+00:00",
    )
    fields.update(overrides)
    return identifier_repository.ResolvedIdentifierRecord(**fields)


def test_get_resolved_identifier_returns_none_when_absent(pg_conn):
    assert identifier_repository.get_resolved_identifier(pg_conn, "SEC EDGAR", "NVDA") is None


def test_upsert_resolved_identifier_inserts_new_row(pg_conn):
    identifier_repository.upsert_resolved_identifier(pg_conn, "SEC EDGAR", "NVDA", _record())
    row = identifier_repository.get_resolved_identifier(pg_conn, "SEC EDGAR", "NVDA")
    assert row is not None
    assert row.identifier == "0000045810"
    assert row.display_name == "NVIDIA"


def test_upsert_resolved_identifier_refreshes_an_existing_row(pg_conn):
    identifier_repository.upsert_resolved_identifier(pg_conn, "SEC EDGAR", "NVDA", _record())
    identifier_repository.upsert_resolved_identifier(
        pg_conn, "SEC EDGAR", "NVDA", _record(display_name="NVIDIA Corporation", retrieved_at="2026-02-01T00:00:00+00:00"),
    )
    row = identifier_repository.get_resolved_identifier(pg_conn, "SEC EDGAR", "NVDA")
    assert row.display_name == "NVIDIA Corporation"
    assert row.retrieved_at == "2026-02-01T00:00:00+00:00"


def test_load_resolved_identifiers_scoped_by_source(pg_conn):
    identifier_repository.upsert_resolved_identifier(pg_conn, "SEC EDGAR", "NVDA", _record())
    identifier_repository.upsert_resolved_identifier(
        pg_conn, "OpenDART / DART", "005930", _record(identifier="00126380", display_name="Samsung Electronics"),
    )
    edgar_records = identifier_repository.load_resolved_identifiers(pg_conn, "SEC EDGAR")
    dart_records = identifier_repository.load_resolved_identifiers(pg_conn, "OpenDART / DART")
    assert set(edgar_records.keys()) == {"NVDA"}
    assert set(dart_records.keys()) == {"005930"}
