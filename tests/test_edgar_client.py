"""EdgarClient — fully mocked, zero network calls, no User-Agent value
required to pass. Covers the User-Agent/config-error requirement,
403/429/timeout handling, the throttle, and the CIK/accession-number
URL-construction helpers."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from src.data_access.edgar.client import (
    DEFAULT_MIN_INTERVAL_SECONDS,
    EdgarClient,
    accession_no_dashes,
    cik_for_archive_url,
    normalize_cik,
)
from src.data_access.edgar.errors import (
    EdgarApiError,
    EdgarConfigError,
    EdgarForbiddenError,
    EdgarParseError,
    EdgarRateLimitError,
    EdgarTimeoutError,
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


def _client(user_agent: str | None = "EevaResearch test@example.com", session=None) -> EdgarClient:
    return EdgarClient(user_agent, session=session or MagicMock(), min_interval_seconds=0)


def test_missing_user_agent_raises_config_error():
    client = _client(user_agent=None)
    with pytest.raises(EdgarConfigError):
        client.get_submissions("0001045810")


def test_get_submissions_returns_parsed_json():
    session = MagicMock()
    session.get.return_value = _mock_response(json_payload={"cik": 1045810, "tickers": ["NVDA"]})
    client = _client(session=session)

    result = client.get_submissions("0001045810")

    assert result == {"cik": 1045810, "tickers": ["NVDA"]}


def test_every_request_sends_the_configured_user_agent():
    session = MagicMock()
    session.get.return_value = _mock_response(json_payload={})
    client = _client(user_agent="EevaResearch contact@example.com", session=session)

    client.get_submissions("0001045810")

    _, kwargs = session.get.call_args
    assert kwargs["headers"] == {"User-Agent": "EevaResearch contact@example.com"}


def test_get_submissions_403_raises_forbidden_not_config_error():
    session = MagicMock()
    session.get.return_value = _mock_response(status_code=403)
    client = _client(session=session)

    with pytest.raises(EdgarForbiddenError):
        client.get_submissions("0001045810")


def test_get_submissions_429_raises_rate_limit():
    session = MagicMock()
    session.get.return_value = _mock_response(status_code=429)
    client = _client(session=session)

    with pytest.raises(EdgarRateLimitError):
        client.get_submissions("0001045810")


def test_get_submissions_other_non_200_raises_api_error():
    session = MagicMock()
    session.get.return_value = _mock_response(status_code=500)
    client = _client(session=session)

    with pytest.raises(EdgarApiError):
        client.get_submissions("0001045810")


def test_get_submissions_malformed_json_raises_parse_error():
    session = MagicMock()
    session.get.return_value = _mock_response(status_code=200, content=b"not json")
    client = _client(session=session)

    with pytest.raises(EdgarParseError):
        client.get_submissions("0001045810")


def test_timeout_raises_edgar_timeout_error():
    session = MagicMock()
    session.get.side_effect = requests.Timeout("timed out")
    client = _client(session=session)

    with pytest.raises(EdgarTimeoutError):
        client.get_submissions("0001045810")


def test_network_error_raises_edgar_api_error():
    session = MagicMock()
    session.get.side_effect = requests.ConnectionError("dns failure")
    client = _client(session=session)

    with pytest.raises(EdgarApiError):
        client.get_submissions("0001045810")


def test_fetch_company_tickers_returns_parsed_json():
    session = MagicMock()
    session.get.return_value = _mock_response(json_payload={"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"}})
    client = _client(session=session)

    result = client.fetch_company_tickers()

    assert result["0"]["ticker"] == "NVDA"


def test_fetch_document_returns_raw_bytes():
    session = MagicMock()
    session.get.return_value = _mock_response(status_code=200, content=b"<html>filing text</html>")
    client = _client(session=session)

    result = client.fetch_document("0001045810", "0001045810-26-000001", "nvda-8k.htm")

    assert result == b"<html>filing text</html>"


def test_fetch_document_archive_url_uses_no_leading_zero_cik_and_no_dash_accession():
    session = MagicMock()
    session.get.return_value = _mock_response(status_code=200, content=b"ok")
    client = _client(session=session)

    client.fetch_document("0001045810", "0001045810-26-000001", "nvda-8k.htm")

    called_url = session.get.call_args[0][0]
    assert called_url == "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000001/nvda-8k.htm"


def test_throttle_enforces_minimum_interval(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("src.data_access.edgar.client.time.sleep", lambda s: sleeps.append(s))

    # _throttle() calls monotonic() once on the first request (to seed
    # _last_request_at) and twice on the second (elapsed check, then to
    # re-seed after sleeping) — 3 values needed for 2 client calls.
    times = iter([100.0, 100.1, 100.1])
    monkeypatch.setattr("src.data_access.edgar.client.time.monotonic", lambda: next(times))

    session = MagicMock()
    session.get.return_value = _mock_response(json_payload={})
    client = EdgarClient("EevaResearch test@example.com", session=session, min_interval_seconds=DEFAULT_MIN_INTERVAL_SECONDS)

    client.get_submissions("0001045810")
    # First call: _last_request_at is None, no sleep yet, but monotonic() consumed once (100.0)
    client.get_submissions("0001045810")
    # Second call: elapsed = 100.1 - 100.0 = 0.1s < 0.5s min interval -> must sleep the remainder
    assert sleeps and sleeps[0] == pytest.approx(DEFAULT_MIN_INTERVAL_SECONDS - 0.1)


def test_filing_index_url_is_a_safe_public_provenance_link():
    url = EdgarClient.filing_index_url("0001045810", "0001045810-26-000001")
    assert url == "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000001/"


def test_normalize_cik_zero_pads_to_ten_digits():
    assert normalize_cik("1045810") == "0001045810"
    assert normalize_cik("0001045810") == "0001045810"


def test_cik_for_archive_url_strips_leading_zeros():
    assert cik_for_archive_url("0001045810") == "1045810"


def test_accession_no_dashes_strips_dashes_only():
    assert accession_no_dashes("0001045810-26-000001") == "000104581026000001"
