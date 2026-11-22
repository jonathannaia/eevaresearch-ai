"""Withdraw one autonomously published EDGAR 424B5 Signal.

    python -m scripts.withdraw_autonomous_signal --candidate-id <id> \
        --reason "excerpt did not support the offering claim"

Dry run by default: without --write nothing is persisted, so an
operator can see exactly what would change before changing it. The same
convention scripts/backfill_daily_news_materiality_tiers.py already
uses.

Withdrawal is a status change, never a delete — the candidate, its
excerpt, its evidence and its full transition history are retained. It
is idempotent: running it twice on the same candidate changes nothing
and records nothing the second time.

Only an autonomously published candidate can be withdrawn here. A
human-published Signal is somebody's editorial decision and is refused,
not silently reversed.

Nothing here prints or accepts a credential, and no issuer name,
headline, excerpt or URL is ever printed — only the candidate id the
operator already supplied and a closed-vocabulary outcome.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

_EXIT_OK = 0
_EXIT_NOT_FOUND = 2
_EXIT_REFUSED = 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Withdraw one autonomously published Signal.")
    parser.add_argument("--candidate-id", required=True, help="Stable candidate identifier.")
    parser.add_argument("--reason", required=True, help="Why (recorded permanently with the record).")
    parser.add_argument("--source", default="SEC EDGAR", help="Source lane the candidate belongs to.")
    parser.add_argument("--mode", default="live", help="Operating mode, recorded with the withdrawal.")
    parser.add_argument("--write", action="store_true",
                        help="Persist the withdrawal. Without this the run is a dry run.")
    args = parser.parse_args(argv)

    from src.config.settings import get_settings
    from src.data_access import backend_factory
    from src.logic.signal_withdrawal import OUTCOME_WITHDRAWN, withdraw_autonomous_signal

    settings = get_settings()
    repository = backend_factory.get_candidate_repository(settings, args.source)
    candidate = repository.get_candidate(args.candidate_id)
    if candidate is None:
        print(f"candidate {args.candidate_id}: not found")
        return _EXIT_NOT_FOUND

    now = datetime.now(timezone.utc).isoformat()
    result = withdraw_autonomous_signal(candidate, reason=args.reason, mode=args.mode, now=now)
    print(f"candidate {args.candidate_id}: {result.outcome}")

    if not result.changed:
        # already_withdrawn is a success: the end state is the wanted one.
        return _EXIT_OK if result.outcome != "not_autonomous" and result.outcome != "not_published" else _EXIT_REFUSED
    if not args.write:
        print("dry run — pass --write to persist. Nothing was changed.")
        return _EXIT_OK

    outcome = repository.update_candidate(result.candidate)
    print(f"persisted: {outcome.status}")
    conn = getattr(repository, "conn", None)
    if conn is not None and hasattr(conn, "commit"):
        conn.commit()
    return _EXIT_OK if outcome.status == "updated" else _EXIT_REFUSED


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
