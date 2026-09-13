"""Government / Public Sector Daily News lane (design/DECISIONS.md) —
focused, source-neutral tests that don't fit cleanly in
test_editorial_pipeline.py's own end-to-end run_editorial_discovery()
suite: the NIST allow-list's own pure matching behavior in isolation,
and a proof that EditorialStory's persisted shape is genuinely
unchanged (no new field, no migration) by this batch."""
from __future__ import annotations

import dataclasses

from src.data_access.daily_news.editorial_pipeline import _NIST_ALLOW_LIST_TERMS, _matches_nist_allow_list
from src.models.daily_news_models import EditorialStory

# ============================================================
# NIST allow-list — pure unit tests
# ============================================================


def test_every_approved_term_matches_in_title():
    for term in _NIST_ALLOW_LIST_TERMS:
        assert _matches_nist_allow_list(f"NIST announces {term} initiative", None), term


def test_every_approved_term_matches_in_summary_when_absent_from_title():
    for term in _NIST_ALLOW_LIST_TERMS:
        assert _matches_nist_allow_list("NIST News", f"Details on the {term} program."), term


def test_matching_is_case_insensitive():
    assert _matches_nist_allow_list("nist backs new chips act funding round", None)
    assert _matches_nist_allow_list("NIST BACKS NEW CHIPS ACT FUNDING ROUND", None)


def test_word_boundary_never_matches_inside_an_unrelated_word():
    # "semiconductor" must not match as a substring of an unrelated word.
    assert not _matches_nist_allow_list("NIST studies extrasemiconductorlike materials", None)


def test_bare_chip_is_not_an_allow_list_term():
    assert not _matches_nist_allow_list("NIST unveils new potato chip moisture sensor", None)


def test_bare_chips_alone_is_not_an_allow_list_term():
    assert not _matches_nist_allow_list("NIST CHIPS program office holiday schedule", None)


def test_advanced_manufacturing_alone_is_not_an_allow_list_term():
    # Explicitly excluded per approved scope — too broad on its own.
    assert not _matches_nist_allow_list("NIST advances advanced manufacturing metrology standards", None)


def test_empty_title_and_none_summary_does_not_match():
    assert not _matches_nist_allow_list("", None)


def test_whitespace_only_title_and_none_summary_does_not_match():
    assert not _matches_nist_allow_list("   ", None)


def test_off_topic_real_sample_item_does_not_match():
    assert not _matches_nist_allow_list(
        "NIST-Developed Quantum Sensors Improve Nuclear Monitoring",
        "New X-ray measurements will allow scientists to more accurately monitor nuclear material "
        "at power plants and weapons facilities.",
    )


def test_on_topic_real_sample_style_item_matches():
    assert _matches_nist_allow_list(
        "NIST Awards Funding to Advance Domestic Semiconductor Manufacturing",
        "The award, made under the CHIPS Act, supports new semiconductor fabrication capacity.",
    )


# ============================================================
# EditorialStory shape — proves no new field / no migration
# ============================================================


def test_editorial_story_has_no_new_persisted_field():
    # Government / Public Sector Daily News lane (design/DECISIONS.md)
    # deliberately adds zero fields to this dataclass — the badge label
    # is derived at render time from the already-existing
    # source_feed_id (see src/ui/components/editorial_coverage.py).
    # This is the exact, unchanged field set from before this batch.
    field_names = {f.name for f in dataclasses.fields(EditorialStory)}
    assert field_names == {
        "id", "headline", "publisher", "source_url", "published_at", "retrieved_at",
        "excerpt", "matched_companies", "matched_themes", "source_feed_id",
    }
