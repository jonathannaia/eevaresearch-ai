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
