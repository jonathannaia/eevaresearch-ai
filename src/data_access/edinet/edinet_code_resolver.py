"""Resolves a TSE securities code -> EDINET's own EDINET code (E##### —
the identifier EDINET's document-list/document endpoints actually key
on), against the official EDINET code list only, never guessed or
hardcoded. Same "resolve from a bulk file, cross-check before trusting
it, never guess forward" discipline as
src/data_access/edgar/cik_resolver.py and DART's own corp_code_resolver.py.

Gate 3 status: this module's parsing logic (decoder chain, row
structure, header mapping, securities-code lookup normalization) was
corrected against the REAL EDINET code-list artifact, retrieved via one
live, credentialed request on 2026-08-17 (Gate 2) — a real ZIP
containing a single member `EdinetcodeDlInfo.csv`. Everything below
marked "confirmed" reflects that real response, not a secondary-source
guess. What remains unconfirmed:

  - `PROVISIONAL_CODE_LIST_URL` is functionally reachable and returns
    the correct shape (confirmed live, Gate 2 — HTTP 200), but was never
    independently read from the official API specification PDF, and
    whether the request *required* the configured credential was not
    established (Gate 2 made no unauthenticated attempt) — see
    design/DECISIONS.md's Gate 2 entry. Client transport/authentication
    assumptions are explicitly out of scope for this gate's correction.
  - The bulk code-list file's update cadence is unconfirmed.
  - No company has been added to tracked configuration this gate
    (explicitly forbidden) — this module's live use with a resolved
    company cohort remains a later gate's work.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.data_access.edinet.client import EdinetClient
from src.data_access.edinet.errors import EdinetError, EdinetParseError

_CACHE_FILENAME = "edinet_codes.json"
_SOURCE_LABEL = "EDINET code list (EdinetcodeDlInfo.csv — real shape confirmed live 2026-08-17, Gate 2)"

# Functionally confirmed reachable and correctly-shaped via one live
# request (Gate 2, 2026-08-17) — not independently read from the
# official spec PDF. See module docstring.
PROVISIONAL_CODE_LIST_URL = "https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/Edinetcode.zip"

# Real member filename observed live (Gate 2) — the ZIP's single CSV
# member is NOT named "Edinetcode.csv" as the URL's own naming would
# suggest. _extract_csv_text does not hardcode this name (it matches any
# single ".csv" member by extension), so this constant exists only for
# documentation/test-fixture purposes, not as a runtime assumption.
REAL_CODE_LIST_CSV_MEMBER_NAME = "EdinetcodeDlInfo.csv"

# Decoder fallback order, real-data-confirmed (Gate 2): the real file
# decodes as cp932 (a Windows-code-page superset of Shift-JIS) — plain
# "shift_jis" alone FAILED to decode the real file live. shift_jis and
# utf-8 are kept as further fallbacks only, never as the first attempt.
_DECODERS: tuple[str, ...] = ("cp932", "shift_jis", "utf-8")

# Real header row, confirmed live (Gate 2) — full-width Japanese column
# labels, not English machine keys. Column order in the real file:
# ＥＤＩＮＥＴコード, 提出者種別, 上場区分, 連結の有無, 資本金, 決算日,
# 提出者名, 提出者名（英字）, 提出者名（ヨミ）, 所在地, 提出者業種,
# 証券コード, 提出者法人番号 (13 columns total). Only the columns this
# module actually needs are mapped below; the rest are ignored, not
# modeled, since nothing downstream currently uses them.
DEFAULT_CODE_LIST_COLUMN_MAP: dict[str, str] = {
    "edinet_code": "ＥＤＩＮＥＴコード",
    "securities_code": "証券コード",
    "filer_name": "提出者名",
    "filer_name_en": "提出者名（英字）",
    "filer_corporate_number": "提出者法人番号",
}
# filer_name_en and filer_corporate_number are allowed to be blank in a
# real row (confirmed live — ispace's real row has an empty English-name
# field) and are therefore NOT required for a row to resolve.
_REQUIRED_MAPPED_FIELDS = ("edinet_code", "securities_code", "filer_name")


@dataclass(frozen=True)
class EdinetCodeEntry:
    edinet_code: str
    securities_code: str  # the real 5-character source-native value, e.g. "99840" or "285A0"
    filer_name: str
    filer_name_en: str
    filer_corporate_number: str
    source: str
    retrieved_at: str


@dataclass(frozen=True)
class CodeResolutionResult:
    """Outcome of a resolve_and_cache() run — always returned, never
    raised, mirroring cik_resolver.CikResolutionResult exactly."""

    resolved: dict[str, EdinetCodeEntry]  # keyed by the caller's own input lookup code (4- or 5-character)
    missing_codes: tuple[str, ...] = ()
    # A lookup code matching more than one real row — never silently
    # resolved to either match. Additive field (Gate 3): the real code
    # list's actual duplicate-code behavior is unconfirmed, so this
    # exists as a safe, explicit "don't guess" outcome bucket.
    ambiguous_codes: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class SummaryRow:
    """The real code-list CSV's first physical line — a metadata/count
    row, e.g. "ダウンロード実行日,2026年08月17日現在,件数,11382件"
    (confirmed live, Gate 2), NOT the column header row."""

    raw: str
    declared_count: int | None  # None when missing/unparseable — never guessed


def _cache_path(cache_dir: Path) -> Path:
    return cache_dir / _CACHE_FILENAME


def load_cached_codes(cache_dir: Path) -> dict[str, EdinetCodeEntry]:
    """Never raises — a missing/corrupt cache just means "not resolved
    yet," not a real error."""
    path = _cache_path(cache_dir)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    required_keys = {
        "edinet_code", "securities_code", "filer_name", "filer_name_en",
        "filer_corporate_number", "source", "retrieved_at",
    }
    return {
        code: EdinetCodeEntry(**record)
        for code, record in raw.items()
        if isinstance(record, dict) and required_keys <= record.keys()
    }


