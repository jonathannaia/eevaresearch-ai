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
    ExtractionState,
    FilingEvent,
    StateTransition,
    Translation,
    TranslationState,
)

_HARNESS = Path(__file__).parent / "apptest_pages" / "radar_inbox_page.py"
# Phase C (editorial-simplicity pass): renamed from "Signals & review
# queue"/"All filing events" — same underlying view/filter/ordering logic.
# Phase F1 (design/DECISIONS.md): "All filings" -> "Captured filings" —
# label text only, same view/filter/ordering logic again.
# Phase R1 (design/DECISIONS.md): "Needs your decision" -> "Latest" —
# same supersession, same underlying `candidate is not None` filter.
_SIGNALS_VIEW = "Latest"
_ALL_FILINGS_VIEW = "Captured filings"


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


def test_radar_inbox_edinet_scope_line_is_truthful_when_configured_but_unscanned(tmp_path):
    # Gate 7.1: the five real EDINET registry entries (tracked_companies.py)
    # are pre-resolved regardless of cache_dir, so a configured key alone
    # makes edinet_readiness.ready True — with zero live scans ever run,
    # this must say "configured; no live scan completed yet," never claim
    # calibration, active monitoring, currency, autonomy, or live signals.
    settings = Settings(
        dart_api_key=None, translation_api_key=None, edgar_user_agent=None,
        edinet_subscription_key="test-key", cache_dir=tmp_path,
    )
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown).lower()
    assert "5 tracked companies configured" in all_text
    assert "no live scan completed yet" in all_text
    assert "filingevents: 0" in all_text
    assert "candidatesignals: 0" in all_text
    assert "last scan: none" in all_text
    for forbidden in ("calibrated", "actively monitored", "autonomous", "live signals"):
        assert forbidden not in all_text


