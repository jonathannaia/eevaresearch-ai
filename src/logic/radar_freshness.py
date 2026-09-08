"""Durable Radar Inbox freshness (Phase F1, design/DECISIONS.md) — pure
functions turning `provider_scan_status` records into the honest,
tester-facing freshness line and the operator-panel per-source category,
using one shared threshold rule for both. Deliberately reads nothing
except what's passed in: no browser/session time, no source code, no
seed/demo files, no page-render time, no file modification time, and no
unpersisted in-session manual-scan report — only durable
`ProviderScanStatus` rows already read by Radar Inbox's own
`_worker_scan_status_snapshot()`.

A source with no successful scan ever (`last_successful_at` is None) is
classified "never_scanned" regardless of whether a failed attempt was
recorded — this phase's operator panel distinguishes disabled / never-
scanned / recent / stale only, folding "attempted and failed" into
"never scanned" rather than exposing `failure_code` detail (see
design/DECISIONS.md's Phase F1 entry for why)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.data_access.state_db.scan_status_repository import ProviderScanStatus
from src.logic.formatting import fmt_datetime_local

DEFAULT_INTERVAL_MINUTES = 60
STALE_THRESHOLD_MULTIPLIER = 3

UNAVAILABLE_MESSAGE = "Filing data may not be current."
NO_SCAN_YET_MESSAGE = "No completed scan yet for this source."


@dataclass(frozen=True)
class RadarFreshness:
    state: str  # "all_recent" | "partial" | "all_stale" | "no_scan_yet" | "unavailable"
    message: str


def effective_interval_minutes(interval_minutes: int | None) -> int:
    """A safe default (the same 60 minutes Settings itself defaults to)
    only when the configured value is missing or non-positive — never a
    silent guess when a real, valid value is available."""
    return interval_minutes if interval_minutes and interval_minutes > 0 else DEFAULT_INTERVAL_MINUTES


def stale_threshold_minutes(interval_minutes: int | None) -> int:
    return effective_interval_minutes(interval_minutes) * STALE_THRESHOLD_MULTIPLIER


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def categorize_source_status(
    status: ProviderScanStatus | None, interval_minutes: int | None, now: datetime | None = None,
) -> str:
    """One of "never_scanned" | "recent" | "stale" for a single source
    that is already known to be enabled/configured — the caller decides
    "disabled" from readiness before ever calling this; this function has
    no concept of readiness itself."""
    now = now or datetime.now(timezone.utc)
    last_success = _parse_utc(status.last_successful_at) if status else None
    if last_success is None:
        return "never_scanned"
    age_minutes = (now - last_success).total_seconds() / 60
    return "recent" if age_minutes <= stale_threshold_minutes(interval_minutes) else "stale"


def compute_radar_freshness(
    worker_status_state: str,
    statuses: dict[str, ProviderScanStatus] | None,
    enabled_sources: tuple[str, ...],
    interval_minutes: int | None,
    now: datetime | None = None,
) -> RadarFreshness:
    """`worker_status_state`/`statuses` come straight from Radar Inbox's
    own `_worker_scan_status_snapshot()` — never recomputed here.
    `enabled_sources` are the display names (e.g. "SEC EDGAR") this
    deployment currently considers configured/ready; a source outside
    this tuple (EDINET, until separately enabled) never counts toward
    any state below, including "no completed scan yet"."""
    if worker_status_state != "ok" or not enabled_sources:
        return RadarFreshness("unavailable", UNAVAILABLE_MESSAGE)

    now = now or datetime.now(timezone.utc)
    never_scanned: list[str] = []
    recent: list[tuple[str, datetime]] = []
    stale: list[tuple[str, datetime]] = []

    for source in enabled_sources:
        status = (statuses or {}).get(source)
        category = categorize_source_status(status, interval_minutes, now)
        if category == "never_scanned":
            never_scanned.append(source)
        else:
            last_success = _parse_utc(status.last_successful_at)  # status is guaranteed non-None here
            (recent if category == "recent" else stale).append((source, last_success))

    if never_scanned:
        return RadarFreshness("no_scan_yet", NO_SCAN_YET_MESSAGE)

    if recent and not stale:
        latest = max(ts for _, ts in recent)
        return RadarFreshness("all_recent", f"Filing data last refreshed {fmt_datetime_local(latest.isoformat())}.")

    if stale and not recent:
        latest = max(ts for _, ts in stale)
        return RadarFreshness(
            "all_stale", f"Filing data may be out of date · Last successful update {fmt_datetime_local(latest.isoformat())}.",
        )

    latest_recent = max(ts for _, ts in recent)
    return RadarFreshness(
        "partial", f"Some sources refreshed as recently as {fmt_datetime_local(latest_recent.isoformat())}; others are delayed.",
    )
