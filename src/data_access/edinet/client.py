"""Thin EDINET REST client (Japan radar pilot, planning Gate 1 — fixture-
only, zero live network calls, credential never read/validated/used this
gate). Every method does exactly one HTTP call and raises a typed error
from errors.py on failure — same one-call-per-method, never-guess
discipline as DartClient/EdgarClient.

Endpoints, parameter names, and the credential transport below are
**cross-confirmed from two independent secondary developer sources
during Gate 0's documentation pass, NOT independently verified against
the official API Version 2 PDF directly** (that PDF's binary/compressed
stream could not be text-extracted this session). See
design/DECISIONS.md's Gate 0 entry for the exact provenance of each
fact and what remains open. Confirmed directly from an official source
(Japan's e-Gov API catalog): provider is the Financial Services Agency,
the API is REST, and response formats include JSON, ZIP, and PDF — this
client's `fetch_document` is deliberately format-agnostic (returns raw
bytes) rather than assuming one format, per that same uncertainty.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import requests

from src.data_access.edinet.errors import (
    EdinetApiError,
    EdinetConfigError,
    EdinetForbiddenError,
    EdinetNotFoundError,
    EdinetParseError,
    EdinetRateLimitError,
    EdinetTimeoutError,
    EdinetUnauthorizedError,
)

# Provisional — cross-confirmed from two independent secondary sources in
# Gate 0, not read directly from the official PDF. To be treated as
# "likely correct, not yet verified" until a Gate 2 live pull confirms
# it, same status DART's earliest endpoint guesses carried before their
# own first live verification.
_DOCUMENT_LIST_URL = "https://api.edinet-fsa.go.jp/api/v2/documents.json"
_DOCUMENT_URL_TEMPLATE = "https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}"
_CREDENTIAL_PARAM_NAME = "Subscription-Key"  # query parameter, NOT a header — per Gate 0 findings

_DEFAULT_TIMEOUT_SECONDS = 15
# Deliberately conservative provisional pilot rate — sourced from a
# community developer's own self-imposed 3-second delay described as
# "respecting the API's rate limit" in Gate 0's secondary-source
# research, NOT an officially documented number (none was found this
# session). Must be reconsidered once the official spec's actual rate
# guidance is confirmed live.
DEFAULT_MIN_INTERVAL_SECONDS = 3.0

# Document-retrieval `type` parameter values, cross-confirmed from
# secondary sources (Gate 0) — not yet independently verified live.
DOCUMENT_TYPE_ZIP = 1
DOCUMENT_TYPE_PDF = 2
DOCUMENT_TYPE_CSV = 5


@dataclass(frozen=True)
class DocumentListResult:
    raw_payload: dict  # the full parsed JSON response — shape not yet finalized into a typed record (see edinet_rules.py / scan_service.py, Gate 1)


class EdinetClient:
    def __init__(
        self,
        subscription_key: str | None,
        session: requests.Session | None = None,
        timeout: int = _DEFAULT_TIMEOUT_SECONDS,
        min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS,
    ) -> None:
        self._subscription_key = subscription_key
        self._session = session or requests.Session()
        self._timeout = timeout
        self._min_interval = min_interval_seconds
        self._last_request_at: float | None = None

    def _require_key(self) -> str:
        if not self._subscription_key:
            raise EdinetConfigError("EDGE_EDINET_SUBSCRIPTION_KEY is not configured.")
        return self._subscription_key

    def _throttle(self) -> None:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            remaining = self._min_interval - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_at = time.monotonic()

    def _get(self, url: str, params: dict) -> requests.Response:
        key = self._require_key()
        self._throttle()
        request_params = {**params, _CREDENTIAL_PARAM_NAME: key}
        try:
            return self._session.get(url, params=request_params, timeout=self._timeout)
        except requests.Timeout as exc:
            raise EdinetTimeoutError(f"EDINET request timed out after {self._timeout}s.") from exc
        except requests.RequestException as exc:
            raise EdinetApiError(0, "Network error contacting EDINET.") from exc

    def _check_response(self, response: requests.Response) -> None:
        status = response.status_code
        if status == 401:
            raise EdinetUnauthorizedError(401, "EDINET rejected the configured credential.")
        if status == 403:
            raise EdinetForbiddenError(403, "EDINET request forbidden.")
        if status == 404:
            raise EdinetNotFoundError(404, "EDINET resource not found.")
        if status == 429:
            raise EdinetRateLimitError(429, "EDINET rate limit exceeded.")
        if status != 200:
            raise EdinetApiError(status, "Unexpected HTTP status from EDINET.")

    def get_document_list(self, date: str, type_: int = 2) -> dict:
        """One day's document list — EDINET's `date` parameter is a
        single calendar date (`YYYY-MM-DD`), not a range, per Gate 0's
        secondary-source findings; callers needing a lookback window
        must call this once per day (see scan_service.py)."""
        response = self._get(_DOCUMENT_LIST_URL, {"date": date, "type": str(type_)})
        self._check_response(response)
        try:
            return response.json()
        except ValueError as exc:
            raise EdinetParseError("EDINET document-list response was not valid JSON.") from exc

    def fetch_document(self, doc_id: str, type_: int = DOCUMENT_TYPE_ZIP) -> bytes:
        """Raw bytes of one document package for `doc_id`. Format is
        NOT asserted here — could be ZIP, PDF, or CSV depending on
        `type_` (see DOCUMENT_TYPE_* constants) — format detection is
        document_extractor.py's job, one layer up."""
        url = _DOCUMENT_URL_TEMPLATE.format(doc_id=doc_id)
        response = self._get(url, {"type": str(type_)})
        self._check_response(response)
        return response.content

    def fetch_code_list(self, url: str) -> bytes:
        """The official EDINET code-list bulk file (EDINET code <->
        securities code <-> company name mapping). `url` is required as
        an explicit caller-supplied argument rather than a hardcoded
        constant: Gate 0 did not confirm this file's real URL, format,
        or update cadence, so this client deliberately does not embed a
        guessed one — see edinet_code_resolver.py's module docstring
        for the provisional value currently in use and why."""
        response = self._get(url, {})
        self._check_response(response)
        return response.content

    @staticmethod
    def document_index_url(doc_id: str) -> str:
        """A safe, human-navigable reference URL for a document — used
        as FilingEvent.source_url, same role as DartClient.viewer_url /
        EdgarClient.filing_index_url. Points at the same documents/{docID}
        API path (no dedicated public "viewer" page was confirmed in
        Gate 0's research) — provisional, same status as the endpoints
        above."""
        return _DOCUMENT_URL_TEMPLATE.format(doc_id=doc_id)
