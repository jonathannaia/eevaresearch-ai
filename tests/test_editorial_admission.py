"""Editorial Signals admission gate (design/DECISIONS.md, "precision-
first correction" — the Nintendo/Amazon false-positive audit). Pure
function tests, no I/O, no AppTest.

Every false-positive fixture here is a real headline shape found during
the audit (or a close paraphrase) — the audit's own "production sample"
disclosure applies here too: this environment has no local editorial-
lane cache or Postgres access, so these are representative
reconstructions run through the real, unmodified admission code, not a
literal production pull."""
from __future__ import annotations

from src.data_access.daily_news.editorial_admission import AdmissionDecision, assess_admission
from src.data_access.daily_news.editorial_matching import matched_companies_and_themes
from src.data_access.daily_news.materiality_classification import classify_editorial_story
from src.data_access.daily_news.source_registry import SourceCategory
from src.models.daily_news_models import NewsMaterialityTier


def _assess(title: str, description: str | None) -> AdmissionDecision:
    """End-to-end helper mirroring exactly what editorial_pipeline.py
    itself does: match, classify, then assess admission — so these
    tests exercise the real integration shape, not just assess_admission()
    in isolation with hand-picked matched_companies/reasons."""
    companies, themes = matched_companies_and_themes(title, description)
    tier, reasons = classify_editorial_story(title, description, SourceCategory.INDEPENDENT_NEWS)
    return assess_admission(title, description, companies, themes, reasons)


# ============================================================
# Verified false positives — must all fail admission (not merely
# demote to Background)
# ============================================================


def test_nintendo_customer_appreciation_sale_fails_admission():
    """The exact reported case: a Nintendo sale article, admitted and
    persisted under Amazon.com, Inc. purely because the description said
    "available on Amazon.com" — zero indication anything Amazon-specific
    happened. Must fail admission entirely, not merely land in
    Background."""
    decision = _assess(
        "Nintendo Switch Customer Appreciation Sale: Save Up to 30% on Select Games",
        "Nintendo is running its annual Customer Appreciation Sale, offering discounts on top titles. "
        "Deals are live now on Amazon.com and the Nintendo eShop, with select games marked down as much as 30%.",
    )
    assert decision.admitted is False
    assert decision.reason.startswith("consumer_editorial_format:")


def test_stock_roundup_mention_fails_admission():
    decision = _assess(
        "Dow Jones Today: Stocks Rally as Apple, Amazon, and Intel Lead Gains",
        "Major indexes rose Tuesday, with Amazon, Apple, and Intel among the biggest gainers in a broad market rally.",
    )
    assert decision.admitted is False


def test_buying_guide_comparison_fails_admission():
    """A taxonomy keyword (GPU) alone must not admit a consumer buying
    guide — even though this genuinely reaches Watchlist tier on
    taxonomy grounds, it must still fail admission as a comparison-
    format article."""
    decision = _assess(
        "AMD vs NVIDIA: Which GPU Should You Buy for Gaming in 2026?",
        "We compare the latest AMD and NVIDIA graphics cards to help you decide which GPU is right for your gaming setup this year.",
    )
    assert decision.admitted is False
    assert decision.reason.startswith("consumer_editorial_format:")


def test_gift_guide_mention_fails_admission():
    decision = _assess(
        "Best Holiday Tech Gifts: NVIDIA Shield TV Tops Our List",
        "Our holiday gift guide rounds up the best streaming devices, including the NVIDIA Shield TV as a top pick for movie fans.",
    )
    assert decision.admitted is False
    assert decision.reason.startswith("consumer_editorial_format:")


def test_disco_corporation_word_collision_fails_admission():
    decision = _assess(
        "Best Disco Playlists to Get Your 80s Dance Party Started",
        "From Donna Summer to the Bee Gees, here is our ultimate disco playlist for your next retro dance party.",
    )
    assert decision.admitted is False
    assert decision.reason == "company_mention_not_subject_worthy"


def test_oracle_of_omaha_idiom_collision_fails_admission():
    decision = _assess(
        "Warren Buffett: The Oracle of Omaha Shares His Best Investing Advice",
        "Known as the Oracle of Omaha, Buffett has built a reputation for plainspoken wisdom about long-term investing.",
    )
    assert decision.admitted is False
    assert decision.reason == "company_mention_not_subject_worthy"


