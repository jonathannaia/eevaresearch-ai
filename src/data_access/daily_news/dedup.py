"""Cross-source deduplication for Daily News. Two distinct mechanisms
are in play, only one of which lives here:

1. Idempotency (the same real-world item seen again on a later
   discovery run) is handled by daily_news_pipeline.py giving each
   NewsStory a deterministic id derived from (company, canonical URL) —
   re-running discovery naturally upserts the same id rather than
   creating a duplicate, same pattern as candidate_store.py's own
   id-keyed dict.
2. This module handles the other case: the SAME development reported
   under a DIFFERENT canonical URL (e.g. two of one company's own feeds
   both carrying the same announcement). Slice 1's pilot has exactly one
   feed per company, so this case doesn't arise in practice yet, but the
   check exists now rather than being deferred, since a future
   multi-feed-per-company config is plausible. A detected duplicate is
   suppressed (not persisted) — the first-seen story wins; Slice 1 does
   not merge corroborating sources onto one story.

Two normalizers, two separate contracts — do not use one where the other
is documented:

- normalize_title() / _NORMALIZE_RE is the legacy ASCII-compatible
  normalizer. Its output is relied on, unchanged, by the DART/EDINET/EDGAR
  filing-candidate adapters (dart_filing_candidate_adapter.py,
  edinet_filing_candidate_adapter.py, edgar_filing_candidate_adapter.py)
  as a non-load-bearing diagnostic suffix on their dedupe_key — those
  adapters' own tests hardcode this ASCII-only behavior (a non-Latin
  title normalizes to ""), so this function's output must never change.
- normalize_title_unicode() / _UNICODE_NORMALIZE_RE is Daily News's own
  Unicode-safe normalizer, used only by is_duplicate_title() below, so
  that Hangul/CJK/accented titles are preserved rather than discarded
  when matching duplicate headlines (see tests/test_daily_news_dedup.py's
  SK Hynix regression tests).
"""
from __future__ import annotations

import re
from typing import Iterable

_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
_UNICODE_NORMALIZE_RE = re.compile(r"[^\w]+")


def normalize_title(title: str) -> str:
    """Legacy ASCII-compatible normalizer — see module docstring. Keep
    this output stable; DART/EDINET/EDGAR filing-candidate adapters and
    their tests depend on its exact (ASCII-only) behavior."""
    return _NORMALIZE_RE.sub(" ", title.lower()).strip()


def normalize_title_unicode(title: str) -> str:
    """Unicode-safe normalizer for Daily News headline duplicate
    matching only — see module docstring. Relies on Python's default
    (non-re.ASCII) \\w behavior to preserve Hangul, CJK, and accented
    Latin word characters instead of discarding them."""
    return _UNICODE_NORMALIZE_RE.sub(" ", title.lower()).strip()


def is_duplicate_title(existing_headlines: Iterable[tuple[str, str]], company_name: str, title: str) -> bool:
    """`existing_headlines` is an iterable of (company_name, headline)
    pairs — the caller's own already-persisted stories, passed in this
    shape rather than full NewsStory objects so this module never needs
    to import daily_news_models.py at all. Uses normalize_title_unicode()
    (not normalize_title()) so titles differing only in non-Latin content
    are correctly treated as distinct."""
    fingerprint = normalize_title_unicode(title)
    if not fingerprint:
        return False
    return any(
        existing_company == company_name and normalize_title_unicode(existing_title) == fingerprint
        for existing_company, existing_title in existing_headlines
    )
