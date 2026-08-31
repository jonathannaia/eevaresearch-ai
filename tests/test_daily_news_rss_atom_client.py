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


def test_fetch_entries_only_ever_requests_the_feed_url_itself(monkeypatch):
    # Proves no per-item/linked-article fetch ever happens: exactly one
    # HTTP call, for the feed document itself, regardless of item count.
    calls = []

    def _record_and_respond(url, *a, **k):
        calls.append(url)
        return _mock_response(_RSS_FIXTURE)

    monkeypatch.setattr(rss_atom_client.requests, "get", _record_and_respond)

    rss_atom_client.fetch_entries("https://example.com/rss")

    assert calls == ["https://example.com/rss"]


# --- Richer-content selection (description vs content:encoded) -----------

_RSS_WITH_RICHER_CONTENT_ENCODED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel>
<title>Example Newsroom</title>
<item>
  <title>Example Announces Something</title>
  <link>https://example.com/news/example-announces-something</link>
  <description>Short blurb.</description>
  <content:encoded><![CDATA[<p>A much longer and richer description of the announcement, with real substance.</p>]]></content:encoded>
  <pubDate>Mon, 24 Aug 2026 12:00:00 GMT</pubDate>
</item>
</channel></rss>"""

_RSS_WITH_RICHER_DESCRIPTION = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel>
<title>Example Newsroom</title>
<item>
  <title>Example Announces Something</title>
  <link>https://example.com/news/example-announces-something</link>
  <description>A much longer and richer description of the announcement, with real substance.</description>
  <content:encoded><![CDATA[Short.]]></content:encoded>
  <pubDate>Mon, 24 Aug 2026 12:00:00 GMT</pubDate>
</item>
</channel></rss>"""


def test_richer_content_encoded_is_preferred_over_a_shorter_description(monkeypatch):
    monkeypatch.setattr(rss_atom_client.requests, "get", lambda *a, **k: _mock_response(_RSS_WITH_RICHER_CONTENT_ENCODED))

    result = rss_atom_client.fetch_entries("https://example.com/rss")

    assert "much longer and richer description" in result.entries[0].summary


def test_richer_description_is_preferred_over_a_shorter_content_encoded(monkeypatch):
    monkeypatch.setattr(rss_atom_client.requests, "get", lambda *a, **k: _mock_response(_RSS_WITH_RICHER_DESCRIPTION))

    result = rss_atom_client.fetch_entries("https://example.com/rss")

    assert "much longer and richer description" in result.entries[0].summary


# --- Image extraction: four formats, in priority order --------------------

_RSS_WITH_MEDIA_CONTENT_IMAGE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
<channel>
<title>Example Newsroom</title>
<item>
  <title>Example Announces Something</title>
  <link>https://example.com/news/example-announces-something</link>
  <description>Example did a thing today.</description>
  <media:content url="https://cdn.example.com/photo.jpg" medium="image" type="image/jpeg"/>
  <pubDate>Mon, 24 Aug 2026 12:00:00 GMT</pubDate>
</item>
</channel></rss>"""

_RSS_WITH_MEDIA_THUMBNAIL_IMAGE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
<channel>
<title>Example Newsroom</title>
<item>
  <title>Example Announces Something</title>
  <link>https://example.com/news/example-announces-something</link>
  <description>Example did a thing today.</description>
  <media:thumbnail url="https://cdn.example.com/thumb.jpg"/>
  <pubDate>Mon, 24 Aug 2026 12:00:00 GMT</pubDate>
</item>
</channel></rss>"""

_RSS_WITH_IMAGE_ENCLOSURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>Example Newsroom</title>
<item>
  <title>Example Announces Something</title>
  <link>https://example.com/news/example-announces-something</link>
  <description>Example did a thing today.</description>
  <enclosure url="https://cdn.example.com/enclosure.jpg" type="image/jpeg" length="1000"/>
  <pubDate>Mon, 24 Aug 2026 12:00:00 GMT</pubDate>
</item>
</channel></rss>"""

_RSS_WITH_NON_IMAGE_ENCLOSURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>Example Newsroom</title>
<item>
  <title>Example Announces Something</title>
  <link>https://example.com/news/example-announces-something</link>
  <description>Example did a thing today.</description>
  <enclosure url="https://cdn.example.com/podcast.mp3" type="audio/mpeg" length="1000"/>
  <pubDate>Mon, 24 Aug 2026 12:00:00 GMT</pubDate>
</item>
</channel></rss>"""

