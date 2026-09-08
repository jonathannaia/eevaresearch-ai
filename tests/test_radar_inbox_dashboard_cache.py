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


# --- Durable-State Phase 4M-3 — timing/instrumentation proves cache hit vs miss, and cache-key stability ---

def test_cache_miss_log_line_appears_once_for_two_calls_within_ttl(tmp_path, capsys):
    """The instrumentation's own diagnostic contract: the 'cache MISS'
    line (and every per-step timing line) must print on the first call
    and must NOT print again on a second call served from cache — its
    mere absence on the second call is the proof a cache hit occurred."""
    settings = _settings(cache_dir=tmp_path)
    fingerprint = radar_inbox._dashboard_config_fingerprint(settings)

    radar_inbox._load_dashboard_snapshot(settings.cache_dir, fingerprint, settings)
    first_output = capsys.readouterr().out
    assert "dashboard snapshot cache MISS" in first_output
    assert "dashboard snapshot TOTAL" in first_output

    radar_inbox._load_dashboard_snapshot(settings.cache_dir, fingerprint, settings)
    second_output = capsys.readouterr().out
    assert second_output == ""  # nothing printed at all — proves the function body did not run


def test_timing_logs_never_contain_a_dsn_or_credential(tmp_path, capsys):
    settings = _settings(cache_dir=tmp_path, db_backend="postgres", state_db_url="postgres://user:pw@example.invalid/db")
    fingerprint = radar_inbox._dashboard_config_fingerprint(settings)

    radar_inbox._load_dashboard_snapshot(settings.cache_dir, fingerprint, settings)
    output = capsys.readouterr().out

    assert "postgres://" not in output
    assert "example.invalid" not in output
    assert "pw" not in output
    # The fingerprint IS expected in the logs — but only its safe, already-
    # non-secret shape (backend name + presence boolean), confirmed here.
    assert str(fingerprint) in output
    assert fingerprint == ("postgres", True)


def test_dashboard_config_fingerprint_is_stable_across_equivalent_settings_instances(tmp_path):
    """Two separately-constructed Settings objects with the same
    backend/state_db_url-presence must produce an identical, equal
    fingerprint — the cache key must not depend on object identity or
    field values Streamlit would need to hash."""
    settings_a = _settings(cache_dir=tmp_path, db_backend="postgres", state_db_url="postgres://a")
    settings_b = _settings(cache_dir=tmp_path, db_backend="postgres", state_db_url="postgres://completely-different-b")

    fingerprint_a = radar_inbox._dashboard_config_fingerprint(settings_a)
    fingerprint_b = radar_inbox._dashboard_config_fingerprint(settings_b)

    # Same backend, both have a URL present -> same fingerprint, even
    # though the DSNs themselves differ (the fingerprint deliberately
    # never encodes the DSN's own value — see its own docstring).
    assert fingerprint_a == fingerprint_b == ("postgres", True)


def test_ordinary_render_never_calls_clear_on_the_dashboard_snapshot_cache(tmp_path, monkeypatch):
    """No unconditional invalidation on ordinary render: a plain page
    load/refresh (no button clicked) must never call .clear() on the
    cached snapshot function — only the explicit action handlers
    (_on_process, _on_review_decision, and a clicked Scan button) may."""
    settings = _settings(cache_dir=tmp_path)
    calls = []
    monkeypatch.setattr(radar_inbox._load_dashboard_snapshot, "clear", lambda: calls.append(1))

    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
        assert not at.exception
        at.run()
        assert not at.exception

    assert calls == []
