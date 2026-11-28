"""Backfill matched_themes on filing-derived editorial stories that were
projected before the bridge started tagging them.

WHY A BACKFILL IS NEEDED AT ALL. filing_signal_bridge.
project_filing_signals() now tags every filing story with its issuer's
themes (src/logic/theme_taxonomy.themes_for_company). That only affects
stories it projects from now on: upsert_new_stories() skips any id that
already exists, by design, so a story already in the store keeps the
empty list it was written with. Without this script, every per-theme
count would under-report each theme by however many filings predate the
change, and would keep doing so forever.

WHERE THE VALUES COME FROM. The issuer registry, via the story's own
matched_companies — the same read the bridge itself does, for the same
company. Nothing is inferred from the filing's text, and no story's
tier, companies, reasons, headline or excerpt is touched.

WHAT IS SKIPPED, AND WHY.
  * Non-filing rows. News stories get their themes from the editorial
    pipeline's own matcher; this script has no business rewriting them.
  * Any story that already has themes. This only ever fills an empty
    list, so a re-run is a no-op and an existing value can never be
    overwritten by a registry change.
  * Any story whose company does not resolve in the registry. themes_
    for_company() fails open and a wrong theme is worse than none.

HISTORY IS NEVER EDITED. Each change writes its OWN append-only
revision row with reason "backfill_filing_story_themes", recording the
themes the row carried before (old_themes_json, added in schema V32 for
exactly this — a revision row that says only that something changed is
the gap the 2026-11-25 repair ran into). Tier and companies are written
unchanged, because this backfill changes neither.

    .venv/bin/python -m scripts.backfill_filing_story_themes
    .venv/bin/python -m scripts.backfill_filing_story_themes --apply

Dry run by default. --apply commits explicitly and then re-reads every
changed row on a FRESH connection and prints it: a previous repair in
this repository reported success while its transaction silently failed
to commit, so the read-back, not this script's own output, is the
evidence that anything happened.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from src.config.settings import get_settings
from src.data_access.daily_news import filing_signal_bridge
from src.data_access.postgres_state_db.connection import connect
from src.logic.theme_taxonomy import themes_for_company

BACKFILL_REASON = "backfill_filing_story_themes"
BACKFILL_SOURCE = "filing_theme_backfill"


def _candidates(conn) -> list[dict]:
    """Filing rows with no themes yet. The empty list is stored as a
    JSON '[]', so both that and a NULL count as empty."""
    rows = conn.execute(
        """
        SELECT id, materiality_tier, matched_companies_json, matched_themes_json
        FROM editorial_stories
        WHERE id LIKE %s
          AND (matched_themes_json IS NULL OR matched_themes_json IN ('[]', ''))
        ORDER BY id
        """,
        (f"{filing_signal_bridge.STORY_ID_PREFIX}:%",),
    ).fetchall()
    return list(rows)


def _themes_for_row(row: dict) -> tuple[str, ...]:
    companies = json.loads(row["matched_companies_json"] or "[]")
    if len(companies) != 1:
        # A filing story carries exactly one issuer by construction
        # (filing_signal_bridge sets matched_companies=(company,)). If
        # that is not true of this row, something else wrote it and this
        # script should not guess which company's themes to use.
        return ()
    return themes_for_company(companies[0])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="commit the backfill")
    args = parser.parse_args(argv)

    settings = get_settings()
    dsn = settings.state_db_url
    if not dsn or not str(dsn).strip():
        print("EDGE_STATE_DB_URL is not configured. Refusing to run.", file=sys.stderr)
        return 2

    with connect(dsn) as conn:
        # Preflight: the revision insert needs schema V32's
        # old_themes_json. Without it --apply would raise partway
        # through the loop, after some rows had been updated and others
        # had not. Checked up front so the run either does all of its
        # work or none of it, and says which.
        #
        # current_schema() matters: information_schema spans every
        # schema on the database, so an unqualified lookup is
        # happily satisfied by a same-named table in another one
        # and reports a column this connection cannot see.
        has_column = conn.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'editorial_story_revisions'
              AND column_name = 'old_themes_json'
            """
        ).fetchone()
        if args.apply and has_column is None:
            print(
                "editorial_story_revisions.old_themes_json is missing: this database is "
                "below schema V32, so a change here could not be recorded. Deploy the "
                "migration first, then re-run.",
                file=sys.stderr,
            )
            return 2

        rows = _candidates(conn)
        planned: list[tuple[dict, tuple[str, ...]]] = []
        unresolved: list[str] = []
        no_tier: list[str] = []
        for row in rows:
            themes = _themes_for_row(row)
            if not themes:
                unresolved.append(row["id"])
            elif row["materiality_tier"] is None:
                # editorial_story_revisions.new_tier is NOT NULL, so a
                # story with no tier at all (rows persisted before the
                # materiality column existed) cannot be given a
                # revision row, and this script will not update a story
                # without logging it. Skipped and reported rather than
                # crashing the run halfway through.
                no_tier.append(row["id"])
            else:
                planned.append((row, themes))

        print(f"filing rows with no themes: {len(rows)}")
        print(
            f"resolvable from the registry: {len(planned)}   "
            f"unresolved: {len(unresolved)}   no tier to log against: {len(no_tier)}"
        )
        for story_id in unresolved:
            print(f"  UNRESOLVED (company not in the registry): {story_id}")
        for story_id in no_tier:
            print(f"  NO TIER (cannot be logged, so not updated): {story_id}")
        spread = Counter(t for _, themes in planned for t in themes)
        for theme, n in sorted(spread.items(), key=lambda kv: -kv[1]):
            print(f"  +{n:4d}  {theme}")

        if not args.apply:
            for row, themes in planned[:20]:
                print(f"  [] -> {list(themes)}  {row['id']}")
            if len(planned) > 20:
                print(f"  ... and {len(planned) - 20} more")
            print("DRY RUN - nothing written. Re-run with --apply.")
            return 0

        now = datetime.now(timezone.utc).isoformat()
        for row, themes in planned:
            conn.execute(
                """
                INSERT INTO editorial_story_revisions (
                    story_id, revised_at, old_tier, new_tier,
                    old_companies_json, new_companies_json, reason, revision_source,
                    old_themes_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    row["id"], now,
                    # Unchanged by this backfill, written as-is so the
                    # row is a complete record rather than a misleading
                    # one.
                    row["materiality_tier"], row["materiality_tier"],
                    row["matched_companies_json"], row["matched_companies_json"],
                    BACKFILL_REASON, BACKFILL_SOURCE,
                    row["matched_themes_json"],
                ),
            )
            conn.execute(
                "UPDATE editorial_stories SET matched_themes_json = %s WHERE id = %s",
                (json.dumps(list(themes)), row["id"]),
            )
        # Explicit, never implicit: a previous repair in this repository
        # relied on a context manager and silently wrote nothing.
        conn.commit()
        print(f"committed: {len(planned)} rows")

    # READ-BACK on a fresh connection. This, not the line above, is the
    # evidence that the backfill happened.
    print("\n--- read-back (fresh connection) ---")
    changed_ids = [row["id"] for row, _ in planned]
    with psycopg.connect(dsn, row_factory=dict_row) as verify:
        read = verify.execute(
            "SELECT id, matched_themes_json FROM editorial_stories WHERE id = ANY(%s) ORDER BY id",
            (changed_ids,),
        ).fetchall()
        still_empty = [r["id"] for r in read if not json.loads(r["matched_themes_json"] or "[]")]
        for r in read[:20]:
            print(f"  {json.loads(r['matched_themes_json'])}  {r['id']}")
        revisions = verify.execute(
            "SELECT count(*) n FROM editorial_story_revisions WHERE reason = %s", (BACKFILL_REASON,)
        ).fetchone()["n"]
        print(f"\n  rows read back: {len(read)}   still empty: {len(still_empty)}")
        print(f"  revision rows with reason '{BACKFILL_REASON}': {revisions}")
        if still_empty or len(read) != len(changed_ids):
            print(f"  BACKFILL INCOMPLETE: {still_empty}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
