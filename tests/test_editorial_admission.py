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

from src.data_access.daily_news import editorial_admission
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


def test_gaming_pc_deal_with_specs_and_a_dollar_discount_is_rejected():
    """Shopping/deal/discount + consumer PC specs/model numbers category
    (Signals precision follow-up, design/SIGNALS_PRECISION_FOLLOWUP_
    RETAIL_BOILERPLATE_GUIDANCE_2026_09_16.md, retail/deal/scarcity
    rule) — supersedes this test's own prior "admitted, materiality-
    only fix" design: a $560-off gaming PC deal is now rejected outright
    at admission, since "save ... $560" is itself a curated consumer-
    format pattern (_CONSUMER_FORMAT_PATTERNS) — a named component
    vendor (NVIDIA) being genuinely in the title no longer rescues a
    deal-framed story on its own; the rescue below still requires real,
    non-weak anchor evidence (see test_named_material_aws_contract_...
    below for that positive control), which this pure retail write-up
    has none of."""
    title = "Save 25% ($560) on This Gaming PC Packed With AMD and Nvidia Hardware"
    description = (
        "This gaming PC deal pairs an AMD Ryzen 7 processor with an Nvidia GeForce RTX 4070 graphics "
        "card, marking one of the biggest price cut deals we've seen on this configuration this year."
    )
    decision = _assess(title, description)
    assert decision.admitted is False
    assert decision.reason.startswith("consumer_editorial_format:")


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


# ============================================================
# Dashboard/Signals quality fix (design/
# DASHBOARD_SIGNAL_QUALITY_FIX_DESIGN.md) — consumer-retail/scalper
# purchase-guide false positive (the Asus RTX 5090 anniversary bundle),
# and positive controls proving the new lexicon never suppresses
# genuine enterprise NVIDIA coverage.
# ============================================================


def test_asus_rtx_5090_anniversary_bundle_scalper_story_fails_admission():
    """The exact reported case: a Tom's Hardware consumer purchase-guide
    article about Asus' RTX 5090 anniversary bundle — "cheapest way to
    buy," a $10,850 bundle price, retail scarcity, and scalper-listing
    price comparisons. Pure consumer retail/purchase-guide content, no
    corporate/data-center/supply-chain/earnings/capex/contract/
    enterprise-demand substance anywhere in the text — must fail
    admission entirely, never merely demote to Background."""
    decision = _assess(
        "Asus' Nvidia RTX 5090 20th Anniversary Bundle: Here's the Cheapest Way to Buy One Right Now",
        "Retailers are listing the $10,850 bundle amid retail scarcity, with scalpers relisting units "
        "well above MSRP on resale marketplaces — here's where to buy it while it's still in stock "
        "before it sells out again.",
    )
    assert decision.admitted is False
    assert decision.reason.startswith("consumer_editorial_format:")


def test_semantically_equivalent_consumer_bundle_story_from_a_different_retailer_also_fails_admission():
    """The rule must cover semantically equivalent consumer bundle/
    scalper/retailer content generally, not only the exact literal
    headline/URL of the reported fixture — a different retailer, a
    different consumer GPU model, and different phrasing of the same
    underlying shape."""
    decision = _assess(
        "Where to Buy the Nvidia RTX 5080 Launch Bundle Before It's Sold Out",
        "Best Buy and other retailers have the Nvidia RTX 5080 bundle back in stock for a limited time — "
        "add it to your cart now before scalpers snap up the remaining units.",
    )
    assert decision.admitted is False
    assert decision.reason.startswith("consumer_editorial_format:")


def test_genuine_nvidia_data_center_capacity_and_supply_story_is_admitted():
    """Legitimate enterprise GPU capacity/supplier-allocation coverage —
    named in the design doc's explicit "must not suppress" list — must
    still be admitted, even though it shares the taxonomy keyword
    surface (NVIDIA, GPU) with the rejected consumer fixtures above."""
    decision = _assess(
        "NVIDIA Expands Data Center GPU Capacity Through New Foundry Supply Agreement",
        "NVIDIA announced an expanded supply agreement with a major foundry partner to increase "
        "data center GPU capacity amid surging enterprise AI infrastructure demand.",
    )
    assert decision.admitted is True
    assert decision.reason == "company_subject:NVIDIA"


