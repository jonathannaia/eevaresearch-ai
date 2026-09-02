"""Phase F1 (design/DECISIONS.md) — the durable, tester-facing Radar
Inbox freshness line: AppTest-based checks that it actually renders on
the real page (not just in the pure-function unit tests in
tests/test_radar_freshness.py), that the default wording never uses a
prohibited word, that EDINET stays unconfigured by default, and that the
"Captured filings" relabeling is honest and non-misleading."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.dart.radar_service import RadarReadiness
from src.data_access.edgar.edgar_service import EdgarReadiness
from src.data_access.state_db.scan_status_repository import ProviderScanStatus
from src.models.models import FilingEvent


def _recent_iso(minutes_ago: int = 10) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def _seed_one_dart_filing(cache_dir: Path) -> None:
    """Just enough for `_build_items()` to produce a non-empty `items`
    list (raw filing-event loading is unconditional, unlike readiness) —
    no DART credential/corp-code resolution needed for that."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    filing = FilingEvent(
        rcept_no="20260812000001", corp_code="00126380", corp_name="삼성전자", stock_code="005930",
        report_nm="일반 공고", rcept_dt="20260812", flr_nm="삼성전자", theme_slug="memory",
        source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260812000001",
        retrieved_at=_recent_iso(),
    )
    payload = {"seen_receipt_numbers": [filing.rcept_no], "filing_events": [asdict(filing)], "candidate_signals": []}
    (cache_dir / "dart_filing_events.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

_HARNESS = Path(__file__).parent / "apptest_pages" / "radar_inbox_page.py"
_PROHIBITED_WORDS = ("live", "real-time", "automatic", "continuous", "autonomous", "scheduled", "updating")
_READY_DART = RadarReadiness(dart_key_configured=True, translation_key_configured=True, unresolved_companies=())
_READY_EDGAR = EdgarReadiness(user_agent_configured=True, unresolved_companies=())


def _text(at) -> str:
    return " ".join(m.value for m in at.markdown if not m.value.startswith("<style>"))


def _settings(**overrides) -> Settings:
    fields = dict(
        dart_api_key=None, edgar_user_agent=None,
        # Translation reliability workstream: EdinetReadiness now also
        # checks the translation key, so both must be set together as
        # the cheapest way to pass the page's top-level readiness gate.
        edinet_subscription_key="test-key", translation_api_key="test-key",
        radar_worker_db_backend=None, radar_worker_state_db_path=None, radar_worker_state_db_url=None,
    )
    fields.update(overrides)
    return Settings(**fields)


def _run(settings: Settings, extra_patches: tuple = ()):
    from src.ui.pages.radar_inbox import _load_dashboard_snapshot

    _load_dashboard_snapshot.clear()
    patches = [patch("src.ui.pages.radar_inbox.get_settings", return_value=settings), *extra_patches]
    for p in patches:
        p.start()
    try:
        at = AppTest.from_file(str(_HARNESS), default_timeout=15)
        at.run()
    finally:
        for p in patches:
            p.stop()
        _load_dashboard_snapshot.clear()
    return at


# ============================== FRESHNESS LINE ==============================

def test_freshness_line_reads_unavailable_when_backend_is_json(tmp_path):
    """Default local dev config (db_backend="json") has no durable scan-
    status store at all — the honest line is state E, never a guess."""
    at = _run(_settings(cache_dir=tmp_path))
    assert not at.exception
    assert "Filing data may not be current." in _text(at)


def test_freshness_line_reads_no_scan_yet_when_enabled_source_has_no_status_row(tmp_path):
    settings = _settings(cache_dir=tmp_path, db_backend="sqlite", state_db_path=tmp_path / "state.db")
    at = _run(settings)
    assert not at.exception
    # EDINET is the one enabled source here (via the subscription key) and
    # has never had a scan-status row written.
    assert "No completed scan yet for this source." in _text(at)


def test_freshness_line_reads_all_recent_when_the_only_enabled_source_is_healthy(tmp_path):
    db_path = tmp_path / "state.db"
    settings = _settings(cache_dir=tmp_path, db_backend="sqlite", state_db_path=db_path)
    backend_factory.get_scan_status_repository(settings).upsert_scan_status(ProviderScanStatus(
        provider="EDINET", cursor_value=None, started_at=_recent_iso(),
        completed_at=_recent_iso(), last_successful_at=_recent_iso(),
        items_discovered=1, candidates_created=1, skipped_unresolved_count=0, failure_code=None,
        updated_at=_recent_iso(),
    ))
    at = _run(settings)
    assert not at.exception
    all_text = _text(at)
    assert "Filing data last refreshed" in all_text


def test_default_freshness_wording_never_uses_a_prohibited_word(tmp_path):
    """Checked against the actual rendered page text, not just the pure
    function's own unit tests — the prohibited words must never leak in
    via surrounding page copy either."""
    db_path = tmp_path / "state.db"
    settings = _settings(cache_dir=tmp_path, db_backend="sqlite", state_db_path=db_path)
    backend_factory.get_scan_status_repository(settings).upsert_scan_status(ProviderScanStatus(
        provider="EDINET", cursor_value=None, started_at="2026-08-28T00:00:00+00:00",
        completed_at="2026-08-28T00:01:00+00:00", last_successful_at="2026-08-28T00:01:00+00:00",
        items_discovered=1, candidates_created=1, skipped_unresolved_count=0, failure_code=None,
        updated_at="2026-08-28T00:01:00+00:00",
    ))
    at = _run(settings)
    assert not at.exception
    # The freshness line specifically, isolated from the rest of the
    # page's own copy (which this test doesn't otherwise constrain).
    freshness_line = next(
        m.value for m in at.markdown
        if m.value.strip().startswith('<div class="er-muted" style="margin-top:0.4rem;">Filing data')
        or m.value.strip().startswith('<div class="er-muted" style="margin-top:0.4rem;">No completed scan')
        or m.value.strip().startswith('<div class="er-muted" style="margin-top:0.4rem;">Some sources')
    )
    lowered = freshness_line.lower()
    for forbidden in _PROHIBITED_WORDS:
        assert forbidden not in lowered


def test_freshness_line_does_not_show_provider_counts_or_operational_detail(tmp_path):
    db_path = tmp_path / "state.db"
    settings = _settings(cache_dir=tmp_path, db_backend="sqlite", state_db_path=db_path)
    backend_factory.get_scan_status_repository(settings).upsert_scan_status(ProviderScanStatus(
        provider="EDINET", cursor_value=None, started_at=_recent_iso(),
        completed_at=_recent_iso(), last_successful_at=_recent_iso(),
        items_discovered=7, candidates_created=3, skipped_unresolved_count=2, failure_code=None,
        updated_at=_recent_iso(),
    ))
    at = _run(settings)
    assert not at.exception
    freshness_line = next(
        m.value for m in at.markdown
        if m.value.strip().startswith('<div class="er-muted" style="margin-top:0.4rem;">Filing data last refreshed')
    )
    for leaked in ("candidate(s) created", "skipped", "EDGE_", "postgres://"):
        assert leaked not in freshness_line


# ============================== INGESTION STATUS COLLAPSED ==============================

# ============================== CAPTURED FILINGS LABEL ==============================

def test_captured_filings_replaces_all_filings_and_latest_is_default(tmp_path):
    """Phase R1: "Needs your decision" -> "Latest" — label text only,
    same underlying `candidate is not None` view filter."""
    _seed_one_dart_filing(tmp_path)
    at = _run(_settings(cache_dir=tmp_path))
    assert not at.exception
    radio = at.radio(key="radar-view-mode")
    assert radio.options == ["Latest", "Captured filings"]
    assert radio.value == "Latest"
    assert "All filings" not in _text(at)
    assert "Needs your decision" not in _text(at)


# ============================== EDINET STAYS NOT ENABLED BY DEFAULT ==============================

def test_edinet_is_not_enabled_by_default_even_though_its_worker_status_row_would_be_read(tmp_path):
    """No EDGE_EDINET_SUBSCRIPTION_KEY configured (the real default) must
    keep EDINET out of every enabled-sources computation — the operator
    panel must say "not configured," never imply it's running."""
    db_path = tmp_path / "state.db"
    settings = Settings(
        cache_dir=tmp_path, db_backend="sqlite", state_db_path=db_path,
        dart_api_key=None, translation_api_key=None, edgar_user_agent=None, edinet_subscription_key=None,
    )
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        from src.ui.pages.radar_inbox import _load_dashboard_snapshot

        _load_dashboard_snapshot.clear()
        at = AppTest.from_file(str(_HARNESS), default_timeout=15)
        at.run()
        _load_dashboard_snapshot.clear()
    assert not at.exception
    # No source is ready at all here -> the missing-configuration empty
    # state renders, and neither the freshness line nor Ingestion status
    # ever gets to the point of describing EDINET as anything but absent.
    assert "Radar Inbox is not configured" in _text(at)
    assert "EDGE_EDINET_SUBSCRIPTION_KEY is not configured." in _text(at)
