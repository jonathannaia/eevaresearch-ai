"""Radar simplicity + translation reliability + layout correction
workstreams — fixture-driven proofs of the exact public card contract:

  a) company name and ticker/security code, with `Filed {date}` at the
     top-right when the filing's own official rcept_dt is parseable;
  b) English filing title;
  c) `Original` — original-language title and/or extracted excerpt;
  d) a display-only `Show English translation`/`Hide English
     translation` toggle, present only when a translation is already
     stored — expanding it reveals `English translation` and the stored
     translated text;
  e) `Open original filing ↗` — the sole action.

Covers a Korean (DART) fixture, a Japanese (EDINET) fixture, an English
(EDGAR) fixture, and a page-wide sweep proving none of the removed
public labels (Why this matters, Memory, detection confidence, Evidence
status, Fact/Interpretation/Uncertainty, Potential materiality, Filing
overview, Needs review, Translation unavailable, "being prepared", etc.)
ever appear.

Zero network calls, no real API key, and the real data/cache/ (gitignored
live pilot cache) is never touched — same discipline as
test_radar_public_read_only.py."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.data_access.dart import candidate_store
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, FilingEvent, StateTransition, Translation

_HARNESS = Path(__file__).parent / "apptest_pages" / "radar_inbox_page.py"

# Every label/section this workstream explicitly removes from the public
# card. Checked as a page-wide sweep across several candidate shapes —
# none of these strings may ever appear anywhere on a rendered card.
_FORBIDDEN_PUBLIC_STRINGS = (
    "Why this matters", "Why flagged",
    "Detection confidence",
    "Evidence status", "Original document:", "Native text", "Evidence location", "Evidence file",
    "Fact</span>", "Interpretation</span>", "Uncertainty</span>",
    "Filing overview", "What happened", "What remains uncertain", "Watch for:",
    "Potential materiality",
    "Needs review", "Processing deferred", "Retrieval failed",
    "Translation unavailable", "being prepared",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _radar_settings(tmp_path, **settings_overrides) -> Settings:
    return Settings(
        dart_api_key="dart-key", translation_api_key="deepl-key", edgar_user_agent="EevaResearch test@example.com",
        edinet_subscription_key="test-key", cache_dir=tmp_path, **settings_overrides,
    )


def _run_radar(tmp_path, **settings_overrides) -> AppTest:
    settings = _radar_settings(tmp_path, **settings_overrides)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=15)
        at.run()
    return at


def _rerun(at: AppTest, tmp_path, **settings_overrides) -> None:
    """get_settings is only patched for the duration of _run_radar's own
    `with` block — any later interaction (a widget click followed by
    at.run()) must re-establish the same patch, or radar_inbox.py falls
    back to real, unpatched ambient settings on that rerun."""
    settings = _radar_settings(tmp_path, **settings_overrides)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at.run()


def _text(at: AppTest) -> str:
    return " ".join(m.value for m in at.markdown if not m.value.startswith("<style>"))


def _seed_corp_codes(cache_dir) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {"005930": {"corp_code": "00126380", "corp_name": "삼성전자", "source": "OpenDART corpCode.xml", "retrieved_at": _now_iso()}}
    (cache_dir / "dart_corp_codes.json").write_text(json.dumps(payload), encoding="utf-8")


def _seed_dart_filing_events(cache_dir, filings: list[FilingEvent]) -> None:
    from dataclasses import asdict

    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {"seen_receipt_numbers": [f.rcept_no for f in filings], "filing_events": [asdict(f) for f in filings], "candidate_signals": []}
    (cache_dir / "dart_filing_events.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _seed_edinet_filing_events(cache_dir, filings: list[FilingEvent]) -> None:
    from dataclasses import asdict

    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {"seen_keys": [f"EDINET:{f.corp_code}:{f.rcept_no}" for f in filings], "filing_events": [asdict(f) for f in filings], "candidate_signals": []}
    (cache_dir / "edinet_filing_events.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _seed_edgar_filing_events(cache_dir, filing: FilingEvent) -> None:
    from dataclasses import asdict

    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {"seen_keys": [f"SEC EDGAR:{filing.corp_code}:{filing.rcept_no}"], "filing_events": [asdict(filing)], "candidate_signals": []}
    (cache_dir / "edgar_filing_events.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _seed_edgar_ciks(cache_dir) -> None:
    from src.config.tracked_companies import get_tracked_companies_for_source

    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        c.krx_code: {"cik": f"{i:010d}", "company_name": c.name.upper(), "source": "test", "retrieved_at": _now_iso()}
        for i, c in enumerate(get_tracked_companies_for_source("SEC EDGAR"), start=1)
    }
    (cache_dir / "edgar_ciks.json").write_text(json.dumps(payload), encoding="utf-8")


# ============================================================
# Korean (DART) fixture — full success
# ============================================================


def test_korean_fixture_shows_only_the_approved_fields(tmp_path):
    _seed_corp_codes(tmp_path)
    filing = FilingEvent(
        rcept_no="20260812000001", corp_code="00126380", corp_name="삼성전자", stock_code="005930",
        report_nm="신규시설투자등 결정", rcept_dt="20260812", flr_nm="삼성전자",
        source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260812000001",
        retrieved_at=_now_iso(),
    )
    _seed_dart_filing_events(tmp_path, [filing])
    candidate = CandidateSignal(
        id="cand-ko-1", filing=filing, matched_rules=["capex_or_facility_investment:facility_investment:신규시설투자"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="신규시설투자등 관련 원문 발췌.",
        title_translation=Translation(translated_text="New facility investment decision", provider="DeepL", source_lang="ko", target_lang="en", translated_at=_now_iso()),
        excerpt_translation=Translation(translated_text="New facility investment related excerpt.", provider="DeepL", source_lang="ko", target_lang="en", translated_at=_now_iso()),
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})

    at = _run_radar(tmp_path)
    assert not at.exception
    all_text = _text(at)

    assert "삼성전자" in all_text
    assert "005930" in all_text
    assert "Filed Aug 12, 2026" in all_text  # rcept_dt == "20260812"
    assert "New facility investment decision" in all_text  # English filing title
    assert "신규시설투자등 관련 원문 발췌." in all_text  # Original (native excerpt)
    assert "New facility investment related excerpt." not in all_text  # stored, but collapsed by default
    toggle_buttons = [b for b in at.button if b.label == "Show English translation"]
    assert len(toggle_buttons) == 1
    link_buttons = [b for b in at.get("link_button") if b.label == "Open original filing ↗"]
    assert len(link_buttons) == 1
    for forbidden in _FORBIDDEN_PUBLIC_STRINGS:
        assert forbidden not in all_text, forbidden

    toggle_buttons[0].click()
    _rerun(at, tmp_path)
    all_text = _text(at)
    assert "New facility investment related excerpt." in all_text  # now expanded
    assert any(b.label == "Hide English translation" for b in at.button)


# ============================================================
# Japanese (EDINET) fixture — full success
# ============================================================


def test_japanese_fixture_shows_only_the_approved_fields(tmp_path):
    filing = FilingEvent(
        rcept_no="S100YGH5", corp_code="E02778", corp_name="SoftBank Group Corp.", stock_code="99840",
        report_nm="有価証券報告書－第46期(2025/04/01－2026/03/31)", rcept_dt="2026-06-22",
        flr_nm="ソフトバンクグループ株式会社", pblntf_ty="030000", pblntf_detail_ty="120", ordinance_code="010",
        source_url="https://api.edinet-fsa.go.jp/api/v2/documents/S100YGH5",
        retrieved_at=_now_iso(), source_name="EDINET", original_language="Japanese",
    )
    _seed_edinet_filing_events(tmp_path, [filing])
    candidate = CandidateSignal(
        id="edinet-cand-ja-1", filing=filing, matched_rules=["annual_securities_report:010:030000:120"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="有価証券報告書の記載内容の抜粋です。",
        excerpt_translation=Translation(translated_text="This is an excerpt from the annual securities report.", provider="DeepL", source_lang="ja", target_lang="en", translated_at=_now_iso()),
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate}, "edinet_candidates.json")

    at = _run_radar(tmp_path)
    assert not at.exception
    all_text = _text(at)

    assert "SoftBank Group Corp." in all_text
    assert "99840" in all_text
    assert "Filed Jun 22, 2026" in all_text  # rcept_dt == "2026-06-22"
    assert "有価証券報告書の記載内容の抜粋です。" in all_text  # Original (native excerpt)
    assert "This is an excerpt from the annual securities report." not in all_text  # stored, but collapsed by default
    toggle_buttons = [b for b in at.button if b.label == "Show English translation"]
    assert len(toggle_buttons) == 1
    link_buttons = [b for b in at.get("link_button") if b.label == "Open original filing ↗"]
    assert len(link_buttons) == 1
    for forbidden in _FORBIDDEN_PUBLIC_STRINGS:
        assert forbidden not in all_text, forbidden
    # EDINET-specific technical metadata (ordinance/form/docType codes)
    # is never shown on the public card either.
    assert "Ordinance code" not in all_text
    assert "Form code" not in all_text
    assert "Document type code" not in all_text

    toggle_buttons[0].click()
    _rerun(at, tmp_path)
    all_text = _text(at)
    assert "This is an excerpt from the annual securities report." in all_text  # now expanded
    assert any(b.label == "Hide English translation" for b in at.button)


# ============================================================
# English (EDGAR) fixture — no redundant Original/English translation
# ============================================================


def test_english_edgar_fixture_has_no_redundant_translation_block(tmp_path):
    _seed_edgar_ciks(tmp_path)
    filing = FilingEvent(
        rcept_no="0001045810-26-000001", corp_code="0001045810", corp_name="NVIDIA", stock_code="NVDA",
        report_nm="8-K filing", rcept_dt="2026-08-12", flr_nm="NVIDIA", pblntf_ty="8-K",
        source_url="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000001/",
        retrieved_at=_now_iso(), source_name="SEC EDGAR", original_language="English", primary_document="nvda-8k.htm",
    )
    _seed_edgar_filing_events(tmp_path, filing)
    candidate = CandidateSignal(
        id="edgar-cand-en-1", filing=filing, matched_rules=["earnings_or_results:8-K item 2.02"], confidence="Moderate",
        status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="Item 2.02 Results of Operations. Revenue increased.",
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate}, "edgar_candidates.json")

    at = _run_radar(tmp_path)
    assert not at.exception
    all_text = _text(at)

    assert "NVIDIA" in all_text
    assert "NVDA" in all_text
    assert "Filed Aug 12, 2026" in all_text  # rcept_dt == "2026-08-12"
    assert "8-K filing" in all_text  # English filing title, shown directly (no translation needed)
    assert "Item 2.02 Results of Operations. Revenue increased." in all_text  # English excerpt, shown directly
    assert "<strong>Original</strong>" not in all_text
    assert "<strong>English translation</strong>" not in all_text
    assert not any(b.label in ("Show English translation", "Hide English translation") for b in at.button)
    for forbidden in _FORBIDDEN_PUBLIC_STRINGS:
        assert forbidden not in all_text, forbidden


# ============================================================
# Translation-being-prepared vs. terminal-failure display contract
# ============================================================


def test_no_stored_translation_shows_no_toggle_even_with_a_retry_scheduled(tmp_path):
    """Layout correction pass (design/DECISIONS.md): the toggle only ever
    appears when a translation is already stored — a scheduled automatic
    retry (translation_next_retry_at set) is worker-internal state this
    public card no longer surfaces at all, superseding the earlier
    "English translation is being prepared." messaging entirely."""
    _seed_corp_codes(tmp_path)
    filing = FilingEvent(
        rcept_no="20260812000002", corp_code="00126380", corp_name="삼성전자", stock_code="005930",
        report_nm="실적 발표", rcept_dt="20260812", flr_nm="삼성전자",
        source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260812000002",
        retrieved_at=_now_iso(),
    )
    _seed_dart_filing_events(tmp_path, [filing])
    from src.models.models import TranslationState

    candidate = CandidateSignal(
        id="cand-ko-retry", filing=filing, matched_rules=["earnings:earnings_or_results_report:실적"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="실적 관련 원문.",
        translation_state=TranslationState.UNAVAILABLE, translation_failure_category="rate_limit",
        translation_failure_reason="Translation provider rate limit exceeded.",
        translation_next_retry_at="2099-01-01T00:00:00+00:00",
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})

    at = _run_radar(tmp_path)
    assert not at.exception
    all_text = _text(at)
    assert "English translation is being prepared." not in all_text
    assert "Translation unavailable" not in all_text
    assert "rate_limit" not in all_text
    assert "실적 관련 원문." in all_text
    assert not any(b.label in ("Show English translation", "Hide English translation") for b in at.button)


def test_terminal_failure_shows_only_original_text_no_error_jargon(tmp_path):
    _seed_corp_codes(tmp_path)
    filing = FilingEvent(
        rcept_no="20260812000003", corp_code="00126380", corp_name="삼성전자", stock_code="005930",
        report_nm="실적 발표", rcept_dt="20260812", flr_nm="삼성전자",
        source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260812000003",
        retrieved_at=_now_iso(),
    )
    _seed_dart_filing_events(tmp_path, [filing])
    from src.models.models import TranslationState

    candidate = CandidateSignal(
        id="cand-ko-terminal", filing=filing, matched_rules=["earnings:earnings_or_results_report:실적"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="실적 관련 원문 종결.",
        translation_state=TranslationState.UNAVAILABLE, translation_failure_category="config_missing_key",
        translation_failure_reason="Translation API key is not configured.",
        translation_next_retry_at=None,
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})

    at = _run_radar(tmp_path)
    assert not at.exception
    all_text = _text(at)
    assert "실적 관련 원문 종결." in all_text
    assert "English translation is being prepared." not in all_text
    assert "English translation</strong>" not in all_text
    assert "Translation unavailable" not in all_text
    assert "config_missing_key" not in all_text
    assert "not configured" not in all_text
    assert not any(b.label in ("Show English translation", "Hide English translation") for b in at.button)


# ============================================================
# Filed date: present (uses the source's official filed date) vs.
# genuinely absent (renders nothing, never a fake/capture-time substitute)
# ============================================================


def test_filed_date_uses_source_filed_date_not_capture_timestamp(tmp_path):
    _seed_edgar_ciks(tmp_path)
    filing = FilingEvent(
        rcept_no="0001045810-26-000003", corp_code="0001045810", corp_name="NVIDIA", stock_code="NVDA",
        report_nm="8-K filing three", rcept_dt="2026-09-03", flr_nm="NVIDIA", pblntf_ty="8-K",
        source_url="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000003/",
        # Deliberately far apart from rcept_dt — proves the card uses the
        # official filed date, never this capture/retrieval timestamp.
        retrieved_at="2099-01-01T00:00:00+00:00",
        source_name="SEC EDGAR", original_language="English", primary_document="nvda-8k-3.htm",
    )
    _seed_edgar_filing_events(tmp_path, filing)
    candidate = CandidateSignal(
        id="edgar-cand-en-3", filing=filing, matched_rules=["earnings_or_results:8-K item 2.02"], confidence="Moderate",
        status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="Item 2.02 Results of Operations. Third filing.",
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate}, "edgar_candidates.json")

    at = _run_radar(tmp_path)
    assert not at.exception
    all_text = _text(at)
    assert "Filed Sep 3, 2026" in all_text
    assert "2099" not in all_text


def test_filed_date_renders_nothing_when_genuinely_absent():
    """Direct unit-level check of radar_card._filed_label: the full Radar
    Inbox page's own pre-existing "Filed between" filter would otherwise
    exclude any filing with no parseable rcept_dt from the results
    entirely before a card is ever rendered, which would make this
    behavior untestable through the full AppTest page route."""
    from src.ui.components import radar_card

    filing = FilingEvent(
        rcept_no="0001045810-26-000004", corp_code="0001045810", corp_name="NVIDIA", stock_code="NVDA",
        report_nm="8-K filing four", rcept_dt="", flr_nm="NVIDIA", pblntf_ty="8-K",
        source_url="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000004/",
        retrieved_at=_now_iso(), source_name="SEC EDGAR", original_language="English", primary_document="nvda-8k-4.htm",
    )
    assert radar_card._filed_label(filing) is None


# ============================================================
# Page-wide sweep: no removed label ever appears, across several shapes
# ============================================================


def test_no_forbidden_labels_appear_across_needs_review_not_material_and_deferred_candidates(tmp_path):
    _seed_corp_codes(tmp_path)
    needs_review_filing = FilingEvent(
        rcept_no="20260812000010", corp_code="00126380", corp_name="삼성전자", stock_code="005930",
        report_nm="신규시설투자등 결정", rcept_dt="20260812", flr_nm="삼성전자",
        source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260812000010", retrieved_at=_now_iso(),
    )
    not_material_filing = FilingEvent(
        rcept_no="20260812000011", corp_code="00126380", corp_name="삼성전자", stock_code="005930",
        report_nm="주식등의대량보유상황보고서(일반)", rcept_dt="20260812", flr_nm="삼성전자",
        source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260812000011", retrieved_at=_now_iso(),
    )
    deferred_filing = FilingEvent(
        rcept_no="20260812000012", corp_code="00126380", corp_name="삼성전자", stock_code="005930",
        report_nm="유상증자 결정", rcept_dt="20260812", flr_nm="삼성전자",
        source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260812000012", retrieved_at=_now_iso(),
    )
    _seed_dart_filing_events(tmp_path, [needs_review_filing, not_material_filing, deferred_filing])

    needs_review = CandidateSignal(
        id="cand-sweep-1", filing=needs_review_filing, matched_rules=["capex_or_facility_investment:facility_investment:신규시설투자"],
        confidence="High", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="신규시설투자등 발췌.",
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    not_material = CandidateSignal(
        id="cand-sweep-2", filing=not_material_filing, matched_rules=["ownership_change:major_shareholder_change:대량보유상황보고서"],
        confidence="Moderate", status=CandidateStatus.NOT_MATERIAL, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="대량보유상황 발췌.", materiality_assessment="Not material · routine ownership update",
        state_history=[StateTransition(status=CandidateStatus.NOT_MATERIAL, at=_now_iso())],
    )
    deferred = CandidateSignal(
        id="cand-sweep-3", filing=deferred_filing, matched_rules=["financing:capital_raise_or_treasury_stock:유상증자"],
        confidence="Moderate", status=CandidateStatus.PROCESSING_DEFERRED,
        state_history=[StateTransition(status=CandidateStatus.PROCESSING_DEFERRED, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {c.id: c for c in (needs_review, not_material, deferred)})

    at = _run_radar(tmp_path)
    assert not at.exception
    all_text = _text(at)
    for forbidden in _FORBIDDEN_PUBLIC_STRINGS:
        assert forbidden not in all_text, forbidden
