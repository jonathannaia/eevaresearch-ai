"""state_db.signal_repository — Signals stay derived from PUBLISHED
candidates, with the same ID scheme the JSON-backed
RadarSignalRepository already produces (signal-{candidate.id}). No
signals table exists (see test_state_db_schema.py); this proves the
derivation itself matches, not just the schema's absence of one."""
from __future__ import annotations

from datetime import datetime, timezone

from src.data_access.state_db import candidate_repository, connection, schema, signal_repository
from src.models.models import CandidateSignal, CandidateStatus, FilingEvent, StateTransition


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn():
    conn = connection.connect_in_memory()
    schema.migrate(conn)
    return conn


def _edgar_filing(rcept_no: str, corp_code: str = "0000002488") -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code=corp_code, corp_name="Advanced Micro Devices", stock_code="AMD",
        report_nm="8-K", rcept_dt="2026-08-17", flr_nm="Advanced Micro Devices", pblntf_ty="8-K",
        theme_slug="ai-buildout", source_url="https://example.com", retrieved_at=_now(),
        source_name="SEC EDGAR", original_language="English",
    )


def _dart_filing(rcept_no: str) -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code="00164779", corp_name="SK Hynix", stock_code="000660",
        report_nm="주요사항보고서", rcept_dt="20260819", flr_nm="SK하이닉스", theme_slug="memory",
        source_url="https://dart.fss.or.kr/", retrieved_at=_now(), source_name="OpenDART / DART",
    )


def test_published_candidate_derives_a_signal_with_the_expected_id_scheme():
    conn = _conn()
    filing = _edgar_filing("0001193125-26-354029")
    candidate = CandidateSignal(
        id="edgar-cand-0001193125-26-354029", filing=filing, matched_rules=["material_agreement:8-K item 1.01"],
        confidence="Moderate", status=CandidateStatus.CANDIDATE_DETECTED,
        state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at=_now())],
    )
    candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [candidate])
    version = candidate_repository.get_candidate_version(conn, candidate.id)
    published = CandidateSignal(
        id=candidate.id, filing=filing, matched_rules=candidate.matched_rules, confidence="Moderate",
        status=CandidateStatus.PUBLISHED,
        state_history=candidate.state_history + [StateTransition(status=CandidateStatus.PUBLISHED, at=_now())],
    )
    candidate_repository.update_candidate(conn, published, expected_version=version)

    repo = signal_repository.SqliteSignalRepository(conn)
    signals = repo.get_all_signals()
    assert [s.id for s in signals] == ["signal-edgar-cand-0001193125-26-354029"]
    assert signals[0].theme_slug == "ai-buildout"


def test_non_published_candidate_produces_no_signal():
    conn = _conn()
    filing = _edgar_filing("0001193125-26-999999")
    for status in (
        CandidateStatus.CANDIDATE_DETECTED, CandidateStatus.NEEDS_REVIEW,
        CandidateStatus.DISMISSED, CandidateStatus.MONITORING, CandidateStatus.NOT_MATERIAL,
    ):
        candidate = CandidateSignal(
            id=f"edgar-cand-status-{status.value}", filing=_edgar_filing(f"acc-{status.value}"),
            matched_rules=[], confidence="Low", status=status,
        )
        candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [candidate])

    repo = signal_repository.SqliteSignalRepository(conn)
    assert repo.get_all_signals() == []


def test_signals_span_all_three_sources():
    conn = _conn()
    edgar_candidate = CandidateSignal(
        id="edgar-cand-multi", filing=_edgar_filing("multi"), matched_rules=[], confidence="Moderate",
        status=CandidateStatus.PUBLISHED,
    )
    dart_candidate = CandidateSignal(
        id="cand-multi", filing=_dart_filing("multi"), matched_rules=[], confidence="Moderate",
        status=CandidateStatus.PUBLISHED,
    )
    candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [edgar_candidate])
    candidate_repository.upsert_new_candidates(conn, "OpenDART / DART", [dart_candidate])

    repo = signal_repository.SqliteSignalRepository(conn)
    ids = {s.id for s in repo.get_all_signals()}
    assert ids == {"signal-edgar-cand-multi", "signal-cand-multi"}


def test_get_signals_for_theme_filters_correctly():
    conn = _conn()
    candidate = CandidateSignal(
        id="edgar-cand-theme", filing=_edgar_filing("theme"), matched_rules=[], confidence="Moderate",
        status=CandidateStatus.PUBLISHED,
    )
    candidate_repository.upsert_new_candidates(conn, "SEC EDGAR", [candidate])
    repo = signal_repository.SqliteSignalRepository(conn)
    assert len(repo.get_signals_for_theme("ai-buildout")) == 1
    assert repo.get_signals_for_theme("memory") == []