def test_genuine_nvidia_earnings_story_is_admitted():
    decision = _assess(
        "NVIDIA Reports Fourth Quarter Financial Results as Data Center Revenue Surges",
        "NVIDIA reported fourth quarter financial results, with data center revenue surging on "
        "strong enterprise AI chip demand.",
    )
    assert decision.admitted is True
    assert decision.reason == "company_subject:NVIDIA"


def test_genuine_nvidia_channel_allocation_story_is_admitted():
    decision = _assess(
        "NVIDIA Allocates Additional GPU Supply to Cloud Providers Amid Enterprise Demand",
        "NVIDIA confirmed it is allocating additional GPU capacity to major cloud providers as "
        "enterprise AI infrastructure demand continues to outpace production.",
    )
    assert decision.admitted is True
    assert decision.reason == "company_subject:NVIDIA"


def test_new_retail_lexicon_phrase_is_still_excused_by_genuine_anchor_evidence():
    """The new retail-purchase-guide phrases route through the exact
    same pre-existing anchor-evidence exception every other consumer-
    format phrase already uses — a genuine earnings story that happens
    to also mention "where to buy" (e.g. describing retail channel
    commentary inside real results coverage) is still admitted, exactly
    like the existing Oracle "% off their prior close" regression."""
    decision = assess_admission(
        "NVIDIA Reports Third-Quarter Financial Results",
        "NVIDIA reported financial results for its fiscal third quarter; retail analysts also "
        "commented on where to buy the latest GPUs amid strong demand.",
        matched_companies=("NVIDIA",), matched_themes=(),
        materiality_reasons=("formal_earnings_materials:financial results",),
    )
    assert decision.admitted is True
    assert decision.reason == "company_subject:NVIDIA"


def test_new_retail_lexicon_phrases_never_include_a_company_gpu_or_price_term_standalone():
    """Narrow safety/consistency pass (design/
    DASHBOARD_SIGNAL_QUALITY_FIX_DESIGN.md follow-up) — a structural,
    code-level guard: the new retail-purchase-guide phrases must never
    themselves be (or contain as a standalone token) "nvidia", "rtx",
    "gpu", "$", "price", or a broad hardware-component name. This locks
    in the design property that the suppression is contextual (retail/
    shopping-action framing) and can never become a company- or
    component-name-based exclusion on its own."""
    forbidden_standalone_terms = (
        "nvidia", "amd", "intel", "rtx", "gpu", "cpu", "$", "price", "pricing",
    )
    new_retail_phrases = (
        "cheapest way to buy", "where to buy", "in stock now", "back in stock",
        "sold out", "add to cart", "scalper", "scalpers", "reseller listing",
        "retail scarcity",
    )
    for phrase in new_retail_phrases:
        assert phrase in editorial_admission._CONSUMER_FORMAT_PHRASES
        lowered = phrase.lower()
        for forbidden in forbidden_standalone_terms:
            assert forbidden not in lowered, (phrase, forbidden)


# ============================================================
# Signals admission precision fix (design/SIGNALS_ADMISSION_
# MATERIALITY_CALIBRATION_2026_09_15.md, design/CURRENT_SIGNALS_
# POLICY_AND_GAP_INVENTORY_2026_09_15.md) — P0 grammatical-agency-aware
# attribution, plaintiff-law-firm solicitation exclusion, personnel/
# leadership-announcement exclusion. Every fixture is a realistic
# reconstruction of the exact named case, run end-to-end via _assess().
# ============================================================

# --- P0: grammatical-agency-aware company attribution (Fixture B — Math Data/AWS) ---


