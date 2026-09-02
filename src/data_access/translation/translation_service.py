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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.data_access.translation.interfaces import (
    TranslationApiError,
    TranslationConfigError,
    TranslationError,
    TranslationProvider,
    TranslationRateLimitError,
    TranslationTimeoutError,
)
from src.models.models import CandidateSignal, Translation, TranslationState

_CACHE_FILENAME = "translation_cache.json"
_MAX_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 1.5
SOURCE_LANG = "KO"
TARGET_LANG = "EN"

# Bounded, unattended background retry for a translation that failed with
# a retryable cause — same shape as src/data_access/dart/retry_policy.py's
# own automatic_retry_eligible()/AUTOMATIC_RETRY_BACKOFF_SCHEDULE_MINUTES
# for stale RETRIEVAL_FAILED candidates, applied here to translation
# specifically since translation is a source-neutral, shared concern
# (DART and EDINET both call into this module; EDGAR never does, since it
# never requests a translation at all). MAX_TRANSLATION_RETRY_ATTEMPTS=5
# with 5 backoff entries: retry_count 0..4 each map to one schedule entry,
# so the 5th failed retry (retry_count reaching 5) stops scheduling and
# the candidate's translation_next_retry_at becomes None — the public
# card's own signal that no further retry will happen.
MAX_TRANSLATION_RETRY_ATTEMPTS = 5
TRANSLATION_RETRY_BACKOFF_MINUTES: tuple[int, ...] = (5, 15, 60, 240, 720)
AUTOMATIC_TRANSLATION_RETRY_MAX_PER_TICK = 5

# Only these categories are ever scheduled for an automatic retry — a
# missing API key or a malformed provider response will not resolve
# itself by waiting, so those stay terminal (translation_next_retry_at
# stays None) rather than retrying forever for no benefit.
_RETRYABLE_TRANSLATION_FAILURE_CATEGORIES = frozenset({"rate_limit", "timeout", "network", "provider_error"})

# FilingEvent.original_language values this app actually sets (see
# scan_service.py in each source) mapped to the DeepL source-language code
# a retry should use — English is never present here since a candidate
# whose translation_state is UNAVAILABLE is, by construction, never an
# EDGAR (English-native) candidate: EDGAR never requests a translation in
# the first place (translation_state stays NOT_REQUESTED).
_LANGUAGE_CODE_BY_NAME: dict[str, str] = {"Korean": "KO", "Japanese": "JA"}


@dataclass(frozen=True)
class TranslationAttempt:
    """The outcome of one translate_cached_with_outcome() call — richer
    than the plain Translation | None translate_cached() returns, so a
    caller can persist *why* a translation failed and whether it's worth
    retrying, not just that it failed. `failure_category`/`failure_reason`
    are None whenever `translation` succeeded; `retryable` is always False
    on success (meaningless there)."""

    translation: Translation | None
    failure_category: str | None = None
    failure_reason: str | None = None
    retryable: bool = False


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


def _categorize_failure(exc: TranslationError) -> tuple[str, str, bool]:
    """Maps a raised TranslationError to (category, safe human-readable
    reason, retryable) — never includes the raw exception text/args
    verbatim (only interfaces.py's own fixed status codes, which are
    never a secret or a credential). Categories are deliberately narrow
    and closed (not derived from the exception's own message) so a
    future new failure mode defaults to non-retryable rather than being
    silently and incorrectly assumed transient."""
    if isinstance(exc, TranslationRateLimitError):
        return "rate_limit", "Translation provider rate limit exceeded.", True
    if isinstance(exc, TranslationTimeoutError):
        return "timeout", "Translation request timed out.", True
    if isinstance(exc, TranslationConfigError):
        return "config_missing_key", "Translation API key is not configured.", False
    if isinstance(exc, TranslationApiError):
        if exc.status == "network":
            return "network", "Translation provider network error.", True
        if exc.status == "parse":
            return "parse_error", "Translation provider response was malformed.", False
        return "provider_error", f"Translation provider returned an error ({exc.status}).", True
    return "unknown", "Translation failed for an unknown reason.", False


