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
"""
from __future__ import annotations

import re
from typing import Iterable

_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def normalize_title(title: str) -> str:
    return _NORMALIZE_RE.sub(" ", title.lower()).strip()


def is_duplicate_title(existing_headlines: Iterable[tuple[str, str]], company_name: str, title: str) -> bool:
    """`existing_headlines` is an iterable of (company_name, headline)
    pairs — the caller's own already-persisted stories, passed in this
    shape rather than full NewsStory objects so this module never needs
    to import daily_news_models.py at all."""
    fingerprint = normalize_title(title)
    if not fingerprint:
        return False
    return any(
        existing_company == company_name and normalize_title(existing_title) == fingerprint
        for existing_company, existing_title in existing_headlines
    )
