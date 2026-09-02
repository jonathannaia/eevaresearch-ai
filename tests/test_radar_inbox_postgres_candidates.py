"""Durable-State Phase 4M-1 — Dashboard Live-Candidate Read Bridge.

Proves Radar Inbox's `_build_items`/`_edinet_scope_line` render
persisted CandidateSignal/FilingEvent records through the dashboard's
own, already-existing (Phase 4B) `EDGE_DB_BACKEND=postgres`/
`EDGE_STATE_DB_URL` settings — never any `EDGE_RADAR_WORKER_*` field —
against the real local disposable Postgres test container (skips softly
without it, per this repo's established convention; see
tests/_postgres_test_support.py). Candidates are seeded directly through
`backend_factory.get_candidate_repository(...).upsert_new_candidates()`
— the exact same write path scripts/radar_worker.py itself uses via
edgar_pipeline.run_pipeline/radar_pipeline.run_pipeline — never through
the worker script itself, which this file never imports or runs.

Every test here is read-only from the page's own perspective: no test
calls a scan/process function, and the dedicated
`test_bare_render_never_changes_a_postgres_backed_candidates_status_or_version`
test below proves rendering alone leaves a seeded candidate completely
unchanged. The one exception, `test_publish_action_still_works_for_a_postgres_backed_candidate`,
deliberately clicks the *existing*, unmodified Publish button — proving
Decision 1 (review controls stay available for Postgres-backed
candidates) — via an explicit user action, not page render."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.data_access import backend_factory
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, FilingEvent, StateTransition
from src.ui.pages.radar_inbox import _load_dashboard_snapshot

from tests._postgres_test_support import pg_isolated_dsn  # noqa: F401


@pytest.fixture(autouse=True)
def _clear_dashboard_snapshot_cache():
    """Durable-State Phase 4M-2 — see the identical fixture's own
    docstring in tests/test_radar_inbox_page.py: `_load_dashboard_snapshot`'s
    `st.cache_data` cache is process-wide, not per-test, and several
    tests in this file share the real default `cache_dir` (their
    `Settings` never overrides it), so this isolation is load-bearing
    here, not just defensive."""
    _load_dashboard_snapshot.clear()
    yield
    _load_dashboard_snapshot.clear()

_HARNESS = Path(__file__).parent / "apptest_pages" / "radar_inbox_page.py"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _edgar_filing(rcept_no: str, corp_name: str, rcept_dt: str, source_url: str) -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code="0000045810", corp_name=corp_name, stock_code="NVDA",
        report_nm="8-K filing", rcept_dt=rcept_dt, flr_nm=corp_name, pblntf_ty="8-K",
        theme_slug="ai-buildout", source_url=source_url, retrieved_at=_now_iso(),
        source_name="SEC EDGAR", original_language="English", primary_document="doc.htm",
    )


def _dart_filing(rcept_no: str, rcept_dt: str) -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code="00126380", corp_name="삼성전자", stock_code="005930",
        report_nm="실적 발표", rcept_dt=rcept_dt, flr_nm="삼성전자", theme_slug="memory",
        source_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}", retrieved_at=_now_iso(),
    )


def _needs_review_candidate(candidate_id: str, filing: FilingEvent) -> CandidateSignal:
    return CandidateSignal(
        id=candidate_id, filing=filing, matched_rules=["earnings:earnings_or_results_report:실적"],
        confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="Filing excerpt.",
        state_history=[StateTransition(status=CandidateStatus.NEEDS_REVIEW, at=_now_iso())],
    )


def _postgres_settings(dsn: str, **overrides) -> Settings:
    fields = dict(
        db_backend="postgres", state_db_url=dsn,
        dart_api_key="dart-key", translation_api_key="deepl-key", edgar_user_agent="EevaResearch test@example.com",
        edinet_subscription_key=None,
    )
    fields.update(overrides)
    return Settings(**fields)


# --- Postgres dashboard configuration renders persisted worker-style candidates ---

def test_postgres_dashboard_configuration_renders_persisted_candidate(pg_isolated_dsn):
    settings = _postgres_settings(pg_isolated_dsn)
    filing = _edgar_filing(
        "0001045810-26-000099", "NVIDIA", "2026-08-20",
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000099/",
    )
    candidate = _needs_review_candidate("edgar-cand-pg-1", filing)
    backend_factory.get_candidate_repository(settings, "SEC EDGAR").upsert_new_candidates([candidate])

    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "NVIDIA" in all_text
    assert "SEC EDGAR" in all_text
    assert "Filing excerpt." in all_text


def test_edinet_scope_line_reflects_postgres_persisted_counts(pg_isolated_dsn):
    settings = _postgres_settings(pg_isolated_dsn, edinet_subscription_key="test-key")
    filing = FilingEvent(
        rcept_no="S100EDINET1", corp_code="E00001", corp_name="SoftBank Group", stock_code="9984",
        report_nm="有価証券報告書", rcept_dt="2026-08-15", flr_nm="SoftBank Group",
        source_url="https://disclosure2.edinet-fsa.go.jp/example", retrieved_at=_now_iso(),
        source_name="EDINET", original_language="Japanese",
    )
    backend_factory.get_filing_event_repository(settings, "EDINET")  # ensure schema migrated
    backend_factory.get_candidate_repository(settings, "EDINET").upsert_new_candidates(
        [_needs_review_candidate("edinet-cand-pg-1", filing)]
    )

    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "FilingEvents: 1" in all_text
    assert "CandidateSignals: 1" in all_text


# --- Incomplete/unavailable Postgres reads degrade safely ---

def test_postgres_backend_with_no_state_db_url_degrades_safely_no_crash_no_leak():
    # EDINET alone stays ready regardless of the broken Postgres backend
    # (its readiness check never touches a repository — see
    # _dart_readiness_or_unavailable's own docstring) — this is what lets
    # the page proceed past the top-level readiness gate into
    # _build_items() itself, which is the function under test here; with
    # every source unready, the page would show "not configured" instead
    # (also safely, but that's a different, already-covered code path).
    settings = _postgres_settings("placeholder", state_db_url=None, edinet_subscription_key="test-key")
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "No filings captured yet" in all_text
    for leaked in ("EDGE_", "BackendConfigurationError", "postgres://", "password", "Traceback"):
        assert leaked not in all_text


def test_postgres_backend_unreachable_target_degrades_safely_no_crash_no_leak():
    bad_dsn = "host=127.0.0.1 port=1 dbname=x user=y password=REDACTED_TEST_ONLY connect_timeout=1"
    settings = _postgres_settings(bad_dsn, edinet_subscription_key="test-key")
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "No filings captured yet" in all_text
    for leaked in ("127.0.0.1", "port=1", "REDACTED_TEST_ONLY", "OperationalError", "Traceback"):
        assert leaked not in all_text


def test_postgres_backend_broken_with_no_source_ready_shows_not_configured_safely():
    """When every source's own readiness also depends on the same broken
    Postgres backend (EDGAR/DART identifier lookups do; see
    _edgar_readiness_or_unavailable/_dart_readiness_or_unavailable), and
    EDINET isn't separately configured either, the page correctly shows
    its existing "not configured" state rather than an empty candidate
    list — still safely, with no leak of the underlying cause."""
    settings = _postgres_settings("placeholder", state_db_url=None, edinet_subscription_key=None)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "Radar Inbox is not configured" in all_text
    assert "status unavailable" in all_text  # honest, non-leaking placeholder
    # Naming a *missing* env var as operator guidance is this page's own
    # pre-existing, legitimate behavior (unrelated to this phase) — the
    # actual sensitive material (DSN, exception type/message, traceback)
    # must still never appear.
    for leaked in ("BackendConfigurationError", "postgres://", "connect_timeout", "Traceback"):
        assert leaked not in all_text


# --- Deterministic ordering ---

def test_candidate_ordering_is_deterministic_across_repeated_renders_with_same_date_ties(pg_isolated_dsn):
    settings = _postgres_settings(pg_isolated_dsn)
    same_date = "2026-08-20"
    edgar_filing = _edgar_filing("0001045810-26-000100", "NVIDIA", same_date, "https://example.invalid/edgar-tie")
    dart_filing = _dart_filing("20260820000100", same_date.replace("-", ""))
    backend_factory.get_candidate_repository(settings, "SEC EDGAR").upsert_new_candidates(
        [_needs_review_candidate("edgar-cand-tie", edgar_filing)]
    )
    backend_factory.get_candidate_repository(settings, "OpenDART / DART").upsert_new_candidates(
        [_needs_review_candidate("cand-tie", dart_filing)]
    )

    orders = []
    for _ in range(2):
        with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
            at = AppTest.from_file(str(_HARNESS), default_timeout=10)
            at.run()
        assert not at.exception
        all_text = " ".join(m.value for m in at.markdown)
        orders.append((all_text.find("NVIDIA"), all_text.find("실적 발표")))

    assert orders[0] == orders[1]  # identical relative order on every render


# --- Provider labels and canonical source links ---

def test_provider_label_and_canonical_source_url_render_for_postgres_candidate(pg_isolated_dsn):
    settings = _postgres_settings(pg_isolated_dsn)
    source_url = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000101/"
    filing = _edgar_filing("0001045810-26-000101", "NVIDIA", "2026-08-21", source_url)
    backend_factory.get_candidate_repository(settings, "SEC EDGAR").upsert_new_candidates(
        [_needs_review_candidate("edgar-cand-link", filing)]
    )

    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    assert any(source_url == lb.url for lb in at.get("link_button"))
    all_text = " ".join(m.value for m in at.markdown)
    assert "SEC EDGAR" in all_text


# --- Empty-but-healthy state ---

def test_no_candidates_qualify_shows_no_signals_yet_not_an_error(pg_isolated_dsn):
    settings = _postgres_settings(pg_isolated_dsn)
    filing = _edgar_filing("0001045810-26-000102", "NVIDIA", "2026-08-22", "https://example.invalid/no-candidate")
    # A bare filing event with no matching candidate — a real event the
    # scan rules simply didn't flag, exactly like a JSON/SQLite "no
    # candidate signals yet" state.
    from src.data_access.postgres_state_db import filing_event_repository as pg_filing_events

    conn = backend_factory.get_candidate_repository(settings, "SEC EDGAR").conn
    pg_filing_events.upsert_filing_event(conn, filing)

    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "No candidate signals yet" in all_text


# --- No writes / no status mutation during bare render; Signals stays PUBLISHED-only ---

def test_bare_render_never_changes_a_postgres_backed_candidates_status_or_version(pg_isolated_dsn):
    settings = _postgres_settings(pg_isolated_dsn)
    filing = _edgar_filing("0001045810-26-000103", "NVIDIA", "2026-08-23", "https://example.invalid/no-mutate")
    candidate = _needs_review_candidate("edgar-cand-nomutate", filing)
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    repo.upsert_new_candidates([candidate])
    version_before = repo.get_candidate_version(candidate.id)
    status_before = repo.get_candidate(candidate.id).status

    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
    assert not at.exception

    assert repo.get_candidate_version(candidate.id) == version_before
    assert repo.get_candidate(candidate.id).status == status_before


def test_rendering_a_non_published_postgres_candidate_produces_no_signal(pg_isolated_dsn):
    settings = _postgres_settings(pg_isolated_dsn)
    filing = _edgar_filing("0001045810-26-000104", "NVIDIA", "2026-08-24", "https://example.invalid/no-signal")
    candidate = _needs_review_candidate("edgar-cand-nosignal", filing)
    backend_factory.get_candidate_repository(settings, "SEC EDGAR").upsert_new_candidates([candidate])

    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
    assert not at.exception

    signals = backend_factory.get_signal_repository(settings).get_all_signals()
    assert signals == []


# --- Review controls remain available for Postgres-backed candidates (Decision 1) ---

def test_publish_action_still_works_for_a_postgres_backed_candidate(pg_isolated_dsn):
    settings = _postgres_settings(pg_isolated_dsn)
    filing = _edgar_filing("0001045810-26-000105", "NVIDIA", "2026-08-25", "https://example.invalid/publish")
    candidate = _needs_review_candidate("edgar-cand-publish-pg", filing)
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    repo.upsert_new_candidates([candidate])

    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        at.text_input(key=f"radar-review-note-{candidate.id}").set_value("Confirmed material.")
        at.run()
        publish_button = next(b for b in at.button if b.key == f"publish-{candidate.id}")
        publish_button.click()
        at.run()

    assert not at.exception
    reloaded = repo.get_candidate(candidate.id)
    assert reloaded.status == CandidateStatus.PUBLISHED
    assert reloaded.reviewed_note == "Confirmed material."

    signals = backend_factory.get_signal_repository(settings).get_all_signals()
    assert [s.id for s in signals] == [f"signal-{candidate.id}"]
