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
from src.data_access.edinet.document_extractor import ANNUAL_SECURITIES_REPORT_CATEGORY, extract_excerpt
from src.data_access.edinet.errors import EdinetError, EdinetRateLimitError, EdinetTimeoutError
from src.models.models import ExtractionState

_CACHE_FILENAME = "edinet_document_excerpts.json"
_MAX_RETRIES = 2
_BACKOFF_BASE_SECONDS = 1.0
_JITTER_MAX_SECONDS = 0.5

# Annual-report extraction/selection policy stamp. Bump this ONLY when
# the semantics of what document_extractor selects or anchors on for an
# `annual_securities_report` change — never for an unrelated edit. It is
# appended to the cache key (see _cache_key) rather than stored as a
# field inside the record, so an entry written under an older policy is
# simply never looked up again: no legacy record is read, rewritten,
# migrated, swept, or deleted, and the previous key keeps its original
# bytes indefinitely.
_ANNUAL_REPORT_CACHE_POLICY = "v2-annual-section-anchored"


@dataclass(frozen=True)
class DocumentFetchResult:
    doc_id: str
    state: ExtractionState
    excerpt_original: str | None
    detail: str
    retrieved_at: str
    from_cache: bool
    # Phase 2, Step 2 — the selected ZIP member's safe archive-relative
    # path/name (see document_extractor.ExtractionResult.evidence_source_
    # member). None for every non-ZIP-success extraction. Additive
    # default so no existing construction of this dataclass breaks.
    evidence_source_member: str | None = None
    # The preferred statutory heading the excerpt was anchored on (see
    # document_extractor.ExtractionResult.location_section). None for
    # every non-annual path and for an annual report whose bounded
    # section search found nothing. Additive default, so no existing
    # construction of this dataclass breaks.
    location_section: str | None = None


def _cache_path(cache_dir: Path) -> Path:
    return cache_dir / _CACHE_FILENAME


def _cache_key(doc_id: str, category: str | None) -> str:
    """The ONE place a cache key is constructed, for both reads and
    writes. Annual-report entries are namespaced by the extraction policy
    that produced them, so a document extracted under an older policy is
    a miss (and is re-fetched under the new key) while its original
    record stays exactly as written. Every other category — and
    `category=None`, which includes every pre-existing caller — keeps the
    bare docID key and byte-identical cache behavior."""
    if category == ANNUAL_SECURITIES_REPORT_CATEGORY:
        return f"{doc_id}#{_ANNUAL_REPORT_CACHE_POLICY}"
    return doc_id


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
    category: str | None = None,
) -> DocumentFetchResult:
    """For ONE explicitly selected document only — never a loop over many
    docIDs. Checks the on-disk cache first, including a previously-failed
    result, so a known-unparseable document isn't retried on every page
    view. `type_` defaults to EdinetClient.DOCUMENT_TYPE_ZIP (1) — the
    format request sent to EDINET, not an assertion about the format
    that comes back (see document_extractor.py's module docstring).

    `category` (additive, defaulting to None) is the routing category
    edinet_pipeline already derived from the candidate's own
    matched_rules. It does exactly two things, both category-scoped: it
    is threaded into extract_excerpt to enable the bounded annual-report
    section search, and it selects the cache key via _cache_key. For
    `category=None` and every non-annual category, this function's
    behavior — including which key is read and written — is byte-
    identical to before the parameter existed."""
    cache = _load_cache(cache_dir)
    key = _cache_key(doc_id, category)
    cached = cache.get(key)
    if cached is not None:
        return DocumentFetchResult(
            doc_id=doc_id, state=ExtractionState(cached["state"]), excerpt_original=cached.get("excerpt_original"),
            detail=cached.get("detail", ""), retrieved_at=cached["retrieved_at"], from_cache=True,
            evidence_source_member=cached.get("evidence_source_member"),
            location_section=cached.get("location_section"),
        )

    retrieved_at = datetime.now(timezone.utc).isoformat()
    try:
        document_bytes = _fetch_with_retry(client, doc_id, type_)
    except EdinetError as exc:
        result = DocumentFetchResult(
            doc_id=doc_id, state=ExtractionState.RETRIEVAL_FAILED, excerpt_original=None,
            detail=str(exc), retrieved_at=retrieved_at, from_cache=False,
        )
        _cache_result(cache, cache_dir, result, key)
        return result

    extraction = extract_excerpt(document_bytes, category)
    result = DocumentFetchResult(
        doc_id=doc_id, state=extraction.state, excerpt_original=extraction.excerpt_original,
        detail=extraction.detail, retrieved_at=retrieved_at, from_cache=False,
        evidence_source_member=extraction.evidence_source_member,
        location_section=extraction.location_section,
    )
    _cache_result(cache, cache_dir, result, key)
    return result


def _cache_result(cache: dict, cache_dir: Path, result: DocumentFetchResult, key: str) -> None:
    """Writes exactly one entry, under the key _cache_key already chose
    for this call. Never touches any other entry: a legacy bare-docID
    record superseded by a namespaced annual-report one is left in place
    with its original bytes, unread and unmodified."""
    cache[key] = {
        "state": result.state.value, "excerpt_original": result.excerpt_original,
        "detail": result.detail, "retrieved_at": result.retrieved_at,
        "evidence_source_member": result.evidence_source_member,
        "location_section": result.location_section,
    }
    _save_cache(cache_dir, cache)
