"""Public filing-card display helpers — title, Summary, the raw-
extraction quality gate, an EDINET-only machine-artifact cleanup, an
excerpt-completeness disclosure, and a compact official-filing-reference
line. Pure functions only: no Streamlit, no I/O, no translation-provider
calls, no state writes. Generic, deterministic heuristics only
(regex-based structural signals) — never issuer- or filing-specific
rules, never a fabricated or inferred claim.

Fixes the raw-XBRL-in-the-public-card defect (some EDGAR filings, e.g. a
Form 10-Q, store an extraction dominated by XML/XBRL tags, taxonomy
namespace prefixes, and machine identifiers instead of readable prose —
see `is_readable_extracted_text`) and the "8-K filing"-style non-title
EDGAR `report_nm` defect (see `display_title`/`_edgar_display_title`).

Filing-card machine-artifact / excerpt-honesty fix (design/DECISIONS.md):
adds `strip_edinet_machine_artifacts` (start-anchored removal of
EDINET's own leaked cover-page document-title-timestamp label and/or
leading item-heading bracket — see that function's own comment),
`excerpt_may_be_incomplete` (an honest, hedged signal that an excerpt may
have been cut by the shared extraction cap), and
`official_filing_reference` (a provider-accurate compact metadata line).
None of these three call each other or `extractive_summary`/
`is_readable_extracted_text` — the caller (radar_card.py) sequences them.
"""
from __future__ import annotations

import re

from src.data_access.edgar.edgar_rules import normalize_form_type
from src.models.models import CandidateSignal, FilingEvent

EDGAR_SOURCE_NAME = "SEC EDGAR"
EDINET_SOURCE_NAME = "EDINET"

# Deterministic, source-safe display titles for the SEC form types this
# app already recognizes (see edgar_rules.FORM_TYPE_CATEGORIES/_FORM_
# ALIASES — normalize_form_type is reused so the same real-world spelling
# variance that module already resolves, e.g. SEC's live "SCHEDULE 13G"
# vs. the abbreviated "SC 13G", is handled identically here) plus the
# four milestone-8-brief examples (10-K/10-Q/8-K/6-K). Every phrase below
# is SEC's own well-known, universally-documented description of what
# the form type legally is (an annual/quarterly/current report, a
# registration statement, a prospectus supplement, a beneficial-ownership
# report) — never an inferred subject, outcome, or characterization of
# THIS specific filing's content. The form identifier is always preserved
# verbatim in the phrase.
_EDGAR_FORM_TITLES: dict[str, str] = {
    "10-K": "Annual Report — Form 10-K",
    "10-K/A": "Annual Report (Amendment) — Form 10-K/A",
    "10-Q": "Quarterly Report — Form 10-Q",
    "10-Q/A": "Quarterly Report (Amendment) — Form 10-Q/A",
    "8-K": "Current Report — Form 8-K",
    "8-K/A": "Current Report (Amendment) — Form 8-K/A",
    "6-K": "Foreign Private Issuer Report — Form 6-K",
    "6-K/A": "Foreign Private Issuer Report (Amendment) — Form 6-K/A",
    "SC 13D": "Beneficial Ownership Report — Schedule 13D",
    "SC 13D/A": "Beneficial Ownership Report (Amendment) — Schedule 13D/A",
    "SC 13G": "Beneficial Ownership Report — Schedule 13G",
    "SC 13G/A": "Beneficial Ownership Report (Amendment) — Schedule 13G/A",
    "S-1": "Registration Statement — Form S-1",
    "S-1/A": "Registration Statement (Amendment) — Form S-1/A",
    "S-3": "Registration Statement — Form S-3",
    "S-3/A": "Registration Statement (Amendment) — Form S-3/A",
    "424B1": "Prospectus Supplement — Form 424B1",
    "424B2": "Prospectus Supplement — Form 424B2",
    "424B3": "Prospectus Supplement — Form 424B3",
    "424B4": "Prospectus Supplement — Form 424B4",
    "424B5": "Prospectus Supplement — Form 424B5",
}