def translate_cached_with_outcome(
    provider: TranslationProvider, document_id: str, text: str, cache_dir: Path, source_lang: str = SOURCE_LANG,
) -> TranslationAttempt:
    """The real implementation behind translate_cached() below — returns
    the full outcome (including failure category/reason/retryable) so a
    caller can persist a safe, specific reason and decide whether an
    automatic retry is worth scheduling, instead of collapsing every
    failure mode to a bare None. Same caching/never-raises contract as
    translate_cached(): a failed attempt is never cached, so a later
    retry can still succeed."""
    if not text:
        return TranslationAttempt(translation=None)
    cache = _load_cache(cache_dir)
    key = _cache_key(document_id, text)
    cached = cache.get(key)
    if cached is not None:
        return TranslationAttempt(translation=Translation(**cached))

    try:
        translated_text = _translate_with_retry(provider, text, source_lang)
    except TranslationError as exc:
        category, reason, retryable = _categorize_failure(exc)
        return TranslationAttempt(translation=None, failure_category=category, failure_reason=reason, retryable=retryable)

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
    return TranslationAttempt(translation=translation)


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
    translation path.

    Translation reliability workstream: this is now a thin wrapper over
    translate_cached_with_outcome() above, which is the function every
    real pipeline call site uses instead so it can persist a specific
    failure category/reason. Kept unchanged in shape and behavior for any
    caller (and test) that only ever needed the plain Translation | None
    contract."""
    return translate_cached_with_outcome(provider, document_id, text, cache_dir, source_lang=source_lang).translation


def record_translation_attempt(candidate: CandidateSignal, attempt: TranslationAttempt, now: datetime | None = None) -> None:
    """The one place translation_state and the persisted failure/retry
    fields (translation_failure_category/_reason/_at, translation_next_
    retry_at) are set from a TranslationAttempt — used identically by the
    first, in-pipeline attempt and by every later automatic retry, so
    both paths compute the exact same backoff schedule from `candidate.
    translation_retry_count`. Callers are responsible for incrementing
    translation_retry_count themselves before calling this on a *retry*
    attempt (see retry_translation_for_candidate below) — the initial,
    in-pipeline attempt calls this with retry_count still at its default
    of 0, so the first scheduled retry always uses this schedule's first
    entry."""
    now = now or datetime.now(timezone.utc)
    if attempt.translation is not None:
        candidate.translation_state = TranslationState.TRANSLATED
        candidate.translation_failure_category = None
        candidate.translation_failure_reason = None
        candidate.translation_failure_at = None
        candidate.translation_next_retry_at = None
        return
    candidate.translation_state = TranslationState.UNAVAILABLE
    candidate.translation_failure_category = attempt.failure_category
    candidate.translation_failure_reason = attempt.failure_reason
    candidate.translation_failure_at = now.isoformat()
    if attempt.retryable and candidate.translation_retry_count < MAX_TRANSLATION_RETRY_ATTEMPTS:
        backoff_index = min(candidate.translation_retry_count, len(TRANSLATION_RETRY_BACKOFF_MINUTES) - 1)
        candidate.translation_next_retry_at = (now + timedelta(minutes=TRANSLATION_RETRY_BACKOFF_MINUTES[backoff_index])).isoformat()
    else:
        candidate.translation_next_retry_at = None


def translation_retry_eligible(candidate: CandidateSignal, now: datetime | None = None) -> bool:
    """Whether a pipeline run may pick up this candidate for an automatic,
    unattended translation retry this tick — mirrors src/data_access/
    dart/retry_policy.py's own automatic_retry_eligible() shape for
    RETRIEVAL_FAILED candidates. Never raises; a malformed persisted
    timestamp fails open (eligible now) rather than permanently blocking
    a candidate that could otherwise recover."""
    if candidate.translation_state != TranslationState.UNAVAILABLE:
        return False
    if candidate.translation_failure_category not in _RETRYABLE_TRANSLATION_FAILURE_CATEGORIES:
        return False
    if candidate.translation_retry_count >= MAX_TRANSLATION_RETRY_ATTEMPTS:
        return False
    if not candidate.translation_next_retry_at:
        return False
    now = now or datetime.now(timezone.utc)
    try:
        next_retry = datetime.fromisoformat(candidate.translation_next_retry_at)
        if next_retry.tzinfo is None:
            next_retry = next_retry.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return True
    return now >= next_retry


def retry_translation_for_candidate(provider: TranslationProvider, candidate: CandidateSignal, cache_dir: Path) -> CandidateSignal:
    """Re-attempts exactly the translation that caused this candidate's
    TranslationState.UNAVAILABLE — the excerpt if one was extracted,
    otherwise the title — persisting the new outcome via
    record_translation_attempt(). Callers must gate with
    translation_retry_eligible() first; this function does not re-check
    eligibility itself (same division of labor as radar_pipeline.
    process_candidate vs. retry_policy.py's own eligibility checks)."""
    candidate.translation_retry_count += 1
    source_lang = _LANGUAGE_CODE_BY_NAME.get(candidate.filing.original_language, SOURCE_LANG)
    primary_text = candidate.excerpt_original or candidate.filing.report_nm
    attempt = translate_cached_with_outcome(provider, candidate.filing.rcept_no, primary_text, cache_dir, source_lang=source_lang)
    if candidate.excerpt_original:
        candidate.excerpt_translation = attempt.translation
    else:
        candidate.title_translation = attempt.translation
    record_translation_attempt(candidate, attempt)
    return candidate
