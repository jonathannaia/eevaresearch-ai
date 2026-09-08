"""Radar Inbox's public read-only contract (reader-facing data-integrity
pass, design/DECISIONS.md) — fixture-driven proofs of the new public
contract, replacing the removed-feature assertions that used to live in
test_radar_inbox_page.py / test_ui_audit_phase_r1.py / _c.py / _t1.py:

  - no public mutation controls (Publish/Monitor/Exclude) or note field;
  - no Scan Now, worker-status, or on-demand extraction/retry action;
  - the card's only action is the official original-source link;
  - evidence/provenance, jurisdiction, captured timestamp, and
    materiality render directly, with no expander anywhere on the page.

Zero network calls, no real API key, and the real data/cache/ (gitignored
live pilot cache) is never touched — same discipline as
test_radar_inbox_page.py."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.data_access.dart import candidate_store
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, FilingEvent, StateTransition

_HARNESS = Path(__file__).parent / "apptest_pages" / "radar_inbox_page.py"


@pytest.fixture(autouse=True)
def _guard_against_live_calls(monkeypatch):
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _filing(rcept_no: str, report_nm: str) -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code="00126380", corp_name="삼성전자", stock_code="005930",
        report_nm=report_nm, rcept_dt="20260812", flr_nm="삼성전자", theme_slug="memory",
        source_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
        retrieved_at=_now_iso(),
    )


def _seed_filing_events(cache_dir, filings: list[FilingEvent]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    from dataclasses import asdict
    payload = {"seen_receipt_numbers": [f.rcept_no for f in filings], "filing_events": [asdict(f) for f in filings], "candidate_signals": []}
    (cache_dir / "dart_filing_events.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _needs_review_candidate(candidate_id: str, filing: FilingEvent, **overrides) -> CandidateSignal:
    defaults = dict(
        id=candidate_id, filing=filing, matched_rules=["earnings:earnings_or_results_report:실적"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="본문 발췌.",
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    defaults.update(overrides)
    return CandidateSignal(**defaults)


def _run_radar(tmp_path) -> AppTest:
    settings = Settings(dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=15)
        at.run()
    return at


def _seed_one_candidate(tmp_path, **overrides) -> CandidateSignal:
    _seed_corp_codes(tmp_path)
    filing = _filing("20260812000001", "실적 발표")
    _seed_filing_events(tmp_path, [filing])
    candidate = _needs_review_candidate("cand-public-1", filing, **overrides)
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})
    return candidate


def _text(at: AppTest) -> str:
    return " ".join(m.value for m in at.markdown if not m.value.startswith("<style>"))


# ============================================================
# No public mutation controls or note field
# ============================================================


def test_radar_has_no_publish_monitor_exclude_buttons(tmp_path):
    _seed_one_candidate(tmp_path)
    at = _run_radar(tmp_path)
    assert not at.exception
    button_labels = {b.label for b in at.button}
    for forbidden in ("Publish", "Monitor", "Exclude", "Confirm exclude", "Cancel"):
        assert forbidden not in button_labels


def test_radar_has_no_review_note_field(tmp_path):
    candidate = _seed_one_candidate(tmp_path)
    at = _run_radar(tmp_path)
    assert not at.exception
    text_input_keys = {ti.key for ti in at.text_input}
    assert f"radar-review-note-{candidate.id}" not in text_input_keys


def test_review_actions_backend_seam_is_never_imported_or_called_from_radar_inbox():
    """Structural (AST-based) proof, not just UI absence: review_actions.
    record_review_decision — the write path Publish/Monitor/Exclude used
    to call — is never imported or called anywhere in radar_inbox.py or
    radar_card.py any more. A module docstring may legitimately mention
    the module by name in prose explaining the removal; only a real
    import or call statement is checked here."""
    import ast

    for rel_path in ("src/ui/pages/radar_inbox.py", "src/ui/components/radar_card.py"):
        path = Path(__file__).parent.parent / rel_path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported_modules = {
            alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
        } | {
            node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
        }
        assert not any("review_actions" in m for m in imported_modules)
        called_names = {
            node.func.attr for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert "record_review_decision" not in called_names


# ============================================================
# No Scan Now, worker-status, or on-demand extraction/retry action
# ============================================================


def test_radar_has_no_scan_now_buttons(tmp_path):
    _seed_one_candidate(tmp_path)
    at = _run_radar(tmp_path)
    assert not at.exception
    button_labels = {b.label for b in at.button}
    for forbidden in ("Scan DART now", "Scan EDGAR now", "Scan EDINET now"):
        assert forbidden not in button_labels


def test_radar_has_no_worker_status_or_ingestion_status_disclosure(tmp_path):
    _seed_one_candidate(tmp_path)
    at = _run_radar(tmp_path)
    assert not at.exception
    expander_labels = {e.label for e in at.expander}
    assert "Ingestion status" not in expander_labels
    all_text = _text(at)
    assert "Continuous worker status" not in all_text
    assert "Source scans can take time and are intended for local/admin use." not in all_text


def test_radar_has_no_extraction_or_retry_action(tmp_path):
    deferred_filing = _filing("20260812000002", "신규시설투자 결정")
    _seed_corp_codes(tmp_path)
    _seed_filing_events(tmp_path, [deferred_filing])
    deferred = CandidateSignal(
        id="cand-deferred-1", filing=deferred_filing, matched_rules=["x:y:z"], confidence="Moderate",
        status=CandidateStatus.PROCESSING_DEFERRED,
        state_history=[StateTransition(status=CandidateStatus.PROCESSING_DEFERRED, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {deferred.id: deferred})
    at = _run_radar(tmp_path)
    assert not at.exception
    button_labels = {b.label for b in at.button}
    for forbidden in ("Prepare analyst view", "Retry analyst view preparation"):
        assert forbidden not in button_labels
    assert not any("Retry limit reached" in label for label in button_labels)
    assert not any("Retry available in" in label for label in button_labels)


def test_run_scan_and_process_candidate_now_are_never_called_from_radar_inbox():
    """Structural proof: the write-path service calls the removed
    buttons used to make no longer appear anywhere in radar_inbox.py's
    own source."""
    source = (Path(__file__).parent.parent / "src" / "ui" / "pages" / "radar_inbox.py").read_text(encoding="utf-8")
    assert ".run_scan(" not in source
    assert ".process_candidate_now(" not in source


# ============================================================
# The card's only action is the official original-source link
# ============================================================


def test_radar_cards_only_action_is_the_original_source_link(tmp_path):
    candidate = _seed_one_candidate(tmp_path)
    at = _run_radar(tmp_path)
    assert not at.exception
    # No button anywhere on the page carries this candidate's id in its
    # key — every former per-candidate action (Publish/Monitor/Exclude/
    # Prepare/Retry) is gone, leaving only the link_button below.
    assert not any(candidate.id in (b.key or "") for b in at.button)
    # Radar simplicity workstream: the label is the exact fixed string
    # the approved spec calls for — no source name interpolated into it.
    link_buttons = [b for b in at.get("link_button") if getattr(b, "label", "") == "Open original filing ↗"]
    assert len(link_buttons) >= 1


# ============================================================
# Evidence/provenance, jurisdiction, captured timestamp, and materiality
# render directly — no expander anywhere on the page
# ============================================================


def test_radar_page_has_no_card_level_expanders(tmp_path):
    """Radar layout correction (design/DECISIONS.md): "Advanced filters"
    — the one previously-permitted expander — is now removed entirely, so
    no expander of any kind may appear anywhere on the page."""
    _seed_one_candidate(tmp_path, materiality_assessment="Material · new facility investment")
    at = _run_radar(tmp_path)
    assert not at.exception
    assert {e.label for e in at.expander} == set()


def test_radar_card_shows_only_the_approved_public_fields(tmp_path):
    # Radar simplicity workstream: Evidence status, jurisdiction,
    # captured timestamp, and materiality are all removed from the
    # public card — replaced by this direct test of the approved contract
    # (company+ticker, filed date, English title, Original, a display-only
    # translation toggle, Open original filing link) that superseded them.
    candidate = _seed_one_candidate(tmp_path, materiality_assessment="Material · new facility investment")
    at = _run_radar(tmp_path)
    assert not at.exception
    all_text = _text(at)
    assert "Evidence status" not in all_text
    assert candidate.filing.retrieved_at not in all_text
    assert "South Korea" not in all_text
    assert "Material · new facility investment" not in all_text
    assert candidate.filing.corp_name in all_text
    assert candidate.filing.stock_code in all_text
    # Filing-quality pass: no translation stored, so Summary is the
    # neutral metadata fallback — the native excerpt is reachable only
    # behind its own quality-gated "View original filing text" toggle.
    assert candidate.excerpt_original not in all_text
    assert any(b.label == "View original filing text" for b in at.button)
    assert "Filed Aug 12, 2026" in all_text  # candidate.filing.rcept_dt == "20260812"
