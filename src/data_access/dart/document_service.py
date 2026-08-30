"""Bounded, single-filing document retrieval + extraction orchestration
(Korea DART radar pilot). Every call is for one explicitly selected
receipt number — never a bulk or background sweep. Caches the extracted
excerpt (never the raw ZIP — see document_extractor.py's module
docstring and design/DECISIONS.md) to disk, outside Git and outside
data/edge_research.db, keyed by receipt number so a repeated request for
the same filing doesn't re-fetch or re-parse.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.data_access.dart.client import DartClient
from src.data_access.dart.document_extractor import extract_excerpt
from src.data_access.dart.errors import DartError, DartRateLimitError, DartTimeoutError
from src.models.models import ExtractionState

_CACHE_FILENAME = "dart_document_excerpts.json"
_MAX_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 1.5


@dataclass(frozen=True)
class DocumentFetchResult:
    rcept_no: str
    state: ExtractionState
    excerpt_original: str | None
    detail: str
    retrieved_at: str
    from_cache: bool


def _cache_path(cache_dir: Path) -> Path:
    return cache_dir / _CACHE_FILENAME


def _load_cache(cache_dir: Path) -> dict:
    path = _cache_path(cache_dir)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_cache(cache_dir: Path, cache: dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    _cache_path(cache_dir).write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _fetch_with_retry(client: DartClient, rcept_no: str) -> bytes:
    """DartClient makes exactly one request per call — retry/backoff for
    transient failures lives here, bounded to _MAX_RETRIES, same pattern
    as scan_service.py."""
    attempt = 0
    while True:
        try:
            return client.fetch_document_zip(rcept_no)
        except (DartRateLimitError, DartTimeoutError):
            attempt += 1
            if attempt > _MAX_RETRIES:
                raise
            time.sleep(_RETRY_BACKOFF_SECONDS * attempt)


def get_or_fetch_excerpt(client: DartClient, rcept_no: str, cache_dir: Path) -> DocumentFetchResult:
    """For ONE explicitly selected filing only — never a loop over many
    receipt numbers (a caller wanting several must call this once per
    filing, deliberately, not get an implicit bulk sweep). Checks the
    on-disk cache first, including a previously-failed result, so a
    known-unparseable document isn't retried on every page view."""
    cache = _load_cache(cache_dir)
    cached = cache.get(rcept_no)
    if cached is not None:
        return DocumentFetchResult(
            rcept_no=rcept_no, state=ExtractionState(cached["state"]), excerpt_original=cached.get("excerpt_original"),
            detail=cached.get("detail", ""), retrieved_at=cached["retrieved_at"], from_cache=True,
        )

    retrieved_at = datetime.now(timezone.utc).isoformat()
    try:
        zip_bytes = _fetch_with_retry(client, rcept_no)
    except DartError as exc:
        result = DocumentFetchResult(
            rcept_no=rcept_no, state=ExtractionState.RETRIEVAL_FAILED, excerpt_original=None,
            detail=str(exc), retrieved_at=retrieved_at, from_cache=False,
        )
        _cache_result(cache, cache_dir, result)
        return result

    extraction = extract_excerpt(zip_bytes)
    result = DocumentFetchResult(
        rcept_no=rcept_no, state=extraction.state, excerpt_original=extraction.excerpt_original,
        detail=extraction.detail, retrieved_at=retrieved_at, from_cache=False,
    )
    _cache_result(cache, cache_dir, result)
    return result


def _cache_result(cache: dict, cache_dir: Path, result: DocumentFetchResult) -> None:
    cache[result.rcept_no] = {
        "state": result.state.value, "excerpt_original": result.excerpt_original,
        "detail": result.detail, "retrieved_at": result.retrieved_at,
    }
    _save_cache(cache_dir, cache)
