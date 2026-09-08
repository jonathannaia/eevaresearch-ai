"""Phase F1 (design/DECISIONS.md) — pure unit tests for
src/logic/radar_freshness.py, no Streamlit involved. Proves all five
tester-facing freshness states, the "3x configured interval" stale
threshold (with a safe default when the interval is missing/invalid),
and that the default wording never uses any of the prohibited words."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.data_access.state_db.scan_status_repository import ProviderScanStatus
from src.logic.radar_freshness import (
    DEFAULT_INTERVAL_MINUTES,
    NO_SCAN_YET_MESSAGE,
    STALE_THRESHOLD_MULTIPLIER,
    UNAVAILABLE_MESSAGE,
    categorize_source_status,
    compute_radar_freshness,
    effective_interval_minutes,
    stale_threshold_minutes,
)

_NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
_PROHIBITED_WORDS = ("live", "real-time", "automatic", "continuous", "autonomous", "scheduled", "updating")


def _status(last_successful_at: str | None, provider: str = "SEC EDGAR") -> ProviderScanStatus:
    return ProviderScanStatus(
        provider=provider, cursor_value=None, started_at=last_successful_at, completed_at=last_successful_at,
        last_successful_at=last_successful_at, items_discovered=1, candidates_created=1,
        skipped_unresolved_count=0, failure_code=None, updated_at=last_successful_at or "2026-08-28T00:00:00+00:00",
    )


def _iso(minutes_ago: int) -> str:
    return (_NOW - timedelta(minutes=minutes_ago)).isoformat()


# --- effective_interval_minutes / stale_threshold_minutes ---

def test_effective_interval_minutes_uses_configured_value():
    assert effective_interval_minutes(30) == 30


def test_effective_interval_minutes_falls_back_to_default_when_missing_or_invalid():
    assert effective_interval_minutes(None) == DEFAULT_INTERVAL_MINUTES
    assert effective_interval_minutes(0) == DEFAULT_INTERVAL_MINUTES
    assert effective_interval_minutes(-5) == DEFAULT_INTERVAL_MINUTES


def test_stale_threshold_is_exactly_three_times_the_effective_interval():
    assert stale_threshold_minutes(60) == 60 * STALE_THRESHOLD_MULTIPLIER == 180
    assert stale_threshold_minutes(None) == DEFAULT_INTERVAL_MINUTES * STALE_THRESHOLD_MULTIPLIER


# --- categorize_source_status ---

def test_categorize_never_scanned_when_no_status_row():
    assert categorize_source_status(None, 60, now=_NOW) == "never_scanned"


def test_categorize_never_scanned_when_status_row_has_no_success_even_if_attempted():
    failed_but_never_succeeded = ProviderScanStatus(
        provider="OpenDART / DART", cursor_value=None, started_at=_iso(5), completed_at=_iso(5),
        last_successful_at=None, items_discovered=0, candidates_created=0, skipped_unresolved_count=0,
        failure_code="ConnectionResetError", updated_at=_iso(5),
    )
    assert categorize_source_status(failed_but_never_succeeded, 60, now=_NOW) == "never_scanned"


def test_categorize_recent_within_threshold():
    status = _status(_iso(90))  # 90 min old, 60-min interval -> 180-min threshold
    assert categorize_source_status(status, 60, now=_NOW) == "recent"


def test_categorize_stale_beyond_threshold():
    status = _status(_iso(181))  # just past the 180-min threshold
    assert categorize_source_status(status, 60, now=_NOW) == "stale"


def test_categorize_at_exactly_the_threshold_boundary_is_recent():
    status = _status(_iso(180))
    assert categorize_source_status(status, 60, now=_NOW) == "recent"


# --- compute_radar_freshness: the five states ---

def test_state_a_all_enabled_sources_recent():
    statuses = {"SEC EDGAR": _status(_iso(10), "SEC EDGAR"), "OpenDART / DART": _status(_iso(20), "OpenDART / DART")}
    result = compute_radar_freshness("ok", statuses, ("SEC EDGAR", "OpenDART / DART"), 60, now=_NOW)
    assert result.state == "all_recent"
    assert result.message.startswith("Filing data last refreshed ")
    assert result.message.endswith(".")


def test_state_b_partial_mix_of_recent_and_stale():
    statuses = {"SEC EDGAR": _status(_iso(10), "SEC EDGAR"), "OpenDART / DART": _status(_iso(500), "OpenDART / DART")}
    result = compute_radar_freshness("ok", statuses, ("SEC EDGAR", "OpenDART / DART"), 60, now=_NOW)
    assert result.state == "partial"
    assert result.message.startswith("Some sources refreshed as recently as ")
    assert result.message.endswith("; others are delayed.")


def test_state_c_all_enabled_sources_stale():
    statuses = {"SEC EDGAR": _status(_iso(500), "SEC EDGAR"), "OpenDART / DART": _status(_iso(600), "OpenDART / DART")}
    result = compute_radar_freshness("ok", statuses, ("SEC EDGAR", "OpenDART / DART"), 60, now=_NOW)
    assert result.state == "all_stale"
    assert result.message.startswith("Filing data may be out of date · Last successful update ")


def test_state_d_an_enabled_source_has_never_completed_a_scan():
    statuses = {"SEC EDGAR": _status(_iso(10), "SEC EDGAR")}  # OpenDART / DART has no row at all
    result = compute_radar_freshness("ok", statuses, ("SEC EDGAR", "OpenDART / DART"), 60, now=_NOW)
    assert result.state == "no_scan_yet"
    assert result.message == NO_SCAN_YET_MESSAGE


def test_state_d_takes_priority_over_a_partial_or_stale_classification():
    """Even when every OTHER enabled source is happily recent, a single
    never-scanned enabled source is the more honest, more conservative
    thing to say — not "all recent" and not "partial"."""
    statuses = {
        "SEC EDGAR": _status(_iso(5), "SEC EDGAR"),
        "OpenDART / DART": _status(_iso(5), "OpenDART / DART"),
        # EDINET deliberately has no entry at all -> never_scanned.
    }
    result = compute_radar_freshness("ok", statuses, ("SEC EDGAR", "OpenDART / DART", "EDINET"), 60, now=_NOW)
    assert result.state == "no_scan_yet"


def test_state_e_no_durable_backend_configured():
    result = compute_radar_freshness("not_configured", None, ("SEC EDGAR",), 60, now=_NOW)
    assert result.state == "unavailable"
    assert result.message == UNAVAILABLE_MESSAGE


def test_state_e_durable_backend_configured_but_unreachable():
    result = compute_radar_freshness("unreachable", None, ("SEC EDGAR",), 60, now=_NOW)
    assert result.state == "unavailable"
    assert result.message == UNAVAILABLE_MESSAGE


def test_state_e_when_no_sources_are_enabled_at_all():
    result = compute_radar_freshness("ok", {}, (), 60, now=_NOW)
    assert result.state == "unavailable"
    assert result.message == UNAVAILABLE_MESSAGE


def test_freshness_uses_a_safe_default_interval_when_configuration_is_unavailable():
    """A missing/invalid interval must not crash or silently treat
    everything as instantly stale — it falls back to the same 60-minute
    default Settings itself uses."""
    status = _status(_iso(90), "SEC EDGAR")  # recent under a 60-min default (180-min threshold)
    result = compute_radar_freshness("ok", {"SEC EDGAR": status}, ("SEC EDGAR",), None, now=_NOW)
    assert result.state == "all_recent"


# --- Prohibited-words guard across every wording this module can produce ---

def test_no_freshness_message_uses_a_prohibited_word():
    messages = [
        UNAVAILABLE_MESSAGE,
        NO_SCAN_YET_MESSAGE,
        compute_radar_freshness("ok", {"SEC EDGAR": _status(_iso(10), "SEC EDGAR")}, ("SEC EDGAR",), 60, now=_NOW).message,
        compute_radar_freshness(
            "ok", {"SEC EDGAR": _status(_iso(10), "SEC EDGAR"), "OpenDART / DART": _status(_iso(500), "OpenDART / DART")},
            ("SEC EDGAR", "OpenDART / DART"), 60, now=_NOW,
        ).message,
        compute_radar_freshness("ok", {"SEC EDGAR": _status(_iso(500), "SEC EDGAR")}, ("SEC EDGAR",), 60, now=_NOW).message,
    ]
    for message in messages:
        lowered = message.lower()
        for forbidden in _PROHIBITED_WORDS:
            assert forbidden not in lowered, f"{message!r} unexpectedly contains {forbidden!r}"
