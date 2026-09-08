"""Typed SEC EDGAR client errors. Every one of these is meant to be
caught by the layer above the client (cik_resolver, edgar_service, the
Radar Inbox page) — nothing here should ever surface as an unhandled
exception in the UI, and none of these ever carry a raw provider
response body, header value, or the configured User-Agent contact
string. See design/DECISIONS.md's milestone-8 reliability requirements.
"""
from __future__ import annotations


class EdgarError(Exception):
    """Base class for every error this client can raise."""


class EdgarConfigError(EdgarError):
    """No identifying User-Agent configured — a setup problem, not a
    service problem. SEC requires a real, non-empty contact string on
    every request; without one this client fails closed rather than
    sending a request SEC would reject anyway."""


class EdgarApiError(EdgarError):
    """EDGAR returned a non-success HTTP status. Carries only the status
    code and a safe, generic message — never the raw response body."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(f"EDGAR error {status}: {message}")


class EdgarRateLimitError(EdgarApiError):
    """HTTP 429 — SEC's published rate limit was exceeded."""


class EdgarForbiddenError(EdgarApiError):
    """HTTP 403 — typically a missing/rejected User-Agent, or (per SEC's
    own documented policy) a temporary IP block following repeated
    violations. Distinct from EdgarConfigError: this is what SEC's
    server said, not what our own config check caught before sending."""


class EdgarTimeoutError(EdgarError):
    """The HTTP request itself timed out (network layer, not an EDGAR
    status code)."""


class EdgarParseError(EdgarError):
    """The response couldn't be parsed as expected — malformed JSON,
    missing required fields, or (see scan_service.py) a `filings.recent`
    columnar block whose parallel arrays don't line up. Never a reason
    to crash the app, only to show a clear "couldn't read this" state."""
