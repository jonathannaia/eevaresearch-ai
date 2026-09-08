"""Bounded EDINET document-list scan + candidate-rule evaluation (Japan
radar pilot). Mirrors src/data_access/edgar/scan_service.py's shape and
discipline (dedup, idempotency, bounded lookback, never-guess
evaluation), with one real structural difference driven by EDINET's own
API shape (see client.py): the document-list endpoint takes a single
calendar `date`, not a date range — so a scan here makes one bounded
EdinetClient call per day in the lookback window, not one call per
company. Lookback defaults are deliberately smaller than EDGAR's for
exactly that reason: each additional day of lookback is its own live
request once this pilot goes live, so the bound matters more here.

Deduplicated by (source, EDINET code, docID) — EDINET's own real,
distinct per-document identifier — never by title, date, or form/type
code alone.

Gate 5 status — response envelope, filing-date derivation, identifier
matching, status handling, and document-availability flags below were
all corrected against the real live response observed in Gate 4
(2026-08-17, SoftBank Group / E02778 query — see
design/DECISIONS.md's Gate 4 entry for the full field list). No live
call occurs in this gate; every correction here is validated only by
fixtures shaped to match that real, already-observed response. This
module still has zero real callers — no company has been added to
tracked configuration for source "EDINET" (explicitly forbidden through
this gate).
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from src.config.tracked_companies import TrackedCompany
from src.data_access.edinet import edinet_rules
from src.data_access.edinet.client import EdinetClient
from src.data_access.edinet.errors import EdinetError
from src.models.models import CandidateSignal, CandidateStatus, FilingEvent, StateTransition, build_flag_reason

# Deliberately small relative to EDGAR's DEFAULT_LOOKBACK_DAYS=30 — see
# module docstring: each day of lookback here is a separate bounded live
# request once this pilot is live, not a single wider-window call.
DEFAULT_LOOKBACK_DAYS = 5
MAX_LOOKBACK_DAYS = 14

_SOURCE_LABEL = "EDINET"
_CACHE_FILENAME = "edinet_filing_events.json"

# Confirmed live (Gate 4) as always present on a real record.
_REQUIRED_FIELDS = ("docID", "docTypeCode", "ordinanceCode", "formCode", "filerName", "docDescription")
# All confirmed live (Gate 4) as real fields on a real record — none
# required, since a missing/malformed value here should never drop an
# otherwise-valid document from the normalized list; it should only
# affect routing/matching/status decisions made further down in scan().
# `secCode` is confirmed nullable. `issuerEdinetCode`/`subjectEdinetCode`/
# `subsidiaryEdinetCode` are distinct identifier-role fields, preserved
# raw and NOT treated as interchangeable with the top-level `edinetCode`
# — see _filing_event_from_row and scan()'s matcher, both of which use
# ONLY the top-level `edinetCode` this gate (deliberately conservative;
# broadening to the role fields is explicitly deferred to a later,
# live-evidence-backed gate).
_OPTIONAL_FIELDS = (
    "secCode", "edinetCode", "JCN", "submitDateTime", "periodStart", "periodEnd", "opeDateTime",
    "issuerEdinetCode", "subjectEdinetCode", "subsidiaryEdinetCode", "parentDocID",
    "withdrawalStatus", "docInfoEditStatus", "disclosureStatus", "legalStatus",
    "xbrlFlag", "pdfFlag", "attachDocFlag", "englishDocFlag", "csvFlag",
)
# The three status fields whose real documented semantics remain
# unconfirmed (Gate 4's live pull never observed a non-empty value for
# any of them). See _status_fields_are_default's docstring.
_UNCONFIRMED_STATUS_FIELDS = ("withdrawalStatus", "docInfoEditStatus", "disclosureStatus")


def clamp_lookback_days(lookback_days: int) -> int:
    return max(1, min(lookback_days, MAX_LOOKBACK_DAYS))


def date_window(lookback_days: int, today: datetime | None = None) -> tuple[date, date]:
    lookback_days = clamp_lookback_days(lookback_days)
    end = (today or datetime.now(timezone.utc)).date()
    begin = end - timedelta(days=lookback_days)
    return begin, end


def _iter_dates(begin: date, end: date) -> list[date]:
    days = (end - begin).days
    return [begin + timedelta(days=i) for i in range(days + 1)]


@dataclass(frozen=True)
class NormalizedDayResult:
    """One date's fetch+normalize outcome — rows before any tracked-
    company matching, dedup, or cache write. See
    fetch_normalized_rows_for_dates."""

    rows: tuple[dict, ...]
    warnings: tuple[str, ...]


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
    # Rows that matched a tracked company by edinetCode but were held
    # back from FilingEvent creation this run because one of the three
    # unconfirmed status fields carried a non-empty value — see
    # _status_fields_are_default. Additive (Gate 5): a visibility seam,
    # never populated before this gate.
    deferred_status_count: int = 0


def normalize_document_list(payload: object) -> tuple[list[dict], tuple[str, ...]]:
    """Validates and flattens the real `{metadata, results}` envelope
    (confirmed live, Gate 4) into plain row dicts. Never raises — every
    failure mode returns ([], one warning) instead, so a malformed day's
    response is skipped by scan() rather than crashing the whole scan
    (same discipline as edgar_rules.normalize_recent_filings's
    mismatched-column-length case).

    Checked, in order: `payload` is a JSON object; it has a `metadata`
    object; `metadata.status == "200"` (a non-"200" status, when present
    and parseable as a string, is treated as a real API-level failure
    even though the HTTP transport itself succeeded); `results` is a
    list; and, when `metadata.resultset.count` is present and parseable
    as an integer, it matches `len(results)` (a mismatch is treated as a
    real structural surprise, not silently trusted — same "fail closed"
    choice made for the code-list CSV's declared count in Gate 3). An
    empty `results` list with `status == "200"` is a normal, warning-free
    empty result — never treated as an error.

    Each surviving result entry missing a required field (see
    _REQUIRED_FIELDS) is dropped, not defaulted. Optional fields (see
    _OPTIONAL_FIELDS — includes both nullable/absent fields like
    `secCode` and every raw diagnostic field this gate preserves but
    does not yet interpret) default to "" when absent/null, never
    guessed."""
    if not isinstance(payload, dict):
        return [], ("EDINET document-list response was not a JSON object.",)

    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return [], ("EDINET document-list response was missing a `metadata` object.",)

    status = metadata.get("status")
    if status is None or str(status).strip() != "200":
        message = metadata.get("message", "")
        return [], (f"EDINET document-list metadata reported a non-success status ({status!r}): {message!r}",)

    results = payload.get("results")
    if not isinstance(results, list):
        return [], ("EDINET document-list response had no `results` list.",)

    resultset = metadata.get("resultset")
    if isinstance(resultset, dict) and "count" in resultset:
        try:
            declared_count = int(resultset["count"])
        except (TypeError, ValueError):
            return [], (f"EDINET document-list metadata.resultset.count was malformed: {resultset.get('count')!r}",)
        if declared_count != len(results):
            return [], (f"EDINET document-list declared {declared_count} results but {len(results)} were present — refusing to trust a mismatched response.",)

    rows: list[dict] = []
    for entry in results:
        if not isinstance(entry, dict):
            continue
        if not all(field in entry and entry[field] not in (None, "") for field in _REQUIRED_FIELDS):
            continue
        row = {field: entry[field] for field in _REQUIRED_FIELDS}
        for field in _OPTIONAL_FIELDS:
            row[field] = entry.get(field) or ""
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


def dedup_key(edinet_code: str, doc_id: str) -> str:
    """source + EDINET code + docID — EDINET's own native identifiers,
    never title, date, or type code alone."""
    return f"{_SOURCE_LABEL}:{edinet_code}:{doc_id}"


def fetch_normalized_rows_for_dates(client: EdinetClient, dates: tuple[date, ...]) -> dict[str, NormalizedDayResult]:
    """The fetch+normalize step, extracted verbatim from scan()'s own
    per-day loop body so a second, independent caller (the EDINET
    discovery path — src/data_access/edinet/discovery_service.py) can
    reuse the identical one-call-per-date fetch/normalize path without
    duplicating it or inlining a second implementation. scan() itself is
    refactored to call this internally; its own signature, return type,
    and behavior are unchanged (see scan()'s own docstring and this
    module's test suite, which passes unmodified against the refactor).

    One client.get_document_list call per date, in order. A date whose
    request raises EdinetError, or whose response fails
    normalize_document_list's envelope validation, has its message
    recorded in that date's own `warnings` — never raised — matching
    scan()'s pre-existing per-day fail-and-continue discipline exactly.
    No cache read, no cache write, no company matching, no
    FilingEvent/CandidateSignal creation."""
    results: dict[str, NormalizedDayResult] = {}
    for day in dates:
        day_key = day.isoformat()
        try:
            payload = client.get_document_list(day_key)
        except EdinetError as exc:
            results[day_key] = NormalizedDayResult(rows=(), warnings=(str(exc),))
            continue
        rows, warnings = normalize_document_list(payload)
        results[day_key] = NormalizedDayResult(rows=tuple(rows), warnings=warnings)
    return results


_DEFAULT_STATUS_VALUES = frozenset({None, "", "0"})


def _status_fields_are_default(row: dict) -> bool:
    """True only when withdrawalStatus/docInfoEditStatus/disclosureStatus
    are all missing, null, "", or the exact string "0". Gate 6's live
    pull of a real, ordinary, fully disclosed SoftBank Group annual
    securities report (docID S100YGH5) confirmed all three fields carry
    the literal string "0" on a routine filing — Gate 5's original
    "empty is the only safe value" check would have deferred every real
    record. Confirmed-safe values are still deliberately narrow and
    exact, never guessed or coerced: an integer 0 (as opposed to the
    string "0"), any whitespace variant, any non-zero numeric string, or
    any other text is NOT treated as safe, since none of those shapes
    have been observed live — see scan()'s use of this function and
    ScanResult.deferred_status_count for what happens to a non-default
    row (withheld, not dropped, not turned into a candidate, and
    re-evaluated on a future scan rather than permanently ignored)."""
    return all(row.get(field) in _DEFAULT_STATUS_VALUES for field in _UNCONFIRMED_STATUS_FIELDS)


def _derive_filing_date(row: dict, query_date: str) -> tuple[str, str]:
    """The real per-record `submitDateTime` (e.g. "2026-08-17 09:00" —
    confirmed live, Gate 4) is the source of truth for a filing's date
    whenever present and parseable, truncated to its date component only
    (FilingEvent.rcept_dt is a date, and the shared Radar Inbox UI's date
    parsing helper — src/ui/pages/radar_inbox.py's _parse_rcept_date —
    expects YYYY-MM-DD/YYYYMMDD, not a timestamp with a time component;
    the full raw timestamp is not persisted anywhere, since no FilingEvent
    slot exists for it and adding one would be a shared-model expansion
    out of this gate's scope). Falls back to `query_date` (the calendar
    day this scan queried) only when submitDateTime is genuinely absent
    or unparsable, returning a deterministic, explicit reason string
    either way — never a silent, unexplained fallback."""
    raw = (row.get("submitDateTime") or "").strip()
    if raw:
        date_part = raw.split(" ", 1)[0]
        try:
            date.fromisoformat(date_part)
            return date_part, "submitDateTime"
        except ValueError:
            return query_date, f"query_date_fallback: submitDateTime was unparsable ({raw!r})"
    return query_date, "query_date_fallback: submitDateTime was missing"


def _derive_filed_at(row: dict) -> str | None:
    """Evidence-packet foundation, Phase 1 (design/DECISIONS.md): the
    full `submitDateTime` (e.g. "2026-08-17 09:00" — confirmed live, Gate
    4), preserved as a complete ISO 8601 timestamp for FilingEvent.filed_at
    — never truncated to a date the way _derive_filing_date's own
    `rcept_dt` output deliberately still is (that field's existing
    date-only contract is depended on elsewhere — see this module's own
    docstring on _derive_filing_date — and is never changed here).

    Returns None whenever submitDateTime is missing or not parseable as a
    "YYYY-MM-DD HH:MM[:SS]"-shaped value — never a fabricated time, and
    never a fallback to the query date (a calendar day is not a
    timestamp). No timezone/offset is added: the observed live value
    carries none, so none is invented; if EDINET ever supplies an
    explicit offset, `datetime.fromisoformat` preserves it unchanged."""
    raw = (row.get("submitDateTime") or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace(" ", "T", 1)).isoformat()
    except ValueError:
        return None


def _filing_event_from_row(row: dict, company: TrackedCompany, retrieved_at: str, filing_date: str, filed_at: str | None = None) -> FilingEvent:
    """Gate 8.1 fix: the real EDINET form-code triplet
    (ordinanceCode/formCode/docTypeCode — all three are _REQUIRED_FIELDS,
    guaranteed present on any row reaching this function) was previously
    only ever partially captured (formCode alone, into pblntf_ty) — the
    other two were read out of `row` here and then discarded, a real
    persistence loss confirmed by Gate 8's own live scan, not just a
    report-formatting gap. All three are now stored — see
    FilingEvent's own docstring (src/models/models.py) for which slot
    carries which code."""
    edinet_code = company.corp_code or row.get("edinetCode", "")
    return FilingEvent(
        rcept_no=row["docID"],
        corp_code=edinet_code,
        corp_name=company.name,
        stock_code=company.krx_code or row.get("secCode", ""),
        report_nm=row.get("docDescription") or row.get("filerName", ""),
        rcept_dt=filing_date,
        flr_nm=row.get("filerName", company.name),
        pblntf_ty=row.get("formCode", ""),
        pblntf_detail_ty=row.get("docTypeCode", ""),
        ordinance_code=row.get("ordinanceCode", ""),
        theme_slug=company.themes[0] if company.themes else "",
        subtheme_slug=company.subthemes[0] if company.subthemes else None,
        source_url=EdinetClient.document_index_url(row["docID"]),
        retrieved_at=retrieved_at,
        source_name=_SOURCE_LABEL,
        original_language="Japanese",
        primary_document="",
        filed_at=filed_at,
    )


def _evaluate_row(row: dict, code_category_map: dict[str, str]) -> edinet_rules.RuleEvaluation:
    return edinet_rules.evaluate_document(row.get("ordinanceCode", ""), row.get("formCode", ""), row.get("docTypeCode", ""), code_category_map)


def _candidate_signal_from_evaluation(filing: FilingEvent, evaluation: edinet_rules.RuleEvaluation) -> CandidateSignal:
    now = datetime.now(timezone.utc).isoformat()
    confidence = evaluation.confidence or "Low"
    source_detail = f"ordinanceCode={filing.ordinance_code} · formCode={filing.pblntf_ty} · docTypeCode={filing.pblntf_detail_ty}"
    return CandidateSignal(
        id=f"edinet-cand-{filing.rcept_no}",
        filing=filing,
        matched_rules=list(evaluation.matched_rules),
        confidence=confidence,
        status=CandidateStatus.CANDIDATE_DETECTED,
        state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at=now)],
        flag_reason=build_flag_reason(evaluation.matched_rules, confidence, source_detail=source_detail),
    )


def scan(
    client: EdinetClient,
    companies: list[TrackedCompany],
    cache_dir: Path,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    code_category_map: dict[str, str] = edinet_rules.DEFAULT_CODE_CATEGORY_MAP,
    dates: date | tuple[date, ...] | None = None,
) -> ScanResult:
    """One bounded scan across the given lookback window — one
    get_document_list call per calendar day (see module docstring).

    `dates` (Gate 8.1): when given, is the exact, explicit, non-empty
    date or dates to query — overrides `lookback_days`/`date_window`
    entirely, one get_document_list call per date given, no more, no
    fewer. This is the supported way to run a fixed-date validation
    scan (e.g. confirming one specific known filing) WITHOUT
    monkeypatching any private function, which is what Gate 8's own
    validation had to resort to and which produced a real, confirmed
    defect: `ScanScope.bgn_date`/`end_date` were computed from
    `date_window()`'s real-"today"-relative window, completely
    independent of the actually-monkeypatched date that was really
    queried, so the reported scope didn't match reality. With `dates`
    now a first-class parameter, `ScanScope.bgn_date`/`end_date` are
    ALWAYS derived from the same `query_dates` that were actually
    requested and processed — for both this explicit-date path and the
    ordinary bounded-lookback path below — so this class of mismatch is
    now structurally impossible, not just avoided by convention. The
    ordinary UI-scan path (`dates=None`) is completely unchanged.

    Company matching (Gate 5, deliberately conservative): a result row
    matches a tracked company ONLY when the row's top-level `edinetCode`
    exactly equals `company.corp_code`. Nullable `secCode`, filer name,
    and the distinct `issuerEdinetCode`/`subjectEdinetCode`/
    `subsidiaryEdinetCode` role fields are NOT used for matching this
    gate — broadening beyond direct `edinetCode` equality requires a
    later, live-evidence-backed gate to establish which role field is
    actually authoritative for "which company does this filing concern."

    A matching row whose withdrawalStatus/docInfoEditStatus/
    disclosureStatus are all empty proceeds to FilingEvent (and, if the
    category map matches, CandidateSignal) creation as before. A
    matching row where any of those three carry a non-empty value is
    counted in `ScanResult.deferred_status_count` and produces NO
    FilingEvent this run — see _status_fields_are_default's docstring
    for why.

    Idempotent: filings already in the on-disk cache (by dedup_key) are
    counted in `already_seen_count`, never duplicated. A day whose
    request raises a non-transient EdinetError, or whose response fails
    `normalize_document_list`'s envelope validation, is recorded in
    `errors` and skipped — the scan continues for the remaining days
    rather than aborting.

    `code_category_map` defaults to edinet_rules.DEFAULT_CODE_CATEGORY_MAP
    (empty — see that module's docstring): with the default, every real
    scan produces FilingEvents but zero CandidateSignals. The parameter
    exists so fixtures can inject their own explicit, fictional test
    mapping to exercise the candidate-creation path without this module
    claiming to know any real EDINET category code."""
    if dates is not None:
        query_dates = (dates,) if isinstance(dates, date) else tuple(sorted(dates))
        if not query_dates:
            raise ValueError("dates must be non-empty when explicitly provided")
    else:
        lookback_days = clamp_lookback_days(lookback_days)
        begin, end = date_window(lookback_days)
        query_dates = tuple(_iter_dates(begin, end))
    scanned_at = datetime.now(timezone.utc).isoformat()

    cache = _load_cache(cache_dir)
    seen: set[str] = set(cache["seen_keys"])

    by_edinet_code = {c.corp_code: c for c in companies if c.corp_code}

    new_filing_events: list[FilingEvent] = []
    new_candidate_signals: list[CandidateSignal] = []
    already_seen_count = 0
    deferred_status_count = 0
    errors: list[str] = []
    companies_with_data: set[str] = set()

    fetched = fetch_normalized_rows_for_dates(client, query_dates)
    for day in query_dates:
        day_result = fetched[day.isoformat()]
        for warning in day_result.warnings:
            errors.append(f"{day.isoformat()}: {warning}")

        for row in day_result.rows:
            company = by_edinet_code.get(row.get("edinetCode", ""))
            if company is None:
                continue
            companies_with_data.add(company.name)

            key = dedup_key(company.corp_code or row.get("edinetCode", ""), row["docID"])
            if key in seen:
                already_seen_count += 1
                continue

            if not _status_fields_are_default(row):
                deferred_status_count += 1
                continue
            seen.add(key)

            filing_date, date_reason = _derive_filing_date(row, day.isoformat())
            if date_reason != "submitDateTime":
                errors.append(f"{day.isoformat()}: {row['docID']}: {date_reason}")
            filed_at = _derive_filed_at(row)
            filing = _filing_event_from_row(row, company, scanned_at, filing_date, filed_at)
            new_filing_events.append(filing)
            evaluation = _evaluate_row(row, code_category_map)
            if evaluation.confidence is not None:
                new_candidate_signals.append(_candidate_signal_from_evaluation(filing, evaluation))

    no_data_companies = tuple(c.name for c in companies if c.name not in companies_with_data)

    cache["seen_keys"] = sorted(seen)
    cache["filing_events"] = cache["filing_events"] + [asdict(f) for f in new_filing_events]
    cache["candidate_signals"] = cache["candidate_signals"] + [asdict(c) for c in new_candidate_signals]
    _save_cache(cache_dir, cache)

    # bgn_date/end_date always derive from query_dates — the same dates
    # actually requested and processed above, for both the explicit-date
    # and ordinary bounded-lookback paths — never a separately computed
    # window that could diverge from reality (see the Gate 8.1 fix
    # described in this function's own docstring).
    scope = ScanScope(
        bgn_date=query_dates[0].isoformat(), end_date=query_dates[-1].isoformat(),
        lookback_days=(query_dates[-1] - query_dates[0]).days,
        companies=tuple(c.name for c in companies), source=_SOURCE_LABEL, scanned_at=scanned_at,
    )
    return ScanResult(
        scope=scope, new_filing_events=tuple(new_filing_events), new_candidate_signals=tuple(new_candidate_signals),
        already_seen_count=already_seen_count, errors=tuple(errors), no_data_companies=no_data_companies,
        deferred_status_count=deferred_status_count,
    )


@dataclass(frozen=True)
class FormCodeBackfillPlan:
    """One target docID's field-level before/after — built entirely
    before any write, so the caller (and, for Gate 9's report, the
    person authorizing the write) can see exactly what would change
    ahead of the write actually happening."""

    doc_id: str
    ordinance_code: str
    form_code: str
    doc_type_code: str


@dataclass(frozen=True)
class FormCodeBackfillResult:
    """Never raises for a validation failure — `error` is set instead,
    and in that case NOTHING was written (see backfill_form_codes'
    docstring). `updated`/`already_complete` only distinguish "needed a
    write" from "already matched" once every target has separately
    passed validation."""

    updated: tuple[str, ...] = ()
    already_complete: tuple[str, ...] = ()
    error: str | None = None


def _save_cache_atomic(cache_dir: Path, cache: dict) -> None:
    """Same JSON shape/location as _save_cache, but via a temp-file +
    os.replace so a crash mid-write can never leave a partially-written
    edinet_filing_events.json — used only by backfill_form_codes, which
    performs a targeted in-place field update on already-persisted
    records rather than the append-only write _save_cache's other
    callers (scan()) always do."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir)
    fd, tmp_name = tempfile.mkstemp(dir=cache_dir, prefix=".edinet_filing_events_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(cache, ensure_ascii=False, indent=2))
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def backfill_form_codes(
    client: EdinetClient,
    cache_dir: Path,
    target_doc_ids: tuple[str, ...],
    query_date: date,
    edinet_code: str,
) -> FormCodeBackfillResult:
    """Bounded, single-purpose repair for the real data-loss bug fixed
    in Gate 8.1: any FilingEvent persisted before that fix has an empty
    `ordinance_code`/`pblntf_detail_ty` even though the real
    `ordinanceCode`/`docTypeCode` values existed in the original scan's
    response — this backfills exactly those two fields on already-
    persisted records, from one fresh live request, never creating a new
    record of any kind.

    Exactly one client.get_document_list(query_date) call, no retry, no
    date iteration — deliberately not built on top of scan()'s own
    per-day loop, since scan() dedups by skipping already-seen records
    entirely (never revisiting or updating them) and would create a
    ScanResult/touch the seen-keys set, neither of which belongs in a
    narrow field-level backfill.

    For every docID in `target_doc_ids`, validates ALL of the following
    before writing ANYTHING (a field-level before/after plan is fully
    built in memory first): the docID appears in the live response
    exactly once (not absent, not duplicated); its row's `edinetCode`
    equals `edinet_code` exactly; a FilingEvent for that docID already
    exists in the on-disk cache (this function only updates existing
    records, never creates one); and the live row's `formCode` exactly
    matches the existing record's `pblntf_ty` (a mismatch there is
    refused, not overwritten — same "never guess, stop and report"
    discipline as every other EDINET module). The first failure of any
    kind for any target aborts the whole call with a clear `error` and
    zero writes — never a partial backfill.

    Idempotent: a docID whose cached `ordinance_code`/`pblntf_detail_ty`
    already match the live values needs no write and is reported in
    `already_complete`; if every target is already complete, the cache
    file is never even opened for writing. Writes (when needed) go
    through `_save_cache_atomic`, never `_save_cache`, so a crash
    mid-write can't corrupt the file. No other FilingEvent field, and no
    other on-disk record, is ever touched."""
    if not target_doc_ids:
        raise ValueError("target_doc_ids must be non-empty")

    try:
        payload = client.get_document_list(query_date.isoformat())
    except EdinetError as exc:
        return FormCodeBackfillResult(error=str(exc))

    rows, warnings = normalize_document_list(payload)
    if not rows and warnings:
        return FormCodeBackfillResult(error="; ".join(warnings))

    by_doc_id: dict[str, list[dict]] = {}
    for row in rows:
        by_doc_id.setdefault(row["docID"], []).append(row)

    cache = _load_cache(cache_dir)
    existing_by_doc_id = {f["rcept_no"]: f for f in cache["filing_events"] if "rcept_no" in f}

    plans: list[FormCodeBackfillPlan] = []
    for doc_id in target_doc_ids:
        matches = by_doc_id.get(doc_id, [])
        if len(matches) == 0:
            return FormCodeBackfillResult(error=f"{doc_id}: not found in the live {query_date.isoformat()} response.")
        if len(matches) > 1:
            return FormCodeBackfillResult(error=f"{doc_id}: ambiguous — {len(matches)} rows in the live response share this docID.")

        row = matches[0]
        live_edinet_code = row.get("edinetCode", "")
        if live_edinet_code != edinet_code:
            return FormCodeBackfillResult(error=f"{doc_id}: edinetCode mismatch — expected {edinet_code!r}, live response has {live_edinet_code!r}.")

        existing = existing_by_doc_id.get(doc_id)
        if existing is None:
            return FormCodeBackfillResult(error=f"{doc_id}: no existing cached FilingEvent — this helper only updates already-persisted records, never creates one.")

        live_form_code = row.get("formCode", "")
        cached_form_code = existing.get("pblntf_ty", "")
        if cached_form_code != live_form_code:
            return FormCodeBackfillResult(error=f"{doc_id}: formCode mismatch — cached pblntf_ty={cached_form_code!r}, live formCode={live_form_code!r}. Refusing to overwrite.")

        plans.append(FormCodeBackfillPlan(
            doc_id=doc_id, ordinance_code=row.get("ordinanceCode", ""),
            form_code=live_form_code, doc_type_code=row.get("docTypeCode", ""),
        ))

    updated: list[str] = []
    already_complete: list[str] = []
    for plan in plans:
        existing = existing_by_doc_id[plan.doc_id]
        if existing.get("ordinance_code", "") == plan.ordinance_code and existing.get("pblntf_detail_ty", "") == plan.doc_type_code:
            already_complete.append(plan.doc_id)
            continue
        existing["ordinance_code"] = plan.ordinance_code
        existing["pblntf_detail_ty"] = plan.doc_type_code
        updated.append(plan.doc_id)

    if updated:
        _save_cache_atomic(cache_dir, cache)

    return FormCodeBackfillResult(updated=tuple(updated), already_complete=tuple(already_complete))
