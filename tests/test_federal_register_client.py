"""federal_register_client.fetch_candidate_documents — mocked HTTP via
unittest.mock, zero real network calls, mirroring
tests/test_daily_news_rss_atom_client.py's own mocking convention."""
from __future__ import annotations

from unittest.mock import MagicMock

import requests

from src.data_access.policy_monitor import federal_register_client

_RAW_DOCUMENT = {
    "title": "Export Controls on Advanced Computing Items",
    "type": "Rule",
    "document_number": "2026-18194",
    "html_url": "https://www.federalregister.gov/documents/2026/09/04/2026-18194/export-controls",
    "pdf_url": "https://www.govinfo.gov/content/pkg/FR-2026-09-04/pdf/2026-18194.pdf",
    "abstract": "This rule amends the Export Administration Regulations...",
    "publication_date": "2026-09-04",
    "agencies": [{"name": "Bureau of Industry and Security", "id": 1, "slug": "bureau-of-industry-and-security"}],
}


def _mock_response(payload: object | None = None, status_code: int = 200, raw_content: bytes | None = None) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.raise_for_status = MagicMock()
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(f"{status_code} error", response=response)
    if raw_content is not None:
        response.json = MagicMock(side_effect=ValueError("not JSON"))
    else:
        response.json = MagicMock(return_value=payload)
    return response


def test_requests_the_documented_endpoint_with_bounded_params(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None, headers=None):
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        captured["headers"] = headers
        return _mock_response({"results": []})

    monkeypatch.setattr(federal_register_client.requests, "get", fake_get)

    federal_register_client.fetch_candidate_documents(per_page=20)

    assert captured["url"] == "https://www.federalregister.gov/api/v1/documents.json"
    assert captured["params"]["per_page"] == 20
    assert captured["params"]["order"] == "newest"
    assert captured["params"]["fields[]"] == [
        "title", "type", "document_number", "html_url", "publication_date", "agencies",
    ]
    assert captured["timeout"] == federal_register_client._TIMEOUT_SECONDS
    assert "User-Agent" in captured["headers"]


def test_happy_path_parses_exactly_the_needed_fields(monkeypatch):
    monkeypatch.setattr(
        federal_register_client.requests, "get",
        lambda *a, **kw: _mock_response({"results": [_RAW_DOCUMENT]}),
    )

    result = federal_register_client.fetch_candidate_documents()

    assert result.failure_code is None
    assert len(result.documents) == 1
    doc = result.documents[0]
    assert doc.title == "Export Controls on Advanced Computing Items"
    assert doc.type == "Rule"
    assert doc.document_number == "2026-18194"
    assert doc.html_url == "https://www.federalregister.gov/documents/2026/09/04/2026-18194/export-controls"
    assert doc.publication_date == "2026-09-04"
    assert doc.agency_names == ("Bureau of Industry and Security",)


def test_never_consumes_pdf_url_or_abstract(monkeypatch):
    monkeypatch.setattr(
        federal_register_client.requests, "get",
        lambda *a, **kw: _mock_response({"results": [_RAW_DOCUMENT]}),
    )

    result = federal_register_client.fetch_candidate_documents()

    doc = result.documents[0]
    assert not hasattr(doc, "pdf_url")
    assert not hasattr(doc, "abstract")
    # FederalRegisterDocument is a frozen dataclass with a fixed field
    # set — this is a structural proof, not just an absence-of-attribute
    # check on a dynamic object.
    field_names = {f.name for f in federal_register_client.FederalRegisterDocument.__dataclass_fields__.values()}
    assert field_names == {"title", "type", "document_number", "html_url", "publication_date", "agency_names"}


def test_non_https_html_url_is_dropped_not_trusted(monkeypatch):
    raw = dict(_RAW_DOCUMENT, html_url="http://www.federalregister.gov/documents/insecure")
    monkeypatch.setattr(federal_register_client.requests, "get", lambda *a, **kw: _mock_response({"results": [raw]}))

    result = federal_register_client.fetch_candidate_documents()

    assert result.documents[0].html_url is None


def test_missing_fields_produce_none_not_a_crash(monkeypatch):
    monkeypatch.setattr(
        federal_register_client.requests, "get",
        lambda *a, **kw: _mock_response({"results": [{"title": "Only a title"}]}),
    )

    result = federal_register_client.fetch_candidate_documents()

    assert result.failure_code is None
    doc = result.documents[0]
    assert doc.title == "Only a title"
    assert doc.type is None
    assert doc.document_number is None
    assert doc.html_url is None
    assert doc.publication_date is None
    assert doc.agency_names == ()


def test_malformed_json_fails_closed(monkeypatch):
    monkeypatch.setattr(
        federal_register_client.requests, "get",
        lambda *a, **kw: _mock_response(raw_content=b"not json"),
    )

    result = federal_register_client.fetch_candidate_documents()

    assert result.documents == ()
    assert result.failure_code == "MalformedResponse"


def test_response_missing_results_key_fails_closed(monkeypatch):
    monkeypatch.setattr(federal_register_client.requests, "get", lambda *a, **kw: _mock_response({"count": 0}))

    result = federal_register_client.fetch_candidate_documents()

    assert result.documents == ()
    assert result.failure_code == "MalformedResponse"


def test_non_200_response_fails_closed_with_status_code(monkeypatch):
    monkeypatch.setattr(federal_register_client.requests, "get", lambda *a, **kw: _mock_response(status_code=503))

    result = federal_register_client.fetch_candidate_documents()

    assert result.documents == ()
    assert result.failure_code == "HTTPError:503"


def test_timeout_fails_closed(monkeypatch):
    def raise_timeout(*args, **kwargs):
        raise requests.exceptions.Timeout("timed out")

    monkeypatch.setattr(federal_register_client.requests, "get", raise_timeout)

    result = federal_register_client.fetch_candidate_documents()

    assert result.documents == ()
    assert result.failure_code == "Timeout"


def test_connection_error_fails_closed(monkeypatch):
    def raise_connection_error(*args, **kwargs):
        raise requests.exceptions.ConnectionError("no route")

    monkeypatch.setattr(federal_register_client.requests, "get", raise_connection_error)

    result = federal_register_client.fetch_candidate_documents()

    assert result.documents == ()
    assert result.failure_code == "ConnectionError"


def test_empty_results_list_is_a_valid_empty_response(monkeypatch):
    monkeypatch.setattr(federal_register_client.requests, "get", lambda *a, **kw: _mock_response({"results": []}))

    result = federal_register_client.fetch_candidate_documents()

    assert result.documents == ()
    assert result.failure_code is None