def is_english_native(filing: FilingEvent) -> bool:
    return filing.original_language == "English"


def _edgar_display_title(filing: FilingEvent) -> str:
    """A known form type always wins deterministically (see
    _EDGAR_FORM_TITLES's own docstring for why: `report_nm` for a
    standard periodic report is usually just the bare form code itself —
    SEC's `primaryDocDescription` rarely adds anything for these — so
    always preferring the curated phrase is both the common case and the
    correct one). For a form type this table doesn't recognize, prefers
    `report_nm` when it is a genuinely distinct, already-human-readable
    official description (not just the form code restated) — appending
    the form identifier so it's always preserved either way. Never
    invents a description for an unrecognized, description-less form."""
    raw_form = (filing.pblntf_ty or "").strip()
    normalized_form = normalize_form_type(raw_form) if raw_form else ""
    mapped = _EDGAR_FORM_TITLES.get(normalized_form)
    if mapped:
        return mapped

    description = (filing.report_nm or "").strip()
    is_bare_form_code = bool(description) and description.upper() in {raw_form.upper(), normalized_form.upper()}
    if description and not is_bare_form_code:
        return f"{description} — Form {raw_form}" if raw_form else description
    if raw_form:
        return f"Form {raw_form}"
    return description or "Filing"


def display_title(filing: FilingEvent, candidate: CandidateSignal | None) -> str:
    """The public card's one title line.

    EDGAR (English-native): a deterministic, source-safe mapping from
    the official SEC form type — see _edgar_display_title. DART/EDINET:
    unchanged from the pre-existing behavior — the stored title
    translation when one exists, otherwise the filing's own native
    official title verbatim. Never a fabricated or inferred title."""
    if is_english_native(filing):
        return _edgar_display_title(filing)
    if candidate is not None and candidate.title_translation is not None:
        return candidate.title_translation.translated_text
    return filing.report_nm


# ============================================================
# Extraction quality gate (C) — reject raw XML/XBRL/tag-heavy content
# ============================================================

# Any XML/SGML-style tag, e.g. "<us-gaap:Revenues ...>" or "</xbrli:context>".
_XML_TAG_PATTERN = re.compile(r"</?[a-zA-Z][\w:\-]*(?:\s+[^<>]*)?/?>")
# A colon-delimited namespace:localname token, the XBRL element-naming
# convention generically (e.g. "xbrli:context", "us-gaap:Revenues",
# "dei:EntityRegistrantName") — ASCII-only so it never fires on Korean/
# Japanese prose (which has no colon-joined Latin identifiers).
_NAMESPACE_TOKEN_PATTERN = re.compile(r"\b[a-zA-Z][\w\-]{1,20}:[A-Za-z][\w\-]{1,60}\b")
# A taxonomy/schema URL — the exact "http://fasb.org/" example the spec
# names, generalized to the handful of real, well-known XBRL taxonomy
# hosts (never issuer-specific).
_TAXONOMY_URL_PATTERN = re.compile(
    r"https?://[^\s\"'<>]*(?:fasb\.org|xbrl\.sec\.gov|xbrl\.us|xbrl\.org|sec\.gov/(?:cgi-bin|Archives)\S*\.xsd)\S*",
    re.IGNORECASE,
)
_ANY_URL_PATTERN = re.compile(r"https?://\S+")
# A long, unbroken, letter-led ASCII token — the shape of an XBRL
# element/context/unit identifier (e.g.
# "RevenueFromContractWithCustomerExcludingAssessedTax",
# "FD2026Q3QTD_us-gaap_StatementClassOfStockAxis"). ASCII-only and
# letter-led so it never matches an ordinary unbroken run of Korean/
# Japanese characters (those scripts have no inter-word spaces, so a
# normal CJK sentence is itself one long \w+ run — an ASCII-restricted
# pattern is what keeps this generic check language-agnostic).
_LONG_MACHINE_TOKEN_PATTERN = re.compile(r"\b[A-Za-z][A-Za-z0-9_\-.]{19,}\b")
# A "word" in any script — Python's Unicode-aware \w already treats
# Hangul/Kanji/Hiragana/Katakana as letters, so this needs no per-
# language special-casing.
_WORD_PATTERN = re.compile(r"[^\W\d_]{2,}")
# Sentence-ending punctuation, Latin and the CJK equivalents, followed by
# whitespace or end-of-string (a bare decimal point, e.g. "Item 2.02",
# never counts — the digit after it fails this pattern).
_SENTENCE_END_PATTERN = re.compile(r"[.!?。！？](?:\s|$)")