def test_generic_aws_partner_tier_announcement_rejects_with_no_amazon_attribution():
    """The exact reported case: a third-party's own generic AWS
    partner-tier/certification announcement must not attach Amazon —
    "announced"/"achieved" are New Math Data's own actions, not
    Amazon's; "Amazon Web Services"/"AWS" appear only as the certifying
    platform's name, well outside the grammatical-actor proximity
    window."""
    decision = _assess(
        "New Math Data Achieves Premier Tier Status in the Amazon Web Services Partner Network",
        "New Math Data today announced it has achieved Premier Tier Partner status in the Amazon Web "
        "Services (AWS) Partner Network. This designation recognizes New Math Data for its proven "
        "customer success and deep AWS expertise. As an AWS Premier Tier Partner, New Math Data has met "
        "rigorous requirements around technical certifications and customer satisfaction.",
    )
    assert decision.admitted is False
    assert "Amazon" not in decision.reason


def test_named_material_aws_contract_remains_eligible_with_correct_attribution():
    """Positive control: a genuine, named, material AWS/Amazon
    announcement — where Amazon/AWS is grammatically the actor
    ("AWS...signed") — must remain admitted and correctly attached to
    Amazon.com, Inc."""
    decision = _assess(
        "AWS Signs $500 Million Multi-Year Cloud Contract With Acme Logistics",
        "Amazon Web Services today announced it signed a $500 million multi-year contract to provide "
        "cloud infrastructure and capacity for Acme Logistics operations worldwide.",
    )
    assert decision.admitted is True
    assert decision.reason == "company_subject:Amazon.com, Inc."


def test_amazon_leading_subject_with_aws_object_further_in_sentence_still_admits():
    """Regression guard for the fix's own asymmetric lookahead window:
    "Amazon Announces $10B Expansion of AWS Data Center Capacity" — the
    recognized alias span is "AWS" (appearing AFTER "Announces," naming
    what was expanded, not who announced it), but it is close enough
    (within the tighter lookahead) to still correctly credit Amazon as
    the real actor, distinguishing this from the Math Data case above,
    where the AWS mention arrives roughly 50+ characters later."""
    decision = _assess(
        "Amazon Announces $10 Billion Expansion of AWS Data Center Capacity in Virginia",
        "Amazon today announced a $10 billion investment to expand AWS data center capacity, adding new "
        "facilities across Virginia over the next three years.",
    )
    assert decision.admitted is True
    assert decision.reason == "company_subject:Amazon.com, Inc."


# --- P0: plaintiff-law-firm solicitation exclusion (Fixture G — Rosen/ASTS) ---


def test_law_firm_lead_plaintiff_solicitation_rejects():
    """The exact reported case: a Rosen Law Firm / PR Newswire-shaped
    securities-fraud lead-plaintiff solicitation for AST SpaceMobile
    must reject — never a claim that the underlying lawsuit is false or
    that fraud has been established, simply that attorney-advertising
    solicitation content does not qualify for admission on its own."""
    decision = _assess(
        "ROSEN, A LEADING LAW FIRM, Encourages AST SpaceMobile, Inc. Investors to Secure Counsel Before "
        "Important Deadline in Securities Class Action - ASTS",
        "WHY: Rosen Law Firm reminds purchasers of securities of AST SpaceMobile, Inc. (NASDAQ: ASTS) "
        "between November 14, 2023 and April 1, 2024 of the important June 17, 2024 lead plaintiff "
        "deadline in the securities class action. If you wish to serve as lead plaintiff, you must move "
        "the Court no later than June 17, 2024.",
    )
    assert decision.admitted is False
    assert decision.reason.startswith("law_firm_solicitation:")


def test_substantive_independent_legal_reporting_remains_eligible():
    """Positive control: genuine independent reporting on a real,
    material court/regulatory development — no attorney-advertising
    solicitation language present — remains admitted normally."""
    decision = _assess(
        "AST SpaceMobile Discloses $150 Million Settlement in Securities Litigation, Court Filing Shows",
        "According to a regulatory filing, AST SpaceMobile disclosed it has agreed to a $150 million "
        "settlement resolving the pending securities class action, according to court documents filed "
        "this week.",
    )
    assert decision.admitted is True
    assert decision.reason == "company_subject:AST SpaceMobile, Inc."


