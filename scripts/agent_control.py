"""The emergency stop — the only database control this release ships.

    python -m scripts.agent_control --mode off --reason "duplicate packets"

It is restrictive by construction. `--mode` accepts `off` and nothing
else: there is no argument, no flag and no value that can start the
agent, raise its mode, or enable publishing. Turning the agent back on
is deliberately an environment change plus a restart, so that resuming
is always a decision someone makes on purpose, while stopping is one
command an operator can run at 3am without a redeploy.

The worker reads this row every tick, so an override takes effect within
one interval rather than at the next deploy.

Nothing here prints, logs or accepts a credential. The connection comes
from the ambient settings, and no argument is ever a secret — which is
what keeps this safe to run in a shell whose history is shared and whose
argv is world-readable through /proc.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from src.config.settings import get_settings
from src.data_access import backend_factory

ALLOWED_MODES = ("off",)
DEFAULT_ACTOR = "agent_control_cli"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent_control",
        description="Restrict the autonomous research agent. This release can only stop it.",
        epilog="To resume, set EDGE_RESEARCH_AGENT_MODE and restart the worker; clearing the "
               "override alone will not start anything.",
    )
    parser.add_argument(
        "--mode", required=True, choices=ALLOWED_MODES,
        help="Only 'off' is accepted. No value of this flag can enable or elevate the agent.",
    )
    parser.add_argument("--reason", required=True, help="Why — recorded with the override. Never a credential.")
    parser.add_argument("--actor", default=DEFAULT_ACTOR, help="Who is making the change (for the audit trail).")
    parser.add_argument("--clear", action="store_true",
                        help="Remove the override instead of setting it. The agent still only runs if its "
                             "environment says so.")
    return parser


def apply(settings, *, mode: str, reason: str, actor: str, clear: bool, now: str) -> str:
    if mode not in ALLOWED_MODES:  # argparse already refuses; this is the seam's own guarantee
        raise ValueError(f"refusing mode {mode!r}: this release accepts only {', '.join(ALLOWED_MODES)}")
    if not (reason or "").strip():
        raise ValueError("a reason is required")
    store = backend_factory.get_agent_store_repository(settings)
    try:
        store.set_mode_override(mode=None if clear else mode, reason=reason, updated_by=actor, now=now)
        control = store.get_control()
    finally:
        store.close()
    override = getattr(control, "mode_override", None)
    if clear:
        return "override cleared; the agent's mode now comes from its environment alone"
    return f"override set to {override!r}; the worker applies it on its next tick"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        message = apply(
            get_settings(), mode=args.mode, reason=args.reason, actor=args.actor, clear=args.clear,
            now=datetime.now(timezone.utc).isoformat(),
        )
    except ValueError as exc:  # our own validation: the message is ours and carries no connection detail
        print(f"agent_control: failed: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        # Deliberately the type only. A driver's exception text can carry
        # the connection string it was built from, and this tool is run in
        # shells whose output is pasted into tickets.
        print(f"agent_control: failed: {type(exc).__name__} (details withheld: they can contain connection settings)")
        return 1
    print(f"agent_control: {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
