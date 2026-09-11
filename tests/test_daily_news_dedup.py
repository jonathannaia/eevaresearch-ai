"""dedup.is_duplicate_title / normalize_title — pure functions, no I/O."""
from __future__ import annotations

from src.data_access.daily_news.dedup import is_duplicate_title, normalize_title


def test_normalize_title_strips_case_and_punctuation():
    assert normalize_title("AMD Reports Q2 2026 Results!") == normalize_title("amd reports q2 2026 results")


def test_matching_title_same_company_is_a_duplicate():
    existing = [("NVIDIA", "NVIDIA Announces Financial Results for Q2 2027")]
    assert is_duplicate_title(existing, "NVIDIA", "NVIDIA Announces Financial Results for Q2 2027!")


def test_matching_title_different_company_is_not_a_duplicate():
    existing = [("Intel Corp.", "Company Announces New Product")]
    assert not is_duplicate_title(existing, "NVIDIA", "Company Announces New Product")


def test_different_title_same_company_is_not_a_duplicate():
    existing = [("NVIDIA", "NVIDIA Announces Financial Results")]
    assert not is_duplicate_title(existing, "NVIDIA", "NVIDIA Ships New GPU Architecture")


def test_empty_title_is_never_treated_as_a_duplicate():
    existing = [("NVIDIA", "")]
    assert not is_duplicate_title(existing, "NVIDIA", "")


# --- Unicode-safe normalization (SK Hynix production fix) ---


def test_existing_english_case_and_punctuation_normalization_is_unchanged():
    """Same assertion as test_normalize_title_strips_case_and_punctuation
    above, kept as an explicit, separately-named regression proof that the
    Unicode-aware regex change did not alter English normalization."""
    assert normalize_title("SK hynix Reports Q3 2026 Results!") == normalize_title("sk hynix reports q3 2026 results")
    existing = [("SK Hynix", "SK hynix Reports Q3 2026 Results!")]
    assert is_duplicate_title(existing, "SK Hynix", "sk hynix reports q3 2026 results")


def test_two_distinct_hangul_titles_same_company_are_not_duplicates():
    existing = [("SK Hynix", "SK하이닉스 3분기 실적 발표")]
    assert not is_duplicate_title(existing, "SK Hynix", "SK하이닉스 신제품 출시")


def test_identical_hangul_title_same_company_is_a_duplicate():
    existing = [("SK Hynix", "SK하이닉스 3분기 실적 발표")]
    assert is_duplicate_title(existing, "SK Hynix", "SK하이닉스 3분기 실적 발표")


def test_mixed_latin_and_hangul_titles_differing_only_in_hangul_are_not_duplicates():
    """Before the Unicode-aware regex, both titles below normalized to the
    identical Latin/digit-only fingerprint ("sk hynix 2026") once their
    differing Hangul content was discarded — the exact collision behind
    the production SK Hynix items_deduplicated=10 evidence."""
    existing = [("SK Hynix", "SK hynix 실적발표 2026")]
    assert not is_duplicate_title(existing, "SK Hynix", "SK hynix 신제품출시 2026")
