"""Durable-State Phase 4M-2 — dashboard-snapshot caching
(`radar_inbox._load_dashboard_snapshot`), added to fix the extreme
per-refresh latency diagnosed against the deployed Postgres-backed
Radar Inbox (9+ fresh repository connections per render, none reused).

Every test here is offline: readiness/build-items/worker-status calls
are monkeypatched with counters, never real repositories or real
network calls. Uses `.clear()` to simulate TTL expiry deterministically
(per the review's own explicit allowance — mocking `st.cache_data`'s
internal clock is fragile/version-dependent; `.clear()` is Streamlit's
own supported mechanism for invalidating a cache_data-backed function)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.data_access.dart import radar_service
from src.data_access.edgar import edgar_service
from src.data_access.edinet import edinet_service
from src.ui.pages import radar_inbox

_HARNESS = Path(__file__).parent / "apptest_pages" / "radar_inbox_page.py"


@pytest.fixture(autouse=True)
def _clear_dashboard_snapshot_cache():
    radar_inbox._load_dashboard_snapshot.clear()
    yield
    radar_inbox._load_dashboard_snapshot.clear()


def _settings(**overrides) -> Settings:
    fields = dict(
        dart_api_key=None, translation_api_key=None, edgar_user_agent=None, edinet_subscription_key="test-key",
        radar_worker_db_backend=None, radar_worker_state_db_path=None, radar_worker_state_db_url=None,
    )
    fields.update(overrides)
    return Settings(**fields)


# --- Unit-level: repeated calls with the same key reuse the cache ---

def test_two_calls_with_the_same_config_fingerprint_and_cache_dir_call_build_items_once(tmp_path, monkeypatch):
    settings = _settings(cache_dir=tmp_path)
    calls = []
    real_build_items = radar_inbox._build_items

    def _counting_build_items(cache_dir, settings=None):
        calls.append(1)
        return real_build_items(cache_dir, settings)

    monkeypatch.setattr(radar_inbox, "_build_items", _counting_build_items)
    fingerprint = radar_inbox._dashboard_config_fingerprint(settings)

    first = radar_inbox._load_dashboard_snapshot(settings.cache_dir, fingerprint, settings)
    second = radar_inbox._load_dashboard_snapshot(settings.cache_dir, fingerprint, settings)

    assert len(calls) == 1  # the second call was served entirely from cache
    assert first == second


def test_two_calls_with_the_same_config_fingerprint_reuse_worker_status_and_readiness_too(tmp_path, monkeypatch):
    settings = _settings(cache_dir=tmp_path)
    dart_calls, edgar_calls, edinet_calls, worker_calls = [], [], [], []

    monkeypatch.setattr(
        radar_service, "radar_readiness",
        lambda s: dart_calls.append(1) or radar_service.RadarReadiness(False, False, ()),
    )
    monkeypatch.setattr(
        edgar_service, "edgar_readiness",
        lambda s: edgar_calls.append(1) or edgar_service.EdgarReadiness(False, ()),
    )
    monkeypatch.setattr(
        edinet_service, "edinet_readiness",
        lambda s: edinet_calls.append(1) or edinet_service.EdinetReadiness(True, ()),
    )
    monkeypatch.setattr(
        radar_inbox, "_worker_scan_status_snapshot",
        lambda s: worker_calls.append(1) or ("not_configured", None),
    )

    fingerprint = radar_inbox._dashboard_config_fingerprint(settings)
    radar_inbox._load_dashboard_snapshot(settings.cache_dir, fingerprint, settings)
    radar_inbox._load_dashboard_snapshot(settings.cache_dir, fingerprint, settings)

    assert len(dart_calls) == 1
    assert len(edgar_calls) == 1
    assert len(edinet_calls) == 1
    assert len(worker_calls) == 1


# --- Unit-level: cache invalidation (simulating TTL expiry) fetches fresh data ---

def test_clearing_the_cache_forces_a_fresh_read_on_the_next_call(tmp_path, monkeypatch):
    settings = _settings(cache_dir=tmp_path)
    calls = []
    real_build_items = radar_inbox._build_items

    def _counting_build_items(cache_dir, settings=None):
        calls.append(1)
        return real_build_items(cache_dir, settings)

    monkeypatch.setattr(radar_inbox, "_build_items", _counting_build_items)
    fingerprint = radar_inbox._dashboard_config_fingerprint(settings)

    radar_inbox._load_dashboard_snapshot(settings.cache_dir, fingerprint, settings)
    radar_inbox._load_dashboard_snapshot(settings.cache_dir, fingerprint, settings)
    assert len(calls) == 1  # still cached

    radar_inbox._load_dashboard_snapshot.clear()  # simulates TTL expiry
    radar_inbox._load_dashboard_snapshot(settings.cache_dir, fingerprint, settings)
    assert len(calls) == 2  # cache expired -> re-fetched


def test_a_different_config_fingerprint_is_not_served_from_the_other_fingerprints_cache_entry(tmp_path, monkeypatch):
    """A real settings change (e.g. EDGE_DB_BACKEND flips) must not be
    masked by a stale cache entry keyed to the old backend."""
    settings = _settings(cache_dir=tmp_path)
    calls = []
    real_build_items = radar_inbox._build_items

    def _counting_build_items(cache_dir, settings=None):
        calls.append((settings.db_backend if settings else "json"))
        return real_build_items(cache_dir, settings)

    monkeypatch.setattr(radar_inbox, "_build_items", _counting_build_items)

    json_settings = settings
    sqlite_settings = Settings(**{**vars(settings), "db_backend": "sqlite", "state_db_path": tmp_path / "state.db"})

    radar_inbox._load_dashboard_snapshot(
        json_settings.cache_dir, radar_inbox._dashboard_config_fingerprint(json_settings), json_settings,
    )
    radar_inbox._load_dashboard_snapshot(
        sqlite_settings.cache_dir, radar_inbox._dashboard_config_fingerprint(sqlite_settings), sqlite_settings,
    )

    assert calls == ["json", "sqlite"]  # both fetched — different fingerprints, no cache collision


# --- Page-level: a second render within the TTL does not re-invoke the expensive reads ---

def test_full_page_rerender_within_ttl_does_not_recall_build_items(tmp_path, monkeypatch):
    settings = _settings(cache_dir=tmp_path)
    calls = []
    real_build_items = radar_inbox._build_items
    monkeypatch.setattr(
        radar_inbox, "_build_items",
        lambda cache_dir, settings=None: calls.append(1) or real_build_items(cache_dir, settings),
    )

    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        assert not at.exception
        at.run()  # a second rerun, simulating a second browser refresh
        assert not at.exception

    assert len(calls) == 1  # the second refresh was served from the cached snapshot


def test_full_page_rerender_after_cache_clear_recalls_build_items(tmp_path, monkeypatch):
    """The TTL-expiry equivalent at the page level: once the cache is
    cleared (standing in for the 60-second TTL elapsing), the very next
    refresh must fetch fresh data again — new worker-written results
    must become visible without any manual action beyond a refresh."""
    settings = _settings(cache_dir=tmp_path)
    calls = []
    real_build_items = radar_inbox._build_items
    monkeypatch.setattr(
        radar_inbox, "_build_items",
        lambda cache_dir, settings=None: calls.append(1) or real_build_items(cache_dir, settings),
    )

    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        assert not at.exception
        radar_inbox._load_dashboard_snapshot.clear()
        at.run()
        assert not at.exception

    assert len(calls) == 2


# --- No live source call — the existing invariant must survive caching ---

def test_dashboard_snapshot_path_never_calls_a_live_scan_or_process_function(tmp_path, monkeypatch):
    def _forbidden(*_args, **_kwargs):
        raise AssertionError("Dashboard snapshot path must never call a live scan/process function.")

    for module, attr in [
        (radar_service, "run_scan"), (radar_service, "process_candidate_now"),
        (edgar_service, "run_scan"), (edgar_service, "process_candidate_now"),
        (edinet_service, "run_scan"), (edinet_service, "process_candidate_now"),
    ]:
        monkeypatch.setattr(module, attr, _forbidden, raising=True)

    settings = _settings(cache_dir=tmp_path)
    fingerprint = radar_inbox._dashboard_config_fingerprint(settings)
    snapshot = radar_inbox._load_dashboard_snapshot(settings.cache_dir, fingerprint, settings)
    assert snapshot is not None  # completed without tripping any forbidden call above
