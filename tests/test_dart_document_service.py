"""document_service.get_or_fetch_excerpt — mocked DartClient, on-disk
cache read/write/hit, retrieval-failure states, and retry/backoff. No
network, no API key required."""
from __future__ import annotations

import io
import zipfile
from unittest.mock import MagicMock

from src.data_access.dart import document_service
from src.data_access.dart.errors import DartParseError, DartRateLimitError, DartTimeoutError
from src.models.models import ExtractionState


def _valid_document_zip() -> bytes:
    xml = (
        '<?xml version="1.0" encoding="utf-8"?><DOCUMENT>'
        "<SECTION-1><P>cover</P></SECTION-1>"
        "<SECTION-1><P>신규시설투자등 결정 안내</P></SECTION-1>"
        "</DOCUMENT>"
    ).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("doc.xml", xml)
    return buf.getvalue()


def test_fetches_and_extracts_a_valid_document(tmp_path):
    client = MagicMock()
    client.fetch_document_zip.return_value = _valid_document_zip()

    result = document_service.get_or_fetch_excerpt(client, "20260807000537", tmp_path)

    assert result.state == ExtractionState.EXTRACTED
    assert "신규시설투자등" in result.excerpt_original
    assert result.from_cache is False


def test_second_call_for_same_receipt_number_hits_cache(tmp_path):
    client = MagicMock()
    client.fetch_document_zip.return_value = _valid_document_zip()

    first = document_service.get_or_fetch_excerpt(client, "20260807000537", tmp_path)
    second = document_service.get_or_fetch_excerpt(client, "20260807000537", tmp_path)

    assert first.from_cache is False
    assert second.from_cache is True
    assert second.excerpt_original == first.excerpt_original
    client.fetch_document_zip.assert_called_once()


def test_retrieval_failure_is_recorded_and_cached_not_retried_forever(tmp_path):
    client = MagicMock()
    client.fetch_document_zip.side_effect = DartParseError("document.xml was not a valid ZIP.")

    result = document_service.get_or_fetch_excerpt(client, "20260807000999", tmp_path)
    cached_result = document_service.get_or_fetch_excerpt(client, "20260807000999", tmp_path)

    assert result.state == ExtractionState.RETRIEVAL_FAILED
    assert cached_result.from_cache is True
    client.fetch_document_zip.assert_called_once()  # second call didn't re-fetch


def test_malformed_document_body_produces_a_clear_state_not_a_crash(tmp_path):
    client = MagicMock()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("doc.xml", b"<broken xml")
    client.fetch_document_zip.return_value = buf.getvalue()

    result = document_service.get_or_fetch_excerpt(client, "20260807000001", tmp_path)

    assert result.state == ExtractionState.PARSE_FAILED


def test_retries_on_rate_limit_then_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(document_service.time, "sleep", lambda seconds: None)
    client = MagicMock()
    client.fetch_document_zip.side_effect = [
        DartRateLimitError("020", "요청 제한을 초과하였습니다."),
        _valid_document_zip(),
    ]

    result = document_service.get_or_fetch_excerpt(client, "20260807000002", tmp_path)

    assert result.state == ExtractionState.EXTRACTED
    assert client.fetch_document_zip.call_count == 2


def test_gives_up_after_max_retries_on_persistent_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(document_service.time, "sleep", lambda seconds: None)
    client = MagicMock()
    client.fetch_document_zip.side_effect = DartTimeoutError("timed out")

    result = document_service.get_or_fetch_excerpt(client, "20260807000003", tmp_path)

    assert result.state == ExtractionState.RETRIEVAL_FAILED
    assert client.fetch_document_zip.call_count == document_service._MAX_RETRIES + 1


def test_handles_corrupt_cache_file_without_raising(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "dart_document_excerpts.json").write_text("{not valid json", encoding="utf-8")
    client = MagicMock()
    client.fetch_document_zip.return_value = _valid_document_zip()

    result = document_service.get_or_fetch_excerpt(client, "20260807000004", tmp_path)

    assert result.state == ExtractionState.EXTRACTED


def test_force_refresh_false_still_serves_cached_failure(tmp_path):
    client = MagicMock()
    client.fetch_document_zip.side_effect = DartParseError("bad zip")
    document_service.get_or_fetch_excerpt(client, "20260828001916", tmp_path)

    result = document_service.get_or_fetch_excerpt(client, "20260828001916", tmp_path, force_refresh=False)

    assert result.from_cache is True
    client.fetch_document_zip.assert_called_once()


def test_force_refresh_true_bypasses_a_cached_retrieval_failure(tmp_path):
    client = MagicMock()
    client.fetch_document_zip.side_effect = [DartParseError("bad zip"), _valid_document_zip()]
    document_service.get_or_fetch_excerpt(client, "20260828001916", tmp_path)

    result = document_service.get_or_fetch_excerpt(client, "20260828001916", tmp_path, force_refresh=True)

    assert result.from_cache is False
    assert result.state == ExtractionState.EXTRACTED
    assert client.fetch_document_zip.call_count == 2


def test_force_refresh_true_still_never_bypasses_a_cached_success(tmp_path):
    client = MagicMock()
    client.fetch_document_zip.return_value = _valid_document_zip()
    document_service.get_or_fetch_excerpt(client, "20260807000537", tmp_path)

    result = document_service.get_or_fetch_excerpt(client, "20260807000537", tmp_path, force_refresh=True)

    assert result.from_cache is True
    client.fetch_document_zip.assert_called_once()  # never re-fetched, regardless of force_refresh


def test_force_refresh_true_bypasses_a_cached_parse_failure(tmp_path):
    client = MagicMock()
    broken_buf = io.BytesIO()
    with zipfile.ZipFile(broken_buf, "w") as archive:
        archive.writestr("doc.xml", b"<broken xml")
    client.fetch_document_zip.side_effect = [broken_buf.getvalue(), _valid_document_zip()]
    document_service.get_or_fetch_excerpt(client, "20260807000005", tmp_path)

    result = document_service.get_or_fetch_excerpt(client, "20260807000005", tmp_path, force_refresh=True)

    assert result.from_cache is False
    assert result.state == ExtractionState.EXTRACTED
    assert client.fetch_document_zip.call_count == 2
