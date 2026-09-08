"""state_db.filing_event_repository — round-trip and source-aware dedup
identity (source_name, corp_code, rcept_no). In-memory SQLite only."""
from __future__ import annotations

from datetime import datetime, timezone

from src.data_access.state_db import connection, filing_event_repository, schema
from src.models.models import FilingEvent


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    return conn


def _filing(**overrides) -> FilingEvent:
    defaults = dict(
        rcept_no="0001193125-26-354029", corp_code="0000002488", corp_name="Advanced Micro Devices",
        stock_code="AMD", report_nm="8-K", rcept_dt="2026-08-17", flr_nm="Advanced Micro Devices",
        pblntf_ty="8-K", theme_slug="ai-buildout", subtheme_slug="compute-accelerators",
        source_url="https://www.sec.gov/Archives/edgar/data/2488/0001193125-26-354029/",
        retrieved_at=_now(), source_name="SEC EDGAR", original_language="English", primary_document="ex99.htm",
    )
    defaults.update(overrides)
    return FilingEvent(**defaults)


def test_upsert_inserts_a_new_filing_event_and_returns_true():
    conn = _conn()
    filing = _filing()
    assert filing_event_repository.upsert_filing_event(conn, filing) is True
    assert filing_event_repository.get_filing_event(conn, "SEC EDGAR", "0000002488", "0001193125-26-354029") == filing


def test_upsert_of_an_already_present_filing_event_returns_false_and_does_not_duplicate():
    conn = _conn()
    filing = _filing()
    filing_event_repository.upsert_filing_event(conn, filing)
    assert filing_event_repository.upsert_filing_event(conn, filing) is False
    assert len(filing_event_repository.load_filing_events(conn, "SEC EDGAR")) == 1


def test_filing_event_exists_matches_the_scan_service_dedup_key_shape():
    conn = _conn()
    filing = _filing()
    assert filing_event_repository.filing_event_exists(conn, "SEC EDGAR", "0000002488", "0001193125-26-354029") is False
    filing_event_repository.upsert_filing_event(conn, filing)
    assert filing_event_repository.filing_event_exists(conn, "SEC EDGAR", "0000002488", "0001193125-26-354029") is True


def test_same_corp_code_and_rcept_no_different_source_are_distinct_records():
    conn = _conn()
    edgar_filing = _filing(source_name="SEC EDGAR", corp_code="0000000001", rcept_no="shared-id")
    dart_filing = _filing(source_name="OpenDART / DART", corp_code="0000000001", rcept_no="shared-id", stock_code="000660")
    filing_event_repository.upsert_filing_event(conn, edgar_filing)
    filing_event_repository.upsert_filing_event(conn, dart_filing)
    assert filing_event_repository.filing_event_exists(conn, "SEC EDGAR", "0000000001", "shared-id")
    assert filing_event_repository.filing_event_exists(conn, "OpenDART / DART", "0000000001", "shared-id")
    assert len(filing_event_repository.load_filing_events(conn, "SEC EDGAR")) == 1
    assert len(filing_event_repository.load_filing_events(conn, "OpenDART / DART")) == 1


def test_load_filing_events_is_source_scoped():
    conn = _conn()
    filing_event_repository.upsert_filing_event(conn, _filing(source_name="SEC EDGAR", corp_code="1", rcept_no="a"))
    filing_event_repository.upsert_filing_event(conn, _filing(source_name="EDINET", corp_code="E00001", rcept_no="b", stock_code="99840"))
    assert len(filing_event_repository.load_filing_events(conn, "SEC EDGAR")) == 1
    assert len(filing_event_repository.load_filing_events(conn, "EDINET")) == 1


def test_optional_and_additive_fields_round_trip_including_none_subtheme():
    conn = _conn()
    filing = _filing(subtheme_slug=None, ordinance_code="010")
    filing_event_repository.upsert_filing_event(conn, filing)
    reloaded = filing_event_repository.get_filing_event(conn, "SEC EDGAR", "0000002488", "0001193125-26-354029")
    assert reloaded.subtheme_slug is None
    assert reloaded.ordinance_code == "010"