def test_mks_unit_system_word_collision_fails_admission():
    """MKS Inc's mechanical short-form alias "MKS" collides with the
    standard meter-kilogram-second unit-system abbreviation used in
    physics/engineering writing — discovered during the full alias-
    universe audit (requirement 9), not in the original bug report."""
    decision = _assess(
        "Physics Teachers Debate Whether to Keep Teaching MKS Units in High School",
        "A growing number of physics educators argue the MKS system of units is outdated compared to SI.",
    )
    assert decision.admitted is False
    assert decision.reason == "company_mention_not_subject_worthy"


def test_towa_vtuber_name_collision_fails_admission():
    """TOWA Corporation's alias "TOWA" collides with the personal name/
    handle of a well-known VTuber — a rival-real-entity collision (not a
    common word), closed with a narrow curated exclusion phrase list
    since the colliding entity also issues announcement-shaped coverage
    that the generic action-language check alone cannot distinguish."""
    decision = _assess(
        "Popular VTuber Towa Announces New Merch Line for Fans",
        "The virtual streamer known as Towa revealed a new line of plushies and apparel for her channel.",
    )
    assert decision.admitted is False
    assert decision.reason == "company_mention_not_subject_worthy"


def test_ceva_logistics_name_collision_fails_admission():
    """CEVA Inc's alias "CEVA" collides with CEVA Logistics, an
    unrelated, real global freight-forwarding company."""
    decision = _assess(
        "CEVA Logistics Opens New Distribution Center in Texas",
        "Global freight forwarder CEVA Logistics announced a new 500,000 square foot distribution center.",
    )
    assert decision.admitted is False
    assert decision.reason == "company_mention_not_subject_worthy"


def test_nokia_finland_town_name_collision_fails_admission():
    decision = _assess(
        "Historic Nokia Mill Building to Reopen as Cultural Center",
        "The town of Nokia, Finland is renovating its old paper mill into a public cultural center.",
    )
    assert decision.admitted is False
    assert decision.reason == "company_mention_not_subject_worthy"


# ============================================================
# Required negative regression fixtures (identity-precedes-materiality
# correction) — run end-to-end through the real matcher and classifier,
# exactly the literal scenarios that exposed the gap
# ============================================================


def test_oracle_of_omaha_raises_stake_by_20_percent_fails_admission():
    """The exact gap found: "raises" is company-action language and
    "Oracle" is in the title, which the old (in_title AND action
    language) bar alone treated as sufficient — without checking whether
    the actor doing the raising is Oracle Corporation at all. Buffett
    raising his own stake in a bank is not an Oracle Corporation
    development."""
    decision = _assess(
        "Oracle of Omaha Raises Stake by 20%",
        "The Oracle of Omaha, Warren Buffett, disclosed that Berkshire Hathaway raised its "
        "stake in a major bank by 20% this quarter.",
    )
    assert decision.admitted is False


def test_ceva_logistics_reports_25_percent_revenue_growth_fails_admission():
    """The exact gap found: CEVA Logistics' own genuine quantified-
    revenue language triggers real anchor evidence for the STORY, which
    the old code let bypass the CEVA Inc/CEVA Logistics rival-entity
    exclusion outright. A rival entity's own earnings are still not
    CEVA Inc's."""
    decision = _assess(
        "CEVA Logistics Reports 25% Revenue Growth",
        "CEVA Logistics announced its quarterly results, reporting 25% revenue growth "
        "driven by strong freight volumes.",
    )
    assert decision.admitted is False


def test_nintendo_sale_offers_30_percent_off_at_amazon_fails_admission():
    decision = _assess(
        "Nintendo Sale Offers 30% Off at Amazon.com",
        "Nintendo games are marked down as much as 30% off this week, available now on Amazon.com.",
    )
    assert decision.admitted is False


def test_best_nvidia_gpus_under_500_fails_admission():
    """A "best X under $Y" buying-guide headline — NVIDIA is in the
    title, which alone would previously have been sufficient for a
    non-ambiguous company. Must still fail as a consumer buying-guide
    format with no genuine corporate-event anchor."""
    decision = _assess(
        "Best NVIDIA GPUs Under $500",
        "We rounded up the best NVIDIA graphics cards you can buy for under $500 right now.",
    )
    assert decision.admitted is False
    assert decision.reason.startswith("consumer_editorial_format:")


