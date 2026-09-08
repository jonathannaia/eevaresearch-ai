"""Public filing-card display helpers — title, Summary, and the raw-
extraction quality gate. Pure functions only: no Streamlit, no I/O, no
translation-provider calls, no state writes. Generic, deterministic
heuristics only (regex-based structural signals) — never issuer- or
filing-specific rules, never a fabricated or inferred claim.

Fixes the raw-XBRL-in-the-public-card defect (some EDGAR filings, e.g. a
Form 10-Q, store an extraction dominated by XML/XBRL tags, taxonomy
namespace prefixes, and machine identifiers instead of readable prose —
see `is_readable_extracted_text`) and the "8-K filing"-style non-title
EDGAR `report_nm` defect (see `display_title`/`_edgar_display_title`).
"""
from __future__ import annotations

import re

from src.data_access.edgar.edgar_rules import normalize_form_type
from src.models.models import CandidateSignal, FilingEvent

EDGAR_SOURCE_NAME = "SEC EDGAR"

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


def extractive_summary(text: str) -> str:
    """A concise summary grounded ONLY in `text` itself — literally a
    leading substring of it, never a paraphrase, inference, or generated
    claim. Stops at the last complete sentence boundary at or before the
    length cap when one exists; otherwise hard-truncates at a word
    boundary. Caller is responsible for only ever passing text that has
    already passed `is_readable_extracted_text`."""
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
    truncated = normalized[:_SUMMARY_MAX_CHARS]
    last_space = truncated.rfind(" ")
    if last_space > 40:
        truncated = truncated[:last_space]
    return truncated.rstrip(" ,;:") + "…"


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