_RSS_WITH_EMBEDDED_IMG_IN_DESCRIPTION = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>Example Newsroom</title>
<item>
  <title>Example Announces Something</title>
  <link>https://example.com/news/example-announces-something</link>
  <description><![CDATA[<p>Some text.</p><img src="https://cdn.example.com/embedded.jpg" alt="An embedded photo"/>]]></description>
  <pubDate>Mon, 24 Aug 2026 12:00:00 GMT</pubDate>
</item>
</channel></rss>"""

_RSS_WITH_NO_IMAGE_AT_ALL = _RSS_FIXTURE

_RSS_WITH_CHANNEL_LEVEL_IMAGE_ONLY = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>Example Newsroom</title>
<image><url>https://cdn.example.com/channel-logo.png</url><title>Example Newsroom</title><link>https://example.com</link></image>
<item>
  <title>Example Announces Something</title>
  <link>https://example.com/news/example-announces-something</link>
  <description>Example did a thing today.</description>
  <pubDate>Mon, 24 Aug 2026 12:00:00 GMT</pubDate>
</item>
</channel></rss>"""


def test_media_content_image_is_extracted(monkeypatch):
    monkeypatch.setattr(rss_atom_client.requests, "get", lambda *a, **k: _mock_response(_RSS_WITH_MEDIA_CONTENT_IMAGE))

    result = rss_atom_client.fetch_entries("https://example.com/rss")

    assert result.entries[0].image_url == "https://cdn.example.com/photo.jpg"


def test_media_thumbnail_image_is_extracted_when_no_media_content(monkeypatch):
    monkeypatch.setattr(rss_atom_client.requests, "get", lambda *a, **k: _mock_response(_RSS_WITH_MEDIA_THUMBNAIL_IMAGE))

    result = rss_atom_client.fetch_entries("https://example.com/rss")

    assert result.entries[0].image_url == "https://cdn.example.com/thumb.jpg"


def test_image_typed_enclosure_is_extracted_when_no_media_fields(monkeypatch):
    monkeypatch.setattr(rss_atom_client.requests, "get", lambda *a, **k: _mock_response(_RSS_WITH_IMAGE_ENCLOSURE))

    result = rss_atom_client.fetch_entries("https://example.com/rss")

    assert result.entries[0].image_url == "https://cdn.example.com/enclosure.jpg"


def test_non_image_enclosure_is_not_extracted_as_an_image(monkeypatch):
    monkeypatch.setattr(rss_atom_client.requests, "get", lambda *a, **k: _mock_response(_RSS_WITH_NON_IMAGE_ENCLOSURE))

    result = rss_atom_client.fetch_entries("https://example.com/rss")

    assert result.entries[0].image_url is None


def test_embedded_img_tag_in_description_is_extracted_as_last_resort(monkeypatch):
    monkeypatch.setattr(rss_atom_client.requests, "get", lambda *a, **k: _mock_response(_RSS_WITH_EMBEDDED_IMG_IN_DESCRIPTION))

    result = rss_atom_client.fetch_entries("https://example.com/rss")

    assert result.entries[0].image_url == "https://cdn.example.com/embedded.jpg"
    assert result.entries[0].image_alt == "An embedded photo"


def test_no_image_candidate_anywhere_yields_none(monkeypatch):
    monkeypatch.setattr(rss_atom_client.requests, "get", lambda *a, **k: _mock_response(_RSS_WITH_NO_IMAGE_AT_ALL))

    result = rss_atom_client.fetch_entries("https://example.com/rss")

    assert result.entries[0].image_url is None
    assert result.entries[0].image_alt is None


def test_channel_level_image_logo_is_never_used_as_an_item_image(monkeypatch):
    # A feed's own channel-level <image> (its logo) is structurally
    # separate from any item — must never leak into an item's image_url
    # just because the item itself has no image of its own.
    monkeypatch.setattr(rss_atom_client.requests, "get", lambda *a, **k: _mock_response(_RSS_WITH_CHANNEL_LEVEL_IMAGE_ONLY))

    result = rss_atom_client.fetch_entries("https://example.com/rss")

    assert result.entries[0].image_url is None
