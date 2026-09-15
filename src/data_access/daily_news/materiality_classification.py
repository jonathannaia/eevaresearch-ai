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
"""
from __future__ import annotations

import re
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
    "deployments", "order", "orders", "revenue", "guidance",
    "pricing", "price increase", "price cut", "expansion", "investment",
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

# --- Gate D: fact-attribution language — a deterministic proxy for "new
# attributable facts," not true NLP fact extraction (see module
# docstring). ---
_REPORTING_VERB_KEYWORDS: tuple[str, ...] = (
    "confirmed", "disclosed", "revealed", "according to a regulatory filing",
    "obtained documents show", "according to sources", "filed with",
    "reported exclusively",
)

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
) -> tuple[NewsMaterialityTier, tuple[str, ...]]:
    reasons: list[str] = []
    survey_content = _is_survey_or_research_content(text)

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

    anchor_hits = _strip_consumer_pricing_anchors_if_deal_framed(text, _contains_any(text, _MATERIALITY_ANCHOR_KEYWORDS))
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
    taxonomy_pairing_anchor_hits = _strip_consumer_pricing_anchors_if_deal_framed(
        text, _contains_any(text, _TAXONOMY_PAIRING_ANCHOR_KEYWORDS),
    )
    taxonomy_hits = _matched_taxonomy_buckets(text)
    if taxonomy_hits and taxonomy_pairing_anchor_hits and not survey_content:
        reasons.append(f"taxonomy_anchored_consequence:{taxonomy_hits[0]}:{taxonomy_pairing_anchor_hits[0]}")

    if is_independent_news:
        reporting_hit = _contains_any(text, _REPORTING_VERB_KEYWORDS)
        substantive = len((text or "").strip()) >= _SUBSTANTIVE_EXCERPT_MIN_CHARS
        if reporting_hit and substantive and (taxonomy_hits or anchor_hits):
            reasons.append(f"credible_editorial_reporting:{reporting_hit[0]}")

    if reasons:
        return NewsMaterialityTier.HIGH_SIGNAL, tuple(reasons)
    if taxonomy_hits:
        return NewsMaterialityTier.WATCHLIST, (f"on_taxonomy_no_anchor:{taxonomy_hits[0]}",)
    capital_return_mention_hit = _contains_any(text, _CAPITAL_RETURN_MENTION_KEYWORDS)
    if capital_return_mention_hit:
        return NewsMaterialityTier.WATCHLIST, (f"capital_return_mention_no_qualifying_action:{capital_return_mention_hit[0]}",)
    if earnings_hit and not earnings_qualifies:
        return NewsMaterialityTier.WATCHLIST, (f"scheduling_notice_not_yet_substantive:{earnings_hit[0]}",)
    return NewsMaterialityTier.BACKGROUND, ("off_taxonomy_no_anchor",)


def classify_issuer_story(
    headline: str | None, excerpt: str | None, source_class: SourceClass,
) -> tuple[NewsMaterialityTier, tuple[str, ...]]:
    """Issuer-lane classification (daily_news_pipeline.py). SourceClass is
    always OFFICIAL_COMPANY in this pipeline today (see SourceClass's own
    docstring) — REGULATORY_FILING is handled here for forward
    compatibility only, since the type itself already allows it.
    `headline`/`excerpt` accept None for input-robustness (see
    _combined_text) even though both live pipelines already guarantee a
    non-empty title before ever calling this function."""
    text = _combined_text(headline, excerpt)
    return _classify_core(
        text, is_primary_disclosure=(source_class == SourceClass.REGULATORY_FILING),
        is_independent_news=False,
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
    )
