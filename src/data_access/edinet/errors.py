"""Typed EDINET client errors (Japan radar pilot, planning Gate 1 —
fixture-only, no live network calls of any kind). Every one of these is
meant to be caught by the layer above the client, and none ever carries
the EDINET Subscription-Key or a raw response body. See
design/DECISIONS.md's Gate 0 documentation-verification findings for
what's confirmed vs. still provisional about EDINET's real error
behavior — this error set is deliberately broader than what Gate 0 could
confirm (401/403/404/429/5xx/timeout/parse), since a fixture-only gate
must be ready for outcomes that haven't been observed live yet, same
"never guess, but be ready" posture the DART/EDGAR clients started
with before their own first live pull.
"""
from __future__ import annotations


class EdinetError(Exception):
    """Base class for every error this client can raise."""


class EdinetConfigError(EdinetError):
    """No Subscription-Key configured — a setup problem, not a service
    problem. This gate never reads or validates the real credential
    value; this error only fires on a missing/empty configuration."""


class EdinetApiError(EdinetError):
    """EDINET returned a non-success HTTP status. Carries only the
    status code and a safe, generic message — never a raw response
    body, which could otherwise leak into logs/UI."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(f"EDINET error {status}: {message}")


class EdinetUnauthorizedError(EdinetApiError):
    """HTTP 401 — an invalid or rejected Subscription-Key. Distinct from
    EdinetConfigError: this is what EDINET's server said about a
    credential that WAS sent, not a local "nothing configured" check."""


class EdinetForbiddenError(EdinetApiError):
    """HTTP 403."""


class EdinetNotFoundError(EdinetApiError):
    """HTTP 404 — e.g. an unknown docID."""


class EdinetRateLimitError(EdinetApiError):
    """HTTP 429."""


class EdinetTimeoutError(EdinetError):
    """The HTTP request itself timed out (network layer, not an EDINET
    status code)."""


class EdinetParseError(EdinetError):
    """The response couldn't be parsed as expected — malformed JSON,
    an unrecognized document payload shape, or similar. Never a reason
    to crash the app, only to show a clear "couldn't read this" state."""
