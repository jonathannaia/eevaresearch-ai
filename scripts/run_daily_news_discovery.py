"""Manual/admin trigger for Daily News Slice 1's discovery pipeline —
NOT an autonomous worker. Invoke as:

    .venv/bin/python -m scripts.run_daily_news_discovery

Runs exactly one bounded discovery pass across the approved pilot feeds
(src/data_access/daily_news/feed_registry.py) and exits — no loop, no
sleep, no scheduling, no master "live enabled" switch, since running
this command IS the manual trigger. A future, separately-approved
Daily News worker would reuse daily_news_pipeline.run_discovery() the
same way this script does, on its own schedule; none exists yet.

Never imports anything from src.data_access.dart/edgar/edinet or
scripts/radar_worker.py. Only prints DailyNewsScanReport's own safe,
already-sanitized fields — never a raw exception, feed content, or
credential (this pipeline uses no credentials at all).
"""
from __future__ import annotations

import sys

from src.config.settings import get_settings
from src.data_access.daily_news import daily_news_pipeline


def main() -> int:
    settings = get_settings()
    report = daily_news_pipeline.run_discovery(settings.cache_dir)

    print(f"Daily News discovery — {report.scan_id}")
    print(f"  sources polled:        {report.sources_polled}")
    print(f"  items discovered:      {report.items_discovered}")
    print(f"  stories published:     {report.stories_published}")
    print(f"  suppressed (no URL):   {report.items_suppressed_no_url}")
    print(f"  deduplicated:          {report.items_deduplicated}")
    if report.source_failures:
        print("  source failures:")
        for company_name, failure_code in report.source_failures.items():
            print(f"    {company_name}: {failure_code}")
    if report.warnings:
        print("  warnings:")
        for warning in report.warnings:
            print(f"    {warning}")
    if report.suppressed_items:
        print("  suppressed items (admin detail):")
        for company_name, title, reason in report.suppressed_items:
            print(f"    [{company_name}] {title!r} — {reason}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
