"""edinet.document_service.get_or_fetch_excerpt — fully mocked
EdinetClient, zero network calls. Covers caching/idempotency, retry+
backoff-with-jitter on transient failures, and terminal failure states
(404/403/429/timeout) that must not retry forever."""
from __future__ import annotations

from unittest.mock import MagicMock

from src.data_access.edinet import document_service
from src.data_access.edinet.errors import (
    EdinetForbiddenError,
    EdinetNotFoundError,
    EdinetRateLimitError,
    EdinetTimeoutError,
)
from src.models.models import ExtractionState


def _client(fetch_result) -> MagicMock:
    client = MagicMock()
    if isinstance(fetch_result, Exception):
        client.fetch_document.side_effect = fetch_result
    else:
        client.fetch_document.return_value = fetch_result
    return client


def test_successful_fetch_extracts_and_caches(tmp_path):
    client = _client(b"<html><body><p>Disclosure summary text.</p></body></html>")

    result = document_service.get_or_fetch_excerpt(client, "S100TEST1", tmp_path)

    assert result.state == ExtractionState.EXTRACTED
    assert result.from_cache is False
    assert "Disclosure summary" in result.excerpt_original


def test_repeated_call_uses_cache_not_a_second_network_call(tmp_path):
    client = _client(b"<html><body><p>Disclosure summary.</p></body></html>")

    first = document_service.get_or_fetch_excerpt(client, "S100TEST1", tmp_path)
    second = document_service.get_or_fetch_excerpt(client, "S100TEST1", tmp_path)

    assert first.from_cache is False
    assert second.from_cache is True
    assert client.fetch_document.call_count == 1


def test_missing_document_404_is_retrieval_failed(tmp_path):
    client = _client(EdinetNotFoundError(404, "not found"))

    result = document_service.get_or_fetch_excerpt(client, "S100MISSING", tmp_path)

    assert result.state == ExtractionState.RETRIEVAL_FAILED
    assert result.from_cache is False


def test_forbidden_403_is_retrieval_failed_not_retried(tmp_path):
    client = _client(EdinetForbiddenError(403, "forbidden"))

    result = document_service.get_or_fetch_excerpt(client, "S100FORBID", tmp_path)

    assert result.state == ExtractionState.RETRIEVAL_FAILED
    assert client.fetch_document.call_count == 1  # 403 is not one of the retried transient errors


def test_rate_limit_429_retries_then_succeeds(monkeypatch, tmp_path):
    monkeypatch.setattr(document_service.time, "sleep", lambda s: None)
    monkeypatch.setattr(document_service.random, "uniform", lambda a, b: 0)
    client = MagicMock()
    client.fetch_document.side_effect = [EdinetRateLimitError(429, "rate limited"), b"<html><body><p>ok content</p></body></html>"]

    result = document_service.get_or_fetch_excerpt(client, "S100RETRY", tmp_path)

    assert result.state == ExtractionState.EXTRACTED
    assert client.fetch_document.call_count == 2


def test_rate_limit_exhausts_retries_and_ends_as_retrieval_failed(monkeypatch, tmp_path):
    monkeypatch.setattr(document_service.time, "sleep", lambda s: None)
    monkeypatch.setattr(document_service.random, "uniform", lambda a, b: 0)
    client = _client(EdinetRateLimitError(429, "still rate limited"))

    result = document_service.get_or_fetch_excerpt(client, "S100RATELIMIT", tmp_path)

    assert result.state == ExtractionState.RETRIEVAL_FAILED
    assert client.fetch_document.call_count == document_service._MAX_RETRIES + 1


