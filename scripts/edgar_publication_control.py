"""The autonomous EDGAR 424B5 publication stop — the only control this
release ships for that cohort.

    python -m scripts.edgar_publication_control --off --reason "bad 424B5 match"

Restrictive by construction, exactly like scripts/agent_control.py. The
only accepted action is `--off`: there is no argument, flag or value
here that can START autonomous publication. Turning it on is
deliberately a production configuration change plus the deployment
setting, so resuming is always a decision someone makes on purpose,
while stopping is one command an operator can run at 3am without a
redeploy.

The worker reads this row every tick, so an --off takes effect within
one scan interval rather than at the next deploy.

Nothing here prints, logs or accepts a credential. The connection comes
from the ambient worker settings and no argument is ever a secret.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stop autonomous EDGAR 424B5 publication.")
    parser.add_argument("--off", action="store_true", required=True,
                        help="Disable autonomous publication. The only supported action.")
    parser.add_argument("--reason", required=True, help="Why (recorded with the row).")
    parser.add_argument("--updated-by", default="operator", help="Who (recorded with the row).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change and write nothing.")
    args = parser.parse_args(argv)

    from src.config.settings import get_settings
    from src.data_access import backend_factory

    settings = get_settings()
    repository = backend_factory.get_edgar_publication_control_repository(settings)
    current = repository.is_publication_enabled()
    print(f"autonomous EDGAR 424B5 publication currently: {'ENABLED' if current else 'disabled'}")

    if args.dry_run:
        print("dry run — no write performed.")
        return 0
    if not current:
        print("already disabled — nothing to do.")
        return 0

    now = datetime.now(timezone.utc).isoformat()
    conn = getattr(repository, "conn", None)
    repository.set_control(enabled=False, reason=args.reason, updated_by=args.updated_by, now=now)
    if conn is not None and hasattr(conn, "commit"):
        conn.commit()
    print("autonomous EDGAR 424B5 publication: DISABLED")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
