"""Signals materiality classification (design/DECISIONS.md) — pure
function tests, no I/O, no AppTest. The three user-supplied examples
from the Phase 1 audit (electric-truck-shaped, solo-developer-CUDA-on-
AMD-shaped, generic-NVIDIA-PR-shaped) are the anchor regression
fixtures — see materiality_classification.py's own module docstring for
the full gate design these tests exercise."""
from __future__ import annotations

from src.data_access.daily_news.materiality_classification import (
    classify_editorial_story,
    classify_issuer_story,
)
from src.data_access.daily_news.source_registry import SourceCategory
from src.models.daily_news_models import NewsMaterialityTier, SourceClass


# --- The three approved example fixtures, plus their "what would flip it" counterpart ---


def test_google_linked_small_electric_truck_deployment_classifies_as_background():
    """Off-taxonomy: a company (Google) is matched, but the subject
    matter (an electric truck pilot) is not in the relevance taxonomy at
    all, and no materiality anchor is present — Background, not
    Watchlist, since there's nothing here to actively watch within the
    AI-infrastructure/semiconductor scope."""
    tier, reasons = classify_editorial_story(
        "Google-linked startup begins limited electric truck pilot in California",
        "A small logistics startup backed partly by Google is testing a handful of electric delivery trucks.",
        SourceCategory.INDEPENDENT_NEWS,
    )
    assert tier == NewsMaterialityTier.BACKGROUND
    assert reasons == ("off_taxonomy_no_anchor",)


def test_solo_developer_cuda_on_amd_without_official_validation_classifies_as_watchlist():
    """On-taxonomy (GPU/CUDA compute) but no primary disclosure, no
    quantified change, and no independent-news fact-attribution language
    — technically interesting, not material without official or
    production-scale validation, exactly as specified."""
    tier, reasons = classify_editorial_story(
        "Solo developer gets NVIDIA's CUDA libraries running on AMD GPUs",
        "A hobbyist programmer has published an open-source project porting CUDA workloads to AMD hardware.",
        SourceCategory.INDEPENDENT_NEWS,
    )
    assert tier == NewsMaterialityTier.WATCHLIST
    assert reasons == ("on_taxonomy_no_anchor:ai_compute_and_data_center",)


def test_generic_nvidia_product_pr_without_quantified_impact_classifies_as_watchlist():
    """On-taxonomy (AI accelerator) but no measurable demand/capacity/
    partnership/pricing/deployment-scale figure anywhere — Watchlist,
    not High Signal, exactly as specified: "may be a Watchlist item...
    not High Signal unless it contains a measurable... implication.\""""
    tier, reasons = classify_issuer_story(
        "NVIDIA Announces New AI Accelerator for Edge Computing",
        "NVIDIA today unveiled its newest AI accelerator platform designed for edge computing applications.",
        SourceClass.OFFICIAL_COMPANY,
    )
    assert tier == NewsMaterialityTier.WATCHLIST
    assert reasons == ("on_taxonomy_no_anchor:ai_compute_and_data_center",)