def test_timeout_retries_with_bounded_exponential_backoff_and_jitter(monkeypatch, tmp_path):
    sleeps = []
    monkeypatch.setattr(document_service.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(document_service.random, "uniform", lambda a, b: 0.25)
    client = MagicMock()
    client.fetch_document.side_effect = [EdinetTimeoutError("t1"), EdinetTimeoutError("t2"), b"<html><body><p>ok</p></body></html>"]

    result = document_service.get_or_fetch_excerpt(client, "S100TIMEOUT", tmp_path)

    assert result.state == ExtractionState.EXTRACTED
    assert len(sleeps) == 2
    assert sleeps[0] == 1.0 + 0.25
    assert sleeps[1] == 2.0 + 0.25


def test_never_retries_indefinitely(monkeypatch, tmp_path):
    monkeypatch.setattr(document_service.time, "sleep", lambda s: None)
    client = _client(EdinetTimeoutError("always times out"))

    result = document_service.get_or_fetch_excerpt(client, "S100ALWAYSTIMEOUT", tmp_path)

    assert result.state == ExtractionState.RETRIEVAL_FAILED
    assert client.fetch_document.call_count == document_service._MAX_RETRIES + 1


def test_failed_result_is_also_cached_so_it_is_not_retried_on_next_page_view(tmp_path):
    client = _client(EdinetNotFoundError(404, "not found"))

    document_service.get_or_fetch_excerpt(client, "S100NOTFOUND", tmp_path)
    second = document_service.get_or_fetch_excerpt(client, "S100NOTFOUND", tmp_path)

    assert second.from_cache is True
    assert client.fetch_document.call_count == 1


def test_default_type_requests_the_zip_format():
    from src.data_access.edinet.client import DOCUMENT_TYPE_ZIP
    client = MagicMock()
    client.fetch_document.return_value = b"<html><body><p>content</p></body></html>"

    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        document_service.get_or_fetch_excerpt(client, "S100TYPE", Path(d))

    client.fetch_document.assert_called_once_with("S100TYPE", DOCUMENT_TYPE_ZIP)


def test_corrupt_zip_content_fails_closed_not_a_crash(tmp_path):
    # ZIP magic bytes followed by garbage is not a valid archive (no real
    # end-of-central-directory record) — Phase 2, Step 1's bounded ZIP
    # extraction path (document_extractor.py) correctly identifies this
    # as a corrupt/malformed archive (PARSE_FAILED), not merely "ZIP
    # format is unsupported" (the pre-Phase-2 behavior this test used to
    # assert). Either way, the service layer must never crash.
    client = _client(b"\x50\x4b\x03\x04" + bytes(range(200)))

    result = document_service.get_or_fetch_excerpt(client, "S100BINARY", tmp_path)

    assert result.state == ExtractionState.PARSE_FAILED
    assert result.from_cache is False


# --- Gate 10.A: a PDF-shaped fetch must never persist raw bytes ---

def _minimal_pdf(text: str = "Cached evidence text.") -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> /MediaBox [0 0 612 792] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream_content = f"BT /F1 24 Tf 100 700 Td ({text}) Tj ET".encode("latin-1")
    objects.append(b"<< /Length " + str(len(stream_content)).encode() + b" >>\nstream\n" + stream_content + b"\nendstream")
    pdf = b"%PDF-1.4\n"
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_offset = len(pdf)
    pdf += f"xref\n0 {len(objects) + 1}\n".encode()
    pdf += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        pdf += f"{off:010d} 00000 n \n".encode()
    pdf += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode()
    return pdf


def test_pdf_fetch_extracts_and_caches_text_only(tmp_path):
    client = _client(_minimal_pdf("Real evidence sentence for the cache."))

    result = document_service.get_or_fetch_excerpt(client, "S100PDF", tmp_path)

    assert result.state == ExtractionState.EXTRACTED
    assert "Real evidence sentence" in result.excerpt_original


def test_pdf_raw_bytes_are_never_written_to_the_cache_file(tmp_path):
    pdf_bytes = _minimal_pdf("Should never appear as raw bytes on disk.")
    client = _client(pdf_bytes)

    document_service.get_or_fetch_excerpt(client, "S100PDF", tmp_path)

    cache_path = tmp_path / "edinet_document_excerpts.json"
    raw_cache_text = cache_path.read_text(encoding="utf-8")
    assert "%PDF-" not in raw_cache_text  # no PDF magic bytes/structure persisted
    assert "endobj" not in raw_cache_text  # no raw PDF syntax persisted
    import json
    cached = json.loads(raw_cache_text)["S100PDF"]
    assert set(cached.keys()) == {"state", "excerpt_original", "detail", "retrieved_at", "evidence_source_member", "location_section"}
    assert isinstance(cached["excerpt_original"], str)
    assert cached["evidence_source_member"] is None  # bare PDF, no ZIP container to name a member from


# --- Evidence-packet foundation, Phase 2, Step 2: ZIP-member provenance ---

def _zip_with_pdf(member_name: str, text: str) -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(member_name, _minimal_pdf(text))
    return buffer.getvalue()


def test_zip_sourced_fetch_threads_evidence_source_member_through_fresh_fetch(tmp_path):
    client = _client(_zip_with_pdf("PublicDoc/0101.pdf", "ZIP-sourced evidence."))

    result = document_service.get_or_fetch_excerpt(client, "S100ZIP", tmp_path)

    assert result.state == ExtractionState.EXTRACTED
    assert result.evidence_source_member == "PublicDoc/0101.pdf"


def test_zip_sourced_evidence_source_member_survives_cache_round_trip(tmp_path):
    client = _client(_zip_with_pdf("PublicDoc/0101.pdf", "ZIP-sourced evidence."))

    document_service.get_or_fetch_excerpt(client, "S100ZIP", tmp_path)
    cached_result = document_service.get_or_fetch_excerpt(client, "S100ZIP", tmp_path)

    assert cached_result.from_cache is True
    assert cached_result.evidence_source_member == "PublicDoc/0101.pdf"


# ============================================================
# Category-aware annual-report cache namespacing (C-a .. C-e). Every
# archive is synthetic and built in-test; the EdinetClient is a MagicMock
# and no network, EDINET, database, or external call occurs. No live
# filing content is copied or committed.
# ============================================================

_ANNUAL = "annual_securities_report"
_ANNUAL_KEY_SUFFIX = "#v2-annual-section-anchored"
_CACHE_FILE = "edinet_document_excerpts.json"

_ISSUER_SPECIFIC_ZIP_BODY = (
    "第2【事業の状況】 【生産、受注及び販売の状況】 "
    "当連結会計年度の受注高は前期比32.4%増の1,284億円となりました。"
)
_BOILERPLATE_ZIP_BODY = (
    "第5【経理の状況】 1. 連結財務諸表及び財務諸表の作成方法について 規則に基づき作成しております。"
)


def _zip_with_honbun(body: str, name: str = "XBRL/PublicDoc/0102010_honbun_a.htm") -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, f"<html><body><p>{body}</p></body></html>")
    return buffer.getvalue()


