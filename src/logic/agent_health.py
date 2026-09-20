"""Is the agent alive, what is it allowed to do, and is anything wrong?

Deliberately dependency-light. The Agent Review page needs this, and the
UI may not import the agent runtime (tests/test_research_agent_worker_
scope_guard.py forbids src.mcp_agent anywhere under src/ui). The
scheduler and the publication policy both reach that package
transitively, so the shared liveness constants live here instead, and
src/logic/agent_scheduler.py imports them from this module rather than
the other way round.

Nothing here reaches a database, a model session or a network: callers
pass in what they read.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

# A run whose heartbeat is older than this is presumed dead: another
# worker may take over, and the review page says so. Comfortably longer
# than one session.
HEARTBEAT_STALE_MINUTES = 45

# Above this, the review page raises a banner rather than leaving the
# numbers to be noticed.
DEAD_JOB_BANNER_THRESHOLD = 5
ERROR_RATE_BANNER_THRESHOLD = 0.25


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def age_minutes(value: str | None, *, now: datetime) -> float | None:
    parsed = parse_iso(value)
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds() / 60.0)


def humanize_age(minutes: float | None) -> str:
    if minutes is None:
        return "never"
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{int(minutes)} min ago"
    if minutes < 60 * 24:
        return f"{minutes / 60:.1f} h ago"
    return f"{minutes / (60 * 24):.1f} d ago"


@dataclass(frozen=True)
class AgentHealth:
    """Everything the health strip shows, already decided. The page
    renders it; it does not work anything out for itself."""
    configured_mode: str
    effective_mode: str
    effective_label: str
    kill_switch_on: bool
    override_mode: str | None
    override_reason: str | None
    override_by: str | None
    last_run_id: str | None = None
    last_run_status: str | None = None
    last_run_started_at: str | None = None
    last_run_completed_at: str | None = None
    heartbeat_at: str | None = None
    heartbeat_age_minutes: float | None = None
    decisions_24h: dict[str, int] | None = None
    jobs_24h: dict[str, int] | None = None
    packets_24h: int = 0

    @property
    def heartbeat_is_stale(self) -> bool:
        """A completed run is not stale — it stopped on purpose. Only a
        run still claiming to be `running` can go stale."""
        if self.last_run_status != "running":
            return False
        return self.heartbeat_age_minutes is None or self.heartbeat_age_minutes > HEARTBEAT_STALE_MINUTES

    @property
    def dead_jobs(self) -> int:
        return (self.jobs_24h or {}).get("dead", 0)

    @property
    def failed_jobs(self) -> int:
        return (self.jobs_24h or {}).get("failed", 0)

    @property
    def finished_jobs(self) -> int:
        counts = self.jobs_24h or {}
        return sum(counts.get(state, 0) for state in ("done", "failed", "dead"))

    @property
    def error_rate(self) -> float:
        return (self.failed_jobs + self.dead_jobs) / self.finished_jobs if self.finished_jobs else 0.0

    @property
    def has_job_trouble(self) -> bool:
        return self.dead_jobs >= DEAD_JOB_BANNER_THRESHOLD or (
            self.finished_jobs > 0 and self.error_rate >= ERROR_RATE_BANNER_THRESHOLD
        )

    @property
    def was_downgraded(self) -> bool:
        return self.configured_mode != self.effective_mode


def build_health(store, resolved, *, now: datetime, since: str) -> AgentHealth:
    """Reads only the agent tables, and tolerates every one of them being
    unreadable — a health panel that raises is worse than one that says
    it does not know."""
    control = _safe(lambda: store.get_control())
    run = _safe(lambda: store.latest_run())
    heartbeat = _safe(lambda: store.latest_heartbeat_at(run.run_id)) if run is not None else None
    return AgentHealth(
        configured_mode=_configured(resolved),
        effective_mode=resolved.mode,
        effective_label=resolved.effective_label,
        kill_switch_on=bool(resolved.kill_switch_on),
        override_mode=getattr(control, "mode_override", None),
        override_reason=getattr(control, "reason", None),
        override_by=getattr(control, "updated_by", None),
        last_run_id=getattr(run, "run_id", None),
        last_run_status=getattr(run, "status", None),
        last_run_started_at=getattr(run, "started_at", None),
        last_run_completed_at=getattr(run, "completed_at", None),
        heartbeat_at=heartbeat,
        heartbeat_age_minutes=age_minutes(heartbeat or getattr(run, "started_at", None), now=now),
        decisions_24h=_safe(lambda: store.count_decisions(since=since)) or {},
        jobs_24h=_safe(lambda: store.count_jobs_by_state(since=since)) or {},
        packets_24h=_safe(lambda: store.count_packets_since(since=since)) or 0,
    )


def _configured(resolved) -> str:
    from src.logic.agent_mode import parse_mode

    return parse_mode(getattr(resolved, "configured", "")) [0]


def _safe(read):
    try:
        return read()
    except Exception:  # noqa: BLE001 — an unreadable panel says so, it never breaks the page
        return None