# ============================================================
# Positive controls — genuine corporate developments must still be
# admitted, for every company named in the false-positive fixtures above
# ============================================================


def test_genuine_amazon_capex_story_is_admitted():
    decision = _assess(
        "Amazon Announces $10 Billion Expansion of AWS Data Center Capacity in Virginia",
        "Amazon today announced a $10 billion investment to expand AWS data center capacity, "
        "adding new facilities across Virginia over the next three years.",
    )
    assert decision.admitted is True
    assert decision.reason == "company_subject:Amazon.com, Inc."


def test_genuine_intel_earnings_story_is_admitted():
    decision = _assess(
        "Intel Reports Second-Quarter 2026 Financial Results",
        "Intel today reported financial results for its second quarter, including revenue and updated full-year guidance.",
    )
    assert decision.admitted is True
    assert decision.reason == "company_subject:Intel Corp."


def test_genuine_nvidia_regulatory_story_is_admitted():
    decision = _assess(
        "NVIDIA Faces New Export Restrictions on AI Chips to China, Commerce Department Says",
        "The U.S. Commerce Department announced new export restrictions affecting NVIDIA AI chip sales to China, "
        "citing national security concerns.",
    )
    assert decision.admitted is True
    assert decision.reason == "company_subject:NVIDIA"


def test_genuine_oracle_contract_story_is_admitted():
    decision = _assess(
        "Oracle Signs $5 Billion Cloud Infrastructure Contract with Major Healthcare Provider",
        "Oracle announced it has signed a $5 billion, multi-year contract to provide cloud infrastructure "
        "services to a major healthcare provider.",
    )
    assert decision.admitted is True
    assert decision.reason == "company_subject:Oracle Corporation"


def test_genuine_disco_corporation_earnings_story_is_admitted():
    decision = _assess(
        "Disco Corporation Reports Record Quarterly Revenue on Strong Semiconductor Equipment Demand",
        "Disco Corporation reported record quarterly revenue, citing strong demand for its semiconductor "
        "dicing and grinding equipment.",
    )
    assert decision.admitted is True
    assert decision.reason == "company_subject:Disco Corporation"


def test_genuine_mks_earnings_story_is_admitted():
    decision = _assess(
        "MKS Inc Reports Third-Quarter Revenue Growth on Strong Semiconductor Demand",
        "MKS Inc announced quarterly financial results, citing strong demand for its vacuum and process control equipment.",
    )
    assert decision.admitted is True
    assert decision.reason == "company_subject:MKS Inc"


def test_genuine_towa_corporation_order_story_is_admitted():
    decision = _assess(
        "TOWA Corporation Announces New Semiconductor Packaging Equipment Order",
        "TOWA Corporation disclosed a major new order for its semiconductor molding and packaging systems.",
    )
    assert decision.admitted is True
    assert decision.reason == "company_subject:TOWA Corporation"


def test_genuine_ceva_inc_licensing_story_is_admitted():
    decision = _assess(
        "CEVA Inc Signs New IP Licensing Agreement with Major Chipmaker",
        "CEVA Inc announced it has signed a new wireless IP licensing agreement with a leading chip manufacturer.",
    )
    assert decision.admitted is True
    assert decision.reason == "company_subject:CEVA INC"


def test_genuine_nokia_5g_deal_story_is_admitted():
    decision = _assess(
        "Nokia Signs 5G Network Equipment Deal with European Carrier",
        "Nokia announced a new multi-year agreement to supply 5G radio equipment to a major European telecom operator.",
    )
    assert decision.admitted is True
    assert decision.reason == "company_subject:Nokia Corp"


# ============================================================
# Alias-confidence tiering (requirement 3)
# ============================================================


def test_ambiguous_alias_title_placement_alone_without_action_language_is_insufficient():
    """Unlike a non-ambiguous company, an ambiguous alias in the title
    with no company-action language and no hard material evidence must
    NOT be admitted by title placement alone."""
    decision = assess_admission(
        "Oracle Predictions for 2026: What the Experts Say",
        "A roundup of predictions from various industry oracles about what 2026 might bring.",
        matched_companies=("Oracle Corporation",), matched_themes=(),
        materiality_reasons=("off_taxonomy_no_anchor",),
    )
    assert decision.admitted is False


