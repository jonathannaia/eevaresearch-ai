"""What the agent is allowed to do on this run, and who decided it.

Three inputs, each able only to restrict:

  * `EDGE_RESEARCH_AGENT_MODE` — the operator's environment setting.
    Anything unrecognized, absent or empty resolves to `off`; a typo is
    never read as permission;
  * the `agent_control` row — a restrictive emergency override. It can
    narrow what the environment allows and can never widen it, so an
    operator can stop the agent from the database without a redeploy,
    but nobody can start it from there;
  * `RELEASE_MAX_MODE` — this release's own ceiling. Publishing is
    deferred to the limited-autonomous-publishing release (E6, E7, E8,
    retraction and the V25 / SQLite V23 migration), so `publish` is
    unreachable here however the environment is configured.

The kill switch is deliberately NOT folded into the mode. It is a
separate hold, recorded separately, because a decision blocked by the
kill switch inside shadow mode has to retain both facts: which hold
stopped it, and what mode the run was in. Collapsing them would lose
exactly the context the review page exists to show.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.models.agent_records import AGENT_MODES, AgentMode, more_restrictive

# This release ships observability, not publishing.
RELEASE_MAX_MODE: AgentMode = "shadow"

ENV_VAR = "EDGE_RESEARCH_AGENT_MODE"


@dataclass(frozen=True)
class ResolvedMode:
    """The mode the run actually gets, plus why — the audit trail for a
    question an operator will ask later ("why did nothing happen?")."""
    mode: AgentMode
    kill_switch_on: bool
    configured: str
    reason: str

    @property
    def is_off(self) -> bool:
        return self.mode == "off"

    @property
    def may_write_outside_agent_tables(self) -> bool:
        """The single question every write path asks. False in every mode
        this release can produce, and false whenever the kill switch is on
        regardless of mode."""
        return self.mode == "publish" and not self.kill_switch_on


def parse_mode(value: object) -> tuple[AgentMode, str]:
    """Fail closed. Returns the mode and the reason it was chosen."""
    if value is None:
        return "off", f"{ENV_VAR} is not set"
    text = str(value).strip().lower()
    if not text:
        return "off", f"{ENV_VAR} is empty"
    if text not in AGENT_MODES:
        return "off", f"{ENV_VAR}={text!r} is not one of {', '.join(AGENT_MODES)}"
    return text, f"{ENV_VAR}={text}"  # type: ignore[return-value]


def resolve_mode(settings, control=None) -> ResolvedMode:
    """`control` is the agent_control row, or None when no override is
    stored. Both the override and the release ceiling may only narrow."""
    configured = getattr(settings, "research_agent_mode", None)
    mode, reason = parse_mode(configured)

    override = getattr(control, "mode_override", None) if control is not None else None
    if override is not None:
        parsed_override, override_reason = parse_mode(override)
        narrowed = more_restrictive(mode, parsed_override)
        if narrowed != mode:
            detail = (getattr(control, "reason", "") or "").strip()
            reason = f"emergency override to {narrowed}" + (f": {detail}" if detail else "")
            mode = narrowed
        elif parsed_override != mode:
            # An override that would widen is ignored, and says so.
            reason = f"{reason} (override {override_reason} ignored: it would widen)"

    capped = more_restrictive(mode, RELEASE_MAX_MODE)
    if capped != mode:
        reason = f"{reason}, capped to {capped}: publishing is not part of this release"
        mode = capped

    return ResolvedMode(
        mode=mode,
        kill_switch_on=bool(getattr(settings, "research_agent_publication_kill_switch_enabled", False)),
        configured="" if configured is None else str(configured),
        reason=reason,
    )
