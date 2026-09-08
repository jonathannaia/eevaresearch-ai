"""Resolves TrackedCompany.krx_code -> DART's internal corp_code, once,
against the real OpenDART bulk lookup — never guessed or hardcoded (see
src/config/tracked_companies.py). Caches the result to
data/cache/dart_corp_codes.json (gitignored, never committed) so a scan
doesn't re-download DART's several-MB corp-code file every time.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.data_access.dart.client import DartClient
from src.data_access.dart.errors import DartError

_CACHE_FILENAME = "dart_corp_codes.json"


@dataclass(frozen=True)
class ResolvedCorpCode:
    corp_code: str
    corp_name: str
    source: str
    retrieved_at: str


@dataclass(frozen=True)
class ResolutionResult:
    """Outcome of a resolve_and_cache() run — always returned, never
    raised, so a caller (the Radar Inbox, a setup script) can show a
    clear state either way rather than handling an exception."""

    resolved: dict[str, ResolvedCorpCode]
    missing_krx_codes: tuple[str, ...]  # requested but not found (or ambiguous) in DART's bulk file
    error: str | None = None  # set on total failure (config/network/parse); resolved may still hold cached values


def _cache_path(cache_dir: Path) -> Path:
    return cache_dir / _CACHE_FILENAME


def load_cached_corp_codes(cache_dir: Path) -> dict[str, ResolvedCorpCode]:
    """Reads whatever a previous resolve_and_cache() run cached. Returns
    {} if nothing's cached yet — never raises, since a missing/corrupt
    cache just means "not resolved yet," not a real error."""
    path = _cache_path(cache_dir)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    required_keys = {"corp_code", "corp_name", "source", "retrieved_at"}
    return {
        krx: ResolvedCorpCode(**record)
        for krx, record in raw.items()
        if isinstance(record, dict) and required_keys <= record.keys()
    }


def _save_cache(cache_dir: Path, resolved: dict[str, ResolvedCorpCode]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {krx: record.__dict__ for krx, record in resolved.items()}
    _cache_path(cache_dir).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_and_cache(client: DartClient, krx_codes: list[str], cache_dir: Path) -> ResolutionResult:
    """Fetches DART's full corp-code bulk file once, resolves the given
    KRX stock codes against it, and writes the merged result to the
    on-disk cache (previously-resolved companies are kept even if this
    run only asks about a new one). Never raises: any DartError is
    captured into ResolutionResult.error, falling back to whatever was
    already cached rather than losing prior results."""
    existing = load_cached_corp_codes(cache_dir)
    try:
        all_codes = client.fetch_all_corp_codes()
    except DartError as exc:
        return ResolutionResult(resolved=existing, missing_krx_codes=tuple(krx_codes), error=str(exc))

    by_stock_code: dict[str, list] = {}
    for record in all_codes:
        if record.stock_code:
            by_stock_code.setdefault(record.stock_code, []).append(record)

    now = datetime.now(timezone.utc).isoformat()
    resolved = dict(existing)
    missing: list[str] = []
    for krx in krx_codes:
        matches = by_stock_code.get(krx, [])
        if len(matches) != 1:
            # Zero matches, or an ambiguous multi-match — either way this
            # is exactly the "don't guess" case: leave it unresolved
            # rather than picking one arbitrarily.
            missing.append(krx)
            continue
        match = matches[0]
        resolved[krx] = ResolvedCorpCode(
            corp_code=match.corp_code, corp_name=match.corp_name,
            source="OpenDART corpCode.xml", retrieved_at=now,
        )

    _save_cache(cache_dir, resolved)
    return ResolutionResult(resolved=resolved, missing_krx_codes=tuple(missing))