def test_ambiguous_alias_with_title_placement_and_action_language_is_admitted():
    decision = assess_admission(
        "Oracle Announces New Partnership with Major Bank",
        None,
        matched_companies=("Oracle Corporation",), matched_themes=(),
        materiality_reasons=("off_taxonomy_no_anchor",),
    )
    assert decision.admitted is True
    assert decision.reason == "company_subject:Oracle Corporation"


def test_non_ambiguous_company_title_placement_alone_is_sufficient():
    """NVIDIA is not in the ambiguous-alias set — title placement alone
    (no action-language requirement) is enough, matching "title placement
    as strong evidence, not an absolute requirement"."""
    decision = assess_admission(
        "NVIDIA Unveils Next-Generation GPU Architecture",
        None,
        matched_companies=("NVIDIA",), matched_themes=(),
        materiality_reasons=("off_taxonomy_no_anchor",),
    )
    assert decision.admitted is True


def test_non_ambiguous_company_description_only_mention_needs_action_language():
    """Requirement 4: description-only matches (not in the title) still
    need company-action-language support even for a non-ambiguous
    company — an incidental retailer/marketplace mention in the body
    alone is not enough."""
    decision = assess_admission(
        "Best Budget Laptops for Students in 2026",
        "Several of these laptops use displays with NVIDIA GPUs for enhanced graphics performance.",
        matched_companies=("NVIDIA",), matched_themes=(),
        materiality_reasons=("off_taxonomy_no_anchor",),
    )
    assert decision.admitted is False
    assert decision.reason == "company_mention_not_subject_worthy"


# ============================================================
# Identity precedes materiality (correction requested after the initial
# implementation): anchor evidence (materiality_reasons) must NEVER be
# usable to establish identity itself — not for an ambiguous alias
# without independent disambiguation, not past a rival-entity exclusion,
# not for a bare description-only mention. It has exactly one job: once
# a company is ALREADY identified as the subject through placement/
# action-language/rival-entity signals alone, it can excuse an otherwise
# disqualifying consumer-format-shaped phrase elsewhere in the text.
# ============================================================


def test_anchor_evidence_does_not_rescue_an_undisambiguated_ambiguous_alias():
    """Oracle Corporation is in title but the text has no company-action
    language — under the OLD (pre-correction) implementation, hard
    evidence alone was enough to bypass this and admit the story purely
    because the STORY happened to carry a genuine earnings signal, even
    though nothing establishes Oracle Corporation itself as this
    sentence's subject. Must now be rejected: identity was never
    established, so the anchor evidence is irrelevant."""
    decision = assess_admission(
        "Quarterly Update",
        "Oracle Corporation reports financial results for its fiscal quarter, disclosing revenue growth.",
        matched_companies=("Oracle Corporation",), matched_themes=(),
        materiality_reasons=("formal_earnings_materials:financial results",),
    )
    assert decision.admitted is False
    assert decision.reason == "company_mention_not_subject_worthy"


def test_anchor_evidence_does_not_rescue_a_rival_entity_exclusion():
    """CEVA Inc's rival-entity exclusion (CEVA Logistics) must hold even
    when the surrounding text carries genuine anchor-shaped materiality
    language — a rival entity's own earnings release is still a rival
    entity's story, never CEVA Inc's."""
    decision = assess_admission(
        "CEVA Logistics Reports Formal Quarterly Earnings Results",
        "CEVA Logistics, the global freight forwarder, filed its formal earnings materials "
        "showing a 12% increase in quarterly revenue.",
        matched_companies=("CEVA INC",), matched_themes=(),
        materiality_reasons=("formal_earnings_materials:quarterly results", "quantified_change:revenue"),
    )
    assert decision.admitted is False
    assert decision.reason == "company_mention_not_subject_worthy"


