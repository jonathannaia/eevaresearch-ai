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

Also extracts an optional per-item image, in priority order: media:content,
media:thumbnail, an image-typed enclosure, then an <img> embedded in the
item's own description/content HTML. Only ever reads entry-level fields
(feedparser exposes the channel/feed-level logo separately as
`parsed.feed.image`, never touched here) — see canonical_url.
validate_image_url() for the separate, per-source exact-hostname gate
applied downstream before any image URL is ever rendered."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

import feedparser
import requests

_TIMEOUT_SECONDS = 10
_USER_AGENT = "EevaResearch-DailyNews/1.0"

_TAG_RE = re.compile(r"<[^>]+>")
_IMG_SRC_RE = re.compile(r'<img\b[^>]*\bsrc=["\']([^"\'>]+)["\']', re.IGNORECASE)
_IMG_ALT_RE = re.compile(r'<img\b[^>]*\balt=["\']([^"\'>]*)["\']', re.IGNORECASE)


@dataclass(frozen=True)
class RawFeedEntry:
    title: str
    link: str
    published_at: str  # ISO 8601, best-effort from the feed's own published/updated field
    summary: str | None  # raw description/summary/content:encoded text, HTML not yet stripped — see summary_grounding.py
    image_url: str | None = None  # raw, not yet validated — see canonical_url.validate_image_url()
    image_alt: str | None = None  # source-supplied alt text if present; caller falls back to the item title otherwise


@dataclass(frozen=True)
class FeedFetchResult:
    entries: tuple[RawFeedEntry, ...]
    failure_code: str | None  # sanitized: exception class name or "MalformedFeed" only


def _parse_published_at(entry: dict) -> str:
    struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if struct is None:
        return ""
    return datetime(*struct[:6], tzinfo=timezone.utc).isoformat()


def _plain_text_length(html_text: str) -> int:
    """Rough richness proxy for choosing between two raw HTML candidate
    fields — strips tags only, no unescaping, since this is a comparison
    signal only; summary_grounding.py's own _strip_html() is what
    actually cleans whichever text is chosen for display."""
    return len(_TAG_RE.sub("", html_text).strip())


def _best_source_text(entry: dict) -> str | None:
    """Prefers the richer of description/summary and content:encoded —
    feedparser normalizes RSS <description>/Atom <summary> into
    entry.summary, and <content:encoded> into entry.content (a list).
    Richness is measured by tag-stripped length so a short text wrapped
    in heavy markup doesn't look artificially longer."""
    candidates = []
    summary = entry.get("summary")
    if summary:
        candidates.append(summary)
    for content_entry in entry.get("content") or []:
        value = content_entry.get("value") if isinstance(content_entry, dict) else None
        if value:
            candidates.append(value)
    if not candidates:
        return None
    return max(candidates, key=_plain_text_length)


def _extract_image(entry: dict) -> tuple[str | None, str | None]:
    """Entry-level only, in priority order: media:content, media:thumbnail,
    an image-typed enclosure, then an <img> embedded in the entry's own
    description/content HTML. Returns (image_url, image_alt), both None
    if nothing qualifies. Never reads anything at the feed/channel level."""
    for media in entry.get("media_content") or []:
        url = media.get("url")
        medium = (media.get("medium") or "").lower()
        media_type = (media.get("type") or "").lower()
        if url and (medium == "image" or media_type.startswith("image/") or (not medium and not media_type)):
            return url, None

    for thumb in entry.get("media_thumbnail") or []:
        url = thumb.get("url")
        if url:
            return url, None

    for enclosure in entry.get("enclosures") or []:
        url = enclosure.get("href")
        enclosure_type = (enclosure.get("type") or "").lower()
        if url and enclosure_type.startswith("image/"):
            return url, None

    for html_field in (entry.get("summary"), *(c.get("value") for c in (entry.get("content") or []) if isinstance(c, dict))):
        if not html_field:
            continue
        match = _IMG_SRC_RE.search(html_field)
        if match:
            alt_match = _IMG_ALT_RE.search(html_field)
            return match.group(1), (alt_match.group(1) if alt_match else None)

    return None, None


def _to_raw_entry(entry: dict) -> RawFeedEntry:
    image_url, image_alt = _extract_image(entry)
    return RawFeedEntry(
        title=(entry.get("title") or "").strip(),
        link=(entry.get("link") or "").strip(),
        published_at=_parse_published_at(entry),
        summary=_best_source_text(entry),
        image_url=image_url,
        image_alt=image_alt,
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
