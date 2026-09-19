"""Signals materiality classification (design/DECISIONS.md) — pure,
deterministic, no I/O, no LLM, no opaque ranking score. Classifies an
already-admitted issuer or editorial item into exactly one
NewsMaterialityTier (HIGH_SIGNAL/WATCHLIST/BACKGROUND), reusing the same
word-boundary keyword-matching discipline
src.data_access.daily_news.editorial_matching already established for
this codebase (fixed, curated, narrow phrase tables — never a bare
overloaded single word).

Called only at story-construction time by daily_news_pipeline.py and
editorial_pipeline.py, for a NEW item being discovered right now — never
against an already-persisted story. This module never reclassifies
existing data; NewsStory.materiality_tier/EditorialStory.materiality_tier
stay None (see NewsMaterialityTier's own docstring) for every record
persisted before this module existed, by design, until a separate,
explicitly-approved backfill decision is made.

Deliberately never imports src.models.models — only
src.models.daily_news_models and source_registry.SourceCategory (both
already Daily-News-owned) — same scope-guard discipline as every other
module in this package (see tests/test_daily_news_scope_guard.py).

Calibrated once (design/DECISIONS.md, "Calibrate Signals materiality
rules against real issuer stories") against a 154-record real-issuer-
lane sample — see that commit for the before/after report. Every rule
below reflects that calibration; nothing here was tuned against
synthetic data alone.

--- The materiality gate (approved design, any ONE qualifies) ---

A. Primary disclosure:
   A1. Regulatory filing (SourceClass.REGULATORY_FILING, or editorial
       SourceCategory.OFFICIAL_FILING).
   A2. Actual (not scheduled) formal earnings/results materials — see
       "Earnings vs. scheduling" below.
   A3. Formal government/regulatory action — editorial
       SourceCategory.REGULATOR/REGULATOR_EXCHANGE/EXCHANGE qualifies by
       category alone (these sources' own admission policy already
       restricts them to regulator/exchange press releases); GOVERNMENT_
       POLICY/GOVERNMENT_PROCUREMENT additionally requires a formal-
       action keyword anchor (reusing the same "narrow source, still
       needs a topical anchor" discipline editorial_pipeline.py already
       applies to the NIST source specifically).
   A4. An explicit, material dividend action (initiated, suspended,
       resumed, materially raised, materially cut, or special) — see
       "Capital allocation" below. The action language itself is the
       qualifying signal; no separate dollar figure is required, since
       e.g. suspending a dividend is inherently material regardless of
       whether a specific amount is stated.
B. Quantified change: a materiality-anchor keyword (capacity, backlog,
   shipment, capex, financing, contract, a deploy*/deployment* form,
   order, revenue, guidance, pricing, ...) co-occurring with a real
   number/currency/percentage/unit pattern — suppressed when survey/
   research language is present (see "Survey statistics" below).
B2. Quantified capital return: a new/expanded buyback, share/stock
   repurchase, or tender-offer term co-occurring with a real number —
   same shape as Gate B, kept as its own separate anchor list so a bare
   capital-return mention can never combine with an unrelated taxonomy
   match to bypass the quantification requirement (see "Capital
   allocation" below).
C. Taxonomy-anchored consequence: a relevance-taxonomy phrase match
   (semiconductors & equipment; AI compute & data-center infrastructure;
   memory/networking/power/cooling/packaging; wafers/materials/critical
   minerals/manufacturing capacity; capital allocation/policy/trade
   controls/supply-chain) AND a materiality-anchor keyword, EXCLUDING the
   deploy-family (see "Deployment qualification" below) — same list as
   Gate B otherwise, without requiring the numeric pattern — also
   suppressed when survey/research language is present.
D. Credible editorial reporting: editorial SourceCategory.
   INDEPENDENT_NEWS, a substantive excerpt (not a one-line blurb), and
   fact-attribution language (confirmed/disclosed/revealed/filed/
   obtained documents/according to a regulatory filing/...) — a
   deterministic proxy for "new attributable facts," not true NLP fact
   extraction; documented here as a known approximation, not overclaimed
   precision.

Zero gate hits -> the item can never reach HIGH_SIGNAL (a hard, tested
invariant — see tests/test_daily_news_materiality_classification.py::
test_zero_gate_item_can_never_reach_high_signal). Among zero-gate items,
in priority order: on-taxonomy (Gate C's phrase match fired, but no
anchor) -> WATCHLIST; a capital-return mention with no qualifying action
or magnitude -> WATCHLIST; an earnings-shaped item disqualified only by
scheduling language -> WATCHLIST; otherwise -> BACKGROUND.

--- Deployment qualification (calibration finding) ---
Every deploy-family word (deploy/deploys/deployed/deploying/deployment/
deployments) fully qualifies for Gate B (a real number/currency/percent/
unit must co-occur — "Deploy Up to 2 Gigawatts", "Deploy up to 2.8 GW").
It is deliberately EXCLUDED from Gate C's taxonomy-pairing, because
Gate C never requires a number: generic product-positioning copy
("Cisco Secure AI Factory... Makes AI Easier to Deploy and Secure")
paired too readily with an incidental taxonomy match ("data center")
and nothing else, with zero quantification anywhere in the text. The
AMD/Anthropic and Bloom Energy/Oracle gigawatt-scale partnerships both
still reach HIGH_SIGNAL — via Gate B alone, since both name a real
quantity — while the Cisco item correctly falls to WATCHLIST (on-
taxonomy, no qualifying anchor).

--- Earnings vs. scheduling (calibration finding) ---
The original Gate A2 matched the bare phrase "financial results"
regardless of tense, so an investor-relations save-the-date notice
("AMD to Report Fiscal Second Quarter 2026 Financial Results") was
indistinguishable from an actual disclosure ("AMD Reports Second
Quarter 2026 Financial Results") — in the calibration sample, roughly
37% of all High Signal items were pure scheduling notices. Fixed by
requiring an _EARNINGS_KEYWORDS hit to NOT be accompanied by
_SCHEDULING_KEYWORDS, unless an unambiguous actual-disclosure phrase
(a "reports/reported {Nth} quarter" form, which real IR usage never
applies to a future event) is also present. A disqualified-by-
scheduling earnings item still lands in Watchlist, not Background.

--- Survey statistics (calibration finding) ---
Gate B/C's generic anchor+number / anchor+taxonomy co-occurrence check
does not care WHOSE number it is — in the calibration sample this let
third-party industry-survey statistics quoted inside a company's own
marketing post ("67% believe...", "New Splunk Research Reveals...")
satisfy the gate exactly like a real issuer disclosure. Fixed by
suppressing Gates B and C specifically (never A/A2/A4/B2 — the narrower,
harder-to-fool gates) when survey/research language is detected. A
story containing survey language can still reach High Signal via any of
those narrower gates — e.g. NVIDIA's real $500B financing-platform
announcement is untouched by this, since it triggers via Gate B's
"financing" anchor with zero survey-language present.

--- Capital allocation (calibration finding) ---
The original classifier had no dividend/buyback/repurchase vocabulary
at all, so six real capital-return announcements in the calibration
sample — including a $1B buyback — landed in Background. Fixed with
three distinct paths, deliberately NOT sharing a keyword list with the
general Gate B/C taxonomy-anchor mechanism (a bare "capital allocation"
taxonomy hit pairing with a bare "buyback"/"dividend" mention would
otherwise bypass the quantification requirement entirely):
  - An explicit material dividend ACTION phrase (suspend/resume/raise/
    cut/initiate/special) -> Gate A4, unconditionally (the action itself
    is material, no number needed).
  - A buyback/repurchase/tender-offer TERM co-occurring with a real
    number -> Gate B2 (mirrors Gate B's own anchor+number shape exactly,
    on its own separate, non-taxonomy-paired anchor list).
  - A bare capital-return MENTION with neither a qualifying action nor a
    number (e.g. "declares quarterly dividend", an unquantified buyback
    authorization) -> Watchlist, never Background and never High
    Signal — routine capital-return news is real and worth surfacing,
    just not material enough for the default feed.

--- Low-signal formats (Signals quality pass, 2026-09-18) ---
A HEADLINE that clearly announces a low-signal format — an IR/
conference attendance notice, an award/ranking/recognition, a sports
or venue sponsorship, a careers/CSR/community program, consumer
gaming, or a "report finds"/survey statistic — blocks the WEAK paths
only: Gate C,
Gate D, and the on-taxonomy Watchlist fallback. The hard gates (A, A2,
A4, B, B2 — results, dividend actions, quantified change/deals,
quantified buybacks) are never affected, so e.g. NVIDIA's blog-hosted
$500B financing-platform announcement still reaches High Signal via
Gate B. Two further shapes block the weak paths ONLY when the headline
carries no concrete development (a launch, result, contract, milestone,
process advance, partnership, or a number): presentation/showcase/venue
framing ("keynote", "showcases", "at Hot Chips 2026") and, on the
issuer lane only, generic essay headlines ("[AI Ecosystem] ...",
"Why ...", "How ..."). So "Intel Foundry Details Process Milestones ...
at VLSI Symposium" keeps its normal tier, and a blog-hosted technical
post is never demoted for where it is hosted. Matched against the
headline only, never the excerpt: real
release bodies routinely mention a conference call, a trade-show demo,
or a careers page in boilerplate, and that must never demote them. An
ambiguous item that matches no format stays exactly where it was.

--- Positive recall (issuer lane only, headline only, 2026-09-18) ---
Three narrow additions for material company events the gates above
missed, evaluated only when the item names its issuer and carries no
low-signal format, rumor/negation, or interview framing:
  - A definitive transaction ("to acquire", "completes acquisition of",
    "signs definitive agreement to merge", "to be acquired by") ->
    HIGH_SIGNAL. Hedged language ("in talks", "explores", "may
    acquire", "terminates") and partial/minority stakes never qualify.
  - A commitment/deal term (invest, commit, partnership, agreement,
    contract, order, financing, supply, ...) or an issuer-specific
    government/industrial-policy award (CHIPS Act, government/state
    grant, subsidy, public incentive, ministry or Department of
    Commerce funding) with a headline currency amount at or above that
    currency's floor (100 million for $/€/£; 10 billion for ¥) ->
    HIGH_SIGNAL, unless the headline is CSR/social-impact framed
    (foundation, philanthropy, donation, charity, scholarship,
    community, education, sustainability, relief, humanitarian, ...)
    or survey content. A bare "grant" without public-policy context,
    and vague partnership language without an amount, never qualify.
  - A full/volume/mass production or shipping milestone -> WATCHLIST,
    never HIGH_SIGNAL, and only where the item would otherwise be
    BACKGROUND.
The editorial lane is unchanged: its headlines include market
forecasts ("AI investment to hit $1 trillion") that are not
company-specific.

--- Issuer naming (issuer lane only, Signals quality pass) ---
classify_issuer_story() accepts the assigned issuer's name forms; when
supplied and neither the headline nor the excerpt names the issuer, the
item is BACKGROUND ("issuer_not_named") regardless of any gate — an
official feed occasionally carries a partner's or a third party's
release that is not about the issuer at all.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from src.data_access.daily_news.source_registry import SourceCategory
from src.models.daily_news_models import NewsMaterialityTier, SourceClass

# --- Relevance taxonomy (5 buckets, design/DECISIONS.md) ---
# Same discipline as editorial_matching.THEME_KEYWORDS: exact, approved,
# narrow standalone terms or required multi-word compounds only — never
# a bare overloaded single word. Deliberately its own, separate table
# from THEME_KEYWORDS (that table is curated/approved for a different
# purpose — editorial admission — and this module never modifies it).
# Matched with plural_safe=True (see _boundary_pattern) so e.g. "GPU"
# also matches "GPUs" — a real word boundary is still required on both
# ends, so this can never create a substring match inside an unrelated
# word (calibration finding: "AMD Instinct MI450 Series GPUs" was
# missed because "GPU" alone didn't match its own plural).
TAXONOMY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "semiconductors_and_equipment": (
        "semiconductor", "semiconductors", "wafer fab", "foundry",
        "chip fabrication", "lithography", "EUV", "semiconductor equipment",
        "wafer fabrication equipment", "etch equipment", "deposition equipment",
        "chipmaker",
    ),
    "ai_compute_and_data_center": (
        "AI data center", "AI infrastructure", "AI chip", "AI accelerator",
        "AI cloud", "GPU cluster", "hyperscaler capex", "data center capex",
        "data center", "AI compute", "inference cluster", "training cluster",
        "GPU shortage", "GPU supply", "GPU", "CUDA",
    ),
    "memory_networking_power_cooling_packaging": (
        "HBM", "DRAM", "NAND flash", "NAND chip", "memory chip", "DDR5",
        "high-bandwidth memory", "networking switch", "optical transceiver",
        "co-packaged optics", "advanced packaging", "chiplet",
        "liquid cooling", "data center power", "power delivery",
        "substation capacity", "transformer capacity", "grid interconnect",
    ),
    "wafers_materials_manufacturing_capacity": (
        "wafer supply", "silicon wafer", "critical minerals", "rare earth",
        "photoresist", "specialty gas", "manufacturing capacity",
        "fab capacity", "new fab", "capacity expansion", "wafer capacity",
    ),
    "capital_policy_trade_controls_supply_chain": (
        "export control", "entity list", "CHIPS Act", "trade restriction",
        "tariff", "supply chain constraint", "supply chain disruption",
        "capital allocation", "capex guidance", "financing round",
    ),
    # Space taxonomy route (Signals admission/materiality precision
    # fix, design/SIGNALS_ADMISSION_MATERIALITY_CALIBRATION_2026_09_15.md
    # Rule/P2) — a deliberate, separate bucket from editorial_matching.
    # THEME_KEYWORDS' own `space` theme list (same "own, separate table"
    # discipline this module's docstring already establishes — see
    # TAXONOMY_KEYWORDS' own comment above). Narrow, multi-word compound
    # phrases only — never a bare "space"/"satellite" — so this can
    # never fire on routine personnel/leadership text or broad aerospace
    # commentary on its own; Gate C still requires a co-occurring
    # materiality anchor (contract/order/capacity/financing/expansion/
    # etc. — see _TAXONOMY_PAIRING_ANCHOR_KEYWORDS), so a bare mission/
    # spacecraft mention with no anchor still lands at Watchlist at
    # most, exactly like every other taxonomy bucket.
    "space_missions_and_launch": (
        "satellite launch", "rocket launch", "orbital launch", "launch vehicle",
        "spacecraft", "spacecraft platform", "space station", "satellite constellation",
        "ground segment", "satellite capacity", "launch contract",
        "mission award", "mission selection", "space procurement",
        "launch license", "orbital slot", "payload integration",
    ),
}

# --- Materiality anchors (Gates B & C) — the change-type vocabulary the
# user's own materiality gate names verbatim. Every deploy*/deployment*
# inflection is its own entry (calibration finding: "Deploy" doesn't
# match a "deployment"-only anchor list) rather than a suffix regex, to
# keep every entry a plain, auditable literal phrase like the rest of
# this list. ---
_MATERIALITY_ANCHOR_KEYWORDS: tuple[str, ...] = (
    "capacity", "backlog", "shipment", "shipments", "capex", "financing",
    "contract", "deploy", "deploys", "deployed", "deploying", "deployment",
    "deployments", "order", "orders", "revenue",
    "pricing", "price increase", "price cut", "expansion", "investment",
    # "guidance" is deliberately NOT a plain member of this list —
    # financial-guidance disambiguation (Signals precision follow-up,
    # design/SIGNALS_PRECISION_FOLLOWUP_RETAIL_BOILERPLATE_GUIDANCE_
    # 2026_09_16.md): the word is a genuine homonym (financial-forecast
    # sense vs. terminal/navigation/travel/instructional senses), so it
    # is added back conditionally by _matched_anchor_keywords() below,
    # never via this plain contains-any list.
)

# Consumer-deal calibration fix (design/DECISIONS.md, "Signals editorial
# lane false-positive audit"): "pricing"/"price increase"/"price cut"
# are meant to anchor a CORPORATE pricing action ("the company announced
# a price increase across its GPU lineup"), but the same three phrases
# also appear verbatim in ordinary retail deal write-ups ("marking one
# of the biggest price cuts we've seen on this configuration"), which
# routinely co-occur with a real percentage/dollar figure (Gate B's own
# numeric-magnitude requirement) purely because the deal itself has a
# discount percentage — e.g. "Save 25% ($560) on This Gaming PC" +
# "...the biggest price cuts we've seen..." satisfies Gate B (and, via
# the same three keywords, Gate C) with zero corporate materiality
# anywhere in the text. Narrow, curated retail-deal phrasings only
# (never a bare "off"/"discount"/"%" alone, which would be far too
# broad) — a genuine corporate pricing-action story essentially never
# uses "save $X"/"$X off"/"X% off" phrasing, so this never suppresses
# one. Scoped to exactly these three ambiguous anchor keywords; every
# other anchor (capacity, contract, order, revenue, ...) is unaffected,
# so a real quantified infrastructure/capex/contract event is never
# touched by this filter.
_CONSUMER_DEAL_PRICE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsave\b.{0,20}\$\d", re.IGNORECASE),
    re.compile(r"\$\d[\d,.]*\s*(off|discount)\b", re.IGNORECASE),
    re.compile(r"\d+(\.\d+)?%\s*off\b", re.IGNORECASE),
)
_CONSUMER_PRICING_ANCHOR_KEYWORDS = frozenset({"pricing", "price increase", "price cut"})


def _is_consumer_deal_price_framing(text: str) -> bool:
    return any(pattern.search(text) for pattern in _CONSUMER_DEAL_PRICE_PATTERNS)


def _strip_consumer_pricing_anchors_if_deal_framed(text: str, anchor_hits: tuple[str, ...]) -> tuple[str, ...]:
    """Drops only "pricing"/"price increase"/"price cut" from an already-
    matched anchor_hits tuple, and only when the text itself carries a
    retail-deal price framing — see this module's own "Consumer-deal
    calibration fix" comment above _CONSUMER_DEAL_PRICE_PATTERNS. A no-op
    (returns anchor_hits unchanged) whenever no deal framing is present,
    or whenever none of the three ambiguous keywords are in anchor_hits
    at all — every other anchor keyword always survives unchanged."""
    if not _is_consumer_deal_price_framing(text):
        return anchor_hits
    return tuple(hit for hit in anchor_hits if hit not in _CONSUMER_PRICING_ANCHOR_KEYWORDS)


# --- Financial-guidance disambiguation (Signals admission/materiality
# precision follow-up, design/SIGNALS_PRECISION_FOLLOWUP_RETAIL_
# BOILERPLATE_GUIDANCE_2026_09_16.md) — "guidance" is a genuine homonym:
# its financial-forecast sense ("the company narrowed its full-year
# guidance") is completely unrelated to its operational/instructional
# senses ("terminal guidance" on a weapons system, "navigation
# guidance," "travel guidance," "user guidance"). Never a broad ban on
# the word — it remains a valid anchor by default everywhere; only a
# small, curated set of disqualifying LOCAL modifiers (checked within a
# short lookback, the same proximity-window discipline already
# established for the P0 negation-aware confirmation fix) excludes one
# specific occurrence. Per-occurrence, not whole-text: a document with
# one disqualified mention ("terminal guidance") and a separate,
# genuine "reaffirmed its full-year guidance" elsewhere is correctly
# NOT suppressed by the first — mirrors _has_rumor_or_negated_
# confirmation_language()'s own per-occurrence convention above. ---
_GUIDANCE_DISQUALIFYING_MODIFIERS: tuple[str, ...] = (
    "terminal", "navigation", "travel", "safety", "operational",
    "installation", "setup", "user", "parental", "style",
)
_GUIDANCE_WORD_PATTERN = re.compile(r"\bguidance\b", re.IGNORECASE)
_GUIDANCE_DISQUALIFYING_MODIFIER_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(m) for m in _GUIDANCE_DISQUALIFYING_MODIFIERS) + r")\b", re.IGNORECASE,
)
_GUIDANCE_MODIFIER_LOOKBACK_CHARS = 25


def _has_qualifying_guidance_mention(text: str) -> bool:
    """True when at least one "guidance" occurrence in text is not
    immediately preceded (within _GUIDANCE_MODIFIER_LOOKBACK_CHARS) by
    one of _GUIDANCE_DISQUALIFYING_MODIFIERS."""
    matches = list(_GUIDANCE_WORD_PATTERN.finditer(text))
    if not matches:
        return False
    for match in matches:
        window_start = max(0, match.start() - _GUIDANCE_MODIFIER_LOOKBACK_CHARS)
        preceding = text[window_start:match.start()]
        if not _GUIDANCE_DISQUALIFYING_MODIFIER_PATTERN.search(preceding):
            return True
    return False


def _matched_anchor_keywords(text: str, keyword_pool: tuple[str, ...]) -> tuple[str, ...]:
    """keyword_pool's own contains-any check, plus "guidance" specifically
    — added back only when _has_qualifying_guidance_mention(text) is
    True (see that function's own docstring), since "guidance" is
    deliberately excluded from both _MATERIALITY_ANCHOR_KEYWORDS and its
    deploy-family-excluded sibling _TAXONOMY_PAIRING_ANCHOR_KEYWORDS.
    Every caller that previously called _contains_any(text_or_window,
    one of those two pools) directly now calls this function instead."""
    hits = _contains_any(text, keyword_pool)
    if _has_qualifying_guidance_mention(text):
        hits = hits + ("guidance",)
    return hits

# The deploy/deploys/deployed/deploying/deployment/deployments family,
# on its own, is excluded from Gate C's taxonomy-pairing (see
# _TAXONOMY_PAIRING_ANCHOR_KEYWORDS below and "Deployment qualification"
# in the module docstring) — calibration finding: generic product-
# positioning copy ("Makes AI Easier to Deploy and Secure") pairs too
# readily with an incidental taxonomy match (e.g. "data center") with no
# quantification anywhere in the text. Deploy-family anchors still fully
# qualify via Gate B, which already requires a real co-occurring number —
# a quantified deployment (capacity/power/systems/locations/investment)
# is exactly what Gate B is for.
_DEPLOY_FAMILY_KEYWORDS = frozenset({
    "deploy", "deploys", "deployed", "deploying", "deployment", "deployments",
})
_TAXONOMY_PAIRING_ANCHOR_KEYWORDS: tuple[str, ...] = tuple(
    k for k in _MATERIALITY_ANCHOR_KEYWORDS if k not in _DEPLOY_FAMILY_KEYWORDS
)

# --- Numeric/currency/unit patterns (Gate B/B2 only — an anchor alone is
# never enough; a real magnitude must co-occur). Deliberately never
# matches a bare date/quarter/year: every pattern requires a currency
# symbol or a named unit (billion/million/%/GW/MW/wafers/chips/...), so
# "Q3 Fiscal Year 2026" or "August 4, 2026" can never satisfy this on
# its own (calibration-verified — see the scheduling-notice fixtures). ---
_NUMERIC_MAGNITUDE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\$\s?\d[\d,.]*\s?(billion|million|bn|mn|b|m)\b", re.IGNORECASE),
    re.compile(r"\d+(\.\d+)?\s?(GW|MW|gigawatts?|megawatts?)\b", re.IGNORECASE),
    re.compile(r"\d+(\.\d+)?\s?%"),
    re.compile(r"\d[\d,]*\s?(thousand|million|billion)\s?(units|wafers|chips|GPUs?|servers)\b", re.IGNORECASE),
)

# --- Gate A2: formal earnings/results materials (content-based, source-
# category-independent) — see "Earnings vs. scheduling" in the module
# docstring for why this is now paired with _SCHEDULING_KEYWORDS and
# _UNAMBIGUOUS_ACTUAL_RESULT_KEYWORDS below rather than used alone. ---
_EARNINGS_KEYWORDS: tuple[str, ...] = (
    "financial results", "quarterly results", "reports fourth quarter",
    "reports third quarter", "reports second quarter", "reports first quarter",
    "full year results", "annual results", "earnings release",
    "fiscal year results", "reports quarterly", "earnings call",
)

# A future-tense/logistics signal — its presence means _EARNINGS_KEYWORDS
# alone is not enough (see _classify_core). "webcast schedule"/"earnings
# release and/& webcast schedule" (calibration finding): a "Q2 2026
# Earnings Release & Webcast Schedule" notice is itself a logistics
# announcement (when/how to tune into the real release, not the release
# itself) despite literally containing the phrase "earnings release."
_SCHEDULING_KEYWORDS: tuple[str, ...] = (
    "to report", "to announce", "will report", "will announce",
    "schedules conference call", "conference call to review", "results on",
    "webcast schedule", "earnings release and webcast schedule",
    "earnings release & webcast schedule",
)

# Real IR usage never applies "reports"/"reported {Nth} quarter" to a
# future event — an unambiguous actual-disclosure signal strong enough
# to override a coincidental _SCHEDULING_KEYWORDS match in the same text
# (e.g. a results release that also happens to mention a follow-up call).
_UNAMBIGUOUS_ACTUAL_RESULT_KEYWORDS: tuple[str, ...] = (
    "reports fourth quarter", "reports third quarter", "reports second quarter",
    "reports first quarter", "reported fourth quarter", "reported third quarter",
    "reported second quarter", "reported first quarter",
)

# --- Gate A3: formal government/regulatory action anchor — required only
# for GOVERNMENT_POLICY/GOVERNMENT_PROCUREMENT sources (same "narrow
# source, still needs a topical anchor" discipline editorial_pipeline.py
# already applies to the NIST source specifically). ---
_FORMAL_ACTION_KEYWORDS: tuple[str, ...] = (
    "rule", "ruling", "order", "sanction", "restriction", "ban", "approve",
    "approval", "license", "revoke", "enforcement", "investigation",
    "settlement", "export control", "entity list", "tariff",
    "rate decision", "policy statement", "final rule", "proposed rule",
)

# --- Gate A4: explicit, material dividend actions — the action language
# itself is material; no dollar figure required (see "Capital
# allocation" in the module docstring). Deliberately excludes routine
# "declares quarterly dividend"/"quarterly cash dividend" phrasing.
# Proximity regexes (verb ... up to 25 chars ... dividend, or the
# reverse order), not fixed literal phrases — real headlines routinely
# wedge a word between the two ("Suspends Quarterly Dividend",
# "Dividend Cut Announced"), which a rigid 2-word phrase would miss;
# still narrow (both the action word and "dividend" itself, a real word
# boundary on each, bounded proximity) — never a bare "dividend" alone. ---
_DIVIDEND_MATERIAL_ACTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "dividend_initiated": re.compile(r"\b(initiates?|initiated|declares?\s+(an?\s+)?initial)\b.{0,25}\bdividends?\b", re.IGNORECASE),
    "dividend_suspended": re.compile(r"\b(suspends?|suspended|suspending)\b.{0,25}\bdividends?\b", re.IGNORECASE),
    "dividend_resumed": re.compile(r"\b(resumes?|resumed|reinstates?|reinstated)\b.{0,25}\bdividends?\b", re.IGNORECASE),
    "dividend_raised": re.compile(r"\b(raises?|raised|increases?|increased|boosts?|boosted)\b.{0,25}\bdividends?\b", re.IGNORECASE),
    "dividend_increase_noun": re.compile(r"\bdividends?\b.{0,15}\b(increase|hike)\b", re.IGNORECASE),
    "dividend_cut": re.compile(r"\b(cuts?|cut|reduces?|reduced|slashes?|slashed)\b.{0,25}\bdividends?\b", re.IGNORECASE),
    "dividend_cut_noun": re.compile(r"\bdividends?\b.{0,15}\b(cut|reduction)\b", re.IGNORECASE),
    "special_dividend": re.compile(r"\bspecial\b.{0,10}\bdividends?\b", re.IGNORECASE),
}

# --- Gate B2: capital-return terms that require a co-occurring number
# (mirrors Gate B exactly) — kept separate from _MATERIALITY_ANCHOR_
# KEYWORDS so these can never pair with an unrelated taxonomy match
# (e.g. "capital allocation") to bypass quantification; see the module
# docstring. ---
_CAPITAL_RETURN_QUANTIFIED_KEYWORDS: tuple[str, ...] = (
    "share repurchase", "stock repurchase", "buyback", "tender offer",
)

# A bare capital-return mention with no qualifying action/magnitude —
# the Watchlist-not-Background-not-High-Signal fallback (see module
# docstring).
_CAPITAL_RETURN_MENTION_KEYWORDS: tuple[str, ...] = (
    "dividend", "repurchase", "buyback", "tender offer",
)

# --- Survey/research-statistics guard — suppresses Gates B and C only
# (see "Survey statistics" in the module docstring). Narrow, curated
# phrases plus one regex for the "NN% believe/predict/..." shape real
# survey write-ups use, since the percentage itself is what would
# otherwise satisfy Gate B. ---
_SURVEY_RESEARCH_KEYWORDS: tuple[str, ...] = (
    "survey", "leaders believe", "leaders predict", "respondents",
    "research reveals", "report finds", "according to research",
    "industry estimate", "new research",
)
_SURVEY_PERCENTAGE_PATTERN = re.compile(r"\d+%\s*(believe|predict|expect|say|plan to|report that)", re.IGNORECASE)

# --- Low-signal formats (see "Low-signal formats" in the module
# docstring). Headline-only, weak-path-only. Each pattern was taken from
# a real item in the 154-record issuer-lane sample; broad single words
# that also carry a material sense ("award" as in "awarded a contract",
# "racing" as in "racing to build") are deliberately absent. ---
_LOW_SIGNAL_HEADLINE_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    # Pure attendance/IR-calendar notices — there is never a concrete
    # development inside "to present at ..." or "... with the financial
    # community", so these demote unconditionally.
    "event_appearance": (
        re.compile(r"\bto (?:present|participate|speak) at\b", re.IGNORECASE),
        re.compile(r"\bparticipate in\b[^.]{0,60}\b(?:conferences?|events?|summit)\b", re.IGNORECASE),
        re.compile(r"\b(?:investor|financial) (?:conferences?|community)\b", re.IGNORECASE),
        re.compile(r"\b(?:webinar|livestream|fireside chat)\b", re.IGNORECASE),
    ),
    "award_or_recognition": (
        re.compile(r"\bmagic quadrant\b", re.IGNORECASE),
        re.compile(r"\bbest of show\b", re.IGNORECASE),
        re.compile(r"\bwins?\b[^.]{0,60}\bawards?\b", re.IGNORECASE),
        re.compile(r"\bnamed\b[^.]{0,80}\b(?:list|leader|best|top|most|lighthouse|winner)\b", re.IGNORECASE),
        re.compile(r"\b(?:tops|ranks?|ranked)\b[^.]{0,60}\b(?:list|ranking)\b", re.IGNORECASE),
        re.compile(r"\bmost trustworthy\b", re.IGNORECASE),
        re.compile(r"\bbest (?:ceos?|places to work|employers?|workplaces?)\b", re.IGNORECASE),
        re.compile(r"\bpositioned as a leader\b", re.IGNORECASE),
    ),
    "sponsorship": (
        re.compile(r"\bofficial\b[^.]{0,60}\bpartner\b", re.IGNORECASE),
        re.compile(r"\b(?:golf|championships?|kentucky derby|olympic games|world cup|motorsport|formula (?:1|one))\b", re.IGNORECASE),
    ),
    "careers_or_community": (
        re.compile(r"\bcareers?\b", re.IGNORECASE),
        re.compile(r"\b(?:scholarships?|internships?|apprenticeships?|workforce development)\b", re.IGNORECASE),
        re.compile(r"\b(?:feeding america|food banks?|non-?profits?|charit(?:y|able)|philanthrop\w*|volunteer\w*)\b", re.IGNORECASE),
    ),
    "consumer_gaming": (
        re.compile(r"\bgeforce now\b", re.IGNORECASE),
        re.compile(r"\bgamers?\b", re.IGNORECASE),
        re.compile(r"\b(?:pc|new|blockbuster|aaa|video) games?\b", re.IGNORECASE),
        re.compile(r"\bgames? coming to\b", re.IGNORECASE),
        re.compile(r"\b(?:gaming bundle|gamer days|gamescom)\b", re.IGNORECASE),
    ),
    "survey_or_report": (
        re.compile(r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine) in (?:10|ten)\b", re.IGNORECASE),
    ),
}

# Presentation/showcase/venue framing — demotes ONLY when the headline
# carries no concrete development (see _has_concrete_development): a
# process milestone, launch, contract, or result that merely happens to
# be announced at a conference keeps its normal classification.
_PRESENTATION_OR_VENUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bkeynote\b", re.IGNORECASE),
    re.compile(r"\bshowcas(?:e|es|ing)\b", re.IGNORECASE),
    # Capitalized event name + year ("at AI Infra Summit 2026", "at Hot
    # Chips 2026") or an event-type noun ("at VLSI Symposium");
    # case-sensitive on purpose.
    re.compile(r"\bat (?:the )?(?:[A-Z][\w&'.-]*\s+){1,4}20\d\d\b"),
    re.compile(r"\bat (?:the )?(?:[A-Z][\w&'.-]*\s+){0,4}(?:Summit|Symposium|Conference|Expo|Congress|Forum)\b"),
    re.compile(r"^[A-Z][\w&'.-]*(?:\s+[A-Z][\w&'.-]*){0,3}\s+20\d\d:"),
)

# Issuer lane only: generic thought-leadership/essay headline shapes.
# Never applied to the editorial lane, where "Why ..."/"How ..." headlines
# are ordinary analysis journalism. Hosting (e.g. a blogs.* domain) is
# deliberately NOT a signal — companies publish real technical and
# product news on their blogs. Demotes only without a concrete
# development, like the presentation/venue patterns above.
_ISSUER_ESSAY_HEADLINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\[[^\]]{2,60}\]"),  # "[AI Ecosystem] ...", "[AI Infrastructure Insight] ..."
    re.compile(r"^(?:Why|How|What)\b"),
    re.compile(r"\bthe future of\b", re.IGNORECASE),
)

# Concrete-development markers: launches/availability, results and
# records, contracts/orders/deals, milestones and process/yield advances,
# partnerships, acquisitions, production. Presentation verbs (outlines,
# discusses, showcases, highlights, details) are deliberately absent —
# they describe the talk, not a development.
_CONCRETE_DEVELOPMENT_PATTERN = re.compile(
    r"\b(?:launch(?:es|ed|ing)?|introduc(?:es|ed|ing)|unveil(?:s|ed|ing)?|releas(?:es|ed|ing)|announc(?:es|ed|ing)|"
    r"availab(?:le|ility)|ships?|shipped|shipping|(?:in|mass|volume|full) production|milestones?|breakthroughs?|"
    r"achiev(?:es|ed)|records?|first|contracts?|orders?|agreements?|acqui\w+|partners?|partnerships?|"
    r"collaborat\w+|results|process(?:es)?|nodes?|yields?|tape[- ]?outs?|deliver(?:s|ed|ing)?|expands?|"
    r"invest(?:s|ed|ment)?|deploy\w*|adopts?)\b",
    re.IGNORECASE,
)


def _has_concrete_development(headline: str) -> bool:
    return bool(_CONCRETE_DEVELOPMENT_PATTERN.search(headline)) or _has_numeric_magnitude(headline)


def _low_signal_format_hit(headline: str, *, issuer_lane: bool) -> str | None:
    """The first matching low-signal format as "<kind>:<matched text>",
    or None. Headline-only by design — see the module docstring."""
    headline = (headline or "").strip()
    if not headline:
        return None
    for kind, patterns in _LOW_SIGNAL_HEADLINE_PATTERNS.items():
        for pattern in patterns:
            match = pattern.search(headline)
            if match:
                return f"{kind}:{match.group(0).strip().lower()}"
    if _contains_any(headline, _SURVEY_RESEARCH_KEYWORDS):
        return f"survey_or_report:{_contains_any(headline, _SURVEY_RESEARCH_KEYWORDS)[0]}"
    concrete = _has_concrete_development(headline)
    if not concrete:
        for pattern in _PRESENTATION_OR_VENUE_PATTERNS:
            match = pattern.search(headline)
            if match:
                return f"event_appearance:{match.group(0).strip().lower()}"
        if issuer_lane:
            for pattern in _ISSUER_ESSAY_HEADLINE_PATTERNS:
                match = pattern.search(headline)
                if match:
                    return f"issuer_essay:{match.group(0).strip().lower()}"
    return None


# --- Positive recall (issuer lane, headline only — see "Positive recall"
# in the module docstring). Never evaluated for issuer_not_named, a
# low-signal format, rumor/negation, or interview framing. ---
_DEFINITIVE_TRANSACTION_PATTERN = re.compile(
    r"\b(?:to acquire|acquires|has acquired|agrees? to acquire|completes? (?:the |its )?acquisition of|"
    r"announces? (?:the |its )?acquisition of|signs? (?:a )?definitive agreement to (?:acquire|merge|be acquired)|"
    r"to merge with|to be acquired by|agrees? to be acquired)\b",
    re.IGNORECASE,
)
# Hedged or non-definitive deal language. Modal verbs are matched only
# directly before a deal verb, so the month "May" never blocks a real
# "Completes Acquisition in May".
_TRANSACTION_HEDGE_PATTERN = re.compile(
    r"\b(?:in talks|explor(?:e|es|ing)|consider(?:s|ing)?|weighs?|plans? to|seeks? to|bids?|offer to|not|"
    r"no longer|terminat\w*|abandon\w*|withdraw\w*|cancel\w*|calls? off|"
    r"(?:may|might|could)\s+(?:acquire|buy|merge|be acquired))\b",
    re.IGNORECASE,
)
# A partial or minority holding is not a definitive acquisition.
_PARTIAL_STAKE_PATTERN = re.compile(r"\b(?:stakes?|minority interest|equity interest)\b", re.IGNORECASE)
_COMMITMENT_TERM_PATTERN = re.compile(
    r"\b(?:invest(?:s|ed|ing|ment|ments)?|commit(?:s|ted|ment|ments)?|partnership|agreement|deal|contract|"
    r"orders?|financ(?:e|es|ing)|fund(?:s|ed|ing)?|supply)\b",
    re.IGNORECASE,
)
# Issuer-specific government/industrial-policy awards qualify as a
# commitment term. A bare "grant" does not — only one with explicit
# public-policy context (CHIPS Act, a government/federal/state grant, a
# subsidy, a public incentive, ministry or Department of Commerce funding).
_POLICY_AWARD_PATTERN = re.compile(
    r"\b(?:CHIPS(?: and Science)? Act|(?:government|federal|state|national|public) (?:grants?|funding|awards?)|"
    r"subsid(?:y|ies)|(?:government|federal|state|public|tax) incentives?|incentive package|ministry|"
    r"department of commerce|commerce department)\b",
    re.IGNORECASE,
)
# CSR/social-impact framing never qualifies as a quantified commitment —
# checked AFTER a policy award is recognised, so charitable language
# always wins (a "CHIPS Act workforce development grant" stays excluded).
_CSR_IMPACT_PATTERN = re.compile(
    r"\b(?:foundation|philanthrop\w*|donat(?:e|es|ed|ing|ion|ions)|charit(?:y|ies|able)|non-?profits?|"
    r"scholarships?|community|communities|education(?:al)?|workforce development|sustainability|"
    r"social[- ]impact|relief|humanitarian)\b",
    re.IGNORECASE,
)
_CURRENCY_AMOUNT_PATTERN = re.compile(
    r"(?P<currency>[$£€¥])\s?(?P<number>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s?(?P<unit>billion|million|bn|mn|b|m)?\b",
    re.IGNORECASE,
)
_CURRENCY_UNIT_MULTIPLIERS = {"billion": 1e9, "bn": 1e9, "b": 1e9, "million": 1e6, "mn": 1e6, "m": 1e6}
# Per-currency floors, in that currency's own units: 100 million for
# $/€/£; 10 billion for ¥ (roughly the same order of magnitude — ¥100
# million is under $1 million). A currency not listed here never counts.
_COMMITMENT_AMOUNT_FLOORS: dict[str, float] = {"$": 1e8, "€": 1e8, "£": 1e8, "¥": 1e10}
_PRODUCTION_MILESTONE_PATTERN = re.compile(
    r"\b(?:(?:in|enters?|begins?|starts?|reach(?:es)?)\s+(?:full|volume|mass)\s+production|"
    r"(?:now|begins?|starts?) shipping)\b",
    re.IGNORECASE,
)


def _headline_currency_amount(headline: str) -> str | None:
    """The first currency amount in the headline that meets its own
    currency's floor (_COMMITMENT_AMOUNT_FLOORS), written with a unit word
    or as a full comma-grouped figure, or None. A magnitude check, not a
    valuation — no exchange-rate conversion."""
    for match in _CURRENCY_AMOUNT_PATTERN.finditer(headline):
        floor = _COMMITMENT_AMOUNT_FLOORS.get(match.group("currency"))
        if floor is None:
            continue
        value = float(match.group("number").replace(",", ""))
        value *= _CURRENCY_UNIT_MULTIPLIERS.get((match.group("unit") or "").lower(), 1.0)
        if value >= floor:
            return match.group(0).strip()
    return None


def _definitive_transaction_hit(headline: str) -> str | None:
    match = _DEFINITIVE_TRANSACTION_PATTERN.search(headline)
    if not match or _TRANSACTION_HEDGE_PATTERN.search(headline) or _PARTIAL_STAKE_PATTERN.search(headline):
        return None
    return match.group(0).lower()


def _quantified_commitment_hit(headline: str) -> str | None:
    term = _COMMITMENT_TERM_PATTERN.search(headline) or _POLICY_AWARD_PATTERN.search(headline)
    amount = _headline_currency_amount(headline)
    if not term or not amount or _CSR_IMPACT_PATTERN.search(headline):
        return None
    return f"{term.group(0).lower()}:{amount}"


def _production_milestone_hit(headline: str) -> str | None:
    match = _PRODUCTION_MILESTONE_PATTERN.search(headline)
    return match.group(0).lower() if match else None


# --- Issuer naming (see "Issuer naming" in the module docstring). A
# deliberately lenient name check — it only has to recognise the
# issuer's own releases in its own feed, so it accepts the full name,
# the legal-suffix-stripped name, a distinctive first word, and an
# alphabetic ticker (case-sensitive, so "AMD" never matches "amd" in
# prose). ---
_ISSUER_LEGAL_SUFFIX_RE = re.compile(
    r"(?:,?\s+(?:Inc\.?|Incorporated|Corp\.?|Corporation|Co\.?,?\s*Ltd\.?|Co\.?|Ltd\.?|Limited|plc|PLC|N\.?V\.?|"
    r"Holdings|Group|Company))+$",
)
_GENERIC_FIRST_WORDS = frozenset({
    "advanced", "applied", "american", "general", "global", "international", "national", "new",
    "united", "first", "micro", "texas", "the",
})


@dataclass(frozen=True)
class IssuerNameForms:
    names: tuple[str, ...]  # matched case-insensitively, whole words
    symbols: tuple[str, ...] = ()  # matched case-sensitively, whole words


def issuer_name_forms(company_name: str, ticker: str | None = None) -> IssuerNameForms:
    name = (company_name or "").strip()
    forms: list[str] = [name] if name else []
    stripped = _ISSUER_LEGAL_SUFFIX_RE.sub("", name).strip()
    if stripped and stripped not in forms:
        forms.append(stripped)
    first_word = stripped.split()[0] if stripped else ""
    if len(first_word) >= 3 and first_word.lower() not in _GENERIC_FIRST_WORDS and first_word not in forms:
        forms.append(first_word)
    symbols = (ticker,) if ticker and ticker.isalpha() and len(ticker) >= 2 else ()
    return IssuerNameForms(names=tuple(forms), symbols=symbols)


def _issuer_is_named(text: str, forms: IssuerNameForms) -> bool:
    if any(_boundary_pattern(name).search(text) for name in forms.names):
        return True
    return any(re.search(r"\b" + re.escape(symbol) + r"\b", text) for symbol in forms.symbols)

# --- Gate D: fact-attribution language — a deterministic proxy for "new
# attributable facts," not true NLP fact extraction (see module
# docstring). ---
_REPORTING_VERB_KEYWORDS: tuple[str, ...] = (
    "confirmed", "disclosed", "revealed", "according to a regulatory filing",
    "obtained documents show", "according to sources", "filed with",
    "reported exclusively",
)

# --- Negation-aware confirmation / rumor-hedge guard (Signals admission/
# materiality precision fix, design/SIGNALS_ADMISSION_MATERIALITY_
# CALIBRATION_2026_09_15.md, P0) — suppresses Gates C and D only, the
# same two "no number required" gates the existing survey-statistics
# guard above already suppresses, via the identical mechanism (a
# suppression flag checked alongside survey_content, never a rewrite of
# either gate's own qualifying condition). Two independent sub-checks:
#
# 1. Rumor/hedge vocabulary — a curated, narrow phrase list naming the
#    real, distinctive vocabulary unconfirmed/tipster-sourced reporting
#    uses (never a bare "unconfirmed"/"rumor" substring search alone;
#    every entry here is checked with the same word-boundary discipline
#    as every other phrase table in this module).
# 2. Negated-confirmation proximity — "confirmed"/"confirms"/"confirm"
#    preceded, within a bounded word window, by a negation marker
#    ("not", "cannot", "has not", "denied", etc.) — the literal defect
#    this fix targets: "has not officially confirmed" previously
#    satisfied the exact same _REPORTING_VERB_KEYWORDS "confirmed" hit
#    as a genuine confirmation, with no negation awareness at all.
#
# Explicit override, mirroring the existing "Earnings vs. scheduling"
# _UNAMBIGUOUS_ACTUAL_RESULT_KEYWORDS pattern exactly: an unambiguous,
# non-negated strong-confirmation phrase ("officially confirmed",
# "the company confirmed", "confirmed in a statement/filing") is
# NEVER suppressed by rumor vocabulary appearing elsewhere in the same
# text (e.g. a piece that both recaps earlier speculation AND reports
# the company's own subsequent confirmation) — a genuine confirmation
# must remain eligible regardless of what led up to it. This override
# checks only for the ABSENCE of a negation marker immediately before
# the confirmation phrase; it never re-enables Gate C/D on rumor
# vocabulary alone.
_RUMOR_HEDGE_KEYWORDS: tuple[str, ...] = (
    "reportedly", "rumored", "rumor", "rumour", "said to", "purportedly",
    "allegedly", "according to a tipster", "according to tipster",
    "according to leaks", "the leaker", "a leaker", "leaked documents",
    "speculated", "speculation",
)
_CONFIRMATION_WORD_PATTERN = re.compile(r"\bconfirm(?:ed|s|ing)?\b", re.IGNORECASE)
_NEGATION_MARKER_PATTERN = re.compile(
    r"\b(not|never|cannot|can't|hasn't|has\s+not|hadn't|had\s+not|didn't|did\s+not|"
    r"doesn't|does\s+not|won't|will\s+not|unable\s+to|declined\s+to|yet\s+to)\b",
    re.IGNORECASE,
)
_DENIAL_PATTERN = re.compile(r"\bdeni(?:ed|es|al)\b", re.IGNORECASE)
_UNCONFIRMED_PATTERN = re.compile(r"\bunconfirmed\b", re.IGNORECASE)
# How far back (characters) a negation marker is still considered to be
# negating a "confirm(ed/s/ing)" occurrence — bounded, never a whole-
# document scan, so a negation elsewhere in a long article can never
# taint an unrelated, later confirmation.
_NEGATION_LOOKBACK_CHARS = 30


def _has_rumor_or_negated_confirmation_language(text: str) -> bool:
    """True when the text carries rumor/hedge vocabulary, a denial, a
    bare "unconfirmed," or every "confirm(ed/s/ing)" occurrence in the
    text is itself negated within a short preceding window ("has not
    officially confirmed," "cannot confirm," ...).

    Checked per-occurrence, not as a single whole-text override: a
    piece that both recaps earlier rumor-negated language AND separately
    reports a genuine, non-negated confirmation ("...had not confirmed
    the deal as of Monday; the company confirmed the acquisition
    today") is correctly NOT suppressed by this function on the
    confirmation-negation check alone — at least one clean, non-negated
    "confirm" occurrence is enough. Rumor/hedge vocabulary (reportedly,
    tipster, leaker, ...) and an explicit denial/"unconfirmed" are each
    independently sufficient to mark the text uncertain regardless of
    any confirmation wording elsewhere, since those signals describe
    the REPORTING itself, not one specific negatable claim."""
    if _contains_any(text, _RUMOR_HEDGE_KEYWORDS):
        return True
    if _DENIAL_PATTERN.search(text):
        return True
    if _UNCONFIRMED_PATTERN.search(text):
        return True
    confirm_matches = list(_CONFIRMATION_WORD_PATTERN.finditer(text))
    if not confirm_matches:
        return False
    for match in confirm_matches:
        window_start = max(0, match.start() - _NEGATION_LOOKBACK_CHARS)
        preceding = text[window_start:match.start()]
        if not _NEGATION_MARKER_PATTERN.search(preceding):
            return False  # at least one clean, non-negated confirmation
    return True  # every confirm(ed/s/ing) occurrence found was negated


# --- Interview/podcast-format guard (Signals admission/materiality
# precision fix, P1) — suppresses Gate C only (never Gate B, Gate D, or
# any of the A gates): an interview/podcast item may still reach High
# Signal via a genuine quantified disclosure (Gate B) or, for
# independent-news content, its own fact-attribution language (Gate D)
# — this guard exists solely to stop a purely topical, no-new-fact
# taxonomy+anchor co-occurrence (Gate C's own "no number required"
# shape) from qualifying an executive restating industry narrative in
# conversation. Narrow, curated title-shape/format markers only —
# never a bare "interview"/"podcast" ban on admission itself (that
# remains editorial_admission.py's own, separate concern). ---
_INTERVIEW_FORMAT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"^[A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+){0,2},\s+"
        r"(CEO|CTO|CFO|COO|President|Founder|Co-Founder|Chairman|Chairwoman)\s+of\s+",
    ),
    re.compile(r"\bpodcast\b", re.IGNORECASE),
    re.compile(r"\binterview\b", re.IGNORECASE),
    re.compile(r"\bq\s*&\s*a\s+with\b", re.IGNORECASE),
    re.compile(r"\bin\s+conversation\s+with\b", re.IGNORECASE),
)


def _is_interview_or_podcast_format(text: str) -> bool:
    return any(pattern.search(text) for pattern in _INTERVIEW_FORMAT_PATTERNS)


# --- Gate C sentence-locality requirement (Signals admission/
# materiality precision fix, P1 — "first-party launch calibration") —
# a taxonomy phrase and a materiality anchor must co-occur in the same
# sentence, or in one of two immediately adjacent sentences, not merely
# "anywhere in the combined title+excerpt text." Closes the gap where a
# generic, boilerplate AI-infrastructure/capex paragraph elsewhere in a
# first-party release (unrelated to the specific event the piece is
# actually announcing) could pair with an anchor word anywhere else in
# the same piece to manufacture materiality for an unrelated, lower-
# materiality event. Reuses the same "require real proximity, not mere
# co-occurrence anywhere in the document" discipline editorial_
# admission.py's own _company_has_nearby_action_language() already
# established for the identical class of problem on the attribution
# side. The GLOBAL, whole-text taxonomy_hits computation in
# _classify_core is deliberately left unchanged and still drives the
# Watchlist "on_taxonomy_no_anchor" fallback — only Gate C's own
# HIGH_SIGNAL-qualifying condition is tightened to local co-occurrence.
def _sentences(text: str) -> tuple[str, ...]:
    pieces = re.split(r"(?<=[.!?])\s+", text)
    return tuple(p for p in pieces if p.strip())


def _taxonomy_anchor_local_hits(text: str) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    sentences = _sentences(text)
    windows = list(sentences) + [
        f"{sentences[i]} {sentences[i + 1]}" for i in range(len(sentences) - 1)
    ]
    for window in windows:
        window_taxonomy_hits = _matched_taxonomy_buckets(window)
        window_anchor_hits = _strip_consumer_pricing_anchors_if_deal_framed(
            window, _matched_anchor_keywords(window, _TAXONOMY_PAIRING_ANCHOR_KEYWORDS),
        )
        if window_taxonomy_hits and window_anchor_hits:
            return window_taxonomy_hits, window_anchor_hits
    return None


# Below this, an excerpt is treated as a one-line blurb, not substantive
# reporting — a deliberately conservative, documented threshold, not a
# precise measure of journalistic depth.
_SUBSTANTIVE_EXCERPT_MIN_CHARS = 200

_GOVERNMENT_CATEGORY_REQUIRES_ANCHOR = (SourceCategory.GOVERNMENT_POLICY, SourceCategory.GOVERNMENT_PROCUREMENT)
_GOVERNMENT_CATEGORY_QUALIFIES_BY_CATEGORY_ALONE = (
    SourceCategory.REGULATOR, SourceCategory.REGULATOR_EXCHANGE, SourceCategory.EXCHANGE,
)


@lru_cache(maxsize=None)
def _boundary_pattern(phrase: str, plural_safe: bool = False) -> re.Pattern[str]:
    """plural_safe appends an optional trailing "s" INSIDE the closing
    word boundary (\\bphrase s?\\b) — still requires a real, non-word
    boundary immediately after the optional "s", so this only ever
    admits the ordinary plural of the exact phrase (e.g. "GPU"/"GPUs"),
    never a substring match inside an unrelated, longer word (calibration
    finding — see TAXONOMY_KEYWORDS' own comment)."""
    suffix = r"s?" if plural_safe else ""
    return re.compile(r"\b" + re.escape(phrase) + suffix + r"\b", re.IGNORECASE)


def _contains_any(text: str, phrases: tuple[str, ...], *, plural_safe: bool = False) -> tuple[str, ...]:
    return tuple(phrase for phrase in phrases if _boundary_pattern(phrase, plural_safe).search(text))


def _has_numeric_magnitude(text: str) -> bool:
    return any(pattern.search(text) for pattern in _NUMERIC_MAGNITUDE_PATTERNS)


def _matched_taxonomy_buckets(text: str) -> tuple[str, ...]:
    matched: list[str] = []
    for bucket, phrases in TAXONOMY_KEYWORDS.items():
        if _contains_any(text, phrases, plural_safe=True):
            matched.append(bucket)
    return tuple(matched)


def _matched_dividend_actions(text: str) -> tuple[str, ...]:
    return tuple(name for name, pattern in _DIVIDEND_MATERIAL_ACTION_PATTERNS.items() if pattern.search(text))


def _is_survey_or_research_content(text: str) -> bool:
    return bool(_contains_any(text, _SURVEY_RESEARCH_KEYWORDS)) or bool(_SURVEY_PERCENTAGE_PATTERN.search(text))


def _combined_text(title: str | None, description: str | None) -> str:
    """Input robustness: a missing title/excerpt (including a literal
    None passed against this function's own type hint, e.g. by a future
    caller without the current pipelines' upstream `if not entry.title`
    gates) is treated as an empty string, never propagated as None into
    the regex matching below."""
    safe_title = title or ""
    safe_description = description or ""
    return safe_title if not safe_description else f"{safe_title}\n{safe_description}"


def _classify_core(
    text: str, *, is_primary_disclosure: bool, is_independent_news: bool,
    headline: str = "", issuer_lane: bool = False,
) -> tuple[NewsMaterialityTier, tuple[str, ...]]:
    reasons: list[str] = []
    survey_content = _is_survey_or_research_content(text)
    # Weak-path-only block (see "Low-signal formats" in the docstring).
    low_signal_format = _low_signal_format_hit(headline, issuer_lane=issuer_lane)

    if is_primary_disclosure:
        reasons.append("primary_disclosure")

    # Gate A2 — earnings, only when not disqualified by scheduling
    # language (see "Earnings vs. scheduling" in the module docstring).
    earnings_hit = _contains_any(text, _EARNINGS_KEYWORDS)
    scheduling_hit = _contains_any(text, _SCHEDULING_KEYWORDS)
    unambiguous_actual_result = bool(_contains_any(text, _UNAMBIGUOUS_ACTUAL_RESULT_KEYWORDS))
    earnings_qualifies = bool(earnings_hit) and (not scheduling_hit or unambiguous_actual_result)
    if earnings_qualifies:
        reasons.append(f"formal_earnings_materials:{earnings_hit[0]}")

    # Gate A4 — explicit material dividend action, unconditional.
    dividend_action_hit = _matched_dividend_actions(text)
    if dividend_action_hit:
        reasons.append(f"material_dividend_action:{dividend_action_hit[0]}")

    anchor_hits = _strip_consumer_pricing_anchors_if_deal_framed(text, _matched_anchor_keywords(text, _MATERIALITY_ANCHOR_KEYWORDS))
    numeric_hit = _has_numeric_magnitude(text)
    if anchor_hits and numeric_hit and not survey_content:
        reasons.append(f"quantified_change:{anchor_hits[0]}")

    # Gate B2 — quantified capital return (buyback/repurchase/tender
    # offer + a real number), independent of survey suppression (these
    # terms are specific enough not to appear in survey write-ups).
    capital_return_quantified_hit = _contains_any(text, _CAPITAL_RETURN_QUANTIFIED_KEYWORDS)
    if capital_return_quantified_hit and numeric_hit:
        reasons.append(f"quantified_capital_return:{capital_return_quantified_hit[0]}")

    # Gate C uses the deploy-family-excluded anchor pool — see
    # _TAXONOMY_PAIRING_ANCHOR_KEYWORDS' own comment. A deploy-family
    # anchor still reaches High Signal here indirectly whenever it also
    # satisfies Gate B above (a real number is present).
    #
    # Signals admission/materiality precision fix (P0/P1): Gate C's own
    # qualifying condition now requires (a) the taxonomy phrase and
    # anchor to co-occur locally (same sentence or one of two adjacent
    # sentences — see _taxonomy_anchor_local_hits' own comment), (b) no
    # rumor/negated-confirmation language, and (c) no interview/podcast-
    # format marker. The GLOBAL, whole-text taxonomy_hits computation
    # below is deliberately UNCHANGED and still drives the Watchlist
    # "on_taxonomy_no_anchor" fallback further down — only whether Gate
    # C itself qualifies for HIGH_SIGNAL is tightened.
    uncertain_reporting = _has_rumor_or_negated_confirmation_language(text)
    interview_format = _is_interview_or_podcast_format(text)
    taxonomy_hits = _matched_taxonomy_buckets(text)
    local_hit = _taxonomy_anchor_local_hits(text)
    if local_hit and not survey_content and not uncertain_reporting and not interview_format and not low_signal_format:
        local_taxonomy_hits, local_anchor_hits = local_hit
        reasons.append(f"taxonomy_anchored_consequence:{local_taxonomy_hits[0]}:{local_anchor_hits[0]}")

    if is_independent_news:
        reporting_hit = _contains_any(text, _REPORTING_VERB_KEYWORDS)
        substantive = len((text or "").strip()) >= _SUBSTANTIVE_EXCERPT_MIN_CHARS
        if (
            reporting_hit and substantive and (taxonomy_hits or anchor_hits)
            and not uncertain_reporting and not interview_format and not low_signal_format
        ):
            reasons.append(f"credible_editorial_reporting:{reporting_hit[0]}")

    # Positive recall (issuer lane only): appended after every existing
    # gate, so an item that already qualifies keeps its original first
    # reason; never evaluated for a low-signal format, rumor/negation,
    # or interview framing (issuer_not_named returned before this call).
    recall_allowed = issuer_lane and not low_signal_format and not uncertain_reporting and not interview_format
    if recall_allowed:
        transaction_hit = _definitive_transaction_hit(headline)
        if transaction_hit:
            reasons.append(f"definitive_transaction:{transaction_hit}")
        commitment_hit = _quantified_commitment_hit(headline)
        if commitment_hit and not survey_content:
            reasons.append(f"quantified_commitment:{commitment_hit}")

    if reasons:
        return NewsMaterialityTier.HIGH_SIGNAL, tuple(reasons)
    # Denial/rumor fallback fix (Signals precision follow-up, design/
    # SIGNALS_PRECISION_FOLLOWUP_RETAIL_BOILERPLATE_GUIDANCE_2026_09_16.md)
    # — uncertain_reporting (computed above, already gating Gates C/D)
    # now also gates this bare taxonomy-only Watchlist fallback: a
    # denial/rumor-shaped item with no hard-gate evidence at all drops
    # to BACKGROUND instead of surfacing at Watchlist. Never touches a
    # denial that independently clears a hard gate (A/A2/A4/B/B2, all
    # entirely unaffected by uncertain_reporting) — a substantive
    # issuer/regulator/court denial with concrete consequence still
    # reaches HIGH_SIGNAL above, before this fallback is ever reached.
    if taxonomy_hits and not uncertain_reporting and not low_signal_format:
        return NewsMaterialityTier.WATCHLIST, (f"on_taxonomy_no_anchor:{taxonomy_hits[0]}",)
    capital_return_mention_hit = _contains_any(text, _CAPITAL_RETURN_MENTION_KEYWORDS)
    if capital_return_mention_hit:
        return NewsMaterialityTier.WATCHLIST, (f"capital_return_mention_no_qualifying_action:{capital_return_mention_hit[0]}",)
    if earnings_hit and not earnings_qualifies:
        return NewsMaterialityTier.WATCHLIST, (f"scheduling_notice_not_yet_substantive:{earnings_hit[0]}",)
    if recall_allowed:
        production_hit = _production_milestone_hit(headline)
        if production_hit:
            return NewsMaterialityTier.WATCHLIST, (f"production_milestone:{production_hit}",)
    if low_signal_format:
        return NewsMaterialityTier.BACKGROUND, (f"low_signal_format:{low_signal_format}",)
    return NewsMaterialityTier.BACKGROUND, ("off_taxonomy_no_anchor",)


def classify_issuer_story(
    headline: str | None, excerpt: str | None, source_class: SourceClass,
    *, issuer_names: IssuerNameForms | None = None,
) -> tuple[NewsMaterialityTier, tuple[str, ...]]:
    """Issuer-lane classification (daily_news_pipeline.py). SourceClass is
    always OFFICIAL_COMPANY in this pipeline today (see SourceClass's own
    docstring) — REGULATORY_FILING is handled here for forward
    compatibility only, since the type itself already allows it.
    `headline`/`excerpt` accept None for input-robustness (see
    _combined_text) even though both live pipelines already guarantee a
    non-empty title before ever calling this function.

    `issuer_names` is optional (Signals quality pass): when supplied, an
    item that names the issuer in neither its headline nor its excerpt
    is BACKGROUND ("issuer_not_named"). Omitted, that check is off.
    Issuer-lane essay headlines are recognised either way (see "Low-
    signal formats" in the module docstring)."""
    text = _combined_text(headline, excerpt)
    if issuer_names is not None and not _issuer_is_named(text, issuer_names):
        return NewsMaterialityTier.BACKGROUND, ("issuer_not_named",)
    return _classify_core(
        text, is_primary_disclosure=(source_class == SourceClass.REGULATORY_FILING),
        is_independent_news=False, headline=headline or "", issuer_lane=True,
    )


def classify_editorial_story(
    headline: str | None, excerpt: str | None, source_category: SourceCategory,
) -> tuple[NewsMaterialityTier, tuple[str, ...]]:
    """Editorial-lane classification (editorial_pipeline.py). The caller
    already has the item's own DailyNewsSourceEntry in scope at
    construction time, so its real `.category` is passed directly —
    never re-derived from source_feed_id here. `headline`/`excerpt`
    accept None for input-robustness — see classify_issuer_story's own
    docstring."""
    text = _combined_text(headline, excerpt)
    is_primary_disclosure = (
        source_category == SourceCategory.OFFICIAL_FILING
        or source_category in _GOVERNMENT_CATEGORY_QUALIFIES_BY_CATEGORY_ALONE
        or (
            source_category in _GOVERNMENT_CATEGORY_REQUIRES_ANCHOR
            and bool(_contains_any(text, _FORMAL_ACTION_KEYWORDS))
        )
    )
    return _classify_core(
        text, is_primary_disclosure=is_primary_disclosure,
        is_independent_news=(source_category == SourceCategory.INDEPENDENT_NEWS),
        headline=headline or "",
    )
