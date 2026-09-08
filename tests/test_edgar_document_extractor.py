"""edgar.document_extractor.extract_excerpt — pure function, fully
fixture-driven. No network. Real EDGAR primary documents are plain
HTML/text, not zipped — simpler shape than DART's."""
from __future__ import annotations

from src.data_access.edgar.document_extractor import (
    EIGHT_K_ITEM_EXCERPT_CHARS,
    MAX_DOCUMENT_SIZE_BYTES,
    MAX_EXCERPT_CHARS,
    extract_excerpt,
)
from src.models.models import ExtractionState

# Cover-page shape mirroring the real NVDA 8-K observed live (milestone 8,
# Gate 3/6) — registrant name, state of incorporation, commission file
# number, address — before any Item header appears.
_COVER_PAGE = (
    "UNITED STATES SECURITIES AND EXCHANGE COMMISSION WASHINGTON, DC 20549 "
    "FORM 8-K CURRENT REPORT PURSUANT TO SECTION 13 OR 15(d) OF THE SECURITIES "
    "EXCHANGE ACT OF 1934 Date of Report (Date of earliest event reported): "
    "August 17, 2026 NVIDIA CORPORATION (Exact name of registrant as specified "
    "in its charter) Delaware 0-23985 94-3177549 (State or other jurisdiction "
    "of incorporation) (Commission File Number) (IRS Employer Identification No.) "
    "2788 San Tomas Expressway, Santa Clara, CA 95051 (Address of principal executive offices) "
)


def test_extracts_text_from_real_looking_8k_html():
    html = b"<html><body><p>Item 2.02 Results of Operations and Financial Condition. Revenue increased 50% year over year.</p></body></html>"
    result = extract_excerpt(html)
    assert result.state == ExtractionState.EXTRACTED
    assert "Item 2.02" in result.excerpt_original
    assert "Revenue increased" in result.excerpt_original


def test_extracts_from_plain_text_document_with_no_html_tags():
    text = b"Item 2.02 Results of Operations. Revenue increased significantly this quarter."
    result = extract_excerpt(text)
    assert result.state == ExtractionState.EXTRACTED
    assert "Revenue increased" in result.excerpt_original


def test_skips_script_and_style_content():
    html = b"<html><head><style>.a{color:red}</style></head><body><script>var x=1;</script><p>Item 1.01 Material Agreement.</p></body></html>"
    result = extract_excerpt(html)
    assert result.state == ExtractionState.EXTRACTED
    assert "color:red" not in result.excerpt_original
    assert "var x=1" not in result.excerpt_original
    assert "Material Agreement" in result.excerpt_original


def test_excerpt_is_bounded_to_max_chars():
    long_text = ("Item 2.02 Results. " + "x" * 2000).encode("utf-8")
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


def test_unrecognized_encoding_falls_back_gracefully():
    # Bytes invalid in utf-8 but valid in latin-1 (the documented
    # fallback) — should still extract, not fail.
    raw = "Item 2.02 café".encode("latin-1")
    result = extract_excerpt(raw)
    assert result.state == ExtractionState.EXTRACTED


def test_result_never_raises_for_garbage_input():
    for payload in (b"\xff\xfe\x00\xff", b"<broken<html", b""):
        result = extract_excerpt(payload)
        assert result.state in ExtractionState


# --- Item-anchored excerpt (milestone 8, Gate 6 fix) ---
# expected_items defaults to () — every test above exercises that
# default path unchanged, confirming DART-style prefix-from-start
# extraction (and DART's own document_extractor.py, untouched entirely)
# both keep working exactly as before this fix.

def test_no_expected_items_preserves_original_prefix_behavior():
    # Default (empty) expected_items — the DART-equivalent, unmodified path.
    html = f"<html><body><p>{_COVER_PAGE}Item 1.01 Entry into a Material Definitive Agreement. Substantive text here.</p></body></html>".encode()
    result = extract_excerpt(html)  # no expected_items passed
    assert result.state == ExtractionState.EXTRACTED
    assert len(result.excerpt_original) == MAX_EXCERPT_CHARS
    assert result.excerpt_original.startswith("UNITED STATES SECURITIES")  # cover page, not skipped


