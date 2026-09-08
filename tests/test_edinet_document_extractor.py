"""edinet.document_extractor.extract_excerpt — pure function, fully
fixture-driven. No network. Gate 10.A added real PDF text extraction
(via pypdf) behind this seam — every PDF fixture below is a small,
synthetic, hand-built, non-secret PDF constructed in this test file
itself (never a real EDINET document or copyrighted filing). Phase 2,
Step 1 added bounded ZIP-package extraction (a single allowlisted `.pdf`
member) behind the same seam — every ZIP fixture below is likewise
synthetic and built in-test via `zipfile.ZipFile`, never a real EDINET
document. XBRL interpretation remains explicitly out of scope and still
falls through to UNSUPPORTED_FORMAT."""
from __future__ import annotations

import io
import warnings
import zipfile
from unittest.mock import patch

from src.data_access.edinet.document_extractor import (
    MAX_DOCUMENT_SIZE_BYTES,
    MAX_EXCERPT_CHARS,
    MAX_ZIP_COMPRESSION_RATIO,
    MAX_ZIP_MEMBER_UNCOMPRESSED_BYTES,
    MAX_ZIP_MEMBERS,
    MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES,
    extract_excerpt,
)
from src.models.models import ExtractionState


def _build_minimal_pdf(text: str = "Hello World") -> bytes:
    """Hand-built minimal single-page PDF with correctly computed xref
    offsets — a real, valid, parseable PDF structure, not a mock. Text
    is placed via a bare `Tj` show-text operator. Synthetic and
    non-secret; never real EDINET content."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> /MediaBox [0 0 612 792] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream_content = f"BT /F1 24 Tf 100 700 Td ({text}) Tj ET".encode("latin-1")
    objects.append(b"<< /Length " + str(len(stream_content)).encode() + b" >>\nstream\n" + stream_content + b"\nendstream")
    return _assemble_pdf(objects)


def _build_pdf_with_no_text_content() -> bytes:
    """A structurally valid, parseable PDF with one page and an empty
    content stream — no text-showing operator at all. Stands in for an
    image-only/no-text-layer PDF without needing real image binary data
    — pypdf's extract_text() legitimately returns "" for a page with no
    text content either way, which is exactly the observable behavior
    this module needs to handle safely."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << >> /MediaBox [0 0 612 792] /Contents 4 0 R >>",
        b"<< /Length 0 >>\nstream\n\nendstream",
    ]
    return _assemble_pdf(objects)


def _assemble_pdf(objects: list[bytes]) -> bytes:
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


