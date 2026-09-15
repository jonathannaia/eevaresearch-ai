"""Gated Japan/Korea source expansion (design/DECISIONS.md) — controlled,
read-only dry-run harness.

Fetches a real, live batch from GATED_JP_KR_SOURCE_REGISTRY (today:
japan-times-business-rss, businesskorea-industries-rss, businesskorea-
science-tech-rss), runs it through the exact same, unmodified fail-
closed company/theme match, materiality classification, and precision-
first admission gate every other Daily News editorial source already
goes through (run_editorial_discovery(dry_run=True) — no separate/
duplicated gate logic exists anywhere in this script), and prints a
concise summary. Never writes to any real store: dry_run=True skips the
one persistence call inside run_editorial_discovery() itself, and this
script additionally always uses a fresh temporary cache_dir (never
data/cache/, never a real Postgres connection).

Does not read Settings.daily_news_enabled_jp_kr_sources (the production
allow-list) at all — that flag decides what the REAL worker fetches;
this harness is specifically for evaluating a gated source BEFORE
deciding whether to add it to that allow-list, so it always offers
every source in the gated registry (or the one named via --source),
regardless of the flag's current value. Deliberately a separate script
from scripts/daily_news_market_news_dry_run.py (same pattern, different
gated registry) — mirrors that harness's own shape exactly.

STANDALONE, READ-ONLY TOOL. Not imported by app.py, any UI page, or
scripts/daily_news_worker.py. Run as:

    .venv/bin/python -m scripts.daily_news_jp_kr_dry_run
    .venv/bin/python -m scripts.daily_news_jp_kr_dry_run --source businesskorea-science-tech-rss
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from src.data_access.daily_news import editorial_pipeline
from src.data_access.daily_news.source_registry import GATED_JP_KR_SOURCE_REGISTRY


def _select_sources(source_id: str | None):
    if source_id is None:
        return GATED_JP_KR_SOURCE_REGISTRY
    selected = tuple(e for e in GATED_JP_KR_SOURCE_REGISTRY if e.source_id == source_id)
    if not selected:
        known = ", ".join(e.source_id for e in GATED_JP_KR_SOURCE_REGISTRY)
        print(f"Unknown --source {source_id!r}. Known gated source_ids: {known}", file=sys.stderr)
        sys.exit(2)
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source", default=None,
        help="A single gated source_id to dry-run (default: every source in GATED_JP_KR_SOURCE_REGISTRY).",
    )
    args = parser.parse_args()
    sources = _select_sources(args.source)

    print("=" * 72)
    print("Daily News gated Japan/Korea source — READ-ONLY DRY RUN")
    print("Nothing is written to any database or cache file by this run.")
    print("=" * 72)
    print(f"Sources this run: {', '.join(e.source_id for e in sources)}")
    print()

    with tempfile.TemporaryDirectory(prefix="daily-news-jp-kr-dry-run-") as tmp:
        report = editorial_pipeline.run_editorial_discovery(
            Path(tmp), source_entries=sources, dry_run=True,
        )

    print(f"Scan id:        {report.scan_id}")
    print(f"Sources polled:  {report.sources_polled}")
    print(f"Items fetched:   {report.items_fetched}")
    print()
    print("Rejected, by reason:")
    print(f"  invalid/off-domain URL:        {report.items_no_valid_url}")
    print(f"  stale (outside freshness window): {report.items_stale}")
    print(f"  no company/theme match:        {report.items_no_match}")
    print(f"  failed admission gate:         {report.items_not_subject_relevant}")
    print(f"  duplicate within this run:     {report.items_duplicate}")
    print(f"  already seen:                  {report.items_already_seen}")
    print(f"  qualified but capped (>{editorial_pipeline._PER_SOURCE_CAP}/source): {report.items_capped}")
    print()
    print(f"WOULD HAVE BEEN ADMITTED: {report.stories_published}")
    print()

    if report.source_failures:
        print("Source fetch failures:")
        for source_id, failure_code in report.source_failures.items():
            print(f"  {source_id}: {failure_code}")
        print()

    if report.admitted_examples:
        print(f"Example admitted titles (up to {editorial_pipeline._DRY_RUN_SAMPLE_SIZE}):")
        for title in report.admitted_examples:
            print(f"  [ADMITTED] {title}")
        print()

    if report.rejected_examples:
        print(f"Example rejected titles with reasons (up to {editorial_pipeline._DRY_RUN_SAMPLE_SIZE}):")
        for title, reason in report.rejected_examples:
            print(f"  [REJECTED: {reason}] {title}")
        print()

    print("=" * 72)
    print("End of dry run. No writes occurred.")
    print("=" * 72)


if __name__ == "__main__":
    main()
