"""Radar simplicity + translation reliability + layout correction +
filing-quality workstreams — fixture-driven proofs of the exact public
card contract:

  a) company name and ticker/security code, with `Filed {date}` at the
     top-right when the filing's own official rcept_dt is parseable;
  b) a clean, deterministic display title (src.logic.filing_display.
     display_title — for EDGAR, a readable mapping from the official SEC
     form type; for DART/EDINET, unchanged: translated when stored,
     otherwise the native official title);
  c) `Summary` — always shown: a concise extractive summary grounded in
     stored, quality-gated readable text, or a neutral, factual
     "{Company} filed {title} on {date}." fallback otherwise;
  d) `View filing text` (EDGAR) or `Show English translation`/`View
     original filing text` (DART/EDINET) — display-only toggles, shown
     only when the corresponding stored text exists and (for any
     original-language/extracted text) passes the quality gate;
  e) `Open original filing ↗` — the sole action.

Covers a Korean (DART) fixture, a Japanese (EDINET) fixture, an English
(EDGAR) fixture, an EDGAR raw-XBRL-extraction fixture (the quality gate
must reject it), and a page-wide sweep proving none of the removed
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
    # Filing-quality pass (B): the Summary — generated or metadata-only —
    # must never make an investment conclusion, infer a financial result,
    # characterize importance, or use any of these words/phrases, and a
    # fallback Summary must never be labeled as a fallback. "material"/
    # "signal" are checked separately, against the card's own Summary
    # text only (see test_summary_wording_never_uses_prohibited_language
    # below) — the page's own pre-existing, unrelated subtitle ("...for
    # material filings... high-confidence signals.") legitimately
    # contains both words, so a whole-page sweep would false-positive.
    "review", "detected", "potential", "analysis",
    "metadata-only", "Phase 1", "pending", "unavailable",
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
    # Summary is grounded in the stored English translation, shown directly.
    assert "New facility investment related excerpt." in all_text
    assert "신규시설투자등 관련 원문 발췌." not in all_text  # native excerpt collapsed by default
    translation_toggle = [b for b in at.button if b.label == "Show English translation"]
    assert len(translation_toggle) == 1
    original_toggle = [b for b in at.button if b.label == "View original filing text"]
    assert len(original_toggle) == 1
    link_buttons = [b for b in at.get("link_button") if b.label == "Open original filing ↗"]
    assert len(link_buttons) == 1
    for forbidden in _FORBIDDEN_PUBLIC_STRINGS:
        assert forbidden not in all_text, forbidden

    original_toggle[0].click()
    _rerun(at, tmp_path)
    all_text = _text(at)
    assert "신규시설투자등 관련 원문 발췌." in all_text  # now expanded
    assert any(b.label == "Hide original filing text" for b in at.button)

    translation_toggle = [b for b in at.button if b.label == "Show English translation"]
    translation_toggle[0].click()
    _rerun(at, tmp_path)
    all_text = _text(at)
    assert "New facility investment related excerpt." in all_text  # still shown (Summary + expanded toggle)
    assert any(b.label == "Hide English translation" for b in at.button)
    assert any(b.label == "Hide original filing text" for b in at.button)  # first toggle stays expanded too


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
    # Summary is grounded in the stored English translation, shown directly.
    assert "This is an excerpt from the annual securities report." in all_text
    assert "有価証券報告書の記載内容の抜粋です。" not in all_text  # native excerpt collapsed by default
    translation_toggle = [b for b in at.button if b.label == "Show English translation"]
    assert len(translation_toggle) == 1
    original_toggle = [b for b in at.button if b.label == "View original filing text"]
    assert len(original_toggle) == 1
    link_buttons = [b for b in at.get("link_button") if b.label == "Open original filing ↗"]
    assert len(link_buttons) == 1
    for forbidden in _FORBIDDEN_PUBLIC_STRINGS:
        assert forbidden not in all_text, forbidden
    # EDINET-specific technical metadata (ordinance/form/docType codes)
    # is never shown on the public card either.
    assert "Ordinance code" not in all_text
    assert "Form code" not in all_text
    assert "Document type code" not in all_text

    original_toggle[0].click()
    _rerun(at, tmp_path)
    all_text = _text(at)
    assert "有価証券報告書の記載内容の抜粋です。" in all_text  # now expanded
    assert any(b.label == "Hide original filing text" for b in at.button)


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
    assert "Current Report — Form 8-K" in all_text  # clean, mapped EDGAR title — never the bare report_nm
    assert "8-K filing" not in all_text  # the old non-title report_nm string itself is never shown
    assert "Item 2.02 Results of Operations. Revenue increased." in all_text  # grounded Summary, shown directly
    assert "<strong>Original</strong>" not in all_text
    assert "<strong>English translation</strong>" not in all_text
    assert not any(b.label in ("Show English translation", "Hide English translation") for b in at.button)
    assert not any(b.label in ("View original filing text", "Hide original filing text") for b in at.button)
    view_filing_text_buttons = [b for b in at.button if b.label == "View filing text"]
    assert len(view_filing_text_buttons) == 1  # readable excerpt passes the quality gate
    for forbidden in _FORBIDDEN_PUBLIC_STRINGS:
        assert forbidden not in all_text, forbidden


# ============================================================
# "Open original filing" link — must open the primary EDGAR document,
# never the bare accession-directory listing; DART/EDINET unaffected
# ============================================================


def test_public_source_url_uses_primary_document_metadata_for_edgar(tmp_path):
    from src.ui.components.radar_card import _public_source_url

    filing = FilingEvent(
        rcept_no="0001045810-26-000078", corp_code="0001045810", corp_name="NVIDIA", stock_code="NVDA",
        report_nm="8-K filing", rcept_dt="2026-09-02", flr_nm="NVIDIA", pblntf_ty="8-K",
        source_url="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000078/",
        retrieved_at=_now_iso(), source_name="SEC EDGAR", original_language="English",
        primary_document="nvda-20260902.htm",
    )
    assert _public_source_url(filing) == (
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000078/nvda-20260902.htm"
    )


def test_public_source_url_falls_back_to_official_index_page_when_primary_document_absent(tmp_path):
    """No primary_document metadata — must never guess a filename from
    the ticker/company name, and must never link to the bare
    accession-directory listing either. Falls back to SEC's own uniform
    `{accession-no-dashes}-index.htm` filing index page, built only from
    the already-stored accession number (rcept_no)."""
    from src.ui.components.radar_card import _public_source_url

    filing = FilingEvent(
        rcept_no="0001045810-26-000078", corp_code="0001045810", corp_name="NVIDIA", stock_code="NVDA",
        report_nm="8-K filing", rcept_dt="2026-09-02", flr_nm="NVIDIA", pblntf_ty="8-K",
        source_url="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000078/",
        retrieved_at=_now_iso(), source_name="SEC EDGAR", original_language="English", primary_document="",
    )
    assert _public_source_url(filing) == (
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000078/"
        "0001045810-26-000078-index.htm"
    )


def test_public_source_url_leaves_edgar_url_unchanged_when_not_a_directory_link(tmp_path):
    """Defensive: an EDGAR source_url that doesn't have the expected
    trailing-slash directory shape (e.g. cik was unresolved at scan
    time, leaving source_url empty) is never rewritten or guessed at."""
    from src.ui.components.radar_card import _public_source_url

    filing = FilingEvent(
        rcept_no="0001045810-26-000079", corp_code="", corp_name="NVIDIA", stock_code="NVDA",
        report_nm="8-K filing", rcept_dt="2026-09-02", flr_nm="NVIDIA", pblntf_ty="8-K",
        source_url="", retrieved_at=_now_iso(), source_name="SEC EDGAR",
        original_language="English", primary_document="nvda-20260902.htm",
    )
    assert _public_source_url(filing) == ""


def test_public_source_url_leaves_dart_and_edinet_links_unchanged(tmp_path):
    from src.ui.components.radar_card import _public_source_url

    dart_filing = FilingEvent(
        rcept_no="20260812000010", corp_code="00126380", corp_name="삼성전자", stock_code="005930",
        report_nm="신규시설투자등 결정", rcept_dt="20260812", flr_nm="삼성전자",
        source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260812000010", retrieved_at=_now_iso(),
    )
    assert _public_source_url(dart_filing) == dart_filing.source_url

    edinet_filing = FilingEvent(
        rcept_no="S100YGH5", corp_code="E02778", corp_name="SoftBank Group Corp.", stock_code="99840",
        report_nm="有価証券報告書", rcept_dt="2026-06-22", flr_nm="ソフトバンクグループ株式会社",
        source_url="https://api.edinet-fsa.go.jp/api/v2/documents/S100YGH5",
        retrieved_at=_now_iso(), source_name="EDINET", original_language="Japanese",
    )
    assert _public_source_url(edinet_filing) == edinet_filing.source_url


def test_open_original_filing_button_links_to_primary_document_end_to_end(tmp_path):
    _seed_edgar_ciks(tmp_path)
    filing = FilingEvent(
        rcept_no="0001045810-26-000078", corp_code="0001045810", corp_name="NVIDIA", stock_code="NVDA",
        report_nm="8-K filing", rcept_dt="2026-09-02", flr_nm="NVIDIA", pblntf_ty="8-K",
        source_url="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000078/",
        retrieved_at=_now_iso(), source_name="SEC EDGAR", original_language="English",
        primary_document="nvda-20260902.htm",
    )
    _seed_edgar_filing_events(tmp_path, filing)

    at = _run_radar(tmp_path)
    assert not at.exception
    link_buttons = list(at.get("link_button"))
    assert len(link_buttons) == 1
    assert link_buttons[0].url == "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000078/nvda-20260902.htm"


def test_open_original_filing_button_links_to_index_page_when_no_primary_document(tmp_path):
    _seed_edgar_ciks(tmp_path)
    filing = FilingEvent(
        rcept_no="0001045810-26-000078", corp_code="0001045810", corp_name="NVIDIA", stock_code="NVDA",
        report_nm="8-K filing", rcept_dt="2026-09-02", flr_nm="NVIDIA", pblntf_ty="8-K",
        source_url="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000078/",
        retrieved_at=_now_iso(), source_name="SEC EDGAR", original_language="English", primary_document="",
    )
    _seed_edgar_filing_events(tmp_path, filing)

    at = _run_radar(tmp_path)
    assert not at.exception
    link_buttons = list(at.get("link_button"))
    assert len(link_buttons) == 1
    assert link_buttons[0].url == (
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000078/"
        "0001045810-26-000078-index.htm"
    )
    # Never the raw, unadorned accession-directory URL.
    assert link_buttons[0].url != filing.source_url


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
    # No stored translation — Summary falls back to the neutral metadata
    # sentence; the native excerpt is still reachable, but only behind
    # its own quality-gated toggle.
    assert "삼성전자 filed 실적 발표 on Aug 12, 2026." in all_text
    assert "실적 관련 원문." not in all_text
    assert not any(b.label in ("Show English translation", "Hide English translation") for b in at.button)
    original_toggle = [b for b in at.button if b.label == "View original filing text"]
    assert len(original_toggle) == 1

    original_toggle[0].click()
    _rerun(at, tmp_path)
    all_text = _text(at)
    assert "실적 관련 원문." in all_text


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
    assert "삼성전자 filed 실적 발표 on Aug 12, 2026." in all_text  # metadata-only Summary, no translation stored
    assert "실적 관련 원문 종결." not in all_text  # native excerpt collapsed behind its own toggle
    assert "English translation is being prepared." not in all_text
    assert "English translation</strong>" not in all_text
    assert "Translation unavailable" not in all_text
    assert "config_missing_key" not in all_text
    assert "not configured" not in all_text
    assert not any(b.label in ("Show English translation", "Hide English translation") for b in at.button)
    original_toggle = [b for b in at.button if b.label == "View original filing text"]
    assert len(original_toggle) == 1

    original_toggle[0].click()
    _rerun(at, tmp_path)
    all_text = _text(at)
    assert "실적 관련 원문 종결." in all_text


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


# ============================================================
# Summary wording (B) — checked directly against the pure functions
# rather than a whole-page sweep, since the page's own pre-existing,
# unrelated subtitle legitimately contains "material" and "signals".
# ============================================================


def test_summary_wording_never_uses_prohibited_language():
    from src.logic import filing_display

    prohibited = ("material", "signal", "review", "detected", "potential", "analysis")

    filing = FilingEvent(
        rcept_no="0001045810-26-000050", corp_code="0001045810", corp_name="NVIDIA", stock_code="NVDA",
        report_nm="10-Q", rcept_dt="2026-08-28", flr_nm="NVIDIA", pblntf_ty="10-Q",
        source_url="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000050/",
        retrieved_at=_now_iso(), source_name="SEC EDGAR", original_language="English",
    )
    title = filing_display.display_title(filing, None)
    metadata_summary = filing_display.metadata_only_summary(filing, title, "Aug 28, 2026")
    grounded_summary = filing_display.extractive_summary(
        "Item 2.02 Results of Operations. Revenue increased for the quarter compared to the prior year period."
    )

    for text in (title, metadata_summary, grounded_summary):
        for word in prohibited:
            assert word not in text.lower(), (word, text)


# ============================================================
# Extraction quality gate (C) — a Marvell-style raw XBRL/XML extraction
# must never reach the public card
# ============================================================


def test_mrvl_style_xbrl_extraction_is_rejected_and_never_shown(tmp_path):
    """Realistic shape of the defect this pass fixes: a Form 10-Q whose
    stored excerpt is dominated by XBRL context/namespace tags, a
    fasb.org taxonomy URL, and long machine-style element identifiers —
    exactly the kind of extraction leakage seen on a real MRVL 10-Q."""
    _seed_edgar_ciks(tmp_path)
    xbrl_extraction = (
        '<xbrli:context id="FD2026Q3QTD_us-gaap_StatementClassOfStockAxis">'
        '<xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0001835632</xbrli:identifier></xbrli:entity>'
        '<xbrli:period><xbrli:startDate>2026-07-04</xbrli:startDate><xbrli:endDate>2026-10-03</xbrli:endDate></xbrli:period>'
        '</xbrli:context> us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax contextRef="FD2026Q3QTD" '
        'unitRef="USD" decimals="-6">1234000000</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax> '
        'dei:EntityRegistrantName xmlns:dei="http://xbrl.sec.gov/dei/2026" xmlns:us-gaap="http://fasb.org/us-gaap/2026"'
    )
    filing = FilingEvent(
        rcept_no="0001835632-26-000042", corp_code="0001835632", corp_name="MARVELL TECHNOLOGY, INC.", stock_code="MRVL",
        report_nm="10-Q", rcept_dt="2026-08-28", flr_nm="MARVELL TECHNOLOGY, INC.", pblntf_ty="10-Q",
        source_url="https://www.sec.gov/Archives/edgar/data/1835632/000183563226000042/",
        retrieved_at=_now_iso(), source_name="SEC EDGAR", original_language="English", primary_document="mrvl-20261003.htm",
    )
    _seed_edgar_filing_events(tmp_path, filing)
    candidate = CandidateSignal(
        id="edgar-cand-mrvl-10q", filing=filing, matched_rules=["earnings_or_results:10-Q"], confidence="Moderate",
        status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original=xbrl_extraction,
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate}, "edgar_candidates.json")

    at = _run_radar(tmp_path)
    assert not at.exception
    all_text = _text(at)

    assert "MARVELL TECHNOLOGY, INC." in all_text
    assert "Quarterly Report — Form 10-Q" in all_text
    # Not one byte of the raw extraction ever reaches the rendered page.
    for fragment in ("xbrli:", "us-gaap:", "dei:", "fasb.org", "contextRef", "RevenueFromContractWithCustomer", "1234000000"):
        assert fragment not in all_text, fragment
    # The neutral, metadata-only Summary instead.
    assert "MARVELL TECHNOLOGY, INC. filed Quarterly Report — Form 10-Q on Aug 28, 2026." in all_text
    # No filing-text toggle — the rejected extraction has nothing to reveal.
    assert not any(b.label in ("View filing text", "Hide filing text") for b in at.button)
    for forbidden in _FORBIDDEN_PUBLIC_STRINGS:
        assert forbidden not in all_text, forbidden


def test_edgar_10q_uses_quarterly_report_title(tmp_path):
    _seed_edgar_ciks(tmp_path)
    filing = FilingEvent(
        rcept_no="0001045810-26-000090", corp_code="0001045810", corp_name="NVIDIA", stock_code="NVDA",
        report_nm="10-Q", rcept_dt="2026-08-28", flr_nm="NVIDIA", pblntf_ty="10-Q",
        source_url="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000090/",
        retrieved_at=_now_iso(), source_name="SEC EDGAR", original_language="English", primary_document="nvda-10q.htm",
    )
    _seed_edgar_filing_events(tmp_path, filing)

    at = _run_radar(tmp_path)
    assert not at.exception
    all_text = _text(at)
    assert "Quarterly Report — Form 10-Q" in all_text
    assert "10-Q" not in all_text.replace("Form 10-Q", "")  # the bare, non-title form code never stands alone
