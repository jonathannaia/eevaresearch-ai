"""EdinetClient — fully mocked, zero network calls, no credential value
required to pass. Covers the config-error requirement, the
query-parameter (not header) credential transport, 401/403/404/429/5xx/
timeout handling, the throttle, and the format-agnostic document fetch."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from src.data_access.edinet.client import (
    DEFAULT_MIN_INTERVAL_SECONDS,
    DOCUMENT_TYPE_PDF,
    DOCUMENT_TYPE_ZIP,
    EdinetClient,
)
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


def _mock_response(status_code: int = 200, json_payload=None, content: bytes = b""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    if json_payload is not None:
        resp.json.return_value = json_payload
    else:
        resp.json.side_effect = ValueError("no json body")
    return resp


def _client(subscription_key: str | None = "test-key", session=None) -> EdinetClient:
    return EdinetClient(subscription_key, session=session or MagicMock(), min_interval_seconds=0)


def test_missing_subscription_key_raises_config_error():
    client = _client(subscription_key=None)
    with pytest.raises(EdinetConfigError):
        client.get_document_list("2026-08-17")


def test_empty_subscription_key_raises_config_error():
    client = _client(subscription_key="")
    with pytest.raises(EdinetConfigError):
        client.get_document_list("2026-08-17")


def test_get_document_list_returns_parsed_json():
    session = MagicMock()
    session.get.return_value = _mock_response(json_payload={"results": []})
    client = _client(session=session)

    result = client.get_document_list("2026-08-17")

    assert result == {"results": []}


def test_credential_is_sent_as_a_query_parameter_not_a_header():
    # Gate 0's confirmed distinction from EdgarClient's header-based
    # User-Agent transport — EDINET's Subscription-Key is a query param.
    session = MagicMock()
    session.get.return_value = _mock_response(json_payload={})
    client = _client(subscription_key="my-secret-key", session=session)

    client.get_document_list("2026-08-17")

    _, kwargs = session.get.call_args
    assert kwargs["params"]["Subscription-Key"] == "my-secret-key"
    assert "headers" not in kwargs or "Subscription-Key" not in (kwargs.get("headers") or {})


def test_get_document_list_sends_date_and_type_params():
    session = MagicMock()
    session.get.return_value = _mock_response(json_payload={})
    client = _client(session=session)

    client.get_document_list("2026-08-17", type_=1)

    _, kwargs = session.get.call_args
    assert kwargs["params"]["date"] == "2026-08-17"
    assert kwargs["params"]["type"] == "1"


def test_get_document_list_401_raises_unauthorized_not_config_error():
    session = MagicMock()
    session.get.return_value = _mock_response(status_code=401)
    client = _client(session=session)

    with pytest.raises(EdinetUnauthorizedError):
        client.get_document_list("2026-08-17")


def test_get_document_list_403_raises_forbidden():
    session = MagicMock()
    session.get.return_value = _mock_response(status_code=403)
    client = _client(session=session)

    with pytest.raises(EdinetForbiddenError):
        client.get_document_list("2026-08-17")


def test_get_document_list_404_raises_not_found():
    session = MagicMock()
    session.get.return_value = _mock_response(status_code=404)
    client = _client(session=session)

    with pytest.raises(EdinetNotFoundError):
        client.get_document_list("2026-08-17")


def test_get_document_list_429_raises_rate_limit():
    session = MagicMock()
    session.get.return_value = _mock_response(status_code=429)
    client = _client(session=session)

    with pytest.raises(EdinetRateLimitError):
        client.get_document_list("2026-08-17")


def test_get_document_list_500_raises_api_error():
    session = MagicMock()
    session.get.return_value = _mock_response(status_code=500)
    client = _client(session=session)

    with pytest.raises(EdinetApiError):
        client.get_document_list("2026-08-17")


def test_get_document_list_malformed_json_raises_parse_error():
    session = MagicMock()
    session.get.return_value = _mock_response(status_code=200, content=b"not json")
    client = _client(session=session)

    with pytest.raises(EdinetParseError):
        client.get_document_list("2026-08-17")


def test_timeout_raises_edinet_timeout_error():
    session = MagicMock()
    session.get.side_effect = requests.Timeout("timed out")
    client = _client(session=session)

    with pytest.raises(EdinetTimeoutError):
        client.get_document_list("2026-08-17")


def test_network_error_raises_edinet_api_error():
    session = MagicMock()
    session.get.side_effect = requests.ConnectionError("dns failure")
    client = _client(session=session)

    with pytest.raises(EdinetApiError):
        client.get_document_list("2026-08-17")


def test_fetch_document_returns_raw_bytes_regardless_of_format():
    # Format-agnostic per Gate 1's explicit requirement — this client
    # never asserts ZIP vs PDF vs CSV, just hands back what came back.
    session = MagicMock()
    session.get.return_value = _mock_response(status_code=200, content=b"\x50\x4b\x03\x04binary-zip-like-bytes")
    client = _client(session=session)

    result = client.fetch_document("S100ABCD", type_=DOCUMENT_TYPE_ZIP)

    assert result == b"\x50\x4b\x03\x04binary-zip-like-bytes"


def test_fetch_document_sends_the_requested_type_param():
    session = MagicMock()
    session.get.return_value = _mock_response(status_code=200, content=b"%PDF-1.4 fake pdf bytes")
    client = _client(session=session)

    client.fetch_document("S100ABCD", type_=DOCUMENT_TYPE_PDF)

    _, kwargs = session.get.call_args
    assert kwargs["params"]["type"] == str(DOCUMENT_TYPE_PDF)


def test_fetch_code_list_requires_an_explicit_url_argument():
    # No hardcoded default is silently trusted — url must be passed
    # explicitly by the caller (see edinet_code_resolver.py).
    session = MagicMock()
    session.get.return_value = _mock_response(status_code=200, content=b"zip bytes")
    client = _client(session=session)

    result = client.fetch_code_list("https://example.invalid/codelist.zip")

    assert result == b"zip bytes"
    called_url = session.get.call_args[0][0]
    assert called_url == "https://example.invalid/codelist.zip"


def test_fetch_code_list_propagates_typed_errors_like_any_other_call():
    session = MagicMock()
    session.get.return_value = _mock_response(status_code=404)
    client = _client(session=session)

    with pytest.raises(EdinetNotFoundError):
        client.fetch_code_list("https://example.invalid/missing.zip")


def test_throttle_enforces_minimum_interval(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("src.data_access.edinet.client.time.sleep", lambda s: sleeps.append(s))

    times = iter([100.0, 100.1, 100.1])
    monkeypatch.setattr("src.data_access.edinet.client.time.monotonic", lambda: next(times))

    session = MagicMock()
    session.get.return_value = _mock_response(json_payload={})
    client = EdinetClient("test-key", session=session, min_interval_seconds=DEFAULT_MIN_INTERVAL_SECONDS)

    client.get_document_list("2026-08-17")
    client.get_document_list("2026-08-17")
    assert sleeps and sleeps[0] == pytest.approx(DEFAULT_MIN_INTERVAL_SECONDS - 0.1)


def test_document_index_url_is_a_safe_public_provenance_link():
    url = EdinetClient.document_index_url("S100ABCD")
    assert url == "https://api.edinet-fsa.go.jp/api/v2/documents/S100ABCD"
