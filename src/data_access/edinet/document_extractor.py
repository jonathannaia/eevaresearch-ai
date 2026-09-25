"""Bounded, best-effort extraction seam for one official EDINET document
(Japan radar pilot). For a single, explicitly selected FilingEvent/
CandidateSignal only, never a bulk/background operation.

Gate 1 status (superseded for PDF only, see Gate 10.A below) —
deliberately minimal, per the approved plan and the explicit Gate 1
instruction "Do not write a real XBRL parser/analytics engine. Build
only a bounded, source-specific extraction seam and fixtures sufficient
to validate its safe fallback behavior": real EDINET documents are
described (unconfirmed — see client.py's module docstring) as ZIP
packages containing XBRL/PDF/CSV content, not plain HTML/text like
EDGAR's primary documents. This module therefore:

  1. Handles genuinely plain-text/HTML content exactly like
     DART/EDGAR's shared `_LenientHtmlTextExtractor` (reused directly,
     not reimplemented).
  2. Handles a PDF payload via pypdf (Gate 10.A — see below).
  3. Handles a ZIP payload via a narrow, bounded single-PDF-member
     extraction (Phase 2, Step 1 — see below).
  4. For anything else (an unrecognized binary payload), returns
     UNSUPPORTED_FORMAT with a clear, honest detail message rather than
     attempting a guessed parse. XBRL interpretation remains explicit
     follow-up work for a later, separately-approved phase.

Gate 10.A — fixture-only PDF text extraction, added behind this same
seam, using `pypdf` (added to requirements.txt this gate — a lightweight,
pure-Python library; no OCR, no external binaries, no browser
automation, no Java tool, no shell-out). Detected by magic bytes
(`%PDF-`) BEFORE the plain-text/HTML path runs, so a real PDF — which is
never valid UTF-8 — gets real extraction instead of falling through to
the generic UNSUPPORTED_FORMAT binary fallback. Every fixture exercising
this path this gate is synthetic, non-secret, and built in-test — no
real EDINET document or copyrighted filing is added to this repository.
Zero live network calls are made or needed to write or test this code;
S100YGH5 itself is not fetched this gate.

Phase 2, Step 1 (evidence-packet foundation) — bounded EDINET ZIP-package
extraction, replacing the previous immediate UNSUPPORTED_FORMAT rejection
for any ZIP-magic payload. Scope is deliberately narrow: open the archive
in memory only (never written to disk), validate `archive.infolist()`
metadata (name, declared size, compression ratio, encryption flag) for
EVERY member before reading any member's content, then read at most ONE
safe, case-insensitively `.pdf`-suffixed member — selected by its exact
`ZipInfo` object (never by filename string, so a duplicate name resolves
deterministically to the first match in `infolist()` order) — and hand
its bytes to the existing, unmodified `_extract_pdf_text()`. No XML,
HTML, XBRL, CSV, taxonomy, signature, image, or other member is ever
read. No nested archive is followed or recursed into. This module still
never fetches anything beyond the one already-selected document URL
document_service.py passes in as bytes; it remains a pure function over
already-fetched bytes, exactly as before. Real EDINET ZIP internal
structure beyond "ZIP is one of three response formats" remains
unconfirmed in this repository (see client.py's module docstring), so
every limit below is a safety bound, not a claim about typical package
composition.

HTML-in-ZIP fallback (narrow, later extension) — a real, live-verified
EDINET ZIP package (docID S100Z0ID, Shin-Etsu Chemical's 自己株券買付状
況報告書, a 010:170000:220-triplet share-buyback status report) was
found to contain no `.pdf` member at all — only inline-XBRL `.htm` files
(a header member and a "honbun"/body member). ONLY when
`_select_safe_pdf_member` returns a genuine "no .pdf member" result (the
archive's own metadata having already passed every safety check above)
does `_select_safe_html_member` run: it applies the identical metadata
scan, then selects a safe `.htm`/`.html` member preferring one whose
basename contains "honbun" (EDINET's own real body-document naming
convention) over the largest safe candidate otherwise, and hands its
bytes to `_LenientHtmlTextExtractor` — the same tag-stripping parser the
bare/non-ZIP plain-text/HTML path already uses. PDF remains the
unconditional first choice whenever a safe `.pdf` member exists; no
`.xbrl`, `.xsd`, or `.xml` member is ever read by either selector; no
nested archive is followed. This remains a pure function over
already-fetched bytes — no document fetching, translation, candidate
creation, or persistence of any kind is added here.

Section-aware annual-report selection (category-scoped, later addition) —
the HTML-in-ZIP fallback above breaks ties among `honbun` members by
LARGEST declared size, which is correct for a single-body package (the
live-verified S100Z0ID buyback report has exactly one honbun member) but
demonstrably wrong for a 有価証券報告書: an annual securities report's
package carries several honbun members, and the largest is reliably the
financial-statements section, whose first 600 characters are the
statutory accounting-policy preamble — identical for every issuer, every
year, and worthless as research evidence.

So, for `category="annual_securities_report"` ONLY, and ONLY after the
genuine no-safe-PDF branch has already been taken (PDF remains the
unconditional first choice), this module now runs a bounded, content-
based section search BEFORE falling back to largest-member selection: at
most MAX_ANNUAL_REPORT_MEMBERS_SCANNED safe honbun members, in
`infolist()` order, each parsed with the same _LenientHtmlTextExtractor
the fallback already uses, looking for one of
PREFERRED_ANNUAL_REPORT_SECTIONS' statutory headings in the extracted
TEXT. Section identity is never inferred from a filename prefix — real
EDINET member-naming semantics beyond the "honbun" body convention
remain unconfirmed in this repository, and guessing them would be exactly
the premature assumption edinet_rules.py's own discipline forbids.

The search takes the BEST-RANKED heading across the members it inspects
(not merely the first member that matches), early-exiting only on a
rank-0 hit since no better rank exists. On a hit the excerpt is anchored
AT the heading, capped at the same MAX_EXCERPT_CHARS, and the matched
heading is returned as `location_section`. On no hit — or on any member
that fails to decode or parse — control falls through to the pre-existing
largest-member fallback UNCHANGED, with `location_section=None`, so this
addition can only ever improve the selection and never lose an extraction
that previously succeeded. Every archive-safety bound (member count,
per-member and total uncompressed size, compression ratio, encryption,
unsafe paths, nested archives, extension allowlist) applies exactly as
before; nothing about non-annual extraction behavior changes."""
from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass

from pypdf import PdfReader

from src.data_access.dart.document_extractor import _LenientHtmlTextExtractor
from src.models.models import ExtractionState

MAX_DOCUMENT_SIZE_BYTES = 8 * 1024 * 1024  # same pilot ceiling as DART/EDGAR
MAX_EXCERPT_CHARS = 600  # same generic bounded-excerpt cap as DART/EDGAR

# Phase 2, Step 1 — bounds on the ZIP *metadata* scan (archive.infolist(),
# no member content read) that runs before any member is opened. These
# are independent of, and in addition to, MAX_DOCUMENT_SIZE_BYTES (which
# already bounds the raw compressed response before any ZIP parsing is
# attempted at all). Values are deliberately generous relative to the
# one-or-two-document real EDINET packages this pilot expects (a single
# selected filing document, mirroring document_service.py's one-doc-per-
# candidate design), while still being a hard, named ceiling against an
# adversarial archive.
MAX_ZIP_MEMBERS = 100
MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES = 64 * 1024 * 1024  # 8x the compressed-response ceiling
MAX_ZIP_MEMBER_UNCOMPRESSED_BYTES = 32 * 1024 * 1024  # half the total ceiling; one PDF filing document
MAX_ZIP_COMPRESSION_RATIO = 100  # common zip-bomb-guard convention; text/XML/PDF rarely exceed ~10:1

_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"
_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:")

_UNSUPPORTED_BINARY_DETAIL = (
    "Document is not plain-text/HTML-decodable and not a recognized PDF or "
    "ZIP — an unrecognized binary EDINET payload. Only a safe "
    "UNSUPPORTED_FORMAT fallback is produced."
)
_PDF_ENCRYPTED_DETAIL = "PDF is encrypted; cannot extract text without a password."
_PDF_CORRUPT_DETAIL = "PDF could not be parsed — corrupt or truncated payload."
_PDF_NO_TEXT_DETAIL = "PDF parsed but contained no extractable text (likely image-only/scanned, with no text layer)."