def test_law_firm_solicitation_phrases_never_contain_a_bare_lawsuit_or_company_term():
    """Structural guard, matching this codebase's own established
    pattern (see test_new_retail_lexicon_phrases_never_include_a_
    company_gpu_or_price_term_standalone above): the law-firm-
    solicitation phrase list must never itself be a bare "lawsuit"/
    "class action"/"securities fraud" ban — every entry names the
    distinctive SOLICITATION language specifically (encourages,
    secure counsel, lead plaintiff, ...), never the mere topic."""
    forbidden_bare_terms = ("lawsuit", "class action", "securities fraud", "litigation")
    for phrase in editorial_admission._LAW_FIRM_SOLICITATION_PHRASES:
        assert phrase.lower() not in forbidden_bare_terms


# --- P0: personnel/leadership/governance-announcement exclusion (Fixture C — DAF) ---


def test_daf_portfolio_executive_announcement_rejects():
    """The exact reported case: a routine DAF/Space Force executive-
    portfolio announcement, with no disclosed contract, budget,
    program award, launch, or operating change, must reject."""
    decision = _assess(
        "DAF establishes space technology portfolio executive",
        "The Department of the Air Force announced the establishment of a new space technology portfolio "
        "executive position within the Space Force, reporting to the Assistant Secretary of the Air "
        "Force. The new portfolio executive will oversee acquisition strategy across multiple space "
        "technology programs.",
    )
    assert decision.admitted is False
    assert decision.reason.startswith("personnel_announcement:")


def test_leadership_change_tied_to_funded_program_remains_eligible():
    """Positive control: a named leadership change directly coupled to
    a disclosed, funded, quantified program/contract remains eligible
    — rescued via the theme-only anchor-evidence exception, since a
    government-agency source (no tracked company) can only ever be
    identified via a matched theme, never a company."""
    decision = _assess(
        "DAF Names New Space Technology Portfolio Executive to Lead $800 Million Satellite Constellation "
        "Contract",
        "The Department of the Air Force named a new space technology portfolio executive to lead an "
        "$800 million satellite constellation contract, overseeing capacity expansion across the "
        "program.",
    )
    assert decision.admitted is True
    assert decision.reason == "theme_subject:space"


def test_routine_personnel_note_with_no_theme_or_company_also_rejects_upstream():
    """A personnel-shaped announcement naming neither a tracked company
    nor a space-theme phrase would already be excluded by the caller's
    own fail-closed pre-check before assess_admission() is ever
    invoked — documented here as the pre-existing, unmodified upstream
    behavior this fix's own personnel-exclusion check is layered on
    top of, not a new gate this fix introduces."""
    companies, themes = matched_companies_and_themes(
        "Executive Portfolio Changes Announced", "A company announced new portfolio executive changes.",
    )
    assert not companies
    assert not themes


# ============================================================
# Daily News Cohort 1 batch (2026-09-15) — SpaceNews and The Robot
# Report are both Lane B (INDEPENDENT_NEWS), so the full assess_
# admission() gate applies to them exactly like every other editorial
# source, unchanged. These fixtures exercise the real, unmodified
# admission code against the two new sources' real content shapes
# (theme-only coverage of a private/untracked company, and coverage
# naming a tracked issuer by name) — no new admission logic is added by
# this batch. See design/DAILY_NEWS_COHORT1_IMPLEMENTATION_DESIGN_
# 2026_09_15.md.
# ============================================================


def test_spacenews_theme_only_item_admitted_without_a_tracked_company():
    """SpaceNews routinely covers private/untracked companies (e.g.
    SpaceX). A theme-only item — no tracked company named — is
    correctly admitted via theme_subject, with zero company attached,
    exactly matching the existing, unmodified theme-only admission path
    every other Lane B source already uses."""
    decision = _assess(
        "SpaceX prepares for next Starship orbital launch as regulators review permit",
        None,
    )
    assert decision.admitted is True
    assert decision.reason == "theme_subject:space"