_MARKUP_DENSITY_PER_100_CHARS_LIMIT = 1.5
_URL_DENSITY_PER_200_CHARS_LIMIT = 1.0
_MIN_NATURAL_LANGUAGE_CHAR_RATIO = 0.45
_SENTENCE_CHECK_MIN_LENGTH = 200


def is_readable_extracted_text(text: str | None) -> bool:
    """True only for text a reader could plausibly read as prose — never
    for text substantially dominated by XML/XBRL markup, taxonomy URLs,
    long machine-style identifiers, excessive URL/tag density, or
    unnaturally low natural-language word/sentence density. Every check
    is a generic structural/statistical signal over the text itself —
    never an issuer-, company-, or filing-specific rule. Ordinary
    financial/legal prose (short sentences, the occasional decimal
    number, a citation) is never over-filtered; language-agnostic (a
    Korean or Japanese excerpt is judged the same way as an English one,
    never by counting ASCII letters alone)."""
    if not text:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    length = len(stripped)

    xml_tag_hits = len(_XML_TAG_PATTERN.findall(stripped))
    namespace_hits = len(_NAMESPACE_TOKEN_PATTERN.findall(stripped))
    long_token_hits = len(_LONG_MACHINE_TOKEN_PATTERN.findall(stripped))
    markup_density = (xml_tag_hits + namespace_hits + long_token_hits) / max(1, length / 100)
    if markup_density > _MARKUP_DENSITY_PER_100_CHARS_LIMIT:
        return False

    if _TAXONOMY_URL_PATTERN.search(stripped):
        return False

    any_url_hits = len(_ANY_URL_PATTERN.findall(stripped))
    if any_url_hits / max(1, length / 200) > _URL_DENSITY_PER_200_CHARS_LIMIT:
        return False

    words = _WORD_PATTERN.findall(stripped)
    natural_language_ratio = sum(len(w) for w in words) / length
    if natural_language_ratio < _MIN_NATURAL_LANGUAGE_CHAR_RATIO:
        return False

    if length > _SENTENCE_CHECK_MIN_LENGTH and not _SENTENCE_END_PATTERN.search(stripped):
        return False

    return True


# ============================================================
# Summary (B)
# ============================================================

_SUMMARY_MAX_CHARS = 320
# Filing-card summary/translation presentation fix (design/DECISIONS.md):
# a bounded, secondary search window only — never unbounded — used solely
# to let a summary that must run slightly past the target still end on a
# complete sentence rather than being cut. Not a second, looser target;
# _SUMMARY_MAX_CHARS above remains the normal length every summary aims
# for first.
_SUMMARY_MAX_SEARCH_CHARS = 640


def extractive_summary(text: str) -> str:
    """A concise summary grounded ONLY in `text` itself — literally a
    leading substring of it, never a paraphrase, inference, or generated
    claim. Stops at the last complete sentence boundary at or before
    _SUMMARY_MAX_CHARS when one exists there. When none exists inside
    that target window, searches forward — bounded by
    _SUMMARY_MAX_SEARCH_CHARS, never unbounded — for the first sentence
    boundary beyond the target, so a summary that must run slightly long
    still ends on a complete sentence. If no sentence boundary exists
    anywhere inside that bounded window either, returns "" rather than a
    word-boundary-truncated excerpt plus an ellipsis: this function has
    no access to the filing/title/date needed to build the metadata-only
    fallback itself, so an empty return is the caller's signal to use
    that fallback instead. Text that already fits within
    _SUMMARY_MAX_CHARS with no sentence boundary at all is returned
    verbatim, unchanged from before — nothing about it needed cutting, so
    it is not a truncation case. Caller is responsible for only ever
    passing text that has already passed `is_readable_extracted_text`."""
    normalized = " ".join(text.split())
    if not normalized:
        return ""

    boundary = None
    for match in _SENTENCE_END_PATTERN.finditer(normalized):
        if match.end() > _SUMMARY_MAX_CHARS:
            break
        boundary = match.end()
    if boundary:
        return normalized[:boundary].strip()

    if len(normalized) <= _SUMMARY_MAX_CHARS:
        return normalized

    match = _SENTENCE_END_PATTERN.search(normalized)
    if match and match.end() <= _SUMMARY_MAX_SEARCH_CHARS:
        return normalized[:match.end()].strip()

    return ""


