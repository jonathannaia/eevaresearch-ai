"""Radar Inbox page — fixture-driven render tests via AppTest, with
radar_inbox.get_settings monkeypatched to a tmp cache_dir seeded with
fixture data. Zero network calls, no real API key, and the real
data/cache/ (gitignored live pilot cache) is never touched."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.dart import candidate_store, retry_policy
from src.models.models import (
    CandidateSignal,
    CandidateStatus,
    EvidenceLocation,
    ExtractionState,
    FilingEvent,
    FlagReason,
    LocationKind,
    StateTransition,
    Translation,
    TranslationState,
)

_HARNESS = Path(__file__).parent / "apptest_pages" / "radar_inbox_page.py"


@pytest.fixture(autouse=True)
def _clear_dashboard_snapshot_cache():
    """Durable-State Phase 4M-2 — `radar_inbox._load_dashboard_snapshot`
    is an `st.cache_data`-backed function, so its cache is a process-wide
    singleton that would otherwise persist across tests in the same
    pytest run. Most tests here use a unique `tmp_path` as `cache_dir`
    (itself part of the cache key), which already isolates them from
    each other — but this fixture makes that isolation explicit and
    load-bearing rather than incidental, and protects any test whose
    `Settings` omits `cache_dir` (falling back to the real default path,
    which multiple tests could otherwise collide on)."""
    from src.ui.pages.radar_inbox import _load_dashboard_snapshot

    _load_dashboard_snapshot.clear()
    yield
    _load_dashboard_snapshot.clear()


@pytest.fixture(autouse=True)
def _guard_against_live_calls(monkeypatch):
    """No test in this file clicks a scan/process control — this just
    makes that guarantee load-bearing rather than incidental. Requirement
    6e: no scan/process service may be called during any render or test."""

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("Test attempted a live call — this suite must stay network-free.")

    for module_path, attr in [
        ("src.data_access.dart.radar_service", "run_scan"),
        ("src.data_access.dart.radar_service", "process_candidate_now"),
        ("src.data_access.edgar.edgar_service", "run_scan"),
        ("src.data_access.edgar.edgar_service", "process_candidate_now"),
        ("src.data_access.edinet.edinet_service", "run_scan"),
        ("src.data_access.edinet.edinet_service", "process_candidate_now"),
    ]:
        monkeypatch.setattr(f"{module_path}.{attr}", _forbidden, raising=True)


def _seed_corp_codes(cache_dir) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "005930": {"corp_code": "00126380", "corp_name": "삼성전자", "source": "OpenDART corpCode.xml", "retrieved_at": "2026-08-01T00:00:00+00:00"},
        "000660": {"corp_code": "00164779", "corp_name": "SK 하이닉스", "source": "OpenDART corpCode.xml", "retrieved_at": "2026-08-01T00:00:00+00:00"},
    }
    (cache_dir / "dart_corp_codes.json").write_text(json.dumps(payload), encoding="utf-8")


def _filing(rcept_no: str, report_nm: str) -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code="00126380", corp_name="삼성전자", stock_code="005930",
        report_nm=report_nm, rcept_dt="20260812", flr_nm="삼성전자", theme_slug="memory",
        source_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
    )


def _seed_filing_events(cache_dir, filings: list[FilingEvent]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    from dataclasses import asdict
    payload = {"seen_receipt_numbers": [f.rcept_no for f in filings], "filing_events": [asdict(f) for f in filings], "candidate_signals": []}
    (cache_dir / "dart_filing_events.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _unconfigured_settings(cache_dir) -> Settings:
    # Every source-readiness field radar_inbox.py reads is explicitly
    # nulled here — this must represent "all sources unconfigured"
    # regardless of what the developer's own local .env holds. A field
    # left out of this call falls back to Settings' own
    # os.getenv-backed default_factory, which is exactly the isolation
    # gap that let a real local EDGE_EDINET_SUBSCRIPTION_KEY leak into
    # this fixture (see design/DECISIONS.md's Gate 5.1 entry).
    return Settings(
        dart_api_key=None, translation_api_key=None, edgar_user_agent=None,
        edinet_subscription_key=None, cache_dir=cache_dir,
    )


def test_radar_inbox_renders_missing_configuration_state(tmp_path):
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=_unconfigured_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "not configured" in all_text.lower()


def _seed_edinet_filing_events(cache_dir, filings: list[FilingEvent]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    from dataclasses import asdict
    payload = {"seen_keys": [f"EDINET:{f.corp_code}:{f.rcept_no}" for f in filings], "filing_events": [asdict(f) for f in filings], "candidate_signals": []}
    (cache_dir / "edinet_filing_events.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_radar_inbox_shows_a_candidate_less_edinet_filing_directly(tmp_path):
    # Radar simplicity workstream: the public card no longer shows
    # ordinance/form/docType codes at all (technical/process metadata,
    # not one of the 5 approved fields). Unify-Radar-into-Latest-Filings
    # pass: a filing is never hidden for lacking a CandidateSignal — this
    # now only proves a candidate-less EDINET event renders on the one
    # unified feed with no view switch needed.
    softbank_filing = FilingEvent(
        rcept_no="S100YGH5", corp_code="E02778", corp_name="SoftBank Group Corp.", stock_code="99840",
        report_nm="有価証券報告書－第46期(2025/04/01－2026/03/31)", rcept_dt="2026-06-22",
        flr_nm="ソフトバンクグループ株式会社", pblntf_ty="030000", pblntf_detail_ty="120", ordinance_code="010",
        theme_slug="ai-buildout", source_url="https://api.edinet-fsa.go.jp/api/v2/documents/S100YGH5",
        retrieved_at=datetime.now(timezone.utc).isoformat(), source_name="EDINET", original_language="Japanese",
    )
    _seed_edinet_filing_events(tmp_path, [softbank_filing])

    settings = Settings(
        dart_api_key=None, translation_api_key="test-key", edgar_user_agent=None,
        edinet_subscription_key="test-key", cache_dir=tmp_path,
    )
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    # This filing has no CandidateSignal, but the unified feed renders it
    # directly all the same — never hidden for lacking a classification.
    all_text = " ".join(m.value for m in at.markdown)
    assert "有価証券報告書" in all_text  # the bare event's own title


def test_radar_inbox_missing_configuration_state_is_unaffected_by_local_env(tmp_path, monkeypatch):
    # Regression for the Gate 5 test-isolation defect: a real, locally
    # configured EDGE_EDINET_SUBSCRIPTION_KEY (or any other real
    # provider credential) must never be able to make this "everything
    # unconfigured" fixture report as configured. monkeypatch.setenv is
    # scoped to this test only and never touches the real .env file;
    # every field _unconfigured_settings() passes to Settings(...) is
    # explicit, so these env values are never actually read for this
    # assertion — that's the isolation property this test proves.
    monkeypatch.setenv("EDGE_EDINET_SUBSCRIPTION_KEY", "a-real-locally-configured-value")
    monkeypatch.setenv("EDGE_EDGAR_USER_AGENT", "EevaResearch test@example.com")
    monkeypatch.setenv("EDGE_DART_API_KEY", "a-real-dart-key")
    monkeypatch.setenv("EDGE_TRANSLATION_API_KEY", "a-real-translation-key")

    with patch("src.ui.pages.radar_inbox.get_settings", return_value=_unconfigured_settings(tmp_path)):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "not configured" in all_text.lower()


def test_radar_inbox_renders_populated_list_with_expected_statuses(tmp_path):
    _seed_corp_codes(tmp_path)

    new_filing = _filing("20260812000001", "일반 공고")
    needs_review_filing = _filing("20260812000002", "신규시설투자등 결정")
    deferred_filing = _filing("20260812000003", "유상증자 결정")
    retry_exhausted_filing = _filing("20260812000004", "타법인주식및출자증권취득")
    _seed_filing_events(tmp_path, [new_filing, needs_review_filing, deferred_filing, retry_exhausted_filing])

    needs_review = CandidateSignal(
        id="cand-1", filing=needs_review_filing, matched_rules=["capex_or_facility_investment:facility_investment:신규시설투자"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        translation_state=TranslationState.TRANSLATED, excerpt_original="신규시설투자등 관련 원문",
        title_translation=Translation(translated_text="New facility investment decision", provider="DeepL", source_lang="ko", target_lang="en", translated_at=_now_iso()),
        excerpt_translation=Translation(translated_text="New facility investment original text", provider="DeepL", source_lang="ko", target_lang="en", translated_at=_now_iso()),
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    deferred = CandidateSignal(
        id="cand-2", filing=deferred_filing, matched_rules=["financing:capital_raise_or_treasury_stock:유상증자"],
        confidence="Moderate", status=CandidateStatus.PROCESSING_DEFERRED,
        state_history=[StateTransition(status=CandidateStatus.PROCESSING_DEFERRED, at=_now_iso(), detail="Scan processing budget reached.")],
    )
    exhausted_attempts = [StateTransition(status=CandidateStatus.QUEUED_FOR_PROCESSING, at=_now_iso()) for _ in range(retry_policy.MAX_RETRY_ATTEMPTS)]
    retry_exhausted = CandidateSignal(
        id="cand-3", filing=retry_exhausted_filing, matched_rules=["equity_or_jv_investment:equity_stake_or_investment_decision:타법인주식및출자증권취득"],
        confidence="Moderate", status=CandidateStatus.RETRIEVAL_FAILED, state_history=exhausted_attempts,
    )
    candidate_store.save_candidates(tmp_path, {c.id: c for c in (needs_review, deferred, retry_exhausted)})

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    # Unify-Radar-into-Latest-Filings pass: the unified feed shows both
    # the 3 candidates and `new_filing` — a bare FilingEvent with no
    # CandidateSignal — together, in the one and only render. Radar
    # simplicity workstream: the public card shows no status pill or
    # failure note.
    all_text = " ".join(m.value for m in at.markdown)
    assert "일반 공고" in all_text  # the bare event's own title
    assert "New facility investment decision" in all_text  # English title translation
    # Radar layout correction: the stored excerpt translation is behind a
    # collapsed, display-only toggle by default — its own expand/collapse
    # behavior is covered by test_radar_card_public_contract.py; this
    # test only confirms one is offered here.
    assert any(b.label == "Show English translation" for b in at.button)


def test_radar_inbox_routine_ownership_candidate_shows_no_materiality_label(tmp_path):
    # Radar simplicity workstream: "Potential materiality" and every other
    # workflow-status label (including "Not material · routine ownership
    # update") are removed from the public card entirely — the underlying
    # materiality_assessment field is untouched (see models.py), just
    # never rendered here.
    _seed_corp_codes(tmp_path)
    ownership_filing = _filing("20260812000009", "주식등의대량보유상황보고서(일반)")
    _seed_filing_events(tmp_path, [ownership_filing])

    routine_candidate = CandidateSignal(
        id="cand-routine", filing=ownership_filing,
        matched_rules=["ownership_change:major_shareholder_change:대량보유상황보고서"],
        confidence="Moderate", status=CandidateStatus.NOT_MATERIAL, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="직전 보고서 1,000,000,000 19.69 이번 보고서 1,000,000,500 19.69",
        materiality_assessment="Not material · routine ownership update",
        state_history=[StateTransition(status=CandidateStatus.NOT_MATERIAL, at=_now_iso(), detail="Not material · routine ownership update")],
    )
    candidate_store.save_candidates(tmp_path, {routine_candidate.id: routine_candidate})

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "주식등의대량보유상황보고서" in all_text  # the card itself still renders
    assert "Not material" not in all_text
    assert "routine ownership update" not in all_text
    # Nothing here claims a broader market-conviction/investment reading.
    assert "market conviction" not in all_text.lower()
    assert "investment confidence" not in all_text.lower()


def _seed_edgar_ciks(cache_dir) -> None:
    # Seeds every currently-tracked EDGAR company (src/config/
    # tracked_companies.py) with a synthetic CIK, not just a fixed
    # historical subset — edgar_readiness.ready requires every tracked
    # company resolved, so a hardcoded partial list silently rots (and
    # silently stops mattering) every time a new EDGAR company is added
    # to the real registry, the same class of test-isolation drift
    # documented elsewhere in this file for EDINET's own real env key.
    from src.config.tracked_companies import get_tracked_companies_for_source

    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        c.krx_code: {"cik": f"{i:010d}", "company_name": c.name.upper(), "source": "test", "retrieved_at": "2026-08-01T00:00:00+00:00"}
        for i, c in enumerate(get_tracked_companies_for_source("SEC EDGAR"), start=1)
    }
    (cache_dir / "edgar_ciks.json").write_text(json.dumps(payload), encoding="utf-8")


def test_radar_inbox_edgar_only_configured_renders_edgar_candidates(tmp_path):
    _seed_edgar_ciks(tmp_path)

    edgar_filing = FilingEvent(
        rcept_no="0001045810-26-000001", corp_code="0001045810", corp_name="NVIDIA", stock_code="NVDA",
        report_nm="8-K filing", rcept_dt="2026-08-12", flr_nm="NVIDIA", pblntf_ty="8-K", theme_slug="ai-buildout",
        source_url="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000001/",
        retrieved_at=_now_iso(), source_name="SEC EDGAR", original_language="English", primary_document="nvda-8k.htm",
    )
    payload = {
        "seen_keys": [f"SEC EDGAR:0001045810:{edgar_filing.rcept_no}"],
        "filing_events": [
            {
                "rcept_no": edgar_filing.rcept_no, "corp_code": edgar_filing.corp_code, "corp_name": edgar_filing.corp_name,
                "stock_code": edgar_filing.stock_code, "report_nm": edgar_filing.report_nm, "rcept_dt": edgar_filing.rcept_dt,
                "flr_nm": edgar_filing.flr_nm, "pblntf_ty": edgar_filing.pblntf_ty, "pblntf_detail_ty": "",
                "theme_slug": edgar_filing.theme_slug, "subtheme_slug": None, "source_url": edgar_filing.source_url,
                "retrieved_at": edgar_filing.retrieved_at, "source_name": edgar_filing.source_name,
                "original_language": edgar_filing.original_language, "is_demo": False,
                "primary_document": edgar_filing.primary_document,
            }
        ],
        "candidate_signals": [],
    }
    (tmp_path / "edgar_filing_events.json").write_text(json.dumps(payload), encoding="utf-8")

    edgar_candidate = CandidateSignal(
        id="edgar-cand-0001045810-26-000001", filing=edgar_filing,
        matched_rules=["earnings_or_results:8-K item 2.02"], confidence="Moderate",
        status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        translation_state=TranslationState.NOT_REQUESTED, excerpt_original="Item 2.02 Results of Operations. Revenue increased.",
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {edgar_candidate.id: edgar_candidate}, "edgar_candidates.json")

    # edinet_subscription_key is explicitly None here — omitting it would
    # let a real locally-configured EDGE_EDINET_SUBSCRIPTION_KEY leak in
    # via Settings' own os.getenv default and (now that EdinetReadiness
    # also checks the translation key) mask what this test is actually
    # proving, which is that EDGAR alone is sufficient to render.
    settings = Settings(
        dart_api_key=None, translation_api_key=None, edgar_user_agent="EevaResearch test@example.com",
        edinet_subscription_key=None, cache_dir=tmp_path,
    )
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "NVIDIA" in all_text
    assert "Revenue increased" in all_text  # English excerpt shown directly, no Original/translation duplication
    # No translation UI leaks in for an EDGAR (native-English) candidate.
    assert "Original</strong>" not in all_text
    assert "English translation</strong>" not in all_text
    # DART is unconfigured here — its scope line must not render.
    assert "OpenDART / DART · Samsung" not in all_text


def test_radar_inbox_bare_event_shows_native_title_only_no_fabricated_translation(tmp_path):
    # Radar simplicity workstream: a bare FilingEvent with no
    # CandidateSignal has never had anything extracted or translated —
    # the card shows its native title as "Original" and no English
    # translation section at all, never a fabricated one.
    _seed_corp_codes(tmp_path)
    bare_filing = _filing("20260812000011", "단순 공시")
    _seed_filing_events(tmp_path, [bare_filing])

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        # No candidate anywhere in this fixture — the unified feed still
        # renders the bare event's own card directly.

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "단순 공시" in all_text
    assert "English translation</strong>" not in all_text
    assert "being prepared" not in all_text
    # Requirement 4: never fabricate excerpt/translation/extraction/status
    # for a bare event.
    assert "Extraction state" not in all_text
    assert "Excerpt quality" not in all_text
    assert "English translation" not in all_text


def test_radar_inbox_paginates_at_twenty_cards(tmp_path):
    _seed_corp_codes(tmp_path)
    filings = [_filing(f"2026081200{i:04d}", f"공시 {i}") for i in range(25)]
    _seed_filing_events(tmp_path, filings)  # no candidates — all bare events

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

        assert not at.exception
        assert len(at.get("link_button")) == 20  # never more than PAGE_SIZE cards rendered
        all_text = " ".join(m.value for m in at.markdown)
        assert "Page 1 of 2" in all_text
        assert "25 of 25 items" in all_text

        next_buttons = [b for b in at.button if b.label == "Next →"]
        assert len(next_buttons) == 1
        next_buttons[0].click()
        at.run()

        assert not at.exception
        assert len(at.get("link_button")) == 5  # the remaining 5 items on page 2
        all_text = " ".join(m.value for m in at.markdown)
        assert "Page 2 of 2" in all_text


def test_radar_inbox_filter_change_resets_pagination_to_page_one(tmp_path):
    _seed_corp_codes(tmp_path)
    filings = [_filing(f"2026081200{i:04d}", f"공시 {i}") for i in range(25)]
    _seed_filing_events(tmp_path, filings)

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        [b for b in at.button if b.label == "Next →"][0].click()
        at.run()
        assert "Page 2 of 2" in " ".join(m.value for m in at.markdown)

        # Typing a search query is a filter change — pagination must clamp
        # back to page 1 rather than staying on a now out-of-range page.
        at.text_input(key="radar-filter-search").set_value("공시 1")
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "Page 1 of" in all_text


def test_radar_inbox_search_filter_narrows_to_matching_company_or_title(tmp_path):
    _seed_corp_codes(tmp_path)
    match_filing = _filing("20260812000020", "특별한 제목")
    other_filing = _filing("20260812000021", "다른 제목")
    _seed_filing_events(tmp_path, [match_filing, other_filing])
    match_candidate = CandidateSignal(
        id="cand-search-1", filing=match_filing, matched_rules=["x:y:z"], confidence="Moderate",
        status=CandidateStatus.NEEDS_REVIEW, state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    other_candidate = CandidateSignal(
        id="cand-search-2", filing=other_filing, matched_rules=["x:y:z"], confidence="Moderate",
        status=CandidateStatus.NEEDS_REVIEW, state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {match_candidate.id: match_candidate, other_candidate.id: other_candidate})

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        at.text_input(key="radar-filter-search").set_value("특별한")
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "특별한 제목" in all_text
    assert "다른 제목" not in all_text


def test_radar_inbox_source_filter_narrows_across_configured_sources(tmp_path):
    _seed_corp_codes(tmp_path)
    _seed_edgar_ciks(tmp_path)

    dart_filing = _filing("20260812000030", "국문 공시")
    _seed_filing_events(tmp_path, [dart_filing])
    dart_candidate = CandidateSignal(
        id="cand-dart-src", filing=dart_filing, matched_rules=["x:y:z"], confidence="Moderate",
        status=CandidateStatus.NEEDS_REVIEW, state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {dart_candidate.id: dart_candidate})

    edgar_filing = FilingEvent(
        rcept_no="0001045810-26-000002", corp_code="0001045810", corp_name="NVIDIA", stock_code="NVDA",
        report_nm="8-K filing two", rcept_dt="2026-08-12", flr_nm="NVIDIA", pblntf_ty="8-K", theme_slug="ai-buildout",
        source_url="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000002/",
        retrieved_at=_now_iso(), source_name="SEC EDGAR", original_language="English", primary_document="nvda-8k-2.htm",
    )
    edgar_payload = {
        "seen_keys": [f"SEC EDGAR:0001045810:{edgar_filing.rcept_no}"],
        "filing_events": [
            {
                "rcept_no": edgar_filing.rcept_no, "corp_code": edgar_filing.corp_code, "corp_name": edgar_filing.corp_name,
                "stock_code": edgar_filing.stock_code, "report_nm": edgar_filing.report_nm, "rcept_dt": edgar_filing.rcept_dt,
                "flr_nm": edgar_filing.flr_nm, "pblntf_ty": edgar_filing.pblntf_ty, "pblntf_detail_ty": "",
                "theme_slug": edgar_filing.theme_slug, "subtheme_slug": None, "source_url": edgar_filing.source_url,
                "retrieved_at": edgar_filing.retrieved_at, "source_name": edgar_filing.source_name,
                "original_language": edgar_filing.original_language, "is_demo": False,
                "primary_document": edgar_filing.primary_document,
            }
        ],
        "candidate_signals": [],
    }
    (tmp_path / "edgar_filing_events.json").write_text(json.dumps(edgar_payload), encoding="utf-8")
    edgar_candidate = CandidateSignal(
        id="edgar-cand-src", filing=edgar_filing, matched_rules=["earnings_or_results:8-K item 2.02"], confidence="Moderate",
        status=CandidateStatus.NEEDS_REVIEW, state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {edgar_candidate.id: edgar_candidate}, "edgar_candidates.json")

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", edgar_user_agent="EevaResearch test@example.com", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        both_text = " ".join(m.value for m in at.markdown)
        assert "국문 공시" in both_text and "8-K filing two" in both_text

        at.multiselect(key="radar-filter-source").set_value(["SEC EDGAR"])
        at.run()

    assert not at.exception
    filtered_text = " ".join(m.value for m in at.markdown)
    assert "8-K filing two" in filtered_text
    assert "국문 공시" not in filtered_text


def test_radar_inbox_source_filter_always_offers_edinet_even_when_absent(tmp_path):
    """Radar layout correction (design/DECISIONS.md): EDINET must be a
    first-class Source option regardless of whether any EDINET filing has
    actually been loaded this session — only DART is seeded here."""
    _seed_corp_codes(tmp_path)
    dart_filing = _filing("20260812000031", "국문 공시 둘")
    _seed_filing_events(tmp_path, [dart_filing])
    dart_candidate = CandidateSignal(
        id="cand-dart-src-2", filing=dart_filing, matched_rules=["x:y:z"], confidence="Moderate",
        status=CandidateStatus.NEEDS_REVIEW, state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {dart_candidate.id: dart_candidate})

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    source_widget = at.multiselect(key="radar-filter-source")
    assert set(source_widget.options) == {"OpenDART / DART", "SEC EDGAR", "EDINET"}

    source_widget.set_value(["EDINET"])
    at.run()
    filtered_text = " ".join(m.value for m in at.markdown)
    assert "국문 공시 둘" not in filtered_text


def test_radar_inbox_clear_all_filters_restores_full_view(tmp_path):
    _seed_corp_codes(tmp_path)
    match_filing = _filing("20260812000040", "특별한 제목 둘")
    other_filing = _filing("20260812000041", "다른 제목 둘")
    _seed_filing_events(tmp_path, [match_filing, other_filing])
    match_candidate = CandidateSignal(
        id="cand-clear-1", filing=match_filing, matched_rules=["x:y:z"], confidence="Moderate",
        status=CandidateStatus.NEEDS_REVIEW, state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    other_candidate = CandidateSignal(
        id="cand-clear-2", filing=other_filing, matched_rules=["x:y:z"], confidence="Moderate",
        status=CandidateStatus.NEEDS_REVIEW, state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {match_candidate.id: match_candidate, other_candidate.id: other_candidate})

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        at.text_input(key="radar-filter-search").set_value("특별한")
        at.run()
        assert "다른 제목 둘" not in " ".join(m.value for m in at.markdown)

        clear_buttons = [b for b in at.button if b.label == "Clear all filters"]
        assert clear_buttons
        clear_buttons[0].click()
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "특별한 제목 둘" in all_text
    assert "다른 제목 둘" in all_text
    assert at.text_input(key="radar-filter-search").value == ""


def _needs_review_candidate(candidate_id: str, filing: FilingEvent) -> CandidateSignal:
    return CandidateSignal(
        id=candidate_id, filing=filing, matched_rules=["earnings:earnings_or_results_report:실적"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="본문 발췌.",
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )


# --- Durable-State Phase 4M-1 — SQLite candidate rendering through a full
# page render (closes a pre-existing gap: only test_backend_factory_phase2b.py
# exercised radar_inbox._build_items' own sqlite branch directly before this;
# no test rendered the actual page against a sqlite-backed candidate store). ---


def test_radar_inbox_renders_sqlite_backed_candidate_through_full_page_render(tmp_path):
    # DART, not EDGAR: dart_readiness only needs its own two tracked
    # companies (Samsung Electronics + SK Hynix) resolved to become
    # ready — EDGAR's tracked registry has 25 companies, all of which
    # would need a resolved identifier for edgar_readiness.ready to be
    # True, which isn't this test's concern.
    filing = _filing("20260812000200", "실적 발표")
    candidate = _needs_review_candidate("cand-sqlite-1", filing)
    settings = Settings(
        dart_api_key="dart-key", translation_api_key="deepl-key", edgar_user_agent=None,
        edinet_subscription_key=None, cache_dir=tmp_path, db_backend="sqlite", state_db_path=tmp_path / "state.db",
    )
    backend_factory.get_candidate_repository(settings, "OpenDART / DART").upsert_new_candidates([candidate])
    # dart_readiness() checks corp_code resolution independently of the
    # candidate/filing data above (it reads the identifier repository,
    # not any filing's own corp_code) — without this, every source would
    # be unready and the page would show its "not configured" state
    # instead of ever reaching _build_items().
    from src.data_access.state_db.identifier_repository import ResolvedIdentifierRecord, upsert_resolved_identifier

    id_repo = backend_factory.get_identifier_repository(settings, "OpenDART / DART")
    for krx_code, corp_code, name in [("005930", "00126380", "삼성전자"), ("000660", "00164779", "SK 하이닉스")]:
        upsert_resolved_identifier(
            id_repo.conn, "OpenDART / DART", krx_code,
            ResolvedIdentifierRecord(identifier=corp_code, display_name=name, resolution_method="synthetic-test-fixture", retrieved_at=_now_iso()),
        )

    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "실적 발표" in all_text
    # No JSON candidate file was ever created for this sqlite-backed render.
    assert not (tmp_path / "dart_candidates.json").exists()


# --- Radar simplicity workstream: the Evidence status panel (flag_reason,
# evidence_location, evidence_source_member) is removed from the public
# card entirely — the underlying CandidateSignal fields are untouched
# (models.py), just never rendered here any more. The one remaining
# safety property worth a dedicated test is that a candidate carrying
# these fields still renders its 5 approved fields normally and without
# exception, and that unsafe characters in rendered content are always
# escaped. ---


def test_radar_inbox_renders_stably_when_evidence_packet_fields_are_present(tmp_path):
    _seed_corp_codes(tmp_path)
    filing = _filing("20260817000001", "유상증자 결정")
    _seed_filing_events(tmp_path, [filing])
    candidate = CandidateSignal(
        id="cand-phase1-present", filing=filing, matched_rules=["financing:capital_increase:유상증자"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="본문 발췌.",
        excerpt_translation=Translation(translated_text="Body excerpt.", provider="DeepL", source_lang="ko", target_lang="en", translated_at=_now_iso()),
        flag_reason=FlagReason(
            category="financing", matched_terms=("financing:capital_increase:유상증자",), score_inputs=("confidence=Moderate",),
            human_readable_reason="Matched a capital-increase financing keyword.", source_detail="detail",
        ),
        evidence_location=EvidenceLocation(kind=LocationKind.SECTION, section="Item 2.03"),
        evidence_source_member="PublicDoc/0101.pdf",
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso(), detail="Extraction succeeded.")],
    )
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "본문 발췌." in all_text
    # Radar layout correction: the stored translation is behind a
    # collapsed, display-only toggle by default now, not shown directly.
    assert "Body excerpt." not in all_text
    assert any(b.label == "Show English translation" for b in at.button)
    assert "Matched a capital-increase financing keyword." not in all_text
    assert "Item 2.03" not in all_text
    assert "PublicDoc/0101.pdf" not in all_text
    assert "Evidence file" not in all_text
    assert "Why flagged" not in all_text
    assert "Evidence location" not in all_text


def test_radar_inbox_escapes_unsafe_characters_in_excerpt_and_title(tmp_path):
    _seed_corp_codes(tmp_path)
    unsafe_report_nm = 'PublicDoc/<script>alert(1)</script>"onerror="x'
    filing = _filing("20260817000004", unsafe_report_nm)
    _seed_filing_events(tmp_path, [filing])
    candidate = CandidateSignal(
        id="cand-unsafe-excerpt", filing=filing, matched_rules=["financing:capital_increase:유상증자"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original='<script>alert(2)</script>"onerror="y',
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso(), detail="Extraction succeeded.")],
    )
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    raw_html = "\n".join(m.value for m in at.markdown)
    assert "<script>alert(1)</script>" not in raw_html
    assert "<script>alert(2)</script>" not in raw_html
    assert "&lt;script&gt;" in raw_html