def _seed_edinet_filing_events(cache_dir, filings: list[FilingEvent]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    from dataclasses import asdict
    payload = {"seen_keys": [f"EDINET:{f.corp_code}:{f.rcept_no}" for f in filings], "filing_events": [asdict(f) for f in filings], "candidate_signals": []}
    (cache_dir / "edinet_filing_events.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_radar_inbox_shows_all_three_edinet_form_codes_for_a_softbank_shaped_event(tmp_path):
    # Gate 8.1 item 4/B: the real SoftBank Group annual-report triplet
    # (ordinanceCode="010", formCode="030000", docTypeCode="120") must be
    # visible in the rendered event, unconditioned by candidate presence
    # (an EDINET FilingEvent realistically has no candidate while the
    # category map stays empty).
    softbank_filing = FilingEvent(
        rcept_no="S100YGH5", corp_code="E02778", corp_name="SoftBank Group Corp.", stock_code="99840",
        report_nm="有価証券報告書－第46期(2025/04/01－2026/03/31)", rcept_dt="2026-06-22",
        flr_nm="ソフトバンクグループ株式会社", pblntf_ty="030000", pblntf_detail_ty="120", ordinance_code="010",
        theme_slug="ai-buildout", source_url="https://api.edinet-fsa.go.jp/api/v2/documents/S100YGH5",
        retrieved_at=datetime.now(timezone.utc).isoformat(), source_name="EDINET", original_language="Japanese",
    )
    _seed_edinet_filing_events(tmp_path, [softbank_filing])

    settings = Settings(
        dart_api_key=None, translation_api_key=None, edgar_user_agent=None,
        edinet_subscription_key="test-key", cache_dir=tmp_path,
    )
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

        assert not at.exception
        # This filing has no CandidateSignal, so the default "Needs your
        # decision" view must show the calm empty state instead — never
        # fabricate a signal for it, never fall back to a raw-inventory dump.
        default_text = " ".join(m.value for m in at.markdown)
        assert "No candidate signals yet" in default_text
        assert "no filing currently meets the configured candidate rules" in default_text.lower()
        assert "有価証券報告書" not in default_text  # the bare filing's own title is not shown here
        action_labels = {b.label for b in at.button}
        assert "Show captured filings" in action_labels

        # get_settings must still be patched for this second run — AppTest
        # re-executes the harness script synchronously on `.run()`.
        at.radio(key="radar-view-mode").set_value(_ALL_FILINGS_VIEW)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "Ordinance code" in all_text and "010" in all_text
    assert "Form code" in all_text and "030000" in all_text
    assert "Document type code" in all_text and "120" in all_text


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
        # Default view ("Latest"): the 3 candidates render,
        # but `new_filing` — a bare FilingEvent with no CandidateSignal —
        # is excluded. Its own report title is a precise, non-coincidental
        # marker (unlike the "New filing" status label, which also appears
        # verbatim inside assets/styles.css's own design-system comments).
        default_text = " ".join(m.value for m in at.markdown)
        assert "일반 공고" not in default_text
        assert "Needs review" in default_text
        assert "Processing deferred" in default_text
        assert "Retrieval failed" in default_text

        at.radio(key="radar-view-mode").set_value(_ALL_FILINGS_VIEW)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "일반 공고" in all_text  # the bare event's own title, now visible
    assert "Needs review" in all_text
    assert "Processing deferred" in all_text
    assert "Retrieval failed" in all_text
    assert "New facility investment decision" in all_text  # English title translation
    assert "New facility investment original text" in all_text  # English excerpt translation
    assert "Machine translation" in all_text
    button_labels = {b.label for b in at.button}
    assert "Prepare analyst view" in button_labels
    assert any("Retry limit reached" in label for label in button_labels)
    assert "When ready, the analyst-ready summary appears above." in all_text


# --- "Prepare analyst view" UX fix: spinner-covered, readiness-gated,
# defense-in-depth process/retry action (see design/DECISIONS.md) ---


def test_radar_inbox_retry_eligible_candidate_shows_retry_label_and_caption(tmp_path):
    _seed_corp_codes(tmp_path)
    failed_filing = _filing("20260812000005", "실적 관련 공시")
    _seed_filing_events(tmp_path, [failed_filing])

    # Zero QUEUED_FOR_PROCESSING attempts and no recent last-attempt
    # timestamp — retry_policy.retry_eligibility(...) is immediately
    # eligible=True for this shape (see retry_policy.py), distinct from
    # the "Retry limit reached" fixture above (3 exhausted attempts).
    retryable = CandidateSignal(
        id="cand-retry-eligible", filing=failed_filing, matched_rules=["earnings:earnings_or_results_report:실적"],
        confidence="Moderate", status=CandidateStatus.RETRIEVAL_FAILED,
        state_history=[StateTransition(status=CandidateStatus.RETRIEVAL_FAILED, at=_now_iso(), detail="DART request timed out.")],
    )
    candidate_store.save_candidates(tmp_path, {retryable.id: retryable})

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    button_labels = {b.label for b in at.button}
    assert "Retry analyst view preparation" in button_labels
    assert "When ready, the analyst-ready summary appears above." in all_text
    retry_button = next(b for b in at.button if b.label == "Retry analyst view preparation")
    assert retry_button.disabled is False


def test_radar_inbox_process_action_disabled_when_source_not_configured(tmp_path):
    # DART left unconfigured while EDINET is configured — at least one
    # source must be ready or render() takes the early "not configured"
    # empty-state return before ever reaching the item list. This proves
    # the disabled state is genuinely per-candidate-source, not a page-
    # wide gate: a DART candidate must render disabled even while the
    # page as a whole is usable because EDINET is ready.
    _seed_corp_codes(tmp_path)
    deferred_filing = _filing("20260812000006", "신규시설투자 결정")
    _seed_filing_events(tmp_path, [deferred_filing])
    deferred = CandidateSignal(
        id="cand-unconfigured-1", filing=deferred_filing, matched_rules=["capex_or_facility_investment:facility_investment:신규시설투자"],
        confidence="Moderate", status=CandidateStatus.PROCESSING_DEFERRED,
        state_history=[StateTransition(status=CandidateStatus.PROCESSING_DEFERRED, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {deferred.id: deferred})

    settings = Settings(
        dart_api_key=None, translation_api_key=None, edgar_user_agent=None,
        edinet_subscription_key="test-key", cache_dir=tmp_path,
    )
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    prepare_button = next(b for b in at.button if b.label == "Prepare analyst view")
    assert prepare_button.disabled is True
    assert "Preparation is unavailable until this source is configured." in all_text
    # Requirement 4: never a credential name, key value, path, or raw
    # exception detail in the disabled-reason text.
    for forbidden in ("EDGE_DART_API_KEY", "EDGE_TRANSLATION_API_KEY", "api_key", "Traceback"):
        assert forbidden not in all_text


def test_radar_inbox_clicking_prepare_analyst_view_calls_processing_once_and_rerenders_from_persisted_status(tmp_path, monkeypatch):
    _seed_corp_codes(tmp_path)
    filing = _filing("20260812000007", "장래사업ㆍ경영계획 공시")
    _seed_filing_events(tmp_path, [filing])
    deferred = CandidateSignal(
        id="cand-click-now", filing=filing, matched_rules=["guidance:forward_looking_business_plan:장래사업ㆍ경영계획"],
        confidence="Moderate", status=CandidateStatus.PROCESSING_DEFERRED,
        state_history=[StateTransition(status=CandidateStatus.PROCESSING_DEFERRED, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {deferred.id: deferred})

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)

    calls: list[str] = []

    def _fake_process(_settings, candidate_id):
        # Simulates the real pipeline's own behavior: mutate and persist
        # via the same candidate_store the page reads from — proves the
        # card re-renders from the persisted store, not an optimistic
        # local value held in the button's own click handler.
        calls.append(candidate_id)
        store = candidate_store.load_candidates(tmp_path)
        candidate = store[candidate_id]
        candidate.status = CandidateStatus.NEEDS_REVIEW
        candidate_store.update_candidate(tmp_path, candidate)
        return candidate

    # Overrides this file's autouse network-call guard for exactly this
    # one source/function — every other guarded entry point (EDGAR,
    # EDINET, DART run_scan) still raises if reached.
    monkeypatch.setattr("src.data_access.dart.radar_service.process_candidate_now", _fake_process, raising=True)

    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

        assert not at.exception
        prepare_button = next(b for b in at.button if b.label == "Prepare analyst view")
        assert prepare_button.disabled is False
        prepare_button.click()
        at.run()

    assert not at.exception
    assert calls == ["cand-click-now"]  # called exactly once
    all_text = " ".join(m.value for m in at.markdown)
    assert "Needs review" in all_text  # re-rendered from the persisted CandidateStatus
    button_labels = {b.label for b in at.button}
    assert "Prepare analyst view" not in button_labels  # no longer PROCESSING_DEFERRED
    assert "Retry analyst view preparation" not in button_labels  # NEEDS_REVIEW isn't retryable


def test_radar_inbox_shows_not_material_label_for_routine_ownership_candidate(tmp_path):
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
    assert "Not material · routine ownership update" in all_text
    # Live/demo isolation: this page's own scope note is present (Phase
    # R1: relocated into the collapsed Ingestion status disclosure, same
    # content, minus the "Live" prefix this phase removed — see
    # design/DECISIONS.md), and nothing here claims a broader market-
    # conviction/investment reading.
    assert "Korea DART + SEC EDGAR pilots configured" in all_text
    assert "market conviction" not in all_text.lower()
    assert "investment confidence" not in all_text.lower()


def _seed_edgar_ciks(cache_dir) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "NVDA": {"cik": "0001045810", "company_name": "NVIDIA CORP", "source": "test", "retrieved_at": "2026-08-01T00:00:00+00:00"},
        "MU": {"cik": "0000723125", "company_name": "MICRON TECHNOLOGY INC", "source": "test", "retrieved_at": "2026-08-01T00:00:00+00:00"},
        "COHR": {"cik": "0000021510", "company_name": "COHERENT CORP", "source": "test", "retrieved_at": "2026-08-01T00:00:00+00:00"},
        "ROK": {"cik": "0001024478", "company_name": "ROCKWELL AUTOMATION INC", "source": "test", "retrieved_at": "2026-08-01T00:00:00+00:00"},
        "RKLB": {"cik": "0001819994", "company_name": "ROCKET LAB USA INC", "source": "test", "retrieved_at": "2026-08-01T00:00:00+00:00"},
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

    settings = Settings(dart_api_key=None, translation_api_key=None, edgar_user_agent="EevaResearch test@example.com", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "SEC EDGAR" in all_text
    assert "NVIDIA" in all_text
    assert "Revenue increased" in all_text
    # No translation UI leaks in for an EDGAR (native-English) candidate.
    assert "Machine translation" not in all_text
    # DART is unconfigured here — its scope line must not render.
    assert "OpenDART / DART · Samsung" not in all_text


# --- Analyst view (deterministic, template-only Details section) ---


def test_radar_inbox_analyst_view_renders_for_dart_market_rumor_response_candidate(tmp_path):
    _seed_corp_codes(tmp_path)
    rumor_filing = FilingEvent(
        rcept_no="20260812000100", corp_code="00164779", corp_name="SK Hynix", stock_code="000660",
        report_nm="조회공시요구(풍문또는보도)에대한답변(미확정)", rcept_dt="20260812", flr_nm="SK 하이닉스",
        source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260812000100",
        retrieved_at=_now_iso(),
    )
    _seed_filing_events(tmp_path, [rumor_filing])
    rumor_candidate = CandidateSignal(
        id="cand-rumor-1", filing=rumor_filing,
        matched_rules=["market_rumor_response:rumor_inquiry_or_response:풍문또는보도"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        translation_state=TranslationState.TRANSLATED,
        excerpt_original="한국거래소의조회공시요구에대한답변으로,보도된내용에대해확인된바없습니다. 향후확인되는대로재공시하겠습니다.",
        excerpt_translation=Translation(
            translated_text="In response to the exchange's disclosure inquiry, nothing has been confirmed regarding the reported content.",
            provider="DeepL", source_lang="ko", target_lang="en", translated_at=_now_iso(),
        ),
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {rumor_candidate.id: rumor_candidate})

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "Filing overview" in all_text
    assert "Phase 1" in all_text
    assert "Not a substantive summary of the filing text." in all_text
    # 1. What happened — deterministic, structured-fields-only sentence,
    # plus the plain-English restatement of why Radar flagged it.
    assert "What happened" in all_text
    assert "SK Hynix filed “조회공시요구(풍문또는보도)에대한답변(미확정)” with OpenDART / DART on 20260812." in all_text
    assert "Radar flagged this filing because:" in all_text
    assert "matched keyword “풍문또는보도”" in all_text
    assert "Open original filing" in all_text
    # 2. What remains uncertain — the exact required wording, plus the
    # follow-up checklist merged into the same section.
    assert "What remains uncertain" in all_text
    assert (
        "This filing is a disclosure inquiry or response about reported information. "
        "It does not confirm that a transaction has occurred." in all_text
    )
    assert "Watch for:" in all_text
    assert "A formal company response or clarification" in all_text
    assert "A subsequent filing that confirms or denies the reported matter" in all_text
    assert "An amendment or related disclosure" in all_text
    # 3. Why it matters — the one real hand-written template for this category.
    assert "Why it matters" in all_text
    assert "This may matter because it is a company's formal response to reported information" in all_text
    # 4. Evidence and provenance — native text + translation still visible,
    # nothing invented (no amount/counterparty this fixture never stated).
    assert "한국거래소의조회공시요구에대한답변" in all_text  # native excerpt still rendered below
    assert "nothing has been confirmed" in all_text  # translation still rendered below
    assert "KRW" not in all_text
    assert "China" not in all_text
    assert "trillion" not in all_text
    # Claim-type vocabulary reused correctly.
    assert "Fact" in all_text
    assert "Uncertainty" in all_text
    assert "Interpretation" in all_text
    # Technical details relocated, not deleted.
    assert "Technical details" in [e.label for e in at.expander]
    assert "State history" in all_text


def test_radar_inbox_analyst_view_absent_for_deferred_and_failed_candidates(tmp_path):
    _seed_corp_codes(tmp_path)
    deferred_filing = _filing("20260812000101", "유상증자 결정")
    failed_filing = _filing("20260812000102", "실적 발표")
    _seed_filing_events(tmp_path, [deferred_filing, failed_filing])
    deferred = CandidateSignal(
        id="cand-deferred-av", filing=deferred_filing, matched_rules=["financing:capital_raise_or_treasury_stock:유상증자"],
        confidence="Moderate", status=CandidateStatus.PROCESSING_DEFERRED,
        state_history=[StateTransition(status=CandidateStatus.PROCESSING_DEFERRED, at=_now_iso())],
    )
    failed = CandidateSignal(
        id="cand-failed-av", filing=failed_filing, matched_rules=["earnings:earnings_or_results_report:실적"],
        confidence="Moderate", status=CandidateStatus.PARSE_FAILED, extraction_state=ExtractionState.PARSE_FAILED,
        state_history=[StateTransition(status=CandidateStatus.PARSE_FAILED, at=_now_iso(), detail="No extractable text.")],
    )
    candidate_store.save_candidates(tmp_path, {deferred.id: deferred, failed.id: failed})

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "Filing overview" not in all_text
    assert "What happened" not in all_text


def test_radar_inbox_analyst_view_unknown_category_uses_exact_fallback_wording(tmp_path):
    _seed_corp_codes(tmp_path)
    capex_filing = _filing("20260812000103", "신규시설투자 결정")
    _seed_filing_events(tmp_path, [capex_filing])
    capex_candidate = CandidateSignal(
        id="cand-capex-av", filing=capex_filing,
        matched_rules=["capex_or_facility_investment:facility_investment:신규시설투자"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="신규시설투자관련공시내용입니다. 투자금액및목적등자세한사항은첨부서류를참고하시기바랍니다.",
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {capex_candidate.id: capex_candidate})

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "Filing overview" in all_text
    assert "What remains uncertain" in all_text
    assert (
        "This filing type has no specific uncertainty template yet. "
        "Read the source excerpt directly before drawing conclusions." in all_text
    )
    assert "Review subsequent company filings or official statements related to this disclosure." in all_text
    # The DART-rumor-specific wording must never leak into an unrelated category.
    assert "does not confirm that a transaction has occurred" not in all_text
    assert "A formal company response or clarification" not in all_text
    # "Why it matters" only exists for market_rumor_response — absent here.
    assert "Why it matters" not in all_text


def test_radar_inbox_analyst_view_edgar_omits_translation_line_when_not_requested(tmp_path):
    _seed_edgar_ciks(tmp_path)
    edgar_filing = FilingEvent(
        rcept_no="0001045810-26-000099", corp_code="0001045810", corp_name="NVIDIA", stock_code="NVDA",
        report_nm="8-K filing", rcept_dt="2026-08-12", flr_nm="NVIDIA", pblntf_ty="8-K", theme_slug="ai-buildout",
        source_url="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000099/",
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
        id="edgar-cand-av-1", filing=edgar_filing,
        matched_rules=["earnings_or_results:8-K item 2.02"], confidence="Moderate",
        status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        translation_state=TranslationState.NOT_REQUESTED,
        # excerpt_quality deliberately left at its default (UNKNOWN) — real
        # EDGAR candidates never have this field set at all (confirmed by
        # grep; see analyst_view.py's module docstring). This is the exact
        # regression case: a substantive excerpt must still render full
        # "What happened" content based on length alone, not ExcerptQuality.
        excerpt_original="Item 2.02 Results of Operations. Revenue increased.",
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {edgar_candidate.id: edgar_candidate}, "edgar_candidates.json")

    settings = Settings(dart_api_key=None, translation_api_key=None, edgar_user_agent="EevaResearch test@example.com", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "Filing overview" in all_text
    assert "NVIDIA filed “8-K filing” with SEC EDGAR on 2026-08-12." in all_text
    assert "Item 2.02 Results of Operations. Revenue increased." in all_text  # native excerpt still shown
    # Requirement: the Analyst view's own translation-status line must not
    # appear for a NOT_REQUESTED candidate (the pre-existing, unrelated
    # "Translation state: Not requested" raw technical-detail row is fine
    # and untouched — this only checks the new Evidence and provenance line).
    assert "Machine translation: see below" not in all_text
    assert "not currently available for this excerpt" not in all_text
    # EDGAR has no hand-written "Why it matters" template — never shown.
    assert "Why it matters" not in all_text


def test_radar_inbox_analyst_view_edinet_never_labels_a_code_match_as_a_keyword_match(tmp_path):
    edinet_filing = FilingEvent(
        rcept_no="S100YTEST", corp_code="E02778", corp_name="SoftBank Group Corp.", stock_code="99840",
        report_nm="有価証券報告書－第46期(2025/04/01－2026/03/31)", rcept_dt="2026-06-22",
        flr_nm="ソフトバンクグループ株式会社", pblntf_ty="030000", pblntf_detail_ty="120", ordinance_code="010",
        theme_slug="ai-buildout", source_url="https://api.edinet-fsa.go.jp/api/v2/documents/S100YTEST",
        retrieved_at=_now_iso(), source_name="EDINET", original_language="Japanese",
    )
    cache_dir = tmp_path
    cache_dir.mkdir(parents=True, exist_ok=True)
    from dataclasses import asdict
    edinet_payload = {
        "seen_keys": [f"EDINET:{edinet_filing.corp_code}:{edinet_filing.rcept_no}"],
        "filing_events": [asdict(edinet_filing)], "candidate_signals": [],
    }
    (cache_dir / "edinet_filing_events.json").write_text(json.dumps(edinet_payload, ensure_ascii=False), encoding="utf-8")

    edinet_candidate = CandidateSignal(
        id="edinet-cand-test-av", filing=edinet_filing,
        matched_rules=["annual_securities_report:010:030000:120"], confidence="Moderate",
        status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="有価証券報告書の記載内容です。事業の状況及び経理の状況について詳細に記載しております。",
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(cache_dir, {edinet_candidate.id: edinet_candidate}, "edinet_candidates.json")

    settings = Settings(
        dart_api_key=None, translation_api_key=None, edgar_user_agent=None,
        edinet_subscription_key="test-key", cache_dir=cache_dir,
    )
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "Filing overview" in all_text
    # The new Analyst view section's own phrasing (precisely isolated —
    # see test_analyst_view.py's own unit test for the exact contract):
    # a routing-code match, correctly never called a keyword match.
    assert "Annual securities report — matched by filing type/form code (010:030000:120)" in all_text
    # Note: the separate, pre-existing "Why this matters:" list elsewhere
    # on this same card (relabeled from "Why flagged:" in Phase R1,
    # design/DECISIONS.md — radar_card.py's _why_this_matters_phrases,
    # renamed from _why_flagged_phrases, content unchanged) still
    # mislabels EDINET code matches as "keyword match" — a real, known
    # gap this task's scope did not include fixing. Not yet documented in
    # design/DECISIONS.md.


def test_radar_inbox_analyst_view_insufficient_excerpt_uses_exact_fallback_and_no_invented_content(tmp_path):
    """Phase 1 "What happened" fallback — the excerpt is shorter than
    _MIN_SUBSTANTIVE_EXCERPT_CHARS (40), so nothing beyond the exact
    approved fallback sentence should appear; no keyword-match phrase, no
    source-facts sentence, nothing invented. Length-based, not
    ExcerptQuality — this candidate never sets excerpt_quality at all
    (stays at its default), matching how EDGAR/EDINET candidates work in
    real data."""
    _seed_corp_codes(tmp_path)
    thin_filing = _filing("20260812000104", "실적 발표")
    _seed_filing_events(tmp_path, [thin_filing])
    thin_candidate = CandidateSignal(
        id="cand-thin-excerpt-av", filing=thin_filing,
        matched_rules=["earnings:earnings_or_results_report:실적"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="…",  # non-empty (passes should_render_analyst_view) but well under the 40-char floor
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {thin_candidate.id: thin_candidate})

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "Filing overview" in all_text
    assert "What happened" in all_text
    assert (
        "The filing was detected, but the available excerpt is not sufficient "
        "to summarize the disclosure reliably. Read the original filing." in all_text
    )
    # No structured-facts sentence or keyword-match phrase — those are
    # gated on the length threshold only.
    assert "filed “실적 발표”" not in all_text
    assert "Radar flagged this filing because:" not in all_text
    # The source link is still offered even when the excerpt is thin.
    assert "Open original filing" in all_text


def test_radar_inbox_analyst_view_excerpt_at_exact_threshold_boundary(tmp_path):
    """Boundary check on the approved constant itself: exactly
    _MIN_SUBSTANTIVE_EXCERPT_CHARS (40) chars renders full content; one
    character short falls back. Proves the gate is length, not
    ExcerptQuality (never set on either candidate here)."""
    _seed_corp_codes(tmp_path)
    at_threshold_filing = _filing("20260812000106", "실적 발표")
    one_under_filing = _filing("20260812000107", "실적 발표")
    _seed_filing_events(tmp_path, [at_threshold_filing, one_under_filing])
    exactly_40 = "x" * 40
    one_under_40 = "x" * 39
    assert len(exactly_40) == 40
    assert len(one_under_40) == 39
    at_threshold_candidate = CandidateSignal(
        id="cand-at-threshold-av", filing=at_threshold_filing,
        matched_rules=["earnings:earnings_or_results_report:실적"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original=exactly_40,
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    one_under_candidate = CandidateSignal(
        id="cand-one-under-av", filing=one_under_filing,
        matched_rules=["earnings:earnings_or_results_report:실적"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original=one_under_40,
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(
        tmp_path, {at_threshold_candidate.id: at_threshold_candidate, one_under_candidate.id: one_under_candidate},
    )

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    # The at-threshold candidate gets full content (structured-facts
    # sentence); the one-under candidate gets only the fallback sentence.
    # Both share the same filing title, so we assert via the distinctive
    # "Radar flagged this filing because:" marker's total count instead
    # of the (identical, non-distinguishing) source-facts sentence text.
    assert all_text.count("Radar flagged this filing because:") == 1


def test_radar_inbox_technical_details_expander_preserves_relocated_fields(tmp_path):
    """Confirms the reorganization moved developer-facing fields into a
    nested, collapsed expander rather than deleting them — the outer
    "Investigate →" expander (renamed from "Details" in Phase R1, design/
    DECISIONS.md) and the inner "Technical details" expander both exist,
    and every relocated field is still present somewhere in the rendered
    output."""
    _seed_corp_codes(tmp_path)
    filing = _filing("20260812000105", "일반 공고")
    _seed_filing_events(tmp_path, [filing])
    candidate = CandidateSignal(
        id="cand-tech-details-av", filing=filing, matched_rules=["earnings:earnings_or_results_report:실적"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="본문 발췌.",
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso(), detail="Extraction succeeded.")],
    )
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    expander_labels = [e.label for e in at.expander]
    assert "Investigate →" in expander_labels
    assert "Technical details" in expander_labels

    all_text = " ".join(m.value for m in at.markdown)
    assert "Filer:" in all_text
    assert "Filed:" in all_text
    assert "Retrieved:" in all_text
    assert "Extraction state:" in all_text
    assert "Translation state:" in all_text
    assert "Excerpt quality:" in all_text
    assert "State history" in all_text
    assert "Extraction succeeded." in all_text
    # Evidence status and the original excerpt stay outside/alongside the
    # collapsed technical section, not inside it — still present either way.
    assert "Evidence status" in all_text
    assert "본문 발췌." in all_text


# --- View selector, pagination, filter simplification, translation copy,
# and Data controls (usability/navigation-stability follow-up) ---


def test_radar_inbox_data_controls_expander_present_with_warning_and_scans_untouched(tmp_path):
    _seed_corp_codes(tmp_path)
    filing = _filing("20260812000010", "일반 공고")
    _seed_filing_events(tmp_path, [filing])
    candidate = CandidateSignal(
        id="cand-dc", filing=filing, matched_rules=["x:y:z"], confidence="Moderate",
        status=CandidateStatus.NEEDS_REVIEW, state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    expander_titles = {e.label for e in at.expander}
    assert "Ingestion status" in expander_titles
    all_text = " ".join(m.value for m in at.markdown)
    assert "Source scans can take time and are intended for local/admin use." in all_text
    # Scan buttons still exist, unclicked — the guard fixture would have
    # raised (failing this test) if any scan/process path were reached.
    button_labels = {b.label for b in at.button}
    assert "Scan DART now" in button_labels


def test_radar_inbox_bare_event_shows_translation_availability_copy(tmp_path):
    _seed_corp_codes(tmp_path)
    bare_filing = _filing("20260812000011", "단순 공시")
    _seed_filing_events(tmp_path, [bare_filing])

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        # No candidate anywhere in this fixture, so Signals view is empty —
        # switch to All filings to reach the bare event's own card.
        at.radio(key="radar-view-mode").set_value(_ALL_FILINGS_VIEW)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "Translation:</strong> Available after document processing" in all_text
    # Requirement 4: never fabricate excerpt/translation/extraction/status
    # for a bare event.
    assert "Extraction state" not in all_text
    assert "Excerpt quality" not in all_text
    assert "English translation" not in all_text


def test_radar_inbox_all_filings_view_paginates_at_twenty_cards(tmp_path):
    _seed_corp_codes(tmp_path)
    filings = [_filing(f"2026081200{i:04d}", f"공시 {i}") for i in range(25)]
    _seed_filing_events(tmp_path, filings)  # no candidates — all bare events

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        at.radio(key="radar-view-mode").set_value(_ALL_FILINGS_VIEW)
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


def test_radar_inbox_switching_view_or_filters_resets_pagination_to_page_one(tmp_path):
    _seed_corp_codes(tmp_path)
    filings = [_filing(f"2026081200{i:04d}", f"공시 {i}") for i in range(25)]
    _seed_filing_events(tmp_path, filings)

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        at.radio(key="radar-view-mode").set_value(_ALL_FILINGS_VIEW)
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


# --- Stage 2B: Publish / Monitor / Exclude review-decision actions ---

def _needs_review_candidate(candidate_id: str, filing: FilingEvent) -> CandidateSignal:
    return CandidateSignal(
        id=candidate_id, filing=filing, matched_rules=["earnings:earnings_or_results_report:실적"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="본문 발췌.",
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )


def test_radar_inbox_publish_action_updates_status_and_persists_note(tmp_path):
    _seed_corp_codes(tmp_path)
    filing = _filing("20260812000100", "실적 발표")
    _seed_filing_events(tmp_path, [filing])
    candidate = _needs_review_candidate("cand-publish-1", filing)
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

        at.text_input(key=f"radar-review-note-{candidate.id}").set_value("Confirmed material.")
        at.run()
        publish_button = next(b for b in at.button if b.key == f"publish-{candidate.id}")
        publish_button.click()
        at.run()

    assert not at.exception
    reloaded = candidate_store.load_candidates(tmp_path)[candidate.id]
    assert reloaded.status == CandidateStatus.PUBLISHED
    assert reloaded.reviewed_note == "Confirmed material."
    assert reloaded.reviewed_at is not None
    assert reloaded.state_history[-1].status == CandidateStatus.PUBLISHED
    assert reloaded.state_history[-1].detail == "Confirmed material."


def test_radar_inbox_monitor_action_updates_status(tmp_path):
    _seed_corp_codes(tmp_path)
    filing = _filing("20260812000101", "실적 발표")
    _seed_filing_events(tmp_path, [filing])
    candidate = _needs_review_candidate("cand-monitor-1", filing)
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

        monitor_button = next(b for b in at.button if b.key == f"monitor-{candidate.id}")
        monitor_button.click()
        at.run()

    assert not at.exception
    reloaded = candidate_store.load_candidates(tmp_path)[candidate.id]
    assert reloaded.status == CandidateStatus.MONITORING
    assert reloaded.reviewed_note == ""
    assert reloaded.state_history[-1].detail == "Reviewer decision: Monitoring"


def test_radar_inbox_exclude_requires_two_clicks_and_a_note(tmp_path):
    _seed_corp_codes(tmp_path)
    filing = _filing("20260812000102", "실적 발표")
    _seed_filing_events(tmp_path, [filing])
    candidate = _needs_review_candidate("cand-exclude-1", filing)
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})
    before = (tmp_path / "dart_candidates.json").read_text(encoding="utf-8")

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

        # First click: only sets pending state, no write.
        exclude_button = next(b for b in at.button if b.key == f"exclude-{candidate.id}")
        exclude_button.click()
        at.run()

        after_first_click = (tmp_path / "dart_candidates.json").read_text(encoding="utf-8")
        assert after_first_click == before  # byte-identical — no write yet

        confirm_button = next(b for b in at.button if b.key == f"exclude-confirm-{candidate.id}")
        assert confirm_button.disabled is True  # no note yet
        all_text = " ".join(m.value for m in at.markdown)
        assert "A note is required before excluding" in all_text

        # Adding a note enables the confirm button.
        at.text_input(key=f"radar-review-note-{candidate.id}").set_value("Routine, no new information.")
        at.run()
        confirm_button = next(b for b in at.button if b.key == f"exclude-confirm-{candidate.id}")
        assert confirm_button.disabled is False

        confirm_button.click()
        at.run()

    assert not at.exception
    reloaded = candidate_store.load_candidates(tmp_path)[candidate.id]
    assert reloaded.status == CandidateStatus.DISMISSED
    assert reloaded.reviewed_note == "Routine, no new information."
    assert reloaded.state_history[-1].status == CandidateStatus.DISMISSED
    assert reloaded.state_history[-1].detail == "Routine, no new information."


def test_radar_inbox_exclude_whitespace_only_note_keeps_confirm_disabled(tmp_path):
    _seed_corp_codes(tmp_path)
    filing = _filing("20260812000103", "실적 발표")
    _seed_filing_events(tmp_path, [filing])
    candidate = _needs_review_candidate("cand-exclude-ws", filing)
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

        exclude_button = next(b for b in at.button if b.key == f"exclude-{candidate.id}")
        exclude_button.click()
        at.run()

        at.text_input(key=f"radar-review-note-{candidate.id}").set_value("    ")
        at.run()
        confirm_button = next(b for b in at.button if b.key == f"exclude-confirm-{candidate.id}")
        assert confirm_button.disabled is True  # whitespace-only treated as empty

    assert not at.exception


def test_radar_inbox_exclude_cancel_clears_pending_without_writing(tmp_path):
    _seed_corp_codes(tmp_path)
    filing = _filing("20260812000104", "실적 발표")
    _seed_filing_events(tmp_path, [filing])
    candidate = _needs_review_candidate("cand-exclude-cancel", filing)
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})
    before = (tmp_path / "dart_candidates.json").read_text(encoding="utf-8")

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

        exclude_button = next(b for b in at.button if b.key == f"exclude-{candidate.id}")
        exclude_button.click()
        at.run()

        cancel_button = next(b for b in at.button if b.key == f"exclude-cancel-{candidate.id}")
        cancel_button.click()
        at.run()

        # Pending state cleared — the plain "Exclude" button is back.
        assert any(b.key == f"exclude-{candidate.id}" for b in at.button)
        assert not any(b.key == f"exclude-confirm-{candidate.id}" for b in at.button)

    after = (tmp_path / "dart_candidates.json").read_text(encoding="utf-8")
    assert after == before  # byte-identical — cancel never writes
    assert not at.exception


def test_radar_inbox_review_actions_available_regardless_of_current_status(tmp_path):
    _seed_corp_codes(tmp_path)
    filing = _filing("20260812000105", "실적 발표")
    _seed_filing_events(tmp_path, [filing])
    already_published = CandidateSignal(
        id="cand-already-published", filing=filing, matched_rules=["earnings:earnings_or_results_report:실적"],
        confidence="Moderate", status=CandidateStatus.PUBLISHED, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="본문 발췌.", reviewed_at=_now_iso(), reviewed_note="Initial approval.",
        state_history=[
            StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso()),
            StateTransition(status=CandidateStatus.PUBLISHED, at=_now_iso(), detail="Initial approval."),
        ],
    )
    candidate_store.save_candidates(tmp_path, {already_published.id: already_published})

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

        button_keys = {b.key for b in at.button}
        assert f"publish-{already_published.id}" in button_keys
        assert f"monitor-{already_published.id}" in button_keys
        assert f"exclude-{already_published.id}" in button_keys

        # Revising an already-published candidate to Monitoring appends,
        # never overwrites, its prior history.
        monitor_button = next(b for b in at.button if b.key == f"monitor-{already_published.id}")
        monitor_button.click()
        at.run()

    assert not at.exception
    reloaded = candidate_store.load_candidates(tmp_path)[already_published.id]
    assert reloaded.status == CandidateStatus.MONITORING
    assert len(reloaded.state_history) == 3
    assert reloaded.state_history[1].status == CandidateStatus.PUBLISHED
    assert reloaded.state_history[1].detail == "Initial approval."  # earlier decision preserved


def test_radar_inbox_review_decision_none_result_shows_error_and_does_not_rerun_as_success(tmp_path, monkeypatch):
    _seed_corp_codes(tmp_path)
    filing = _filing("20260812000106", "실적 발표")
    _seed_filing_events(tmp_path, [filing])
    candidate = _needs_review_candidate("cand-vanishes", filing)
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})

    # Simulates the candidate having disappeared from the store between
    # render and click (the one real case record_review_decision returns
    # None for) — this must surface a candidate-specific error, not
    # silently proceed as if the decision succeeded.
    monkeypatch.setattr("src.ui.pages.radar_inbox.review_actions.record_review_decision", lambda *a, **kw: None)

    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

        publish_button = next(b for b in at.button if b.key == f"publish-{candidate.id}")
        publish_button.click()
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "Could not record this decision" in all_text
    reloaded = candidate_store.load_candidates(tmp_path)[candidate.id]
    assert reloaded.status == CandidateStatus.NEEDS_REVIEW  # unchanged — not silently treated as success


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
    assert "Needs review" in all_text
    # No JSON candidate file was ever created for this sqlite-backed render.
    assert not (tmp_path / "dart_candidates.json").exists()
