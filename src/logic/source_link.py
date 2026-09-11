"""Shared, narrowly-scoped source-link safety helper (design/DECISIONS.md).

EDINET's document/list API (`https://api.edinet-fsa.go.jp/...`) requires a
server-side Subscription-Key to return anything but an HTTP 401 — see
src/data_access/edinet/client.py's own EdinetClient.document_index_url()
docstring for why that raw, key-required URL is nonetheless what ends up
stored as FilingEvent.source_url for every EDINET filing (no confirmed
public per-document viewer URL exists). Every UI surface that renders a
`source_url`-shaped string as a clickable link must pass it through
public_source_url() first, so a browser is never handed that raw,
guaranteed-401 API endpoint.

No credential is ever read, embedded, or required here — this module does
not import EdinetClient, Settings, or anything that could touch the real
Subscription-Key. It is a pure string transform over an already-stored URL.
"""
from __future__ import annotations

_EDINET_API_HOST_PREFIX = "https://api.edinet-fsa.go.jp/"
# The public EDINET disclosure portal root — a safe, always-reachable
# fallback, never claimed to open the specific document (no confirmed
# derivable per-document public URL exists; see EdinetClient.
# document_index_url()'s own docstring). Same value this app has already
# used for this exact purpose (formerly radar_card.py's own
# _EDINET_SEARCH_URL constant, now consolidated here as the one source of
# truth every rendering surface shares).
EDINET_PUBLIC_PORTAL_URL = "https://disclosure2.edinet-fsa.go.jp/"


def public_source_url(url: str | None) -> str | None:
    """Returns `url` unchanged for every source except EDINET's own
    key-required API host, which is rewritten to the public disclosure
    portal root instead. `None`/empty input is returned unchanged (never
    invented, never coerced to a placeholder) — callers already decide
    separately whether to render a link at all when this returns falsy.

    The EDINET-host check is case-insensitive: some existing callers
    pass a URL through their own scheme-safety check first (matched
    case-insensitively there) and pass the ORIGINAL, non-lowercased
    string on to this function — matching case-insensitively here too
    means this safety check can never be bypassed by casing alone,
    regardless of which caller's own normalization discipline it goes
    through first."""
    if not url:
        return url
    if url.lower().startswith(_EDINET_API_HOST_PREFIX):
        return EDINET_PUBLIC_PORTAL_URL
    return url
