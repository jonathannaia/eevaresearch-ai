"""RadarSignalRepository — combines real DART + EDINET + EDGAR candidate
caches, filters to eligible statuses only, and never touches the demo
seed file. No Streamlit runtime; writes only to tmp_path, never the real
cache dir."""
from __future__ import annotations

from datetime import datetime, timezone

from src.config.settings import Settings
from src.data_access.dart import candidate_store
from src.data_access.edgar import edgar_pipeline
from src.data_access.edinet import edinet_pipeline
from src.data_access.live.radar_signal_repository import RadarSignalRepository
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, FilingEvent, StateTransition


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dart_filing(rcept_no: str, theme_slug: str = "memory") -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code="00164779", corp_name="SK Hynix", stock_code="000660",
        report_nm="조회공시요구(풍문또는보도)에대한답변(미확정)", rcept_dt="20260812", flr_nm="SK 하이닉스",
        theme_slug=theme_slug, source_url="https://dart.fss.or.kr/x", retrieved_at=_now_iso(),
        source_name="OpenDART / DART",
    )


def _edinet_filing(rcept_no: str, theme_slug: str = "ai-buildout") -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code="E02778", corp_name="SoftBank Group Corp.", stock_code="99840",
        report_nm="有価証券報告書", rcept_dt="2026-06-22", flr_nm="ソフトバンクグループ株式会社",
        theme_slug=theme_slug, source_url="https://api.edinet-fsa.go.jp/x", retrieved_at=_now_iso(),
        source_name="EDINET", original_language="Japanese",
    )


def _edgar_filing(rcept_no: str, theme_slug: str = "ai-buildout") -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code="0001045810", corp_name="NVIDIA", stock_code="NVDA",
        report_nm="8-K", rcept_dt="2026-08-17", flr_nm="NVIDIA", pblntf_ty="8-K",
        theme_slug=theme_slug, source_url=f"https://www.sec.gov/Archives/edgar/data/1045810/{rcept_no.replace('-', '')}/",
        retrieved_at=_now_iso(), source_name="SEC EDGAR", original_language="English",
        primary_document="nvda-20260817.htm",
    )


def _candidate(candidate_id: str, filing: FilingEvent, status: CandidateStatus, matched_rules: list[str]) -> CandidateSignal:
    return CandidateSignal(
        id=candidate_id, filing=filing, matched_rules=matched_rules, confidence="Moderate", status=status,
        extraction_state=ExtractionState.EXTRACTED if status in {CandidateStatus.EXTRACTED, CandidateStatus.NEEDS_REVIEW, CandidateStatus.PUBLISHED} else ExtractionState.NOT_FETCHED,
        excerpt_original="본문" if status in {CandidateStatus.EXTRACTED, CandidateStatus.NEEDS_REVIEW, CandidateStatus.PUBLISHED} else None,
        state_history=[StateTransition(status=status, at=_now_iso())],
    )


def test_combines_eligible_candidates_from_all_three_sources(tmp_path):
    dart_eligible = _candidate("cand-d1", _dart_filing("d1"), CandidateStatus.PUBLISHED, ["market_rumor_response:x:풍문"])
    dart_ineligible = _candidate("cand-d2", _dart_filing("d2"), CandidateStatus.PROCESSING_DEFERRED, ["earnings:x:실적"])
    dart_not_material = _candidate("cand-d3", _dart_filing("d3"), CandidateStatus.NOT_MATERIAL, ["earnings:x:실적"])
    candidate_store.save_candidates(tmp_path, {c.id: c for c in [dart_eligible, dart_ineligible, dart_not_material]}, "dart_candidates.json")

    edinet_eligible = _candidate("edinet-cand-e1", _edinet_filing("e1"), CandidateStatus.PUBLISHED, ["annual_securities_report:010:030000:120"])
    edinet_ineligible = _candidate("edinet-cand-e2", _edinet_filing("e2"), CandidateStatus.CANDIDATE_DETECTED, [])
    candidate_store.save_candidates(
        tmp_path, {c.id: c for c in [edinet_eligible, edinet_ineligible]}, edinet_pipeline.CANDIDATE_STORE_FILENAME,
    )

    edgar_eligible = _candidate("edgar-cand-g1", _edgar_filing("0001045810-26-000069"), CandidateStatus.PUBLISHED, ["earnings_or_results:8-K item 2.02"])
    edgar_ineligible = _candidate("edgar-cand-g2", _edgar_filing("0001045810-26-000070"), CandidateStatus.PROCESSING_DEFERRED, [])
    candidate_store.save_candidates(
        tmp_path, {c.id: c for c in [edgar_eligible, edgar_ineligible]}, edgar_pipeline.CANDIDATE_STORE_FILENAME,
    )

    repo = RadarSignalRepository(Settings(cache_dir=tmp_path))
    signals = repo.get_all_signals()

    assert {s.id for s in signals} == {"signal-cand-d1", "signal-edinet-cand-e1", "signal-edgar-cand-g1"}
    assert all(s.is_demo is False for s in signals)


def test_get_signals_for_theme_filters_correctly(tmp_path):
    memory_candidate = _candidate("cand-m1", _dart_filing("m1", theme_slug="memory"), CandidateStatus.PUBLISHED, ["earnings:x:실적"])
    ai_candidate = _candidate("cand-a1", _dart_filing("a1", theme_slug="ai-buildout"), CandidateStatus.PUBLISHED, ["earnings:x:실적"])
    candidate_store.save_candidates(tmp_path, {c.id: c for c in [memory_candidate, ai_candidate]}, "dart_candidates.json")

    repo = RadarSignalRepository(Settings(cache_dir=tmp_path))
    memory_signals = repo.get_signals_for_theme("memory")

    assert len(memory_signals) == 1
    assert memory_signals[0].id == "signal-cand-m1"


def test_no_caches_present_returns_empty_list(tmp_path):
    repo = RadarSignalRepository(Settings(cache_dir=tmp_path))
    assert repo.get_all_signals() == []