def _save_cache(cache_dir: Path, resolved: dict[str, EdinetCodeEntry]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {code: record.__dict__ for code, record in resolved.items()}
    _cache_path(cache_dir).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_csv_text(zip_bytes: bytes) -> str:
    """Unwraps the single CSV member from a ZIP payload (real member
    name confirmed live as EdinetcodeDlInfo.csv, Gate 2 — matched here
    by extension, not exact name, so a future filename change doesn't
    break this). Decoder order is real-data-confirmed: cp932 first (the
    real file's actual encoding), Shift-JIS and UTF-8 kept only as
    further fallbacks. Raises EdinetParseError only after every decoder
    in the chain fails, or the ZIP itself doesn't look like a
    single-CSV-file archive — never guesses which member is the real one
    and never silently returns mojibake."""
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            csv_names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
            if len(csv_names) != 1:
                raise EdinetParseError(f"Expected exactly one CSV file in the EDINET code-list ZIP, found {len(csv_names)}.")
            raw = archive.read(csv_names[0])
    except zipfile.BadZipFile as exc:
        raise EdinetParseError("EDINET code-list payload was not a valid ZIP file.") from exc

    for encoding in _DECODERS:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    raise EdinetParseError(f"EDINET code-list CSV could not be decoded (tried {', '.join(_DECODERS)}).")


def _parse_declared_count(summary_line: str) -> int | None:
    """Real shape confirmed live (Gate 2): the summary line's last
    comma-separated field is a Japanese count suffixed with "件" (a
    counter word for "items"), e.g. "11382件" -> 11382. Returns None
    (never raises, never guesses a number) for anything that doesn't
    match this exact shape."""
    parts = summary_line.split(",")
    if not parts:
        return None
    last = parts[-1].strip()
    if last.endswith("件"):
        last = last[:-1]
    try:
        return int(last)
    except ValueError:
        return None


def parse_summary_row(line: str) -> SummaryRow:
    return SummaryRow(raw=line, declared_count=_parse_declared_count(line))


def parse_code_list_csv(
    csv_text: str, column_map: dict[str, str] = DEFAULT_CODE_LIST_COLUMN_MAP,
) -> tuple[list[dict[str, str]], tuple[str, ...]]:
    """Structure confirmed live (Gate 2): physical row 0 is a summary/
    metadata row (see parse_summary_row), physical row 1 is the real
    column-header row, data begins at physical row 2. Header-driven
    field lookup (never position-driven within a row — a real column
    reorder must not silently corrupt this), using `column_map`'s real
    Japanese header text (see DEFAULT_CODE_LIST_COLUMN_MAP) to find each
    field's column index from the actual header row.

    Never raises. Returns (rows, warnings):
      - Fewer than 2 physical lines (no header row possible at all):
        ([], one warning).
      - Header row present but missing a required mapped column:
        ([], one warning) — never guesses which column might be the
        right one.
      - Summary row missing/unparseable (declared_count is None): rows
        are still returned, with one warning noting the count could not
        be validated (the header/data structure is positionally fixed
        regardless of the summary row's own content).
      - Summary row parseable but its declared count does NOT match the
        number of parsed data rows: ([], one warning) — a real
        structural surprise this significant is treated the same as any
        other malformed input elsewhere in this codebase (e.g.
        edgar_rules.normalize_recent_filings's mismatched-length case):
        fail closed rather than trust a partially-understood parse.
      - Rows missing a required field (_REQUIRED_MAPPED_FIELDS) are
        silently dropped, not guessed — same rule as before Gate 3.
    """
    lines = csv_text.splitlines()
    if len(lines) < 2:
        return [], ("EDINET code-list CSV is missing the header row (fewer than 2 physical rows present).",)

    summary = parse_summary_row(lines[0])
    header_row = next(csv.reader([lines[1]]), [])
    if not header_row or not all(column_map.get(field) in header_row for field in _REQUIRED_MAPPED_FIELDS):
        return [], ("EDINET code-list CSV header row did not contain the expected columns.",)
    col_index = {field: header_row.index(source_col) for field, source_col in column_map.items() if source_col in header_row}

    rows: list[dict[str, str]] = []
    for raw_row in csv.reader(lines[2:]):
        if not raw_row:
            continue
        entry = {
            field: (raw_row[idx].strip() if idx < len(raw_row) else "")
            for field, idx in col_index.items()
        }
        if all(entry.get(field) for field in _REQUIRED_MAPPED_FIELDS):
            rows.append(entry)

    if summary.declared_count is None:
        return rows, ("EDINET code-list summary row was missing or unparseable; record count was not validated.",)
    if summary.declared_count != len(rows):
        return [], (f"EDINET code-list declared {summary.declared_count} records but {len(rows)} were parsed — refusing to trust a mismatched parse.",)
    return rows, ()


def _normalize_lookup_code(input_code: str) -> str:
    """A 4-character TSE code (numeric like "9984" or alphanumeric like
    "285A") normalizes to `<input>0` SOLELY for matching against the
    real code list's native 5-character securities-code column
    (confirmed live, Gate 2 — e.g. "9984" -> "99840", "285A" -> "285A0").
    An already-5-character input passes through unchanged. This is a
    lookup-matching convenience only, not a general identifier
    transform — nothing outside this module's own resolution path
    should treat a 4-character code as equivalent to its normalized
    5-character form."""
    code = input_code.strip().upper()
    return f"{code}0" if len(code) == 4 else code


def resolve_and_cache(
    client: EdinetClient,
    securities_codes: list[str],
    cache_dir: Path,
    code_list_url: str = PROVISIONAL_CODE_LIST_URL,
    column_map: dict[str, str] = DEFAULT_CODE_LIST_COLUMN_MAP,
) -> CodeResolutionResult:
    """Fetches the code-list ZIP once, resolves the given securities
    codes (4- or 5-character — see _normalize_lookup_code) against it,
    and writes the merged result to the on-disk cache. Never raises: any
    EdinetError or parse failure/warning is captured into
    CodeResolutionResult.error, falling back to whatever was already
    cached. A lookup code matching more than one real row is recorded in
    `ambiguous_codes`, never silently resolved to either match."""
    existing = load_cached_codes(cache_dir)
    try:
        zip_bytes = client.fetch_code_list(code_list_url)
        csv_text = _extract_csv_text(zip_bytes)
    except EdinetError as exc:
        return CodeResolutionResult(resolved=existing, missing_codes=tuple(securities_codes), error=str(exc))

    rows, warnings = parse_code_list_csv(csv_text, column_map)
    if not rows and warnings:
        return CodeResolutionResult(resolved=existing, missing_codes=tuple(securities_codes), error="; ".join(warnings))

    by_securities_code: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_securities_code.setdefault(row["securities_code"], []).append(row)

    now = datetime.now(timezone.utc).isoformat()
    resolved = dict(existing)
    missing: list[str] = []
    ambiguous: list[str] = []

    for input_code in securities_codes:
        matches = by_securities_code.get(_normalize_lookup_code(input_code), [])
        if len(matches) == 0:
            missing.append(input_code)
            continue
        if len(matches) > 1:
            ambiguous.append(input_code)
            continue
        row = matches[0]
        resolved[input_code] = EdinetCodeEntry(
            edinet_code=row["edinet_code"], securities_code=row["securities_code"],
            filer_name=row["filer_name"], filer_name_en=row.get("filer_name_en", ""),
            filer_corporate_number=row.get("filer_corporate_number", ""),
            source=_SOURCE_LABEL, retrieved_at=now,
        )

    _save_cache(cache_dir, resolved)
    return CodeResolutionResult(resolved=resolved, missing_codes=tuple(missing), ambiguous_codes=tuple(ambiguous))


# --- Additive, discovery-only sibling below. Reuses _extract_csv_text /
# parse_code_list_csv / EdinetCodeEntry / CodeResolutionResult /
# PROVISIONAL_CODE_LIST_URL / DEFAULT_CODE_LIST_COLUMN_MAP / _SOURCE_LABEL
# unchanged. Every function above this point is untouched by this
# addition — same signatures, same behavior, same edinet_codes.json cache.

_DISCOVERY_CACHE_FILENAME = "edinet_discovery_codes.json"


def _discovery_cache_path(cache_dir: Path) -> Path:
    return cache_dir / _DISCOVERY_CACHE_FILENAME


def load_cached_discovery_codes(cache_dir: Path) -> dict[str, EdinetCodeEntry]:
    """Same shape/discipline as load_cached_codes, but reads the separate
    edinet_discovery_codes.json store used only by
    resolve_edinet_codes_by_code() — never edinet_codes.json, so this can
    never collide with or mutate the tracked pipeline's own resolver
    cache."""
    path = _discovery_cache_path(cache_dir)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    required_keys = {
        "edinet_code", "securities_code", "filer_name", "filer_name_en",
        "filer_corporate_number", "source", "retrieved_at",
    }
    return {
        code: EdinetCodeEntry(**record)
        for code, record in raw.items()
        if isinstance(record, dict) and required_keys <= record.keys()
    }


def _save_discovery_cache(cache_dir: Path, resolved: dict[str, EdinetCodeEntry]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {code: record.__dict__ for code, record in resolved.items()}
    _discovery_cache_path(cache_dir).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_edinet_codes_by_code(
    client: EdinetClient,
    edinet_codes: list[str],
    cache_dir: Path,
    code_list_url: str = PROVISIONAL_CODE_LIST_URL,
    column_map: dict[str, str] = DEFAULT_CODE_LIST_COLUMN_MAP,
) -> CodeResolutionResult:
    """Reverse-direction sibling of resolve_and_cache(): resolves a list
    of EDINET codes (e.g. "E12345") directly against the same code-list
    artifact, indexed by the list's own edinet_code column instead of
    securities_code — the direction resolve_and_cache() doesn't support.
    Used only by src/data_access/edinet/discovery_service.py, for
    company-name resolution of filers not in tracked_companies.py. Writes
    to its own edinet_discovery_codes.json; never reads or writes
    edinet_codes.json or touches resolve_and_cache()'s behavior in any
    way. Same never-guess discipline: a code matching zero or more than
    one real row is reported in `missing_codes`/`ambiguous_codes`, never
    silently resolved."""
    existing = load_cached_discovery_codes(cache_dir)
    try:
        zip_bytes = client.fetch_code_list(code_list_url)
        csv_text = _extract_csv_text(zip_bytes)
    except EdinetError as exc:
        return CodeResolutionResult(resolved=existing, missing_codes=tuple(edinet_codes), error=str(exc))

    rows, warnings = parse_code_list_csv(csv_text, column_map)
    if not rows and warnings:
        return CodeResolutionResult(resolved=existing, missing_codes=tuple(edinet_codes), error="; ".join(warnings))

    by_edinet_code: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_edinet_code.setdefault(row["edinet_code"], []).append(row)

    now = datetime.now(timezone.utc).isoformat()
    resolved = dict(existing)
    missing: list[str] = []
    ambiguous: list[str] = []

    for input_code in edinet_codes:
        matches = by_edinet_code.get(input_code.strip().upper(), [])
        if len(matches) == 0:
            missing.append(input_code)
            continue
        if len(matches) > 1:
            ambiguous.append(input_code)
            continue
        row = matches[0]
        resolved[input_code] = EdinetCodeEntry(
            edinet_code=row["edinet_code"], securities_code=row["securities_code"],
            filer_name=row["filer_name"], filer_name_en=row.get("filer_name_en", ""),
            filer_corporate_number=row.get("filer_corporate_number", ""),
            source=_SOURCE_LABEL, retrieved_at=now,
        )

    _save_discovery_cache(cache_dir, resolved)
    return CodeResolutionResult(resolved=resolved, missing_codes=tuple(missing), ambiguous_codes=tuple(ambiguous))
