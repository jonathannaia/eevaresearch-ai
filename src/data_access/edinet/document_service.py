"""Bounded, single-document retrieval + extraction orchestration (Japan
radar pilot, planning Gate 1). Every call is for one explicitly selected
docID only — never a bulk or background sweep. Caches the extracted
excerpt (never the raw document bytes) to disk, outside Git and outside
data/edge_research.db, keyed by EDINET's own docID. Mirrors
src/data_access/edgar/document_service.py's shape exactly.
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.data_access.edinet.client import DOCUMENT_TYPE_ZIP, EdinetClient
from src.data_access.edinet.document_extractor import extract_excerpt
from src.data_access.edinet.errors import EdinetError, EdinetRateLimitError, EdinetTimeoutError
from src.models.models import ExtractionState

_CACHE_FILENAME = "edinet_document_excerpts.json"
_MAX_RETRIES = 2
_BACKOFF_BASE_SECONDS = 1.0
_JITTER_MAX_SECONDS = 0.5


@dataclass(frozen=True)
class DocumentFetchResult:
    doc_id: str
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


def _fetch_with_retry(client: EdinetClient, doc_id: str, type_: int) -> bytes:
    """EdinetClient makes exactly one request per call — bounded
    exponential backoff WITH JITTER for transient failures lives here,
    capped at _MAX_RETRIES, never an unbounded/indefinite retry loop."""
    attempt = 0
    while True:
        try:
            return client.fetch_document(doc_id, type_)
        except (EdinetRateLimitError, EdinetTimeoutError):
            attempt += 1
            if attempt > _MAX_RETRIES:
                raise
            backoff = _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, _JITTER_MAX_SECONDS)
            time.sleep(backoff)


def get_or_fetch_excerpt(
    client: EdinetClient, doc_id: str, cache_dir: Path, type_: int = 1,
) -> DocumentFetchResult:
    """For ONE explicitly selected document only — never a loop over many
    docIDs. Checks the on-disk cache first, including a previously-failed
    result, so a known-unparseable document isn't retried on every page
    view. `type_` defaults to EdinetClient.DOCUMENT_TYPE_ZIP (1) — the
    format request sent to EDINET, not an assertion about the format
    that comes back (see document_extractor.py's module docstring)."""
    cache = _load_cache(cache_dir)
    cached = cache.get(doc_id)
    if cached is not None:
        return DocumentFetchResult(
            doc_id=doc_id, state=ExtractionState(cached["state"]), excerpt_original=cached.get("excerpt_original"),
            detail=cached.get("detail", ""), retrieved_at=cached["retrieved_at"], from_cache=True,
        )

    retrieved_at = datetime.now(timezone.utc).isoformat()
    try:
        document_bytes = _fetch_with_retry(client, doc_id, type_)
    except EdinetError as exc:
        result = DocumentFetchResult(
            doc_id=doc_id, state=ExtractionState.RETRIEVAL_FAILED, excerpt_original=None,
            detail=str(exc), retrieved_at=retrieved_at, from_cache=False,
        )
        _cache_result(cache, cache_dir, result)
        return result

    extraction = extract_excerpt(document_bytes)
    result = DocumentFetchResult(
        doc_id=doc_id, state=extraction.state, excerpt_original=extraction.excerpt_original,
        detail=extraction.detail, retrieved_at=retrieved_at, from_cache=False,
    )
    _cache_result(cache, cache_dir, result)
    return result


def _cache_result(cache: dict, cache_dir: Path, result: DocumentFetchResult) -> None:
    cache[result.doc_id] = {
        "state": result.state.value, "excerpt_original": result.excerpt_original,
        "detail": result.detail, "retrieved_at": result.retrieved_at,
    }
    _save_cache(cache_dir, cache)
