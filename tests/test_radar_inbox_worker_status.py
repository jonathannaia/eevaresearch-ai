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

from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.state_db.scan_status_repository import ProviderScanStatus
from src.ui.pages.radar_inbox import _WORKER_STATUS_SCOPE_NOTE, _load_dashboard_snapshot, _worker_scan_status_snapshot

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
    db_path = tmp_path / "state.db"
    settings = _ready_settings(cache_dir=tmp_path, db_backend="sqlite", state_db_path=db_path)
    repo = backend_factory.get_scan_status_repository(settings)
    repo.upsert_scan_status(ProviderScanStatus(
        provider="SEC EDGAR", cursor_value="2026-08-20", started_at="2026-08-20T00:00:00+00:00",
        completed_at="2026-08-20T00:01:00+00:00", last_successful_at="2026-08-20T00:01:00+00:00",
        items_discovered=3, candidates_created=1, skipped_unresolved_count=0, failure_code=None,
        updated_at="2026-08-20T00:01:00+00:00",
    ))
    repo.upsert_scan_status(ProviderScanStatus(
        provider="OpenDART / DART", cursor_value=None, started_at="2026-08-21T00:00:00+00:00",
        completed_at="2026-08-21T00:00:05+00:00", last_successful_at=None,
        items_discovered=0, candidates_created=0, skipped_unresolved_count=0,
        failure_code="ConnectionResetError", updated_at="2026-08-21T00:00:05+00:00",
    ))
    # EDINET is left with no status row at all -> "no continuous scan has run yet".

    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "SEC EDGAR: healthy" in all_text
    assert "OpenDART / DART: last continuous scan attempt did not complete" in all_text
    assert "EDINET: no continuous scan has run yet" in all_text
    assert "ConnectionResetError" not in all_text