def test_robot_report_theme_only_item_admitted_without_a_tracked_company():
    """The Robot Report routinely covers private/untracked robotics
    companies (e.g. Agility Robotics — a private-ecosystem entity, not
    a tracked issuer). Same theme-only admission path as SpaceNews
    above."""
    decision = _assess(
        "Agility Robotics unveils new humanoid robot capabilities for warehouse robotics deployment",
        None,
    )
    assert decision.admitted is True
    assert decision.reason == "theme_subject:humanoids"


def test_spacenews_item_naming_firefly_aerospace_admitted_as_company_subject():
    """Cross-lane overlap fixture (design's own §2/§1.3 risk note):
    Firefly Aerospace has its own Lane A newsroom feed AND may be
    independently covered by SpaceNews (Lane B) — this proves the
    shared entity-resolution/admission machinery both lanes' output
    flows through identifies Firefly Aerospace identically regardless
    of which lane the coverage came from, the necessary precondition
    for select_canonical_stories()'s existing cross-source
    canonicalization to correctly treat the two as the same story."""
    decision = _assess(
        "Firefly Aerospace Signs Contract with SSC Space for Two Alpha Launches from Esrange Space Center",
        None,
    )
    assert decision.admitted is True
    assert decision.reason == "company_subject:Firefly Aerospace Inc."


def test_spacenews_item_naming_l3harris_admitted_as_company_subject_without_a_space_theme_match():
    """Cross-lane overlap fixture, paired with the L3Harris non-space-
    tagging proof below: a real L3Harris headline shape (proximity-
    sensor production, not literally a `space`-theme phrase) is
    correctly identified as an L3Harris story via title placement alone
    (L3Harris is not an ambiguous-alias company — see
    _AMBIGUOUS_ALIAS_COMPANIES) — matched_themes is empty here, proving
    company identification never depends on a space-theme keyword
    match."""
    companies, themes = matched_companies_and_themes(
        "L3Harris Technologies Advances Production of Proximity Sensors for US Air Force", None,
    )
    assert companies == ("L3Harris Technologies, Inc.",)
    assert themes == ()  # no `space` theme phrase in this headline — confirmed, not assumed
    decision = _assess(
        "L3Harris Technologies Advances Production of Proximity Sensors for US Air Force", None,
    )
    assert decision.admitted is True
    assert decision.reason == "company_subject:L3Harris Technologies, Inc."


def test_l3harris_non_space_content_gets_no_theme_tag_from_source_or_issuer_association_alone():
    """The exact requirement this design's own scope targets: L3Harris's
    own registry entry carries no `space` theme field (DailyNewsSourceEntry
    has no such field at all — themes are never stored per-source), and
    this codebase's shared matched_companies_and_themes() only ever
    attaches a theme when one of THEME_KEYWORDS' own narrow, approved
    compound phrases is literally present in the item's own text — never
    because the company itself happens to carry a `space` tag in
    tracked_companies.py. A real, non-space L3Harris press release
    (proximity sensors, not launch/satellite/spacecraft language) is
    proof: matched_themes is empty, confirming no "Space Signal" ever
    gets attached to this story purely from L3Harris's own issuer-level
    theme association."""
    _, themes = matched_companies_and_themes(
        "L3Harris Technologies Advances Production of Proximity Sensors for US Air Force", None,
    )
    assert "space" not in themes
    assert themes == ()


def test_robot_report_item_naming_yaskawa_admitted_as_company_subject():
    """Cross-lane overlap fixture: Yaskawa has its own Lane A newsroom
    feed AND may be independently covered by The Robot Report (Lane B)
    — same cross-lane identity-resolution proof as the Firefly/SpaceNews
    fixture above, for the humanoids theme's own overlap pair."""
    decision = _assess(
        "YASKAWA Electric launches new collaborative robot MOTOMAN-HC12",
        None,
    )
    assert decision.admitted is True
    assert decision.reason == "company_subject:YASKAWA Electric Corporation"


