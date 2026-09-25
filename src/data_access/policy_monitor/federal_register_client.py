"""Federal Register policy-monitor pilot — read-time, in-memory client
for the Federal Register's own public documents.json API. No API key,
no write call, no persistence beyond a small process-local TTL cache (see
FEDERAL_REGISTER_CACHE_TTL_SECONDS): a cache miss is a single bounded
GET (design/DECISIONS.md, Federal Register Policy Monitor Pilot).

Deliberately outside src/data_access/daily_news/ — this is a distinct
policy surface, not a Daily News source. Zero import of anything under
src.data_access.daily_news or src.models.daily_news_models.

Endpoint and field set live-verified this batch (direct fetch of
https://www.federalregister.gov/api/v1/documents.json): the response is
a JSON object with a top-level `results` list; each result's `agencies`
field is itself a list of objects, each carrying (among other fields) a
`name` string — only `name` is read here. Only the six metadata fields
this pilot actually needs are requested via `fields[]`
(title/type/document_number/html_url/publication_date/agencies) — never
`abstract`/body text, and never `pdf_url` (that field resolves to
govinfo.gov, a different official domain this project has already
chosen not to treat as an approved item domain for this source — see
src/data_access/daily_news/source_candidates.py's own
federal-register-api record). No `conditions[...]` server-side filter is
applied here — agency/keyword/type/theme qualification is entirely
src.logic.policy_monitor.federal_register_matching's job, working
against a small, unfiltered, newest-first candidate set.

Never raises: any network failure, timeout, non-200 response, or
malformed JSON body all produce an empty result with a sanitized
failure_code (mirrors src/data_access/daily_news/rss_atom_client.py's
own fetch_entries()/_failure_code() convention exactly, reimplemented
here rather than imported, to keep this module fully decoupled from
anything under src.data_access.daily_news)."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

from src.ui.render_timing import get_logger

_DOCUMENTS_ENDPOINT = "https://www.federalregister.gov/api/v1/documents.json"
_TIMEOUT_SECONDS = 10

# Phase 2B — process-local TTL cache around the fetch+parse result.
#
# Before this, every Dashboard render issued a fresh blocking GET here.
# A production measurement put completed Dashboard renders at 12.7-14.5s
# with a 10.4-11.3s residual outside the page's declared data load, and
# this is the only network call on that path — a synchronous request
# carrying a 10-second timeout.
#
# Unit: SECONDS. The freshness tradeoff is deliberate. The Federal
# Register publishes on a daily cadence, so a policy document is not
# meaningfully less useful for being up to this many seconds old, and in
# exchange the page stops paying a blocking round trip on every rerun —
# including reruns caused by an unrelated widget interaction. Raise it to
# cut requests further; lower it only if freshness ever matters more than
# render latency here.
#
# The 10-second timeout above is deliberately UNCHANGED in this phase.
# Whether it should be shorter is an evidence question, and the fetch
# logging added below is what produces that evidence: a separate PR can
# decide once real outcome/elapsed_ms records exist.
FEDERAL_REGISTER_CACHE_TTL_SECONDS = 900

_CACHE_LOCK = threading.Lock()
_cache_entry: "tuple[float, FederalRegisterFetchResult] | None" = None
_cached_per_page: int | None = None

_LOGGER = get_logger("eeva.federal_register")
_USER_AGENT = "EevaResearch-PolicyMonitor/1.0"
_REQUESTED_FIELDS = ("title", "type", "document_number", "html_url", "publication_date", "agencies")


@dataclass(frozen=True)
class FederalRegisterDocument:
    """One raw parsed result row — deliberately unfiltered and
    unvalidated beyond basic type/shape safety; every qualification
    decision belongs to federal_register_matching.py, not here."""

    title: str | None
    type: str | None
    document_number: str | None
    html_url: str | None  # only ever the official html_url field, never pdf_url
    publication_date: str | None  # "YYYY-MM-DD" as returned — date-only, no time-of-day component
    agency_names: tuple[str, ...]


@dataclass(frozen=True)
class FederalRegisterFetchResult:
    documents: tuple[FederalRegisterDocument, ...]
    failure_code: str | None  # sanitized: exception class name, "HTTPError:<status>", or "MalformedResponse"


def _failure_code(exc: requests.RequestException) -> str:
    if isinstance(exc, requests.HTTPError):
        status_code = getattr(exc.response, "status_code", None)
        if isinstance(status_code, int):
            return f"HTTPError:{status_code}"
    return type(exc).__name__


def _is_https_url(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(parsed.netloc)


def _clean_str(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _parse_agency_names(raw_agencies: object) -> tuple[str, ...]:
    if not isinstance(raw_agencies, list):
        return ()
    names: list[str] = []
    for agency in raw_agencies:
        if isinstance(agency, dict):
            name = _clean_str(agency.get("name"))
            if name is not None:
                names.append(name)
    return tuple(names)


def _parse_document(raw: object) -> FederalRegisterDocument | None:
    if not isinstance(raw, dict):
        return None
    html_url = raw.get("html_url")
    return FederalRegisterDocument(
        title=_clean_str(raw.get("title")),
        type=_clean_str(raw.get("type")),
        document_number=_clean_str(raw.get("document_number")),
        html_url=html_url if _is_https_url(html_url) else None,
        publication_date=_clean_str(raw.get("publication_date")),
        agency_names=_parse_agency_names(raw.get("agencies")),
    )


def _fetch_candidate_documents_uncached(per_page: int = 20) -> FederalRegisterFetchResult:
    """One bounded, unfiltered, newest-first GET against the Federal
    Register's public documents.json endpoint. `per_page` is
    deliberately small — federal_register_matching.py's own strict,
    approved agency+keyword gate is expected to suppress most of what
    comes back, so there is no need to request a large page."""
    params = {
        "per_page": per_page,
        "order": "newest",
        "fields[]": list(_REQUESTED_FIELDS),
    }
    try:
        response = requests.get(
            _DOCUMENTS_ENDPOINT, params=params, timeout=_TIMEOUT_SECONDS, headers={"User-Agent": _USER_AGENT},
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        return FederalRegisterFetchResult(documents=(), failure_code=_failure_code(exc))

    try:
        payload = response.json()
    except ValueError:
        return FederalRegisterFetchResult(documents=(), failure_code="MalformedResponse")

    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return FederalRegisterFetchResult(documents=(), failure_code="MalformedResponse")

    documents = tuple(
        doc for doc in (_parse_document(item) for item in payload["results"]) if doc is not None
    )
    return FederalRegisterFetchResult(documents=documents, failure_code=None)


def fetch_candidate_documents(per_page: int = 20) -> FederalRegisterFetchResult:
    """Cached entry point — the one every caller uses.

    Performs at most one real outbound request per
    FEDERAL_REGISTER_CACHE_TTL_SECONDS per process. A cache hit returns
    the previously parsed result unchanged: same dataclass, same
    documents, same html_url links, same empty-result and failure
    semantics, so nothing downstream can tell a hit from a miss.

    A different `per_page` than the cached one is treated as a miss, so
    a caller asking for more rows never silently receives fewer.

    Exception safety is inherited from the uncached fetch, which never
    raises: a failure result is cached like any other, deliberately, so a
    network problem cannot turn every render back into a blocking
    10-second attempt. The next attempt happens after the TTL."""
    global _cache_entry, _cached_per_page

    now = time.monotonic()
    with _CACHE_LOCK:
        entry = _cache_entry
        if (
            entry is not None
            and _cached_per_page == per_page
            and (now - entry[0]) < FEDERAL_REGISTER_CACHE_TTL_SECONDS
        ):
            return entry[1]

    started_at = time.monotonic()
    result = _fetch_candidate_documents_uncached(per_page=per_page)
    elapsed_ms = (time.monotonic() - started_at) * 1000

    # One record per ACTUAL outbound fetch, never per cache hit. Carries
    # only an outcome, a duration, a count, and — on failure — the
    # already-sanitized failure code. Never the endpoint URL, the query
    # parameters, a response body, or an exception message.
    if result.failure_code is None:
        _LOGGER.info(
            'event="federal_register_fetch" outcome="completed" elapsed_ms=%.1f result_count=%d',
            elapsed_ms, len(result.documents),
        )
    else:
        _LOGGER.info(
            'event="federal_register_fetch" outcome="failed" elapsed_ms=%.1f result_count=0 failure_kind="%s"',
            elapsed_ms, result.failure_code,
        )

    with _CACHE_LOCK:
        _cache_entry = (time.monotonic(), result)
        _cached_per_page = per_page
    return result


def reset_cache_for_tests() -> None:
    """Clears the process-local cache. Test-only seam: nothing in the
    application calls this, and it performs no I/O."""
    global _cache_entry, _cached_per_page
    with _CACHE_LOCK:
        _cache_entry = None
        _cached_per_page = None
