"""Bounded OpenDART disclosure scan + candidate-rule evaluation for the
Korea radar pilot (Samsung Electronics, SK Hynix; Memory + AI Buildout
themes only). Orchestrates DartClient + dart_rules against the verified
tracked-company cohort — never scans unbounded history, never promotes a
filing to a candidate signal on weak evidence, and is fully idempotent
across repeated scans (deduplicated by DART's own receipt number, cached
to disk outside Git and outside data/edge_research.db).

No Scan button, no Radar Inbox UI, and no scheduling live here yet — this
module is the orchestration layer a later milestone's UI calls into.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.config.tracked_companies import TrackedCompany
from src.data_access.dart import dart_rules
from src.data_access.dart.client import DartClient, DisclosureRecord
from src.data_access.dart.errors import DartError, DartRateLimitError, DartTimeoutError
from src.models.models import CandidateSignal, CandidateStatus, FilingEvent, StateTransition, build_flag_reason

DEFAULT_LOOKBACK_DAYS = 30
# A hard ceiling so a UI lookback input can never accidentally trigger an
# unbounded historical scan — every caller is clamped through here, not
# just validated after the fact.
MAX_LOOKBACK_DAYS = 90
# Bounded pagination safety valve — at 100 records/page this allows up to
# 500 disclosures per company per scan. Grounded in a real observed pull
# (SK Hynix: 58 total disclosures over a live 90-day window; Samsung's
# higher volume is dominated by routine per-insider filings the rule
# engine already excludes), not an arbitrary guess.
MAX_PAGES_PER_COMPANY = 5
PAGE_SIZE = 100
_MAX_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 1.5

_CACHE_FILENAME = "dart_filing_events.json"


def clamp_lookback_days(lookback_days: int) -> int:
    """Never returns more than MAX_LOOKBACK_DAYS regardless of input —
    the one place a UI lookback selector's value must pass through."""
    return max(1, min(lookback_days, MAX_LOOKBACK_DAYS))


def date_window(lookback_days: int, today: datetime | None = None) -> tuple[str, str]:
    lookback_days = clamp_lookback_days(lookback_days)
    end = (today or datetime.now(timezone.utc)).date()
    begin = end - timedelta(days=lookback_days)
    return begin.strftime("%Y%m%d"), end.strftime("%Y%m%d")


@dataclass(frozen=True)
class ScanScope:
    bgn_de: str
    end_de: str
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
    # One entry per company that failed — the scan continues for the
    # remaining companies rather than aborting entirely.
    errors: tuple[str, ...]
    # Companies whose search legitimately returned zero disclosures this
    # window (DART status "013" / an empty first page) — an expected,
    # non-error outcome, tracked separately from `errors` so a caller can
    # report "no data" distinctly from a real failure.
    no_data_companies: tuple[str, ...] = ()


def _cache_path(cache_dir: Path) -> Path:
    return cache_dir / _CACHE_FILENAME


def _empty_cache() -> dict:
    return {"seen_receipt_numbers": [], "filing_events": [], "candidate_signals": []}


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


def load_seen_receipt_numbers(cache_dir: Path) -> set[str]:
    return set(_load_cache(cache_dir)["seen_receipt_numbers"])


def load_filing_events(cache_dir: Path) -> tuple[FilingEvent, ...]:
    """Every filing event ever scanned (not just ones promoted to a
    CandidateSignal) — read-only accessor over the cache scan() already
    writes to. Individually corrupt entries are skipped rather than
    failing the whole read, same tolerance as candidate_store.load_candidates."""
    raw_events = _load_cache(cache_dir)["filing_events"]
    events: list[FilingEvent] = []
    for data in raw_events:
        try:
            events.append(FilingEvent(**data))
        except TypeError:
            continue
    return tuple(events)


def _search_with_retry(
    client: DartClient, corp_code: str, bgn_de: str, end_de: str, page_no: int,
) -> tuple[list[DisclosureRecord], int]:
    """DartClient makes exactly one request per call (see its own
    docstring) — retry/backoff for transient failures (rate limit,
    timeout) lives here, one layer up, bounded to _MAX_RETRIES so a
    struggling connection can't retry forever."""
    attempt = 0
    while True:
        try:
            return client.search_disclosures(corp_code, bgn_de, end_de, page_no=page_no, page_count=PAGE_SIZE)
        except (DartRateLimitError, DartTimeoutError):
            attempt += 1
            if attempt > _MAX_RETRIES:
                raise
            time.sleep(_RETRY_BACKOFF_SECONDS * attempt)


