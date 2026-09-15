"""Gated KR/Light Reading rollout — offline quality-review harness
(design/DECISIONS.md). Read-only, no network, no writes: classifies a
pasted batch of real Daily News story titles/snippets (copied from the
live app by a human reviewer) using the exact same matching/materiality/
admission logic the production pipeline already runs
(editorial_matching.matched_companies_and_themes(),
materiality_classification.classify_editorial_story(),
editorial_admission.assess_admission()) — never a separate/duplicated
gate, and never a live feed fetch. This is deliberately NOT
scripts/daily_news_jp_kr_dry_run.py's job (that one fetches real feeds
and runs the full pipeline against them); this harness classifies
already-observed production output pasted in by hand, for a human
operational review after the gated sources go live.

Classification (three buckets, each reusing an existing enum/decision
so this harness never introduces a new, separate notion of relevance):
  HIGH VALUE  — admitted AND NewsMaterialityTier.HIGH_SIGNAL
  BORDERLINE  — admitted AND (WATCHLIST or BACKGROUND) — "relevant
                research context with low/uncertain immediate
                materiality," per editorial_admission.py's own framing
  IRRELEVANT  — rejected by the fail-closed match gate or the
                admission gate (the "reason" field carries the exact
                same reason string production would have recorded)

Input: a JSON array of objects with `title` (required), `summary`
(optional), `publisher` (optional, echoed back only). Example:
  [{"title": "...", "summary": "...", "publisher": "Business Korea"}]

Usage:
  .venv/bin/python -m scripts.daily_news_quality_review --input samples.json
  echo '[{"title": "..."}]' | .venv/bin/python -m scripts.daily_news_quality_review --stdin

Also prints a rejection-reason histogram, to make systematic noise
patterns (consumer format, off-topic, personnel, etc.) visible at a
glance across a batch — this is a reporting aid only; it proposes
nothing and changes no admission rule itself."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass

from src.data_access.daily_news.editorial_admission import assess_admission
from src.data_access.daily_news.editorial_matching import matched_companies_and_themes
from src.data_access.daily_news.materiality_classification import classify_editorial_story
from src.data_access.daily_news.source_registry import SourceCategory
from src.models.daily_news_models import NewsMaterialityTier

_MATERIALITY_TO_LABEL = {
    NewsMaterialityTier.HIGH_SIGNAL: "HIGH VALUE",
    NewsMaterialityTier.WATCHLIST: "BORDERLINE",
    NewsMaterialityTier.BACKGROUND: "BORDERLINE",
}


@dataclass(frozen=True)
class QualityReviewResult:
    title: str
    publisher: str | None
    label: str  # "HIGH VALUE" | "BORDERLINE" | "IRRELEVANT"
    reason: str


def classify_item(title: str, summary: str | None, publisher: str | None = None) -> QualityReviewResult:
    """Pure classification of one pasted item — no I/O. Always treats the
    source as SourceCategory.INDEPENDENT_NEWS: every KR/Light Reading
    gated source is INDEPENDENT_NEWS (see source_registry.py), and this
    harness reviews already-published output from exactly those
    sources, never government/regulator/exchange feeds."""
    matched_companies, matched_themes = matched_companies_and_themes(title, summary)
    if not matched_companies and not matched_themes:
        return QualityReviewResult(title, publisher, "IRRELEVANT", "no_qualifying_company_or_theme_match")

    materiality_tier, materiality_reasons = classify_editorial_story(title, summary, SourceCategory.INDEPENDENT_NEWS)
    admission = assess_admission(title, summary, matched_companies, matched_themes, materiality_reasons)
    if not admission.admitted:
        return QualityReviewResult(title, publisher, "IRRELEVANT", admission.reason)

    return QualityReviewResult(title, publisher, _MATERIALITY_TO_LABEL[materiality_tier], admission.reason)


def _load_items(raw: str) -> list[dict]:
    items = json.loads(raw)
    if not isinstance(items, list):
        raise SystemExit("Input must be a JSON array of {title, summary?, publisher?} objects.")
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--input", metavar="PATH", help="Path to a JSON file of pasted items.")
    source_group.add_argument("--stdin", action="store_true", help="Read the JSON array from stdin.")
    args = parser.parse_args()

    raw = sys.stdin.read() if args.stdin else open(args.input, encoding="utf-8").read()
    items = _load_items(raw)

    results = [
        classify_item(item.get("title", ""), item.get("summary"), item.get("publisher"))
        for item in items
        if item.get("title")
    ]

    print("=" * 72)
    print("Daily News KR/Light Reading — READ-ONLY QUALITY REVIEW")
    print("Classifies pasted production output only; fetches nothing, writes nothing.")
    print("=" * 72)
    print(f"Items reviewed: {len(results)}\n")

    for result in results:
        publisher_suffix = f" ({result.publisher})" if result.publisher else ""
        print(f"[{result.label}] {result.title}{publisher_suffix}")
        print(f"    reason: {result.reason}")

    label_counts = Counter(r.label for r in results)
    print("\nSummary:")
    for label in ("HIGH VALUE", "BORDERLINE", "IRRELEVANT"):
        print(f"  {label}: {label_counts.get(label, 0)}")

    irrelevant_reasons = Counter(r.reason for r in results if r.label == "IRRELEVANT")
    if irrelevant_reasons:
        print("\nIRRELEVANT reason histogram (noise-pattern spotting only — no rule changes applied):")
        for reason, count in irrelevant_reasons.most_common():
            print(f"  {count:3d}  {reason}")

    print("\n" + "=" * 72)
    print("End of review. No feeds fetched, no writes occurred.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
