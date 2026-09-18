"""Redesign v2 — materiality/status consistency across the redesigned
surfaces, using the Wonik IPS treasury-share-disposal case as the
regression fixture (the same constructed title/excerpt/status shape
tests/test_radar_inbox_page.py and tests/test_equity_transaction_
materiality.py already use — never a real cached document).

The non-negotiable policy under test: a CandidateStatus.NOT_MATERIAL
filing never appears on a default research-facing surface (Filings,
Dashboard Regional Brief, Dashboard Theme Activity), exposes no
escalation control, and — where the audited record is re-included behind
the explicit Filings control — is labelled with its actual category
("Treasury Stock Disposal or Acquisition"), never as a capital raise /
dividend decision. All against tmp_path; no network, no scan."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.data_access.dart import candidate_store
from src.logic import filing_display
from src.logic.filing_visibility import is_not_material, not_material_rcept_nos
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, FilingEvent, StateTransition
from src.ui.components import recent_theme_activity, regional_brief
from src.ui.pages import radar_inbox

_FILINGS_HARNESS = Path(__file__).parent / "apptest_pages" / "radar_inbox_page.py"
_DASHBOARD_HARNESS = Path(__file__).parent / "apptest_pages" / "dashboard_page.py"

_WONIK_IPS_TITLE = "주요사항보고서(자기주식처분결정)"
_WONIK_IPS_EXCERPT_CONSTRUCTED = (
    "자기주식처분결정 1. 처분예정주식(주) 보통주식 51,456 2. 처분예정금액(원) 6,143,846,400 "
    "3. 처분목적 임직원 성과급 지급을 위한 자기주식 처분 4. 처분방법 시간외대량매매"
)
_MATERIAL_TITLE = "주요사항보고서(신규시설투자등)"
_TREASURY_RCEPT = "20260917000122"
_MATERIAL_RCEPT = "20260917000123"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture(autouse=True)
def _clear_caches_and_guard_live_calls(monkeypatch):
    radar_inbox._load_dashboard_snapshot.clear()
    from src.data_access.dart import radar_service
    from src.data_access.edgar import edgar_service
    from src.data_access.edinet import edinet_service

    def _never(*args, **kwargs):
        raise AssertionError("live scan attempted during a render test")

    for module in (radar_service, edgar_service, edinet_service):
        for name in ("run_scan", "process_candidate_now"):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, _never)
    yield
    radar_inbox._load_dashboard_snapshot.clear()


def _filing(rcept_no: str, report_nm: str, rcept_dt: str = "20260917") -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code="01135941", corp_name="원익IPS", stock_code="240810", report_nm=report_nm,
        rcept_dt=rcept_dt, flr_nm="원익IPS", theme_slug="memory", source_name="OpenDART / DART",
        original_language="Korean", source_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
        retrieved_at=_now_iso(),
    )


def _seed_filing_events(cache_dir: Path, filings: list[FilingEvent]) -> None:
    payload = {
        "seen_receipt_numbers": [f.rcept_no for f in filings],
        "filing_events": [asdict(f) for f in filings],
        "candidate_signals": [],
    }
    (cache_dir / "dart_filing_events.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _treasury_candidate(filing: FilingEvent) -> CandidateSignal:
    return CandidateSignal(
        id="cand-wonik-treasury", filing=filing,
        matched_rules=["treasury_stock_activity:treasury_stock_disposal_or_acquisition:자기주식처분"],
        confidence="Moderate", status=CandidateStatus.NOT_MATERIAL, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original=_WONIK_IPS_EXCERPT_CONSTRUCTED,
        materiality_assessment="Not material · routine internal equity transaction",
        state_history=[StateTransition(status=CandidateStatus.NOT_MATERIAL, at=_now_iso())],
    )


def _material_candidate(filing: FilingEvent) -> CandidateSignal:
    return CandidateSignal(
        id="cand-wonik-capex", filing=filing,
        matched_rules=["capex_or_facility_investment:new_facility_investment:신규시설투자"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="신규시설투자등 1. 투자구분 신규시설투자 2. 투자금액(원) 120,000,000,000 3. 투자목적 생산능력 확대.",
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )


def _seed(cache_dir: Path) -> tuple[FilingEvent, FilingEvent]:
    treasury, material = _filing(_TREASURY_RCEPT, _WONIK_IPS_TITLE), _filing(_MATERIAL_RCEPT, _MATERIAL_TITLE)
    _seed_filing_events(cache_dir, [treasury, material])
    candidate_store.save_candidates(cache_dir, {c.id: c for c in (_treasury_candidate(treasury), _material_candidate(material))})
    return treasury, material


def _settings(cache_dir: Path) -> Settings:
    return Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=cache_dir)


# --- the gate itself -----------------------------------------------------------

def test_visibility_gate_reads_only_the_persisted_not_material_status(tmp_path):
    treasury, material = _seed(tmp_path)
    assert not_material_rcept_nos(_settings(tmp_path), "OpenDART / DART") == frozenset({_TREASURY_RCEPT})
    assert is_not_material(_treasury_candidate(treasury)) and not is_not_material(_material_candidate(material))
    assert is_not_material(None) is False


def test_treasury_only_candidate_uses_its_actual_category_label_never_capital_raise():
    filing = _filing(_TREASURY_RCEPT, _WONIK_IPS_TITLE)
    label = filing_display.display_category_label(filing, _treasury_candidate(filing))
    assert label == "Treasury Stock Disposal or Acquisition"
    assert "Capital Raise" not in label and "Dividend" not in label


# --- Dashboard: Regional Brief + Theme Activity ----------------------------------

def test_regional_brief_excludes_the_not_material_treasury_filing_but_keeps_the_material_one(tmp_path):
    _seed(tmp_path)
    shown = regional_brief._load_recent_filings("OpenDART / DART", _settings(tmp_path))
    assert [f.rcept_no for f in shown] == [_MATERIAL_RCEPT]


def test_theme_activity_never_counts_or_surfaces_the_not_material_treasury_filing(tmp_path):
    _seed(tmp_path)
    items = recent_theme_activity._load_filing_items(_settings(tmp_path))
    dart_items = [i for i in items if i.company_name == "원익IPS"]
    assert len(dart_items) == 1 and dart_items[0].timestamp is not None
    assert all(_WONIK_IPS_TITLE not in (i.edinet_title or "") for i in dart_items)


# --- Filings page --------------------------------------------------------------------

def _run_filings(cache_dir: Path, include_admin: bool = False) -> AppTest:
    """Renders Filings against tmp_path; a toggle rerun stays inside the
    settings patch so it reads the same seeded store."""
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=_settings(cache_dir)):
        at = AppTest.from_file(str(_FILINGS_HARNESS), default_timeout=15)
        at.run()
        if include_admin:
            assert not at.exception, at.exception
            at.checkbox(key="radar-filter-include-admin").check().run()
    assert not at.exception, at.exception
    return at


def _text(at: AppTest) -> str:
    return " ".join(m.value for m in at.markdown)


def test_filings_default_list_excludes_the_treasury_disposal_and_shows_the_material_filing(tmp_path):
    _seed(tmp_path)
    at = _run_filings(tmp_path)
    text = _text(at)
    assert _WONIK_IPS_TITLE not in text and "Treasury Stock Disposal" not in text
    assert _MATERIAL_TITLE in text
    assert "Capital Raise or Dividend Decision" not in text


def test_treasury_only_class_stays_excluded_even_behind_the_administrative_toggle(tmp_path):
    # The treasury-only NOT_MATERIAL class is excluded at snapshot time by
    # the existing _is_low_value_suppressed gate (an earlier, tested
    # decision); the new toggle only re-includes OTHER NOT_MATERIAL
    # candidates. Either way the Wonik IPS disposal is never drafted into
    # the list and never labelled a capital raise.
    _seed(tmp_path)
    at = _run_filings(tmp_path, include_admin=True)
    text = _text(at)
    assert _WONIK_IPS_TITLE not in text
    assert "Capital Raise or Dividend Decision" not in text


def test_no_escalation_controls_exist_on_the_filings_page(tmp_path):
    _seed(tmp_path)
    at = _run_filings(tmp_path)
    labels = {b.label for b in at.button} | {pl.label for pl in at.get("page_link")}
    assert not any(label.startswith(("Add to thesis", "Create signal", "Link to thesis")) for label in labels)


def test_dashboard_never_surfaces_the_treasury_disposal(tmp_path, monkeypatch):
    _seed(tmp_path)
    settings = _settings(tmp_path)
    # The Federal Register pilot is a live read-time fetch; not under test here.
    monkeypatch.setattr("src.ui.pages.dashboard.render_policy_developments", lambda: None)
    with patch("src.ui.pages.dashboard.get_settings", return_value=settings), \
         patch("src.ui.ui._nav_badge_counts", return_value={}), \
         patch("src.ui.ui._latest_filings_refresh_label", return_value=None):
        at = AppTest.from_file(str(_DASHBOARD_HARNESS), default_timeout=20)
        at.run()
    assert not at.exception, at.exception
    all_text = " ".join(m.value for m in at.main.get("markdown"))
    assert _WONIK_IPS_TITLE not in all_text
    assert "Capital Raise or Dividend Decision" not in all_text
    assert _MATERIAL_TITLE in all_text  # the material Wonik IPS filing does surface in the Regional Brief
