"""Bounded, best-effort extraction of a short Korean excerpt from one
official DART filing document — for a single, explicitly selected
FilingEvent/CandidateSignal only, never a bulk/background operation.

Verified against real DART documents during development, not guessed —
and the second check caught a real gap the first one missed:

1. A 주요사항보고서 / major-event report (SK Hynix, rcept_no
   20260807000537, "주요사항보고서(자기주식 처분 결정)"): a ZIP
   containing one UTF-8 XML file in DART's own DART4 schema (nested
   <SECTION-1>/<TABLE>/<TR>/<TD> markup). The first <SECTION-1> was the
   cover-page/company-info block; extraction skips it and starts from
   the second onward.
2. A 최대주주등소유주식변동신고서 / major-shareholder-change report
   (Samsung, rcept_no 20260721801260): strict XML parsing
   (xml.etree.ElementTree) rejected this one outright with a genuine
   mismatched-tag error — the document is loosely-formatted HTML
   (<head>/<body>/<div class="xforms">...), not the DART4 schema. This
   was found by running the *orchestrator* against real current filings
   (milestone 4), not by a synthetic test, and is the reason
   extract_excerpt now falls back to Python's stdlib `html.parser`
   (which is deliberately forgiving of malformed markup) when strict XML
   parsing fails, rather than giving up immediately.

Every failure mode (oversized package, encoding not recognized, no
XML/HTML file at all, both the strict and lenient extraction paths
coming up empty) still degrades to an explicit ExtractionState rather
than a wrong guess or a crash — extract_excerpt() never raises.
"""
from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser

from src.models.models import ExcerptQuality, ExtractionState

# 8MB — comfortably above the ~5KB-90KB observed for the real documents
# verified during development, well below what a full annual report with
# embedded exhibits could reach. A hard reject, not a truncate-and-hope.
MAX_ZIP_SIZE_BYTES = 8 * 1024 * 1024
MAX_EXCERPT_CHARS = 600
# utf-8 confirmed against every real document verified during
# development; cp949/euc-kr are the well-known legacy encodings for
# older Korean regulatory filings, kept as fallbacks rather than assumed.
_ENCODING_FALLBACKS: tuple[str, ...] = ("utf-8", "cp949", "euc-kr")

_VERY_SHORT_THRESHOLD_CHARS = 20
# If a quarter or more of an excerpt's non-space characters are digits,
# it's very likely a numbered-table dump rather than prose — grounded in
# a real document verified during development (a treasury-stock-disposal
# report whose extracted excerpt was dense with share counts, won
# amounts, and dates).
_TABLE_HEAVY_DIGIT_RATIO = 0.25
# Cover-page field labels — the same markers the DART4-schema section-1-
# skip heuristic is meant to avoid. A match here means that heuristic
# didn't fully work for this particular document, worth flagging rather
# than presenting the excerpt as clean prose.
_BOILERPLATE_MARKERS: tuple[str, ...] = ("회사명", "대표이사", "본점소재지", "작성책임자")


@dataclass(frozen=True)
class ExtractionResult:
    state: ExtractionState
    excerpt_original: str | None = None
    document_filename: str | None = None
    # Human-readable, safe to show in the UI — never a raw stack trace or
    # exception repr.
    detail: str = ""


class _LenientHtmlTextExtractor(HTMLParser):
    """Stdlib HTMLParser is deliberately forgiving of malformed/
    mismatched tags, unlike xml.etree.ElementTree — the fallback path for
    a DART document that isn't the strict DART4 XML schema (see module
    docstring, case 2). Skips <script>/<style> contents so CSS/JS text
    doesn't pollute the excerpt."""

    _SKIP_TAGS = frozenset({"script", "style"})

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data and data.strip():
            self.text_parts.append(data)


