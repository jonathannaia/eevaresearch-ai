"""Re-classify already-persisted editorial stories inside the live
7-day display window, so a shipped classification fix reaches the feed
instead of waiting a week for the wrong rows to age out.

DRY RUN BY DEFAULT. With no flag this prints what would change and
writes nothing. --apply is the only thing that writes, and when it
does, every story update is paired in one transaction with an
append-only revision row recording the old tier, the old companies,
the reason and the timestamp (see editorial_story_revisions, schema
V29 / SQLite V27).

    .venv/bin/python -m scripts.reclassify_editorial_window
    .venv/bin/python -m scripts.reclassify_editorial_window --apply

Postgres only, deliberately: the stories this corrects are the live
ones, and the live store is Postgres. There is no "which backend"
switch to get wrong.

WHY DEDUPE RUNS INSIDE THE PASS. Re-classifying a window that predates
the same-story dedupe would otherwise promote every member of a
duplicate cluster at once — measured on the live window, the
governance gate alone would have raised eight copies of one Nvidia
story to High Signal simultaneously. plan_reclassification() therefore
re-runs same-story and same-event detection across the window and
suppresses later members, first-seen-wins. See
editorial_reclassification.py.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from src.config.settings import Settings
from src.data_access.daily_news.editorial_reclassification import plan_reclassification
from src.data_access.postgres_state_db import editorial_story_repository
from src.data_access.postgres_state_db import schema as postgres_schema
from src.data_access.postgres_state_db.connection import connect


def _format(change) -> str:
    old_tier = change.old_tier.value if change.old_tier else "none"
    lines = [f"  {change.headline[:88]}"]
    if change.tier_changed:
        lines.append(f"      tier:      {old_tier} -> {change.new_tier.value}")
    if change.companies_changed:
        lines.append(f"      companies: {list(change.old_companies)} -> {list(change.new_companies)}")
    lines.append(f"      reason:    {change.reason}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="write the corrections. Without this the run is read-only.",
    )
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    now = datetime.now(timezone.utc)

    dsn = settings.state_db_url
    if not dsn or not str(dsn).strip():
        # Same fail-closed, no-detail convention as
        # daily_news_backend._require_postgres_connection: say what is
        # missing, never echo connection information.
        print("EDGE_STATE_DB_URL is not configured. Refusing to run.", file=sys.stderr)
        return 2

    with connect(dsn) as conn:
        postgres_schema.migrate(conn)
        stories = editorial_story_repository.load_stories(conn)
        changes = plan_reclassification(list(stories.values()), now)

        tier_changes = [c for c in changes if c.tier_changed]
        company_changes = [c for c in changes if c.companies_changed]
        duplicates = [c for c in changes if c.reason.startswith(("same_story", "same_event"))]

        print(f"stories in store: {len(stories)}")
        print(f"changes proposed: {len(changes)}"
              f"  (tier: {len(tier_changes)}, companies: {len(company_changes)},"
              f" duplicates suppressed: {len(duplicates)})")
        print()
        for change in changes:
            print(_format(change))
            print()

        if not args.apply:
            print("DRY RUN — nothing written. Re-run with --apply to write.")
            return 0
        if not changes:
            print("nothing to apply.")
            return 0

        updated = editorial_story_repository.apply_reclassification(conn, changes)
        print(f"applied: {updated} stories updated, {updated} revision rows written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
