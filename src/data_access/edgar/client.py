"""Thin SEC EDGAR REST client. Every method does exactly one HTTP call
and raises a typed error from errors.py on failure — it never guesses,
retries silently, or returns partial/fabricated data. Retry/backoff for
transient failures lives one layer up (scan_service.py), same separation
DART's own client uses.

Endpoints, the User-Agent/rate-limit policy, the submissions API's
columnar `filings.recent` shape, and the Archives document-URL pattern
were verified via SEC's own published access policy and cross-confirming
technical documentation during the milestone-8 planning pass (SEC.gov
itself 403'd this session's direct-fetch tooling) — not recalled from
memory. See design/DECISIONS.md. The exact company_tickers.json/
submissions JSON key names should get one live confirming pull at first
real use, the same "verify before trusting" step every DART milestone
took before writing production code against a new endpoint.

No API key — EDGAR is fully open — but every request requires a real,
non-empty identifying User-Agent (`"AppName contact@email"`) or SEC
returns 403 and may temporarily block the IP. This client never logs,
prints, or includes that string in any exception message.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import requests

from src.data_access.edgar.errors import (
    EdgarApiError,
    EdgarConfigError,
    EdgarForbiddenError,
    EdgarParseError,
    EdgarRateLimitError,
    EdgarTimeoutError,
)

_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_ARCHIVES_BASE_URL = "https://www.sec.gov/Archives/edgar/data"
_DEFAULT_TIMEOUT_SECONDS = 15
# Pilot default: 2 req/sec, no bursts — well under SEC's published 10/sec
# ceiling (best-practice guidance itself suggests self-limiting to ~8/sec;
# this pilot is deliberately more conservative). Enforced as a blocking
# minimum-interval throttle in this client, so every caller gets it for
# free rather than each layer needing to remember to rate-limit itself.
DEFAULT_MIN_INTERVAL_SECONDS = 0.5


def normalize_cik(raw: str) -> str:
    """Zero-pads to SEC's required 10-digit CIK string for API URLs.
    Never guesses a CIK's value — only reformats one already resolved
    via cik_resolver.py."""
    digits = raw.strip().lstrip("0") or "0"
    return digits.zfill(10)


def cik_for_archive_url(cik: str) -> str:
    """Archive document URLs use the CIK WITHOUT leading zeros — derived
    here only at URL-construction time, never stored (the canonical,
    stored form is always the zero-padded 10-digit CIK)."""
    return str(int(cik))


def accession_no_dashes(accession_no: str) -> str:
    """Archive folder names use the accession number with dashes
    stripped — derived only at URL-construction time. The canonical,
    stored/dedup-key form always keeps the dashes
    (e.g. "0000320193-25-000079")."""
    return accession_no.replace("-", "")


@dataclass(frozen=True)
class TickerCikRecord:
    ticker: str
    cik: str  # zero-padded 10-digit
    title: str


class EdgarClient:
    def __init__(
        self,
        user_agent: str | None,
        session: requests.Session | None = None,
        timeout: int = _DEFAULT_TIMEOUT_SECONDS,
        min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS,
    ) -> None:
        self._user_agent = user_agent
        self._session = session or requests.Session()
        self._timeout = timeout
        self._min_interval = min_interval_seconds
        self._last_request_at: float | None = None

    def _require_user_agent(self) -> str:
        if not self._user_agent:
            raise EdgarConfigError("EDGE_EDGAR_USER_AGENT is not configured.")
        return self._user_agent

    def _throttle(self) -> None:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            remaining = self._min_interval - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_at = time.monotonic()

    def _get(self, url: str) -> requests.Response:
        user_agent = self._require_user_agent()
        self._throttle()
        try:
            return self._session.get(url, headers={"User-Agent": user_agent}, timeout=self._timeout)
        except requests.Timeout as exc:
            raise EdgarTimeoutError(f"EDGAR request timed out after {self._timeout}s.") from exc
        except requests.RequestException as exc:
            raise EdgarApiError(0, "Network error contacting EDGAR.") from exc

    def _check_response(self, response: requests.Response) -> None:
        if response.status_code == 429:
            raise EdgarRateLimitError(429, "EDGAR rate limit exceeded.")
        if response.status_code == 403:
            raise EdgarForbiddenError(403, "EDGAR request forbidden — check User-Agent configuration.")
        if response.status_code != 200:
            raise EdgarApiError(response.status_code, "Unexpected HTTP status from EDGAR.")

    def get_submissions(self, cik: str) -> dict:
        """One company's filing history metadata. `cik` must already be
        the normalized 10-digit form (see normalize_cik) — this method
        does not resolve or guess it."""
        response = self._get(_SUBMISSIONS_URL.format(cik=cik))
        self._check_response(response)
        try:
            return response.json()
        except ValueError as exc:
            raise EdgarParseError("EDGAR submissions response was not valid JSON.") from exc

    def fetch_company_tickers(self) -> dict:
        """SEC's official bulk ticker->CIK mapping file. Callers should
        cache the result (see cik_resolver.py) rather than call this per
        lookup — it's not scoped by company."""
        response = self._get(_COMPANY_TICKERS_URL)
        self._check_response(response)
        try:
            return response.json()
        except ValueError as exc:
            raise EdgarParseError("EDGAR company_tickers.json response was not valid JSON.") from exc

    def fetch_document(self, cik: str, accession_no: str, filename: str) -> bytes:
        """Raw bytes of one named primary document from one filing.
        `cik`/`accession_no` are the canonical stored forms (zero-padded
        CIK, dashed accession number) — the no-leading-zero/no-dash forms
        the Archives URL needs are derived here only, never stored."""
        url = f"{_ARCHIVES_BASE_URL}/{cik_for_archive_url(cik)}/{accession_no_dashes(accession_no)}/{filename}"
        response = self._get(url)
        self._check_response(response)
        return response.content

    @staticmethod
    def filing_index_url(cik: str, accession_no: str) -> str:
        """A safe, real, public provenance link to the filing's index
        page — used as FilingEvent.source_url, same role as
        DartClient.viewer_url."""
        return f"{_ARCHIVES_BASE_URL}/{cik_for_archive_url(cik)}/{accession_no_dashes(accession_no)}/"
