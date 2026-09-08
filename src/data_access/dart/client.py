"""Thin OpenDART/DART REST client. Every method does exactly one HTTP
call (or one ZIP-of-XML fetch) and raises a typed error from errors.py on
failure — it never guesses, retries silently, or returns partial/
fabricated data, since this app's evidence system depends on every claim
tracing to something real.

Endpoints, parameters, status codes, and the document-zip/viewer-URL
formats below were verified against OpenDART's own documentation
(https://opendart.fss.or.kr/guide/) and real DART URLs during the Korea
radar pilot plan — not recalled from memory. See design/DECISIONS.md.
"""
from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass

import requests

from src.data_access.dart.errors import (
    DartApiError,
    DartConfigError,
    DartParseError,
    DartRateLimitError,
    DartTimeoutError,
)

_BASE_URL = "https://opendart.fss.or.kr/api"
_DEFAULT_TIMEOUT_SECONDS = 15

# DART's own documented status codes (verified against
# https://opendart.fss.or.kr/guide/, not guessed). "000" is the only
# success code; everything else is a typed failure.
_STATUS_MESSAGES = {
    "010": "Unregistered API key",
    "011": "API key disabled",
    "012": "Inaccessible IP address",
    "013": "No data found",
    "014": "Requested file does not exist",
    "020": "Request limit exceeded",
    "021": "Company query limit exceeded",
    "100": "Invalid field value",
    "101": "Improper access",
    "800": "DART system maintenance",
    "900": "Undefined error",
    "901": "Account data retention expired",
}


@dataclass(frozen=True)
class CorpCodeRecord:
    corp_code: str
    corp_name: str
    corp_eng_name: str
    stock_code: str
    modify_date: str


@dataclass(frozen=True)
class DisclosureRecord:
    """One row from DART's disclosure-list ("list.json") response —
    metadata only, never filing text. See fetch_document_zip for the
    actual document."""

    corp_cls: str
    corp_name: str
    corp_code: str
    stock_code: str
    report_nm: str
    rcept_no: str
    flr_nm: str
    rcept_dt: str
    rm: str


class DartClient:
    def __init__(
        self, api_key: str | None, session: requests.Session | None = None, timeout: int = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._session = session or requests.Session()
        self._timeout = timeout

    def _require_key(self) -> str:
        if not self._api_key:
            raise DartConfigError("EDGE_DART_API_KEY is not configured.")
        return self._api_key

    def _get(self, path: str, params: dict) -> requests.Response:
        try:
            return self._session.get(f"{_BASE_URL}/{path}", params=params, timeout=self._timeout)
        except requests.Timeout as exc:
            raise DartTimeoutError(f"DART request to {path} timed out after {self._timeout}s.") from exc
        except requests.RequestException as exc:
            raise DartApiError("network", str(exc)) from exc

    def _check_json_status(self, payload: dict) -> None:
        status = str(payload.get("status", ""))
        if status == "000":
            return
        message = payload.get("message") or _STATUS_MESSAGES.get(status, "Unknown DART error")
        if status == "020":
            raise DartRateLimitError(status, message)
        raise DartApiError(status, message)

    def fetch_all_corp_codes(self) -> list[CorpCodeRecord]:
        """Downloads DART's full corp-code bulk file (all listed and
        unlisted companies, several MB) and parses it. Callers should
        cache the result (see corp_code_resolver.py) rather than call
        this per lookup — it's not scoped by company."""
        key = self._require_key()
        response = self._get("corpCode.xml", {"crtfc_key": key})
        if response.status_code != 200:
            raise DartApiError(str(response.status_code), "Unexpected HTTP status fetching corpCode.xml")
        # DART returns a plain XML error body (not a ZIP) on failure —
        # e.g. an invalid key — rather than a non-200 HTTP status, so a
        # ZIP-signature check is the reliable way to tell success from
        # failure here.
        if not response.content.startswith(b"PK"):
            try:
                root = ET.fromstring(response.content)
                status = root.findtext("status", default="900")
                message = root.findtext("message", default="Unknown DART error")
            except ET.ParseError as exc:
                raise DartParseError("corpCode.xml error response was not valid XML.") from exc
            if status == "020":
                raise DartRateLimitError(status, message)
            raise DartApiError(status, message)
        try:
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                xml_name = next((n for n in archive.namelist() if n.lower().endswith(".xml")), None)
                if xml_name is None:
                    raise DartParseError("corpCode.xml ZIP did not contain an XML file.")
                xml_bytes = archive.read(xml_name)
        except zipfile.BadZipFile as exc:
            raise DartParseError("corpCode.xml response was not a valid ZIP file.") from exc
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as exc:
            raise DartParseError("corpCode.xml's inner XML could not be parsed.") from exc
        return [
            CorpCodeRecord(
                corp_code=(el.findtext("corp_code") or "").strip(),
                corp_name=(el.findtext("corp_name") or "").strip(),
                corp_eng_name=(el.findtext("corp_eng_name") or "").strip(),
                stock_code=(el.findtext("stock_code") or "").strip(),
                modify_date=(el.findtext("modify_date") or "").strip(),
            )
            for el in root.findall("list")
        ]

    def search_disclosures(
        self, corp_code: str, bgn_de: str, end_de: str, page_no: int = 1, page_count: int = 100,
    ) -> tuple[list[DisclosureRecord], int]:
        """Returns (records for this page, total_count). Callers own
        pagination and date-window bounding — this method makes exactly
        one request."""
        key = self._require_key()
        params = {
            "crtfc_key": key, "corp_code": corp_code, "bgn_de": bgn_de, "end_de": end_de,
            "page_no": str(page_no), "page_count": str(min(page_count, 100)),
        }
        response = self._get("list.json", params)
        try:
            payload = response.json()
        except ValueError as exc:
            raise DartParseError("list.json response was not valid JSON.") from exc
        # DART uses status "013" ("no data found") for a search that
        # legitimately matched nothing — a normal, common outcome for a
        # narrow date window, not a failure. Every other non-"000" status
        # still raises via _check_json_status.
        if str(payload.get("status", "")) == "013":
            return [], 0
        self._check_json_status(payload)
        records = [
            DisclosureRecord(
                corp_cls=row.get("corp_cls", ""), corp_name=row.get("corp_name", ""),
                corp_code=row.get("corp_code", ""), stock_code=row.get("stock_code", ""),
                report_nm=row.get("report_nm", ""), rcept_no=row.get("rcept_no", ""),
                flr_nm=row.get("flr_nm", ""), rcept_dt=row.get("rcept_dt", ""), rm=row.get("rm", ""),
            )
            for row in payload.get("list", [])
        ]
        return records, int(payload.get("total_count", len(records)))

    def fetch_document_zip(self, rcept_no: str) -> bytes:
        """Raw filing-document package for `rcept_no` — a ZIP of XML/HTML
        in DART's own format. Parsing/excerpt-extraction is deliberately
        not this client's job: real filings are irregular enough that
        extraction belongs in its own bounded, best-effort module with an
        explicit "couldn't parse this" fallback, not baked into the
        transport layer."""
        key = self._require_key()
        response = self._get("document.xml", {"crtfc_key": key, "rcept_no": rcept_no})
        if response.status_code != 200 or not response.content.startswith(b"PK"):
            raise DartParseError(f"document.xml for rcept_no={rcept_no} was not a valid ZIP.")
        return response.content

    @staticmethod
    def viewer_url(rcept_no: str) -> str:
        """The real, public DART filing-viewer page for a receipt number
        — used as FilingEvent.source_url so every citation links to the
        actual primary source, not just the raw API."""
        return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