# Phase 2, Step 1 — sanitized ZIP-package failure details. Never include a
# raw exception message, archive contents, or an unsanitized member name.
_ZIP_CORRUPT_DETAIL = "ZIP package could not be parsed — corrupt or malformed archive."
_ZIP_READ_FAILED_DETAIL = "ZIP package's selected member could not be read from the archive."
_ZIP_ENCRYPTED_DETAIL = "ZIP package rejected — archive contains an encrypted member."
_ZIP_TOO_MANY_MEMBERS_DETAIL = f"ZIP package rejected — exceeds the {MAX_ZIP_MEMBERS}-member safety limit."
_ZIP_TOTAL_SIZE_DETAIL = (
    f"ZIP package rejected — exceeds the {MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES // (1024 * 1024)}MB "
    "total uncompressed safety limit."
)
_ZIP_MEMBER_SIZE_DETAIL = (
    f"ZIP package rejected — a member exceeds the {MAX_ZIP_MEMBER_UNCOMPRESSED_BYTES // (1024 * 1024)}MB "
    "per-member uncompressed safety limit."
)
_ZIP_RATIO_DETAIL = f"ZIP package rejected — a member's compression ratio exceeds the {MAX_ZIP_COMPRESSION_RATIO}:1 safety limit."
_ZIP_UNSAFE_PATH_DETAIL = "ZIP package rejected — contains an unsafe (absolute, traversal, or drive-letter) member path."
_ZIP_NESTED_DETAIL = "ZIP package rejected — selected member is itself an archive; nested archives are not processed."
_ZIP_NO_PDF_DETAIL = "ZIP package contained no safe, allowlisted PDF document."
_ZIP_INVALID_PDF_DETAIL = "ZIP package's selected member did not contain valid PDF content."

# HTML-in-ZIP fallback (narrow extension of Phase 2, Step 1) — used ONLY
# when no safe .pdf member exists at all; see _select_safe_html_member
# and _extract_from_zip's own call-ordering docstring for exactly when
# this path is even reached. Never XBRL/XML-aware — the selected member
# is parsed with the same _LenientHtmlTextExtractor the bare/non-ZIP
# HTML path already uses, so a `.xbrl`/`.xsd`/`.xml` member is still
# never read, regardless of how "close" its name looks to a report body.
_HTML_EXTENSIONS = (".htm", ".html")
_HONBUN_MARKER = "honbun"
_ZIP_NO_HTML_DETAIL = "ZIP package contained no safe, allowlisted HTML document."
_ZIP_NO_SAFE_MEMBER_DETAIL = "ZIP package contained no safe, allowlisted PDF or HTML document."
_ZIP_HTML_UNDECODABLE_DETAIL = "ZIP package's selected HTML member is not valid UTF-8 text."
_ZIP_HTML_PARSE_FAILED_DETAIL = "ZIP package's selected HTML member could not be parsed."
_ZIP_HTML_NO_TEXT_DETAIL = "ZIP package's selected HTML member contained no extractable text."

# Section-aware annual-report selection (see module docstring). Scoped
# strictly to this one category string, which edinet_pipeline derives
# from the candidate's own already-recorded matched_rules — this module
# never routes, classifies, or re-evaluates a document itself.
ANNUAL_SECURITIES_REPORT_CATEGORY = "annual_securities_report"

# Hard ceiling on how many safe honbun members the annual-report search
# will READ (not merely scan metadata for). A real 有価証券報告書 package
# carries roughly one body member per statutory section, so six is
# generous for reaching the business/operational sections while staying a
# named, bounded limit rather than an open loop over an adversarial
# archive. Members beyond this bound are never read.
MAX_ANNUAL_REPORT_MEMBERS_SCANNED = 6

# Preferred issuer-specific section headings, in descending rank order
# (index 0 = best). These are the STANDARD statutory section headings of
# a Japanese annual securities report — the same "standard, documented
# terms, not independently live-verified in this repository" convention
# dart/ownership_materiality.MATERIAL_OWNERSHIP_MARKERS already uses, and
# the distinction is kept explicit here for the same reason: none of
# these strings was taken from, or verified against, a checked-in copy of
# a real filing body. A tuple per rank allows real heading variants to
# share one rank without implying one is a different-quality signal.
#
# The ordering is a research-evidence judgment, not a statutory one:
# production/orders/backlog and management's own analysis of results
# carry the most issuer-specific operational content; risk, strategy and
# capex follow; the bare 【事業の状況】 container heading sits last as a
# fallback, since matching it means we reached the business-situation
# body member but not a named sub-section within it.
PREFERRED_ANNUAL_REPORT_SECTIONS: tuple[tuple[str, ...], ...] = (
    ("【生産、受注及び販売の状況】",),
    ("【経営者による財政状態、経営成績及びキャッシュ・フローの状況の分析】",),
    ("【事業等のリスク】",),
    ("【経営方針、経営環境及び対処すべき課題等】",),
    ("【設備投資等の概要】", "【設備の状況】"),
    ("【事業の状況】",),
)

