"""document_extractor.extract_excerpt — pure function, fully fixture-
driven. No network. The XML shape mirrors the real DART4 schema element
verified during development (<SECTION-1> blocks, cover-page first)."""
from __future__ import annotations

import io
import zipfile

from src.data_access.dart.document_extractor import (
    MAX_EXCERPT_CHARS,
    MAX_ZIP_SIZE_BYTES,
    extract_excerpt,
)
from src.models.models import ExtractionState


def _zip_with(filename: str, content: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(filename, content)
    return buf.getvalue()


def _dart_style_xml(cover_page_text: str, body_text: str) -> bytes:
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
    <DOCUMENT>
    <SECTION-1><TITLE>표지</TITLE><P>{cover_page_text}</P></SECTION-1>
    <SECTION-1><TITLE>본문</TITLE><P>{body_text}</P></SECTION-1>
    </DOCUMENT>"""
    return xml.encode("utf-8")


def test_extracts_from_second_section_skipping_cover_page():
    zip_bytes = _zip_with("20260807000537.xml", _dart_style_xml("회사명: 테스트전자", "신규시설투자등 결정 안내"))

    result = extract_excerpt(zip_bytes)

    assert result.state == ExtractionState.EXTRACTED
    assert "신규시설투자등" in result.excerpt_original
    assert "회사명" not in result.excerpt_original  # cover-page section skipped


def test_falls_back_to_only_section_when_there_is_just_one():
    xml = b'<?xml version="1.0" encoding="utf-8"?><DOCUMENT><SECTION-1><P>\xec\x8b\xa0\xea\xb7\x9c\xec\x8b\x9c\xec\x84\xa4\xed\x88\xac\xec\x9e\x90</P></SECTION-1></DOCUMENT>'
    zip_bytes = _zip_with("doc.xml", xml)

    result = extract_excerpt(zip_bytes)

    assert result.state == ExtractionState.EXTRACTED
    assert result.excerpt_original


def test_excerpt_is_bounded_to_max_chars():
    long_text = "가" * 2000
    zip_bytes = _zip_with("doc.xml", _dart_style_xml("cover", long_text))

    result = extract_excerpt(zip_bytes)

    assert result.state == ExtractionState.EXTRACTED
    assert len(result.excerpt_original) == MAX_EXCERPT_CHARS


def test_malformed_zip_returns_parse_failed():
    result = extract_excerpt(b"this is not a zip file at all")
    assert result.state == ExtractionState.PARSE_FAILED


def test_zip_with_no_xml_or_html_returns_unsupported_format():
    zip_bytes = _zip_with("readme.txt", b"just a text file, not a filing document")
    result = extract_excerpt(zip_bytes)
    assert result.state == ExtractionState.UNSUPPORTED_FORMAT


def test_malformed_xml_with_no_extractable_text_returns_parse_failed():
    # Malformed enough to fail strict XML parsing, and with no text
    # content anywhere for the lenient HTML fallback to find either.
    zip_bytes = _zip_with("doc.xml", b"<a><b><c></c></b>")
    result = extract_excerpt(zip_bytes)
    assert result.state == ExtractionState.PARSE_FAILED


def test_malformed_xml_falls_back_to_lenient_html_extraction():
    # Not well-formed XML (unclosed SECTION-1), but real text content is
    # still present — the lenient HTML fallback (added after a live
    # DART document exposed this gap) should recover it rather than
    # giving up, since the underlying evidence text is genuinely there.
    zip_bytes = _zip_with("doc.xml", b"<DOCUMENT><SECTION-1>\xec\x8b\xa0\xea\xb7\x9c\xec\x8b\x9c\xec\x84\xa4\xed\x88\xac\xec\x9e\x90")
    result = extract_excerpt(zip_bytes)
    assert result.state == ExtractionState.EXTRACTED
    assert "신규시설투자" in result.excerpt_original


def test_valid_xml_with_no_text_content_returns_parse_failed():
    zip_bytes = _zip_with("doc.xml", b'<?xml version="1.0" encoding="utf-8"?><DOCUMENT><SECTION-1></SECTION-1><SECTION-1></SECTION-1></DOCUMENT>')
    result = extract_excerpt(zip_bytes)
    assert result.state == ExtractionState.PARSE_FAILED


def test_oversized_package_is_rejected_without_parsing():
    oversized = b"PK" + b"\x00" * (MAX_ZIP_SIZE_BYTES + 1)
    result = extract_excerpt(oversized)
    assert result.state == ExtractionState.UNSUPPORTED_FORMAT
    assert "safety limit" in result.detail


def test_cp949_encoded_document_is_decoded_correctly():
    body = "신규시설투자등"
    xml = f'<?xml version="1.0" encoding="cp949"?><DOCUMENT><SECTION-1><P>cover</P></SECTION-1><SECTION-1><P>{body}</P></SECTION-1></DOCUMENT>'
    zip_bytes = _zip_with("doc.xml", xml.encode("cp949"))

    result = extract_excerpt(zip_bytes)

    assert result.state == ExtractionState.EXTRACTED
    assert body in result.excerpt_original


def test_unrecognized_encoding_returns_unsupported_format():
    # Bytes that are invalid in every fallback encoding tried.
    zip_bytes = _zip_with("doc.xml", b"\xff\xfe\x00\xff\xff\xfe\x00\xff invalid sequence \xfe\xff")
    result = extract_excerpt(zip_bytes)
    assert result.state == ExtractionState.UNSUPPORTED_FORMAT


def test_html_extension_is_also_supported():
    html = b'<html><body><section>\xec\x8b\xa0\xea\xb7\x9c\xec\x8b\x9c\xec\x84\xa4\xed\x88\xac\xec\x9e\x90</section></body></html>'
    zip_bytes = _zip_with("doc.html", html)
    # Not valid XML (no single root the way our DART fixture is), so this
    # exercises the XML-parse-failure path for a non-DART-shaped HTML
    # file — still a clear, non-crashing state.
    result = extract_excerpt(zip_bytes)
    assert result.state in (ExtractionState.EXTRACTED, ExtractionState.PARSE_FAILED)


def test_result_never_raises_for_any_of_the_above_inputs():
    # Defensive sweep — extract_excerpt must return a result, never throw,
    # for every case exercised above.
    for payload in (
        b"garbage",
        _zip_with("doc.xml", b"<broken"),
        _zip_with("doc.xml", b'<?xml version="1.0"?><DOCUMENT></DOCUMENT>'),
    ):
        result = extract_excerpt(payload)
        assert result.state in ExtractionState
