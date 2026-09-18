"""Signals materiality classification (design/DECISIONS.md) — pure
function tests, no I/O, no AppTest. The three user-supplied examples
from the Phase 1 audit (electric-truck-shaped, solo-developer-CUDA-on-
AMD-shaped, generic-NVIDIA-PR-shaped) are the anchor regression
fixtures — see materiality_classification.py's own module docstring for
the full gate design these tests exercise.

The sections below "Zero gate hits..." were added by the calibration
pass (design/DECISIONS.md, "Calibrate Signals materiality rules against
real issuer stories") — every fixture there is a real headline (or a
close paraphrase of one) found during that pass's 154-record read-only
sample, not a synthetic example."""
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


# ============================================================
# Calibration pass (design/DECISIONS.md) — real-headline fixtures
# ============================================================


# --- Deployment vocabulary + plural-safe taxonomy matching ---


def test_amd_anthropic_gigawatt_partnership_is_high_signal():
    """The headline finding that drove this whole calibration pass: two
    compounding gaps — "Deploy" (verb) didn't match a "deployment"-only
    anchor list, and "GPU" didn't match its own plural "GPUs" — meant
    this, arguably the single most material item in the calibration
    sample, landed in Background before this fix."""
    tier, reasons = classify_editorial_story(
        "AMD and Anthropic Announce Strategic Partnership to Deploy Up to 2 Gigawatts of AMD Instinct MI450 Series GPUs",
        None,
        SourceCategory.INDEPENDENT_NEWS,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL
    assert any(r.startswith("quantified_change:deploy") for r in reasons)


def test_every_deploy_inflection_is_recognized_as_a_materiality_anchor():
    for form in ("deploy", "deploys", "deployed", "deploying", "deployment", "deployments"):
        tier, reasons = classify_issuer_story(
            f"Company Announces Plan to {form.capitalize()} 500 Megawatts of New Capacity",
            None,
            SourceClass.OFFICIAL_COMPANY,
        )
        assert tier == NewsMaterialityTier.HIGH_SIGNAL, (form, tier, reasons)


def test_gpu_taxonomy_term_matches_its_ordinary_plural_gpus():
    tier, reasons = classify_editorial_story(
        "Report: Data Center Operators Are Racing to Secure More GPUs",
        "Demand for GPUs continues to outstrip supply across the industry, analysts say.",
        SourceCategory.INDEPENDENT_NEWS,
    )
    assert tier == NewsMaterialityTier.WATCHLIST  # on-taxonomy (GPUs), no anchor/number here
    assert reasons == ("on_taxonomy_no_anchor:ai_compute_and_data_center",)


def test_plural_matching_never_creates_a_substring_match_inside_an_unrelated_word():
    """The safety requirement, verified directly: "GPU" + optional "s"
    must never match inside a longer, unrelated word."""
    tier, reasons = classify_editorial_story(
        "Local Band GPUniverse Announces New Album Release Tour",
        None,
        SourceCategory.INDEPENDENT_NEWS,
    )
    assert tier == NewsMaterialityTier.BACKGROUND
    assert reasons == ("off_taxonomy_no_anchor",)


# --- Capital allocation ---


def test_new_quantified_one_billion_repurchase_is_high_signal():
    tier, reasons = classify_issuer_story(
        "Rockwell Automation Approves $1 Billion for Common Stock Repurchase and Declares Common Stock Dividend",
        None,
        SourceClass.OFFICIAL_COMPANY,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL
    assert any(r.startswith("quantified_capital_return:") for r in reasons)


def test_routine_unchanged_quarterly_dividend_is_watchlist_not_high_signal():
    tier, reasons = classify_issuer_story(
        "Quanta Services Announces Quarterly Cash Dividend",
        None,
        SourceClass.OFFICIAL_COMPANY,
    )
    assert tier == NewsMaterialityTier.WATCHLIST
    assert reasons == ("capital_return_mention_no_qualifying_action:dividend",)


def test_dividend_suspension_is_high_signal():
    tier, reasons = classify_issuer_story(
        "Company Suspends Quarterly Dividend Amid Restructuring",
        None,
        SourceClass.OFFICIAL_COMPANY,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL
    assert any(r.startswith("material_dividend_action:") for r in reasons)


def test_dividend_material_cut_is_high_signal():
    tier, reasons = classify_issuer_story(
        "Company Cuts Quarterly Dividend by 50% Amid Weak Demand",
        None,
        SourceClass.OFFICIAL_COMPANY,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL
    assert any(r.startswith("material_dividend_action:") for r in reasons)


def test_generic_buyback_mention_never_reaches_high_signal_without_a_qualifying_action():
    tier, reasons = classify_editorial_story(
        "Analyst Notes Mention Buyback in Passing Commentary on Sector Trends",
        None,
        SourceCategory.INDEPENDENT_NEWS,
    )
    assert tier != NewsMaterialityTier.HIGH_SIGNAL
    assert reasons == ("capital_return_mention_no_qualifying_action:buyback",)


def test_capital_return_vocabulary_without_magnitude_never_bypasses_the_zero_gate_invariant():
    """A bare "share repurchase" mention with no number and no taxonomy
    match must not combine with anything to reach High Signal — the
    quantified-capital-return anchor list is deliberately kept separate
    from the general taxonomy-pairing anchor list for exactly this
    reason (see module docstring)."""
    tier, reasons = classify_issuer_story(
        "Company Discusses Share Repurchase Philosophy in Investor Letter",
        None,
        SourceClass.OFFICIAL_COMPANY,
    )
    assert tier != NewsMaterialityTier.HIGH_SIGNAL


# --- Earnings: actual results vs. scheduling notices ---


def test_amd_to_report_scheduling_notice_is_watchlist():
    tier, reasons = classify_issuer_story(
        "AMD to Report Fiscal Second Quarter 2026 Financial Results",
        None,
        SourceClass.OFFICIAL_COMPANY,
    )
    assert tier == NewsMaterialityTier.WATCHLIST
    assert reasons == ("scheduling_notice_not_yet_substantive:financial results",)


def test_cisco_schedules_conference_call_notice_is_watchlist():
    tier, reasons = classify_issuer_story(
        "Cisco Schedules Conference Call for Q3 Fiscal Year 2026 Financial Results",
        None,
        SourceClass.OFFICIAL_COMPANY,
    )
    assert tier == NewsMaterialityTier.WATCHLIST
    assert reasons == ("scheduling_notice_not_yet_substantive:financial results",)


def test_amd_reports_actual_results_is_high_signal():
    tier, reasons = classify_issuer_story(
        "AMD Reports Second Quarter 2026 Financial Results",
        None,
        SourceClass.OFFICIAL_COMPANY,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL
    assert any(r.startswith("formal_earnings_materials:") for r in reasons)


def test_scheduling_notice_with_date_and_call_time_never_reaches_high_signal_from_those_numbers_alone():
    """A date, quarter number, and clock time are not a quantified
    business change — Gate B's own numeric patterns require a currency/
    percentage/named unit, so none of "Q2 2026", "August 4, 2026", or
    "5:00 p.m." can satisfy it by construction; this is the direct
    end-to-end proof."""
    tier, reasons = classify_issuer_story(
        "Arista Networks to Announce Q2 2026 Financial Results on Tuesday, August 4, 2026 at 5:00 p.m. Eastern Time",
        None,
        SourceClass.OFFICIAL_COMPANY,
    )
    assert tier != NewsMaterialityTier.HIGH_SIGNAL
    assert tier == NewsMaterialityTier.WATCHLIST
    assert reasons == ("scheduling_notice_not_yet_substantive:financial results",)


def test_actual_results_announcement_with_non_contiguous_quarter_reference_still_qualifies():
    """Real IR headlines routinely wedge the quarter/date between the
    verb and "Financial Results" ("Announces Second Quarter 2026
    Financial Results") — this must still qualify as an actual
    disclosure, not be penalized for the word order."""
    tier, reasons = classify_issuer_story(
        "MaxLinear, Inc. Announces Second Quarter 2026 Financial Results",
        None,
        SourceClass.OFFICIAL_COMPANY,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL


# --- Survey / third-party research statistics ---


def test_intel_robotics_survey_story_is_not_high_signal():
    tier, reasons = classify_issuer_story(
        "Six in 10 Leaders Bet Big on Robots. Only Four in 10 Are Ready.",
        "Six in 10 senior business and IT leaders, robotics specialists, government and healthcare officials "
        "expect their organizations to operate robot fleets within five years. Leaders predict full-scale "
        "robotics deployment could double operational output. 67% believe they will be ready to manage a "
        "mixed human-robot workforce by 2030. 74% believe workforce planning and robotics investment must "
        "accelerate.",
        SourceClass.OFFICIAL_COMPANY,
    )
    assert tier != NewsMaterialityTier.HIGH_SIGNAL


def test_cisco_splunk_600_billion_research_story_is_not_high_signal():
    tier, reasons = classify_issuer_story(
        "The $600 Billion Wake-up Call: New Splunk Research Reveals Downtime is a Systemic Business Crisis",
        "Downtime has become a systemic business crisis that threatens revenue, brand equity and shareholder value.",
        SourceClass.OFFICIAL_COMPANY,
    )
    assert tier != NewsMaterialityTier.HIGH_SIGNAL


def test_nvidia_real_500_billion_financing_platform_remains_high_signal():
    """The survey-content suppression must never catch a genuine issuer
    disclosure that happens to contain a large number — this excerpt
    names six real financial partners and a concrete financing-platform
    commitment, with zero survey/research language present."""
    tier, reasons = classify_issuer_story(
        "NVIDIA AI Factory Compute Is Becoming an Investable Asset Class",
        "We announced partnerships with Apollo, BlackRock, Blackstone, Brookfield, Goldman Sachs and KKR to "
        "establish independent financing platforms designed to mobilize over $500 billion of third-party "
        "capital to support the buildout of AI infrastructure over time.",
        SourceClass.OFFICIAL_COMPANY,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL
    assert any(r.startswith("quantified_change:") or r.startswith("taxonomy_anchored_consequence:") for r in reasons)


def test_survey_language_does_not_suppress_an_independent_genuine_disclosure_in_the_same_story():
    """The suppression is narrow, not a blanket "contains the word
    survey" exclusion: a story with BOTH survey language AND a real,
    independent primary-disclosure/earnings/dividend-action/quantified-
    capital-return signal must still reach High Signal via that
    narrower gate (Gates A/A2/A4/B2 are never suppressed)."""
    tier, reasons = classify_issuer_story(
        "Company Reports Second Quarter 2026 Financial Results; Survey Finds Customers Optimistic",
        None,
        SourceClass.OFFICIAL_COMPANY,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL
    assert any(r.startswith("formal_earnings_materials:") for r in reasons)


# --- Input robustness ---


def test_none_title_and_none_excerpt_return_safely_never_high_signal():
    tier, reasons = classify_issuer_story(None, None, SourceClass.OFFICIAL_COMPANY)
    assert tier == NewsMaterialityTier.BACKGROUND
    assert reasons == ("off_taxonomy_no_anchor",)
    tier, reasons = classify_editorial_story(None, None, SourceCategory.INDEPENDENT_NEWS)
    assert tier == NewsMaterialityTier.BACKGROUND


def test_empty_string_title_and_excerpt_return_safely_never_high_signal():
    tier, reasons = classify_issuer_story("", "", SourceClass.OFFICIAL_COMPANY)
    assert tier == NewsMaterialityTier.BACKGROUND


def test_whitespace_only_title_and_excerpt_return_safely_never_high_signal():
    tier, reasons = classify_issuer_story("   ", "\n\t  ", SourceClass.OFFICIAL_COMPANY)
    assert tier == NewsMaterialityTier.BACKGROUND


def test_none_title_with_a_real_excerpt_does_not_crash():
    tier, reasons = classify_issuer_story(None, "NVIDIA reported financial results.", SourceClass.OFFICIAL_COMPANY)
    assert tier in (NewsMaterialityTier.HIGH_SIGNAL, NewsMaterialityTier.WATCHLIST, NewsMaterialityTier.BACKGROUND)


def test_real_title_with_none_excerpt_does_not_crash():
    tier, reasons = classify_issuer_story("NVIDIA Reports Second Quarter 2026 Financial Results", None, SourceClass.OFFICIAL_COMPANY)
    assert tier == NewsMaterialityTier.HIGH_SIGNAL


# ============================================================
# "Close remaining Signals calibration edge cases" — second calibration pass
# ============================================================


# --- Deployment qualification: bare deploy-language must not independently
# satisfy Gate C's taxonomy pairing; a real quantity still qualifies via
# Gate B regardless. ---


def test_amd_anthropic_gigawatt_deployment_remains_high_signal_via_quantified_gate():
    tier, reasons = classify_editorial_story(
        "AMD and Anthropic Announce Strategic Partnership to Deploy Up to 2 Gigawatts of AMD Instinct MI450 Series GPUs",
        None,
        SourceCategory.INDEPENDENT_NEWS,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL
    assert reasons == ("quantified_change:deploy",)


def test_bloom_energy_oracle_gigawatt_deployment_remains_high_signal_via_quantified_gate():
    tier, reasons = classify_issuer_story(
        "Bloom Energy and Oracle Expand Strategic Partnership to Deploy up to 2.8 GW to Accelerate AI Infrastructure Build-Out",
        None,
        SourceClass.OFFICIAL_COMPANY,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL
    assert reasons == ("quantified_change:deploy",)


def test_cisco_generic_ease_of_deployment_copy_is_watchlist_not_high_signal():
    """The negative fixture: real product-positioning copy — "Makes AI
    Easier to Deploy and Secure" / "gives customers a framework for
    deploying AI infrastructure" — pairs an incidental taxonomy match
    ("data center") with a deploy-family anchor and nothing else. No
    number anywhere in the text, so Gate B never fires either."""
    tier, reasons = classify_issuer_story(
        "Cisco Secure AI Factory with NVIDIA Makes AI Easier to Deploy and Secure, Anywhere Organizations Need It",
        "Expanded Cisco Secure AI Factory with NVIDIA gives customers a framework for deploying AI infrastructure "
        "– from central data center to local sites.",
        SourceClass.OFFICIAL_COMPANY,
    )
    assert tier == NewsMaterialityTier.WATCHLIST
    assert reasons == ("on_taxonomy_no_anchor:ai_compute_and_data_center",)


def test_deploy_family_alone_never_satisfies_gate_c_without_a_number():
    """Generalized negative fixture, independent of the Cisco headline
    specifically: any on-taxonomy story whose only anchor is a deploy-
    family word, with zero numeric magnitude anywhere, must land in
    Watchlist, never High Signal."""
    tier, reasons = classify_issuer_story(
        "Company Says New AI Data Center Design Makes Deployment Simpler for Customers",
        None,
        SourceClass.OFFICIAL_COMPANY,
    )
    assert tier == NewsMaterialityTier.WATCHLIST
    assert reasons == ("on_taxonomy_no_anchor:ai_compute_and_data_center",)


def test_deploy_family_with_a_named_partner_and_a_real_number_still_qualifies_via_gate_b():
    """A named customer/partner commitment that is ALSO quantified still
    qualifies — this is exactly the AMD/Anthropic and Bloom/Oracle
    shape; this fixture isolates that the partner name itself is not
    what's doing the work, the number is."""
    tier, reasons = classify_issuer_story(
        "Acme Corp and Contoso Announce Partnership to Deploy 500 Megawatts of New Sites",
        None,
        SourceClass.OFFICIAL_COMPANY,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL
    assert reasons == ("quantified_change:deploy",)


# --- Scheduling: webcast-schedule notices ---


def test_quanta_earnings_release_and_webcast_schedule_notice_is_watchlist():
    tier, reasons = classify_issuer_story(
        "Quanta Services Announces Second Quarter 2026 Earnings Release & Webcast Schedule",
        None,
        SourceClass.OFFICIAL_COMPANY,
    )
    assert tier == NewsMaterialityTier.WATCHLIST
    assert reasons == ("scheduling_notice_not_yet_substantive:earnings release",)


def test_webcast_schedule_phrase_variants_are_all_recognized_as_scheduling():
    for phrase in ("webcast schedule", "earnings release and webcast schedule", "earnings release & webcast schedule"):
        tier, reasons = classify_issuer_story(
            f"Company Announces First Quarter 2026 Financial Results {phrase.title()}",
            None,
            SourceClass.OFFICIAL_COMPANY,
        )
        assert tier == NewsMaterialityTier.WATCHLIST, (phrase, tier, reasons)
        assert reasons == ("scheduling_notice_not_yet_substantive:financial results",), (phrase, reasons)


def test_actual_earnings_release_with_reported_figures_remains_high_signal_after_webcast_fix():
    """Confirms the webcast-schedule addition doesn't collaterally
    disqualify a real earnings release that happens to also be
    livestreamed — the unambiguous "reports {Nth} quarter" override
    still applies."""
    tier, reasons = classify_issuer_story(
        "Acme Corp Reports Second Quarter 2026 Results; Webcast Replay Available",
        None,
        SourceClass.OFFICIAL_COMPANY,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL
    assert any(r.startswith("formal_earnings_materials:") for r in reasons)


# ============================================================
# Signals editorial lane false-positive audit (design/DECISIONS.md) —
# consumer deal/discount pricing must never reach HIGH_SIGNAL merely
# because a retail dollar/percent figure co-occurs with the same
# "pricing"/"price cut"/"price increase" keywords a genuine corporate
# pricing action uses.
# ============================================================


def test_gaming_pc_deal_with_a_dollar_discount_and_price_cut_language_is_never_high_signal():
    """The verified false positive: a $560-off gaming PC deal reached
    HIGH_SIGNAL via quantified_change:price cut, purely from retail
    marketing copy — zero corporate materiality anywhere in the text."""
    tier, reasons = classify_editorial_story(
        "Save 25% ($560) on This Gaming PC Packed With AMD and Nvidia Hardware",
        "This gaming PC deal pairs an AMD Ryzen 7 processor with an Nvidia GeForce RTX 4070 graphics "
        "card, marking one of the biggest price cut deals we've seen on this configuration this year.",
        SourceCategory.INDEPENDENT_NEWS,
    )
    assert tier != NewsMaterialityTier.HIGH_SIGNAL
    assert not any(r.startswith("quantified_change:price") or r.startswith("taxonomy_anchored_consequence:") for r in reasons)


def test_dollar_off_consumer_deal_phrasing_alone_is_never_high_signal():
    tier, reasons = classify_editorial_story(
        "This Laptop Deal Saves You $300 Right Now",
        "The discounted laptop configuration marks a real price cut compared to last month's listing.",
        SourceCategory.INDEPENDENT_NEWS,
    )
    assert tier != NewsMaterialityTier.HIGH_SIGNAL


def test_percent_off_consumer_deal_phrasing_alone_is_never_high_signal():
    tier, reasons = classify_editorial_story(
        "Grab This Monitor While It's 40% Off",
        "This is one of the best price cuts we've tracked on this monitor all year.",
        SourceCategory.INDEPENDENT_NEWS,
    )
    assert tier != NewsMaterialityTier.HIGH_SIGNAL


def test_genuine_corporate_price_increase_still_reaches_high_signal():
    """The calibration fix must never suppress a real corporate pricing
    action — only "save $X"/"$X off"/"X% off" retail-deal phrasing is
    excluded; ordinary corporate pricing-action language is untouched."""
    tier, reasons = classify_editorial_story(
        "Nvidia Raises GPU Prices by 15% Amid Tariff Pressures",
        "Nvidia confirmed a 15% price increase across its GPU lineup will take effect next quarter, "
        "citing new import tariffs on semiconductor components.",
        SourceCategory.INDEPENDENT_NEWS,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL
    assert any(r.startswith("quantified_change:price increase") for r in reasons)


def test_quantified_capacity_infrastructure_event_is_unaffected_by_the_consumer_deal_fix():
    """Positive control: the consumer-deal-price fix is scoped to
    exactly the "pricing"/"price increase"/"price cut" anchor keywords
    — a real quantified capacity/lease event must be completely
    unaffected, since "capacity" is never in the affected keyword set."""
    tier, reasons = classify_editorial_story(
        "Polarise to Lease 15MW of Data Center Capacity in Prague",
        "Polarise has signed an agreement to lease 15MW of data center capacity in Prague, expanding "
        "its footprint in Central Europe as demand for AI infrastructure grows across the region.",
        SourceCategory.INDEPENDENT_NEWS,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL
    assert any(r.startswith("quantified_change:capacity") for r in reasons)


# ============================================================
# Signals admission/materiality precision fix (design/SIGNALS_
# ADMISSION_MATERIALITY_CALIBRATION_2026_09_15.md, design/CURRENT_
# SIGNALS_POLICY_AND_GAP_INVENTORY_2026_09_15.md) — P0 negation-aware
# confirmation, P1 interview/podcast guard + first-party launch
# sentence-locality, P2 space taxonomy. Every fixture below is a
# realistic reconstruction of the exact named case (this environment
# has no live editorial-lane cache/Postgres access — see this file's
# own module docstring convention above), run through the real,
# unmodified classify_editorial_story()/classify_issuer_story().
# ============================================================

# --- P0: negation-aware confirmation (Fixture A — Intel rumor) ------


def test_unconfirmed_tipster_rumor_does_not_reach_high_signal():
    """The exact reported case: a Tom's Hardware-shaped rumor sourced to
    a named tipster, with the chipmaker's own non-confirmation — must
    not qualify for High Signal via Gate D."""
    tier, reasons = classify_editorial_story(
        "Intel reportedly cans 12Xe option for Nova Lake-S desktop — gaming APU design said to resurface "
        "with Razor Lake",
        "According to tipster Jaykihn, Intel has cancelled the 12 Xe3P graphics core option for its Nova "
        "Lake-S desktop lineup. The leaker says the design has been cancelled and Intel intends to pick it "
        "back up with Razor Lake, the generation that will follow Nova Lake. The chipmaker has not "
        "officially confirmed the change.",
        SourceCategory.INDEPENDENT_NEWS,
    )
    assert tier != NewsMaterialityTier.HIGH_SIGNAL
    assert not any(r.startswith("credible_editorial_reporting:") for r in reasons)


def test_genuine_official_confirmation_reaches_high_signal():
    """Positive control: a genuine, non-negated, non-rumor confirmation
    of a quantified event must remain fully eligible."""
    tier, reasons = classify_editorial_story(
        "Intel officially confirmed a new $2 billion investment in Arizona fab capacity",
        "Intel today officially confirmed it will invest $2 billion to expand fab capacity at its Arizona "
        "facility, adding new wafer fabrication equipment.",
        SourceCategory.INDEPENDENT_NEWS,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL


def test_confirmation_after_earlier_rumor_recap_remains_eligible():
    """A piece that both recaps earlier, now-resolved speculation AND
    separately reports the company's own subsequent, non-negated
    confirmation must not be suppressed by the earlier rumor language —
    the per-occurrence negation check (at least one clean confirmation
    is enough) plus the independent quantified-change gate both keep
    this eligible."""
    tier, reasons = classify_editorial_story(
        "Intel confirms Arizona expansion after weeks of speculation",
        "The plans had not been confirmed for weeks, with reports relying on unnamed sources close to the "
        "matter. Intel officially confirmed the $2 billion Arizona fab capacity expansion in a statement "
        "today, ending the speculation.",
        SourceCategory.INDEPENDENT_NEWS,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL


def test_denied_report_does_not_reach_high_signal():
    tier, reasons = classify_editorial_story(
        "Company denies reports of upcoming layoffs",
        "A spokesperson denied earlier reports that the company was planning layoffs, calling the "
        "speculation inaccurate and disclosed nothing further about internal plans.",
        SourceCategory.INDEPENDENT_NEWS,
    )
    assert tier != NewsMaterialityTier.HIGH_SIGNAL


# --- P1: interview/podcast guard (Fixture D — Kirkwood IG) -----------


def test_interview_without_new_measurable_disclosure_does_not_reach_high_signal():
    tier, reasons = classify_editorial_story(
        "Scott Bergs, CEO of Kirkwood IG: Fiber and the AI Data Center Buildout",
        "In this interview, Scott Bergs discusses how Kirkwood IG is expanding its fiber network to "
        "support the AI data center buildout across the Southeast, describing the capacity investment "
        "needed to keep pace with hyperscale demand.",
        SourceCategory.INDEPENDENT_NEWS,
    )
    assert tier != NewsMaterialityTier.HIGH_SIGNAL
    assert not any(r.startswith("taxonomy_anchored_consequence:") for r in reasons)


def test_interview_with_attributable_quantified_commitment_reaches_high_signal():
    """Positive control: the interview-format guard suppresses Gate C
    only — a genuine quantified disclosure made during the interview
    still qualifies via Gate B, unaffected."""
    tier, reasons = classify_editorial_story(
        "Scott Bergs, CEO of Kirkwood IG: Fiber and the AI Data Center Buildout",
        "In this interview, Scott Bergs disclosed a $300 million expansion of the company fiber route, "
        "adding 40,000 route-miles by 2027 to support AI data center capacity in the Southeast.",
        SourceCategory.INDEPENDENT_NEWS,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL
    assert any(r.startswith("quantified_change:") for r in reasons)


def test_podcast_format_marker_alone_suppresses_gate_c():
    tier, reasons = classify_editorial_story(
        "AI Infrastructure Weekly Podcast: Data Center Capacity Trends",
        "This week's podcast covers general trends in AI infrastructure and data center capacity "
        "planning across the industry.",
        SourceCategory.INDEPENDENT_NEWS,
    )
    assert tier != NewsMaterialityTier.HIGH_SIGNAL


# --- P1: first-party launch calibration (Fixture E — Meta One; Fixture F — Nvidia) ---


def test_first_party_launch_with_unrelated_boilerplate_capex_language_defaults_low():
    """The exact reported case: a first-party subscription/product
    launch whose only "AI infrastructure" language is generic capex
    boilerplate, several sentences away from the actual product-launch
    content — must not inflate to High Signal via Gate C's former
    "anywhere in the document" pairing."""
    tier, reasons = classify_issuer_story(
        "Introducing Meta One: A Subscription Service With More Features and AI to Create, Connect, "
        "and Stand Out",
        "Meta today introduced Meta One, a subscription service bringing together premium features "
        "across Instagram, Facebook, and WhatsApp, with plans supporting continued investment in the "
        "platform. It offers plans starting at $3.99 per month, with higher AI creation tools available "
        "in top tiers for creators. Meta One Premium also includes early access to new features as they "
        "roll out to eligible users. As we continue to invest in AI infrastructure to power new "
        "experiences for people worldwide, we are excited about what is ahead for our community.",
        SourceClass.OFFICIAL_COMPANY,
    )
    assert tier != NewsMaterialityTier.HIGH_SIGNAL


def test_first_party_launch_with_material_scale_evidence_reaches_high_signal():
    """Positive control: a first-party launch WITH a real, concrete
    materiality anchor (here, disclosed adoption + revenue scale in the
    same sentence) remains fully eligible."""
    tier, reasons = classify_issuer_story(
        "Introducing Meta One: A Subscription Service With More Features and AI to Create, Connect, "
        "and Stand Out",
        "Meta today introduced Meta One. Meta One Premium has already reached 50 million paid "
        "subscribers, adding an estimated $2 billion in annualized subscription revenue.",
        SourceClass.OFFICIAL_COMPANY,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL
    assert any(r.startswith("quantified_change:revenue") for r in reasons)


def test_nvidia_quantified_ai_factory_power_flexibility_remains_high_signal():
    """The required positive control: NVIDIA's own quantified AI-
    factory/power-flexibility disclosure must be unaffected by any of
    the P0/P1 tightening — reaches High Signal via the same-sentence
    Gate C pairing (and, independently, Gate B's own real MW figure)."""
    tier, reasons = classify_issuer_story(
        "From Megawatts to Tokens: How NVIDIA Maximizes AI Factory Production",
        "NVIDIA now delivers 50 megawatts of AI factory capacity per rack-scale deployment, a major new "
        "investment in cooling and power delivery infrastructure across the data center.",
        SourceClass.OFFICIAL_COMPANY,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL
    assert any(r.startswith("quantified_change:") for r in reasons)
    assert any(r.startswith("taxonomy_anchored_consequence:") for r in reasons)


def test_nvidia_forward_looking_projection_is_still_eligible():
    """Explicit forward-looking-statement check (required by the P1
    scope): a first-party projection ("could enable," "is expected to")
    that still states a real, quantified figure remains eligible —
    this fix never distinguishes measured-vs-projected for eligibility
    purposes, only whether a concrete materiality anchor exists at
    all."""
    tier, reasons = classify_issuer_story(
        "NVIDIA Outlines Path to Higher AI Factory Power Efficiency",
        "NVIDIA said its next-generation architecture could enable up to 100 megawatts of additional AI "
        "factory capacity at the same power envelope, a projected investment the company expects to "
        "translate into higher token throughput per watt.",
        SourceClass.OFFICIAL_COMPANY,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL


# --- P2: space taxonomy route (Fixture C positive control; Fixture H) ---


def test_space_mission_award_with_contract_anchor_reaches_high_signal():
    """The new space taxonomy bucket + an existing anchor ("contract")
    in the same sentence qualifies via Gate C — proving the new bucket
    integrates with the existing anchor-pairing/sentence-locality
    machinery rather than requiring new anchor vocabulary."""
    tier, reasons = classify_editorial_story(
        "KSAT Selected by Intuitive Machines to Support NASA JPL EAGLE-VSWIR Mission",
        "Intuitive Machines selected KSAT to provide ground segment support for the EAGLE-VSWIR mission, "
        "delivering the spacecraft platform and mission operations under a new contract. The mission is "
        "targeted for launch in 2028 as part of a broader NASA Earth Science Division program.",
        SourceCategory.INDEPENDENT_NEWS,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL
    assert any(r.startswith("taxonomy_anchored_consequence:space_missions_and_launch:") for r in reasons)


def test_space_mission_selection_never_asserts_launch_or_revenue_completion():
    """Explicit check that the space taxonomy route's own qualifying
    reason never itself claims a launch, revenue recognition, or
    mission completion — the underlying fixture text states only a
    selection/contract event with a future-targeted launch date, and
    the reason string is a fixed, generic gate label, never free text
    describing an unverified outcome."""
    tier, reasons = classify_editorial_story(
        "KSAT Selected by Intuitive Machines to Support NASA JPL EAGLE-VSWIR Mission",
        "Intuitive Machines selected KSAT to provide ground segment support for the EAGLE-VSWIR mission "
        "under a new contract. The mission is targeted for launch in 2028.",
        SourceCategory.INDEPENDENT_NEWS,
    )
    for reason in reasons:
        assert "launched" not in reason.lower()
        assert "completed" not in reason.lower()
        assert "revenue recognition" not in reason.lower()


def test_routine_personnel_note_with_broad_aerospace_commentary_stays_off_taxonomy():
    """The space taxonomy bucket must not elevate routine personnel or
    broad, non-specific aerospace commentary — no launch/mission/
    spacecraft/contract phrase appears in this fixture at all, so
    neither Gate C nor the Watchlist "on_taxonomy" fallback fires."""
    tier, reasons = classify_editorial_story(
        "Aerospace Industry Roundup: Executive Moves and Market Commentary",
        "Several aerospace companies announced routine leadership changes this week. Industry analysts "
        "commented broadly on the state of the space sector heading into next year.",
        SourceCategory.INDEPENDENT_NEWS,
    )
    assert tier == NewsMaterialityTier.BACKGROUND


# ============================================================
# Signals precision follow-up (design/SIGNALS_PRECISION_FOLLOWUP_RETAIL_
# BOILERPLATE_GUIDANCE_2026_09_16.md) — financial-guidance
# disambiguation and the denial/rumor Watchlist-fallback fix. Every
# fixture below is the exact live item confirmed in design/POST_MERGE_
# DAILY_NEWS_SIGNAL_PERFORMANCE_AUDIT_2026_09_16.md, or a required
# positive control, run through the real, unmodified
# classify_editorial_story().
# ============================================================


def test_terminal_guidance_national_security_item_never_matches_financial_guidance():
    """The exact confirmed live collision: "terminal guidance" (a
    weapons-targeting term in a genuine, non-boilerplate sentence about
    an Anthropic threat-intelligence report) must never count as a
    financial-guidance anchor. No taxonomy/anchor local pairing exists
    here at all once "guidance" is correctly excluded, so this item
    never reaches Gate B, C, or D via the word "guidance"."""
    tier, reasons = classify_editorial_story(
        "Autonomous drone swarm developed with AI assistance, threat report finds",
        "Researchers built swarm coordination, computer vision, and terminal guidance software for a "
        "drone program, according to a new threat intelligence report examining dual-use AI risks.",
        SourceCategory.INDEPENDENT_NEWS,
    )
    assert not any("guidance" in r for r in reasons)
    assert tier != NewsMaterialityTier.HIGH_SIGNAL


def test_earnings_guidance_with_no_disqualifying_modifier_remains_eligible():
    """Positive control: a genuine, unmodified financial-guidance
    disclosure — issued, raised, lowered, reaffirmed, or revised —
    remains a fully valid anchor. "guidance" is not broadly banned; only
    a small, curated set of disqualifying local modifiers (terminal,
    navigation, travel, ...) excludes one specific occurrence."""
    tier, reasons = classify_editorial_story(
        "Company X narrows full-year guidance, citing stronger-than-expected demand",
        "Company X today narrowed its full-year guidance to $4.2 billion to $4.4 billion, citing "
        "stronger-than-expected demand for its core cloud products.",
        SourceCategory.INDEPENDENT_NEWS,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL
    assert any(r.startswith("quantified_change:guidance") for r in reasons)


def test_denial_rumor_article_reaching_only_the_taxonomy_fallback_is_demoted_to_background():
    """The exact confirmed live Watchlist-tier defect: a denial story
    ("SK hynix denies ... rumors") reached visible Watchlist via the
    on_taxonomy_no_anchor fallback, which — unlike Gates C/D — was not
    gated by the existing rumor/negation/denial guard. Now demoted to
    Background; still admitted (the company match itself is genuine),
    just no longer surfaced by default."""
    tier, reasons = classify_editorial_story(
        "SK hynix denies US production rumors linked to Intel",
        "SK hynix has denied reports that it is in talks with Intel over memory chip manufacturing in the "
        "United States, calling the speculation premature.",
        SourceCategory.INDEPENDENT_NEWS,
    )
    assert tier == NewsMaterialityTier.BACKGROUND
    assert reasons == ("off_taxonomy_no_anchor",)


def test_material_denial_with_concrete_settlement_and_anchor_reaches_high_signal():
    """Positive control: an attributable denial that is itself a
    substantive issuer/regulatory/legal development — a real anchor
    (revenue) and a real quantified figure both present — must still
    reach HIGH_SIGNAL under the existing, unmodified hard gates (A/A2/
    A4/B/B2), all of which uncertain_reporting never touches."""
    tier, reasons = classify_editorial_story(
        "AST SpaceMobile's regulatory filing formally denies allegations, resolving matter with $50 "
        "million settlement",
        "According to a regulatory filing, AST SpaceMobile formally denied the allegations, but agreed to "
        "a $50 million settlement, recording a corresponding one-time reduction to quarterly revenue, the "
        "company disclosed in its 8-K filing this week.",
        SourceCategory.INDEPENDENT_NEWS,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL
    assert any(r.startswith("quantified_change:revenue") for r in reasons)


# --- Signals quality pass (2026-09-18): low-signal formats and issuer
# naming. Every negative below is a real item from the local 154-record
# issuer-lane cache that previously surfaced at Watchlist; each group is
# paired with real positive controls that must not move. ---

from src.data_access.daily_news.materiality_classification import (  # noqa: E402
    _low_signal_format_hit,
    issuer_name_forms,
)

_NVIDIA = issuer_name_forms("NVIDIA", "NVDA")
_INTEL = issuer_name_forms("Intel Corp.", "INTC")


def _issuer(headline, excerpt, names):
    return classify_issuer_story(headline, excerpt, SourceClass.OFFICIAL_COMPANY, issuer_names=names)


def test_event_appearance_headlines_drop_from_watchlist_to_background():
    """Generic attendance/presentation at a named event, with no concrete
    development in the headline."""
    for headline in (
        "Intel at AI Infra Summit 2026",
        "Intel Outlines Architectures for Agentic AI at Hot Chips 2026",
    ):
        tier, reasons = _issuer(headline, "", _INTEL)
        assert tier == NewsMaterialityTier.BACKGROUND, headline
        assert reasons[0].startswith("low_signal_format:event_appearance:"), (headline, reasons)


def test_careers_program_and_survey_report_drop_to_background():
    tier, reasons = _issuer("Creating Pathways to Semiconductor Careers: Intel Launches SEPP", "", _INTEL)
    assert (tier, reasons[0].split(":")[1]) == (NewsMaterialityTier.BACKGROUND, "careers_or_community")
    tier, reasons = _issuer(
        "AI Data Center Growth Hinges on Solving Both Power Constraints and Community Concerns, Bloom Energy Report Finds",
        "", issuer_name_forms("Bloom Energy Corp", "BE"),
    )
    assert (tier, reasons[0].split(":")[1]) == (NewsMaterialityTier.BACKGROUND, "survey_or_report")


def test_concrete_development_announced_at_a_conference_keeps_its_normal_tier():
    """Exception (precision refinement 2): the conference is incidental —
    a process-milestone announcement at VLSI Symposium stays Watchlist."""
    headline = "Intel Foundry Details Process Milestones and Future Innovation at VLSI Symposium"
    assert _low_signal_format_hit(headline, issuer_lane=True) is None
    assert _issuer(headline, "", _INTEL) == (
        NewsMaterialityTier.WATCHLIST, ("on_taxonomy_no_anchor:semiconductors_and_equipment",),
    )
    # A real launch/delivery headline under an event prefix is not demoted either.
    assert _low_signal_format_hit("AAI 2026: AMD Delivers Full-Stack Compute for the Agentic AI Era", issuer_lane=True) is None


def test_showcase_and_keynote_without_a_concrete_development_are_event_appearances():
    for headline in (
        "MaxLinear Showcases Panther for AI Storage Efficiency and AI Inference Performance at FMS 2026",
        "Marvell Keynote at COMPUTEX 2026: The Future of AI Scaling Depends on Connectivity",
        "Sparks Fly: NVIDIA Accelerates Local AI at IFA 2026",
    ):
        hit = _low_signal_format_hit(headline, issuer_lane=True)
        assert hit is not None and hit.startswith("event_appearance:"), (headline, hit)


def test_blog_hosted_technical_item_is_never_demoted_for_its_hosting():
    """Exception (precision refinement 1): this real item is published on
    blogs.nvidia.com; hosting is not a signal, so it goes through the
    normal classifier and stays Watchlist (on-taxonomy, no anchor)."""
    headline = "NVIDIA NVLink Fusion Expands With NVHBM Custom High-Bandwidth Memory"
    assert _low_signal_format_hit(headline, issuer_lane=True) is None
    tier, reasons = _issuer(
        headline,
        "The next wave of AI is placing new demands on infrastructure. As AI agents and trillion-parameter "
        "workloads become mainstream, the performance of AI infrastructure depends not only on compute, but on "
        "how compute, memory, storage, networking and software are designed together as a unified system.",
        _NVIDIA,
    )
    assert tier == NewsMaterialityTier.WATCHLIST
    assert reasons[0].startswith("on_taxonomy_no_anchor:")


def test_generic_issuer_essay_headlines_are_low_signal_only_without_concrete_content():
    for headline in (
        "How XPUs Meet a World-Class AI Factory",
        "Why Scaling AI Compute Performance Requires a New Power Architecture",
        "[AI Ecosystem] The real bottleneck: Data, not compute",
    ):
        hit = _low_signal_format_hit(headline, issuer_lane=True)
        assert hit is not None and hit.startswith("issuer_essay:"), (headline, hit)
    # Same essay shapes carrying a concrete development are exempt.
    for headline in (
        "How NVIDIA and AWS Will Deliver 2 Million Additional GPUs",
        "[AI Ecosystem] SK hynix Begins Mass Production of HBM4E",
    ):
        assert _low_signal_format_hit(headline, issuer_lane=True) is None, headline


def test_sponsorship_award_and_gaming_headlines_are_low_signal_formats():
    for headline, kind in (
        ("Intel Named Official Compute Partner of McLaren Racing", "sponsorship"),
        ("Cisco and USGA Extend Partnership with Renewed Focus on Powering the Game of Golf in the AI Era", "sponsorship"),
        ("Arista Networks Positioned as a Leader in the 2026 Gartner® Magic Quadrant™ for Enterprise Wired and Wireless LAN Infrastructure", "award_or_recognition"),
        ("NVIDIA CEO Tops Glassdoor’s 2026 List of Best CEOs", "award_or_recognition"),
        ("MaxLinear Panther Storage Accelerator Wins FMS 2026 Best of Show Award in Servers & Networking Category", "award_or_recognition"),
        ("‘NBA 2K27’ With NVIDIA DLSS 5 Leads 26 New Games Coming to GeForce NOW", "consumer_gaming"),
        ("Rockwell Automation to Present at Morgan Stanley 14th Annual Laguna Conference", "event_appearance"),
    ):
        hit = _low_signal_format_hit(headline, issuer_lane=True)
        assert hit is not None and hit.startswith(f"{kind}:"), (headline, hit)


def test_issuer_not_named_items_are_background():
    for company, ticker, headline, excerpt in (
        ("nVent Electric plc", "NVT", "Siemens and partners develop reference  architecture purpose-built for NVIDIA AI data centers", ""),
        ("SK Hynix", "000660", "[AI Infrastructure Insight] Why faster GPUs alone can’t deliver AI performance", ""),
        ("NVIDIA", "NVDA", "How XPUs Meet a World-Class AI Factory",
         "To generate intelligence at scale, AI factories run continuously, and their economics are defined by delivered output."),
    ):
        assert _issuer(headline, excerpt, issuer_name_forms(company, ticker)) == (
            NewsMaterialityTier.BACKGROUND, ("issuer_not_named",),
        ), headline


def test_issuer_naming_accepts_first_word_and_case_sensitive_ticker():
    cisco = issuer_name_forms("Cisco Systems, Inc.", "CSCO")
    assert "Cisco" in cisco.names and "Cisco Systems" in cisco.names
    assert _issuer("Cisco Reports Fourth Quarter Earnings", "", cisco)[0] == NewsMaterialityTier.HIGH_SIGNAL
    assert _issuer("nVent Expands Data Center Liquid Cooling Capacity", "", issuer_name_forms("nVent Electric plc", "NVT"))[0] == NewsMaterialityTier.HIGH_SIGNAL
    amd = issuer_name_forms("Advanced Micro Devices", "AMD")
    assert "Advanced" not in amd.names  # generic first word never used on its own
    assert _issuer(
        "AMD, Cisco and HUMAIN Expand Saudi Arabia’s AI Infrastructure as AMD Instinct Systems Go Live", "", amd,
    )[0] == NewsMaterialityTier.WATCHLIST
    assert _issuer("amd lowercase prose mention of data center gpus", "", amd) == (
        NewsMaterialityTier.BACKGROUND, ("issuer_not_named",),
    )


def test_hard_gates_survive_a_low_signal_format():
    """Positive control: NVIDIA's blog-hosted $500B financing-platform
    announcement reaches High Signal via Gate B, and a low-signal format
    (here an event framing) never blocks a hard gate."""
    tier, reasons = _issuer(
        "NVIDIA AI Factory Compute Is Becoming an Investable Asset Class",
        "We announced partnerships with Apollo, BlackRock, Blackstone, Brookfield, Goldman Sachs and KKR to "
        "establish independent financing platforms designed to mobilize over $500 billion of third-party capital "
        "to support the buildout of AI infrastructure over time.",
        _NVIDIA,
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL
    assert reasons[0].startswith("quantified_change:")
    tier, reasons = _issuer(
        "Rockwell Automation to Present at Morgan Stanley Conference; Approves $1 Billion Stock Repurchase",
        "", issuer_name_forms("Rockwell Automation", "ROK"),
    )
    assert tier == NewsMaterialityTier.HIGH_SIGNAL
    assert reasons[0].startswith("quantified_capital_return:")


def test_real_high_signal_and_ambiguous_watchlist_controls_do_not_move():
    for company, ticker, headline, expected in (
        ("Advanced Micro Devices", "AMD", "AMD and Anthropic Announce Strategic Partnership to Deploy Up to 2 Gigawatts of AMD Instinct MI450 Series GPUs", NewsMaterialityTier.HIGH_SIGNAL),
        ("Rockwell Automation", "ROK", "Rockwell Automation Approves $1 Billion for Common Stock Repurchase and Declares Common Stock Dividend", NewsMaterialityTier.HIGH_SIGNAL),
        ("Quanta Services, Inc.", "PWR", "QUANTA SERVICES REPORTS SECOND QUARTER 2026 RESULTS", NewsMaterialityTier.HIGH_SIGNAL),
        ("Marvell Technology, Inc.", "MRVL", "Marvell Announces Availability of Industry’s First 102.4 Tbps Switch Purpose-Built for AI and Cloud Data Centers", NewsMaterialityTier.WATCHLIST),
        ("Arista Networks, Inc.", "ANET", "Arista Networks to Announce Q2 2026 Financial Results on Tuesday, August 4, 2026", NewsMaterialityTier.WATCHLIST),
        ("MaxLinear, Inc.", "MXL", "MaxLinear, Inc. Announces Conference Call to Review Second Quarter 2026 Financial Results", NewsMaterialityTier.WATCHLIST),
        ("Quanta Services, Inc.", "PWR", "Quanta Services Announces Quarterly Cash Dividend", NewsMaterialityTier.WATCHLIST),
    ):
        assert _issuer(headline, "", issuer_name_forms(company, ticker))[0] == expected, headline
    # Real excerpt (the headline alone is off-taxonomy): stays Watchlist.
    assert _issuer(
        "Cisco Expands Secure AI Factory with NVIDIA for the Rack-Scale Era",
        "The next era of AI infrastructure can't just deliver raw compute; it must harness that compute "
        "through validated, full-stack architectures",
        issuer_name_forms("Cisco Systems, Inc.", "CSCO"),
    )[0] == NewsMaterialityTier.WATCHLIST


def test_material_headlines_with_overlapping_words_are_not_low_signal_formats():
    for headline in (
        "Micron awarded $6.1 billion CHIPS Act grant to expand memory fab capacity",
        "Big Tech racing to build AI data centers as power demand surges",
        "Nvidia gaming revenue rises 20% on RTX demand",
        "Intel Announces Leadership Appointment at Intel Foundry to Accelerate Development and Manufacturing",
    ):
        assert _low_signal_format_hit(headline, issuer_lane=True) is None, headline


def test_essay_rules_never_apply_to_the_editorial_lane():
    assert _low_signal_format_hit("Why Nvidia's data center story is changing", issuer_lane=False) is None
    assert _low_signal_format_hit("[Analysis] Samsung HBM supply", issuer_lane=False) is None


def test_omitting_issuer_names_keeps_previous_behavior_for_other_callers():
    tier, reasons = classify_issuer_story(
        "Siemens and partners develop reference  architecture purpose-built for NVIDIA AI data centers",
        "", SourceClass.OFFICIAL_COMPANY,
    )
    assert tier == NewsMaterialityTier.WATCHLIST
    assert reasons[0].startswith("on_taxonomy_no_anchor:")