def test_anchor_evidence_does_not_rescue_a_description_only_incidental_mention():
    """A description-only company mention with neither title placement
    nor company-action language must stay unadmitted even when
    materiality_reasons happens to carry a genuine anchor for the STORY
    overall — the anchor says nothing about whether this particular
    company is the subject."""
    decision = assess_admission(
        "Tech Sector Overview: Chips, Components, and Market Trends",
        "Several devices in this roundup use components, including NVIDIA GPUs, for graphics processing.",
        matched_companies=("NVIDIA",), matched_themes=(),
        materiality_reasons=("formal_earnings_materials:quarterly earnings",),
    )
    assert decision.admitted is False
    assert decision.reason == "company_mention_not_subject_worthy"


# ============================================================
# The exception clause (requirement 5): a genuine material development
# for an ALREADY-IDENTIFIED subject overrides a coincidental
# consumer-format-shaped phrase — identity must come first
# ============================================================


def test_anchor_evidence_overrides_a_consumer_format_match_once_identity_is_established():
    """Oracle Corporation is in the title AND the text carries company-
    action language ("reports") — identity is independently established
    before anchor evidence is ever consulted. Only then does the genuine
    earnings anchor excuse the coincidental "% off" phrasing."""
    decision = assess_admission(
        "Oracle Corporation Reports Second-Quarter Financial Results",
        "Oracle Corporation reported financial results for its fiscal second quarter; "
        "shares dropped 5% off their prior close after the report.",
        matched_companies=("Oracle Corporation",), matched_themes=(),
        materiality_reasons=("formal_earnings_materials:financial results",),
    )
    assert decision.admitted is True
    assert decision.reason == "company_subject:Oracle Corporation"


def test_consumer_format_exception_requires_anchor_not_just_identity():
    """Identity alone (title placement) is not enough to excuse a
    consumer-format phrase — the exception also requires genuine anchor
    evidence, never a bare quantity like a discount percentage standing
    in for one."""
    decision = assess_admission(
        "Amazon Sale Offers 30% Off NVIDIA GPUs",
        "This weekend, Amazon is offering 30% off select NVIDIA GPUs as part of a limited-time sale.",
        matched_companies=("Amazon.com, Inc.", "NVIDIA"), matched_themes=(),
        materiality_reasons=("on_taxonomy_no_anchor:ai_compute_and_data_center",),
    )
    assert decision.admitted is False
    assert decision.reason.startswith("consumer_editorial_format:")


# ============================================================
# Theme-only admission (requirement 6)
# ============================================================


def test_theme_only_match_with_no_consumer_format_is_admitted():
    decision = assess_admission(
        "Export Controls Tighten on Advanced Semiconductor Equipment Sales",
        "New rules restrict sales of advanced lithography and etch equipment to certain countries.",
        matched_companies=(), matched_themes=("ai-buildout",),
        materiality_reasons=("off_taxonomy_no_anchor",),
    )
    assert decision.admitted is True
    assert decision.reason == "theme_subject:ai-buildout"


def test_theme_only_match_inside_a_consumer_format_still_fails():
    decision = assess_admission(
        "Best AI Data Center Gadgets: Our Gift Guide for Tech Lovers",
        "Our holiday gift guide rounds up fun AI data center-themed gadgets for the tech enthusiast in your life.",
        matched_companies=(), matched_themes=("ai-buildout",),
        materiality_reasons=("off_taxonomy_no_anchor",),
    )
    assert decision.admitted is False
    assert decision.reason.startswith("consumer_editorial_format:")


# ============================================================
# Defensive: no match at all (the pipeline's own pre-existing gate
# already prevents this from being called, but the function itself
# fails closed regardless)
# ============================================================


def test_no_company_or_theme_match_fails_closed():
    decision = assess_admission(
        "A Generic Headline About Nothing In Particular", None,
        matched_companies=(), matched_themes=(), materiality_reasons=("off_taxonomy_no_anchor",),
    )
    assert decision.admitted is False
    assert decision.reason == "no_qualifying_subject_evidence"


# ============================================================
# Signals editorial lane false-positive audit (design/DECISIONS.md) —
# five verified live false positives, reconstructed from the reported
# headline shapes (this environment has no live editorial-lane cache or
# Postgres access — see this file's own opening disclosure). Each fixture
# covers one of the five requested categories: shopping/deal/discount
# articles, consumer PC specs/model numbers, game mods/unofficial
# ports/emulation/hobbyist software, general AI opinion/commentary with
# no concrete corporate event, and incidental product/company mentions.
# ============================================================


