"""Fetches and parses one official company RSS 2.0 or Atom feed —
feedparser handles both formats transparently, so this module never
branches on feed_format itself (feed_registry.py's own field is
informational/documentation only). Never raises: any network or parse
failure returns an empty result plus a sanitized failure_code
(`type(exc).__name__` — never a raw exception message, matching this
codebase's existing radar_worker.py discipline — except `requests.
HTTPError`, whose failure_code also includes the real HTTP status code,
e.g. "HTTPError:403", when available; see `_failure_code()`), so one
feed's failure can be isolated by the caller without special-casing
exceptions.

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
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

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
    # Daily News worker observability, Part A (design/DECISIONS.md) —
    # purely descriptive of the one request fetch_entries() already
    # makes; adding these two fields introduces no second request, no
    # retry, and no change to failure_code's own existing behavior.
    duration_ms: float | None = None  # wall-clock time of the single GET, in milliseconds
    http_status: int | None = None  # the response's real HTTP status code, success or failure, when known


_FUTURE_TOLERANCE = timedelta(minutes=5)  # allows ordinary clock skew, catches a genuine mislabeling


def _parse_published_at(entry: dict) -> str:
    """Timezone-aware UTC, always. feedparser's own published_parsed/
    updated_parsed is a UTC-normalized struct_time when the source's raw
    date string carried a real, recognizable offset (RFC822 numeric/zone-
    name, RFC3339 'Z', etc.) — tagging that struct_time UTC is correct.
    But a source whose raw date string has NO offset at all (confirmed
    live for TheElec's own <pubDate>2026-09-14 07:26:41</pubDate> — no
    'Z', no numeric offset, not even a zone name) is echoed back by
    feedparser completely unshifted, as literal wall-clock digits; that
    is Korea local time here, not UTC, and blindly tagging it UTC makes
    a same-day KST-morning story display as tomorrow to an EDT reader —
    exactly the symptom this guards against.

    Never guesses the source's real offset (that would require a
    per-feed timezone table this module has no way to know) — instead,
    the one fact always true regardless of the source's own timezone
    convention is that a genuine publication can never be timestamped
    later than the moment this process is parsing it. A result more than
    _FUTURE_TOLERANCE past "now" is therefore never trustworthy — never
    displayed, never persisted — and falls back to "now" itself: always
    real, always safe, never in the future, matching this function's own
    existing "never a fabricated value" discipline (an unparseable date
    already falls back to "" rather than a guess; this is the same
    posture applied to a parseable-but-impossible one)."""
    struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if struct is None:
        return ""
    parsed = datetime(*struct[:6], tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if parsed > now + _FUTURE_TOLERANCE:
        return now.isoformat()
    return parsed.isoformat()


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


def _http_status_from_exception(exc: requests.RequestException) -> int | None:
    """The real HTTP status code carried by an HTTPError, when available
    — never the response body, headers, cookies, or the exception's own
    message/str(). None for every other RequestException subclass
    (ConnectionError, Timeout, TooManyRedirects, etc.), which never
    reached an HTTP response at all. Shared by _failure_code() (the
    existing sanitized-string diagnostic) and fetch_entries() (the new
    FeedFetchResult.http_status field, Daily News worker observability
    Part A, design/DECISIONS.md) so both read the exact same value."""
    if isinstance(exc, requests.HTTPError):
        status_code = getattr(exc.response, "status_code", None)
        if isinstance(status_code, int):
            return status_code
    return None


def _failure_code(exc: requests.RequestException) -> str:
    """Sanitized failure identifier — the exception class name only
    (`type(exc).__name__`), except for `requests.HTTPError`, where the
    real HTTP status code is additionally included when available (e.g.
    "HTTPError:403") — the bare class name alone conflates a 403 (bot-
    blocked), 404 (moved/gone), 429 (rate-limited), and a 5xx (origin
    outage) into one indistinguishable string, which is exactly what
    made a real production diagnosis (design/DECISIONS.md, Daily News
    operational-fix workstream) unable to tell those cases apart. Every
    other `RequestException` subclass is completely unaffected — returns
    exactly `type(exc).__name__`, same as before this change."""
    status_code = _http_status_from_exception(exc)
    if status_code is not None:
        return f"HTTPError:{status_code}"
    return type(exc).__name__


def fetch_entries(feed_url: str) -> FeedFetchResult:
    """One bounded fetch of one feed. Never a loop over many feeds — a
    caller wanting several calls this once per feed, deliberately.
    Exactly one `requests.get` call, unconditionally — no retry, no
    second validation request (Daily News worker observability Part A,
    design/DECISIONS.md, adds only `duration_ms`/`http_status` as
    descriptive facts about this same single request; Part B's bounded
    retry is a separate, not-yet-approved change)."""
    start = time.monotonic()
    try:
        response = requests.get(feed_url, timeout=_TIMEOUT_SECONDS, headers={"User-Agent": _USER_AGENT})
        response.raise_for_status()
    except requests.RequestException as exc:
        duration_ms = (time.monotonic() - start) * 1000
        return FeedFetchResult(
            entries=(), failure_code=_failure_code(exc),
            duration_ms=duration_ms, http_status=_http_status_from_exception(exc),
        )
    duration_ms = (time.monotonic() - start) * 1000

    parsed = feedparser.parse(response.content)
    if parsed.bozo and not parsed.entries:
        return FeedFetchResult(
            entries=(), failure_code="MalformedFeed", duration_ms=duration_ms, http_status=response.status_code,
        )

    entries = tuple(_to_raw_entry(entry) for entry in parsed.entries)
    return FeedFetchResult(entries=entries, failure_code=None, duration_ms=duration_ms, http_status=response.status_code)
