"""Read-only survey: what a structured DART headline would say.

Walks every OpenDART filing in the store — not just the Signals
lookback window, because the window holds only a handful of
major-decision disclosures and the question here is what the RULES do
across the corpus, not what today happens to contain.

Prints, per filing: the headline the card shows today, the structured
headline the field parser would build, the exact filed figure, and the
raw field values it read. Also reports the yield, which is the number
that decides whether this is worth shipping: how many filings carry a
field table at all, how many are amendments, and how many withheld the
figure that would have made the headline.

Writes nothing.

    .venv/bin/python -m scripts.survey_dart_structured_fields
    .venv/bin/python -m scripts.survey_dart_structured_fields --with-headline
"""
from __future__ import annotations

import argparse
from collections import Counter

from src.config.settings import get_settings
from src.data_access import backend_factory
from src.data_access.daily_news import dart_structured_fields as D

_SOURCE = "OpenDART / DART"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-headline", action="store_true",
                        help="only filings that produce a structured headline")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args(argv)

    settings = get_settings()
    filing_repository = backend_factory.get_filing_event_repository(settings, _SOURCE)
    candidate_repository = backend_factory.get_candidate_repository(settings, _SOURCE)
    candidates_by_key = {
        (c.filing.corp_code, c.filing.rcept_no): c
        for c in candidate_repository.load_candidates().values()
    }

    outcomes: Counter[str] = Counter()
    rows = []
    for filing in filing_repository.load_filing_events():
        candidate = candidates_by_key.get((filing.corp_code, filing.rcept_no))
        excerpt = (candidate.excerpt_original if candidate else None) or ""
        text = " ".join(excerpt.split())
        if not text:
            outcomes["no excerpt stored"] += 1
            continue
        if D.is_amendment_body(text):
            outcomes["amendment — correction table, not read"] += 1
            continue
        template = D.detect_template(text)
        if template is None:
            outcomes["no field table (periodic report etc.)"] += 1
            continue

        facts = D.parse_structured_fields(text)
        company = filing.corp_name or filing.flr_nm or ""
        headline = D.structured_headline(company, facts) if facts else None
        if headline is None:
            outcomes["field table, but every headline figure withheld"] += 1
        else:
            outcomes["structured headline produced"] += 1
        rows.append((filing, facts, headline, text))

    print("OpenDART filings in the store:", sum(outcomes.values()))
    for name, count in outcomes.most_common():
        print(f"  {count:4}  {name}")
    print()

    shown = [r for r in rows if r[2] is not None] if args.with_headline else rows
    if args.limit:
        shown = shown[: args.limit]
    for i, (filing, facts, headline, text) in enumerate(shown, 1):
        print(f"--- [{i}] {filing.corp_name} / {filing.rcept_dt} / {filing.rcept_no}")
        print(f"    today:      {(filing.report_nm or '')!r}")
        print(f"    structured: {headline!r}")
        if facts is not None:
            print(f"    template={facts.template} amount={facts.amount_won} "
                  f"ratio={facts.ratio_percent} withheld={facts.withheld}")
            if facts.amount_won is not None:
                print(f"    exact:      {D.exact_won(facts.amount_won)}")
            print(f"    counterparty_raw={facts.counterparty!r} -> "
                  f"{D.counterparty_for_display(facts.counterparty)}")
            print(f"    detail={facts.detail!r} region={facts.region!r} "
                  f"dates={facts.start_date}..{facts.end_date}")
        print(f"    source: {text[:300]!r}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