def test_unofficial_game_port_mentioning_the_owning_company_historically_fails_admission():
    """Hobbyist/mod/unofficial-port category. Real false positive:
    admitted and tagged to Microsoft Corporation purely because the
    article mentions, in passing, that Microsoft acquired Mojang (the
    Minecraft developer) in 2014 — a historical/background fact, not a
    current Microsoft event. Must not match Microsoft at all."""
    companies, _ = matched_companies_and_themes(
        "Modder Rewrites Minecraft Legacy Console Engine to Run on PS2 and Wii in Just 32MB of RAM",
        "A hobbyist developer has rebuilt Minecraft's legacy console engine from scratch to run on the "
        "PlayStation 2 and Wii. Microsoft acquired Mojang, the Swedish studio behind Minecraft, for $2.5 "
        "billion in 2014, and the original console edition was later discontinued on modern platforms.",
    )
    assert "Microsoft Corporation" in companies  # matched by keyword, but must not be IDENTIFIED as subject
    decision = _assess(
        "Modder Rewrites Minecraft Legacy Console Engine to Run on PS2 and Wii in Just 32MB of RAM",
        "A hobbyist developer has rebuilt Minecraft's legacy console engine from scratch to run on the "
        "PlayStation 2 and Wii. Microsoft acquired Mojang, the Swedish studio behind Minecraft, for $2.5 "
        "billion in 2014, and the original console edition was later discontinued on modern platforms.",
    )
    assert decision.admitted is False


def test_incidental_customer_mention_in_an_unrelated_labor_story_fails_admission():
    """Incidental-mention category. Real false positive: an article
    about LS Cable & System's own labor dispute (LS Cable is not a
    tracked company) wrongly matched SK Hynix and Samsung Electronics —
    named only as customers of LS Cable's cable products, doing nothing
    themselves in the text. Neither company should be identified as the
    story's subject."""
    title = "LS Cable & System Workers Threaten First Strike in 37 Years Over Wage Dispute"
    description = (
        "LS Cable & System announced its labor union is threatening the company's first strike in 37 "
        "years after wage talks stalled. The cable maker supplies components used across Korea's "
        "electronics industry, including customers such as SK Hynix and Samsung Electronics."
    )
    companies, _ = matched_companies_and_themes(title, description)
    assert set(companies) == {"SK Hynix", "Samsung Electronics"}
    decision = _assess(title, description)
    assert decision.admitted is False
    assert decision.reason == "company_mention_not_subject_worthy"


def test_incidental_mention_with_an_explicit_relationship_is_still_admitted():
    """Companion proof to the LS Cable case above: when a company named
    alongside an unrelated actor's action IS itself the one taking that
    action (a genuine, explicit relationship — not mere customer-list
    context), it must still be admitted normally. Tightening the
    incidental-mention gate must never suppress a real event."""
    title = "SK Hynix Signs Long-Term Supply Deal With LS Cable & System"
    description = "SK Hynix signed a multi-year agreement with LS Cable & System to secure cabling components for its new memory fabs."
    decision = _assess(title, description)
    assert decision.admitted is True
    assert decision.reason == "company_subject:SK Hynix"


def test_ai_opinion_commentary_mentioning_a_company_only_via_a_founders_history_fails_admission():
    """General AI opinion/interview commentary category — the exact
    verified false positive, dedicated regression. A Bill Gates
    AI-commentary piece was tagged to Microsoft Corporation purely
    because Gates is described as "Microsoft co-founder" — Gates's own
    historical company affiliation, not any current Microsoft action.
    Must be rejected as company_mention_not_subject_worthy; must not
    create a Microsoft OR Broadcom subject match based solely on the
    historical/co-founder phrasing and incidental chipmaker
    name-dropping; and must never reach High Signal regardless of
    admission (defense in depth — this fixture asserts materiality
    directly, not merely inferring it from the admission rejection)."""
    title = "Bill Gates Compares AI to Alien Intelligence, Warns Governments to Prepare"
    description = (
        "Microsoft co-founder Bill Gates said artificial intelligence should be treated like contact with "
        "an alien intelligence and warns governments around the world need to prepare for the societal "
        "shift AI will bring. AI chipmakers such as Nvidia, AMD, and Broadcom have benefited from the "
        "surge in demand Gates described."
    )
    companies, themes = matched_companies_and_themes(title, description)
    assert "Microsoft Corporation" in companies  # matched by keyword, but must not be IDENTIFIED as subject
    assert "Broadcom Inc." in companies  # matched by keyword, but must not be IDENTIFIED as subject

    tier, reasons = classify_editorial_story(title, description, SourceCategory.INDEPENDENT_NEWS)
    assert tier != NewsMaterialityTier.HIGH_SIGNAL

    decision = assess_admission(title, description, companies, themes, reasons)
    assert decision.admitted is False
    assert decision.reason == "company_mention_not_subject_worthy"


