"""state_db.identifier_repository — round-trip and key normalization
matching the existing EDGAR/DART resolver conventions. In-memory SQLite
only; no real cik_resolver/corp_code_resolver call, no network."""
from __future__ import annotations

from src.data_access.state_db import connection, identifier_repository, schema


def _conn():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    return conn


def test_upsert_and_load_round_trip_edgar_shaped_record():
    conn = _conn()
    record = identifier_repository.ResolvedIdentifierRecord(
        identifier="0000002488", display_name="ADVANCED MICRO DEVICES INC",
        resolution_method="SEC company_tickers.json + submissions cross-check",
        retrieved_at="2026-08-20T17:17:01+00:00",
    )
    identifier_repository.upsert_resolved_identifier(conn, "SEC EDGAR", "AMD", record)
    loaded = identifier_repository.load_resolved_identifiers(conn, "SEC EDGAR")
    assert loaded == {"AMD": record}


def test_upsert_and_load_round_trip_dart_shaped_record():
    conn = _conn()
    record = identifier_repository.ResolvedIdentifierRecord(
        identifier="00164779", display_name="SK하이닉스",
        resolution_method="OpenDART corpCode.xml", retrieved_at="2026-08-17T16:29:54+00:00",
    )
    identifier_repository.upsert_resolved_identifier(conn, "OpenDART / DART", "000660", record)
    loaded = identifier_repository.load_resolved_identifiers(conn, "OpenDART / DART")
    assert loaded == {"000660": record}


def test_lookup_key_uses_the_exact_ticker_string_supplied_no_normalization_here():
    # cik_resolver.py itself does the .upper().strip() normalization
    # before calling this layer — this repository stores/reads exactly
    # the key it's given, matching load_cached_ciks()'s own dict-keying
    # behavior (keyed verbatim by whatever ticker string was resolved).
    conn = _conn()
    record = identifier_repository.ResolvedIdentifierRecord(
        identifier="0001841925", display_name="indie Semiconductor, Inc.",
        resolution_method="SEC company_tickers.json + submissions cross-check", retrieved_at="2026-08-20T17:17:01+00:00",
    )
    identifier_repository.upsert_resolved_identifier(conn, "SEC EDGAR", "INDI", record)
    assert identifier_repository.get_resolved_identifier(conn, "SEC EDGAR", "INDI") == record
    assert identifier_repository.get_resolved_identifier(conn, "SEC EDGAR", "indi") is None


def test_re_resolving_the_same_key_overwrites_with_the_latest_result():
    conn = _conn()
    old = identifier_repository.ResolvedIdentifierRecord(
        identifier="0000002488", display_name="OLD NAME",
        resolution_method="SEC company_tickers.json + submissions cross-check", retrieved_at="2026-01-01T00:00:00+00:00",
    )
    new = identifier_repository.ResolvedIdentifierRecord(
        identifier="0000002488", display_name="ADVANCED MICRO DEVICES INC",
        resolution_method="SEC company_tickers.json + submissions cross-check", retrieved_at="2026-08-20T17:17:01+00:00",
    )
    identifier_repository.upsert_resolved_identifier(conn, "SEC EDGAR", "AMD", old)
    identifier_repository.upsert_resolved_identifier(conn, "SEC EDGAR", "AMD", new)
    loaded = identifier_repository.load_resolved_identifiers(conn, "SEC EDGAR")
    assert loaded == {"AMD": new}
    assert len(loaded) == 1  # no duplicate row


def test_identifiers_are_source_scoped_and_never_collide():
    conn = _conn()
    edgar_record = identifier_repository.ResolvedIdentifierRecord(
        identifier="0000000001", display_name="Edgar Co",
        resolution_method="SEC company_tickers.json + submissions cross-check", retrieved_at="2026-01-01T00:00:00+00:00",
    )
    dart_record = identifier_repository.ResolvedIdentifierRecord(
        identifier="00000001", display_name="Dart Co",
        resolution_method="OpenDART corpCode.xml", retrieved_at="2026-01-01T00:00:00+00:00",
    )
    # Same lookup_key string ("SAME") used on purpose by both sources.
    identifier_repository.upsert_resolved_identifier(conn, "SEC EDGAR", "SAME", edgar_record)
    identifier_repository.upsert_resolved_identifier(conn, "OpenDART / DART", "SAME", dart_record)
    assert identifier_repository.get_resolved_identifier(conn, "SEC EDGAR", "SAME") == edgar_record
    assert identifier_repository.get_resolved_identifier(conn, "OpenDART / DART", "SAME") == dart_record


def test_get_resolved_identifier_returns_none_when_absent():
    conn = _conn()
    assert identifier_repository.get_resolved_identifier(conn, "SEC EDGAR", "ZZZZ") is None
