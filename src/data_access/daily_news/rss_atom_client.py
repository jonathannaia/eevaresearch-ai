"""Fetches and parses one official company RSS 2.0 or Atom feed —
feedparser handles both formats transparently, so this module never
branches on feed_format itself (feed_registry.py's own field is
informational/documentation only). Never raises: any network or parse
failure returns an empty result plus a sanitized failure_code
(`type(exc).__name__` only — never a raw exception message, matching
this codebase's existing radar_worker.py discipline), so one feed's
failure can be isolated by the caller without special-casing exceptions.

Only ever reads the feed document itself — never follows an item's own
link to fetch the linked page (see summary_grounding.py's own docstring
for why: grounding is scoped to the feed's own bounded fields only).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import feedparser
import requests

_TIMEOUT_SECONDS = 10
_USER_AGENT = "EevaResearch-DailyNews/1.0"


@dataclass(frozen=True)
class RawFeedEntry:
    title: str
    link: str
    published_at: str  # ISO 8601, best-effort from the feed's own published/updated field
    summary: str | None  # raw description/summary field, HTML not yet stripped — see summary_grounding.py


@dataclass(frozen=True)
class FeedFetchResult:
    entries: tuple[RawFeedEntry, ...]
    failure_code: str | None  # sanitized: exception class name or "MalformedFeed" only


def _parse_published_at(entry: dict) -> str:
    struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if struct is None:
        return ""
    return datetime(*struct[:6], tzinfo=timezone.utc).isoformat()


def _to_raw_entry(entry: dict) -> RawFeedEntry:
    return RawFeedEntry(
        title=(entry.get("title") or "").strip(),
        link=(entry.get("link") or "").strip(),
        published_at=_parse_published_at(entry),
        summary=(entry.get("summary") or entry.get("description") or None),
    )


def fetch_entries(feed_url: str) -> FeedFetchResult:
    """One bounded fetch of one feed. Never a loop over many feeds — a
    caller wanting several calls this once per feed, deliberately."""
    try:
        response = requests.get(feed_url, timeout=_TIMEOUT_SECONDS, headers={"User-Agent": _USER_AGENT})
        response.raise_for_status()
    except requests.RequestException as exc:
        return FeedFetchResult(entries=(), failure_code=type(exc).__name__)

    parsed = feedparser.parse(response.content)
    if parsed.bozo and not parsed.entries:
        return FeedFetchResult(entries=(), failure_code="MalformedFeed")

    entries = tuple(_to_raw_entry(entry) for entry in parsed.entries)
    return FeedFetchResult(entries=entries, failure_code=None)
