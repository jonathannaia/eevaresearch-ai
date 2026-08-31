"""Canonical-URL gate — the one hard check between a raw feed entry and
persistence. An entry that fails this check is suppressed entirely (see
daily_news_pipeline.py), never partially rendered with a missing or
weak link. Deliberately conservative: exact-match against each feed
source's own explicit canonical_domains tuple, no wildcard subdomain
expansion — a domain not explicitly listed in feed_registry.py is never
implicitly trusted just because it looks related.
"""
from __future__ import annotations

from urllib.parse import urlparse

# Exact path-segment names that indicate a search-result page rather
# than an individual announcement. Matched as a whole path segment, not
# a raw substring — a naive substring check on "search" would wrongly
# reject a real announcement slug like ".../ai-innovation-and-research-
# in-the-united-kingdom" (confirmed live against AMD's own feed during
# implementation — "research" contains "search").
_SEARCH_PATH_SEGMENTS = {"search", "results"}
_SEARCH_QUERY_PARAMS = {"q", "query", "s", "search"}


def validate_canonical_url(url: str, canonical_domains: tuple[str, ...], feed_url: str) -> bool:
    if not url or not url.strip():
        return False
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False

    if parsed.scheme != "https":
        return False
    if not parsed.netloc:
        return False

    netloc = parsed.netloc.lower().rstrip(".")
    allowed = {domain.lower().rstrip(".") for domain in canonical_domains}
    if netloc not in allowed:
        return False

    if url.strip().rstrip("/") == feed_url.strip().rstrip("/"):
        return False  # never the feed URL itself

    path = (parsed.path or "").rstrip("/")
    if path == "":
        return False  # bare homepage, not an individual item

    segments = {segment.lower() for segment in path.split("/") if segment}
    if segments & _SEARCH_PATH_SEGMENTS:
        return False

    query_params = {pair.split("=", 1)[0].lower() for pair in (parsed.query or "").split("&") if pair}
    if query_params & _SEARCH_QUERY_PARAMS:
        return False

    return True


def validate_image_url(url: str | None, image_host: str | None) -> bool:
    """A separate, stricter allowlist from validate_canonical_url()'s
    article-link canonical_domains — an image is very commonly hosted on
    a different domain than the source's own IR page (a CDN, an asset
    host), confirmed live for NVIDIA and SK Hynix (see
    design/DECISIONS.md), so this is never derived from
    canonical_domains and never falls back to it. `image_host=None`
    means no image host has been explicitly approved for this source —
    every candidate image fails closed, exactly like an unset allowlist
    rather than an implicitly-trusted one. Exact hostname match only —
    no wildcard, suffix, or parent-domain matching, and no shared-CDN
    trust (a different company's own CloudFront/Q4/etc. host is never
    implicitly accepted just because the pattern looks similar)."""
    if image_host is None:
        return False
    if not url or not url.strip():
        return False
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False

    if parsed.scheme != "https":
        return False  # rejects http, data:, blob:, file:, and scheme-less/relative URLs alike
    if parsed.username is not None or parsed.password is not None:
        return False  # rejects credentials embedded in the URL

    hostname = (parsed.hostname or "").lower().rstrip(".")
    return hostname == image_host.lower().rstrip(".")
