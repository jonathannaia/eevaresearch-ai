"""Historical editorial Signals audit harness (design/DECISIONS.md,
"Signals editorial lane false-positive audit" follow-up). Read-only:
takes an externally-supplied export of real EditorialStory-shaped
records and recomputes admission/materiality/company-matching against
this repository's REAL, unmodified functions — never a reimplementation,
never a new classifier. Writes a report; never fetches a feed, never
opens a database connection, never calls any repository or store, never
writes to data/cache or any persisted store. No source, worker, or
Render configuration is read or touched.

This module deliberately does not know how to reach production data —
it has no notion of a database URL, a repository, or a live scan. It is
handed a file (JSON array or CSV) that some OTHER, separately-approved
export step already produced, and it never claims otherwise. See
--input's own help text for the exact required shape.

Reuses, unmodified, exactly the same functions the real pipeline calls
(editorial_pipeline.py's own import list):
  - editorial_matching.matched_companies_and_themes() — the fail-closed
    company/theme keyword match.
  - materiality_classification.classify_editorial_story() — the tier
    classifier (post PR #54's consumer-deal-price calibration fix).
  - editorial_admission.assess_admission() — the precision-first
    admission gate (post PR #54's sentence-scoped action-language and
    historical-background-framing fixes).

Why this module also imports editorial_admission's PRIVATE
_admission_attributed_companies() and _combined_text() directly (both
carry a leading underscore, i.e. "internal to that module" by this
codebase's own convention):
  1. The public AdmissionDecision assess_admission() returns carries
     only ONE company name — the FIRST identified subject, embedded in
     its `reason` string (e.g. "company_subject:SK Hynix") — never the
     full list.
  2. This audit's whole point requires the full list: it must compare
     EVERY stored company tag against EVERY recomputed genuine subject,
     to tell "every stored tag is still valid" apart from "only some of
     them are" (see REMOVE_INVALID_COMPANY_TAGS below, and the LS Cable
     regression fixture, where SK Hynix stays a genuine subject and
     Samsung Electronics does not).
  3. This import is intentional, read-only, and entirely internal to
     this repository — no external package, no network call, no write.
     It calls the real functions exactly as production does; it never
     reimplements or approximates their logic, which is what keeps this
     audit's company-validity check byte-for-byte identical to
     production behavior.
  4. If editorial_admission.py ever grows a PUBLIC "all identified
     subjects" API, this harness should migrate to it and drop the
     private import — that would be a strict improvement, not a
     required one.
  5. Because this harness depends on these two functions' exact
     internals (not just their public contract), any future change to
     either one — a rename, a signature change, a behavioral change —
     must also update this harness and its regression tests in
     tests/test_daily_news_historical_signals_audit.py. They are not
     independently free to drift.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

# _combined_text/_admission_attributed_companies: intentional private
# imports — see this module's own docstring ("Why this module also
# imports editorial_admission's PRIVATE...") for the full rationale.
# Short version: assess_admission()'s public AdmissionDecision only
# reports the first identified company; this audit needs the complete
# list to validate every stored company tag, not just one. Read-only,
# internal to this repo. A future change to either function requires
# updating this harness and tests/test_daily_news_historical_signals_audit.py too.
from src.data_access.daily_news.editorial_admission import (
    _combined_text,
    _admission_attributed_companies,
    assess_admission,
)
from src.data_access.daily_news.editorial_matching import matched_companies_and_themes
from src.data_access.daily_news.materiality_classification import classify_editorial_story
from src.data_access.daily_news.source_registry import (
    EDITORIAL_SOURCE_REGISTRY,
    GATED_JP_KR_SOURCE_REGISTRY,
    GATED_MARKET_NEWS_SOURCE_REGISTRY,
    SourceCategory,
)
from src.models.daily_news_models import NewsMaterialityTier

# Every real, currently-known source_feed_id this app can fetch from —
# the ONLY source of truth for source_id -> SourceCategory (never
# guessed, never a hardcoded per-publisher mapping duplicated here).
_ALL_SOURCE_ENTRIES = EDITORIAL_SOURCE_REGISTRY + GATED_MARKET_NEWS_SOURCE_REGISTRY + GATED_JP_KR_SOURCE_REGISTRY
_SOURCE_CATEGORY_BY_ID: dict[str, SourceCategory] = {entry.source_id: entry.category for entry in _ALL_SOURCE_ENTRIES}

_REQUIRED_FIELDS: tuple[str, ...] = ("id", "headline", "source_url", "published_at", "publisher", "source_feed_id")

_TIER_RANK: dict[str, int] = {
    NewsMaterialityTier.BACKGROUND.value: 0,
    NewsMaterialityTier.WATCHLIST.value: 1,
    NewsMaterialityTier.HIGH_SIGNAL.value: 2,
}

_DISPOSITIONS = (
    "KEEP", "RETIER_WATCHLIST", "RETIER_BACKGROUND", "REMOVE_INVALID_COMPANY_TAGS",
    "SUPPRESS_FROM_USER_FEED", "NEEDS_HUMAN_REVIEW",
)


@dataclass(frozen=True)
class InputRecord:
    """One historical editorial Signal, as supplied by the export this
    tool never produces itself. `excerpt` is the only optional field —
    every other required field's absence makes the record unauditable
    (see _record_from_row)."""

    id: str
    headline: str
    source_url: str
    published_at: str
    publisher: str
    source_feed_id: str
    excerpt: str | None = None
    matched_companies: tuple[str, ...] = ()
    matched_themes: tuple[str, ...] = ()
    materiality_tier: str = ""
    materiality_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class AuditResult:
    id: str
    headline: str
    source_url: str
    published_at: str
    publisher: str
    source_feed_id: str
    stored_tier: str
    recomputed_tier: str
    recomputed_materiality_reasons: tuple[str, ...]
    stored_companies: tuple[str, ...]
    recomputed_matched_companies: tuple[str, ...]  # fail-closed keyword match — the same set editorial_pipeline.py stores
    recomputed_identified_companies: tuple[str, ...]  # genuine subjects only, per editorial_admission.py's own identity check
    invalid_company_tags: tuple[str, ...]  # stored companies that are no longer a genuine subject
    stored_themes: tuple[str, ...]
    recomputed_themes: tuple[str, ...]
    admitted: bool
    admission_reason: str
    disposition: str
    notes: tuple[str, ...] = field(default_factory=tuple)


def _parse_list_field(value: object) -> tuple[str, ...]:
    """Accepts a real JSON list (from JSON input), a JSON-encoded string
    list, a `;`- or `|`-delimited string (common CSV export shapes), or
    a single bare value. Never raises on malformed input — an unparsable
    value degrades to a single-item tuple of its own string form, never
    silently dropped, so a downstream reviewer can see exactly what was
    in the source export."""
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    text = str(value).strip()
    if not text:
        return ()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return tuple(str(v) for v in parsed)
    except (json.JSONDecodeError, TypeError):
        pass
    for delimiter in (";", "|"):
        if delimiter in text:
            return tuple(part.strip() for part in text.split(delimiter) if part.strip())
    return (text,)


def _record_from_row(row: dict) -> InputRecord:
    missing = [f for f in _REQUIRED_FIELDS if not str(row.get(f, "")).strip()]
    if missing:
        raise ValueError(f"record {row.get('id', '<no id>')!r} is missing required field(s): {missing}")
    excerpt = row.get("excerpt")
    return InputRecord(
        id=str(row["id"]),
        headline=str(row["headline"]),
        source_url=str(row["source_url"]),
        published_at=str(row["published_at"]),
        publisher=str(row["publisher"]),
        source_feed_id=str(row["source_feed_id"]),
        excerpt=(str(excerpt) if excerpt not in (None, "") else None),
        matched_companies=_parse_list_field(row.get("matched_companies")),
        matched_themes=_parse_list_field(row.get("matched_themes")),
        materiality_tier=str(row.get("materiality_tier") or ""),
        materiality_reasons=_parse_list_field(row.get("materiality_reasons")),
    )


def load_records(path: Path) -> list[InputRecord]:
    """Format is chosen by file extension: `.json` (a JSON array of
    record objects) or `.csv` (a header row matching InputRecord's own
    field names; list-shaped fields use _parse_list_field's accepted
    shapes). Any other extension is rejected — never guessed."""
    suffix = path.suffix.lower()
    if suffix == ".json":
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise SystemExit(f"{path}: JSON input must be an array of record objects, got {type(raw).__name__}.")
        return [_record_from_row(row) for row in raw]
    if suffix == ".csv":
        with path.open(newline="", encoding="utf-8") as handle:
            return [_record_from_row(row) for row in csv.DictReader(handle)]
    raise SystemExit(f"{path}: unsupported input format {suffix!r} — pass a .json or .csv file.")


def _disposition_for(
    *, admitted: bool, invalid_tags: tuple[str, ...], stored_tier: str, recomputed_tier: str,
    stored_tier_valid: bool, source_category_known: bool, headline_present: bool,
) -> str:
    if not headline_present or not source_category_known or not stored_tier_valid:
        return "NEEDS_HUMAN_REVIEW"
    if not admitted:
        return "SUPPRESS_FROM_USER_FEED"
    if invalid_tags:
        return "REMOVE_INVALID_COMPANY_TAGS"
    stored_rank = _TIER_RANK[stored_tier]
    recomputed_rank = _TIER_RANK[recomputed_tier]
    if recomputed_rank < stored_rank:
        return "RETIER_WATCHLIST" if recomputed_tier == NewsMaterialityTier.WATCHLIST.value else "RETIER_BACKGROUND"
    return "KEEP"


def audit_record(record: InputRecord) -> AuditResult:
    """Pure recomputation over one already-loaded record — no I/O. See
    this module's own docstring for exactly which real, unmodified
    functions are reused and why _admission_attributed_companies is
    imported directly."""
    notes: list[str] = []

    source_category = _SOURCE_CATEGORY_BY_ID.get(record.source_feed_id)
    source_category_known = source_category is not None
    if not source_category_known:
        notes.append(f"unknown_source_feed_id:{record.source_feed_id}")
    # A conservative, documented default (the most common editorial
    # category) only for the recomputation itself — never silently
    # treated as a confident result; source_category_known=False always
    # forces NEEDS_HUMAN_REVIEW below regardless of what this produces.
    effective_category = source_category or SourceCategory.INDEPENDENT_NEWS

    headline_present = bool(record.headline.strip())

    recomputed_companies, recomputed_themes = matched_companies_and_themes(record.headline, record.excerpt)
    recomputed_tier, recomputed_reasons = classify_editorial_story(record.headline, record.excerpt, effective_category)
    admission = assess_admission(record.headline, record.excerpt, recomputed_companies, recomputed_themes, recomputed_reasons)

    text = _combined_text(record.headline, record.excerpt)
    identified_companies = tuple(_admission_attributed_companies(text, record.headline, recomputed_companies))

    invalid_tags = tuple(c for c in record.matched_companies if c not in identified_companies)
    newly_unmatched = tuple(c for c in record.matched_companies if c not in recomputed_companies)
    if newly_unmatched:
        notes.append(f"no_longer_keyword_matched:{','.join(newly_unmatched)}")

    stored_tier_valid = record.materiality_tier in _TIER_RANK
    if record.materiality_tier and not stored_tier_valid:
        notes.append(f"unparseable_stored_tier:{record.materiality_tier}")

    stored_theme_set = set(record.matched_themes)
    recomputed_theme_set = set(recomputed_themes)
    if stored_theme_set != recomputed_theme_set:
        notes.append(f"theme_tags_changed:stored={sorted(stored_theme_set)}|recomputed={sorted(recomputed_theme_set)}")

    disposition = _disposition_for(
        admitted=admission.admitted, invalid_tags=invalid_tags,
        stored_tier=record.materiality_tier, recomputed_tier=recomputed_tier.value,
        stored_tier_valid=stored_tier_valid, source_category_known=source_category_known,
        headline_present=headline_present,
    )

    return AuditResult(
        id=record.id, headline=record.headline, source_url=record.source_url, published_at=record.published_at,
        publisher=record.publisher, source_feed_id=record.source_feed_id,
        stored_tier=record.materiality_tier, recomputed_tier=recomputed_tier.value,
        recomputed_materiality_reasons=recomputed_reasons,
        stored_companies=record.matched_companies, recomputed_matched_companies=recomputed_companies,
        recomputed_identified_companies=identified_companies, invalid_company_tags=invalid_tags,
        stored_themes=record.matched_themes, recomputed_themes=recomputed_themes,
        admitted=admission.admitted, admission_reason=admission.reason,
        disposition=disposition, notes=tuple(notes),
    )


def audit_records(records: list[InputRecord]) -> list[AuditResult]:
    return [audit_record(r) for r in records]


def write_json_report(results: list[AuditResult], path: Path) -> None:
    path.write_text(json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8")


def write_csv_report(results: list[AuditResult], path: Path) -> None:
    if not results:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(asdict(results[0]).keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            row = asdict(result)
            for key, value in row.items():
                if isinstance(value, tuple):
                    row[key] = json.dumps(list(value))
            writer.writerow(row)


def write_report(results: list[AuditResult], path: Path) -> None:
    suffix = path.suffix.lower()
    if suffix == ".json":
        write_json_report(results, path)
    elif suffix == ".csv":
        write_csv_report(results, path)
    else:
        raise SystemExit(f"{path}: unsupported output format {suffix!r} — pass a .json or .csv path.")


def _counts_table(title: str, counter: Counter) -> str:
    lines = [f"### {title}", "", "| Value | Count |", "|---|---|"]
    for value, count in counter.most_common():
        lines.append(f"| {value or '(empty)'} | {count} |")
    return "\n".join(lines) + "\n"


def build_markdown_summary(results: list[AuditResult]) -> str:
    lines = [
        "# Historical Editorial Signals Audit", "",
        f"Records audited: **{len(results)}**", "",
        _counts_table("Current (stored) tier", Counter(r.stored_tier or "(missing)" for r in results)),
        _counts_table("Recomputed tier", Counter(r.recomputed_tier for r in results)),
        _counts_table("Recomputed admission reason", Counter(r.admission_reason for r in results)),
        _counts_table("Source (publisher)", Counter(r.publisher for r in results)),
        _counts_table("Source feed ID", Counter(r.source_feed_id for r in results)),
        _counts_table("Recommended disposition", Counter(r.disposition for r in results)),
    ]

    no_longer_high_signal = [
        r for r in results
        if r.stored_tier == NewsMaterialityTier.HIGH_SIGNAL.value
        and (r.recomputed_tier != NewsMaterialityTier.HIGH_SIGNAL.value or not r.admitted)
    ]
    lines.append("## Existing High Signal cards that would no longer qualify")
    lines.append("")
    if not no_longer_high_signal:
        lines.append("None — every currently High Signal record in this export still qualifies under current rules.")
    else:
        lines.append(f"{len(no_longer_high_signal)} of {sum(1 for r in results if r.stored_tier == NewsMaterialityTier.HIGH_SIGNAL.value)} currently High Signal records:")
        lines.append("")
        lines.append("| ID | Headline | Recomputed tier | Admitted | Reason | Disposition |")
        lines.append("|---|---|---|---|---|---|")
        for r in no_longer_high_signal:
            lines.append(
                f"| {r.id} | {r.headline} | {r.recomputed_tier} | {r.admitted} | {r.admission_reason} | {r.disposition} |"
            )
    lines.append("")

    high_signal_source_share = Counter()
    total_by_source = Counter()
    for r in results:
        total_by_source[r.publisher] += 1
        if r.stored_tier == NewsMaterialityTier.HIGH_SIGNAL.value:
            high_signal_source_share[r.publisher] += 1
    lines.append("## Sources with a high proportion of stored High Signal records")
    lines.append("")
    lines.append("| Source | High Signal | Total | Share |")
    lines.append("|---|---|---|---|")
    for publisher, total in total_by_source.most_common():
        high = high_signal_source_share.get(publisher, 0)
        share = f"{(high / total * 100):.0f}%" if total else "0%"
        lines.append(f"| {publisher} | {high} | {total} | {share} |")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=(
        "Read-only historical editorial Signals audit. Input: a .json array or .csv file of "
        "already-exported records with at least id, headline, source_url, published_at, publisher, "
        "source_feed_id (and, when available, excerpt, matched_companies, matched_themes, "
        "materiality_tier, materiality_reasons). Produces zero network calls, zero writes to any "
        "cache/store/database, and never modifies its input."
    ))
    parser.add_argument("--input", required=True, type=Path, help="Path to a .json or .csv export of real editorial Signal records.")
    parser.add_argument("--output", required=True, type=Path, help="Machine-readable audit report path (.json or .csv).")
    parser.add_argument("--summary", required=True, type=Path, help="Markdown summary report path.")
    args = parser.parse_args()

    records = load_records(args.input)
    results = audit_records(records)
    write_report(results, args.output)
    args.summary.write_text(build_markdown_summary(results), encoding="utf-8")

    print(f"Audited {len(results)} record(s) from {args.input}")
    print(f"Machine-readable report: {args.output}")
    print(f"Markdown summary: {args.summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
