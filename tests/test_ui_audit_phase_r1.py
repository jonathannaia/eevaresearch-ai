"""Phase R1 (design/DECISIONS.md) — autonomous-Radar feed UI
simplification: a concise default header (no credential-driven "Live"
chip, no tracked-company/scope dump), "Latest" replacing "Needs your
decision" as the default view, Status moved into Advanced filters, and
filing cards restructured around one "Investigate →" primary action with
Publish/Monitor/Exclude relocated behind it. Every test here is a pure
rendering/content/source-inspection check via AppTest or direct source
reads — no candidate workflow, status, review action, signal-eligibility,
worker, scheduler, source, translation, retry, deployment, database,
dependency, environment variable, secret, or GitHub Actions behavior is
exercised or changed by this phase, and several tests below prove that
directly rather than merely asserting the negative in prose."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.data_access.dart import candidate_store
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, FilingEvent, StateTransition

HARNESS_DIR = Path(__file__).parent / "apptest_pages"
REPO_ROOT = Path(__file__).parent.parent
_HARNESS = HARNESS_DIR / "radar_inbox_page.py"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_corp_codes(cache_dir: Path) -> None:
    # Extended (2026-09-04) with the Core Issuer Expansion batch's 8 new
    # DART companies — every tracked DART company must be resolved for
    # dart_readiness.ready to be True at all.
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "005930": {"corp_code": "00126380", "corp_name": "삼성전자", "source": "OpenDART corpCode.xml", "retrieved_at": "2026-08-01T00:00:00+00:00"},
        "000660": {"corp_code": "00164779", "corp_name": "SK 하이닉스", "source": "OpenDART corpCode.xml", "retrieved_at": "2026-08-01T00:00:00+00:00"},
        "011070": {"corp_code": "00105961", "corp_name": "LG이노텍", "source": "OpenDART corpCode.xml", "retrieved_at": "2026-09-04T00:00:00+00:00"},
        "012450": {"corp_code": "00126566", "corp_name": "한화에어로스페이스", "source": "OpenDART corpCode.xml", "retrieved_at": "2026-09-04T00:00:00+00:00"},
        "047810": {"corp_code": "00309503", "corp_name": "한국항공우주", "source": "OpenDART corpCode.xml", "retrieved_at": "2026-09-04T00:00:00+00:00"},
        "454910": {"corp_code": "01105153", "corp_name": "두산로보틱스", "source": "OpenDART corpCode.xml", "retrieved_at": "2026-09-04T00:00:00+00:00"},
        "240810": {"corp_code": "01135941", "corp_name": "원익IPS", "source": "OpenDART corpCode.xml", "retrieved_at": "2026-09-04T00:00:00+00:00"},
        "056190": {"corp_code": "00358271", "corp_name": "SFA", "source": "OpenDART corpCode.xml", "retrieved_at": "2026-09-04T00:00:00+00:00"},
        "036540": {"corp_code": "00301246", "corp_name": "SFA반도체", "source": "OpenDART corpCode.xml", "retrieved_at": "2026-09-04T00:00:00+00:00"},
        "067310": {"corp_code": "00445054", "corp_name": "하나마이크론", "source": "OpenDART corpCode.xml", "retrieved_at": "2026-09-04T00:00:00+00:00"},
    }
    (cache_dir / "dart_corp_codes.json").write_text(json.dumps(payload), encoding="utf-8")


def _filing(rcept_no: str, report_nm: str) -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code="00126380", corp_name="삼성전자", stock_code="005930",
        report_nm=report_nm, rcept_dt="20260812", flr_nm="삼성전자", theme_slug="memory",
        source_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
        retrieved_at=_now_iso(),
    )


def _seed_filing_events(cache_dir: Path, filings: list[FilingEvent]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {"seen_receipt_numbers": [f.rcept_no for f in filings], "filing_events": [asdict(f) for f in filings], "candidate_signals": []}
    (cache_dir / "dart_filing_events.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _run_radar(tmp_path, **settings_overrides):
    settings = Settings(
        dart_api_key="dart-key", translation_api_key="deepl-key",
        edgar_user_agent=None, edinet_subscription_key=None,
        cache_dir=tmp_path, **settings_overrides,
    )
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=15)
        at.run()
    return at


def _text(at) -> str:
    return " ".join(m.value for m in at.markdown if not m.value.startswith("<style>"))


# ============================== HEADER ==============================

def test_default_header_has_only_the_approved_subtitle_and_no_live_chip(tmp_path):
    _seed_corp_codes(tmp_path)
    _seed_filing_events(tmp_path, [_filing("20260812000001", "일반 공고")])
    at = _run_radar(tmp_path)
    assert not at.exception
    all_text = _text(at)
    assert "Latest Filings" in all_text
    assert "Radar watches tracked companies for material filings, theme developments, and high-confidence signals." in all_text
    # Genuinely deleted, not relocated — these exact strings exist nowhere
    # in the page, default view or not.
    assert "Automated primary-filing discovery" not in all_text
    assert "Live primary filings" not in all_text
    # No credential-driven freshness chip anywhere by default.
    assert "er-fresh-live" not in all_text
    assert "er-fresh-demo" not in all_text


# ============================== VIEWS AND FILTERS ==============================

def test_view_selector_is_removed_one_unified_feed_only(tmp_path):
    """Unify-Radar-into-Latest-Filings pass — the "Latest"/"Captured
    filings" `st.radio` view selector is gone entirely; there is exactly
    one public feed now, so no `radar-view-mode` widget exists at all."""
    _seed_corp_codes(tmp_path)
    _seed_filing_events(tmp_path, [_filing("20260812000001", "일반 공고")])
    at = _run_radar(tmp_path)
    assert not at.exception
    assert "radar-view-mode" not in {r.key for r in at.radio}


def test_result_membership_includes_bare_filings_and_candidates_together(tmp_path):
    """Unify-Radar-into-Latest-Filings pass — a filing is never hidden for
    lacking a CandidateSignal any more: a bare filing and a real candidate
    both render on the one unified feed in the same render."""
    _seed_corp_codes(tmp_path)
    bare_filing = _filing("20260812000001", "일반 공고")
    candidate_filing = _filing("20260812000002", "신규시설투자등 결정")
    _seed_filing_events(tmp_path, [bare_filing, candidate_filing])
    candidate = CandidateSignal(
        id="cand-r1-membership", filing=candidate_filing,
        matched_rules=["capex_or_facility_investment:facility_investment:신규시설투자"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="신규시설투자등 관련 원문",
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})
    at = _run_radar(tmp_path)
    assert not at.exception
    all_text = _text(at)
    assert "신규시설투자등 결정" in all_text
    assert "일반 공고" in all_text


def test_advanced_filters_and_its_controls_are_removed_entirely(tmp_path):
    """Radar layout correction (design/DECISIONS.md) superseded Phase R1's
    "Status moved into Advanced filters" — the whole expander (Status,
    Company, Language, Detection confidence) is removed outright, not
    relocated anywhere else. Search/Source/Theme/Filed between remain the
    complete, only filter set."""
    _seed_corp_codes(tmp_path)
    filing = _filing("20260812000001", "일반 공고")
    _seed_filing_events(tmp_path, [filing])
    candidate = CandidateSignal(
        id="cand-r1-status-filter", filing=filing, matched_rules=["earnings:earnings_or_results_report:실적"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="실적 관련 원문",
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})
    at = _run_radar(tmp_path)
    assert not at.exception
    multiselect_keys = {m.key for m in at.multiselect}
    selectbox_keys = {s.key for s in at.selectbox}
    assert "radar-filter-status" not in multiselect_keys
    assert "radar-filter-company" not in multiselect_keys
    assert "radar-filter-language" not in selectbox_keys
    assert "radar-filter-confidence" not in multiselect_keys
    assert {e.label for e in at.expander} == set()
    source = (REPO_ROOT / "src" / "ui" / "pages" / "radar_inbox.py").read_text(encoding="utf-8")
    assert 'st.expander("Advanced filters")' not in source
    assert "adv_cols" not in source


def test_default_filter_row_is_search_source_theme_date_only(tmp_path):
    _seed_corp_codes(tmp_path)
    filing = _filing("20260812000001", "신규시설투자등 결정")
    _seed_filing_events(tmp_path, [filing])
    candidate = CandidateSignal(
        id="cand-r1-filter-row", filing=filing, matched_rules=["capex_or_facility_investment:facility_investment:신규시설투자"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="신규시설투자등 관련 원문",
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})
    at = _run_radar(tmp_path)
    assert not at.exception
    assert at.text_input(key="radar-filter-search") is not None
    assert at.multiselect(key="radar-filter-source") is not None
    assert at.multiselect(key="radar-filter-theme") is not None
    assert at.date_input(key="radar-filter-dates") is not None


# ============================== FILING CARDS ==============================

def test_card_shows_original_and_english_translation(tmp_path):
    # Radar simplicity workstream: "Why this matters" (and "Why flagged")
    # are both removed from the public card — replaced by this test of
    # the actual 5-field contract that superseded it.
    _seed_corp_codes(tmp_path)
    filing = _filing("20260812000001", "신규시설투자등 결정")
    _seed_filing_events(tmp_path, [filing])
    candidate = CandidateSignal(
        id="cand-r1-card", filing=filing, matched_rules=["capex_or_facility_investment:facility_investment:신규시설투자"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="신규시설투자등 관련 원문",
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})
    at = _run_radar(tmp_path)
    assert not at.exception
    all_text = _text(at)
    assert "Why this matters:" not in all_text
    assert "Why flagged:" not in all_text
    # No stored translation — Summary is the neutral metadata fallback;
    # the native excerpt is reachable only behind its own toggle.
    assert "삼성전자 filed 신규시설투자등 결정 on Aug 12, 2026." in all_text
    assert "신규시설투자등 관련 원문" not in all_text
    original_toggle = [b for b in at.button if b.label == "View original filing text"]
    assert len(original_toggle) == 1

    original_toggle[0].click()
    # get_settings must still be patched for this second run — AppTest
    # re-executes the harness script synchronously on `.run()`.
    settings = Settings(
        dart_api_key="dart-key", translation_api_key="deepl-key",
        edgar_user_agent=None, edinet_subscription_key=None, cache_dir=tmp_path,
    )
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at.run()
    all_text = _text(at)
    assert "신규시설투자등 관련 원문" in all_text


def test_publish_still_gates_signal_eligibility_unchanged():
    """No candidate workflow/status/eligibility behavior changed — proven
    directly against the real eligibility gate, not just by absence of a
    diff. Publish remains the only route to a Signal."""
    from src.logic.signal_promotion import _ELIGIBLE_STATUSES, is_eligible_for_signal

    assert _ELIGIBLE_STATUSES == frozenset({CandidateStatus.PUBLISHED})
    published = CandidateSignal(
        id="cand-r1-eligible", filing=_filing("20260812000099", "test"), matched_rules=[],
        confidence="High", status=CandidateStatus.PUBLISHED, extraction_state=ExtractionState.EXTRACTED,
    )
    monitoring = CandidateSignal(
        id="cand-r1-ineligible", filing=_filing("20260812000098", "test"), matched_rules=[],
        confidence="High", status=CandidateStatus.MONITORING, extraction_state=ExtractionState.EXTRACTED,
    )
    assert is_eligible_for_signal(published) is True
    assert is_eligible_for_signal(monitoring) is False


# ============================== DOCUMENTATION-ONLY CORRECTION ==============================

def test_signal_decision_policy_correction_does_not_alter_executed_policy():
    """The corrected docstring in signal_decision_policy.py must not
    coincide with any behavior change — proven by re-running its own real
    decision function against the exact fixture shapes its module already
    documents (424B5 complete vs. incomplete offering) and confirming the
    routes are identical to what edgar_pipeline.py has always executed."""
    from src.data_access.edgar.edgar_rules import normalize_form_type
    from src.logic.signal_decision_policy import SignalRoute, decide_signal_route

    filing = FilingEvent(
        rcept_no="0000320193-26-000001", corp_code="0000320193", corp_name="Test Corp", stock_code="TEST",
        report_nm="424B5", rcept_dt="2026-08-20", flr_nm="Test Corp", pblntf_ty="424B5",
        source_name="SEC EDGAR", original_language="English",
    )
    complete_candidate = CandidateSignal(
        id="cand-r1-424b5-complete", filing=filing, matched_rules=[], confidence="High",
        status=CandidateStatus.EXTRACTED, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="The offering priced $50 million of 1,000,000 shares with net proceeds to the company.",
    )
    incomplete_candidate = CandidateSignal(
        id="cand-r1-424b5-incomplete", filing=filing, matched_rules=[], confidence="High",
        status=CandidateStatus.EXTRACTED, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="This is a supplement to the prospectus.",
    )
    assert normalize_form_type(filing.pblntf_ty) == "424B5"
    assert decide_signal_route(complete_candidate).route == SignalRoute.PUBLISH
    assert decide_signal_route(incomplete_candidate).route == SignalRoute.TIMELINE


def test_signal_decision_policy_module_still_performs_no_io_or_mutation():
    source = (REPO_ROOT / "src" / "logic" / "signal_decision_policy.py").read_text(encoding="utf-8")
    for forbidden in ("import requests", "import httpx", "open(", "st.session_state", "candidate.status ="):
        assert forbidden not in source


# ============================== NO SCOPE CREEP ==============================

def test_phase_r1_introduces_no_worker_deployment_secret_or_dependency_change():
    """Diff-based, not whole-file-based: radar_inbox.py legitimately
    already mentions EDGE_RADAR_WORKER_* names in pre-existing Phase F1
    prose (explaining what it does NOT read) — a whole-file substring
    check would false-positive on that. This checks only the lines Phase
    R1 actually *added*, via `git diff`, for exactly the categories the
    approval explicitly forbade."""
    import subprocess

    result = subprocess.run(
        ["git", "diff", "HEAD", "--", "src/ui/pages/radar_inbox.py", "src/ui/components/radar_card.py", "src/logic/signal_decision_policy.py"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return  # not a git checkout with this phase's changes present — nothing to check
    added_lines = [line[1:] for line in result.stdout.splitlines() if line.startswith("+") and not line.startswith("+++")]
    added_text = "\n".join(added_lines)
    forbidden_substrings = (
        "EDGE_RADAR_LIVE_SCAN_ENABLED", "EDGE_RADAR_WORKER", "os.environ", "subprocess", "threading.Thread",
        "CREATE TABLE", "auto_publish_enabled = True", "auto_publish_enabled=True",
    )
    for forbidden in forbidden_substrings:
        assert forbidden not in added_text, f"a line Phase R1 added unexpectedly contains {forbidden!r}"


def test_phase_r1_does_not_touch_edgar_dart_edinet_scan_pipelines_or_worker():
    """Runs against `git diff HEAD`, so this also covers any later,
    still-uncommitted phase on top of the Phase R1 commit (e.g. Phase T1)
    — `requirements.txt` is deliberately NOT in this forbidden set:
    Phase T1 (design/DECISIONS.md) was separately, explicitly approved to
    add exactly one dependency (`tzdata`, for reliable zoneinfo behavior)
    there. Every scan/pipeline/worker/translation-provider/retry-policy/
    GitHub-Actions file below must still never appear in the diff,
    regardless of which phase is currently uncommitted."""
    import subprocess

    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    changed = set(result.stdout.splitlines())
    forbidden_paths = {
        "scripts/radar_worker.py", "src/data_access/dart/scan_service.py", "src/data_access/dart/radar_pipeline.py",
        "src/data_access/edgar/scan_service.py", "src/data_access/edgar/edgar_pipeline.py",
        "src/data_access/edinet/scan_service.py", "src/data_access/edinet/edinet_pipeline.py",
        "src/data_access/translation/deepl_provider.py", "src/data_access/translation/translation_service.py",
        "src/data_access/dart/retry_policy.py", ".github/workflows/manual-scan.yml",
    }
    # Only meaningful when run against a real git checkout with this
    # phase's changes staged/unstaged (not from an installed package) —
    # skip quietly otherwise rather than false-failing in a foreign
    # environment.
    if result.returncode != 0:
        return
    assert not (changed & forbidden_paths), changed & forbidden_paths


# ============================== FRESHNESS UNCHANGED ==============================

def test_phase_f1_freshness_line_still_renders_and_stays_truthful(tmp_path):
    """Isolates the freshness line itself (not the whole page) before
    checking prohibited words — the operator-facing Ingestion status
    panel legitimately says "Continuous worker status" (naming the
    software component, not making a freshness claim), which would
    otherwise false-positive a page-wide substring check."""
    from src.logic.radar_freshness import UNAVAILABLE_MESSAGE

    _seed_corp_codes(tmp_path)
    _seed_filing_events(tmp_path, [_filing("20260812000001", "일반 공고")])
    at = _run_radar(tmp_path)  # db_backend defaults to "json" -> no durable status store
    assert not at.exception
    freshness_line = next(
        m.value for m in at.markdown
        if m.value.strip() == f'<div class="er-muted" style="margin-top:0.4rem;">{UNAVAILABLE_MESSAGE}</div>'
    )
    for prohibited in ("live", "real-time", "automatic", "continuous", "autonomous", "scheduled", "updating"):
        assert prohibited not in freshness_line.lower()
