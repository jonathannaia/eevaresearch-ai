"""Phase 2B — the process-local TTL cache around the Federal Register fetch.

Fully offline: `requests.get` is always replaced with a counting fake, a
fake monotonic clock drives TTL expiry, and log records are observed
through a handler attached to the client's own logger. No outbound
request, credential, database, or cache service is involved.

Why this cache exists: a production measurement put completed Dashboard
renders at 12.7-14.5s, with a 10.4-11.3s residual outside the page's
declared data load. This synchronous 10-second-timeout GET was the only
network call on that path and ran on every render.
"""
from __future__ import annotations

import logging

import pytest
import requests

from src.data_access.policy_monitor import federal_register_client as client


@pytest.fixture(autouse=True)
def _reset_cache():
    client.reset_cache_for_tests()
    yield
    client.reset_cache_for_tests()


@pytest.fixture
def fake_clock(monkeypatch):
    class _Clock:
        def __init__(self) -> None:
            self.now = 10_000.0

        def __call__(self) -> float:
            return self.now

        def advance_seconds(self, seconds: float) -> None:
            self.now += seconds

    clock = _Clock()
    monkeypatch.setattr(client.time, "monotonic", clock)
    return clock


class _Counter:
    def __init__(self) -> None:
        self.calls = 0


def _install_ok(monkeypatch, payload=None):
    counter = _Counter()
    body = payload if payload is not None else {"results": [{
        "title": "Synthetic Rule", "type": "Rule", "document_number": "2026-00001",
        "html_url": "https://www.federalregister.gov/documents/2026/synthetic",
        "publication_date": "2026-09-24", "agencies": [{"name": "Department of Commerce"}],
    }]}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return body

    def _fake_get(*args, **kwargs):
        counter.calls += 1
        return _Resp()

    monkeypatch.setattr(client.requests, "get", _fake_get)
    return counter


@pytest.fixture
def records(caplog):
    logger = logging.getLogger("eeva.federal_register")
    previous = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(caplog.handler)
    try:
        yield caplog
    finally:
        logger.removeHandler(caplog.handler)
        logger.setLevel(previous)


def _text(records) -> str:
    return "\n".join(r.getMessage() for r in records.records)


# --- one request across repeated calls within the TTL ----------------------

def test_repeated_calls_within_the_ttl_perform_exactly_one_request(fake_clock, monkeypatch):
    counter = _install_ok(monkeypatch)

    for _ in range(10):
        client.fetch_candidate_documents(per_page=20)

    assert counter.calls == 1


def test_a_cache_hit_returns_the_identical_result(fake_clock, monkeypatch):
    _install_ok(monkeypatch)

    first = client.fetch_candidate_documents(per_page=20)
    second = client.fetch_candidate_documents(per_page=20)

    assert second == first
    assert second.documents == first.documents
    assert second.failure_code is None


def test_calls_just_before_the_tti_boundary_still_hit(fake_clock, monkeypatch):
    counter = _install_ok(monkeypatch)
    client.fetch_candidate_documents(per_page=20)

    fake_clock.advance_seconds(client.FEDERAL_REGISTER_CACHE_TTL_SECONDS - 1)
    client.fetch_candidate_documents(per_page=20)

    assert counter.calls == 1


# --- expiry causes exactly one renewed request -----------------------------

def test_expiry_causes_exactly_one_renewed_request(fake_clock, monkeypatch):
    counter = _install_ok(monkeypatch)
    client.fetch_candidate_documents(per_page=20)

    fake_clock.advance_seconds(client.FEDERAL_REGISTER_CACHE_TTL_SECONDS + 1)
    for _ in range(5):
        client.fetch_candidate_documents(per_page=20)

    assert counter.calls == 2  # the original, plus exactly one renewal


def test_a_different_per_page_is_treated_as_a_miss(fake_clock, monkeypatch):
    counter = _install_ok(monkeypatch)

    client.fetch_candidate_documents(per_page=20)
    client.fetch_candidate_documents(per_page=50)

    assert counter.calls == 2


# --- cache-miss success retains the result shape ---------------------------

def test_cache_miss_success_retains_the_full_result_shape(fake_clock, monkeypatch):
    _install_ok(monkeypatch)

    result = client.fetch_candidate_documents(per_page=20)

    assert isinstance(result, client.FederalRegisterFetchResult)
    assert result.failure_code is None
    assert len(result.documents) == 1
    document = result.documents[0]
    assert document.title == "Synthetic Rule"
    assert document.document_number == "2026-00001"
    assert document.html_url == "https://www.federalregister.gov/documents/2026/synthetic"
    assert document.publication_date == "2026-09-24"
    assert document.agency_names == ("Department of Commerce",)