# ============================================================
# EDINET-only machine-artifact cleanup (D)
# ============================================================

# EDINET's inline-XBRL cover page embeds its own internal machine-
# generated document-title cell (e.g. native "臨時報告書_20260909153311"
# / translated "Extraordinary Report_20260909153311" — a short label
# followed by "_" and a 14-digit YYYYMMDDHHMMSS timestamp) directly as
# visible text. The shared, deliberately lenient HTML-to-text extractor
# (dart/document_extractor.py's _LenientHtmlTextExtractor, reused by
# EDINET's own extractor) has no cover-page awareness the way DART's own
# SECTION-1 skip or EDGAR's own Item-header anchoring do, so this label
# is captured verbatim into excerpt_original and, sitting at the very
# front of the bounded excerpt, translated verbatim too. Evidenced live
# for docID S100Z0OT — EDINET-specific, deliberately never applied to
# DART/EDGAR, whose own extraction already keeps this class of artifact
# out (see each provider's own document_extractor.py).
#
# Bounded to a short run of plain words with no embedded underscore or
# punctuation of their own — specifically so this can never consume real
# mid-sentence prose that merely happens to contain a "word_14digits"
# shape somewhere later in the text. Only a genuine leading label, never
# anything else, can ever match starting at position 0.
_EDINET_TITLE_TIMESTAMP_PREFIX_RE = re.compile(r"^[^\W_]+(?: [^\W_]+){0,6}_\d{14}\s*")
# EDINET's native item-heading convention numbers each disclosure item
# and wraps its heading in full-width brackets (e.g. "１【提出理由】");
# DeepL's English translation of that same heading typically renders as
# an ASCII-bracketed "1 [Reason for Submission]". Both bracket styles are
# handled by one pattern; \d here already matches EDINET's own full-width
# numerals (e.g. "１") under Python's default Unicode \d behavior — no
# ASCII-only restriction needed, the same principle dedup.py's own
# Unicode-safe normalizer already relies on. Anchored to the very start
# of the text only — a bracketed reference occurring anywhere else in
# real prose (e.g. "[Note 1]" mid-sentence) can never match this pattern.
_EDINET_ITEM_HEADING_PREFIX_RE = re.compile(r"^\d{1,2}\s*[\[【][^\]】]{1,80}[\]】]\s*")
_EDINET_ARTIFACT_STRIP_MAX_ITERATIONS = 3


def strip_edinet_machine_artifacts(text: str) -> str:
    """Removes, from the START of `text` only, EDINET's own machine-
    generated cover-page document-title-timestamp label and/or a leading
    numbered item-heading bracket — never touching either shape wherever
    it occurs elsewhere in the text. Runs in a small, bounded loop (never
    unbounded) so a title-timestamp prefix immediately followed by a
    heading prefix are both removed, in whichever order they appear. A
    no-op (returns `text` unchanged) whenever neither pattern matches at
    the current start — including every already-clean text, and any text
    where a matching shape exists but not at position 0.

    This function has no notion of source/provider — the caller decides
    WHEN to call it (EDINET only; never DART or EDGAR, see this
    function's own module-level comment above for why)."""
    if not text:
        return text
    cleaned = text
    for _ in range(_EDINET_ARTIFACT_STRIP_MAX_ITERATIONS):
        match = _EDINET_TITLE_TIMESTAMP_PREFIX_RE.match(cleaned) or _EDINET_ITEM_HEADING_PREFIX_RE.match(cleaned)
        if not match:
            break
        cleaned = cleaned[match.end():]
    return cleaned


