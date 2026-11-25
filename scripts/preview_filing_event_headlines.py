"""Dry run for item 4 — the event headline every filing card would get.

Read-only. Opens the filing stores, runs the same projection the worker
runs each tick, and prints for each filing:

    old        the headline the card shows today (the form name)
    new        the headline filing_event_headline.py derives
    provenance excerpt | native_title | fallback
    grounded   the subsequence check, re-run here independently of the
               module's own internal check, against the raw excerpt
    source     the untouched sentence an extracted headline came from

Nothing is written and no story is persisted. The point is to put the
headline and the text it came from side by side before any of this
ships, so a wrong one is caught by reading rather than by a reader.

    .venv/bin/python -m scripts.preview_filing_event_headlines
    .venv/bin/python -m scripts.preview_filing_event_headlines --only-extracted
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone

from src.config.settings import get_settings
from src.data_access import backend_factory
from src.data_access.daily_news import filing_event_headline, filing_signal_bridge
from src.logic import filing_display


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only-extracted", action="store_true")
    parser.add_argument("--source-chars", type=int, default=400)
    args = parser.parse_args(argv)

    settings = get_settings()
    from scripts.daily_news_worker import _FILING_SIGNAL_SOURCES

    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=settings.filing_signals_lookback_days)
    ).date().isoformat()
    now = datetime.now(timezone.utc).isoformat()

    rows = []
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
            if candidate is None:
                continue
            document = filing_signal_bridge.FilingDocumentContext(
                excerpt=candidate.excerpt_original,
                excerpt_translated=(
                    candidate.excerpt_translation.translated_text
                    if candidate.excerpt_translation is not None else None
                ),
                item_anchor=filing_display.item_anchor_label(candidate.evidence_location),
            )
            story = filing_signal_bridge.build_filing_signal_story(
                filing, matched_rules=tuple(candidate.matched_rules), retrieved_at=now,
                excerpt=document.excerpt, excerpt_translated=document.excerpt_translated,
                item_anchor=document.item_anchor,
            )
            if story is None:
                continue
            provenance = next(
                (r.split(":", 1)[1] for r in story.materiality_reasons
                 if r.startswith("filing_headline:")), "?",
            )
            category = next(
                (r.split(":", 1)[1] for r in story.materiality_reasons
                 if r.startswith("filing_event:")), "",
            )
            raw = " ".join((candidate.excerpt_original or "").split())
            # Re-derived here rather than read off the story, so the
            # printed source sentence is the module's own input and the
            # grounding verdict is computed from the raw excerpt by this
            # script, not taken on the module's word.
            detail = filing_event_headline.edgar_event_headline(
                company=story.matched_companies[0] if story.matched_companies else "",
                category=category, excerpt=document.excerpt,
                form_type=filing.pblntf_ty, item_anchor=document.item_anchor,
                excerpt_is_readable=bool(raw) and filing_display.is_readable_extracted_text(raw),
            ) if filing.source_name == "SEC EDGAR" else None

            body = story.headline
            company = story.matched_companies[0] if story.matched_companies else ""
            if body.startswith(f"{company} — "):
                body = body[len(company) + 3:]
            grounded = (
                filing_event_headline.is_grounded_in(body.rstrip("…"), raw)
                if provenance == "excerpt" else None
            )
            rows.append({
                "source": filing.source_name,
                "form": (filing.pblntf_ty or "").strip() or "-",
                "item": filing_event_headline.normalized_item_anchor(document.item_anchor) or "-",
                "category": category,
                "tier": story.materiality_tier.value if story.materiality_tier else "",
                "old": candidate.title_translated or candidate.title_native or "",
                "new": story.headline,
                "provenance": provenance,
                "grounded": grounded,
                "excerpt_shown": bool(story.excerpt),
                "reason": detail.rejected_because if detail else None,
                "sentence": (detail.source_sentence if detail and detail.source_sentence else raw),
            })

    rows.sort(key=lambda r: (r["provenance"] != "excerpt", r["source"], r["form"], r["new"]))
    if args.only_extracted:
        rows = [r for r in rows if r["provenance"] == "excerpt"]

    counts = Counter(r["provenance"] for r in rows)
    ungrounded = [r for r in rows if r["grounded"] is False]
    print(f"filings projected: {len(rows)}")
    for name, n in sorted(counts.items()):
        print(f"  {name}: {n}")
    print(f"  cards that now show a summary: {sum(1 for r in rows if r['excerpt_shown'])}")
    print(f"  FAILED the grounding re-check: {len(ungrounded)}")
    print()
    for i, r in enumerate(rows, 1):
        mark = {True: "grounded", False: "UNGROUNDED", None: "n/a"}[r["grounded"]]
        print(f"--- [{i}] {r['source']} {r['form']} {r['item']} / {r['category']} / {r['tier']}")
        print(f"    old: {r['old']!r}")
        print(f"    new: {r['new']!r}")
        print(f"    provenance={r['provenance']} grounding={mark} summary={'yes' if r['excerpt_shown'] else 'no'}"
              + (f" rejected={r['reason']}" if r["reason"] else ""))
        print(f"    source: {r['sentence'][: args.source_chars]!r}")
        print()
    return 1 if ungrounded else 0


if __name__ == "__main__":
    raise SystemExit(main())
