"""summary_grounding.generate_summary — pure function, no I/O, no
network, no LLM call. Covers the extractive-excerpt path, the fallback
sentence, the original-language-preserved path, and the structural
UngroundedSummaryError guard."""
from __future__ import annotations

import pytest

from src.data_access.daily_news.summary_grounding import (
    FALLBACK_SENTENCE,
    MAX_SUMMARY_CHARS,
    UngroundedSummaryError,
    generate_summary,
)


def test_grounded_excerpt_produced_from_short_description():
    result = generate_summary(
        "NVIDIA Announces Upcoming Event", "NVIDIA will present at an event for the financial community.",
        has_valid_source_url=True,
    )
    assert not result.is_fallback
    assert not result.translation_unavailable
    assert "financial community" in result.eeva_summary


def test_html_is_stripped_from_the_excerpt():
    result = generate_summary(
        "Title", "<p>Revenue <b>rose</b> significantly this quarter.</p>", has_valid_source_url=True,
    )
    assert "<" not in result.eeva_summary and ">" not in result.eeva_summary
    assert "Revenue rose significantly this quarter." in result.eeva_summary


def test_long_description_is_trimmed_to_a_sentence_boundary_within_bound():
    long_description = " ".join(f"Sentence number {i} adds more detail." for i in range(1, 30))
    result = generate_summary("Title", long_description, has_valid_source_url=True)
    assert len(result.eeva_summary) <= MAX_SUMMARY_CHARS
    assert result.eeva_summary.endswith(".")  # cut at a sentence boundary, not mid-word


def test_missing_description_uses_the_fixed_fallback_sentence():
    result = generate_summary("Title only, no description", None, has_valid_source_url=True)
    assert result.is_fallback
    assert result.eeva_summary == FALLBACK_SENTENCE


def test_empty_string_description_uses_the_fallback_sentence():
    result = generate_summary("Title only", "   ", has_valid_source_url=True)
    assert result.is_fallback
    assert result.eeva_summary == FALLBACK_SENTENCE


def test_korean_title_preserves_original_and_marks_translation_unavailable():
    result = generate_summary("삼성전자 신규시설투자 결정", "설명입니다.", has_valid_source_url=True)
    assert result.translation_unavailable
    assert result.eeva_summary is None
    assert result.original_title == "삼성전자 신규시설투자 결정"


def test_japanese_description_also_triggers_translation_unavailable():
    result = generate_summary("English title", "これは日本語の説明です。", has_valid_source_url=True)
    assert result.translation_unavailable
    assert result.eeva_summary is None


def test_ungrounded_summary_error_when_no_valid_source_url():
    with pytest.raises(UngroundedSummaryError):
        generate_summary("Title", "Some description", has_valid_source_url=False)


def test_no_investment_language_is_ever_added_only_source_text_is_used():
    # Structural proof, not a keyword blocklist: the function only ever
    # selects/trims substrings of the input — it cannot introduce a
    # phrase (like a price target) absent from the source.
    description = "The company reported quarterly results in line with expectations."
    result = generate_summary("Title", description, has_valid_source_url=True)
    assert result.eeva_summary in description or result.eeva_summary == description