def test_equivalent_product_story_with_quantified_capacity_scale_classifies_as_high_signal():
    """Same shape as the Watchlist fixture above, but now with a real
    capacity/capex figure — proves the gate actually fires, not just
    that it misses the unquantified case."""
    tier, reasons = classify_issuer_story(
        "NVIDIA Announces $2 Billion Capacity Expansion for AI Accelerator Production",
        "NVIDIA today announced a $2 billion capacity expansion to scale AI accelerator production.",
        SourceClass.OFFICIAL_COMPANY,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL
    assert any(r.startswith("quantified_change:") for r in reasons)
    assert any(r.startswith("taxonomy_anchored_consequence:") for r in reasons)


def test_formal_earnings_release_classifies_as_high_signal_via_primary_disclosure_gate():
    tier, reasons = classify_issuer_story(
        "NVIDIA Reports Fourth Quarter Financial Results",
        "NVIDIA today reported financial results for its fourth quarter.",
        SourceClass.OFFICIAL_COMPANY,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL
    assert any(r.startswith("formal_earnings_materials:") for r in reasons)


def test_regulatory_filing_classifies_as_high_signal_via_primary_disclosure_gate():
    tier, reasons = classify_editorial_story(
        "SEC charges company with disclosure violations",
        "The SEC announced charges related to disclosure violations.",
        SourceCategory.OFFICIAL_FILING,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL
    assert reasons == ("primary_disclosure",)


def test_issuer_lane_regulatory_filing_source_class_also_reaches_high_signal():
    """SourceClass.REGULATORY_FILING is never produced by the live
    issuer pipeline today (see SourceClass's own docstring), but the
    classifier still honors it correctly — forward compatibility, not
    dead code."""
    tier, reasons = classify_issuer_story(
        "Company files 8-K disclosing material agreement",
        None,
        SourceClass.REGULATORY_FILING,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL
    assert reasons == ("primary_disclosure",)


# --- The structural invariant: zero gate hits can never reach High Signal ---


def test_zero_gate_item_can_never_reach_high_signal():
    fixtures = [
        ("Company holds annual holiday party for employees", "Employees gathered for the annual holiday celebration."),
        ("CEO featured in local business magazine profile", "A short human-interest profile with no financial detail."),
        ("Company sponsors youth soccer league", "A community sponsorship announcement."),
    ]
    for headline, excerpt in fixtures:
        tier, reasons = classify_issuer_story(headline, excerpt, SourceClass.OFFICIAL_COMPANY)
        assert tier != NewsMaterialityTier.HIGH_SIGNAL, (headline, tier, reasons)
        tier, reasons = classify_editorial_story(headline, excerpt, SourceCategory.INDEPENDENT_NEWS)
        assert tier != NewsMaterialityTier.HIGH_SIGNAL, (headline, tier, reasons)


def test_high_signal_always_has_at_least_one_gate_reason():
    """The converse of the invariant above, checked directly against the
    reasons tuple rather than inferred: every HIGH_SIGNAL result must
    carry at least one non-empty gate reason — there is no code path
    that returns HIGH_SIGNAL with zero reasons."""
    tier, reasons = classify_issuer_story(
        "NVIDIA Reports Fourth Quarter Financial Results", None, SourceClass.OFFICIAL_COMPANY,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL
    assert len(reasons) >= 1


# --- Gate A3: government/regulator source category handling ---


def test_regulator_exchange_source_category_qualifies_by_category_alone():
    """REGULATOR/EXCHANGE sources' own admission policy already restricts
    them to real regulator/exchange press releases — no additional
    keyword anchor required, unlike GOVERNMENT_POLICY below."""
    tier, reasons = classify_editorial_story(
        "Market notice: trading halt procedures updated",
        "Routine administrative notice.",
        SourceCategory.EXCHANGE,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL
    assert reasons == ("primary_disclosure",)


def test_government_policy_source_without_a_formal_action_anchor_does_not_reach_high_signal_via_gate_a():
    """GOVERNMENT_POLICY sources (e.g. NIST) publish a wide range of
    general content — unlike REGULATOR/EXCHANGE, category membership
    alone is not sufficient; a formal-action keyword anchor is required,
    mirroring editorial_pipeline.py's own NIST-specific allow-list
    discipline."""
    tier, reasons = classify_editorial_story(
        "Agency publishes annual employee recognition newsletter",
        "A routine internal newsletter.",
        SourceCategory.GOVERNMENT_POLICY,
    )
    assert tier != NewsMaterialityTier.HIGH_SIGNAL
    assert not any(r == "primary_disclosure" for r in reasons)


def test_government_policy_source_with_a_formal_action_anchor_reaches_high_signal():
    tier, reasons = classify_editorial_story(
        "Agency issues final rule on export control entity list",
        "The agency published a final rule affecting the entity list.",
        SourceCategory.GOVERNMENT_POLICY,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL
    assert reasons == ("primary_disclosure",)


# --- Gate D: credible editorial reporting ---


def test_independent_news_with_attribution_language_and_substantive_excerpt_reaches_high_signal():
    long_excerpt = (
        "According to a regulatory filing reviewed by this publication, the company disclosed a material "
        "change to its manufacturing capacity plans following an internal review that began earlier this year "
        "and involved consultations with several major customers across its supply chain."
    )
    tier, reasons = classify_editorial_story(
        "Company quietly disclosed capacity change, filing shows",
        long_excerpt,
        SourceCategory.INDEPENDENT_NEWS,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL
    assert any(r.startswith("credible_editorial_reporting:") for r in reasons)


def test_independent_news_with_attribution_language_but_a_short_blurb_does_not_reach_high_signal_via_gate_d():
    tier, reasons = classify_editorial_story(
        "Company disclosed capacity change, filing shows",
        "A short blurb.",
        SourceCategory.INDEPENDENT_NEWS,
    )
    assert not any(r.startswith("credible_editorial_reporting:") for r in reasons)