# Failure reasons meaning the archive itself could not be safely opened or
# trusted at all (corrupt structure at open time, or an encrypted member
# found during the metadata scan) map to PARSE_FAILED, matching this
# module's existing encrypted/corrupt-PDF convention. Every other
# metadata-scan or content-check rejection (limits, unsafe paths, no
# usable member, wrong content in the selected member) maps to
# UNSUPPORTED_FORMAT, matching the existing MAX_DOCUMENT_SIZE_BYTES
# precedent: a structurally-openable payload that safety policy declines
# to process further is UNSUPPORTED_FORMAT, not PARSE_FAILED.
_ZIP_PARSE_FAILED_DETAILS = frozenset({_ZIP_ENCRYPTED_DETAIL})


@dataclass(frozen=True)
class ExtractionResult:
    state: ExtractionState
    excerpt_original: str | None = None
    detail: str = ""
    # Phase 2, Step 2 — the selected ZIP member's safe archive-relative
    # path/name, set ONLY when this result came from a successful
    # (EXTRACTED) ZIP-member extraction (see _extract_from_zip). None for
    # every other path: bare PDF, plain-text/HTML, any non-EXTRACTED ZIP
    # outcome (unsupported/malformed/no-PDF/invalid-PDF/parse-failed).
    evidence_source_member: str | None = None
    # Section-aware annual-report selection — the statutory heading the
    # excerpt was actually anchored on, set ONLY by the bounded
    # annual-report section search (see _extract_annual_report_member).
    # None for every other path, including an annual-report package whose
    # search found no preferred section and fell back to largest-member
    # selection. Never a fabricated or inferred section: if this is set,
    # the heading was found verbatim in the selected member's own
    # extracted text.
    location_section: str | None = None