def _filing_event_from_record(record: DisclosureRecord, company: TrackedCompany, retrieved_at: str) -> FilingEvent:
    return FilingEvent(
        rcept_no=record.rcept_no,
        corp_code=record.corp_code or (company.corp_code or ""),
        corp_name=record.corp_name,
        stock_code=record.stock_code or company.krx_code,
        report_nm=record.report_nm,
        rcept_dt=record.rcept_dt,
        flr_nm=record.flr_nm,
        theme_slug=company.themes[0] if company.themes else "",
        subtheme_slug=company.subthemes[0] if company.subthemes else None,
        source_url=DartClient.viewer_url(record.rcept_no),
        retrieved_at=retrieved_at,
    )


def _candidate_signal_from_evaluation(filing: FilingEvent, evaluation: dart_rules.RuleEvaluation) -> CandidateSignal:
    # Rule-based promotion always lands as CANDIDATE_DETECTED here — the
    # first stage in the milestone-4 orchestration lifecycle
    # (radar_pipeline.py). Document retrieval/extraction/translation are
    # separate later steps this function knows nothing about.
    now = datetime.now(timezone.utc).isoformat()
    confidence = evaluation.confidence or "Low"
    return CandidateSignal(
        id=f"cand-{filing.rcept_no}",
        filing=filing,
        matched_rules=list(evaluation.matched_rules),
        confidence=confidence,
        status=CandidateStatus.CANDIDATE_DETECTED,
        state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at=now)],
        flag_reason=build_flag_reason(evaluation.matched_rules, confidence),
    )


def scan(
    client: DartClient,
    companies: list[TrackedCompany],
    cache_dir: Path,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    max_pages_per_company: int = MAX_PAGES_PER_COMPANY,
) -> ScanResult:
    """One bounded scan across the given companies. Idempotent: filings
    already in the on-disk cache (by rcept_no) are counted in
    `already_seen_count` and never duplicated into `new_filing_events` or
    `new_candidate_signals`. A company missing a resolved corp_code, or
    that raises a non-transient DartError, is recorded in `errors` and
    skipped — the scan continues for the remaining companies rather than
    aborting entirely."""
    lookback_days = clamp_lookback_days(lookback_days)
    bgn_de, end_de = date_window(lookback_days)
    scanned_at = datetime.now(timezone.utc).isoformat()

    cache = _load_cache(cache_dir)
    seen: set[str] = set(cache["seen_receipt_numbers"])

    new_filing_events: list[FilingEvent] = []
    new_candidate_signals: list[CandidateSignal] = []
    already_seen_count = 0
    errors: list[str] = []
    no_data_companies: list[str] = []

    for company in companies:
        if not company.corp_code:
            errors.append(f"{company.name}: corp_code not resolved — run corp_code_resolver first.")
            continue
        try:
            page_no = 1
            while page_no <= max_pages_per_company:
                records, total_count = _search_with_retry(client, company.corp_code, bgn_de, end_de, page_no)
                if page_no == 1 and total_count == 0:
                    no_data_companies.append(company.name)
                for record in records:
                    if record.rcept_no in seen:
                        already_seen_count += 1
                        continue
                    seen.add(record.rcept_no)
                    filing = _filing_event_from_record(record, company, scanned_at)
                    new_filing_events.append(filing)
                    evaluation = dart_rules.evaluate_report_name(record.report_nm)
                    if evaluation.confidence is not None:
                        new_candidate_signals.append(_candidate_signal_from_evaluation(filing, evaluation))
                if not records or page_no * PAGE_SIZE >= total_count:
                    break
                page_no += 1
        except DartError as exc:
            errors.append(f"{company.name}: {exc}")
            continue

    cache["seen_receipt_numbers"] = sorted(seen)
    cache["filing_events"] = cache["filing_events"] + [asdict(f) for f in new_filing_events]
    cache["candidate_signals"] = cache["candidate_signals"] + [asdict(c) for c in new_candidate_signals]
    _save_cache(cache_dir, cache)

    scope = ScanScope(
        bgn_de=bgn_de, end_de=end_de, lookback_days=lookback_days,
        companies=tuple(c.name for c in companies), source="OpenDART / DART", scanned_at=scanned_at,
    )
    return ScanResult(
        scope=scope, new_filing_events=tuple(new_filing_events), new_candidate_signals=tuple(new_candidate_signals),
        already_seen_count=already_seen_count, errors=tuple(errors), no_data_companies=tuple(no_data_companies),
    )
