"""Durable-State Phase 4M-0 (corrected) — Radar Inbox's read-only,
worker-status display (_worker_scan_status_snapshot/_render_worker_status).

Corrected boundary: this section reads only this page's own, already
existing `settings.db_backend`/`settings.state_db_url` (the same
dashboard Postgres/SQLite bridge established in Phase 4B) — never
`radar_worker_db_backend`/`radar_worker_state_db_path`/
`radar_worker_state_db_url`, which are worker-only (see
tests/test_radar_worker_dsn_boundary.py for the structural guard on
that). It also never claims worker-persisted candidates appear
anywhere in this dashboard — that requires a separately approved
dashboard persistence-read bridge that does not exist yet.

Direct unit tests for the pure snapshot function (no Streamlit needed),
plus a small AppTest-based check that the rendered text never leaks a
raw exception, DSN, or environment-variable name, and always carries
the scope-limiting note. No scan/process service is ever called."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.dart.radar_service import RadarReadiness
from src.data_access.edgar.edgar_service import EdgarReadiness
from src.data_access.state_db.scan_status_repository import ProviderScanStatus
from src.ui.pages.radar_inbox import _WORKER_STATUS_SCOPE_NOTE, _load_dashboard_snapshot, _worker_scan_status_snapshot

_READY_DART = RadarReadiness(dart_key_configured=True, translation_key_configured=True, unresolved_companies=())
_READY_EDGAR = EdgarReadiness(user_agent_configured=True, unresolved_companies=())


def _recent_iso(minutes_ago: int = 10) -> str:
    """A timestamp relative to the actual moment the test runs — Phase F1's
    "recent" vs. "stale" categorization is threshold-based (3x the
    configured interval), so a hardcoded past date would drift into
    "stale" as real time passes, unlike the pre-Phase-F1 wording this
    test file used to check, which didn't depend on elapsed time at all."""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()

_HARNESS = Path(__file__).parent / "apptest_pages" / "radar_inbox_page.py"


@pytest.fixture(autouse=True)
def _clear_dashboard_snapshot_cache():
    """Durable-State Phase 4M-2 — see the identical fixture's own
    docstring in tests/test_radar_inbox_page.py for why this is needed:
    `_load_dashboard_snapshot`'s `st.cache_data` cache is process-wide,
    not per-test."""
    _load_dashboard_snapshot.clear()
    yield
    _load_dashboard_snapshot.clear()


def _base_settings(**overrides) -> Settings:
    fields = dict(
        dart_api_key=None, translation_api_key=None, edgar_user_agent=None,
        edinet_subscription_key=None,
        radar_worker_db_backend=None, radar_worker_state_db_path=None, radar_worker_state_db_url=None,
    )
    fields.update(overrides)
    return Settings(**fields)


# --- Pure snapshot function — no Streamlit involved ---

def test_snapshot_reports_not_configured_when_dashboards_own_backend_is_json(tmp_path):
    settings = _base_settings(cache_dir=tmp_path)  # db_backend defaults to "json"
    assert _worker_scan_status_snapshot(settings) == ("not_configured", None)


def test_snapshot_ignores_a_configured_worker_backend_when_dashboards_own_backend_is_json(tmp_path):
    """The critical boundary proof: even if the worker fields are fully
    configured, the dashboard's own status read must not be influenced
    by them at all — only settings.db_backend/state_db_url matter here."""
    settings = _base_settings(
        cache_dir=tmp_path, db_backend="json",
        radar_worker_db_backend="sqlite", radar_worker_state_db_path=tmp_path / "worker-state.db",
    )
    assert _worker_scan_status_snapshot(settings) == ("not_configured", None)


def test_snapshot_reads_the_dashboards_own_backend_fields_only(tmp_path):
    settings = _base_settings(cache_dir=tmp_path, db_backend="sqlite", state_db_path=tmp_path / "state.db")
    seeded = ProviderScanStatus(
        provider="SEC EDGAR", cursor_value="2026-08-20", started_at="2026-08-20T00:00:00+00:00",
        completed_at="2026-08-20T00:01:00+00:00", last_successful_at="2026-08-20T00:01:00+00:00",
        items_discovered=3, candidates_created=1, skipped_unresolved_count=0, failure_code=None,
        updated_at="2026-08-20T00:01:00+00:00",
    )
    # Seeded through the dashboard's own settings — exactly the store an
    # operator would be pointing both the worker and the dashboard at,
    # via their own separately-named env vars, if they want this to work.
    backend_factory.get_scan_status_repository(settings).upsert_scan_status(seeded)

    state, statuses = _worker_scan_status_snapshot(settings)
    assert state == "ok"
    assert statuses == {"SEC EDGAR": seeded}


def test_snapshot_reports_unreachable_for_a_configured_but_unreachable_postgres_target(tmp_path):
    bad_dsn = "host=127.0.0.1 port=1 dbname=x user=y password=unreachable connect_timeout=1"
    settings = _base_settings(cache_dir=tmp_path, db_backend="postgres", state_db_url=bad_dsn)
    state, statuses = _worker_scan_status_snapshot(settings)
    assert state == "unreachable"
    assert statuses is None


# --- Rendered text — never leaks secrets/exceptions/env var names; always scoped ---

def _ready_settings(**overrides) -> Settings:
    # EDINET alone is enough to pass the top-level readiness gate (its
    # five tracked companies are pre-resolved — see this codebase's own
    # Gate 7.1 tests) without needing a real DART/EDGAR credential.
    return _base_settings(edinet_subscription_key="test-key", **overrides)


