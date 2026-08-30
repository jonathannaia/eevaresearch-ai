"""edgar.document_service.get_or_fetch_excerpt — fully mocked EdgarClient,
zero network calls. Covers caching/idempotency, retry+backoff-with-jitter
on transient failures, and terminal failure states (missing primary
document / 403 / 429 / timeout) that must not retry forever."""
from __future__ import annotations

from unittest.mock import MagicMock

from src.data_access.edgar import document_service
from src.data_access.edgar.errors import EdgarApiError, EdgarForbiddenError, EdgarRateLimitError, EdgarTimeoutError
from src.models.models import ExtractionState


def _client(fetch_result) -> MagicMock:
    client = MagicMock()
    if isinstance(fetch_result, Exception):
        client.fetch_document.side_effect = fetch_result
    else:
        client.fetch_document.return_value = fetch_result
    return client


def test_successful_fetch_extracts_and_caches(tmp_path):
    client = _client(b"<html><body><p>Item 2.02 Results of Operations.</p></body></html>")

    result = document_service.get_or_fetch_excerpt(client, "0001045810", "0001045810-26-000001", "nvda-8k.htm", tmp_path)

    assert result.state == ExtractionState.EXTRACTED
    assert result.from_cache is False
    assert "Item 2.02" in result.excerpt_original


def test_repeated_call_uses_cache_not_a_second_network_call(tmp_path):
    client = _client(b"<html><body><p>Item 2.02 Results.</p></body></html>")

    first = document_service.get_or_fetch_excerpt(client, "0001045810", "0001045810-26-000001", "nvda-8k.htm", tmp_path)
    second = document_service.get_or_fetch_excerpt(client, "0001045810", "0001045810-26-000001", "nvda-8k.htm", tmp_path)

    assert first.from_cache is False
    assert second.from_cache is True
    assert client.fetch_document.call_count == 1


def test_missing_primary_document_404_style_is_retrieval_failed(tmp_path):
    # A missing/unavailable document surfaces as a non-200 -> EdgarApiError.
    client = _client(EdgarApiError(404, "not found"))

    result = document_service.get_or_fetch_excerpt(client, "0001045810", "0001045810-26-000001", "missing.htm", tmp_path)

    assert result.state == ExtractionState.RETRIEVAL_FAILED
    assert result.from_cache is False


def test_forbidden_403_is_retrieval_failed_not_retried(tmp_path):
    client = _client(EdgarForbiddenError(403, "forbidden"))

    result = document_service.get_or_fetch_excerpt(client, "0001045810", "0001045810-26-000001", "doc.htm", tmp_path)

    assert result.state == ExtractionState.RETRIEVAL_FAILED
    assert client.fetch_document.call_count == 1  # 403 is not one of the retried transient errors


def test_rate_limit_429_retries_then_succeeds(monkeypatch, tmp_path):
    monkeypatch.setattr(document_service.time, "sleep", lambda s: None)
    monkeypatch.setattr(document_service.random, "uniform", lambda a, b: 0)
    client = MagicMock()
    client.fetch_document.side_effect = [EdgarRateLimitError(429, "rate limited"), b"<html><body><p>ok content</p></body></html>"]

    result = document_service.get_or_fetch_excerpt(client, "0001045810", "0001045810-26-000001", "doc.htm", tmp_path)

    assert result.state == ExtractionState.EXTRACTED
    assert client.fetch_document.call_count == 2


def test_rate_limit_exhausts_retries_and_defers_as_retrieval_failed(monkeypatch, tmp_path):
    monkeypatch.setattr(document_service.time, "sleep", lambda s: None)
    monkeypatch.setattr(document_service.random, "uniform", lambda a, b: 0)
    client = _client(EdgarRateLimitError(429, "still rate limited"))

    result = document_service.get_or_fetch_excerpt(client, "0001045810", "0001045810-26-000001", "doc.htm", tmp_path)

    assert result.state == ExtractionState.RETRIEVAL_FAILED
    assert client.fetch_document.call_count == document_service._MAX_RETRIES + 1


def test_timeout_retries_with_bounded_exponential_backoff_and_jitter(monkeypatch, tmp_path):
    sleeps = []
    monkeypatch.setattr(document_service.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(document_service.random, "uniform", lambda a, b: 0.25)
    client = MagicMock()
    client.fetch_document.side_effect = [EdgarTimeoutError("t1"), EdgarTimeoutError("t2"), b"<html><body><p>ok</p></body></html>"]

    result = document_service.get_or_fetch_excerpt(client, "0001045810", "0001045810-26-000001", "doc.htm", tmp_path)

    assert result.state == ExtractionState.EXTRACTED
    assert len(sleeps) == 2
    # Exponential: base=1.0 * 2^0 + jitter, then base * 2^1 + jitter
    assert sleeps[0] == 1.0 + 0.25
    assert sleeps[1] == 2.0 + 0.25


def test_never_retries_indefinitely(monkeypatch, tmp_path):
    monkeypatch.setattr(document_service.time, "sleep", lambda s: None)
    client = _client(EdgarTimeoutError("always times out"))

    result = document_service.get_or_fetch_excerpt(client, "0001045810", "0001045810-26-000001", "doc.htm", tmp_path)

    assert result.state == ExtractionState.RETRIEVAL_FAILED
    assert client.fetch_document.call_count == document_service._MAX_RETRIES + 1


def test_failed_result_is_also_cached_so_it_is_not_retried_on_next_page_view(tmp_path):
    client = _client(EdgarApiError(404, "not found"))

    document_service.get_or_fetch_excerpt(client, "0001045810", "0001045810-26-000001", "doc.htm", tmp_path)
    second = document_service.get_or_fetch_excerpt(client, "0001045810", "0001045810-26-000001", "doc.htm", tmp_path)

    assert second.from_cache is True
    assert client.fetch_document.call_count == 1


def test_force_refresh_false_still_serves_cached_failure(tmp_path):
    client = _client(EdgarApiError(404, "not found"))
    document_service.get_or_fetch_excerpt(client, "0001045810", "0001045810-26-000001", "doc.htm", tmp_path)

    result = document_service.get_or_fetch_excerpt(
        client, "0001045810", "0001045810-26-000001", "doc.htm", tmp_path, force_refresh=False,
    )

    assert result.from_cache is True
    assert client.fetch_document.call_count == 1


def test_force_refresh_true_bypasses_a_cached_retrieval_failure(tmp_path):
    client = MagicMock()
    client.fetch_document.side_effect = [EdgarApiError(404, "not found"), b"<html><body><p>Item 2.02 ok</p></body></html>"]
    document_service.get_or_fetch_excerpt(client, "0001045810", "0001045810-26-000001", "doc.htm", tmp_path)

    result = document_service.get_or_fetch_excerpt(
        client, "0001045810", "0001045810-26-000001", "doc.htm", tmp_path, force_refresh=True,
    )

    assert result.from_cache is False
    assert result.state == ExtractionState.EXTRACTED
    assert client.fetch_document.call_count == 2


def test_force_refresh_true_still_never_bypasses_a_cached_success(tmp_path):
    client = _client(b"<html><body><p>Item 2.02 Results of Operations.</p></body></html>")
    document_service.get_or_fetch_excerpt(client, "0001045810", "0001045810-26-000001", "nvda-8k.htm", tmp_path)

    result = document_service.get_or_fetch_excerpt(
        client, "0001045810", "0001045810-26-000001", "nvda-8k.htm", tmp_path, force_refresh=True,
    )

    assert result.from_cache is True
    assert client.fetch_document.call_count == 1