def test_an_empty_result_set_is_preserved_and_cached(fake_clock, monkeypatch):
    counter = _install_ok(monkeypatch, payload={"results": []})

    first = client.fetch_candidate_documents(per_page=20)
    second = client.fetch_candidate_documents(per_page=20)

    assert first.documents == ()
    assert first.failure_code is None
    assert second == first
    assert counter.calls == 1


# --- failure / timeout still degrades safely -------------------------------

@pytest.mark.parametrize(
    "exception", [requests.Timeout("slow"), requests.ConnectionError("refused"), requests.RequestException("x")],
)
def test_failure_yields_the_existing_empty_fallback_without_raising(fake_clock, monkeypatch, exception):
    def _raise(*args, **kwargs):
        raise exception

    monkeypatch.setattr(client.requests, "get", _raise)

    result = client.fetch_candidate_documents(per_page=20)  # must not raise

    assert result.documents == ()
    assert result.failure_code is not None


def test_a_failure_is_cached_so_a_stalled_endpoint_is_not_retried_every_render(fake_clock, monkeypatch):
    counter = _Counter()

    def _raise(*args, **kwargs):
        counter.calls += 1
        raise requests.Timeout("slow")

    monkeypatch.setattr(client.requests, "get", _raise)

    for _ in range(6):
        result = client.fetch_candidate_documents(per_page=20)
        assert result.documents == ()

    assert counter.calls == 1


def test_after_expiry_a_previously_failing_endpoint_is_retried_and_can_recover(fake_clock, monkeypatch):
    monkeypatch.setattr(client.requests, "get", lambda *a, **kw: (_ for _ in ()).throw(requests.Timeout("slow")))
    assert client.fetch_candidate_documents(per_page=20).failure_code is not None

    fake_clock.advance_seconds(client.FEDERAL_REGISTER_CACHE_TTL_SECONDS + 1)
    _install_ok(monkeypatch)

    recovered = client.fetch_candidate_documents(per_page=20)
    assert recovered.failure_code is None
    assert len(recovered.documents) == 1


# --- logging: one record per real fetch, never per hit, never sensitive ----

def test_one_log_record_per_actual_fetch_and_none_on_cache_hits(fake_clock, monkeypatch, records):
    _install_ok(monkeypatch)

    for _ in range(5):
        client.fetch_candidate_documents(per_page=20)

    fetch_records = [r for r in records.records if "federal_register_fetch" in r.getMessage()]
    assert len(fetch_records) == 1
    assert 'outcome="completed"' in fetch_records[0].getMessage()
    assert "result_count=1" in fetch_records[0].getMessage()
    assert "elapsed_ms=" in fetch_records[0].getMessage()


def test_a_failed_fetch_logs_failure_kind_only(fake_clock, monkeypatch, records):
    monkeypatch.setattr(
        client.requests, "get",
        lambda *a, **kw: (_ for _ in ()).throw(requests.Timeout("secret-host-detail-in-message")),
    )

    client.fetch_candidate_documents(per_page=20)

    text = _text(records)
    assert 'outcome="failed"' in text
    assert "failure_kind=" in text
    assert "result_count=0" in text
    assert "secret-host-detail-in-message" not in text


def test_logs_never_contain_the_url_query_or_exception_payload(fake_clock, monkeypatch, records):
    _install_ok(monkeypatch)
    client.fetch_candidate_documents(per_page=20)
    fake_clock.advance_seconds(client.FEDERAL_REGISTER_CACHE_TTL_SECONDS + 1)
    monkeypatch.setattr(
        client.requests, "get",
        lambda *a, **kw: (_ for _ in ()).throw(requests.ConnectionError("https://leak.invalid/secret?token=abc")),
    )
    client.fetch_candidate_documents(per_page=20)

    text = _text(records)
    for forbidden in (
        "federalregister.gov", "documents.json", "https://", "http://", "per_page", "fields[]",
        "token=", "leak.invalid", "User-Agent", "Traceback",
    ):
        assert forbidden not in text, forbidden


# --- constants and scope ---------------------------------------------------

def test_ttl_constant_is_named_and_positive():
    assert isinstance(client.FEDERAL_REGISTER_CACHE_TTL_SECONDS, int)
    assert client.FEDERAL_REGISTER_CACHE_TTL_SECONDS > 0


def test_the_ten_second_timeout_is_deliberately_unchanged_in_this_phase():
    """Phase 2B does not touch the timeout: whether it should be shorter
    is an evidence question the new fetch logging is meant to answer."""
    assert client._TIMEOUT_SECONDS == 10