def _raw_cache(tmp_path) -> dict:
    import json

    return json.loads((tmp_path / _CACHE_FILE).read_text(encoding="utf-8"))


# --- C-a: annual namespaced miss then hit -------------------------------

def test_c_a1_annual_fetch_writes_under_the_namespaced_key(tmp_path):
    client = _client(_zip_with_honbun(_ISSUER_SPECIFIC_ZIP_BODY))

    result = document_service.get_or_fetch_excerpt(client, "S100ANN", tmp_path, category=_ANNUAL)

    assert result.from_cache is False
    assert result.location_section == "【生産、受注及び販売の状況】"
    assert set(_raw_cache(tmp_path)) == {f"S100ANN{_ANNUAL_KEY_SUFFIX}"}


def test_c_a2_second_annual_call_is_a_cache_hit_with_no_second_fetch(tmp_path):
    client = _client(_zip_with_honbun(_ISSUER_SPECIFIC_ZIP_BODY))

    document_service.get_or_fetch_excerpt(client, "S100ANN", tmp_path, category=_ANNUAL)
    second = document_service.get_or_fetch_excerpt(client, "S100ANN", tmp_path, category=_ANNUAL)

    assert second.from_cache is True
    assert client.fetch_document.call_count == 1


# --- C-b: a legacy bare entry is not reused and is left byte-identical ---

def test_c_b_legacy_bare_annual_entry_is_not_reused_and_is_left_byte_identical(tmp_path):
    legacy_client = _client(_zip_with_honbun(_BOILERPLATE_ZIP_BODY, "XBRL/PublicDoc/0105010_honbun_b.htm"))
    document_service.get_or_fetch_excerpt(legacy_client, "S100ANN", tmp_path)  # category=None, bare key
    legacy_bytes = (tmp_path / _CACHE_FILE).read_bytes()
    legacy_record = _raw_cache(tmp_path)["S100ANN"]

    annual_client = _client(_zip_with_honbun(_ISSUER_SPECIFIC_ZIP_BODY))
    result = document_service.get_or_fetch_excerpt(annual_client, "S100ANN", tmp_path, category=_ANNUAL)

    # The legacy entry was a MISS for the annual category: a real fetch happened.
    assert result.from_cache is False
    assert annual_client.fetch_document.call_count == 1
    assert result.location_section == "【生産、受注及び販売の状況】"
    # ...and the legacy record itself is untouched, still present, unmutated.
    after = _raw_cache(tmp_path)
    assert after["S100ANN"] == legacy_record
    assert "連結財務諸表及び財務諸表の作成方法について" in after["S100ANN"]["excerpt_original"]
    assert set(after) == {"S100ANN", f"S100ANN{_ANNUAL_KEY_SUFFIX}"}
    assert legacy_bytes != (tmp_path / _CACHE_FILE).read_bytes()  # appended, not rewritten in place