def _decode(raw: bytes) -> str | None:
    for encoding in _ENCODING_FALLBACKS:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _extract_from_dart_xml_schema(text: str) -> str | None:
    """DART4-schema path (see module docstring, case 1). Returns None on
    any failure — ParseError, no <SECTION-1> elements, or no text found —
    so the caller can fall through to the lenient HTML path rather than
    failing outright."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    sections = root.findall(".//SECTION-1")
    if not sections:
        return None
    # Best-effort heuristic — see module docstring. Falls back to
    # whatever sections exist if there's only one.
    body_sections = sections[1:] if len(sections) > 1 else sections
    text_parts = [t for section in body_sections for t in section.itertext() if t and t.strip()]
    return _collapse_whitespace(" ".join(text_parts)) or None


def _extract_lenient_html(text: str) -> str | None:
    """Fallback path (see module docstring, case 2) — no cover-page-skip
    heuristic here, since this path exists precisely because the
    document doesn't follow the DART4 <SECTION-1> schema that heuristic
    depends on. Takes the whole document's text, bounded downstream."""
    parser = _LenientHtmlTextExtractor()
    try:
        parser.feed(text)
    except Exception:
        return None
    return _collapse_whitespace(" ".join(parser.text_parts)) or None


def assess_excerpt_quality(excerpt: str | None) -> ExcerptQuality:
    """Descriptive metadata only — never a materiality score. A simple,
    deterministic shape check (length, digit density, cover-page marker
    presence), not an attempt at real text-quality classification."""
    if excerpt is None:
        return ExcerptQuality.UNKNOWN
    stripped = excerpt.strip()
    if len(stripped) < _VERY_SHORT_THRESHOLD_CHARS:
        return ExcerptQuality.VERY_SHORT_OR_EMPTY
    if any(marker in stripped for marker in _BOILERPLATE_MARKERS):
        return ExcerptQuality.LIKELY_BOILERPLATE
    non_space = [c for c in stripped if not c.isspace()]
    digit_ratio = sum(c.isdigit() for c in non_space) / len(non_space) if non_space else 0.0
    if digit_ratio >= _TABLE_HEAVY_DIGIT_RATIO:
        return ExcerptQuality.TABLE_HEAVY
    return ExcerptQuality.USABLE_TEXT


def extract_excerpt(zip_bytes: bytes) -> ExtractionResult:
    """Pure function over already-fetched bytes — network I/O and the
    per-receipt-number cache/dedup live in document_service.py, one
    layer up (same separation DartClient itself uses)."""
    if len(zip_bytes) > MAX_ZIP_SIZE_BYTES:
        return ExtractionResult(
            state=ExtractionState.UNSUPPORTED_FORMAT,
            detail=f"Document package exceeds the {MAX_ZIP_SIZE_BYTES // (1024 * 1024)}MB safety limit.",
        )
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        return ExtractionResult(state=ExtractionState.PARSE_FAILED, detail="Document package was not a valid ZIP file.")

    with archive:
        xml_names = [n for n in archive.namelist() if n.lower().endswith((".xml", ".htm", ".html"))]
        if not xml_names:
            return ExtractionResult(state=ExtractionState.UNSUPPORTED_FORMAT, detail="No XML/HTML document found inside the package.")
        primary_name = xml_names[0]
        try:
            raw = archive.read(primary_name)
        except (KeyError, zipfile.BadZipFile, RuntimeError):
            return ExtractionResult(state=ExtractionState.PARSE_FAILED, detail="Could not read the document file from the package.")

    text = _decode(raw)
    if text is None:
        return ExtractionResult(
            state=ExtractionState.UNSUPPORTED_FORMAT, document_filename=primary_name,
            detail="Document text encoding was not recognized.",
        )

    excerpt = _extract_from_dart_xml_schema(text)
    if excerpt is None:
        excerpt = _extract_lenient_html(text)

    if not excerpt:
        return ExtractionResult(
            state=ExtractionState.PARSE_FAILED, document_filename=primary_name,
            detail="Document parsed but contained no extractable text.",
        )

    return ExtractionResult(state=ExtractionState.EXTRACTED, excerpt_original=excerpt[:MAX_EXCERPT_CHARS], document_filename=primary_name)
