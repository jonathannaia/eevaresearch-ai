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
credential. Dashboard/Signals quality fix (design/
DASHBOARD_SIGNAL_QUALITY_FIX_DESIGN.md): now passes the same shared,
source-neutral DeepL translation provider EDGAR/DART/EDINET's own
pilots already use (src.data_access.translation), used only to compare
a candidate's already-translated title against an existing story when
deciding whether it is a cross-language localized duplicate of an
already-published one (see daily_news_pipeline.run_discovery's own
`translation_provider` parameter) — never for any other purpose here,
and never a new/separate credential (the same EDGE_TRANSLATION_API_KEY
every other translation call site already reads).

Editorial Daily News v1 (design/DECISIONS.md): an explicit
--editorial-only flag, reusing this exact same command, runs
editorial_pipeline.run_editorial_discovery() instead — a completely
separate pipeline/store from the issuer discovery above. Default
behavior (no flag) is byte-for-byte unchanged: still issuer-only, same
as before this flag existed. The two pipelines are never both run in
one invocation, by design — keeps each run's own report simple and
keeps this narrow addition from touching the issuer branch at all.
"""
from __future__ import annotations

import sys

from src.config.settings import get_settings
from src.data_access.daily_news import daily_news_backend, daily_news_pipeline, editorial_pipeline
from src.data_access.translation.deepl_provider import DeepLProvider


def _run_issuer_discovery() -> int:
    settings = get_settings()
    # Daily News durability workstream: storage only — this remains the
    # exact same one-shot manual trigger; which backend it reads/writes
    # against now follows EDGE_DB_BACKEND like every other repository in
    # this app, instead of being hardcoded to the JSON file.
    repository = daily_news_backend.get_daily_news_repository(settings)
    # Dashboard/Signals quality fix (design/
    # DASHBOARD_SIGNAL_QUALITY_FIX_DESIGN.md) — see this module's own
    # docstring for exactly what this provider is used for. Never raises
    # if settings.translation_api_key is unset: DeepLProvider's own
    # constructor never validates the key, and a real translate attempt
    # then fails closed (caught, treated as "insufficient confidence,
    # keep both items") — same discipline as every other optional
    # translation call site in this app.
    report = daily_news_pipeline.run_discovery(
        settings.cache_dir, daily_news_repository=repository,
        translation_provider=DeepLProvider(settings.translation_api_key),
    )

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


def _run_editorial_discovery() -> int:
    settings = get_settings()
    repository = daily_news_backend.get_editorial_story_repository(settings)
    report = editorial_pipeline.run_editorial_discovery(settings.cache_dir, editorial_repository=repository)

    print(f"Editorial Daily News discovery — {report.scan_id}")
    print(f"  sources polled:        {report.sources_polled}")
    print(f"  items fetched:         {report.items_fetched}")
    print(f"  stories published:     {report.stories_published}")
    print(f"  no valid URL:          {report.items_no_valid_url}")
    print(f"  stale (>72h):          {report.items_stale}")
    print(f"  no company/theme match:{report.items_no_match}")
    print(f"  duplicate:             {report.items_duplicate}")
    print(f"  capped (>5 per feed):  {report.items_capped}")
    print(f"  already seen:          {report.items_already_seen}")
    if report.source_failures:
        print("  source failures:")
        for source_id, failure_code in report.source_failures.items():
            print(f"    {source_id}: {failure_code}")

    return 0


def main() -> int:
    if "--editorial-only" in sys.argv[1:]:
        return _run_editorial_discovery()
    return _run_issuer_discovery()


if __name__ == "__main__":
    raise SystemExit(main())