# ============================================================
# Excerpt-completeness disclosure (E)
# ============================================================

# Mirrors, rather than imports, the 600-char MAX_EXCERPT_CHARS constant
# independently defined in dart/edgar/edinet's own document_extractor.py
# modules (the same accepted duplication already used for that constant
# across all three) — this module stays a pure presentation layer with
# no dependency on extraction internals. An excerpt_original at or above
# this length was almost certainly cut by that hard character-count
# extraction cap; a real document's extractable content coincidentally
# ending at exactly this length is not a realistic case.
#
# EDGAR's own 8-K item-anchored extraction path uses a separate, larger
# cap (edgar/document_extractor.py's EIGHT_K_ITEM_EXCERPT_CHARS, 1200)
# that this presentation layer has no way to know was used for any given
# excerpt without a new extraction-side field (out of scope for this
# batch). Checking only against the smaller, shared 600-char cap means
# this can, in that one narrow EDGAR sub-case, under-flag a genuinely
# complete excerpt rather than ever falsely asserting incompleteness as
# an unqualified fact; the required wording is deliberately hedged ("may
# be incomplete") for exactly this reason and must stay that way.
_KNOWN_EXTRACTION_CAP_CHARS = 600


def excerpt_may_be_incomplete(excerpt_original: str | None) -> bool:
    """True only when `excerpt_original` (the ORIGINAL-language excerpt's
    own length — never a translation's) is at or beyond the shared
    extraction cap. Never call this with a translated string: translation
    changes character count independent of whether the source itself was
    truncated, so only the original's length is a meaningful signal."""
    return len(excerpt_original or "") >= _KNOWN_EXTRACTION_CAP_CHARS


# ============================================================
# Official filing reference (F)
# ============================================================


def official_filing_reference(filing: FilingEvent, filed_label: str | None) -> str:
    """A compact, labeled line of official identifying metadata for the
    reference block shown near the card's source action — built only
    from FilingEvent's own already-stored fields (see FilingEvent's own
    docstring for the source_name-dependent meaning of corp_code/
    stock_code/rcept_no), never a new fetch or an inferred value.

    Provider-accurate labels only: EDINET's corp_code is genuinely its
    own EDINET issuer code; DART's and EDGAR's corp_code are their own,
    different provider-specific issuer identifiers and are never
    mislabeled as an "EDINET" value. Any field that is empty/absent for
    this filing is simply omitted, never shown as a placeholder."""
    if filing.source_name == EDINET_SOURCE_NAME:
        issuer_label, doc_label = "EDINET issuer code", "Document ID"
    elif filing.source_name == EDGAR_SOURCE_NAME:
        issuer_label, doc_label = "CIK", "Accession number"
    else:
        issuer_label, doc_label = "DART issuer code", "Receipt number"

    parts = [f"Provider: {filing.source_name}"]
    if filing.corp_code:
        parts.append(f"{issuer_label}: {filing.corp_code}")
    if filing.stock_code:
        parts.append(f"Securities code: {filing.stock_code}")
    if filing.rcept_no:
        parts.append(f"{doc_label}: {filing.rcept_no}")
    if filed_label:
        parts.append(f"Filed: {filed_label}")
    return " · ".join(parts)


def metadata_only_summary(filing: FilingEvent, display_title_text: str, filed_label: str | None) -> str:
    """The neutral, factual fallback Summary used whenever no readable
    source text is available: exactly "{Company} filed {display title}
    on {filed date}." (or, with no parseable filed date, "{Company}
    filed {display title}." — never a fabricated date). States only what
    official metadata already establishes — never an investment
    conclusion, an inferred financial result, a characterization of
    importance, or any technical/process label describing the fallback
    itself (no "metadata-only", "pending", "unavailable", etc.)."""
    company = filing.corp_name.strip() or "The company"
    if filed_label:
        return f"{company} filed {display_title_text} on {filed_label}."
    return f"{company} filed {display_title_text}."
