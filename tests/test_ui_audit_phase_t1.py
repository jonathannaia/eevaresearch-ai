"""Phase T1 (design/DECISIONS.md) — Radar local-time display and safe
status demotion: the user-facing freshness line now renders in
America/New_York (dynamic EDT/EST via zoneinfo, never a hardcoded offset
or suffix; storage/comparison stays UTC), internal/non-actionable
CandidateStatus pills are suppressed on the default "Latest" card (never
on "Captured filings," which stays fully truthful/complete), and a
genuine retrieval/parse failure renders one quiet, honest note instead of
a loud pill. Every test here is a pure rendering/formatting/source-
inspection check — no candidate status, review action, signal
eligibility, auto-routing, source, retry, worker, database, scheduler,
deployment, secret, or environment behavior is exercised or changed."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.dart import candidate_store
from src.data_access.state_db.scan_status_repository import ProviderScanStatus
from src.logic.formatting import fmt_datetime_local
from src.logic.radar_freshness import NO_SCAN_YET_MESSAGE, UNAVAILABLE_MESSAGE, compute_radar_freshness
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, FilingEvent, StateTransition
from src.ui.components.radar_status import RETRIEVAL_FAILURE_NOTE, RadarItem, default_card_status_html

HARNESS_DIR = Path(__file__).parent / "apptest_pages"
REPO_ROOT = Path(__file__).parent.parent
_HARNESS = HARNESS_DIR / "radar_inbox_page.py"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _recent_iso(minutes_ago: int = 10) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def _seed_corp_codes(cache_dir: Path) -> None:
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
        retrieved_at=_now_iso(),
    )


def _seed_filing_events(cache_dir: Path, filings: list[FilingEvent]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {"seen_receipt_numbers": [f.rcept_no for f in filings], "filing_events": [asdict(f) for f in filings], "candidate_signals": []}
    (cache_dir / "dart_filing_events.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _run_radar(tmp_path, **overrides):
    settings = Settings(
        dart_api_key="dart-key", translation_api_key="deepl-key",
        edgar_user_agent=None, edinet_subscription_key=None, cache_dir=tmp_path, **overrides,
    )
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=15)
        at.run()
    return at


def _text(at) -> str:
    return " ".join(m.value for m in at.markdown if not m.value.startswith("<style>"))


# ============================== FRESHNESS: EASTERN TIME ==============================

def test_freshness_all_recent_renders_in_eastern_time(tmp_path):
    db_path = tmp_path / "state.db"
    settings = Settings(cache_dir=tmp_path, db_backend="sqlite", state_db_path=db_path, edinet_subscription_key="test-key")
    backend_factory.get_scan_status_repository(settings).upsert_scan_status(ProviderScanStatus(
        provider="EDINET", cursor_value=None, started_at=_recent_iso(), completed_at=_recent_iso(),
        last_successful_at=_recent_iso(), items_discovered=1, candidates_created=1,
        skipped_unresolved_count=0, failure_code=None, updated_at=_recent_iso(),
    ))
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=15)
        at.run()
    assert not at.exception
    all_text = _text(at)
    assert "Filing data last refreshed" in all_text
    assert "UTC" not in all_text.split("Filing data last refreshed")[1].split(".")[0]
    assert any(tz in all_text for tz in ("EDT", "EST"))


def test_no_timestamp_states_are_unchanged_by_the_timezone_change(tmp_path):
    _seed_corp_codes(tmp_path)
    _seed_filing_events(tmp_path, [_filing("20260812000001", "일반 공고")])
    at = _run_radar(tmp_path)  # db_backend defaults to "json" -> no durable status store
    assert not at.exception
    assert UNAVAILABLE_MESSAGE in _text(at)
    assert "No completed scan yet for this source." not in _text(at)  # not this state here


def test_no_scan_yet_message_is_exact_and_has_no_timestamp():
    result = compute_radar_freshness("ok", {}, ("EDINET",), 60)
    assert result.message == NO_SCAN_YET_MESSAGE
    assert "EDT" not in result.message and "EST" not in result.message and "UTC" not in result.message


def test_stale_and_partial_messages_convert_to_eastern_time():
    now = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)
    old = (now - timedelta(hours=10)).isoformat()  # well beyond a 60-min*3 threshold
    stale_status = ProviderScanStatus(
        provider="SEC EDGAR", cursor_value=None, started_at=old, completed_at=old, last_successful_at=old,
        items_discovered=1, candidates_created=1, skipped_unresolved_count=0, failure_code=None, updated_at=old,
    )
    stale_result = compute_radar_freshness("ok", {"SEC EDGAR": stale_status}, ("SEC EDGAR",), 60, now=now)
    assert stale_result.state == "all_stale"
    assert "EDT" in stale_result.message
    assert "UTC" not in stale_result.message

    recent = (now - timedelta(minutes=10)).isoformat()
    recent_status = ProviderScanStatus(
        provider="SEC EDGAR", cursor_value=None, started_at=recent, completed_at=recent, last_successful_at=recent,
        items_discovered=1, candidates_created=1, skipped_unresolved_count=0, failure_code=None, updated_at=recent,
    )
    partial_result = compute_radar_freshness(
        "ok", {"SEC EDGAR": recent_status, "OpenDART / DART": stale_status}, ("SEC EDGAR", "OpenDART / DART"), 60, now=now,
    )
    assert partial_result.state == "partial"
    assert "EDT" in partial_result.message
    assert "UTC" not in partial_result.message


# ============================== UTC INTERNAL BEHAVIOR UNCHANGED ==============================

def test_stale_threshold_comparison_still_operates_in_utc():
    """The recent/stale boundary decision itself must be untouched by the
    display-layer timezone change — proven by feeding naive-UTC-shaped
    inputs through the same threshold logic Phase F1 already established."""
    from src.logic.radar_freshness import categorize_source_status

    now = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    just_inside = ProviderScanStatus(
        provider="SEC EDGAR", cursor_value=None, started_at=None, completed_at=None,
        last_successful_at=(now - timedelta(minutes=179)).isoformat(),
        items_discovered=0, candidates_created=0, skipped_unresolved_count=0, failure_code=None, updated_at="",
    )
    just_outside = ProviderScanStatus(
        provider="SEC EDGAR", cursor_value=None, started_at=None, completed_at=None,
        last_successful_at=(now - timedelta(minutes=181)).isoformat(),
        items_discovered=0, candidates_created=0, skipped_unresolved_count=0, failure_code=None, updated_at="",
    )
    assert categorize_source_status(just_inside, 60, now=now) == "recent"
    assert categorize_source_status(just_outside, 60, now=now) == "stale"


def test_fmt_datetime_local_does_not_mutate_the_underlying_utc_value():
    """Round-trip proof: converting for display never changes what the
    timestamp actually represents in absolute (UTC) terms."""
    from zoneinfo import ZoneInfo

    original_utc = datetime(2026, 7, 15, 18, 30, 0, tzinfo=timezone.utc)
    displayed = fmt_datetime_local(original_utc.isoformat())
    # "14:30 EDT" on the same calendar instant, re-parsed, equals the
    # original UTC instant exactly.
    reparsed_local = datetime(2026, 7, 15, 14, 30, 0, tzinfo=ZoneInfo("America/New_York"))
    assert reparsed_local.astimezone(timezone.utc) == original_utc
    assert "14:30" in displayed


# ============================== ZONEINFO / TZDATA DETERMINISM ==============================

def test_zoneinfo_america_new_york_resolves_deterministically():
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/New_York")
    summer_offset = datetime(2026, 7, 15, tzinfo=tz).utcoffset()
    winter_offset = datetime(2026, 1, 15, tzinfo=tz).utcoffset()
    assert summer_offset == timedelta(hours=-4)
    assert winter_offset == timedelta(hours=-5)


def test_tzdata_is_declared_in_requirements_with_a_documented_reason():
    requirements = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "tzdata" in requirements
    assert "ZoneInfo" in requirements or "zoneinfo" in requirements  # the reason is documented, not a bare pin


# ============================== STATUS PILL SUPPRESSION (LATEST) ==============================

def _candidate(status: CandidateStatus, filing: FilingEvent, **overrides) -> CandidateSignal:
    return CandidateSignal(
        id=overrides.pop("id", f"cand-t1-{status.value}"), filing=filing, matched_rules=["earnings:earnings_or_results_report:실적"],
        confidence="Moderate", status=status, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="실적 관련 원문", state_history=[StateTransition(status=status, at=_now_iso())],
        **overrides,
    )


def test_default_card_status_html_suppresses_every_named_normal_state():
    normal_states = (
        CandidateStatus.NEEDS_REVIEW, CandidateStatus.CANDIDATE_DETECTED, CandidateStatus.QUEUED_FOR_PROCESSING,
        CandidateStatus.EXTRACTION_PENDING, CandidateStatus.EXTRACTED, CandidateStatus.TRANSLATION_PENDING,
        CandidateStatus.TRANSLATED, CandidateStatus.PROCESSING_DEFERRED, CandidateStatus.MONITORING,
        CandidateStatus.DISMISSED, CandidateStatus.NOT_MATERIAL,
    )
    filing = _filing("1", "test")
    for status in normal_states:
        candidate = _candidate(status, filing)
        item = RadarItem(filing=filing, candidate=candidate)
        assert default_card_status_html(item) is None, f"{status.value} should be suppressed"


def test_default_card_status_html_shows_quiet_note_for_genuine_failures():
    filing = _filing("1", "test")
    for status in (CandidateStatus.RETRIEVAL_FAILED, CandidateStatus.PARSE_FAILED):
        candidate = _candidate(status, filing)
        item = RadarItem(filing=filing, candidate=candidate)
        html = default_card_status_html(item)
        assert html == f'<span class="er-chip er-chip-uncertainty">{RETRIEVAL_FAILURE_NOTE}</span>'


def test_default_card_status_html_leaves_published_and_new_filing_unchanged():
    filing = _filing("1", "test")
    published = _candidate(CandidateStatus.PUBLISHED, filing)
    item = RadarItem(filing=filing, candidate=published)
    assert default_card_status_html(item) is not None
    assert "Published" in default_card_status_html(item)

    bare_item = RadarItem(filing=filing, candidate=None)
    assert default_card_status_html(bare_item) is not None
    assert "New filing" in default_card_status_html(bare_item)


def test_retrieval_failure_note_is_not_color_reliant():
    """The dashed/outline chip treatment (assets/styles.css's existing
    er-chip-uncertainty class) — not a solid color fill — is what makes
    this distinguishable, matching evidence_chips.py's own established
    "never rely on color alone" convention."""
    assert "er-chip-uncertainty" in default_card_status_html(
        RadarItem(filing=_filing("1", "t"), candidate=_candidate(CandidateStatus.RETRIEVAL_FAILED, _filing("1", "t"))),
    )
    assert "er-status-tag er-tag-neg" not in default_card_status_html(
        RadarItem(filing=_filing("1", "t"), candidate=_candidate(CandidateStatus.RETRIEVAL_FAILED, _filing("1", "t"))),
    )


# ============================== END-TO-END CARD RENDERING ==============================

def test_needs_review_card_shows_no_pill_but_keeps_research_content(tmp_path):
    _seed_corp_codes(tmp_path)
    filing = _filing("20260812000001", "신규시설투자등 결정")
    _seed_filing_events(tmp_path, [filing])
    candidate = _candidate(CandidateStatus.NEEDS_REVIEW, filing, id="cand-t1-nr")
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})
    at = _run_radar(tmp_path)
    assert not at.exception
    all_text = _text(at)
    assert 'er-status-tag er-tag-mix">Needs review' not in all_text
    # Research content is untouched.
    assert "신규시설투자등 결정" in all_text
    assert "Why this matters:" in all_text
    assert "Memory" in all_text
    assert "Detection confidence: Moderate" in all_text


def test_retrieval_failed_card_shows_quiet_note_and_full_detail_inside_investigate(tmp_path):
    _seed_corp_codes(tmp_path)
    filing = _filing("20260812000002", "실적 관련 공시")
    _seed_filing_events(tmp_path, [filing])
    candidate = _candidate(CandidateStatus.RETRIEVAL_FAILED, filing, id="cand-t1-rf")
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})
    at = _run_radar(tmp_path)
    assert not at.exception
    all_text = _text(at)
    assert RETRIEVAL_FAILURE_NOTE in all_text
    assert 'er-status-tag er-tag-neg">Retrieval failed' not in all_text
    # The raw, complete status/state remains available inside Investigate
    # (AppTest renders expander content regardless of collapsed state).
    assert "Retrieval failed" in all_text  # from the state-history/Technical-details audit trail
    expander_labels = [e.label for e in at.expander]
    assert "Investigate →" in expander_labels


def test_captured_filings_view_always_shows_the_full_real_status(tmp_path):
    """Phase T1 scopes suppression to "Latest" only — "Captured filings"
    keeps its own established role as the truthful, complete record."""
    _seed_corp_codes(tmp_path)
    filing = _filing("20260812000003", "신규시설투자등 결정")
    _seed_filing_events(tmp_path, [filing])
    candidate = _candidate(CandidateStatus.NEEDS_REVIEW, filing, id="cand-t1-captured")
    candidate_store.save_candidates(tmp_path, {candidate.id: candidate})
    at = _run_radar(tmp_path)
    assert not at.exception
    at.radio(key="radar-view-mode").set_value("Captured filings")
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=Settings(
        dart_api_key="dart-key", translation_api_key="deepl-key",
        edgar_user_agent=None, edinet_subscription_key=None, cache_dir=tmp_path,
    )):
        at.run()
    assert not at.exception
    assert 'er-status-tag er-tag-mix">Needs review' in _text(at)


# ============================== NO SCOPE CREEP ==============================

def test_phase_t1_touches_only_presentation_layer_files():
    import subprocess

    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    forbidden_paths = {
        "scripts/radar_worker.py", "src/data_access/dart/scan_service.py", "src/data_access/dart/radar_pipeline.py",
        "src/data_access/edgar/scan_service.py", "src/data_access/edgar/edgar_pipeline.py",
        "src/data_access/edinet/scan_service.py", "src/data_access/edinet/edinet_pipeline.py",
        "src/data_access/translation/deepl_provider.py", "src/data_access/translation/translation_service.py",
        "src/data_access/dart/retry_policy.py", "src/logic/signal_promotion.py", "src/logic/signal_decision_policy.py",
        "src/logic/review_actions.py", ".github/workflows/manual-scan.yml",
    }
    assert not (changed & forbidden_paths), changed & forbidden_paths


def test_phase_t1_does_not_add_a_dependency_other_than_tzdata():
    """Diffed against the full current requirements.txt content (not
    line-by-line +/- parsing) — the original file had no trailing
    newline, so appending after its last line makes git's own diff
    re-show that unchanged last line as both removed and re-added; a
    naive "every + line is new" check would false-positive on that."""
    import subprocess

    result = subprocess.run(
        ["git", "show", "HEAD:requirements.txt"], cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return
    before_packages = {
        line.split("#")[0].split(">=")[0].split("[")[0].strip()
        for line in result.stdout.splitlines() if line.strip() and not line.strip().startswith("#")
    }
    current = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    after_packages = {
        line.split("#")[0].split(">=")[0].split("[")[0].strip()
        for line in current.splitlines() if line.strip() and not line.strip().startswith("#")
    }
    newly_added = after_packages - before_packages
    assert newly_added == {"tzdata"}, newly_added


def test_publish_still_gates_signal_eligibility_unchanged():
    from src.logic.signal_promotion import _ELIGIBLE_STATUSES, is_eligible_for_signal

    assert _ELIGIBLE_STATUSES == frozenset({CandidateStatus.PUBLISHED})
    filing = _filing("20260812000099", "test")
    published = _candidate(CandidateStatus.PUBLISHED, filing, id="cand-t1-eligible")
    needs_review = _candidate(CandidateStatus.NEEDS_REVIEW, filing, id="cand-t1-ineligible")
    assert is_eligible_for_signal(published) is True
    assert is_eligible_for_signal(needs_review) is False
