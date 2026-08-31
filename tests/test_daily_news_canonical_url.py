"""canonical_url.validate_canonical_url — pure function, no I/O."""
from __future__ import annotations

from src.data_access.daily_news.canonical_url import validate_canonical_url, validate_image_url

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


# --- validate_image_url — separate, stricter, exact-hostname-only gate ---


def test_nvidia_approved_image_host_is_accepted():
    assert validate_image_url("https://iprsoftwaremedia.com/photo.jpg", "iprsoftwaremedia.com")


def test_sk_hynix_approved_image_host_is_accepted():
    assert validate_image_url("https://d18r0a86za96sg.cloudfront.net/image.png", "d18r0a86za96sg.cloudfront.net")


def test_cisco_approved_image_host_is_accepted():
    assert validate_image_url("https://newsroom.cisco.com/image.jpg", "newsroom.cisco.com")


def test_none_image_host_fails_closed_even_for_a_plausible_url():
    assert not validate_image_url("https://iprsoftwaremedia.com/photo.jpg", None)


def test_none_url_is_rejected():
    assert not validate_image_url(None, "iprsoftwaremedia.com")


def test_empty_url_is_rejected_for_image():
    assert not validate_image_url("", "iprsoftwaremedia.com")


def test_http_scheme_is_rejected_for_image_https_required():
    assert not validate_image_url("http://iprsoftwaremedia.com/photo.jpg", "iprsoftwaremedia.com")


def test_data_uri_is_rejected_for_image():
    assert not validate_image_url("data:image/png;base64,iVBORw0KGgo=", "iprsoftwaremedia.com")


def test_blob_uri_is_rejected_for_image():
    assert not validate_image_url("blob:https://example.com/abc-123", "iprsoftwaremedia.com")


def test_file_uri_is_rejected_for_image():
    assert not validate_image_url("file:///etc/passwd", "iprsoftwaremedia.com")


def test_relative_url_is_rejected_for_image():
    assert not validate_image_url("/photo.jpg", "iprsoftwaremedia.com")


def test_credentialed_url_is_rejected_for_image():
    assert not validate_image_url("https://user:pass@iprsoftwaremedia.com/photo.jpg", "iprsoftwaremedia.com")


def test_bare_parent_domain_is_rejected_for_image():
    assert not validate_image_url("https://cloudfront.net/image.png", "d18r0a86za96sg.cloudfront.net")


def test_other_subdomain_of_the_same_parent_is_rejected_for_image():
    assert not validate_image_url("https://other12345.cloudfront.net/image.png", "d18r0a86za96sg.cloudfront.net")


def test_lookalike_domain_is_rejected_for_image():
    assert not validate_image_url("https://iprsoftwaremedia.com.evil-example.com/photo.jpg", "iprsoftwaremedia.com")


def test_other_companys_approved_image_host_is_rejected_for_a_different_source():
    # Cisco's own image is hosted on newsroom.cisco.com — must not be
    # accepted against NVIDIA's separately-approved image_host.
    assert not validate_image_url("https://newsroom.cisco.com/photo.jpg", "iprsoftwaremedia.com")


def test_all_image_candidates_for_a_source_with_no_approved_host_are_rejected():
    # A source with image_host=None (every pilot feed except NVIDIA/SK
    # Hynix/Cisco) must fail closed for every candidate, including ones
    # that would be valid elsewhere.
    for url in (
        "https://iprsoftwaremedia.com/photo.jpg",
        "https://d18r0a86za96sg.cloudfront.net/image.png",
        "https://newsroom.cisco.com/image.jpg",
    ):
        assert not validate_image_url(url, None)
