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
