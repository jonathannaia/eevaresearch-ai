"""Typed OpenDART/DART client errors. Every one of these is meant to be
caught by the layer above the client (corp_code_resolver, radar_service,
the Radar Inbox page) — nothing here should ever surface as an unhandled
exception in the UI. See design/DECISIONS.md's reliability requirements
for the Korea DART pilot.
"""
from __future__ import annotations


class DartError(Exception):
    """Base class for every error this client can raise."""


class DartConfigError(DartError):
    """No API key configured — a setup problem, not a service problem."""


class DartApiError(DartError):
    """DART returned a non-success status. Carries the raw status/message
    so callers can show DART's own explanation rather than a generic
    failure. status is a string since DART's own codes are ("000", "020",
    ...), and "network"/HTTP status codes are folded in here too."""

    def __init__(self, status: str, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(f"DART error {status}: {message}")


class DartRateLimitError(DartApiError):
    """DART status 020 — request-limit exceeded."""


class DartTimeoutError(DartError):
    """The HTTP request itself timed out (network layer, not a DART
    status code)."""


class DartParseError(DartError):
    """The response couldn't be parsed as expected (malformed ZIP/XML/
    JSON) — never a reason to crash the app, only to show a clear
    "couldn't read this" state."""
