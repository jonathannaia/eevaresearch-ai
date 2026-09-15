"""Pure-function tests for src.data_access.daily_news.localization_dedup
— the structural gates plus the translated-token-containment check.
No I/O, no translation provider (candidate_translated_title is always
supplied directly by the caller in these tests)."""
from __future__ import annotations

from src.data_access.daily_news import localization_dedup
from src.models.daily_news_models import SourceClass


def _base_kwargs(**overrides) -> dict:
    kwargs = dict(
        candidate_company="Meta Platforms, Inc.", candidate_source_class=SourceClass.OFFICIAL_COMPANY,
        candidate_language="French", candidate_published_at_epoch=1_000_000.0,
        candidate_translated_title="Meta Launches Meta One",
        existing_company="Meta Platforms, Inc.", existing_source_class=SourceClass.OFFICIAL_COMPANY,
        existing_language="English", existing_published_at_epoch=1_000_000.0 + 3600,
        existing_title="Meta Launches Meta One",
    )
    kwargs.update(overrides)
    return kwargs


def test_matches_when_every_gate_and_the_similarity_threshold_hold():
    assert localization_dedup.is_localized_duplicate(**_base_kwargs()) is True


def test_no_match_when_companies_differ():
    assert localization_dedup.is_localized_duplicate(**_base_kwargs(existing_company="Oracle Corporation")) is False


def test_no_match_when_candidate_source_class_is_not_official_company():
    """First-party only — editorial/independent-journalism content can
    never trigger this check, structurally, regardless of similarity."""
    assert localization_dedup.is_localized_duplicate(
        **_base_kwargs(candidate_source_class=SourceClass.INDEPENDENT_JOURNALISM)
    ) is False


def test_no_match_when_existing_source_class_is_not_official_company():
    assert localization_dedup.is_localized_duplicate(
        **_base_kwargs(existing_source_class=SourceClass.PRESS_RELEASE_WIRE)
    ) is False


def test_no_match_when_languages_are_the_same():
    """Two of one company's own same-language feeds are the existing
    dedup.is_duplicate_title's job, not this module's — same-language
    inputs must never match here."""
    assert localization_dedup.is_localized_duplicate(**_base_kwargs(existing_language="French")) is False


def test_no_match_when_published_outside_the_window():
    far_apart = _base_kwargs(existing_published_at_epoch=1_000_000.0 + localization_dedup.MAX_PUBLISH_GAP_SECONDS + 1)
    assert localization_dedup.is_localized_duplicate(**far_apart) is False


def test_match_at_exactly_the_window_boundary():
    at_boundary = _base_kwargs(existing_published_at_epoch=1_000_000.0 + localization_dedup.MAX_PUBLISH_GAP_SECONDS)
    assert localization_dedup.is_localized_duplicate(**at_boundary) is True


def test_no_match_when_translated_titles_share_too_few_tokens():
    """Two genuinely distinct releases must never collapse just because
    they share the company name and product line — "Meta launches a
    messaging feature" vs. "Meta announces a partnership with Orange"
    share almost nothing once translated."""
    distinct = _base_kwargs(
        candidate_translated_title="Meta Announces Partnership With Orange",
        existing_title="Meta Launches New Messaging Feature",
    )
    assert localization_dedup.is_localized_duplicate(**distinct) is False


def test_no_match_when_containment_ratio_is_below_threshold_despite_some_overlap():
    """Only "meta" is shared — one token is never enough, regardless of
    ratio math on very short titles."""
    weak_overlap = _base_kwargs(
        candidate_translated_title="Meta Expands European Data Center Footprint",
        existing_title="Meta Announces New Hardware Lineup",
    )
    assert localization_dedup.is_localized_duplicate(**weak_overlap) is False


def test_match_is_symmetric_regardless_of_which_side_is_the_non_english_candidate():
    """The reverse arrival order (English discovered after an already-
    persisted non-English original) must be detected identically — the
    gates and similarity check read only company/source_class/language/
    time/title, never which side is the "new" one."""
    reverse_order = _base_kwargs(candidate_language="English", existing_language="French")
    assert localization_dedup.is_localized_duplicate(**reverse_order) is True