# ============================================================
# Signals precision follow-up (design/SIGNALS_PRECISION_FOLLOWUP_RETAIL_
# BOILERPLATE_GUIDANCE_2026_09_16.md) — retail/deal/scarcity rule. Every
# fixture below is the exact live item confirmed in design/POST_MERGE_
# DAILY_NEWS_SIGNAL_PERFORMANCE_AUDIT_2026_09_16.md, run end-to-end via
# _assess() (real matching + materiality + admission).
# ============================================================


def test_retail_deal_bundle_with_ram_ssd_capacity_spec_is_rejected():
    """The exact confirmed live false positive: "Save $1,289..." reached
    HIGH_SIGNAL purely because the article's own RAM/SSD spec language
    ("capacity") survived the deal-price anchor strip — capacity is
    never one of the three pricing-only keywords that strip removes.
    Now rejected at admission via the "save ... $" consumer-format
    pattern; no company is identified here at all (theme-only), so the
    rescue's own identified_companies requirement was never even the
    deciding factor for this specific fixture."""
    decision = _assess(
        "Save $1,289 when you build an extreme PC with these top-tier components — combo deal features "
        "AMD's Ryzen 9 9950X3D2 processor along with an 8TB 9100 Pro SSD, MSI X870E motherboard, and "
        "32GB of DDR5-6000 memory",
        "When money is no object, but you still want to be slightly frugal, combo bundles like today's "
        "Newegg offering might pique your interest. This is the most extreme size for a superfast SSD in "
        "both capacity and price. Right now, you can save $1,289 with this combo deal.",
    )
    assert decision.admitted is False
    assert decision.reason.startswith("consumer_editorial_format:")


def test_retail_deal_bundle_grab_a_saving_with_capacity_spec_is_rejected():
    """Second confirmed live retail-deal false positive, same root
    cause: "Grab a $560 saving..." also survived via a bare "storage
    capacity" spec mention."""
    decision = _assess(
        "Grab a $560 saving on this 1440p-ready gaming PC with a 9800X3D and RTX 5060 Ti 16GB, now $1,859",
        "A pre-built gaming PC with one of AMD's top X3D chips inside is always going to be a formidable "
        "option. That all-white CyberPowerPC machine, down to $1,859.99 thanks to a $560 discount, has a "
        "2TB Gen 4 SSD that gives you good storage capacity for several big game installations.",
    )
    assert decision.admitted is False
    assert decision.reason.startswith("consumer_editorial_format:")


def test_scalper_retail_scarcity_third_party_sellers_is_rejected():
    """The exact confirmed live scalper/scarcity false positive: NVIDIA
    is genuinely title-placed (identified_companies is non-empty), and
    the article's own "orders never being fulfilled" customer-complaint
    sentence satisfied Gate B's quantified_change:orders — a spurious,
    non-corporate anchor that must no longer be able to rescue a
    resale-markup story into admission."""
    decision = _assess(
        "Nvidia's RTX 5090 vanishes from online retail in the US — third-party sellers now demand as "
        "much as $9,500 for Nvidia's fastest GPU",
        "Nvidia's fastest gaming graphics card, the RTX 5090, has been on a tear of price increases over "
        "the past several weeks. Now, you can only find the RTX 5090 from third-party sellers at online "
        "retailers like Newegg and Amazon, commanding anywhere from $6,500 to $9,500 for Team Green's "
        "best GPU. The most recent reviews are a string of one-star reviews about orders never being "
        "fulfilled.",
    )
    assert decision.admitted is False
    assert decision.reason.startswith("consumer_editorial_format:")


