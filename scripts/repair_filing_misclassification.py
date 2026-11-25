"""One-off repair: restore filing-derived editorial stories that the
window re-classification pass wrongly ran through the NEWS classifier
on 2026-11-25.

WHAT HAPPENED. editorial_reclassification.plan_reclassification() had
no notion of a filing-derived story and classified every row in the
window with classify_editorial_story(..., INDEPENDENT_NEWS). A filing's
tier comes from filing_signal_tiers.py — an 8-K Item 2.02 is High
Signal because of what that item IS — so the news gates, which have no
concept of a 臨時報告書 or a 10-Q, dropped 12 rows to Background with
"off_taxonomy_no_anchor". The same pass also ran same-story dedupe over
them, and since every SEC 8-K is titled "8-K", the exact-headline route
linked 5 of them as duplicates of unrelated companies' filings — the
identical failure project_filing_signals()'s own docstring records.

The root cause is fixed in editorial_reclassification._is_filing_story.
This script repairs the rows that pass already damaged.

WHERE THE CORRECT VALUES COME FROM. The filing store, which is
authoritative and was never touched: the same gather-and-project the
worker runs each tick rebuilds each story exactly, tier, reasons and
company together. The revision log could not supply them on its own,
because V29 recorded the old tier and old companies but not the old
reasons (V31 adds old_reasons_json so a future repair needs none of
this).

HISTORY IS NEVER EDITED. Each restore writes its OWN append-only
revision row with reason "repair_filing_misclassification", so the log
shows the damage and the fix as two separate events. The 62 rows the
damaging pass wrote stay exactly as they are.

    .venv/bin/python -m scripts.repair_filing_misclassification
    .venv/bin/python -m scripts.repair_filing_misclassification --apply

Dry run by default. --apply commits explicitly and then re-reads every
repaired row on a FRESH connection and prints it: a previous version of
this repair reported success while its transaction silently failed to
commit, so the read-back, not this script's own output, is the evidence
that anything happened.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

from src.config.settings import get_settings
from src.data_access import backend_factory
from src.data_access.daily_news import filing_signal_bridge
from src.data_access.postgres_state_db.connection import connect

REPAIR_REASON = "repair_filing_misclassification"
REPAIR_SOURCE = "filing_repair"


def _damaged_ids(conn) -> list[str]:
    """A filing row is damaged when its reasons no longer carry a
    filing_event marker — the signature of having been through the news
    classifier — or when it was linked as a duplicate of another
    filing."""
    rows = conn.execute(
        """
        SELECT id FROM editorial_stories
        WHERE id LIKE %s
          AND (materiality_reasons NOT LIKE %s OR duplicate_of_story_id IS NOT NULL)
        ORDER BY id
        """,
        (f"{filing_signal_bridge.STORY_ID_PREFIX}:%", "%filing_event%"),
    ).fetchall()
    return [r["id"] for r in rows]


def _rebuild_from_filing_store(settings) -> dict:
    """The worker's own gather-and-project, read-only."""
    from scripts.daily_news_worker import _FILING_SIGNAL_SOURCES

    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=settings.filing_signals_lookback_days)
    ).date().isoformat()
    filings_with_rules = []
    excerpts: dict[str, str] = {}
    for source in _FILING_SIGNAL_SOURCES:
        filing_repository = backend_factory.get_filing_event_repository(settings, source)
        candidate_repository = backend_factory.get_candidate_repository(settings, source)
        candidates_by_key = {
            (c.filing.corp_code, c.filing.rcept_no): c
            for c in candidate_repository.load_candidates().values()
        }
        for filing in filing_repository.load_filing_events():
            if (filing.filed_at or filing.rcept_dt or "") < cutoff:
                continue
            candidate = candidates_by_key.get((filing.corp_code, filing.rcept_no))
            filings_with_rules.append(
                (filing, tuple(candidate.matched_rules) if candidate else ())
            )
            if candidate and candidate.excerpt_original:
                excerpts[filing_signal_bridge.story_id_for(filing)] = candidate.excerpt_original

    stories = filing_signal_bridge.project_filing_signals(
        tuple(filings_with_rules),
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        excerpts_by_story_id=excerpts,
    )
    return {s.id: s for s in stories}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="commit the repair")
    args = parser.parse_args(argv)

    settings = get_settings()
    dsn = settings.state_db_url
    if not dsn or not str(dsn).strip():
        print("EDGE_STATE_DB_URL is not configured. Refusing to run.", file=sys.stderr)
        return 2

    with connect(dsn) as conn:
        damaged = _damaged_ids(conn)
        rebuilt = _rebuild_from_filing_store(settings)
        restorable = [i for i in damaged if i in rebuilt]
        unrecoverable = [i for i in damaged if i not in rebuilt]

        print(f"damaged filing rows: {len(damaged)}")
        print(f"rebuilt from filing store: {len(rebuilt)}")
        print(f"restorable: {len(restorable)}   not rebuildable: {len(unrecoverable)}")
        for story_id in unrecoverable:
            print(f"  NOT REBUILDABLE (left untouched): {story_id}")

        if not args.apply:
            for story_id in restorable:
                current = conn.execute(
                    "SELECT materiality_tier t FROM editorial_stories WHERE id = %s", (story_id,)
                ).fetchone()
                print(f"  {current['t']} -> {rebuilt[story_id].materiality_tier.value}  {story_id}")
            print("DRY RUN - nothing written. Re-run with --apply.")
            return 0

        now = datetime.now(timezone.utc).isoformat()
        for story_id in restorable:
            current = conn.execute(
                """SELECT materiality_tier t, materiality_reasons r, matched_companies_json mc
                   FROM editorial_stories WHERE id = %s""",
                (story_id,),
            ).fetchone()
            story = rebuilt[story_id]
            conn.execute(
                """
                INSERT INTO editorial_story_revisions (
                    story_id, revised_at, old_tier, new_tier,
                    old_companies_json, new_companies_json, reason, revision_source,
                    old_reasons_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    story_id, now, current["t"], story.materiality_tier.value,
                    current["mc"], json.dumps(list(story.matched_companies)),
                    REPAIR_REASON, REPAIR_SOURCE, current["r"],
                ),
            )
            conn.execute(
                """
                UPDATE editorial_stories
                SET materiality_tier = %s, materiality_reasons = %s,
                    matched_companies_json = %s, duplicate_of_story_id = NULL
                WHERE id = %s
                """,
                (
                    story.materiality_tier.value,
                    json.dumps(list(story.materiality_reasons)),
                    json.dumps(list(story.matched_companies)),
                    story_id,
                ),
            )
        # Explicit, never implicit: the previous attempt at this repair
        # relied on a context manager and silently wrote nothing.
        conn.commit()
        print(f"committed: {len(restorable)} rows")

    # READ-BACK on a fresh connection. This, not the line above, is the
    # evidence that the repair happened.
    print("\n--- read-back (fresh connection) ---")
    with psycopg.connect(dsn, row_factory=dict_row) as verify:
        rows = verify.execute(
            """
            SELECT id, materiality_tier, materiality_reasons, matched_companies_json,
                   duplicate_of_story_id
            FROM editorial_stories WHERE id = ANY(%s) ORDER BY id
            """,
            (restorable,),
        ).fetchall()
        for row in rows:
            reasons = json.loads(row["materiality_reasons"] or "[]")
            print(
                f"  {str(row['materiality_tier']):<12} "
                f"dup={row['duplicate_of_story_id'] or '-':<4} "
                f"{reasons[:1]}  {row['id']}"
            )
        still_wrong = [
            r["id"] for r in rows
            if "filing_event" not in (r["materiality_reasons"] or "")
            or r["duplicate_of_story_id"] is not None
        ]
        revisions = verify.execute(
            "SELECT count(*) n FROM editorial_story_revisions WHERE reason = %s", (REPAIR_REASON,)
        ).fetchone()["n"]
        print(f"\n  rows read back: {len(rows)}   still wrong: {len(still_wrong)}")
        print(f"  revision rows with reason '{REPAIR_REASON}': {revisions}")
        if still_wrong:
            print(f"  REPAIR INCOMPLETE: {still_wrong}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