def test_gaming_pc_deal_with_specs_and_a_dollar_discount_is_admitted_but_never_high_signal():
    """Shopping/deal/discount + consumer PC specs/model numbers
    category. Real false positive: a $560-off gaming PC deal reached
    HIGH_SIGNAL purely because the article's own "price cut" marketing
    copy co-occurred with a percentage figure — pure retail pricing, not
    corporate quantification. The company match itself (NVIDIA, named
    in the title) is legitimate and admission is correct to let it
    through — the defect was materiality tier, verified separately in
    tests/test_daily_news_materiality_classification.py. This test
    locks in the admission side only: still admitted, never rejected
    outright, since a real component vendor is genuinely named."""
    title = "Save 25% ($560) on This Gaming PC Packed With AMD and Nvidia Hardware"
    description = (
        "This gaming PC deal pairs an AMD Ryzen 7 processor with an Nvidia GeForce RTX 4070 graphics "
        "card, marking one of the biggest price cut deals we've seen on this configuration this year."
    )
    decision = _assess(title, description)
    assert decision.admitted is True
    assert decision.reason == "company_subject:NVIDIA"


# ============================================================
# Preserve legitimate events — the false-positive fixes above must
# never broadly suppress a real product launch, corporate deployment,
# supply disruption, labor action, or company-specific regulatory
# development, even when it shares surface features with a suppressed
# shape (a named company + an incidental-sounding relationship, a
# founder mentioned by name, a real dollar figure).
# ============================================================


def test_genuine_labor_action_by_a_tracked_company_is_still_admitted():
    decision = _assess(
        "Samsung Electronics Workers Vote to Authorize Strike Over Wage Dispute",
        "Samsung Electronics' largest labor union voted to authorize a strike after wage negotiations with management broke down this week.",
    )
    assert decision.admitted is True
    assert decision.reason == "company_subject:Samsung Electronics"


def test_genuine_supply_disruption_is_still_admitted():
    decision = _assess(
        "SK Hynix Warns of HBM Supply Constraints Through Next Year",
        "SK Hynix warned customers that high-bandwidth memory supply will remain constrained through next year amid surging AI demand.",
    )
    assert decision.admitted is True
    assert decision.reason == "company_subject:SK Hynix"


def test_genuine_founder_led_company_announcement_is_still_admitted_via_title_placement():
    """A founder's name appearing alongside a historical-background
    marker must never suppress the company's OWN identity when the
    company is genuinely in the title — the historical-background veto
    only ever blocks the action-language route, never in_title."""
    decision = _assess(
        "Microsoft Announces New AI Data Center Investment in Wisconsin",
        "Microsoft, co-founded by Bill Gates in 1975, announced a new multi-billion-dollar AI data center "
        "campus in Wisconsin as part of its continued AI infrastructure buildout.",
    )
    assert decision.admitted is True
    assert decision.reason == "company_subject:Microsoft Corporation"


def test_genuine_product_launch_with_real_dollar_pricing_is_still_admitted():
    """A genuine corporate pricing action (not a retail deal) must
    still be admitted and, per the materiality suite, still reach
    HIGH_SIGNAL — the consumer-deal-price calibration fix is scoped to
    "save $X"/"$X off"/"X% off" retail-deal phrasing only."""
    decision = _assess(
        "Nvidia Raises GPU Prices by 15% Amid Tariff Pressures",
        "Nvidia confirmed a 15% price increase across its GPU lineup will take effect next quarter, "
        "citing new import tariffs on semiconductor components.",
    )
    assert decision.admitted is True
    assert decision.reason == "company_subject:NVIDIA"