def test_genuine_issuer_price_cut_with_deal_adjacent_phrasing_remains_eligible():
    """Positive control: a genuine, attributable, material issuer pricing
    action — AWS is grammatically the actor ("AWS cuts...") and a real,
    non-weak anchor ("investment") independently qualifies via Gate B —
    must remain admitted even though its own phrasing happens to trip
    the same "save ... $" consumer-format pattern the rejected fixtures
    above use. Proves the rescue still works for real issuer-level
    pricing/inventory/channel disclosures, just not for weak (capacity/
    orders) anchors."""
    decision = _assess(
        "AWS Cuts S3 Storage Pricing, Customers Can Now Save $40 Million a Year",
        "AWS today cut its S3 storage pricing as part of a broader capex-funded infrastructure investment, "
        "and customers can now save $40 million annually across their storage footprint.",
    )
    assert decision.admitted is True
    assert decision.reason == "company_subject:Amazon.com, Inc."


# ============================================================
# Hobbyist/developer/modding-content exclusion (live-card audit, design/
# POST_MERGE_SIGNALS_LIVE_CARD_AUDIT_2026_09_16.md). The first fixture
# below is the exact confirmed live false positive; the rest cover the
# required regression matrix.
# ============================================================


def test_rtx_laptop_power_limit_unofficial_tool_is_rejected():
    """The exact confirmed live false positive: a community-built,
    AI-generated GitHub tool ("vibe coded") that unlocks a laptop GPU's
    own power-delivery limit. NVIDIA is genuinely title-placed but does
    nothing — no disclosure, no official release, no corporate action.
    Must reject at admission and never reach Watchlist/High Signal."""
    decision = _assess(
        "Developer vibe codes a tool to let Nvidia RTX 50-series laptop owners crank up their power "
        "limits — can juice RTX 5090 mobile GPU to 225W",
        "Folks with Nvidia-based gaming laptops can now use a new tool called NvpwrControl to unlock "
        "additional performance from their assuredly power-limited mobile GPU. The tool, spotted by "
        "VideoCardz, is available for download on GitHub, and it is labeled as 'experimental'. The "
        "developer is called 'LevinAI', and there are the hallmarks of generative AI all over the GitHub "
        "repository.",
    )
    assert decision.admitted is False
    assert decision.reason.startswith("hobbyist_modding_format:")
    # Rejected — no persisted story will ever exist for this item, but
    # the decision itself must still report zero attributed companies,
    # never NVIDIA, even though NVIDIA was genuinely title-placed and
    # transiently recognized during matching (design/DAILY_NEWS_
    # IDENTIFIED_VS_MATCHED_COMPANIES_DISCOVERY_2026_09_16.md).
    assert decision.identified_companies == ()


def test_general_non_company_overclock_mod_diy_item_is_rejected():
    """General, non-company-specific overclock/mod/DIY consumer item —
    no tracked company at all, correctly rejected regardless (both via
    the ordinary fail-closed precondition and the new exclusion, proving
    the exclusion itself, not merely the precondition, correctly fires
    for this shape)."""
    decision = _assess(
        "Modder overclocks budget graphics card with DIY power tweak, gains major frame rate boost",
        "A hobbyist modder shared a community-built overclocking tool on GitHub that lets budget graphics "
        "card owners unlock higher power limits via a DIY voltage tweak, claiming a major frame rate "
        "boost in benchmarks.",
    )
    assert decision.admitted is False


def test_official_nvidia_driver_security_patch_remains_eligible():
    """Positive control: an official driver/security disclosure, with no
    hobbyist/mod vocabulary at all, remains fully eligible — proves the
    new exclusion is scoped to the confirmed unofficial-modification
    shape, not to any mention of drivers, GPUs, or power."""
    decision = _assess(
        "NVIDIA Releases Emergency Driver Update to Patch Critical GPU Security Vulnerability",
        "NVIDIA today released an official emergency driver update addressing a critical security "
        "vulnerability affecting its RTX GPU lineup, urging all enterprise and consumer customers to "
        "update immediately.",
    )
    assert decision.admitted is True
    assert decision.reason == "company_subject:NVIDIA"
    assert decision.identified_companies == ("NVIDIA",)