def _build_zip(members: list[tuple[str, bytes]], compress_type: int = zipfile.ZIP_DEFLATED) -> bytes:
    """Assembles a real, valid, in-memory ZIP from `(name, bytes)` pairs
    via the stdlib `zipfile` module itself — never a hand-rolled binary
    layout. Synthetic and non-secret; never a real EDINET document."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compress_type) as archive:
        for name, data in members:
            archive.writestr(name, data)
    return buffer.getvalue()


def test_extracts_text_from_plain_html():
    html = "<html><body><p>有価証券報告書 Annual Securities Report summary text.</p></body></html>".encode("utf-8")
    result = extract_excerpt(html)
    assert result.state == ExtractionState.EXTRACTED
    assert "Annual Securities Report" in result.excerpt_original


def test_extracts_from_plain_text_document_with_no_html_tags():
    text = "重要な開示事項です。 An important disclosure summary follows.".encode("utf-8")
    result = extract_excerpt(text)
    assert result.state == ExtractionState.EXTRACTED
    assert "An important disclosure" in result.excerpt_original


def test_skips_script_and_style_content():
    html = b"<html><head><style>.a{color:red}</style></head><body><script>var x=1;</script><p>Disclosure text here.</p></body></html>"
    result = extract_excerpt(html)
    assert result.state == ExtractionState.EXTRACTED
    assert "color:red" not in result.excerpt_original
    assert "var x=1" not in result.excerpt_original
    assert "Disclosure text here" in result.excerpt_original


def test_plain_html_leaves_evidence_source_member_none():
    # Phase 2, Step 2: plain HTML/text has no ZIP container either.
    html = "<html><body><p>有価証券報告書 summary text.</p></body></html>".encode("utf-8")
    result = extract_excerpt(html)
    assert result.state == ExtractionState.EXTRACTED
    assert result.evidence_source_member is None


def test_excerpt_is_bounded_to_max_chars():
    long_text = ("Disclosure summary. " + "x" * 2000).encode("utf-8")
    result = extract_excerpt(long_text)
    assert result.state == ExtractionState.EXTRACTED
    assert len(result.excerpt_original) == MAX_EXCERPT_CHARS


def test_oversized_document_is_rejected_without_parsing():
    oversized = b"x" * (MAX_DOCUMENT_SIZE_BYTES + 1)
    result = extract_excerpt(oversized)
    assert result.state == ExtractionState.UNSUPPORTED_FORMAT
    assert "safety limit" in result.detail


def test_empty_document_returns_parse_failed():
    result = extract_excerpt(b"")
    assert result.state == ExtractionState.PARSE_FAILED


def test_html_with_only_tags_and_no_text_returns_parse_failed():
    result = extract_excerpt(b"<html><body><div></div><span></span></body></html>")
    assert result.state == ExtractionState.PARSE_FAILED


def test_corrupt_zip_shaped_payload_fails_closed_not_a_crash():
    # ZIP magic bytes (PK\x03\x04) followed by garbage is not a valid
    # archive (no real end-of-central-directory record) — the ZIP path
    # (Phase 2, Step 1) must fail closed with PARSE_FAILED, never crash.
    zip_like = b"\x50\x4b\x03\x04\x14\x00\x00\x00\x08\x00" + bytes(range(200))
    result = extract_excerpt(zip_like)
    assert result.state == ExtractionState.PARSE_FAILED
    assert "ZIP" in result.detail
    assert result.evidence_source_member is None


def test_result_never_raises_for_garbage_input():
    for payload in (b"\xff\xfe\x00\xff", b"<broken<html", b""):
        result = extract_excerpt(payload)
        assert result.state in ExtractionState


def test_reprocessing_the_same_bytes_is_deterministic():
    html = b"<html><body><p>Consistent disclosure text.</p></body></html>"
    first = extract_excerpt(html)
    second = extract_excerpt(html)
    assert first.excerpt_original == second.excerpt_original
    assert first.state == second.state


# --- Gate 10.A: PDF text extraction (pypdf, fixture-only, synthetic
# non-secret PDFs built above) ---

def test_valid_text_bearing_pdf_is_extracted():
    pdf = _build_minimal_pdf("Synthetic test evidence text for extraction validation.")
    result = extract_excerpt(pdf)
    assert result.state == ExtractionState.EXTRACTED
    assert "Synthetic test evidence text" in result.excerpt_original


def test_bare_pdf_leaves_evidence_source_member_none():
    # Phase 2, Step 2: a bare (non-ZIP) PDF response has no container to
    # name a member from — evidence_source_member must stay None.
    pdf = _build_minimal_pdf("Bare PDF, no ZIP container.")
    result = extract_excerpt(pdf)
    assert result.state == ExtractionState.EXTRACTED
    assert result.evidence_source_member is None


def test_pdf_excerpt_is_bounded_to_max_chars():
    pdf = _build_minimal_pdf("A" * 2000)
    result = extract_excerpt(pdf)
    assert result.state == ExtractionState.EXTRACTED
    assert len(result.excerpt_original) == MAX_EXCERPT_CHARS


def test_pdf_text_is_normalized_but_not_translated_or_summarized():
    # Collapsed whitespace only — the exact substring must survive
    # verbatim, proving no translation/summarization/classification
    # occurs during extraction.
    pdf = _build_minimal_pdf("Original Japanese-context evidence unchanged")
    result = extract_excerpt(pdf)
    assert result.state == ExtractionState.EXTRACTED
    assert "Original Japanese-context evidence unchanged" == result.excerpt_original


def test_image_only_no_text_pdf_returns_parse_failed():
    pdf = _build_pdf_with_no_text_content()
    result = extract_excerpt(pdf)
    assert result.state == ExtractionState.PARSE_FAILED
    assert "image-only" in result.detail or "no extractable text" in result.detail


def test_empty_bytes_returns_parse_failed_not_pdf_path():
    # Empty bytes never match the %PDF- magic prefix, so this exercises
    # the pre-existing generic empty-input path, not the new PDF code —
    # confirming the two paths don't interfere with each other.
    result = extract_excerpt(b"")
    assert result.state == ExtractionState.PARSE_FAILED


def test_corrupt_truncated_pdf_returns_parse_failed_not_a_crash():
    # Real %PDF- magic bytes followed by non-PDF garbage — must be
    # caught by _extract_pdf_text's broad exception handler, never
    # surface a raw pypdf exception/stack trace.
    corrupt = b"%PDF-1.4\n" + bytes(range(200))
    result = extract_excerpt(corrupt)
    assert result.state == ExtractionState.PARSE_FAILED
    assert "corrupt" in result.detail.lower() or "truncated" in result.detail.lower()


def test_truncated_valid_pdf_returns_parse_failed():
    pdf = _build_minimal_pdf("This text will never be reached.")
    truncated = pdf[: len(pdf) // 2]  # cut a real, valid PDF in half
    result = extract_excerpt(truncated)
    assert result.state == ExtractionState.PARSE_FAILED


def test_zip_magic_garbage_when_pdf_expected_returns_parse_failed():
    zip_like = b"\x50\x4b\x03\x04" + bytes(range(200))
    result = extract_excerpt(zip_like)
    assert result.state == ExtractionState.PARSE_FAILED
    assert "ZIP" in result.detail


def test_non_pdf_unrecognized_binary_is_unsupported_format():
    unrecognized = bytes([0x00, 0x01, 0x02, 0x03]) + bytes(range(200))
    result = extract_excerpt(unrecognized)
    assert result.state == ExtractionState.UNSUPPORTED_FORMAT


def test_oversize_pdf_shaped_payload_is_unsupported_format_before_parsing():
    # The 8MB size gate must fire before any PDF parsing is attempted,
    # even when the payload starts with real PDF magic bytes.
    oversized_pdf_shaped = b"%PDF-1.4\n" + b"x" * MAX_DOCUMENT_SIZE_BYTES
    result = extract_excerpt(oversized_pdf_shaped)
    assert result.state == ExtractionState.UNSUPPORTED_FORMAT
    assert "safety limit" in result.detail


def test_encrypted_pdf_returns_parse_failed():
    # No real encrypted-PDF fixture is hand-built (nontrivial without a
    # PDF-writing library) — the reader.is_encrypted branch is verified
    # directly via a minimal patch of PdfReader, not by mocking away the
    # rest of the function's real logic.
    with patch("src.data_access.edinet.document_extractor.PdfReader") as mock_reader_cls:
        mock_reader_cls.return_value.is_encrypted = True
        pdf = _build_minimal_pdf("irrelevant")
        result = extract_excerpt(pdf)
    assert result.state == ExtractionState.PARSE_FAILED
    assert "encrypted" in result.detail.lower()


def test_pdf_extraction_is_deterministic_on_repeat():
    pdf = _build_minimal_pdf("Deterministic repeat check.")
    first = extract_excerpt(pdf)
    second = extract_excerpt(pdf)
    assert first.excerpt_original == second.excerpt_original
    assert first.state == second.state


def test_pdf_result_never_raises_for_garbage_shaped_like_pdf():
    for payload in (b"%PDF-", b"%PDF-1.4", b"%PDF-" + bytes(range(255)), b"%PDF-\x00\x00\x00"):
        result = extract_excerpt(payload)
        assert result.state in ExtractionState


# ============================================================
# Phase 2, Step 1: bounded EDINET ZIP-package extraction (a single
# allowlisted `.pdf` member). Every fixture is a real, valid, in-memory
# ZIP assembled via zipfile.ZipFile itself (see _build_zip above) — never
# a real EDINET document.
# ============================================================


def test_zip_with_one_valid_pdf_member_is_extracted():
    pdf_bytes = _build_minimal_pdf("Evidence extracted from inside a ZIP package.")
    zip_bytes = _build_zip([("PublicDoc/0101.pdf", pdf_bytes)])
    result = extract_excerpt(zip_bytes)
    assert result.state == ExtractionState.EXTRACTED
    assert "Evidence extracted from inside a ZIP package" in result.excerpt_original
    # Phase 2, Step 2: the selected member's safe path is recorded as
    # provenance, exactly as read — never a URL, never fetchable.
    assert result.evidence_source_member == "PublicDoc/0101.pdf"


def test_zip_with_pdf_plus_irrelevant_members_reads_only_the_pdf():
    pdf_bytes = _build_minimal_pdf("The one true selected document.")
    zip_bytes = _build_zip([
        ("XBRL/PublicDoc/jpcrp030000-asr-001.xbrl", b"<xbrl>irrelevant</xbrl>"),
        ("manifest.xml", b"<manifest/>"),
        ("PublicDoc/0101.htm", b"<html><body>irrelevant html</body></html>"),
        ("meta/logo.jpg", bytes(range(50))),
        ("AuditDoc/signature.p7s", bytes(range(30))),
        ("taxonomy/jppfs_cor.xsd", b"<xsd/>"),
        ("PublicDoc/0101.pdf", pdf_bytes),
    ])
    with patch(
        "src.data_access.dart.document_extractor._LenientHtmlTextExtractor",
        side_effect=AssertionError("no non-PDF member may be parsed"),
    ):
        result = extract_excerpt(zip_bytes)
    assert result.state == ExtractionState.EXTRACTED
    assert "The one true selected document" in result.excerpt_original
    assert result.evidence_source_member == "PublicDoc/0101.pdf"


def test_zip_with_no_pdf_and_no_html_member_is_unsupported_format():
    # XBRL/XSD/XML-only package (no .pdf, no .htm/.html at all) — the
    # HTML fallback must never treat these as candidates; genuinely
    # nothing usable remains.
    zip_bytes = _build_zip([
        ("manifest.xml", b"<manifest/>"),
        ("XBRL/PublicDoc/jpcrp170000-sbr-001.xbrl", b"<xbrl>irrelevant</xbrl>"),
        ("XBRL/PublicDoc/jpcrp170000-sbr-001.xsd", b"<xsd/>"),
    ])
    result = extract_excerpt(zip_bytes)
    assert result.state == ExtractionState.UNSUPPORTED_FORMAT
    assert "PDF" in result.detail
    assert "HTML" in result.detail
    assert result.evidence_source_member is None


# ============================================================
# HTML-in-ZIP fallback (narrow extension) — only ever considered when no
# safe .pdf member exists at all. Fixture shape mirrors the real,
# live-verified EDINET ZIP found for docID S100Z0ID (Shin-Etsu Chemical's
# 自己株券買付状況報告書, 010:170000:220): a header .htm and a "honbun"
# .htm, no .pdf member.
# ============================================================


def test_zip_with_safe_pdf_plus_html_still_selects_pdf():
    pdf_bytes = _build_minimal_pdf("PDF must win even though HTML is also present.")
    zip_bytes = _build_zip([
        ("XBRL/PublicDoc/0000000_header_...htm", b"<html><body>header, never read</body></html>"),
        ("XBRL/PublicDoc/0100010_honbun_...htm", b"<html><body>honbun body, never read</body></html>"),
        ("PublicDoc/0101.pdf", pdf_bytes),
    ])
    with patch(
        "src.data_access.edinet.document_extractor._select_safe_html_member",
        side_effect=AssertionError("HTML selection must never run when a safe PDF member exists"),
    ):
        result = extract_excerpt(zip_bytes)
    assert result.state == ExtractionState.EXTRACTED
    assert "PDF must win" in result.excerpt_original
    assert result.evidence_source_member == "PublicDoc/0101.pdf"


def test_zip_with_no_pdf_prefers_honbun_html_over_header_html():
    header_html = "<html><body>Header/cover text, must not be selected.</body></html>".encode("utf-8")
    honbun_html = "<html><body>自己株券買付状況報告書の本文テキストです。</body></html>".encode("utf-8")
    zip_bytes = _build_zip([
        ("XBRL/PublicDoc/0000000_header_jpcrp170000-sbr-001_E00776-000_2026-09-04_01_2026-09-04_ixbrl.htm", header_html),
        ("XBRL/PublicDoc/0100010_honbun_jpcrp170000-sbr-001_E00776-000_2026-09-04_01_2026-09-04_ixbrl.htm", honbun_html),
        ("XBRL/PublicDoc/jpcrp170000-sbr-001_E00776-000_2026-09-04_01_2026-09-04.xbrl", b"<xbrl>irrelevant</xbrl>"),
        ("XBRL/PublicDoc/jpcrp170000-sbr-001_E00776-000_2026-09-04_01_2026-09-04.xsd", b"<xsd/>"),
    ])
    result = extract_excerpt(zip_bytes)
    assert result.state == ExtractionState.EXTRACTED
    assert "自己株券買付状況報告書の本文テキストです" in result.excerpt_original
    assert "Header/cover text" not in result.excerpt_original
    assert result.evidence_source_member == "XBRL/PublicDoc/0100010_honbun_jpcrp170000-sbr-001_E00776-000_2026-09-04_01_2026-09-04_ixbrl.htm"


def test_zip_with_no_pdf_and_no_honbun_selects_largest_safe_html():
    small_html = "<html><body>Small member.</body></html>".encode("utf-8")
    large_html = ("<html><body>Large member with much more disclosure text content padding it out well beyond the small one.</body></html>").encode("utf-8")
    zip_bytes = _build_zip([
        ("PublicDoc/0000000_cover.htm", small_html),
        ("PublicDoc/0100010_body.htm", large_html),
    ])
    result = extract_excerpt(zip_bytes)
    assert result.state == ExtractionState.EXTRACTED
    assert "Large member with much more disclosure" in result.excerpt_original
    assert "Small member" not in result.excerpt_original
    assert result.evidence_source_member == "PublicDoc/0100010_body.htm"


def test_zip_with_unsafe_path_traversal_html_member_fails_closed_before_reading():
    zip_bytes = _build_zip([("../../evil.htm", b"<html><body>should never be read</body></html>")])
    with patch(
        "src.data_access.edinet.document_extractor._extract_zip_html_text",
        side_effect=AssertionError("member content must not be read"),
    ):
        result = extract_excerpt(zip_bytes)
    assert result.state == ExtractionState.UNSUPPORTED_FORMAT
    assert "unsafe" in result.detail.lower()


def test_zip_html_member_with_no_extractable_text_returns_parse_failed():
    zip_bytes = _build_zip([("PublicDoc/0100010_honbun.htm", b"<html><body><div></div></body></html>")])
    result = extract_excerpt(zip_bytes)
    assert result.state == ExtractionState.PARSE_FAILED
    assert result.evidence_source_member is None


def test_zip_html_fallback_never_reads_xbrl_or_xml_members():
    honbun_html = "<html><body>Only this member may ever be read.</body></html>".encode("utf-8")
    zip_bytes = _build_zip([
        ("XBRL/PublicDoc/report.xbrl", b"<xbrl>must never be read as text</xbrl>"),
        ("XBRL/PublicDoc/report.xsd", b"<xsd>must never be read as text</xsd>"),
        ("XBRL/PublicDoc/report_pre.xml", b"<pre>must never be read as text</pre>"),
        ("XBRL/PublicDoc/0100010_honbun.htm", honbun_html),
    ])
    result = extract_excerpt(zip_bytes)
    assert result.state == ExtractionState.EXTRACTED
    assert "Only this member may ever be read" in result.excerpt_original
    for forbidden in ("must never be read as text",):
        assert forbidden not in result.excerpt_original


def test_zip_html_fallback_archive_bomb_safeguards_still_enforced():
    # Same ratio-bomb shape as the existing PDF-path test, but with no
    # .pdf member at all — proves the HTML selector's own independently
    # duplicated safety scan (not just the PDF selector's) enforces the
    # compression-ratio limit before any member is read.
    ratio_bomb = b"0" * (1024 * 1024)
    zip_bytes = _build_zip([("PublicDoc/0100010_honbun.htm", ratio_bomb)], compress_type=zipfile.ZIP_DEFLATED)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as check:
        info = check.infolist()[0]
        assert info.file_size / max(info.compress_size, 1) > MAX_ZIP_COMPRESSION_RATIO  # sanity
    with patch(
        "src.data_access.edinet.document_extractor._extract_zip_html_text",
        side_effect=AssertionError("member content must not be read"),
    ):
        result = extract_excerpt(zip_bytes)
    assert result.state == ExtractionState.UNSUPPORTED_FORMAT
    assert "compression ratio" in result.detail.lower()


def test_zip_html_fallback_too_many_members_fails_closed_before_reading():
    members = [(f"file_{i}.xml", b"<x/>") for i in range(MAX_ZIP_MEMBERS)]
    members.append(("PublicDoc/0100010_honbun.htm", b"<html><body>should never be read</body></html>"))
    zip_bytes = _build_zip(members)
    with patch(
        "src.data_access.edinet.document_extractor._extract_zip_html_text",
        side_effect=AssertionError("member content must not be read"),
    ):
        result = extract_excerpt(zip_bytes)
    assert result.state == ExtractionState.UNSUPPORTED_FORMAT
    assert "member" in result.detail.lower()


def test_zip_with_absolute_path_pdf_member_fails_closed_before_reading():
    zip_bytes = _build_zip([("/etc/evil.pdf", _build_minimal_pdf("should never be read"))])
    with patch(
        "src.data_access.edinet.document_extractor._extract_pdf_text",
        side_effect=AssertionError("member content must not be read"),
    ):
        result = extract_excerpt(zip_bytes)
    assert result.state == ExtractionState.UNSUPPORTED_FORMAT
    assert "unsafe" in result.detail.lower()


def test_zip_with_path_traversal_pdf_member_fails_closed_before_reading():
    zip_bytes = _build_zip([("../../evil.pdf", _build_minimal_pdf("should never be read"))])
    with patch(
        "src.data_access.edinet.document_extractor._extract_pdf_text",
        side_effect=AssertionError("member content must not be read"),
    ):
        result = extract_excerpt(zip_bytes)
    assert result.state == ExtractionState.UNSUPPORTED_FORMAT
    assert "unsafe" in result.detail.lower()


def test_zip_with_drive_letter_pdf_member_fails_closed_before_reading():
    zip_bytes = _build_zip([("C:\\evil\\evil.pdf", _build_minimal_pdf("should never be read"))])
    with patch(
        "src.data_access.edinet.document_extractor._extract_pdf_text",
        side_effect=AssertionError("member content must not be read"),
    ):
        result = extract_excerpt(zip_bytes)
    assert result.state == ExtractionState.UNSUPPORTED_FORMAT
    assert "unsafe" in result.detail.lower()


def test_zip_with_too_many_members_fails_closed_before_reading():
    members = [(f"file_{i}.xml", b"<x/>") for i in range(MAX_ZIP_MEMBERS)]
    members.append(("PublicDoc/0101.pdf", _build_minimal_pdf("should never be read")))
    zip_bytes = _build_zip(members)
    with patch(
        "src.data_access.edinet.document_extractor._extract_pdf_text",
        side_effect=AssertionError("member content must not be read"),
    ):
        result = extract_excerpt(zip_bytes)
    assert result.state == ExtractionState.UNSUPPORTED_FORMAT
    assert "member" in result.detail.lower()


def test_zip_with_oversized_total_uncompressed_size_fails_closed_before_reading():
    # A real archive whose total uncompressed size exceeds the total cap
    # (while every individual member stays at-or-under the per-member
    # cap, and every member's ratio stays at/near 1:1) can't be built
    # from real DEFLATE-compressed content without either tripping the
    # outer 8MB raw-response gate first (if stored uncompressed) or the
    # ratio check first (if compressed, since near-constant filler bytes
    # compress far past the ratio limit long before reaching this size).
    # _select_safe_pdf_member is therefore exercised directly against
    # bare, in-memory ZipInfo metadata — still fully synthetic, isolating
    # exactly the total-size check from the per-member and ratio checks.
    from src.data_access.edinet.document_extractor import _select_safe_pdf_member

    info1 = zipfile.ZipInfo(filename="PublicDoc/big1.xml")
    info1.file_size = MAX_ZIP_MEMBER_UNCOMPRESSED_BYTES
    info1.compress_size = MAX_ZIP_MEMBER_UNCOMPRESSED_BYTES
    info2 = zipfile.ZipInfo(filename="PublicDoc/big2.xml")
    info2.file_size = MAX_ZIP_MEMBER_UNCOMPRESSED_BYTES
    info2.compress_size = MAX_ZIP_MEMBER_UNCOMPRESSED_BYTES
    pdf_info = zipfile.ZipInfo(filename="PublicDoc/0101.pdf")
    pdf_info.file_size = 10
    pdf_info.compress_size = 10

    assert info1.file_size + info2.file_size + pdf_info.file_size > MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES  # sanity: fixture really exceeds the total cap

    class _FakeArchive:
        def infolist(self):
            return [info1, info2, pdf_info]

    selected, detail = _select_safe_pdf_member(_FakeArchive())
    assert selected is None
    assert "total uncompressed" in detail.lower()


def test_zip_with_oversized_individual_member_fails_closed_before_reading():
    big_pdf_shaped = b"0" * (MAX_ZIP_MEMBER_UNCOMPRESSED_BYTES + 1)
    zip_bytes = _build_zip([("PublicDoc/0101.pdf", big_pdf_shaped)])
    with patch(
        "src.data_access.edinet.document_extractor._extract_pdf_text",
        side_effect=AssertionError("member content must not be read"),
    ):
        result = extract_excerpt(zip_bytes)
    assert result.state == ExtractionState.UNSUPPORTED_FORMAT
    assert "per-member" in result.detail.lower()


def test_zip_with_excessive_compression_ratio_fails_closed_before_reading():
    # A highly compressible payload (all zeros) deflates far beyond
    # MAX_ZIP_COMPRESSION_RATIO:1 — a classic zip-bomb shape — while
    # staying comfortably under the per-member/total-size caps above, so
    # the ratio check (not the size checks) is what fires here.
    ratio_bomb = b"0" * (1024 * 1024)  # 1MB of zeros
    zip_bytes = _build_zip([("PublicDoc/0101.pdf", ratio_bomb)], compress_type=zipfile.ZIP_DEFLATED)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as check:
        info = check.infolist()[0]
        assert info.file_size / max(info.compress_size, 1) > MAX_ZIP_COMPRESSION_RATIO  # sanity: fixture is actually a ratio bomb
    with patch(
        "src.data_access.edinet.document_extractor._extract_pdf_text",
        side_effect=AssertionError("member content must not be read"),
    ):
        result = extract_excerpt(zip_bytes)
    assert result.state == ExtractionState.UNSUPPORTED_FORMAT
    assert "compression ratio" in result.detail.lower()


def test_zip_with_encrypted_member_fails_closed():
    # zipfile provides no way to WRITE a real encrypted entry (and
    # actively normalizes flag_bits on write), so the encryption-flag
    # check is exercised directly against _select_safe_pdf_member with a
    # bare, in-memory ZipInfo — still fully synthetic, no real archive or
    # document involved.
    from src.data_access.edinet.document_extractor import _select_safe_pdf_member

    info = zipfile.ZipInfo(filename="PublicDoc/0101.pdf")
    info.flag_bits = 0x1
    info.file_size = 100
    info.compress_size = 50

    class _FakeArchive:
        def infolist(self):
            return [info]

    selected, detail = _select_safe_pdf_member(_FakeArchive())
    assert selected is None
    assert "encrypted" in detail.lower()


def test_zip_with_pdf_named_member_containing_zip_bytes_fails_closed_without_recursion():
    inner_zip = _build_zip([("inner.txt", b"nested content, must never be read")])
    zip_bytes = _build_zip([("PublicDoc/0101.pdf", inner_zip)])
    real_zipfile_cls = zipfile.ZipFile
    open_calls = []

    def _tracking_zipfile(*args, **kwargs):
        open_calls.append(1)
        return real_zipfile_cls(*args, **kwargs)

    with patch("src.data_access.edinet.document_extractor.zipfile.ZipFile", side_effect=_tracking_zipfile):
        result = extract_excerpt(zip_bytes)
    assert result.state == ExtractionState.UNSUPPORTED_FORMAT
    assert "archive" in result.detail.lower()
    assert len(open_calls) == 1  # the inner ZIP bytes were never reopened as an archive


def test_zip_with_invalid_pdf_named_content_fails_cleanly():
    zip_bytes = _build_zip([("PublicDoc/0101.pdf", b"not a real pdf, just garbage bytes")])
    result = extract_excerpt(zip_bytes)
    assert result.state == ExtractionState.UNSUPPORTED_FORMAT
    assert "pdf" in result.detail.lower()
    assert result.evidence_source_member is None


def test_zip_with_pdf_member_that_fails_pdf_extraction_leaves_evidence_source_member_none():
    # Phase 2, Step 2: a member that IS a real PDF by magic bytes but
    # fails pypdf extraction (corrupt/truncated) must not record
    # provenance for content that never became a usable excerpt.
    corrupt_pdf = b"%PDF-1.4\n" + bytes(range(200))
    zip_bytes = _build_zip([("PublicDoc/0101.pdf", corrupt_pdf)])
    result = extract_excerpt(zip_bytes)
    assert result.state == ExtractionState.PARSE_FAILED
    assert result.evidence_source_member is None


def test_zip_with_duplicate_pdf_names_selects_first_entry_deterministically():
    pdf_a = _build_minimal_pdf("FIRST ENTRY MUST BE SELECTED.")
    pdf_b = _build_minimal_pdf("SECOND ENTRY MUST NEVER BE READ.")
    with warnings.catch_warnings():
        # zipfile itself warns on write about the duplicate name — the
        # archive is still a valid ZIP with two distinct entries; this is
        # exactly the adversarial/ambiguous shape this test verifies is
        # handled deterministically.
        warnings.simplefilter("ignore", UserWarning)
        zip_bytes = _build_zip([("PublicDoc/0101.pdf", pdf_a), ("PublicDoc/0101.pdf", pdf_b)])
    result = extract_excerpt(zip_bytes)
    assert result.state == ExtractionState.EXTRACTED
    assert "FIRST ENTRY MUST BE SELECTED" in result.excerpt_original
    assert "SECOND ENTRY MUST NEVER BE READ" not in result.excerpt_original
    # Phase 2, Step 2: provenance names the first ZipInfo entry actually
    # read, matching the deterministic selection this test verifies.
    assert result.evidence_source_member == "PublicDoc/0101.pdf"


def test_oversized_zip_shaped_payload_is_rejected_before_zip_parsing():
    # The existing 8MB MAX_DOCUMENT_SIZE_BYTES gate must fire before any
    # ZIP parsing is attempted, even when the payload starts with real
    # ZIP magic bytes.
    oversized_zip_shaped = b"PK\x03\x04" + b"x" * MAX_DOCUMENT_SIZE_BYTES
    with patch(
        "src.data_access.edinet.document_extractor.zipfile.ZipFile",
        side_effect=AssertionError("ZIP must not be opened once the 8MB cap is exceeded"),
    ):
        result = extract_excerpt(oversized_zip_shaped)
    assert result.state == ExtractionState.UNSUPPORTED_FORMAT
    assert "safety limit" in result.detail


def test_zip_path_never_raises_for_adversarial_or_malformed_input():
    payloads = [
        b"PK\x03\x04",
        b"PK\x03\x04" + bytes(range(255)),
        b"PK\x05\x06" + b"\x00" * 18,  # a bare, empty end-of-central-directory record
        _build_zip([]),  # a real, valid, empty archive
        _build_zip([("PublicDoc/0101.pdf", b"")]),  # zero-byte pdf-named member
    ]
    for payload in payloads:
        result = extract_excerpt(payload)
        assert result.state in ExtractionState


def test_zip_extraction_never_writes_to_disk():
    pdf_bytes = _build_minimal_pdf("Disk-write check.")
    zip_bytes = _build_zip([("PublicDoc/0101.pdf", pdf_bytes)])
    with patch("builtins.open", side_effect=AssertionError("document_extractor must not open any file")):
        result = extract_excerpt(zip_bytes)
    assert result.state == ExtractionState.EXTRACTED


def test_zip_extraction_is_deterministic_on_repeat():
    pdf_bytes = _build_minimal_pdf("Deterministic ZIP repeat check.")
    zip_bytes = _build_zip([("PublicDoc/0101.pdf", pdf_bytes)])
    first = extract_excerpt(zip_bytes)
    second = extract_excerpt(zip_bytes)
    assert first.state == second.state
    assert first.excerpt_original == second.excerpt_original


# --- Phase 2, Step 1 scope guard: no new dependency, network call, or
# disk-write behavior was introduced; only the extractor and this test
# file were touched ---


def test_zip_path_introduces_no_new_network_or_process_modules():
    import ast
    from pathlib import Path

    path = Path(__file__).parent.parent / "src" / "data_access" / "edinet" / "document_extractor.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    forbidden_modules = ("requests", "httpx", "urllib", "socket", "subprocess", "shutil")
    offenders = []
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for module in modules:
            if any(module == forbidden or module.startswith(forbidden + ".") for forbidden in forbidden_modules):
                offenders.append(module)
    assert not offenders, offenders


def test_no_new_dependency_added_to_requirements():
    import subprocess
    from pathlib import Path

    repo_root = Path(__file__).parent.parent
    result = subprocess.run(
        ["git", "diff", "HEAD", "--", "requirements.txt"], cwd=repo_root, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return
    assert result.stdout.strip() == "", f"requirements.txt was modified: {result.stdout}"


def test_phase2_step1_touches_only_the_edinet_extractor_and_its_test():
    """Runs against `git diff HEAD` — only meaningful in a real checkout
    with this step's changes present, and only additive to (never a
    substitute for) the fixed changed-files list Phase 1's own scope
    guard already checks in tests/test_evidence_packet_phase1.py."""
    import subprocess
    from pathlib import Path

    repo_root = Path(__file__).parent.parent
    result = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return
    changed = set(result.stdout.splitlines())
    allowed = {
        "src/data_access/edinet/document_extractor.py",
        "tests/test_edinet_document_extractor.py",
        # One pre-existing document_service.py test asserted the old
        # "ZIP is always UNSUPPORTED_FORMAT" behavior for a corrupt-ZIP
        # fixture — updated to the new, more accurate PARSE_FAILED
        # outcome; document_service.py itself is untouched.
        "tests/test_edinet_document_service.py",
    }
    assert changed <= allowed, changed - allowed


# --- No raw PDF bytes are ever persisted; DART/EDGAR extractors are
# untouched by this gate ---

def test_extract_excerpt_never_returns_raw_document_bytes():
    # The function's own contract: excerpt_original is always str or
    # None, never the original bytes object — the only way raw PDF
    # bytes could leak into anything persisted downstream.
    pdf = _build_minimal_pdf("Some evidence text.")
    result = extract_excerpt(pdf)
    assert result.excerpt_original is None or isinstance(result.excerpt_original, str)
    assert not isinstance(result.excerpt_original, bytes)


def test_dart_document_extractor_module_is_not_imported_by_pdf_path():
    # DART's own document_extractor.py is reused only for its
    # _LenientHtmlTextExtractor helper (plain-text/HTML path) — the new
    # PDF path must not touch it at all.
    import src.data_access.edinet.document_extractor as edinet_extractor
    import src.data_access.dart.document_extractor as dart_extractor
    pdf = _build_minimal_pdf("Isolation check.")
    with patch.object(dart_extractor, "_LenientHtmlTextExtractor", side_effect=AssertionError("DART extractor must not be invoked for a PDF payload")):
        result = edinet_extractor.extract_excerpt(pdf)
    assert result.state == ExtractionState.EXTRACTED


def test_edgar_document_extractor_is_unmodified_and_still_html_only():
    # Confirms EDGAR's own extractor (a separate module entirely) was
    # not touched by this gate — it still has no PDF-handling code path.
    import src.data_access.edgar.document_extractor as edgar_extractor
    assert not hasattr(edgar_extractor, "PdfReader")
    assert not hasattr(edgar_extractor, "_extract_pdf_text")


def test_dart_document_extractor_is_unmodified_and_still_html_only():
    import src.data_access.dart.document_extractor as dart_extractor
    assert not hasattr(dart_extractor, "PdfReader")
    assert not hasattr(dart_extractor, "_extract_pdf_text")
