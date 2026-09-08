"""Resolves TrackedCompany.krx_code (holding a ticker for EDGAR entries —
see tracked_companies.py's module docstring) -> SEC's own 10-digit CIK,
against real official SEC data only, never guessed or hardcoded.

Two-step resolution, both real API calls, no shortcuts:
1. Look up the ticker in SEC's official bulk company_tickers.json file
   to get a candidate CIK.
2. Cross-check that candidate CIK against the company's own real
   submissions metadata (data.sec.gov/submissions/CIK##########.json)
   before accepting it — the ticker must actually appear in that
   filing's own reported `tickers` list. A ticker that resolves to a
   bulk-file entry but fails this cross-check is left unresolved
   (missing_tickers), never guessed forward. This second step is more
   thorough than DART's own corp_code_resolver.py, which only does the
   single-file lookup — required explicitly for the SEC EDGAR pilot.

Caches the result to data/cache/edgar_ciks.json (gitignored, never
committed) so a scan doesn't re-download/re-cross-check every time.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.data_access.edgar.client import EdgarClient, normalize_cik
from src.data_access.edgar.errors import EdgarError

_CACHE_FILENAME = "edgar_ciks.json"
_SOURCE_LABEL = "SEC company_tickers.json + submissions cross-check"


@dataclass(frozen=True)
class ResolvedCik:
    cik: str  # zero-padded 10-digit, cross-check-verified
    company_name: str
    source: str
    retrieved_at: str


@dataclass(frozen=True)
class CikResolutionResult:
    """Outcome of a resolve_and_cache() run — always returned, never
    raised, so a caller (the Radar Inbox, a setup script) can show a
    clear state either way rather than handling an exception."""

    resolved: dict[str, ResolvedCik]  # keyed by ticker
    missing_tickers: tuple[str, ...]  # requested but unresolved or failed cross-check
    error: str | None = None  # set on total failure (config/network/parse)


def _cache_path(cache_dir: Path) -> Path:
    return cache_dir / _CACHE_FILENAME


def load_cached_ciks(cache_dir: Path) -> dict[str, ResolvedCik]:
    """Never raises — a missing/corrupt cache just means "not resolved
    yet," not a real error."""
    path = _cache_path(cache_dir)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    required_keys = {"cik", "company_name", "source", "retrieved_at"}
    return {
        ticker: ResolvedCik(**record)
        for ticker, record in raw.items()
        if isinstance(record, dict) and required_keys <= record.keys()
    }


def _save_cache(cache_dir: Path, resolved: dict[str, ResolvedCik]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {ticker: record.__dict__ for ticker, record in resolved.items()}
    _cache_path(cache_dir).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _candidate_cik_by_ticker(bulk_tickers: dict) -> dict[str, dict]:
    """SEC's company_tickers.json shape (per cross-confirmed docs — see
    client.py's module docstring): a dict of numeric-string keys, each
    mapping to {"cik_str": int, "ticker": str, "title": str}. Tolerant of
    an unexpected top-level shape (returns {} rather than raising) so a
    format change surfaces as "nothing resolved," not a crash."""
    by_ticker: dict[str, dict] = {}
    if not isinstance(bulk_tickers, dict):
        return by_ticker
    for entry in bulk_tickers.values():
        if not isinstance(entry, dict):
            continue
        ticker = str(entry.get("ticker", "")).upper().strip()
        if ticker:
            by_ticker[ticker] = entry
    return by_ticker


def resolve_and_cache(client: EdgarClient, tickers: list[str], cache_dir: Path) -> CikResolutionResult:
    """Fetches SEC's bulk ticker file once, resolves the given tickers
    against it, cross-checks each candidate CIK against real submissions
    metadata, and writes the merged, verified result to the on-disk
    cache (previously-resolved companies are kept even if this run only
    asks about a new one). Never raises: any EdgarError during the bulk
    fetch is captured into CikResolutionResult.error, falling back to
    whatever was already cached; a per-ticker cross-check failure is
    recorded in missing_tickers rather than aborting the whole run."""
    existing = load_cached_ciks(cache_dir)
    try:
        bulk = client.fetch_company_tickers()
    except EdgarError as exc:
        return CikResolutionResult(resolved=existing, missing_tickers=tuple(tickers), error=str(exc))

    by_ticker = _candidate_cik_by_ticker(bulk)
    now = datetime.now(timezone.utc).isoformat()
    resolved = dict(existing)
    missing: list[str] = []

    for ticker in tickers:
        candidate = by_ticker.get(ticker.upper().strip())
        if candidate is None:
            missing.append(ticker)
            continue
        cik = normalize_cik(str(candidate.get("cik_str", "")))
        if not cik or int(cik) == 0:
            missing.append(ticker)
            continue

        try:
            submissions = client.get_submissions(cik)
        except EdgarError:
            missing.append(ticker)
            continue

        submitted_tickers = {str(t).upper().strip() for t in submissions.get("tickers", [])}
        if ticker.upper().strip() not in submitted_tickers:
            # Bulk-file lookup found a candidate, but the company's own
            # real submissions metadata doesn't confirm it — never guess
            # forward past a failed cross-check.
            missing.append(ticker)
            continue

        resolved[ticker] = ResolvedCik(
            cik=cik, company_name=str(candidate.get("title", "")),
            source=_SOURCE_LABEL, retrieved_at=now,
        )

    _save_cache(cache_dir, resolved)
    return CikResolutionResult(resolved=resolved, missing_tickers=tuple(missing))
