"""Bounded translation orchestration for the Korea DART radar pilot —
titles and short extracted excerpts only, never whole documents. Caches
by (document id, excerpt hash) so the same text is never re-sent to the
translation provider twice. The Korean original always stays
authoritative; a translation is only ever a labeled convenience string
attached alongside it, never a replacement.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from src.data_access.translation.interfaces import (
    TranslationError,
    TranslationProvider,
    TranslationRateLimitError,
    TranslationTimeoutError,
)
from src.models.models import Translation

_CACHE_FILENAME = "translation_cache.json"
_MAX_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 1.5
SOURCE_LANG = "KO"
TARGET_LANG = "EN"


def _excerpt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _cache_key(document_id: str, text: str) -> str:
    return f"{document_id}:{_excerpt_hash(text)}"


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


def _translate_with_retry(provider: TranslationProvider, text: str, source_lang: str) -> str:
    attempt = 0
    while True:
        try:
            return provider.translate(text, source_lang, TARGET_LANG)
        except (TranslationRateLimitError, TranslationTimeoutError):
            attempt += 1
            if attempt > _MAX_RETRIES:
                raise
            time.sleep(_RETRY_BACKOFF_SECONDS * attempt)


def translate_cached(
    provider: TranslationProvider, document_id: str, text: str, cache_dir: Path, source_lang: str = SOURCE_LANG,
) -> Translation | None:
    """Returns a Translation on success, or None on ANY failure
    (missing key, network, rate limit, timeout, malformed response) —
    callers show "Translation unavailable" and keep the original text
    rather than raising into the UI. Never called on empty text.

    `source_lang` (evidence-packet foundation, Phase 1) is additive and
    optional, defaulting to this module's own pre-existing SOURCE_LANG
    ("KO") — every pre-Phase-1 call site (DART's radar_pipeline.py) is
    therefore completely unchanged. EDINET's edinet_pipeline.py is the
    first caller to pass "JA" explicitly, reusing this exact same
    function/provider/cache — the smallest possible extension, not a new
    translation path."""
    if not text:
        return None
    cache = _load_cache(cache_dir)
    key = _cache_key(document_id, text)
    cached = cache.get(key)
    if cached is not None:
        return Translation(**cached)

    try:
        translated_text = _translate_with_retry(provider, text, source_lang)
    except TranslationError:
        return None

    translation = Translation(
        translated_text=translated_text, provider=provider.name,
        source_lang=source_lang.lower(), target_lang=TARGET_LANG.lower(),
        translated_at=datetime.now(timezone.utc).isoformat(),
    )
    cache[key] = {
        "translated_text": translation.translated_text, "provider": translation.provider,
        "source_lang": translation.source_lang, "target_lang": translation.target_lang,
        "translated_at": translation.translated_at, "model": translation.model,
    }
    _save_cache(cache_dir, cache)
    return translation
