"""rss_atom_client.fetch_entries — mocked HTTP via requests_mock-style
monkeypatching of requests.get, zero real network calls. Covers RSS 2.0,
Atom, malformed content, and network-failure isolation."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from src.data_access.daily_news import rss_atom_client

_RSS_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Example Newsroom</title>
<item>
  <title>Example Announces Something</title>
  <link>https://example.com/news/example-announces-something</link>
  <description>Example did a thing today.</description>
  <pubDate>Mon, 24 Aug 2026 12:00:00 GMT</pubDate>
</item>
</channel></rss>"""

_ATOM_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Example Newsroom</title>
<entry>
  <title>Example Ships a New Product</title>
  <link href="https://example.com/news/example-ships-a-new-product"/>
  <summary>Example shipped a new product this week.</summary>
  <updated>2026-08-24T12:00:00Z</updated>
</entry>
</feed>"""

_MALFORMED_FIXTURE = b"this is not xml at all, just plain text >>> <<<"


def _mock_response(content: bytes, status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.content = content
    response.status_code = status_code
    response.raise_for_status = MagicMock()
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(f"{status_code} error")
    return response


def test_valid_rss_feed_is_parsed(monkeypatch):
    monkeypatch.setattr(rss_atom_client.requests, "get", lambda *a, **k: _mock_response(_RSS_FIXTURE))

    result = rss_atom_client.fetch_entries("https://example.com/rss")

    assert result.failure_code is None
    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.title == "Example Announces Something"
    assert entry.link == "https://example.com/news/example-announces-something"
    assert entry.summary == "Example did a thing today."
    assert entry.published_at.startswith("2026-08-24")


def test_valid_atom_feed_is_parsed(monkeypatch):
    monkeypatch.setattr(rss_atom_client.requests, "get", lambda *a, **k: _mock_response(_ATOM_FIXTURE))

    result = rss_atom_client.fetch_entries("https://example.com/atom")

    assert result.failure_code is None
    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.title == "Example Ships a New Product"
    assert entry.link == "https://example.com/news/example-ships-a-new-product"
    assert entry.summary == "Example shipped a new product this week."


def test_malformed_feed_content_is_isolated_as_a_sanitized_failure(monkeypatch):
    monkeypatch.setattr(rss_atom_client.requests, "get", lambda *a, **k: _mock_response(_MALFORMED_FIXTURE))

    result = rss_atom_client.fetch_entries("https://example.com/broken")

    assert result.entries == ()
    assert result.failure_code == "MalformedFeed"


def test_network_error_is_isolated_as_a_sanitized_failure_code(monkeypatch):
    def _raise(*args, **kwargs):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(rss_atom_client.requests, "get", _raise)

    result = rss_atom_client.fetch_entries("https://example.com/rss")

    assert result.entries == ()
    assert result.failure_code == "ConnectionError"


def test_http_error_status_is_isolated_as_a_sanitized_failure_code(monkeypatch):
    monkeypatch.setattr(rss_atom_client.requests, "get", lambda *a, **k: _mock_response(b"", status_code=404))

    result = rss_atom_client.fetch_entries("https://example.com/missing.xml")

    assert result.entries == ()
    assert result.failure_code == "HTTPError"


def test_timeout_is_isolated_as_a_sanitized_failure_code(monkeypatch):
    def _raise(*args, **kwargs):
        raise requests.Timeout("timed out")

    monkeypatch.setattr(rss_atom_client.requests, "get", _raise)

    result = rss_atom_client.fetch_entries("https://example.com/rss")

    assert result.failure_code == "Timeout"


def test_feed_url_ending_in_json_still_parses_as_rss_when_content_is_rss(monkeypatch):
    # Cisco's own registered feed URL ends in ".json" (design/DECISIONS.md)
    # even though its response body is genuine RSS 2.0 XML — parsing must
    # be driven entirely by the response content (feedparser), never by
    # the URL's own file extension.
    monkeypatch.setattr(rss_atom_client.requests, "get", lambda *a, **k: _mock_response(_RSS_FIXTURE))

    result = rss_atom_client.fetch_entries("https://newsroom.cisco.com/c/services/i/servlets/newsroom/rssfeed.json?feed=press-releases")

    assert result.failure_code is None
    assert len(result.entries) == 1
    assert result.entries[0].title == "Example Announces Something"