def test_c_b_legacy_bare_entry_still_serves_a_category_none_call_unchanged(tmp_path):
    legacy_client = _client(_zip_with_honbun(_BOILERPLATE_ZIP_BODY, "XBRL/PublicDoc/0105010_honbun_b.htm"))
    document_service.get_or_fetch_excerpt(legacy_client, "S100ANN", tmp_path)
    document_service.get_or_fetch_excerpt(_client(_zip_with_honbun(_ISSUER_SPECIFIC_ZIP_BODY)), "S100ANN", tmp_path, category=_ANNUAL)

    replay = document_service.get_or_fetch_excerpt(legacy_client, "S100ANN", tmp_path)

    assert replay.from_cache is True
    assert legacy_client.fetch_document.call_count == 1
    assert "連結財務諸表及び財務諸表の作成方法について" in replay.excerpt_original


# --- C-c / C-d: non-annual and category=None keep the bare key ----------

def test_c_c_non_annual_category_uses_the_bare_key_and_is_unchanged(tmp_path):
    client = _client(_zip_with_honbun(_ISSUER_SPECIFIC_ZIP_BODY))

    result = document_service.get_or_fetch_excerpt(client, "S100BUY", tmp_path, category="share_buyback_status")

    assert set(_raw_cache(tmp_path)) == {"S100BUY"}
    assert result.location_section is None


def test_c_c_non_annual_and_category_none_share_one_cache_entry(tmp_path):
    client = _client(_zip_with_honbun(_ISSUER_SPECIFIC_ZIP_BODY))

    document_service.get_or_fetch_excerpt(client, "S100BUY", tmp_path, category="share_buyback_status")
    hit = document_service.get_or_fetch_excerpt(client, "S100BUY", tmp_path)

    assert hit.from_cache is True
    assert client.fetch_document.call_count == 1
    assert set(_raw_cache(tmp_path)) == {"S100BUY"}


def test_c_d_category_none_behavior_is_unchanged(tmp_path):
    client = _client(b"<html><body><p>Disclosure summary text.</p></body></html>")

    first = document_service.get_or_fetch_excerpt(client, "S100TEST1", tmp_path)
    second = document_service.get_or_fetch_excerpt(client, "S100TEST1", tmp_path)

    assert first.state == ExtractionState.EXTRACTED
    assert second.from_cache is True
    assert client.fetch_document.call_count == 1
    assert set(_raw_cache(tmp_path)) == {"S100TEST1"}
    assert first.location_section is None


def test_cache_key_helper_is_the_single_construction_point(tmp_path):
    assert document_service._cache_key("S100X", _ANNUAL) == f"S100X{_ANNUAL_KEY_SUFFIX}"
    assert document_service._cache_key("S100X", None) == "S100X"
    assert document_service._cache_key("S100X", "share_buyback_status") == "S100X"
    assert document_service._cache_key("S100X", "extraordinary_report") == "S100X"


# --- C-e: location_section round-trips ----------------------------------

def test_c_e_location_section_survives_the_cache_round_trip(tmp_path):
    client = _client(_zip_with_honbun(_ISSUER_SPECIFIC_ZIP_BODY))

    document_service.get_or_fetch_excerpt(client, "S100ANN", tmp_path, category=_ANNUAL)
    cached = document_service.get_or_fetch_excerpt(client, "S100ANN", tmp_path, category=_ANNUAL)

    assert cached.from_cache is True
    assert cached.location_section == "【生産、受注及び販売の状況】"
    assert cached.evidence_source_member == "XBRL/PublicDoc/0102010_honbun_a.htm"


def test_annual_failure_result_is_also_namespaced_and_not_retried(tmp_path):
    client = _client(EdinetNotFoundError(404, "not found"))

    document_service.get_or_fetch_excerpt(client, "S100GONE", tmp_path, category=_ANNUAL)
    second = document_service.get_or_fetch_excerpt(client, "S100GONE", tmp_path, category=_ANNUAL)

    assert second.state == ExtractionState.RETRIEVAL_FAILED
    assert second.from_cache is True
    assert client.fetch_document.call_count == 1
    assert set(_raw_cache(tmp_path)) == {f"S100GONE{_ANNUAL_KEY_SUFFIX}"}


def test_no_cache_file_is_ever_deleted_or_emptied(tmp_path):
    client = _client(_zip_with_honbun(_ISSUER_SPECIFIC_ZIP_BODY))
    for category in (None, "share_buyback_status", _ANNUAL, "extraordinary_report"):
        document_service.get_or_fetch_excerpt(client, "S100MULTI", tmp_path, category=category)

    assert set(_raw_cache(tmp_path)) == {"S100MULTI", f"S100MULTI{_ANNUAL_KEY_SUFFIX}"}
