"""Eeva-authored summary generation for Daily News Slice 1 — grounded,
bounded, and honest about what it actually is: an extractive excerpt of
the feed's own description/summary field (HTML-stripped, normalized,
trimmed to a bounded length at a sentence boundary), never an
independent generative rewrite. No LLM or outside API call is made here
— none is approved this slice — so "in Eeva's own words" this round
means "condensed from the company's own permitted text," not "written
from scratch by a model." A later, separately-approved slice may add
real generative summarization; this module's job until then is to never
overclaim what it's doing.

Never fetches a linked page — only ever reads the title/description
already present on the feed entry itself (see rss_atom_client.py).
Never produces investment advice, price implications, valuation claims,
or any fact not already stated in the source text, because it never
adds anything: it only selects and trims.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass

MAX_SUMMARY_CHARS = 400
FALLBACK_SENTENCE = "The company published this update through its official Investor Relations channel."

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")

# Unicode block ranges for a simple, dependency-free non-Latin-script
# heuristic — Hangul (Korean), Hiragana/Katakana (Japanese kana), and
# the shared CJK Unified Ideographs block (Han/Kanji). Good enough to
# decide "this needs a real translation, not a summary" without any
# language-detection library.
_NON_LATIN_RANGES = (
    (0xAC00, 0xD7A3),  # Hangul syllables
    (0x3040, 0x309F),  # Hiragana
    (0x30A0, 0x30FF),  # Katakana
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
)


class UngroundedSummaryError(ValueError):
    """Raised if summary generation is ever attempted without a
    validated source URL — structurally impossible to produce a
    "grounded" summary with nothing to ground it in."""


@dataclass(frozen=True)
class SummaryResult:
    eeva_summary: str | None
    is_fallback: bool
    translation_unavailable: bool
    original_title: str | None


def _contains_non_latin_script(text: str) -> bool:
    return any(
        any(low <= ord(ch) <= high for low, high in _NON_LATIN_RANGES)
        for ch in text
    )


def _strip_html(text: str) -> str:
    unescaped = html.unescape(text)
    no_tags = _HTML_TAG_RE.sub(" ", unescaped)
    return _WHITESPACE_RE.sub(" ", no_tags).strip()


def _extractive_excerpt(raw_description: str) -> str:
    cleaned = _strip_html(raw_description)
    if not cleaned:
        return ""
    if len(cleaned) <= MAX_SUMMARY_CHARS:
        return cleaned

    excerpt = ""
    for sentence in _SENTENCE_END_RE.split(cleaned):
        candidate = f"{excerpt} {sentence}".strip() if excerpt else sentence
        if len(candidate) > MAX_SUMMARY_CHARS:
            break
        excerpt = candidate
    if not excerpt:
        excerpt = cleaned[:MAX_SUMMARY_CHARS].rsplit(" ", 1)[0]
    return excerpt.strip()


def generate_summary(title: str, description: str | None, has_valid_source_url: bool) -> SummaryResult:
    """Never raises except UngroundedSummaryError for the one case
    above. Language check runs before the excerpt path: a non-English
    title/description is never summarized as if translated — original
    text is preserved and the caller labels it translation-unavailable
    instead."""
    if not has_valid_source_url:
        raise UngroundedSummaryError("Cannot generate a summary without a validated source URL.")

    if _contains_non_latin_script(title) or (description and _contains_non_latin_script(description)):
        return SummaryResult(eeva_summary=None, is_fallback=False, translation_unavailable=True, original_title=title)

    if not description or not description.strip():
        return SummaryResult(eeva_summary=FALLBACK_SENTENCE, is_fallback=True, translation_unavailable=False, original_title=None)

    excerpt = _extractive_excerpt(description)
    if not excerpt:
        return SummaryResult(eeva_summary=FALLBACK_SENTENCE, is_fallback=True, translation_unavailable=False, original_title=None)

    return SummaryResult(eeva_summary=excerpt, is_fallback=False, translation_unavailable=False, original_title=None)
