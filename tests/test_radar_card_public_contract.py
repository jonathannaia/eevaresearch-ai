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
  d) `View filing excerpt` (EDGAR) or `View translated filing excerpt`/`View
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
    translation_toggle = [b for b in at.button if b.label == "View translated filing excerpt"]
    assert len(translation_toggle) == 1
    original_toggle = [b for b in at.button if b.label == "View original filing excerpt"]
    assert len(original_toggle) == 1
    link_buttons = [b for b in at.get("link_button") if b.label == "Open original filing ↗"]
    assert len(link_buttons) == 1
    for forbidden in _FORBIDDEN_PUBLIC_STRINGS:
        assert forbidden not in all_text, forbidden
    # DART reference block: provider-accurate labels, no EDINET wording.
    assert "Official filing reference" in all_text
    assert "DART issuer code: 00126380" in all_text
    assert "Receipt number: 20260812000001" in all_text
    assert "EDINET issuer code" not in all_text
    assert "To find this filing on EDINET" not in all_text

    original_toggle[0].click()
    _rerun(at, tmp_path)
    all_text = _text(at)
    assert "신규시설투자등 관련 원문 발췌." in all_text  # now expanded
    assert any(b.label == "Hide original filing excerpt" for b in at.button)

    translation_toggle = [b for b in at.button if b.label == "View translated filing excerpt"]
    translation_toggle[0].click()
    _rerun(at, tmp_path)
    all_text = _text(at)
    assert "New facility investment related excerpt." in all_text  # still shown (Summary + expanded toggle)
    assert any(b.label == "Hide translated filing excerpt" for b in at.button)
    assert any(b.label == "Hide original filing excerpt" for b in at.button)  # first toggle stays expanded too


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
    translation_toggle = [b for b in at.button if b.label == "View translated filing excerpt"]
    assert len(translation_toggle) == 1
    original_toggle = [b for b in at.button if b.label == "View original filing excerpt"]
    assert len(original_toggle) == 1
    # EDINET original-source-link fallback: no working direct document
    # link exists (verified live), so the card links to the official
    # search portal root instead of the old, now-broken "Open original
    # filing" action.
    assert not any(b.label == "Open original filing ↗" for b in at.get("link_button"))
    edinet_link_buttons = [b for b in at.get("link_button") if b.label == "Search original EDINET filing ↗"]
    assert len(edinet_link_buttons) == 1
    assert edinet_link_buttons[0].url == "https://disclosure2.edinet-fsa.go.jp/"
    assert "api.edinet-fsa.go.jp" not in all_text
    # Filing-card machine-artifact / excerpt-honesty fix: the old field-
    # listing locator line is gone, replaced by the fixed lookup-guidance
    # sentence plus the shared, provider-neutral reference block.
    assert "Official EDINET search:" not in all_text
    assert "To find this filing on EDINET, search by EDINET issuer code or securities code, then filter by filing date and type." in all_text
    assert "Official filing reference" in all_text
    assert "EDINET issuer code: E02778" in all_text
    assert "Securities code: 99840" in all_text
    assert "Document ID: S100YGH5" in all_text
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
    assert any(b.label == "Hide original filing excerpt" for b in at.button)


def test_edinet_unreadable_translation_hides_toggle_and_falls_back_to_metadata_summary(tmp_path):
    """Filing-card summary/translation presentation fix regression proof
    — modeled on the real production case (EDINET Extraordinary Report,
    ispace, inc., docID S100Z0OT): a long, itemized, non-narrative stored
    translation (real words, but structurally raw/document-like — no
    sentence-ending punctuation anywhere) must never render the "Show
    English translation" toggle, and the Summary must fall back to the
    honest metadata-only sentence rather than an ellipsis-terminated or
    partial fragment. The official EDINET source action must stay
    visible regardless."""
    filing = FilingEvent(
        rcept_no="S100Z0OT", corp_code="E12345", corp_name="ispace, inc.", stock_code="93480",
        report_nm="臨時報告書", rcept_dt="2026-07-01",
        flr_nm="株式会社ispace", pblntf_ty="180000", pblntf_detail_ty="010", ordinance_code="010",
        source_url="https://api.edinet-fsa.go.jp/api/v2/documents/S100Z0OT",
        retrieved_at=_now_iso(), source_name="EDINET", original_language="Japanese",
    )
    _seed_edinet_filing_events(tmp_path, [filing])
    raw_like_translation = (
        "Lender name Example Bank Ltd loan amount five hundred million yen "
        "use of proceeds working capital term five years interest rate variable "
        "collateral none guarantor none execution date 2026 07 01 repayment schedule "
        "lump sum at maturity governing law Japan "
    ) * 2  # long, ordinary real words, zero sentence-ending punctuation anywhere
    candidate = CandidateSignal(
        id="edinet-cand-ispace-1", filing=filing, matched_rules=["extraordinary_report:010:180000:010"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="臨時報告書の抜粋。",
        excerpt_translation=Translation(
            translated_text=raw_like_translation, provider="DeepL", source_lang="ja", target_lang="en", translated_at=_now_iso()
        ),
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate}, "edinet_candidates.json")

    at = _run_radar(tmp_path)
    assert not at.exception
    all_text = _text(at)

    assert "ispace, inc." in all_text
    assert "93480" in all_text
    # Summary must be the honest metadata-only fallback — never the raw
    # translation, never an ellipsis, never a partial sentence.
    assert "ispace, inc. filed 臨時報告書 on Jul 1, 2026." in all_text
    assert "…" not in all_text
    assert "..." not in all_text
    assert raw_like_translation not in all_text
    # The unreadable translation must never render its toggle at all.
    assert not any(b.label in ("View translated filing excerpt", "Hide translated filing excerpt") for b in at.button)
    # The official EDINET source action remains visible regardless.
    edinet_link_buttons = [b for b in at.get("link_button") if b.label == "Search original EDINET filing ↗"]
    assert len(edinet_link_buttons) == 1
    for forbidden in _FORBIDDEN_PUBLIC_STRINGS:
        assert forbidden not in all_text, forbidden


def test_edinet_readable_translation_with_no_early_boundary_falls_back_to_metadata_summary_but_keeps_toggle(tmp_path):
    """A stored translation can pass is_readable_extracted_text (it does
    contain a sentence terminator somewhere) while still giving
    extractive_summary() nothing usable within its own bounded 640-char
    search window — extractive_summary() then returns "", and it is the
    caller's (candidate_row's) responsibility to fall back to
    metadata_only_summary() rather than render that empty string. The
    toggle, gated only on readability (not on the Summary's own
    boundary search), still renders — the raw text is legitimately
    readable, just not summarizable within the bounded window."""
    filing = FilingEvent(
        rcept_no="S100Z0OT", corp_code="E12345", corp_name="ispace, inc.", stock_code="93480",
        report_nm="臨時報告書", rcept_dt="2026-07-01",
        flr_nm="株式会社ispace", pblntf_ty="180000", pblntf_detail_ty="010", ordinance_code="010",
        source_url="https://api.edinet-fsa.go.jp/api/v2/documents/S100Z0OT",
        retrieved_at=_now_iso(), source_name="EDINET", original_language="Japanese",
    )
    _seed_edinet_filing_events(tmp_path, [filing])
    late_boundary_translation = ("filler word " * 60) + "Sentence finally ends far too late."
    candidate = CandidateSignal(
        id="edinet-cand-ispace-3", filing=filing, matched_rules=["extraordinary_report:010:180000:010"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="臨時報告書の抜粋。",
        excerpt_translation=Translation(
            translated_text=late_boundary_translation, provider="DeepL", source_lang="ja", target_lang="en", translated_at=_now_iso()
        ),
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate}, "edinet_candidates.json")

    at = _run_radar(tmp_path)
    assert not at.exception
    all_text = _text(at)

    assert "ispace, inc. filed 臨時報告書 on Jul 1, 2026." in all_text
    assert "…" not in all_text
    assert "..." not in all_text
    assert late_boundary_translation not in all_text
    # Readable, just not summarizable in-window — the toggle still renders.
    translation_toggle = [b for b in at.button if b.label == "View translated filing excerpt"]
    assert len(translation_toggle) == 1
    edinet_link_buttons = [b for b in at.get("link_button") if b.label == "Search original EDINET filing ↗"]
    assert len(edinet_link_buttons) == 1


def test_edinet_readable_translation_still_renders_the_toggle_regardless_of_summary_boundary(tmp_path):
    """Companion proof to the unreadable-translation case above: ordinary
    readable prose (short, real sentences) must still render the "Show
    English translation" toggle exactly as before this fix — the new
    readability gate on the toggle must not over-filter legitimate
    translations. Uses the same ispace/S100Z0OT identity so the two
    tests are directly comparable."""
    filing = FilingEvent(
        rcept_no="S100Z0OT", corp_code="E12345", corp_name="ispace, inc.", stock_code="93480",
        report_nm="臨時報告書", rcept_dt="2026-07-01",
        flr_nm="株式会社ispace", pblntf_ty="180000", pblntf_detail_ty="010", ordinance_code="010",
        source_url="https://api.edinet-fsa.go.jp/api/v2/documents/S100Z0OT",
        retrieved_at=_now_iso(), source_name="EDINET", original_language="Japanese",
    )
    _seed_edinet_filing_events(tmp_path, [filing])
    candidate = CandidateSignal(
        id="edinet-cand-ispace-2", filing=filing, matched_rules=["extraordinary_report:010:180000:010"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="臨時報告書の抜粋。",
        excerpt_translation=Translation(
            translated_text="The company entered into a loan agreement with a lender for working capital.",
            provider="DeepL", source_lang="ja", target_lang="en", translated_at=_now_iso(),
        ),
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate}, "edinet_candidates.json")

    at = _run_radar(tmp_path)
    assert not at.exception
    all_text = _text(at)

    assert "The company entered into a loan agreement with a lender for working capital." in all_text
    translation_toggle = [b for b in at.button if b.label == "View translated filing excerpt"]
    assert len(translation_toggle) == 1
    edinet_link_buttons = [b for b in at.get("link_button") if b.label == "Search original EDINET filing ↗"]
    assert len(edinet_link_buttons) == 1

    translation_toggle[0].click()
    _rerun(at, tmp_path)
    all_text = _text(at)
    assert any(b.label == "Hide translated filing excerpt" for b in at.button)
    # The source action stays visible with the toggle expanded too.
    edinet_link_buttons = [b for b in at.get("link_button") if b.label == "Search original EDINET filing ↗"]
    assert len(edinet_link_buttons) == 1


# ============================================================
# Filing-card machine-artifact / excerpt-honesty fix — S100Z0OT
# acceptance proof, using the real evidenced identity and leaked shape.
# ============================================================


def _s100z0ot_filing() -> FilingEvent:
    return FilingEvent(
        rcept_no="S100Z0OT", corp_code="E37584", corp_name="ispace, inc.", stock_code="93480",
        report_nm="臨時報告書", rcept_dt="2026-09-10",
        flr_nm="株式会社ispace", pblntf_ty="180000", pblntf_detail_ty="010", ordinance_code="010",
        source_url="https://api.edinet-fsa.go.jp/api/v2/documents/S100Z0OT",
        retrieved_at=_now_iso(), source_name="EDINET", original_language="Japanese",
    )


def test_s100z0ot_leaked_machine_artifacts_never_appear_and_reference_block_is_correct(tmp_path):
    """Full acceptance proof for the live regression: the leaked cover-
    page label and item-heading artifacts (both languages) never appear
    anywhere on the card, the default Summary is a clean, complete
    sentence, the excerpt toggles are honestly labeled and (since this
    excerpt reaches the 600-char extraction cap) carry the completeness
    disclosure when expanded, the source action stays visible throughout,
    and the reference block plus the one EDINET lookup-guidance sentence
    show exactly the real evidenced identity."""
    filing = _s100z0ot_filing()
    _seed_edinet_filing_events(tmp_path, [filing])
    leaked_native = (
        "臨時報告書_20260909153311 １【提出理由】本日開催の取締役会において、"
        + ("資金を借り入れることを決議した。" * 40)
    )
    leaked_translation = (
        "Extraordinary Report_20260909153311 1 [Reason for Submission] "
        "The Company resolved to borrow long-term funds from Shizuoka Bank "
        "in the amount of three billion yen at a floating interest rate "
        "over a three-year term for working-capital and Mission-related "
        "purposes, repayable in a lump sum, unsecured and unguaranteed."
    )
    assert len(leaked_native) >= 600  # exercises the completeness disclosure
    candidate = CandidateSignal(
        id="edinet-cand-s100z0ot", filing=filing, matched_rules=["extraordinary_report:010:180000:010"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original=leaked_native,
        excerpt_translation=Translation(
            translated_text=leaked_translation, provider="DeepL", source_lang="ja", target_lang="en", translated_at=_now_iso(),
        ),
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate}, "edinet_candidates.json")

    at = _run_radar(tmp_path)
    assert not at.exception
    all_text = _text(at)

    for leaked in (
        "Extraordinary Report_20260909153311", "[Reason for Submission]",
        "臨時報告書_20260909153311", "１【提出理由】",
    ):
        assert leaked not in all_text, leaked

    assert "ispace, inc." in all_text
    assert "93480" in all_text
    # Clean, complete, grounded summary — no leaked artifact prefix.
    assert "Shizuoka Bank" in all_text
    assert "three billion yen" in all_text

    translation_toggle = [b for b in at.button if b.label == "View translated filing excerpt"]
    assert len(translation_toggle) == 1
    original_toggle = [b for b in at.button if b.label == "View original filing excerpt"]
    assert len(original_toggle) == 1

    # Source action visible before any toggle is opened.
    edinet_link_buttons = [b for b in at.get("link_button") if b.label == "Search original EDINET filing ↗"]
    assert len(edinet_link_buttons) == 1

    # Official filing reference — exact real evidenced identity.
    assert "Official filing reference" in all_text
    assert "EDINET issuer code: E37584" in all_text
    assert "Securities code: 93480" in all_text
    assert "Document ID: S100Z0OT" in all_text
    assert "Filed: Sep 10, 2026" in all_text
    assert (
        "To find this filing on EDINET, search by EDINET issuer code or "
        "securities code, then filter by filing date and type."
    ) in all_text

    # Completeness disclosure absent while every toggle is collapsed.
    assert "Excerpt may be incomplete" not in all_text

    translation_toggle[0].click()
    _rerun(at, tmp_path)
    all_text = _text(at)
    for leaked in ("Extraordinary Report_20260909153311", "[Reason for Submission]"):
        assert leaked not in all_text, leaked
    assert "Excerpt may be incomplete. Open the official filing for the full document." in all_text
    edinet_link_buttons = [b for b in at.get("link_button") if b.label == "Search original EDINET filing ↗"]
    assert len(edinet_link_buttons) == 1  # source action still visible, translated excerpt expanded

    original_toggle = [b for b in at.button if b.label == "View original filing excerpt"]
    original_toggle[0].click()
    _rerun(at, tmp_path)
    all_text = _text(at)
    for leaked in ("臨時報告書_20260909153311", "１【提出理由】"):
        assert leaked not in all_text, leaked
    assert "Excerpt may be incomplete. Open the official filing for the full document." in all_text
    edinet_link_buttons = [b for b in at.get("link_button") if b.label == "Search original EDINET filing ↗"]
    assert len(edinet_link_buttons) == 1  # source action still visible, both excerpts expanded


def test_edinet_machine_artifact_cleanup_never_applies_to_dart_or_edgar(tmp_path):
    """The same leading artifact shape, seeded on a DART filing, must be
    preserved verbatim — the cleanup only ever runs for source_name ==
    "EDINET". DART/EDGAR cards also receive no EDINET-only reference
    label or lookup-guidance sentence."""
    _seed_corp_codes(tmp_path)
    dart_filing = FilingEvent(
        rcept_no="20260812000099", corp_code="00126380", corp_name="삼성전자", stock_code="005930",
        report_nm="신규시설투자등 결정", rcept_dt="20260812", flr_nm="삼성전자",
        source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260812000099",
        retrieved_at=_now_iso(),
    )
    _seed_dart_filing_events(tmp_path, [dart_filing])
    artifact_shaped_translation = "Extraordinary Report_20260909153311 1 [Reason for Submission] The Company borrowed funds."
    dart_candidate = CandidateSignal(
        id="cand-dart-artifact-shaped", filing=dart_filing, matched_rules=["capex_or_facility_investment:facility_investment:신규시설투자"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="신규시설투자등 관련 원문 발췌.",
        excerpt_translation=Translation(
            translated_text=artifact_shaped_translation, provider="DeepL", source_lang="ko", target_lang="en", translated_at=_now_iso(),
        ),
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {dart_candidate.id: dart_candidate})

    at = _run_radar(tmp_path)
    assert not at.exception
    all_text = _text(at)

    # DART's translated excerpt is NOT cleaned — the shape survives
    # verbatim in the Summary (the whole thing is one sentence, no early
    # terminator until the very end, so it is used whole).
    assert "Extraordinary Report_20260909153311 1 [Reason for Submission] The Company borrowed funds." in all_text
    assert "DART issuer code: 00126380" in all_text
    assert "EDINET issuer code" not in all_text
    assert "To find this filing on EDINET" not in all_text


def test_edgar_card_receives_no_edinet_reference_label_or_lookup_guidance(tmp_path):
    _seed_edgar_ciks(tmp_path)
    filing = FilingEvent(
        rcept_no="0001045810-26-000002", corp_code="0001045810", corp_name="NVIDIA", stock_code="NVDA",
        report_nm="8-K filing", rcept_dt="2026-08-28", flr_nm="NVIDIA", pblntf_ty="8-K",
        source_url="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000002/",
        retrieved_at=_now_iso(), source_name="SEC EDGAR", original_language="English", primary_document="nvda-8k.htm",
    )
    _seed_edgar_filing_events(tmp_path, filing)
    candidate = CandidateSignal(
        id="cand-edgar-no-edinet-1", filing=filing, matched_rules=["financing:new_debt:credit facility"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="The Company entered into a new credit facility for working capital purposes.",
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate}, "edgar_candidates.json")

    at = _run_radar(tmp_path)
    assert not at.exception
    all_text = _text(at)

    assert "CIK: 0001045810" in all_text
    assert "EDINET issuer code" not in all_text
    assert "DART issuer code" not in all_text
    assert "To find this filing on EDINET" not in all_text


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
    assert "<strong>Translated filing excerpt</strong>" not in all_text
    assert not any(b.label in ("View translated filing excerpt", "Hide translated filing excerpt") for b in at.button)
    assert not any(b.label in ("View original filing excerpt", "Hide original filing excerpt") for b in at.button)
    view_filing_text_buttons = [b for b in at.button if b.label == "View filing excerpt"]
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
# EDINET original-source-link fallback — no working direct document
# link exists (verified live against disclosure2.edinet-fsa.go.jp: the
# per-row "PDF表示" action is a session-bound JS postback keyed to an
# opaque per-render token, not a URL derivable from stored metadata).
# The card links to the official search portal root instead of the
# broken authenticated-API link, plus a non-clickable locator line.
# ============================================================


def test_edinet_render_quiet_links_uses_new_label_and_portal_root_never_old_label_or_api_url():
    from src.ui.components.radar_card import _render_quiet_links  # noqa: F401  (imported for AppTest script below)

    script = """
from src.models.models import FilingEvent
from src.ui.components.radar_card import _render_quiet_links

filing = FilingEvent(
    rcept_no="S100YGH5", corp_code="E02778", corp_name="SoftBank Group Corp.", stock_code="99840",
    report_nm="有価証券報告書－第46期(2025/04/01－2026/03/31)", rcept_dt="2026-06-22", flr_nm="ソフトバンクグループ株式会社",
    source_url="https://api.edinet-fsa.go.jp/api/v2/documents/S100YGH5",
    retrieved_at="2026-06-22T00:00:00+00:00", source_name="EDINET", original_language="Japanese",
)
_render_quiet_links(filing, "Jun 22, 2026")
"""
    at = AppTest.from_string(script, default_timeout=10)
    at.run()
    assert not at.exception

    link_buttons = list(at.get("link_button"))
    assert len(link_buttons) == 1
    assert link_buttons[0].label == "Search original EDINET filing ↗"
    assert link_buttons[0].url == "https://disclosure2.edinet-fsa.go.jp/"

    all_text = " ".join(m.value for m in at.markdown if not m.value.startswith("<style>"))
    assert "Open original filing ↗" not in all_text
    assert "api.edinet-fsa.go.jp" not in all_text
    assert "S100YGH5" not in all_text  # docID/private token never leaked into the locator line either
    # Filing-card machine-artifact / excerpt-honesty fix: the old field-
    # listing locator line is gone; _edinet_locator_line now returns the
    # fixed lookup-guidance sentence instead (see its own docstring for
    # why the field-listing role moved to the shared, provider-neutral
    # official_filing_reference block, rendered separately).
    assert "Official EDINET search:" not in all_text
    assert (
        "To find this filing on EDINET, search by EDINET issuer code or "
        "securities code, then filter by filing date and type."
    ) in all_text


def test_edinet_locator_line_returns_the_fixed_guidance_sentence_regardless_of_filing_fields():
    """Filing-card machine-artifact / excerpt-honesty fix: the old field-
    listing locator (filer name, native title, codes, filed date) is
    replaced by one fixed, concise guidance sentence — the field-listing
    role now lives in filing_display.official_filing_reference instead
    (rendered separately, for all three providers, not just EDINET).
    `_edinet_locator_line` therefore can no longer echo a stored English
    title_translation (it reads no filing field at all any more, so
    there is structurally nothing left for it to leak), and it returns
    the identical sentence whether every field is present or every field
    is empty/absent — never a placeholder, never an omission decision to
    get wrong."""
    from src.ui.components.radar_card import _edinet_locator_line

    expected = (
        "To find this filing on EDINET, search by EDINET issuer code or "
        "securities code, then filter by filing date and type."
    )

    full = FilingEvent(
        rcept_no="S100YGH5", corp_code="E02778", corp_name="SoftBank Group Corp.", stock_code="99840",
        report_nm="有価証券報告書－第46期(2025/04/01－2026/03/31)", rcept_dt="2026-06-22",
        flr_nm="ソフトバンクグループ株式会社", retrieved_at=_now_iso(), source_name="EDINET",
    )
    locator = _edinet_locator_line(full, "Jun 22, 2026")
    assert locator == expected
    assert "有価証券報告書" not in locator
    assert "ソフトバンクグループ株式会社" not in locator
    assert "Annual Securities Report" not in locator

    empty = FilingEvent(
        rcept_no="S100YGH7", corp_code="", corp_name="No Codes Corp.", stock_code="",
        report_nm="", rcept_dt="", flr_nm="", retrieved_at=_now_iso(), source_name="EDINET",
    )
    assert _edinet_locator_line(empty, None) == expected


def test_edgar_and_dart_render_quiet_links_unchanged_by_edinet_fallback():
    """The EDINET-specific branch in `_render_quiet_links` must never
    affect EDGAR or DART — both still render the original "Open original
    filing ↗" action pointing at `_public_source_url`, exactly as before
    this fallback existed."""
    script = """
from src.models.models import FilingEvent
from src.ui.components.radar_card import _render_quiet_links

edgar_filing = FilingEvent(
    rcept_no="0001045810-26-000078", corp_code="0001045810", corp_name="NVIDIA", stock_code="NVDA",
    report_nm="8-K filing", rcept_dt="2026-09-02", flr_nm="NVIDIA", pblntf_ty="8-K",
    source_url="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000078/",
    retrieved_at="2026-09-02T00:00:00+00:00", source_name="SEC EDGAR", original_language="English",
    primary_document="nvda-20260902.htm",
)
_render_quiet_links(edgar_filing, "Sep 2, 2026")

dart_filing = FilingEvent(
    rcept_no="20260812000010", corp_code="00126380", corp_name="삼성전자", stock_code="005930",
    report_nm="신규시설투자등 결정", rcept_dt="20260812", flr_nm="삼성전자",
    source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260812000010",
    retrieved_at="2026-08-12T00:00:00+00:00",
)
_render_quiet_links(dart_filing, "Aug 12, 2026")
"""
    at = AppTest.from_string(script, default_timeout=10)
    at.run()
    assert not at.exception

    link_buttons = list(at.get("link_button"))
    assert len(link_buttons) == 2
    assert {b.label for b in link_buttons} == {"Open original filing ↗"}
    assert {b.url for b in link_buttons} == {
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000078/nvda-20260902.htm",
        "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260812000010",
    }
    all_text = " ".join(m.value for m in at.markdown if not m.value.startswith("<style>"))
    assert "Search original EDINET filing ↗" not in all_text
    assert "Official EDINET search:" not in all_text


# ============================================================
# EDINET shares DART's exact title/Summary/translation-toggle mechanism
# — no EDINET-only translation control, label, or card mode. Confirms
# byte-for-byte parity with test_korean_fixture_shows_only_the_approved_
# fields above: same toggle labels, same default-shows-translation-when-
# stored / original-behind-a-toggle contract, and (this section's own
# addition) the same title_translation-by-default behavior, which the
# pre-existing Japanese fixture test above never exercised (it left
# title_translation unset).
# ============================================================


def test_edinet_with_stored_title_and_excerpt_translation_matches_dart_default_and_toggle_contract(tmp_path):
    filing = FilingEvent(
        rcept_no="S100Z0ID", corp_code="E00776", corp_name="Shin-Etsu Chemical Co., Ltd.", stock_code="40630",
        report_nm="自己株券買付状況報告書（法２４条の６第１項に基づくもの）", rcept_dt="2026-09-04",
        flr_nm="信越化学工業株式会社", pblntf_ty="170000", pblntf_detail_ty="220", ordinance_code="010",
        source_url="https://api.edinet-fsa.go.jp/api/v2/documents/S100Z0ID",
        retrieved_at=_now_iso(), source_name="EDINET", original_language="Japanese",
    )
    _seed_edinet_filing_events(tmp_path, [filing])
    candidate = CandidateSignal(
        id="edinet-cand-buyback-1", filing=filing, matched_rules=["share_buyback_status:010:170000:220"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="自己株券買付状況報告書の記載内容の抜粋です。",
        title_translation=Translation(translated_text="Status Report of Purchase of Own Shares", provider="DeepL", source_lang="ja", target_lang="en", translated_at=_now_iso()),
        excerpt_translation=Translation(translated_text="This is an excerpt from the status report of purchase of own shares.", provider="DeepL", source_lang="ja", target_lang="en", translated_at=_now_iso()),
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate}, "edinet_candidates.json")

    at = _run_radar(tmp_path)
    assert not at.exception
    all_text = _text(at)

    # Default state: translated title + translated excerpt shown, exactly
    # like DART's own default (test_korean_fixture_shows_only_the_
    # approved_fields) — no EDINET-only "default to original" behavior.
    # The card's own title element specifically (er-card-title) must be
    # the translated title — not just "present somewhere on the page",
    # since the native title legitimately also appears in the locator
    # line below (checked separately further down).
    card_title = next(m.value for m in at.markdown if 'class="er-card-title"' in m.value)
    assert "Status Report of Purchase of Own Shares" in card_title
    assert "自己株券買付状況報告書" not in card_title
    assert "This is an excerpt from the status report of purchase of own shares." in all_text
    assert "自己株券買付状況報告書の記載内容の抜粋です。" not in all_text  # native excerpt collapsed by default

    # Same two toggle labels DART uses — no EDINET-only "Translate"/"Show
    # original" control was introduced.
    translation_toggle = [b for b in at.button if b.label == "View translated filing excerpt"]
    assert len(translation_toggle) == 1
    original_toggle = [b for b in at.button if b.label == "View original filing excerpt"]
    assert len(original_toggle) == 1

    # Filing-card machine-artifact / excerpt-honesty fix: the locator is
    # now a fixed guidance sentence, carrying no filing-specific text at
    # all — it structurally cannot echo the translated title (or the
    # native one either). The provider-neutral reference block supplies
    # the actual identifying fields instead.
    assert (
        "To find this filing on EDINET, search by EDINET issuer code or "
        "securities code, then filter by filing date and type."
    ) in all_text
    assert "Official filing reference" in all_text
    assert "EDINET issuer code: E00776" in all_text
    assert "Status Report of Purchase of Own Shares" not in all_text.split("Official filing reference")[-1]

    original_toggle[0].click()
    _rerun(at, tmp_path)
    all_text = _text(at)
    assert "自己株券買付状況報告書の記載内容の抜粋です。" in all_text  # native excerpt now revealed
    assert any(b.label == "Hide original filing excerpt" for b in at.button)


def test_edinet_with_no_stored_translation_shows_native_title_and_excerpt_matching_dart_untranslated_contract(tmp_path):
    """No title_translation/excerpt_translation stored at all — matches
    DART's own no-translation contract exactly (test_no_stored_
    translation_shows_no_toggle_even_with_a_retry_scheduled below): the
    native title/report_nm is shown (display_title's own fallback), the
    Summary falls back to the neutral metadata sentence, and only the
    original-text toggle appears — no "View translated filing excerpt" toggle
    at all, since there is nothing translated to show."""
    filing = FilingEvent(
        rcept_no="S100Z0ID2", corp_code="E00776", corp_name="Shin-Etsu Chemical Co., Ltd.", stock_code="40630",
        report_nm="自己株券買付状況報告書（法２４条の６第１項に基づくもの）", rcept_dt="2026-09-04",
        flr_nm="信越化学工業株式会社", pblntf_ty="170000", pblntf_detail_ty="220", ordinance_code="010",
        source_url="https://api.edinet-fsa.go.jp/api/v2/documents/S100Z0ID2",
        retrieved_at=_now_iso(), source_name="EDINET", original_language="Japanese",
    )
    _seed_edinet_filing_events(tmp_path, [filing])
    candidate = CandidateSignal(
        id="edinet-cand-buyback-2", filing=filing, matched_rules=["share_buyback_status:010:170000:220"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="自己株券買付状況報告書の記載内容の抜粋です。",
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate}, "edinet_candidates.json")

    at = _run_radar(tmp_path)
    assert not at.exception
    all_text = _text(at)

    assert "自己株券買付状況報告書（法２４条の６第１項に基づくもの）" in all_text  # native title (no translation stored)
    assert not any(b.label in ("View translated filing excerpt", "Hide translated filing excerpt") for b in at.button)
    original_toggle = [b for b in at.button if b.label == "View original filing excerpt"]
    assert len(original_toggle) == 1


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
    assert not any(b.label in ("View translated filing excerpt", "Hide translated filing excerpt") for b in at.button)
    original_toggle = [b for b in at.button if b.label == "View original filing excerpt"]
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
    assert "Translated filing excerpt</strong>" not in all_text
    assert "Translation unavailable" not in all_text
    assert "config_missing_key" not in all_text
    assert "not configured" not in all_text
    assert not any(b.label in ("View translated filing excerpt", "Hide translated filing excerpt") for b in at.button)
    original_toggle = [b for b in at.button if b.label == "View original filing excerpt"]
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
    assert not any(b.label in ("View filing excerpt", "Hide filing excerpt") for b in at.button)
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