def test_expected_item_anchoring_skips_cover_page_boilerplate():
    html = f"<html><body><p>{_COVER_PAGE}Item 1.01 Entry into a Material Definitive Agreement. The Company entered into an agreement.</p></body></html>".encode()
    result = extract_excerpt(html, expected_items=("1.01",))
    assert result.state == ExtractionState.EXTRACTED
    assert result.excerpt_original.startswith("Item 1.01")
    assert "UNITED STATES SECURITIES" not in result.excerpt_original
    assert "The Company entered into an agreement" in result.excerpt_original
    assert result.detail == ""


def test_expected_item_excerpt_stays_under_edgar_cap():
    body = "Item 1.01 Entry into a Material Definitive Agreement. " + ("Substantive detail. " * 200)
    html = f"<html><body><p>{_COVER_PAGE}{body}</p></body></html>".encode()
    result = extract_excerpt(html, expected_items=("1.01",))
    assert result.state == ExtractionState.EXTRACTED
    assert len(result.excerpt_original) <= EIGHT_K_ITEM_EXCERPT_CHARS


def test_multi_item_excerpt_includes_all_expected_items_not_truncated_at_first():
    # Real shape: several expected items covered in adjacent sections —
    # must not stop after just the first one.
    body = "Item 1.01 Entry into a Material Definitive Agreement. Agreement details. Item 2.03 Creation of a Direct Financial Obligation. Obligation details. Item 7.01 Regulation FD Disclosure. Disclosure details."
    html = f"<html><body><p>{_COVER_PAGE}{body}</p></body></html>".encode()
    result = extract_excerpt(html, expected_items=("1.01", "2.03", "7.01"))
    assert result.state == ExtractionState.EXTRACTED
    assert "Agreement details" in result.excerpt_original
    assert "Obligation details" in result.excerpt_original
    assert "Disclosure details" in result.excerpt_original


def test_excerpt_stops_before_next_unexpected_item_header():
    # Item 9.01 (Financial Statements and Exhibits) is NOT one of this
    # filing's expected items — a real, common trailing section in 8-Ks —
    # and should act as the stop boundary.
    body = "Item 1.01 Entry into a Material Definitive Agreement. Agreement details. Item 9.01 Financial Statements and Exhibits. Exhibit list follows."
    html = f"<html><body><p>{_COVER_PAGE}{body}</p></body></html>".encode()
    result = extract_excerpt(html, expected_items=("1.01",))
    assert result.state == ExtractionState.EXTRACTED
    assert "Agreement details" in result.excerpt_original
    assert "Exhibit list follows" not in result.excerpt_original


def test_missing_expected_item_header_falls_back_to_first_available_item():
    # Expected items (5.02) never actually appear — 1.01 does. Falls back
    # to the first real Item header rather than the document start.
    html = f"<html><body><p>{_COVER_PAGE}Item 1.01 Entry into a Material Definitive Agreement. Agreement details.</p></body></html>".encode()
    result = extract_excerpt(html, expected_items=("5.02",))
    assert result.state == ExtractionState.EXTRACTED
    assert result.excerpt_original.startswith("Item 1.01")
    assert "Expected item header not found" in result.detail


def test_no_item_header_at_all_falls_back_to_document_start_prefix():
    html = f"<html><body><p>{_COVER_PAGE}No item headers anywhere in this filing body at all.</p></body></html>".encode()
    result = extract_excerpt(html, expected_items=("1.01",))
    assert result.state == ExtractionState.EXTRACTED
    assert result.excerpt_original.startswith("UNITED STATES SECURITIES")
    assert len(result.excerpt_original) <= MAX_EXCERPT_CHARS  # generic cap, not the 8-K cap, on this fallback
    assert "Substantive excerpt unavailable" in result.detail


def test_malformed_spacing_and_casing_in_item_header_still_matches():
    html = f"<html><body><p>{_COVER_PAGE}item   1.01   Entry into a Material Definitive Agreement. Agreement details here.</p></body></html>".encode()
    result = extract_excerpt(html, expected_items=("1.01",))
    assert result.state == ExtractionState.EXTRACTED
    assert "Agreement details here" in result.excerpt_original
    assert "UNITED STATES SECURITIES" not in result.excerpt_original


def test_reprocessing_the_same_bytes_with_same_expected_items_is_deterministic():
    html = f"<html><body><p>{_COVER_PAGE}Item 1.01 Entry into a Material Definitive Agreement. Agreement details.</p></body></html>".encode()
    first = extract_excerpt(html, expected_items=("1.01",))
    second = extract_excerpt(html, expected_items=("1.01",))
    assert first.excerpt_original == second.excerpt_original
    assert first.detail == second.detail
