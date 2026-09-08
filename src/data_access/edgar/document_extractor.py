"""Bounded, best-effort extraction of a short English excerpt from one
official SEC EDGAR primary filing document — for a single, explicitly
selected FilingEvent/CandidateSignal only, never a bulk/background
operation.

Simpler than DART's document pipeline: EDGAR primary documents are
served as plain HTML or plain text directly (no ZIP wrapper, no custom
XML schema, no encoding-fallback chain — SEC filings are UTF-8). Reuses
`_LenientHtmlTextExtractor` from
src/data_access/dart/document_extractor.py directly rather than writing
a second HTML-text extractor — that class was already built generic
("deliberately forgiving of malformed markup," per its own docstring),
not DART-specific. DART's own document_extractor.py is never modified
by anything in this module.

8-K item-anchored excerpt (milestone 8, Gate 6 fix): a real live pull
(NVDA accession 0001045810-26-000069) showed the generic MAX_EXCERPT_CHARS
(600) prefix-from-start excerpt captures only the standard 8-K cover
page (registrant name, state of incorporation, address) for at least
some real filings — never reaching the substantive Item 1.01/2.03/7.01
text at all. When the caller already knows a filing's expected item
numbers (from scan-time metadata or prior document-time refinement —
see edgar_rules.items_from_matched_rules), extract_excerpt anchors the
excerpt at the first matching expected Item header instead of the
document start, under a separate, larger EIGHT_K_ITEM_EXCERPT_CHARS cap
— MAX_EXCERPT_CHARS itself is never changed, and every non-8-K /
no-expected-items call keeps the exact original prefix-from-start
behavior.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from src.data_access.dart.document_extractor import _LenientHtmlTextExtractor
from src.data_access.edgar import edgar_rules
from src.models.models import ExtractionState

MAX_DOCUMENT_SIZE_BYTES = 8 * 1024 * 1024  # same pilot ceiling as DART's MAX_ZIP_SIZE_BYTES
MAX_EXCERPT_CHARS = 600  # unchanged generic/default bounded-excerpt cap — never increased
# EDGAR-8-K-item-anchored excerpt cap only — a separate, larger bound
# used solely on the item-anchored path below, initial value per the
# milestone-8 Gate 6 brief.
EIGHT_K_ITEM_EXCERPT_CHARS = 1200

_NO_ITEM_HEADER_DETAIL = "Substantive excerpt unavailable — no Item header found in the extracted text; cover-page prefix retained."
_EXPECTED_ITEM_NOT_FOUND_DETAIL = "Expected item header not found; used the first available Item header as a substantive-content anchor instead of the document start."


@dataclass(frozen=True)
class ExtractionResult:
    state: ExtractionState
    excerpt_original: str | None = None
    detail: str = ""
    # Evidence-packet foundation, Phase 1 (design/DECISIONS.md) — the
    # Item number the excerpt was anchored on, when the item-anchored path
    # (see _item_anchored_excerpt below) actually found and used one.
    # None for every non-8-K excerpt and for an 8-K excerpt that fell all
    # the way back to the plain document-start prefix (no Item header
    # found at all) — this is surfaced data already computed while
    # building the excerpt, never a new parse added to populate it.
    location_section: str | None = None


def _decode(raw: bytes) -> str | None:
    # SEC filings are documented as UTF-8; a latin-1 fallback is kept
    # only as a last resort for the rare legacy filing, mirroring DART's
    # own encoding-fallback discipline rather than assuming perfection.
    for encoding in ("utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def _item_anchored_excerpt(full_text: str, expected_items: tuple[str, ...]) -> tuple[str, str, str | None]:
    """Anchors the excerpt at the first (in document order) occurrence of
    one of `expected_items`, capped at EIGHT_K_ITEM_EXCERPT_CHARS. Ends
    before the next Item header that is NOT itself one of the expected
    items — a multi-item filing (e.g. Items 1.01, 2.03, and 7.01 covered
    in adjacent sections) must not be truncated after just the first
    expected item's text; the boundary is "left the zone of items we
    already know matter," not "hit any header at all." Falls back to the
    first Item header of any kind (past the cover page) if none of the
    expected items are found; falls back further to the plain
    document-start prefix (existing behavior, MAX_EXCERPT_CHARS) if no
    Item header exists anywhere. Returns (excerpt, detail, anchor_item) —
    detail is "" on a clean expected-item match, or a safe, non-guessing
    note otherwise; anchor_item is the Item number actually used as the
    anchor (e.g. "2.03"), or None when there was no header to anchor on
    at all (the plain-prefix fallback). Original English text only —
    never summarized, translated, or interpreted."""
    positions = edgar_rules.iter_item_header_positions(full_text)
    if not positions:
        return full_text[:MAX_EXCERPT_CHARS], _NO_ITEM_HEADER_DETAIL, None

    start_idx = next((idx for item, idx in positions if item in expected_items), None)
    anchor_item = next((item for item, idx in positions if item in expected_items), None)
    detail = ""
    if start_idx is None:
        anchor_item, start_idx = positions[0]
        detail = _EXPECTED_ITEM_NOT_FOUND_DETAIL

    hard_cap_end = start_idx + EIGHT_K_ITEM_EXCERPT_CHARS
    next_unexpected_header = next(
        (idx for item, idx in positions if idx > start_idx and item not in expected_items), None,
    )
    end_idx = min(hard_cap_end, next_unexpected_header) if next_unexpected_header is not None else hard_cap_end

    excerpt = full_text[start_idx:end_idx].strip()
    return excerpt, detail, anchor_item


def extract_excerpt(document_bytes: bytes, expected_items: tuple[str, ...] = ()) -> ExtractionResult:
    """Pure function over already-fetched bytes — network I/O and the
    per-accession-number cache/dedup live in document_service.py, one
    layer up (same separation DART's own document_service.py uses).

    `expected_items` (e.g. ("1.01", "2.03", "7.01"), from
    edgar_rules.items_from_matched_rules) is additive and optional — an
    empty tuple (the default, and every non-8-K call) preserves the
    exact original prefix-from-start behavior under MAX_EXCERPT_CHARS.
    Only 8-K candidates with known expected items use the item-anchored
    path under the separate EIGHT_K_ITEM_EXCERPT_CHARS cap."""
    if len(document_bytes) > MAX_DOCUMENT_SIZE_BYTES:
        return ExtractionResult(
            state=ExtractionState.UNSUPPORTED_FORMAT,
            detail=f"Document exceeds the {MAX_DOCUMENT_SIZE_BYTES // (1024 * 1024)}MB safety limit.",
        )

    text = _decode(document_bytes)
    if text is None:
        return ExtractionResult(state=ExtractionState.UNSUPPORTED_FORMAT, detail="Document text encoding was not recognized.")

    parser = _LenientHtmlTextExtractor()
    try:
        parser.feed(text)
    except Exception:
        return ExtractionResult(state=ExtractionState.PARSE_FAILED, detail="Document could not be parsed.")

    excerpt = re.sub(r"\s+", " ", " ".join(parser.text_parts)).strip()
    if not excerpt and "<" not in text:
        # Plain-text (non-HTML) primary documents have no tags for the
        # HTML parser to walk — fall back to the raw decoded text itself.
        # Only when the input isn't HTML at all: real HTML with empty/no
        # text content (e.g. only empty tags) must still fail rather than
        # surface the raw markup as if it were content.
        excerpt = re.sub(r"\s+", " ", text).strip()

    if not excerpt:
        return ExtractionResult(state=ExtractionState.PARSE_FAILED, detail="Document parsed but contained no extractable text.")

    if expected_items:
        anchored_excerpt, detail, anchor_item = _item_anchored_excerpt(excerpt, expected_items)
        location_section = f"Item {anchor_item}" if anchor_item else None
        return ExtractionResult(
            state=ExtractionState.EXTRACTED, excerpt_original=anchored_excerpt[:EIGHT_K_ITEM_EXCERPT_CHARS],
            detail=detail, location_section=location_section,
        )

    return ExtractionResult(state=ExtractionState.EXTRACTED, excerpt_original=excerpt[:MAX_EXCERPT_CHARS])