def test_hobbyist_modding_phrases_never_contain_a_bare_power_gpu_or_developer_term_standalone():
    """Structural guard, matching this codebase's own established
    pattern (see test_new_retail_lexicon_phrases_never_include_a_
    company_gpu_or_price_term_standalone and test_law_firm_solicitation_
    phrases_never_contain_a_bare_lawsuit_or_company_term above): the
    hobbyist/modding phrase list must never itself be a bare "power,"
    "gpu," "developer," "gaming," or "tool" ban — every entry names the
    distinctive UNOFFICIAL-MODIFICATION shape specifically, never the
    mere topic."""
    forbidden_bare_terms = ("power", "gpu", "developer", "gaming", "tool", "github", "driver")
    for phrase in editorial_admission._HOBBYIST_MODDING_PHRASES:
        assert phrase.lower() not in forbidden_bare_terms


# ============================================================
# AdmissionDecision.identified_companies (design/DAILY_NEWS_IDENTIFIED_
# VS_MATCHED_COMPANIES_DISCOVERY_2026_09_16.md, design/DAILY_NEWS_
# COMPANY_ATTRIBUTION_IMPLEMENTATION_READINESS_2026_09_16.md) — the real,
# vetted per-company subject list (formerly discarded after admission),
# now threaded all the way out of assess_admission(). Alias text is
# deliberately exact — bare "Amazon"/"Meta"/"Generac" are NOT valid
# aliases in the real matcher (company_aliases.py deliberately excludes
# ambiguous single-word brand names); "Amazon Web Services", "Meta
# Platforms", and "Generac Holdings" are the real, tested aliases used
# instead.
# ============================================================


def test_named_two_issuer_supply_agreement_both_companies_are_attributed():
    """Fixture 2 (Generac/AWS-style named supply agreement) — synthetic,
    authored text. Both companies are title-placed genuine actors; the
    admission-vetted list must include both, in match order, never
    collapsed to one issuer."""
    decision = _assess(
        "Generac Holdings signs multi-year supply agreement with Amazon Web Services to provide backup "
        "power systems for data centers",
        "Generac Holdings announced a new multi-year supply agreement under which Generac Holdings will "
        "provide backup power generation systems for Amazon Web Services data center facilities. Amazon "
        "Web Services confirmed the agreement as part of its data center power resiliency buildout.",
    )
    assert decision.admitted is True
    assert decision.identified_companies == ("Amazon.com, Inc.", "Generac Holdings Inc.")


def test_named_multi_company_infrastructure_deployment_both_companies_are_attributed():
    """Fixture 3 (CoreWeave/NVIDIA-style named infrastructure
    deployment) — synthetic, authored text. Both companies are
    title-placed genuine actors in an official technical deployment."""
    decision = _assess(
        "CoreWeave deploys new NVIDIA GB200 cluster to expand AI cloud capacity",
        "CoreWeave announced it has deployed a new cluster of NVIDIA GB200 GPUs, expanding its AI cloud "
        "infrastructure capacity. CoreWeave said the NVIDIA-powered cluster will support enterprise AI "
        "training workloads.",
    )
    assert decision.admitted is True
    assert decision.identified_companies == ("CoreWeave, Inc.", "NVIDIA")


def test_private_startup_incidental_tracked_company_mention_is_not_attributed():
    """Fixture 4 (private third-party/startup story merely mentioning a
    tracked company) — synthetic, authored text, engineered so the story
    admits via a genuine theme match ("humanoids") rather than any
    company subject. Meta Platforms is raw-matched (a passive "including
    headsets from ..." listing — no title placement, no action language)
    but must never be attributed as the subject."""
    decision = _assess(
        "Startup smartARM unveils new humanoid robot arm for home assistance tasks",
        "smartARM, a private robotics startup, unveiled a new humanoid robot arm this week aimed at home "
        "assistance tasks. The arm can be paired over Bluetooth with a variety of third-party VR headsets "
        "for remote teleoperation, including headsets from Meta Platforms and other manufacturers.",
    )
    assert decision.admitted is True
    assert decision.reason == "theme_subject:humanoids"
    assert decision.identified_companies == ()
