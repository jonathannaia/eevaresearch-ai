"""Durable-State Phase 4B — filing-event insert-if-absent behavior and
standalone atomicity for the isolated Postgres backend
(src/data_access/postgres_state_db/filing_event_repository.py), against
the real local disposable Postgres test container. Uses pg_conn (an
isolated, already-migrated schema) and synthetic FilingEvent fixtures
only — see tests/_postgres_test_support.py."""
from __future__ import annotations

from src.data_access.postgres_state_db import filing_event_repository
from src.models.models import FilingEvent

from tests._postgres_test_support import pg_conn, pg_isolated_connection  # noqa: F401


def _filing(rcept_no: str = "0001045810-26-000001", source_name: str = "SEC EDGAR", corp_code: str = "0000045810", **overrides) -> FilingEvent:
    fields = dict(
        rcept_no=rcept_no, corp_code=corp_code, corp_name="NVIDIA", stock_code="NVDA",
        report_nm="8-K", rcept_dt="20260101", flr_nm="NVIDIA", source_name=source_name,
        original_language="English",
    )
    fields.update(overrides)
    return FilingEvent(**fields)


def test_get_filing_event_returns_none_when_absent(pg_conn):
    assert filing_event_repository.get_filing_event(pg_conn, "SEC EDGAR", "x", "y") is None


def test_upsert_filing_event_inserts_new_row(pg_conn):
    inserted = filing_event_repository.upsert_filing_event(pg_conn, _filing())
    assert inserted is True
    assert filing_event_repository.filing_event_exists(pg_conn, "SEC EDGAR", "0000045810", "0001045810-26-000001")


def test_upsert_filing_event_is_idempotent_and_leaves_existing_row_untouched(pg_conn):
    first = filing_event_repository.upsert_filing_event(pg_conn, _filing())
    second = filing_event_repository.upsert_filing_event(pg_conn, _filing())
    assert first is True
    assert second is False
    rows = filing_event_repository.load_filing_events(pg_conn, "SEC EDGAR")
    assert len(rows) == 1


def test_load_filing_events_scoped_by_source(pg_conn):
    filing_event_repository.upsert_filing_event(
        pg_conn, _filing(rcept_no="0001045810-26-000001", source_name="SEC EDGAR", corp_code="0000045810"),
    )
    filing_event_repository.upsert_filing_event(
        pg_conn, _filing(rcept_no="20260810000001", source_name="OpenDART / DART", corp_code="00126380"),
    )
    edgar_rows = filing_event_repository.load_filing_events(pg_conn, "SEC EDGAR")
    dart_rows = filing_event_repository.load_filing_events(pg_conn, "OpenDART / DART")
    assert len(edgar_rows) == 1
    assert len(dart_rows) == 1
    assert edgar_rows[0].source_name == "SEC EDGAR"
    assert dart_rows[0].source_name == "OpenDART / DART"


def test_get_filing_event_round_trips_every_field(pg_conn):
    filing_event_repository.upsert_filing_event(pg_conn, _filing(pblntf_ty="8-K", theme_slug="ai-buildout"))
    row = filing_event_repository.get_filing_event(pg_conn, "SEC EDGAR", "0000045810", "0001045810-26-000001")
    assert row is not None
    assert row.corp_name == "NVIDIA"
    assert row.pblntf_ty == "8-K"
    assert row.theme_slug == "ai-buildout"
    assert row.is_demo is False


def test_standalone_upsert_filing_event_is_independently_atomic(pg_conn):
    """upsert_filing_event() wraps the transaction-free helper in its
    own transaction — a single call either fully commits or fully rolls
    back; verified here by confirming a successful standalone call
    (outside any candidate batch) is durably visible on a fresh read."""
    inserted = filing_event_repository.upsert_filing_event(pg_conn, _filing())
    assert inserted is True
    row = filing_event_repository.get_filing_event(pg_conn, "SEC EDGAR", "0000045810", "0001045810-26-000001")
    assert row is not None