def test_render_shows_not_configured_message_with_scope_note_and_no_secrets(tmp_path):
    settings = _ready_settings(cache_dir=tmp_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert _WORKER_STATUS_SCOPE_NOTE in all_text
    assert "not configured with a" in all_text
    for leaked in ("EDGE_RADAR_WORKER", "EDGE_STATE_DB", "EDGE_DB_BACKEND", "postgres://", "password"):
        assert leaked not in all_text


def test_render_never_claims_worker_candidates_automatically_appear(tmp_path):
    """Even in the 'ok' state with real status rows, the section must
    never claim the underlying candidate records themselves are visible
    anywhere in this dashboard — only the scope note's own honest
    'not a candidate feed' language."""
    db_path = tmp_path / "state.db"
    settings = _ready_settings(cache_dir=tmp_path, db_backend="sqlite", state_db_path=db_path)
    backend_factory.get_scan_status_repository(settings).upsert_scan_status(ProviderScanStatus(
        provider="SEC EDGAR", cursor_value="2026-08-20", started_at="2026-08-20T00:00:00+00:00",
        completed_at="2026-08-20T00:01:00+00:00", last_successful_at="2026-08-20T00:01:00+00:00",
        items_discovered=3, candidates_created=1, skipped_unresolved_count=0, failure_code=None,
        updated_at="2026-08-20T00:01:00+00:00",
    ))
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "not a candidate feed" in all_text
    assert "automatically appear" not in all_text.lower()


def test_render_shows_per_provider_states_without_leaking_failure_code_text(tmp_path):
    """Phase F1 (design/DECISIONS.md): the three-way healthy/failed/
    never-run wording is replaced by four plain-text categories —
    disabled, never-scanned, recently-successful, stale — gated first by
    readiness (so a source that isn't even configured never shows scan
    detail at all) and then by the same threshold rule the tester-facing
    freshness line uses. EDGAR/DART readiness is faked ready here (real
    readiness would also require seeding resolved identifiers into the
    SQLite identifier repository, an unrelated concern this test doesn't
    need) so all three providers' status rows are actually exercised."""
    db_path = tmp_path / "state.db"
    settings = _ready_settings(cache_dir=tmp_path, db_backend="sqlite", state_db_path=db_path)
    repo = backend_factory.get_scan_status_repository(settings)
    repo.upsert_scan_status(ProviderScanStatus(
        provider="SEC EDGAR", cursor_value="2026-08-20", started_at=_recent_iso(),
        completed_at=_recent_iso(), last_successful_at=_recent_iso(),
        items_discovered=3, candidates_created=1, skipped_unresolved_count=0, failure_code=None,
        updated_at=_recent_iso(),
    ))
    repo.upsert_scan_status(ProviderScanStatus(
        provider="OpenDART / DART", cursor_value=None, started_at=_recent_iso(), completed_at=_recent_iso(),
        last_successful_at=None, items_discovered=0, candidates_created=0, skipped_unresolved_count=0,
        failure_code="ConnectionResetError", updated_at=_recent_iso(),
    ))
    # EDINET is left with no status row at all -> "configured — no completed scan yet".

    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings), \
         patch("src.data_access.dart.radar_service.radar_readiness", return_value=_READY_DART), \
         patch("src.data_access.edgar.edgar_service.edgar_readiness", return_value=_READY_EDGAR):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "SEC EDGAR: recently successful" in all_text
    # OpenDART / DART has a failure_code but has NEVER succeeded — folded
    # into "never scanned" wording, not a distinct failure state (this
    # phase never exposes failure_code, even indirectly via a different
    # label for "attempted and failed").
    assert "OpenDART / DART: configured — no completed scan yet." in all_text
    assert "EDINET: configured — no completed scan yet." in all_text
    assert "ConnectionResetError" not in all_text


def test_render_shows_not_configured_wording_for_a_source_with_no_readiness(tmp_path):
    """A source that isn't configured at all (no credential/company
    resolution) must read as "not configured for this deployment" —
    never as "no completed scan yet," which would wrongly imply it's
    enabled and just hasn't run — and must never imply EDINET is enabled
    just because its tracked companies/identifiers exist in code."""
    db_path = tmp_path / "state.db"
    settings = _ready_settings(cache_dir=tmp_path, db_backend="sqlite", state_db_path=db_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        # DART/EDGAR left with their real (not-ready) readiness — only
        # EDINET is ready via _ready_settings()'s own subscription key.
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "SEC EDGAR: not configured for this deployment." in all_text
    assert "OpenDART / DART: not configured for this deployment." in all_text
    assert "EDINET: configured — no completed scan yet." in all_text


def test_render_shows_refresh_mode_and_expected_interval_lines(tmp_path):
    db_path = tmp_path / "state.db"
    settings = _ready_settings(cache_dir=tmp_path, db_backend="sqlite", state_db_path=db_path)
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "Refresh mode: Automatic worker when configured; manual scan controls remain available." in all_text
    assert "Expected scan interval: 60 minutes" in all_text


def test_render_shows_not_configured_worker_status_wording_when_backend_is_json(tmp_path):
    settings = _ready_settings(cache_dir=tmp_path)  # db_backend defaults to "json"
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "Automatic worker status is not configured." in all_text
    # The "Expected scan interval" line requires a reachable durable
    # status store — never shown when one isn't configured.
    assert "Expected scan interval" not in all_text
