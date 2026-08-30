"""canonical_url.validate_canonical_url — pure function, no I/O."""
from __future__ import annotations

from src.data_access.daily_news.canonical_url import validate_canonical_url

_DOMAINS = ("nvidianews.nvidia.com", "blogs.nvidia.com")
_FEED_URL = "https://nvidianews.nvidia.com/releases.xml"


def test_valid_direct_item_url_is_accepted():
    url = "https://nvidianews.nvidia.com/news/nvidia-announces-something-1234"
    assert validate_canonical_url(url, _DOMAINS, _FEED_URL)


def test_valid_secondary_allowed_domain_is_accepted():
    url = "https://blogs.nvidia.com/blog/some-post/"
    assert validate_canonical_url(url, _DOMAINS, _FEED_URL)


def test_empty_url_is_rejected():
    assert not validate_canonical_url("", _DOMAINS, _FEED_URL)


def test_whitespace_only_url_is_rejected():
    assert not validate_canonical_url("   ", _DOMAINS, _FEED_URL)


def test_http_scheme_is_rejected_https_required():
    url = "http://nvidianews.nvidia.com/news/some-release"
    assert not validate_canonical_url(url, _DOMAINS, _FEED_URL)


def test_off_domain_url_is_rejected():
    url = "https://prnewswire.com/news/some-release"
    assert not validate_canonical_url(url, _DOMAINS, _FEED_URL)


def test_bare_homepage_is_rejected():
    assert not validate_canonical_url("https://nvidianews.nvidia.com/", _DOMAINS, _FEED_URL)
    assert not validate_canonical_url("https://nvidianews.nvidia.com", _DOMAINS, _FEED_URL)


def test_the_feed_url_itself_is_rejected():
    assert not validate_canonical_url(_FEED_URL, _DOMAINS, _FEED_URL)


def test_search_result_page_is_rejected():
    url = "https://nvidianews.nvidia.com/search?q=gpu"
    assert not validate_canonical_url(url, _DOMAINS, _FEED_URL)


def test_search_path_segment_is_rejected():
    url = "https://nvidianews.nvidia.com/search/results"
    assert not validate_canonical_url(url, _DOMAINS, _FEED_URL)


def test_word_containing_search_substring_is_not_falsely_rejected():
    # Regression: a real AMD press release slug contains "research" as a
    # substring of a normal word, which a naive "search" in url check
    # would incorrectly reject — confirmed live during implementation.
    url = "https://ir.amd.com/news-events/press-releases/detail/1288/amd-commits-up-to-2-billion-to-accelerate-ai-innovation-and-research-in-the-united-kingdom"
    assert validate_canonical_url(url, ("ir.amd.com",), "https://ir.amd.com/news-events/press-releases/rss")


def test_malformed_url_with_no_scheme_is_rejected():
    assert not validate_canonical_url("nvidianews.nvidia.com/news/some-release", _DOMAINS, _FEED_URL)


def test_redirector_style_off_domain_query_param_is_rejected_by_domain_check():
    url = "https://bit.ly/xyz?url=https://nvidianews.nvidia.com/news/real-release"
    assert not validate_canonical_url(url, _DOMAINS, _FEED_URL)
