"""Durable-State Phase 4B — derived-Signal behavior for the isolated
Postgres backend
(src/data_access/postgres_state_db/signal_repository.py), against the
real local disposable Postgres test container. Proves Signals exist
only for PUBLISHED candidates and are never persisted — reusing the
existing, unmodified src/logic/signal_promotion.py exactly as the
SQLite-backed SqliteSignalRepository does. Uses pg_conn (an isolated,
already-migrated schema) — see tests/_postgres_test_support.py."""
from __future__ import annotations

from src.data_access.postgres_state_db import candidate_repository
from src.data_access.postgres_state_db.signal_repository import PostgresSignalRepository
from src.models.models import CandidateSignal, CandidateStatus, FilingEvent, StateTransition

from tests._postgres_test_support import pg_conn, pg_isolated_connection  # noqa: F401


def _filing(rcept_no: str = "0001045810-26-000001") -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code="0000045810", corp_name="NVIDIA", stock_code="NVDA",
        report_nm="8-K", rcept_dt="20260101", flr_nm="NVIDIA", source_name="SEC EDGAR",
        original_language="English", theme_slug="ai-buildout",
    )


def _candidate(candidate_id: str, filing: FilingEvent, status: CandidateStatus) -> CandidateSignal:
    return CandidateSignal(
        id=candidate_id, filing=filing, matched_rules=["earnings"], confidence="High", status=status,
        state_history=[StateTransition(status=status, at="2026-01-01T00:00:00+00:00")],
    )


def test_no_signal_for_non_published_candidate(pg_conn):
    filing = _filing()
    candidate_repository.upsert_new_candidates(
        pg_conn, "SEC EDGAR", [_candidate("cand-1", filing, CandidateStatus.NEEDS_REVIEW)],
    )
    repo = PostgresSignalRepository(pg_conn)
    assert repo.get_all_signals() == []


def test_signal_exists_only_for_published_candidate(pg_conn):
    filing = _filing()
    candidate_repository.upsert_new_candidates(
        pg_conn, "SEC EDGAR", [_candidate("cand-1", filing, CandidateStatus.PUBLISHED)],
    )
    repo = PostgresSignalRepository(pg_conn)
    signals = repo.get_all_signals()
    assert len(signals) == 1
    assert signals[0].id == "signal-cand-1"
    assert signals[0].theme_slug == "ai-buildout"


def test_get_signals_for_theme_filters_correctly(pg_conn):
    filing_a = _filing(rcept_no="acc-a")
    filing_b = FilingEvent(
        rcept_no="acc-b", corp_code="0000320193", corp_name="Apple", stock_code="AAPL",
        report_nm="8-K", rcept_dt="20260101", flr_nm="Apple", source_name="SEC EDGAR",
        original_language="English", theme_slug="other-theme",
    )
    candidate_repository.upsert_new_candidates(
        pg_conn, "SEC EDGAR",
        [_candidate("cand-a", filing_a, CandidateStatus.PUBLISHED), _candidate("cand-b", filing_b, CandidateStatus.PUBLISHED)],
    )
    repo = PostgresSignalRepository(pg_conn)
    theme_signals = repo.get_signals_for_theme("ai-buildout")
    assert {s.id for s in theme_signals} == {"signal-cand-a"}


def test_no_signals_table_is_ever_written_to(pg_conn):
    """Structural proof, not just behavioral: this schema has no
    `signals` table at all (see schema.py) — a Signal genuinely cannot
    be persisted through this backend, only derived."""
    row = pg_conn.execute(
        "SELECT to_regclass('signals') AS reg"
    ).fetchone()
    assert row["reg"] is None
