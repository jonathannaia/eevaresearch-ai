"""src.logic.source_link.public_source_url — pure function, no I/O.
EDINET-safety fix (design/DECISIONS.md): proves the shared helper every
source-link rendering surface now uses rewrites a raw, key-required
EDINET API URL to the public disclosure portal root, and leaves every
other URL — including any non-EDINET https:// source — completely
unchanged."""
from __future__ import annotations

from src.logic.source_link import EDINET_PUBLIC_PORTAL_URL, public_source_url


def test_edinet_api_document_url_is_rewritten_to_the_public_portal_root():
    assert public_source_url("https://api.edinet-fsa.go.jp/api/v2/documents/S100Z0OT") == EDINET_PUBLIC_PORTAL_URL


def test_edinet_api_document_list_url_is_also_rewritten():
    assert public_source_url("https://api.edinet-fsa.go.jp/api/v2/documents.json") == EDINET_PUBLIC_PORTAL_URL


def test_edinet_api_url_with_a_query_string_is_still_rewritten():
    assert public_source_url("https://api.edinet-fsa.go.jp/api/v2/documents/S100Z0OT?type=2") == EDINET_PUBLIC_PORTAL_URL


def test_portal_root_constant_is_the_exact_public_disclosure_portal():
    assert EDINET_PUBLIC_PORTAL_URL == "https://disclosure2.edinet-fsa.go.jp/"


def test_edgar_url_passes_through_unchanged():
    url = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000078/nvda-8k.htm"
    assert public_source_url(url) == url


def test_dart_url_passes_through_unchanged():
    url = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260812000001"
    assert public_source_url(url) == url


def test_daily_news_publisher_url_passes_through_unchanged():
    url = "https://nvidianews.nvidia.com/news/results"
    assert public_source_url(url) == url


def test_edinet_disclosure_portal_url_itself_passes_through_unchanged():
    """The public portal URL is not itself the api.edinet-fsa.go.jp host,
    so it is never rewritten a second time — idempotent."""
    assert public_source_url(EDINET_PUBLIC_PORTAL_URL) == EDINET_PUBLIC_PORTAL_URL


def test_edinet_api_host_match_is_case_insensitive():
    """Some existing callers pass the original, non-lowercased URL
    through after their own case-insensitive scheme check — this must
    still be caught regardless of casing."""
    assert public_source_url("HTTPS://API.EDINET-FSA.GO.JP/api/v2/documents/S100Z0OT") == EDINET_PUBLIC_PORTAL_URL
    assert public_source_url("Https://Api.Edinet-Fsa.Go.Jp/api/v2/documents/S100Z0OT") == EDINET_PUBLIC_PORTAL_URL


def test_none_input_returns_none():
    assert public_source_url(None) is None


def test_empty_string_input_returns_empty_string():
    assert public_source_url("") == ""


def test_result_of_a_prior_rewrite_is_never_reinterpreted_as_unsafe():
    """Applying the helper twice in a row (e.g. if a caller accidentally
    double-wraps) must never produce anything other than the same safe
    portal root — no double-rewrite, no error."""
    once = public_source_url("https://api.edinet-fsa.go.jp/api/v2/documents/S100Z0OT")
    twice = public_source_url(once)
    assert once == twice == EDINET_PUBLIC_PORTAL_URL