def _decode_if_plain_text(raw: bytes) -> str | None:
    """Returns decoded text only when the payload is plausibly plain
    text/HTML — never for binary content. A ZIP/PDF payload will fail
    UTF-8 decoding almost always (both are binary formats with byte
    sequences invalid as UTF-8), which is exactly the signal this
    function uses to refuse rather than guess. No Shift-JIS/legacy
    fallback is attempted here: unlike DART's Korean HWP/legacy-encoding
    problem (a real, confirmed encoding-variance issue), EDINET's actual
    text encoding for non-binary content is unconfirmed, and guessing an
    encoding chain for a format this module doesn't yet know the real
    shape of would be exactly the kind of premature assumption Gate 1 is
    meant to avoid."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _extract_pdf_text(document_bytes: bytes) -> ExtractionResult:
    """pypdf-based extraction (Gate 10.A) — every pypdf-raised exception
    (malformed structure, unsupported filter, truncated stream, etc.) is
    caught broadly and mapped to PARSE_FAILED with a safe, generic
    detail, same discipline the HTML-parser path below already uses:
    never a raw exception/stack trace surfaced to the caller. Text is
    normalized (collapsed whitespace) but never translated, summarized,
    or interpreted — this function only decides EXTRACTED vs.
    PARSE_FAILED and returns the bounded original-language excerpt."""
    try:
        reader = PdfReader(io.BytesIO(document_bytes))
        if reader.is_encrypted:
            return ExtractionResult(state=ExtractionState.PARSE_FAILED, detail=_PDF_ENCRYPTED_DETAIL)
        parts = [page.extract_text() or "" for page in reader.pages]
    except Exception:
        return ExtractionResult(state=ExtractionState.PARSE_FAILED, detail=_PDF_CORRUPT_DETAIL)

    excerpt = re.sub(r"\s+", " ", " ".join(parts)).strip()
    if not excerpt:
        return ExtractionResult(state=ExtractionState.PARSE_FAILED, detail=_PDF_NO_TEXT_DETAIL)
    return ExtractionResult(state=ExtractionState.EXTRACTED, excerpt_original=excerpt[:MAX_EXCERPT_CHARS])


def _is_safe_member_name(name: str) -> bool:
    """Rejects absolute paths, drive-letter paths, and any `..` path-
    traversal segment. Applied only to the `.pdf`-suffixed candidate this
    module would otherwise select — every other member is ignored
    entirely and never has its name safety-checked, since it's never
    read."""
    if not name:
        return False
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or _DRIVE_LETTER_RE.match(normalized):
        return False
    if ".." in normalized.split("/"):
        return False
    return True


def _select_safe_pdf_member(archive: "zipfile.ZipFile") -> tuple["zipfile.ZipInfo | None", str | None]:
    """Metadata-only scan of `archive.infolist()` — no member content is
    read here. Every check below runs on `ZipInfo` fields (name,
    file_size, compress_size, flag_bits) the ZIP central directory
    already provides without decompressing anything. Returns
    `(selected_info, None)` on success, or `(None, detail)` on rejection.

    Duplicate-name handling: the loop below returns the FIRST
    `.pdf`-suffixed entry encountered in `infolist()` order as a `ZipInfo`
    object, not a filename string — the caller reads that exact object
    (`archive.read(zip_info)`), which resolves duplicates deterministically
    regardless of `zipfile`'s own name-to-info dict (which keeps the LAST
    entry for a duplicate name)."""
    infos = archive.infolist()
    if len(infos) > MAX_ZIP_MEMBERS:
        return None, _ZIP_TOO_MANY_MEMBERS_DETAIL

    total_uncompressed = 0
    for info in infos:
        if info.flag_bits & 0x1:
            return None, _ZIP_ENCRYPTED_DETAIL
        if info.file_size > MAX_ZIP_MEMBER_UNCOMPRESSED_BYTES:
            return None, _ZIP_MEMBER_SIZE_DETAIL
        if info.file_size > 0 and (info.file_size / max(info.compress_size, 1)) > MAX_ZIP_COMPRESSION_RATIO:
            return None, _ZIP_RATIO_DETAIL
        total_uncompressed += info.file_size
    if total_uncompressed > MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES:
        return None, _ZIP_TOTAL_SIZE_DETAIL

    for info in infos:
        if not info.filename.lower().endswith(".pdf"):
            continue
        if not _is_safe_member_name(info.filename):
            return None, _ZIP_UNSAFE_PATH_DETAIL
        return info, None

    return None, _ZIP_NO_PDF_DETAIL


def _select_safe_html_member(archive: "zipfile.ZipFile") -> tuple["zipfile.ZipInfo | None", str | None]:
    """HTML-in-ZIP fallback — only ever called by _extract_from_zip after
    _select_safe_pdf_member has already returned exactly `_ZIP_NO_PDF_
    DETAIL` (a genuine "no .pdf member" result, with the archive's own
    metadata already having passed every safety check below during that
    call) — never called after an unsafe-path or safety-limit rejection,
    which fail the whole extraction closed immediately instead. Repeats
    the same metadata-only scan (member count, size, ratio, encryption)
    _select_safe_pdf_member already performed — deliberately duplicated,
    not shared, so this function's own safety is independently verifiable
    and this narrow addition never has to touch the pre-existing PDF
    selector's tested code path.

    Every `.htm`/`.html`-suffixed member's path is safety-checked (never
    only the ultimately-selected one) — any unsafe path among them fails
    the whole selection closed, matching the same fail-closed discipline
    an unsafe `.pdf` member already gets.

    Selection preference, among safe candidates only: a member whose
    basename (last `/`-separated path segment) contains "honbun" case-
    insensitively — EDINET's own real "body" document naming convention,
    confirmed live for docID S100Z0ID — wins over any other; otherwise
    the largest safe eligible member by declared uncompressed size (a
    real header/cover member is reliably much smaller than the actual
    report body). No `.xbrl`, `.xsd`, `.xml`, or any other extension is
    ever a candidate here."""
    infos = archive.infolist()
    if len(infos) > MAX_ZIP_MEMBERS:
        return None, _ZIP_TOO_MANY_MEMBERS_DETAIL

    total_uncompressed = 0
    for info in infos:
        if info.flag_bits & 0x1:
            return None, _ZIP_ENCRYPTED_DETAIL
        if info.file_size > MAX_ZIP_MEMBER_UNCOMPRESSED_BYTES:
            return None, _ZIP_MEMBER_SIZE_DETAIL
        if info.file_size > 0 and (info.file_size / max(info.compress_size, 1)) > MAX_ZIP_COMPRESSION_RATIO:
            return None, _ZIP_RATIO_DETAIL
        total_uncompressed += info.file_size
    if total_uncompressed > MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES:
        return None, _ZIP_TOTAL_SIZE_DETAIL

    candidates = []
    for info in infos:
        if not info.filename.lower().endswith(_HTML_EXTENSIONS):
            continue
        if not _is_safe_member_name(info.filename):
            return None, _ZIP_UNSAFE_PATH_DETAIL
        candidates.append(info)

    if not candidates:
        return None, _ZIP_NO_HTML_DETAIL

    honbun_candidates = [c for c in candidates if _HONBUN_MARKER in c.filename.rsplit("/", 1)[-1].lower()]
    pool = honbun_candidates or candidates
    return max(pool, key=lambda c: c.file_size), None


def _extract_zip_html_text(member_bytes: bytes) -> ExtractionResult:
    """Parses one already-selected, already-safety-checked ZIP HTML
    member with the exact same _LenientHtmlTextExtractor the bare/non-ZIP
    plain-text/HTML path (extract_excerpt's own final branch) already
    uses — never XBRL/XML-aware, purely tag-stripping text extraction.
    UTF-8 only, matching _decode_if_plain_text's own "no guessed encoding
    chain" discipline. Text is normalized (collapsed whitespace) but
    never translated, summarized, or interpreted."""
    try:
        text = member_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return ExtractionResult(state=ExtractionState.UNSUPPORTED_FORMAT, detail=_ZIP_HTML_UNDECODABLE_DETAIL)

    parser = _LenientHtmlTextExtractor()
    try:
        parser.feed(text)
    except Exception:
        return ExtractionResult(state=ExtractionState.PARSE_FAILED, detail=_ZIP_HTML_PARSE_FAILED_DETAIL)

    excerpt = re.sub(r"\s+", " ", " ".join(parser.text_parts)).strip()
    if not excerpt:
        return ExtractionResult(state=ExtractionState.PARSE_FAILED, detail=_ZIP_HTML_NO_TEXT_DETAIL)
    return ExtractionResult(state=ExtractionState.EXTRACTED, excerpt_original=excerpt[:MAX_EXCERPT_CHARS])


def _normalize_heading_text(text: str) -> str:
    """NFKC only — the project's existing CJK normalization convention,
    already used by material_event_shadow._normalize and
    src.logic.theme_matching.normalize_text, reused rather than invented
    here. Applied ONLY as a second-chance matching pass (see
    _first_preferred_heading's caller): the statutory headings below are
    kanji inside full-width 【】 brackets, which NFKC leaves untouched, so
    the raw text virtually always matches first and the excerpt returned
    is the extractor's own unmodified text."""
    return unicodedata.normalize("NFKC", text)


def _member_plain_text(member_bytes: bytes) -> str | None:
    """Decode + tag-strip one already-safety-checked HTML member with the
    same _LenientHtmlTextExtractor every other HTML path in this module
    uses. Returns None — never raises — for a member that isn't valid
    UTF-8, can't be parsed, or yields no text, so one unusable member in
    an annual-report package never fails the whole search."""
    try:
        text = member_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return None
    parser = _LenientHtmlTextExtractor()
    try:
        parser.feed(text)
    except Exception:
        return None
    return re.sub(r"\s+", " ", " ".join(parser.text_parts)).strip() or None


def _first_preferred_heading(text: str) -> tuple[int, str, int] | None:
    """Best-ranked preferred heading present in `text`, as
    (rank, heading, character_index), or None. Ranks are iterated
    outermost so the return is always the BEST rank available in this
    text, never merely the earliest occurrence; within one rank, the
    first variant found wins."""
    for rank, variants in enumerate(PREFERRED_ANNUAL_REPORT_SECTIONS):
        for heading in variants:
            index = text.find(heading)
            if index >= 0:
                return rank, heading, index
    return None


def _safe_honbun_members(archive: "zipfile.ZipFile") -> tuple[list["zipfile.ZipInfo"] | None, str | None]:
    """Metadata-only scan (no member content read), identical in every
    check to _select_safe_html_member's own — deliberately duplicated
    rather than shared, on the same reasoning that function documents:
    this narrow addition never has to touch the pre-existing, tested
    selector's code path, and its safety is independently verifiable.

    Returns `(members, None)` with the safe `honbun`-marked HTML members
    in `infolist()` order, or `(None, detail)` on any safety rejection.
    A rejection here is never turned into an extraction result by this
    module: _extract_from_zip falls through to _select_safe_html_member,
    which repeats the same checks and fails closed with the same detail,
    so the annual path can never weaken or bypass a safety bound."""
    infos = archive.infolist()
    if len(infos) > MAX_ZIP_MEMBERS:
        return None, _ZIP_TOO_MANY_MEMBERS_DETAIL

    total_uncompressed = 0
    for info in infos:
        if info.flag_bits & 0x1:
            return None, _ZIP_ENCRYPTED_DETAIL
        if info.file_size > MAX_ZIP_MEMBER_UNCOMPRESSED_BYTES:
            return None, _ZIP_MEMBER_SIZE_DETAIL
        if info.file_size > 0 and (info.file_size / max(info.compress_size, 1)) > MAX_ZIP_COMPRESSION_RATIO:
            return None, _ZIP_RATIO_DETAIL
        total_uncompressed += info.file_size
    if total_uncompressed > MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES:
        return None, _ZIP_TOTAL_SIZE_DETAIL

    members: list["zipfile.ZipInfo"] = []
    for info in infos:
        if not info.filename.lower().endswith(_HTML_EXTENSIONS):
            continue
        if not _is_safe_member_name(info.filename):
            return None, _ZIP_UNSAFE_PATH_DETAIL
        if _HONBUN_MARKER in info.filename.rsplit("/", 1)[-1].lower():
            members.append(info)
    return members, None


def _extract_annual_report_member(archive: "zipfile.ZipFile") -> ExtractionResult | None:
    """Bounded, content-based preferred-section search across at most
    MAX_ANNUAL_REPORT_MEMBERS_SCANNED safe honbun members, in
    `infolist()` order. Returns an EXTRACTED result anchored at the
    best-ranked heading found, or None meaning "no preferred section —
    caller should use the pre-existing fallback unchanged". Never raises,
    and never returns a non-EXTRACTED result: every failure mode here is
    a fall-through, so the legacy path decides the final outcome and this
    addition can only improve a selection, never break one."""
    members, _failure_detail = _safe_honbun_members(archive)
    if members is None:
        return None

    best_rank: int | None = None
    best_heading: str | None = None
    best_info: "zipfile.ZipInfo | None" = None
    best_text: str | None = None
    best_index = 0

    for info in members[:MAX_ANNUAL_REPORT_MEMBERS_SCANNED]:
        try:
            member_bytes = archive.read(info)
        except (zipfile.BadZipFile, RuntimeError, KeyError):
            continue
        if member_bytes.startswith(_ZIP_MAGIC):
            continue  # nested archive — never processed, same as the fallback path
        text = _member_plain_text(member_bytes)
        if text is None:
            continue
        hit = _first_preferred_heading(text)
        if hit is None:
            normalized = _normalize_heading_text(text)
            hit = _first_preferred_heading(normalized)
            if hit is not None:
                text = normalized  # anchor in the exact string the heading matched in
        if hit is None:
            continue
        rank, heading, index = hit
        if best_rank is None or rank < best_rank:
            best_rank, best_heading, best_info, best_text, best_index = rank, heading, info, text, index
            if rank == 0:
                break  # no better rank exists; stop reading further members

    if best_info is None or best_text is None:
        return None

    excerpt = best_text[best_index:best_index + MAX_EXCERPT_CHARS].strip()
    if not excerpt:
        return None
    return ExtractionResult(
        state=ExtractionState.EXTRACTED,
        excerpt_original=excerpt,
        evidence_source_member=best_info.filename,
        location_section=best_heading,
    )


def _extract_from_zip(document_bytes: bytes, category: str | None = None) -> ExtractionResult:
    """Bounded ZIP-package extraction (Phase 2, Step 1; provenance added
    Step 2; HTML fallback added as a narrow, later extension). Opens the
    archive in memory only (`io.BytesIO` — never written to disk),
    validates `archive.infolist()` metadata before reading any member
    content, and selects at most one safe member. No nested archive is
    followed. Never raises.

    Selection order — PDF remains the unconditional first choice: only
    when `_select_safe_pdf_member` returns exactly `_ZIP_NO_PDF_DETAIL`
    (a genuine "no .pdf member, and the archive's own metadata already
    passed every safety check" result) does `_select_safe_html_member`
    ever run. Every other PDF-selection outcome — a safety-limit breach
    (member count/size/ratio/encryption) or an unsafe `.pdf` path — fails
    the whole extraction closed immediately, exactly as before this
    fallback existed; HTML is never considered in that case. No `.xbrl`,
    `.xsd`, or `.xml` member is ever read by either selector.

    `evidence_source_member` is stamped onto the result ONLY when the
    selected member's own extraction reports EXTRACTED — every other
    outcome returns that inner result unchanged, with
    `evidence_source_member` left at its default of None, so provenance
    is never recorded for a member whose content didn't actually yield a
    usable excerpt."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(document_bytes))
    except zipfile.BadZipFile:
        return ExtractionResult(state=ExtractionState.PARSE_FAILED, detail=_ZIP_CORRUPT_DETAIL)

    with archive:
        selected, failure_detail = _select_safe_pdf_member(archive)
        member_kind = "pdf"
        if selected is None and failure_detail == _ZIP_NO_PDF_DETAIL:
            # Section-aware annual-report selection runs here and ONLY
            # here — strictly after the genuine "no safe .pdf member"
            # result, so PDF remains the unconditional first choice and
            # every other PDF-selection outcome (safety-limit breach,
            # unsafe path) still fails the whole extraction closed below
            # without this path ever being consulted. A None return means
            # "no preferred section found"; control then falls through to
            # the pre-existing largest-member fallback, unchanged.
            if category == ANNUAL_SECURITIES_REPORT_CATEGORY:
                annual_result = _extract_annual_report_member(archive)
                if annual_result is not None:
                    return annual_result
            selected, failure_detail = _select_safe_html_member(archive)
            member_kind = "html"
        if selected is None:
            state = ExtractionState.PARSE_FAILED if failure_detail in _ZIP_PARSE_FAILED_DETAILS else ExtractionState.UNSUPPORTED_FORMAT
            detail = _ZIP_NO_SAFE_MEMBER_DETAIL if failure_detail == _ZIP_NO_HTML_DETAIL else failure_detail
            return ExtractionResult(state=state, detail=detail)
        try:
            member_bytes = archive.read(selected)
        except (zipfile.BadZipFile, RuntimeError, KeyError):
            return ExtractionResult(state=ExtractionState.PARSE_FAILED, detail=_ZIP_READ_FAILED_DETAIL)

    if member_bytes.startswith(_ZIP_MAGIC):
        return ExtractionResult(state=ExtractionState.UNSUPPORTED_FORMAT, detail=_ZIP_NESTED_DETAIL)

    if member_kind == "pdf":
        if not member_bytes.startswith(_PDF_MAGIC):
            return ExtractionResult(state=ExtractionState.UNSUPPORTED_FORMAT, detail=_ZIP_INVALID_PDF_DETAIL)
        inner_result = _extract_pdf_text(member_bytes)
    else:
        inner_result = _extract_zip_html_text(member_bytes)

    if inner_result.state != ExtractionState.EXTRACTED:
        return inner_result
    return ExtractionResult(
        state=inner_result.state,
        excerpt_original=inner_result.excerpt_original,
        detail=inner_result.detail,
        evidence_source_member=selected.filename,
    )


def extract_excerpt(document_bytes: bytes, category: str | None = None) -> ExtractionResult:
    """Pure function over already-fetched bytes — network I/O and the
    per-docID cache/dedup live in document_service.py, one layer up
    (same separation DART/EDGAR's own document_service.py modules use).

    `category` is the routing category edinet_pipeline already derived
    from the candidate's own matched_rules — additive, defaulting to
    None, and consulted ONLY to enable the bounded annual-report section
    search inside _extract_from_zip (see module docstring). Every other
    value, None included, reproduces this module's prior behavior
    exactly, on every path: bare PDF, plain text/HTML, and non-annual ZIP
    packages are untouched by its presence."""
    if len(document_bytes) > MAX_DOCUMENT_SIZE_BYTES:
        return ExtractionResult(
            state=ExtractionState.UNSUPPORTED_FORMAT,
            detail=f"Document exceeds the {MAX_DOCUMENT_SIZE_BYTES // (1024 * 1024)}MB safety limit.",
        )

    if document_bytes.startswith(_ZIP_MAGIC):
        return _extract_from_zip(document_bytes, category)

    if document_bytes.startswith(_PDF_MAGIC):
        return _extract_pdf_text(document_bytes)

    text = _decode_if_plain_text(document_bytes)
    if text is None:
        return ExtractionResult(state=ExtractionState.UNSUPPORTED_FORMAT, detail=_UNSUPPORTED_BINARY_DETAIL)

    parser = _LenientHtmlTextExtractor()
    try:
        parser.feed(text)
    except Exception:
        return ExtractionResult(state=ExtractionState.PARSE_FAILED, detail="Document could not be parsed.")

    excerpt = re.sub(r"\s+", " ", " ".join(parser.text_parts)).strip()
    if not excerpt and "<" not in text:
        # Plain-text (non-HTML, non-binary) document — no tags for the
        # HTML parser to walk. Same "only for genuinely non-HTML input"
        # gate EDGAR's own extractor uses.
        excerpt = re.sub(r"\s+", " ", text).strip()

    if not excerpt:
        return ExtractionResult(state=ExtractionState.PARSE_FAILED, detail="Document parsed but contained no extractable text.")

    return ExtractionResult(state=ExtractionState.EXTRACTED, excerpt_original=excerpt[:MAX_EXCERPT_CHARS])
