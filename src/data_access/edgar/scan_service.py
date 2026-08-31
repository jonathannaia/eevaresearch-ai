"""Bounded SEC EDGAR filing scan + candidate-rule evaluation (SEC EDGAR
pilot — NVIDIA, Micron Technology, Coherent Corp, Rockwell Automation,
Rocket Lab; AI Buildout/Memory/Photonics/Humanoids/Space only).
Orchestrates EdgarClient + edgar_rules against the verified tracked-
company cohort — never scans unbounded history, never promotes a filing
to a candidate signal on weak evidence, and is fully idempotent across
repeated scans.

Deduplicated by (source, normalized CIK, canonical dashed accession
number) — never by ticker, name, date, or form type alone, per the
milestone-8 brief's explicit instruction. Uses only `filings.recent`
(the submissions endpoint's most-recent-filings block) — never the
older paginated filing-history files EDGAR's submissions response can
reference, keeping this a bounded lookback rather than a bulk
filing-history download.
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from src.config.tracked_companies import TrackedCompany
from src.data_access.edgar import edgar_rules
from src.data_access.edgar.client import EdgarClient
from src.data_access.edgar.errors import EdgarError, EdgarRateLimitError, EdgarTimeoutError
from src.models.models import CandidateSignal, CandidateStatus, FilingEvent, StateTransition, build_flag_reason

DEFAULT_LOOKBACK_DAYS = 30
MAX_LOOKBACK_DAYS = 90
_BACKOFF_BASE_SECONDS = 1.0
_JITTER_MAX_SECONDS = 0.5
_MAX_RETRIES = 2
_SOURCE_LABEL = "SEC EDGAR"
_CACHE_FILENAME = "edgar_filing_events.json"
_REQUIRED_COLUMNS = ("accessionNumber", "filingDate", "form", "primaryDocument")
# `items` verified live (milestone 8, Gate 3) as a real column in SEC's
# `filings.recent` block — a comma-separated 8-K item-number string, e.g.
# "1.01,2.03,7.01". Optional like primaryDocDescription: missing/
# misaligned defaults to "" per row rather than failing the whole parse.
_OPTIONAL_COLUMNS = ("primaryDocDescription", "items")


def clamp_lookback_days(lookback_days: int) -> int:
    return max(1, min(lookback_days, MAX_LOOKBACK_DAYS))


def date_window(lookback_days: int, today: datetime | None = None) -> tuple[date, date]:
    lookback_days = clamp_lookback_days(lookback_days)
    end = (today or datetime.now(timezone.utc)).date()
    begin = end - timedelta(days=lookback_days)
    return begin, end


@dataclass(frozen=True)
class ScanScope:
    bgn_date: str
    end_date: str
    lookback_days: int
    companies: tuple[str, ...]
    source: str
    scanned_at: str


@dataclass(frozen=True)
class ScanResult:
    scope: ScanScope
    new_filing_events: tuple[FilingEvent, ...]
    new_candidate_signals: tuple[CandidateSignal, ...]
    already_seen_count: int
    errors: tuple[str, ...]
    no_data_companies: tuple[str, ...] = ()


def normalize_recent_filings(recent: object) -> tuple[list[dict], tuple[str, ...]]:
    """Zips SEC's COLUMNAR filings.recent arrays (parallel lists indexed
    by position — NOT an array of filing objects, a real, verified
    EDGAR API shape) into row dicts, validating that every required
    column is present and all columns have matching lengths. Never
    raises and never guesses which entries line up — a malformed or
    misaligned block returns no rows plus a clear warning instead."""
    warnings: list[str] = []
    if not isinstance(recent, dict):
        return [], ("filings.recent was not a JSON object.",)

    columns: dict[str, list] = {}
    for col in _REQUIRED_COLUMNS:
        value = recent.get(col)
        if not isinstance(value, list):
            warnings.append(f"filings.recent.{col} is missing or not a list.")
    if warnings:
        return [], tuple(warnings)
    for col in _REQUIRED_COLUMNS:
        columns[col] = recent[col]

    lengths = {len(v) for v in columns.values()}
    if len(lengths) > 1:
        detail = ", ".join(f"{k}={len(v)}" for k, v in columns.items())
        return [], (f"filings.recent columns have mismatched lengths: {detail}.",)

    count = lengths.pop() if lengths else 0
    optional: dict[str, list] = {}
    for col in _OPTIONAL_COLUMNS:
        value = recent.get(col)
        optional[col] = value if isinstance(value, list) and len(value) == count else [""] * count

    rows: list[dict] = []
    for i in range(count):
        row = {col: columns[col][i] for col in _REQUIRED_COLUMNS}
        for col in _OPTIONAL_COLUMNS:
            row[col] = optional[col][i]
        rows.append(row)
    return rows, ()


def _cache_path(cache_dir: Path) -> Path:
    return cache_dir / _CACHE_FILENAME


def _empty_cache() -> dict:
    return {"seen_keys": [], "filing_events": [], "candidate_signals": []}


def _load_cache(cache_dir: Path) -> dict:
    path = _cache_path(cache_dir)
    if not path.exists():
        return _empty_cache()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_cache()
    if not isinstance(raw, dict):
        return _empty_cache()
    for key, default in _empty_cache().items():
        raw.setdefault(key, default)
    return raw


def _save_cache(cache_dir: Path, cache: dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    _cache_path(cache_dir).write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def load_seen_dedup_keys(cache_dir: Path) -> set[str]:
    return set(_load_cache(cache_dir)["seen_keys"])


def load_filing_events(cache_dir: Path) -> tuple[FilingEvent, ...]:
    events: list[FilingEvent] = []
    for data in _load_cache(cache_dir)["filing_events"]:
        try:
            events.append(FilingEvent(**data))
        except TypeError:
            continue
    return tuple(events)


def dedup_key(cik: str, accession_no: str) -> str:
    """source + normalized CIK + canonical dashed accession number —
    never ticker, name, date, or form type, per the milestone-8 brief."""
    return f"{_SOURCE_LABEL}:{cik}:{accession_no}"


def _get_submissions_with_retry(client: EdgarClient, cik: str) -> dict:
    """EdgarClient makes exactly one request per call — bounded
    exponential backoff WITH JITTER lives here, capped at _MAX_RETRIES."""
    attempt = 0
    while True:
        try:
            return client.get_submissions(cik)
        except (EdgarRateLimitError, EdgarTimeoutError):
            attempt += 1
            if attempt > _MAX_RETRIES:
                raise
            time.sleep(_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, _JITTER_MAX_SECONDS))


def _filing_event_from_row(row: dict, company: TrackedCompany, retrieved_at: str) -> FilingEvent:
    accession_no = row["accessionNumber"]
    cik = company.corp_code or ""
    return FilingEvent(
        rcept_no=accession_no,
        corp_code=cik,
        corp_name=company.name,
        stock_code=company.krx_code,
        report_nm=row.get("primaryDocDescription") or row.get("form", ""),
        rcept_dt=row.get("filingDate", ""),
        flr_nm=company.name,
        pblntf_ty=row.get("form", ""),
        theme_slug=company.themes[0] if company.themes else "",
        subtheme_slug=company.subthemes[0] if company.subthemes else None,
        source_url=EdgarClient.filing_index_url(cik, accession_no) if cik else "",
        retrieved_at=retrieved_at,
        source_name=_SOURCE_LABEL,
        original_language="English",
        primary_document=row.get("primaryDocument", ""),
    )


def _evaluate_row(row: dict) -> edgar_rules.RuleEvaluation:
    """Scan-time classification for one normalized filing row. For 8-K
    filings, uses the real `items` column (verified live, milestone 8
    Gate 3 — see edgar_rules.py's module docstring) when present and
    well-formed, via edgar_rules.refine_8k_evaluation — the full
    category+confidence immediately, no document fetch needed. Falls
    back to the coarse evaluate_form_type() classification when `items`
    is absent/malformed, exactly as before this fix. Every other form
    type is unaffected — evaluate_form_type() already gives a complete
    signal for those."""
    form = row.get("form", "")
    if form.strip().upper() == "8-K":
        item_numbers = edgar_rules.parse_items_metadata(row.get("items", ""))
        if item_numbers:
            return edgar_rules.refine_8k_evaluation(item_numbers)
    return edgar_rules.evaluate_form_type(form)


def _candidate_signal_from_evaluation(filing: FilingEvent, evaluation: edgar_rules.RuleEvaluation) -> CandidateSignal:
    now = datetime.now(timezone.utc).isoformat()
    confidence = evaluation.confidence or "Low"
    return CandidateSignal(
        id=f"edgar-cand-{filing.rcept_no}",
        filing=filing,
        matched_rules=list(evaluation.matched_rules),
        confidence=confidence,
        status=CandidateStatus.CANDIDATE_DETECTED,
        state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at=now)],
        flag_reason=build_flag_reason(evaluation.matched_rules, confidence),
    )


def scan(
    client: EdgarClient,
    companies: list[TrackedCompany],
    cache_dir: Path,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> ScanResult:
    """One bounded scan across the given companies, using only each
    company's `filings.recent` block (never older paginated history —
    see module docstring). Idempotent: filings already in the on-disk
    cache (by dedup_key) are counted in `already_seen_count` and never
    duplicated. A company missing a resolved CIK, or that raises a
    non-transient EdgarError, is recorded in `errors` and skipped — the
    scan continues for the remaining companies rather than aborting."""
    lookback_days = clamp_lookback_days(lookback_days)
    begin, end = date_window(lookback_days)
    scanned_at = datetime.now(timezone.utc).isoformat()

    cache = _load_cache(cache_dir)
    seen: set[str] = set(cache["seen_keys"])

    new_filing_events: list[FilingEvent] = []
    new_candidate_signals: list[CandidateSignal] = []
    already_seen_count = 0
    errors: list[str] = []
    no_data_companies: list[str] = []

    for company in companies:
        if not company.corp_code:
            errors.append(f"{company.name}: CIK not resolved — run cik_resolver first.")
            continue
        try:
            payload = _get_submissions_with_retry(client, company.corp_code)
        except EdgarError as exc:
            errors.append(f"{company.name}: {exc}")
            continue

        rows, warnings = normalize_recent_filings(payload.get("filings", {}).get("recent", {}))
        for warning in warnings:
            errors.append(f"{company.name}: {warning}")
        if not rows and not warnings:
            no_data_companies.append(company.name)
            continue

        matched_in_window = 0
        for row in rows:
            filing_date_str = row.get("filingDate", "")
            try:
                filing_date = date.fromisoformat(filing_date_str)
            except ValueError:
                continue
            if not (begin <= filing_date <= end):
                continue
            matched_in_window += 1

            key = dedup_key(company.corp_code, row["accessionNumber"])
            if key in seen:
                already_seen_count += 1
                continue
            seen.add(key)

            filing = _filing_event_from_row(row, company, scanned_at)
            new_filing_events.append(filing)
            evaluation = _evaluate_row(row)
            if evaluation.confidence is not None:
                new_candidate_signals.append(_candidate_signal_from_evaluation(filing, evaluation))

        if matched_in_window == 0 and not warnings:
            no_data_companies.append(company.name)

    cache["seen_keys"] = sorted(seen)
    cache["filing_events"] = cache["filing_events"] + [asdict(f) for f in new_filing_events]
    cache["candidate_signals"] = cache["candidate_signals"] + [asdict(c) for c in new_candidate_signals]
    _save_cache(cache_dir, cache)

    scope = ScanScope(
        bgn_date=begin.isoformat(), end_date=end.isoformat(), lookback_days=lookback_days,
        companies=tuple(c.name for c in companies), source=_SOURCE_LABEL, scanned_at=scanned_at,
    )
    return ScanResult(
        scope=scope, new_filing_events=tuple(new_filing_events), new_candidate_signals=tuple(new_candidate_signals),
        already_seen_count=already_seen_count, errors=tuple(errors), no_data_companies=tuple(no_data_companies),
    )
