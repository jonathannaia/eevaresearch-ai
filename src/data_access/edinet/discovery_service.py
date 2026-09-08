"""Isolated, read-only EDINET discovery path (Phase 1) — finds
Moderate/High-confidence filings from companies NOT in
tracked_companies.py, reusing the exact same fetch+normalize step
(scan_service.fetch_normalized_rows_for_dates) and the exact same,
unchanged deterministic rule engine (edinet_rules.evaluate_document) the
tracked pipeline uses. Writes only to its own separate cache files
(edinet_discovered_candidates.json here; edinet_discovery_codes.json via
edinet_code_resolver.resolve_edinet_codes_by_code) — never
edinet_filing_events.json or edinet_candidates.json, never Signals, never
Radar Inbox, never tracked_companies.py.

Deliberately independent of the tracked scan (not piggybacked on it): a
future discovery run makes its own get_document_list call(s), one per
explicit date given — `dates` is a required parameter with no
lookback-window default, per the approved Phase 1 scope. No scheduler,
no button, no CLI entry point exists in this phase — scan_for_discoveries
is a plain library function, callable only by whoever explicitly imports
and calls it (a future, separately-approved step).

Never auto-promotes a discovered company to tracked_companies.py, never
assigns a theme (a discovered company has none — deliberately
unclassified, not guessed), never extracts a document, never calls an
LLM, never filters confidence at write time (both Moderate and High are
persisted, whatever the rule engine actually returned)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from src.config.tracked_companies import TrackedCompany
from src.data_access.edinet import edinet_code_resolver, edinet_rules, scan_service
from src.data_access.edinet.client import EdinetClient

_CACHE_FILENAME = "edinet_discovered_candidates.json"


@dataclass(frozen=True)
class DiscoveredCandidate:
    """Standalone record — deliberately not added to src/models/models.py.
    Every field is either a structured API field copied verbatim, a
    resolver output, or a rule-engine output; nothing here is inferred."""

    edinet_code: str
    company_name: str
    doc_id: str
    source_url: str
    filing_date: str
    doc_description: str
    ordinance_code: str
    form_code: str
    doc_type_code: str
    matched_rule: str
    confidence: str
    discovered_at: str


@dataclass(frozen=True)
class DiscoveryScanResult:
    new_discoveries: tuple[DiscoveredCandidate, ...]
    already_seen_count: int
    # EDINET codes whose company-name resolution failed this run — the
    # row is dropped, never given a guessed name.
    skipped_unresolved: tuple[str, ...]
    errors: tuple[str, ...]


def _cache_path(cache_dir: Path) -> Path:
    return cache_dir / _CACHE_FILENAME


def _empty_cache() -> dict:
    return {"seen_keys": [], "discoveries": []}


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


def load_discoveries(cache_dir: Path) -> tuple[DiscoveredCandidate, ...]:
    """Read-only — used by scripts/view_edinet_discoveries.py. Never
    triggers a scan, resolution, or write."""
    items: list[DiscoveredCandidate] = []
    for data in _load_cache(cache_dir)["discoveries"]:
        try:
            items.append(DiscoveredCandidate(**data))
        except TypeError:
            continue
    return tuple(items)


def _filing_date_for_row(row: dict, query_date: str) -> tuple[str, str]:
    """Mirrors scan_service._derive_filing_date's logic exactly, kept as
    a small independent copy rather than importing a private helper
    across a module boundary — consistent with this module's isolated-
    path design (see module docstring)."""
    raw = (row.get("submitDateTime") or "").strip()
    if raw:
        date_part = raw.split(" ", 1)[0]
        try:
            date.fromisoformat(date_part)
            return date_part, "submitDateTime"
        except ValueError:
            return query_date, f"query_date_fallback: submitDateTime was unparsable ({raw!r})"
    return query_date, "query_date_fallback: submitDateTime was missing"


def scan_for_discoveries(
    client: EdinetClient,
    tracked_companies: list[TrackedCompany],
    discovery_cache_dir: Path,
    dates: date | tuple[date, ...],
    code_category_map: dict[str, str] = edinet_rules.DEFAULT_CODE_CATEGORY_MAP,
) -> DiscoveryScanResult:
    """One client.get_document_list call per explicit date given — `dates`
    is required (no default), so this never runs an implicit lookback
    window. Reuses scan_service.fetch_normalized_rows_for_dates() (the
    exact fetch+normalize step scan() itself uses) and
    edinet_rules.evaluate_document() (unchanged, real
    DEFAULT_CODE_CATEGORY_MAP) — never scan_service.scan() itself, so
    edinet_filing_events.json/edinet_candidates.json are never read or
    written here.

    A row is a candidate discovery only when its edinetCode is NOT one of
    the given tracked_companies' corp_code values AND
    evaluate_document() returns a non-None confidence (Moderate or High —
    both persisted, the actual value returned, never filtered to
    High-only). Deduped by the same (edinet_code, docID) shape as
    scan_service.dedup_key, against this store's own separate seen-set —
    never scan_service's tracked-scan seen-set.

    Company names for newly-seen edinet_codes are resolved in one batched
    call to edinet_code_resolver.resolve_edinet_codes_by_code() (all
    distinct new codes this run, not one call per row) — a code that
    fails to resolve is recorded in `skipped_unresolved` and its row is
    dropped, never given a guessed name."""
    query_dates = (dates,) if isinstance(dates, date) else tuple(sorted(dates))
    if not query_dates:
        raise ValueError("dates must be non-empty")

    tracked_codes = {c.corp_code for c in tracked_companies if c.corp_code}

    cache = _load_cache(discovery_cache_dir)
    seen: set[str] = set(cache["seen_keys"])

    fetched = scan_service.fetch_normalized_rows_for_dates(client, query_dates)

    errors: list[str] = []
    matched_rows: list[tuple[dict, str, edinet_rules.RuleEvaluation]] = []

    for day in query_dates:
        day_key = day.isoformat()
        day_result = fetched[day_key]
        for warning in day_result.warnings:
            errors.append(f"{day_key}: {warning}")

        for row in day_result.rows:
            edinet_code = row.get("edinetCode", "")
            if not edinet_code or edinet_code in tracked_codes:
                continue
            evaluation = edinet_rules.evaluate_document(
                row.get("ordinanceCode", ""), row.get("formCode", ""), row.get("docTypeCode", ""), code_category_map,
            )
            if evaluation.confidence is None:
                continue
            matched_rows.append((row, day_key, evaluation))

    already_seen_count = 0
    to_resolve: list[tuple[dict, str, edinet_rules.RuleEvaluation]] = []
    for row, day_key, evaluation in matched_rows:
        key = scan_service.dedup_key(row.get("edinetCode", ""), row["docID"])
        if key in seen:
            already_seen_count += 1
            continue
        to_resolve.append((row, day_key, evaluation))

    distinct_codes = sorted({row.get("edinetCode", "") for row, _day_key, _evaluation in to_resolve})
    resolution = (
        edinet_code_resolver.resolve_edinet_codes_by_code(client, distinct_codes, discovery_cache_dir)
        if distinct_codes
        else None
    )
    if resolution is not None and resolution.error:
        errors.append(f"EDINET code-list resolution: {resolution.error}")

    new_discoveries: list[DiscoveredCandidate] = []
    skipped_unresolved: list[str] = []
    discovered_at = datetime.now(timezone.utc).isoformat()

    for row, day_key, evaluation in to_resolve:
        edinet_code = row.get("edinetCode", "")
        entry = resolution.resolved.get(edinet_code) if resolution else None
        if entry is None:
            if edinet_code not in skipped_unresolved:
                skipped_unresolved.append(edinet_code)
            continue

        company_name = entry.filer_name_en or entry.filer_name
        filing_date, date_reason = _filing_date_for_row(row, day_key)
        if date_reason != "submitDateTime":
            errors.append(f"{day_key}: {row['docID']}: {date_reason}")

        key = scan_service.dedup_key(edinet_code, row["docID"])
        seen.add(key)
        new_discoveries.append(DiscoveredCandidate(
            edinet_code=edinet_code,
            company_name=company_name,
            doc_id=row["docID"],
            source_url=EdinetClient.document_index_url(row["docID"]),
            filing_date=filing_date,
            doc_description=row.get("docDescription") or row.get("filerName", ""),
            ordinance_code=row.get("ordinanceCode", ""),
            form_code=row.get("formCode", ""),
            doc_type_code=row.get("docTypeCode", ""),
            matched_rule=evaluation.matched_rules[0],
            confidence=evaluation.confidence,
            discovered_at=discovered_at,
        ))

    cache["seen_keys"] = sorted(seen)
    cache["discoveries"] = cache["discoveries"] + [asdict(d) for d in new_discoveries]
    _save_cache(discovery_cache_dir, cache)

    return DiscoveryScanResult(
        new_discoveries=tuple(new_discoveries),
        already_seen_count=already_seen_count,
        skipped_unresolved=tuple(skipped_unresolved),
        errors=tuple(errors),
    )
