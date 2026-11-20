"""Editorial Signals admission gate (design/DECISIONS.md, "precision-
first correction" — the Nintendo/Amazon false-positive audit). Pure,
deterministic, no I/O, no LLM, no network, no randomness.

Preserves a strict distinction from materiality_classification.py:
  - ADMISSION (this module): is this item genuinely relevant to Eeva's
    research universe at all? A company- or theme-keyword match alone
    (editorial_matching.matched_companies_and_themes()) is NEVER
    sufficient — see assess_admission()'s own docstring below.
  - TIERING (materiality_classification.py, unchanged): once admitted,
    how material is it — High Signal, Watchlist, or Background?

Root cause this module fixes (design/DECISIONS.md audit): a Nintendo
"Customer Appreciation Sale" article was admitted and persisted, tagged
to Amazon.com, Inc., purely because its description said "available on
Amazon.com" — a retail-channel reference with zero indication anything
Amazon-specific happened. Nintendo itself is not a tracked company; the
story's real subject has no representation in this system at all. Before
this module, editorial_pipeline.py's own fail-closed gate
(`not matched_companies and not matched_themes`) was a pure OR: any one
incidental mention was unconditionally sufficient, and — critically —
that admission decision was entirely independent of materiality tier.
Even though this specific example correctly classified as Background,
Background items are still persisted and permanently discoverable under
that company's own filtered view (editorial_pipeline.
select_visible_editorial_stories_for_company(), no total cap there) —
this module's approved framing: "Background means relevant research
context with low immediate materiality — not unrelated content retained
for audit." Irrelevant items must not be admitted at all, not merely
demoted.

Called once per qualifying candidate, in the editorial pipeline's own
matching loop — see editorial_pipeline.py's own call site for exactly
where. Never applied to the SpaceForce/NIST hardcoded exceptions (an
existing, narrower, separately-approved bypass this module does not
touch or expand).

Entity identity precedes materiality (design/DECISIONS.md, the
"identity-before-materiality" correction). A company's identity as the
genuine SUBJECT of the story is established first, using only
placement/action-language/rival-entity signals — materiality
("anchor_evidence", see _HARD_MATERIAL_REASON_PREFIXES) is NEVER
consulted while deciding identity, and so can never rescue an
ambiguous alias with insufficient disambiguation, a rival-entity
mention, or an incidental description-only mention with no action
language. This closes a real gap found during the alias audit: e.g.
"Oracle of Omaha Raises Stake by 20%" previously admitted, because
"raises" (action language) plus the idiom's title placement was, on its
own, already sufficient — materiality wasn't even needed to cause that
false positive, but a second one was: "CEVA Logistics Reports 25%
Revenue Growth" was admitted purely because the story's own genuine
quantified-revenue language triggered anchor_evidence, which the old
code let bypass the CEVA Inc/CEVA Logistics rival-entity exclusion
outright. Once identity is established this way, anchor_evidence has
exactly one remaining job: letting an already-identified subject's
story through a coincidentally consumer-format-shaped phrase (e.g. a
genuine earnings release whose text happens to also say "shares fell
5% off their high") — never generic quantities like discounts, prices,
rankings, percentages, or product specs standing alone, and never
identity itself.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from src.data_access.daily_news.editorial_matching import company_mention_spans, match_companies
from src.models.daily_news_models import NewsMaterialityTier

# --- Alias confidence (audited across the full 106-company alias
# universe — design/DECISIONS.md). A generated alias (mechanical
# legal-suffix-stripped short form, or a curated brand alias) that
# collides with an ordinary English word, idiom, or extremely common
# casual usage must never, on its own, establish that a story is about
# the company. Listed at the COMPANY level (not the literal alias
# string) — deliberately conservative: even a full-legal-name mention of
# one of these companies gets the same elevated bar, since a real
# corporate story about any of them will trivially also carry company-
# action language anyway (see _COMPANY_ACTION_KEYWORDS), so this costs
# nothing in practice for genuine coverage.
#
#   - "Disco Corporation" -> "Disco": ordinary word (the music genre).
#     Verified false positive: "Best Disco Playlists to Get Your 80s
#     Dance Party Started".
#   - "Oracle Corporation" -> "Oracle": ordinary word/idiom ("the Oracle
#     of Delphi/Omaha"). Verified false positive: a Warren-Buffett
#     "Oracle of Omaha" investing-advice article.
#   - "Alphabet Inc." -> "Alphabet": ordinary word (the 26 letters).
#   - "Coherent Corp" -> "Coherent": ordinary adjective ("a coherent
#     argument").
#   - "Intel Corp." -> "Intel": common slang for "intelligence/
#     information" in casual, military, and gaming writing, independent
#     of its stock-roundup risk (see _STOCK_ROUNDUP_PHRASES below).
#   - "Amazon.com, Inc." -> the mechanical "Amazon.com" alias
#     specifically (note bare "Amazon" is already excluded entirely by
#     company_aliases.py's own deliberate design — this module's
#     addition is narrower: even the *not-bare* "Amazon.com" mechanical
#     form is an extremely common, low-signal retail-channel reference).
#     Verified false positive: the reported Nintendo sale article.
#   - "MKS Inc" -> "MKS": the standard physics/engineering abbreviation
#     for the "meter-kilogram-second" unit system. Verified false
#     positive: a physics-education article about teaching MKS units.
#   - "TOWA Corporation" -> "TOWA": also a common Japanese given name and
#     the handle of a well-known VTuber/streamer, independent of the
#     semiconductor-equipment company. Verified false positive: a
#     streamer-merchandise article.
#   - "CEVA INC" -> "CEVA": collides with CEVA Logistics, an unrelated,
#     real global freight-forwarding company of similar public
#     visibility. Verified false positive: a CEVA Logistics facility
#     announcement mistakenly attributable to CEVA Inc (semiconductor
#     IP).
#   - "Nokia Corp" -> "Nokia": also the name of the Finnish town the
#     company itself is named after. Verified false positive: a local
#     cultural-center renovation story about the town.
_AMBIGUOUS_ALIAS_COMPANIES: frozenset[str] = frozenset({
    "Disco Corporation", "Oracle Corporation", "Alphabet Inc.",
    "Coherent Corp", "Intel Corp.", "Amazon.com, Inc.",
    "MKS Inc", "TOWA Corporation", "CEVA INC", "Nokia Corp",
})

# TOWA and CEVA collide with a *different real, named entity* (not a
# common noun/idiom) that itself issues its own announcement-shaped
# coverage — the generic _COMPANY_ACTION_KEYWORDS check alone cannot
# tell "TOWA Corporation announces X" apart from "the VTuber Towa
# announces new merch," since both are grammatically a company/creator
# "announcing" something. A narrow, curated per-company exclusion
# closes this the same way company_aliases.py's own _BRAND_ALIAS_OVERLAY
# curates exceptions: presence of a rival-entity phrase denies identity
# outright — this is an identity signal, not a materiality one, so
# anchor_evidence (even a genuine formal-earnings/quantified-change
# signal drawn from the rival's own story) never overrides it. A rival
# entity's own earnings release is still a rival entity's story.
_RIVAL_ENTITY_EXCLUSION_PHRASES: dict[str, tuple[str, ...]] = {
    "TOWA Corporation": ("vtuber", "streamer", "livestream", "live stream", "merch"),
    "CEVA INC": ("ceva logistics", "freight forwarder", "freight forwarding", "logistics company"),
    # Not a rival company, but the same class of problem: a fixed idiom
    # referring to Warren Buffett, not Oracle Corporation. "Raises",
    # "cuts", "wins" etc. read as company-action language for either
    # referent, so the idiom itself must be excluded directly.
    "Oracle Corporation": ("oracle of omaha", "oracle of delphi"),
}

# --- Company-action language (requirement: title placement is strong
# but not absolute evidence; a description-only mention needs this kind
# of support). Deliberately broader than materiality_classification.
# py's own narrow, quantification-oriented anchor list — this is about
# "is the company DOING something," not "is it quantified." ---
_COMPANY_ACTION_KEYWORDS: tuple[str, ...] = (
    "announces", "announced", "reports", "reported", "discloses", "disclosed",
    "files", "filed", "launches", "launched", "acquires", "acquired", "acquisition",
    "partners with", "partnership with", "expands", "expanded", "invests", "invested",
    "signs", "signed", "wins", "won", "faces", "sues", "sued", "fined",
    "recalls", "recalled", "discontinues", "discontinued", "raises", "cuts",
    "suspends", "resumes", "unveils", "unveiled", "confirms", "confirmed",
    "warns", "warned", "plans to", "will invest", "to invest", "layoffs",
    "restructuring", "executives", "board of directors", "shareholders",
    "regulators", "lawsuit", "investigation",
)

# --- Consumer/editorial format exclusion. Curated, narrow, literal
# phrases plus a small number of templated regex patterns for the "which
# X should you buy" / "best X for" / "N% off" shapes — this is a v1,
# first-pass list built directly from the verified false positives in
# the audit, not an attempt at a universal classifier. The exception
# clause (see _has_hard_material_evidence below) is what keeps this from
# ever suppressing genuine coverage. ---
_CONSUMER_FORMAT_PHRASES: tuple[str, ...] = (
    "deal of the day", "deals on", "sale on", "coupon code", "promo code",
    "discount code", "clearance sale", "flash sale", "doorbuster",
    "gift guide", "buying guide", "holiday gift", "product roundup",
    "gear roundup", "top picks", "what to watch", "streaming this weekend",
    "movie review", "album review", "red carpet", "customer appreciation sale",
    # Dashboard/Signals quality fix (design/
    # DASHBOARD_SIGNAL_QUALITY_FIX_DESIGN.md) — retail purchase-guide/
    # marketplace/scalper framing, verified false positive: a Tom's
    # Hardware article about a consumer GPU anniversary bundle ("cheapest
    # way to buy," retail scarcity, scalper-listing price comparisons).
    # Every phrase below is purchase-guide/shopping-action language in
    # its own right (never a bare company name, product name, hardware
    # term, or price) — none references NVIDIA/GPU/RTX/a price at all,
    # so this can never single out any one company, product line, or
    # dollar figure; it only ever routes an already-identified subject's
    # story into the SAME anchor-evidence-required exception below,
    # never a direct rejection on its own. A genuine capacity/supply/
    # earnings/capex/contract/first-party-disclosure story is unaffected
    # regardless of whether one of these phrases also appears in it (see
    # _HARD_MATERIAL_REASON_PREFIXES below).
    "cheapest way to buy", "where to buy", "in stock now", "back in stock",
    "sold out", "add to cart", "scalper", "scalpers", "reseller listing",
    "retail scarcity",
)
_CONSUMER_FORMAT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bwhich\b.{0,40}\bshould you buy\b", re.IGNORECASE),
    re.compile(r"\bbest\b.{0,40}\b(gifts?|deals?)\b", re.IGNORECASE),
    re.compile(r"\bbest\b.{0,60}\bunder\s*\$\d", re.IGNORECASE),
    re.compile(r"\d+%\s*off\b", re.IGNORECASE),
    re.compile(r"\bvs\.?\b.{0,40}\b(buy|choose|pick|better|which is right)\b", re.IGNORECASE),
)
_STOCK_ROUNDUP_PHRASES: tuple[str, ...] = (
    "stocks rally", "stock market today", "dow jones today", "biggest gainers",
    "biggest movers", "lead gains", "market rally", "stocks to watch",
)

# The materiality_reasons prefixes that represent a genuine corporate-
# event anchor (Gates A/A2/A4/B/B2 — regulatory filing, actual earnings,
# an explicit dividend action, a quantified change, a quantified capital
# return: i.e. earnings, guidance, acquisition, regulation, capex,
# contract, production change, supply constraint, or a formal company
# announcement) — deliberately excludes Gate C (taxonomy_anchored_
# consequence, no number required) and Gate D (credible_editorial_
# reporting) and the Watchlist-only fallbacks (on_taxonomy_no_anchor,
# capital_return_mention_no_qualifying_action, scheduling_notice_not_
# yet_substantive). This is used for exactly one thing (see
# assess_admission): once a company's identity as the story's subject
# is ALREADY established independently, this anchor can excuse a
# coincidentally consumer-format-shaped phrase elsewhere in the text. It
# is never consulted while establishing identity itself, and a bare
# quantity — a discount, a price, a ranking, a percentage, a product
# spec — is not itself an anchor; these prefixes only fire on
# materiality_classification.py's own corporate-event-shaped gates.
_HARD_MATERIAL_REASON_PREFIXES: tuple[str, ...] = (
    "primary_disclosure", "formal_earnings_materials:", "material_dividend_action:",
    "quantified_change:", "quantified_capital_return:",
)

# --- Plaintiff-law-firm solicitation exclusion (Signals admission
# precision fix, P0). Curated, narrow phrases naming the real,
# distinctive attorney-advertising vocabulary these releases use (the
# real, live-verified Rosen Law Firm / AST SpaceMobile release series
# — "ROSEN ... Encourages AST SpaceMobile, Inc. Investors to Secure
# Counsel Before Important Deadline in Securities Class Action") —
# never a bare "lawsuit"/"class action"/"securities fraud" ban, which
# would also catch genuine independent reporting on a real legal
# action, a company's own primary-source disclosure of litigation (a
# 10-K risk factor, an 8-K legal-proceedings item), or an SEC
# enforcement action — none of which use this self-promotional,
# solicitation-specific phrasing. Same exception shape as the consumer-
# format exclusion above: a genuine, independently-quantified corporate
# event about an already-identified subject is never suppressed by
# this list alone. ---
_LAW_FIRM_SOLICITATION_PHRASES: tuple[str, ...] = (
    "encourages investors to secure counsel", "encourages shareholders to secure counsel",
    "lead plaintiff deadline", "serve as lead plaintiff", "move the court no later than",
    "shareholder rights law firm", "investor rights law firm", "securities law firm",
    "law firm reminds", "if you purchased or otherwise acquired", "join the class action",
    "law offices of", "shareholder alert:", "investor alert:", "national trial lawyers",
    "top ranked law firm", "trusted investor counsel", "skilled investor counsel",
    "investigating claims on behalf of",
)
_LAW_FIRM_SOLICITATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bencourages\b.{0,40}\binvestors\b.{0,40}\bsecure counsel\b", re.IGNORECASE),
    re.compile(r"\bencourages\b.{0,40}\bshareholders\b.{0,40}\bsecure counsel\b", re.IGNORECASE),
)

# --- Personnel/leadership/governance-announcement exclusion (Signals
# admission precision fix, P0). Curated, narrow phrases naming routine
# executive/board/organizational-change content — never a bare
# "executive"/"appoints"/"board" ban (both already appear, deliberately,
# in _COMPANY_ACTION_KEYWORDS above for a DIFFERENT purpose — granting
# subject IDENTITY — and stay unchanged there; this is a separate,
# later, content-SHAPE check that runs only once identity is already
# resolved). Same exception shape as the other two exclusions above: a
# leadership change directly coupled to a disclosed transaction, funded
# program, restructuring, or capital-allocation event remains fully
# eligible via _HARD_MATERIAL_REASON_PREFIXES (e.g. "Names New Program
# Executive Officer for $1.2B Resilient GPS Program" keeps its own
# quantified_change:contract/financing hit). Written to apply generally
# to any Lane B content this function evaluates — including, once a
# separate, future change wires it into editorial_pipeline.py's own
# per-source qualifying loop, content from a source with a broader
# admission bypass (see this module's own top-of-file docstring: that
# wiring is explicitly out of this change's file scope). ---
_PERSONNEL_ANNOUNCEMENT_PHRASES: tuple[str, ...] = (
    "establishes a new executive", "establishes a new position", "establishes a new role",
    "creates a new role", "creates a new position", "new portfolio executive",
    "portfolio executive", "joins the board", "joins as chief", "joins as president",
    "steps down as", "steps down from", "announces retirement of", "names new chief",
    "names new president", "succession plan", "board appointment",
)
_PERSONNEL_ANNOUNCEMENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bappoints\b.{0,40}\b(as|to)\b", re.IGNORECASE),
    re.compile(r"\bnames\b.{0,40}\bas\b.{0,20}\b(chief|president|executive|director|officer)\b", re.IGNORECASE),
    re.compile(r"\bestablishes?\b.{0,40}\b(executive|portfolio|position|role)\b", re.IGNORECASE),
    re.compile(r"\bpromotes?\b.{0,40}\bto\b.{0,20}\b(chief|president|executive|director|officer)\b", re.IGNORECASE),
)


# --- Incidental/historical-mention calibration fix (design/DECISIONS.md,
# "Signals editorial lane false-positive audit") — has_action_language
# used to be a single, whole-article boolean: any company-action keyword
# ANYWHERE in the text was enough to grant identity to EVERY matched,
# non-ambiguous company, even one named only in a sentence that has
# nothing to do with that keyword. Verified false positive: "LS Cable &
# System... announced its labor union is threatening [a strike]... The
# cable maker supplies... customers such as SK Hynix and Samsung
# Electronics" wrongly identified SK Hynix and Samsung Electronics as
# subjects via LS Cable's own "announced" — neither company does
# anything in the text; they're named only as customer context. Fixed
# below by requiring the action keyword to co-occur in the SAME
# SENTENCE as this specific company's own mention (see
# _company_has_nearby_action_language, reusing match_companies() per
# sentence — the exact same alias resolution already used everywhere
# else, never a new matching system).
#
# A second, narrower fix covers same-sentence cases sentence-scoping
# alone cannot catch: "Microsoft co-founder Bill Gates ... warns
# governments" and "Microsoft acquired Mojang... in 2014" both put the
# company name and an action keyword in ONE sentence, but the action
# (Gates personally warning; a specific, dated historical acquisition)
# is not the company's own current activity. A small, curated set of
# biographical/historical-background markers vetoes the action-language
# route specifically — never the in-title route, so a company genuinely
# named in the headline is always unaffected regardless of this veto.
_HISTORICAL_BACKGROUND_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bco-founders?\b", re.IGNORECASE),
    re.compile(r"\bfounders?\s+of\b", re.IGNORECASE),
    re.compile(r"\b(acquired|bought)\b.{0,80}\bin\s+(19|20)\d{2}\b", re.IGNORECASE),
)


def _sentences(text: str) -> tuple[str, ...]:
    """A deliberately simple sentence split — sufficient to scope the
    action-language check to a company's own vicinity; not intended as
    real sentence-boundary detection. Falls back to treating the whole
    text as one sentence when no terminal punctuation exists at all.
    Also splits on a literal newline (Signals admission precision fix,
    P0) — _combined_text() joins title and description with exactly one
    "\\n" and no terminal punctuation on the title itself, so without
    this, a title ending in a company mention could merge with the
    description's own first sentence into one blob, letting that
    company appear deceptively close to an action verb that is really
    the description's own opening word, not the title's. A real
    excerpt's own sentences never legitimately contain an embedded raw
    newline, so this is a strictly safer boundary, not merely scoped to
    the title/description join."""
    pieces = re.split(r"(?<=[.!?])\s+|\n+", text)
    return tuple(p for p in pieces if p.strip())


# Signals admission precision fix (P0, design/SIGNALS_ADMISSION_
# MATERIALITY_CALIBRATION_2026_09_15.md / design/CURRENT_SIGNALS_
# POLICY_AND_GAP_INVENTORY_2026_09_15.md) — a code-verified gap the
# sentence-scoping fix above did not close: the same-sentence check
# only asked "does SOME action keyword and this company both appear in
# this sentence," never WHO performs that action. Verified false
# positive: "New Math Data today announced it has achieved Premier
# Tier Partner status in the Amazon Web Services (AWS) Partner
# Network" granted Amazon.com, Inc. identity purely because "announced"
# and "Amazon Web Services" share a sentence — even though New Math
# Data, not Amazon, is the one who announced anything; Amazon is named
# only as the certifying platform. Fixed by requiring the company's own
# recognized mention to appear CLOSE to the action keyword — either
# immediately before it (the ordinary "Company announced X" subject-
# verb order, given a generous lookback) or shortly after it (a
# tighter lookahead, covering the equally common "Company Announces
# $10B Expansion of AWS Data Center Capacity" shape, where the
# sentence's real subject is a short/unaliased form ("Amazon") this
# codebase's own alias table doesn't recognize on its own, but the
# recognized alias ("AWS") still appears close by, naming what the
# company acted upon). The asymmetry is deliberate: Math Data's own
# false positive has its "Amazon Web Services" mention arriving roughly
# 60+ characters after "announced," well outside even the tighter
# lookahead window — the true, load-bearing distinction is proximity,
# not strict word order. This is a deterministic, bounded-proximity
# heuristic, not a grammatical parse (this codebase has no parser
# anywhere) — a real, accepted, narrow limitation, not a claim of full
# agency detection. Never company-specific: the exact same rule applies
# to every company in the Daily News universe.
_ACTOR_LOOKBACK_CHARS = 50
_ACTOR_LOOKAHEAD_CHARS = 40
_ACTION_KEYWORD_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(keyword) for keyword in _COMPANY_ACTION_KEYWORDS) + r")\b", re.IGNORECASE,
)


def _company_is_grammatical_actor(text: str, company: str) -> bool:
    """True only when this company's own name/alias appears close to a
    company-action keyword in the same sentence — within
    _ACTOR_LOOKBACK_CHARS immediately before it, or within the tighter
    _ACTOR_LOOKAHEAD_CHARS immediately after it — approximating "the
    company is the one performing (or is closely bound to) this
    action" rather than "the company and some action verb both merely
    appear somewhere in this sentence," which the prior version
    conflated (see this function's own preceding comment for the exact
    false positive this closes, and the asymmetric-window false
    positive it also had to avoid reintroducing)."""
    for sentence in _sentences(text):
        company_spans = company_mention_spans(sentence, company)
        if not company_spans:
            continue
        for match in _ACTION_KEYWORD_PATTERN.finditer(sentence):
            verb_start, verb_end = match.start(), match.end()
            for span_start, span_end in company_spans:
                if span_end <= verb_start and (verb_start - span_end) <= _ACTOR_LOOKBACK_CHARS:
                    return True
                if span_start >= verb_end and (span_start - verb_end) <= _ACTOR_LOOKAHEAD_CHARS:
                    return True
    return False


def _has_historical_background_framing(text: str) -> bool:
    return any(pattern.search(text) for pattern in _HISTORICAL_BACKGROUND_PATTERNS)


@lru_cache(maxsize=None)
def _boundary_pattern(phrase: str) -> re.Pattern[str]:
    return re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)


def _contains_any(text: str, phrases: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(phrase for phrase in phrases if _boundary_pattern(phrase).search(text))


def _combined_text(title: str, description: str | None) -> str:
    safe_title = title or ""
    safe_description = description or ""
    return safe_title if not safe_description else f"{safe_title}\n{safe_description}"


def _matched_consumer_format(text: str) -> str | None:
    hit = _contains_any(text, _CONSUMER_FORMAT_PHRASES)
    if hit:
        return hit[0]
    for pattern in _CONSUMER_FORMAT_PATTERNS:
        if pattern.search(text):
            return pattern.pattern
    stock_hit = _contains_any(text, _STOCK_ROUNDUP_PHRASES)
    if stock_hit:
        return stock_hit[0]
    return None


def _matched_law_firm_solicitation(text: str) -> str | None:
    hit = _contains_any(text, _LAW_FIRM_SOLICITATION_PHRASES)
    if hit:
        return hit[0]
    for pattern in _LAW_FIRM_SOLICITATION_PATTERNS:
        if pattern.search(text):
            return pattern.pattern
    return None


def _matched_personnel_announcement(text: str) -> str | None:
    hit = _contains_any(text, _PERSONNEL_ANNOUNCEMENT_PHRASES)
    if hit:
        return hit[0]
    for pattern in _PERSONNEL_ANNOUNCEMENT_PATTERNS:
        if pattern.search(text):
            return pattern.pattern
    return None


def _has_anchor_evidence(materiality_reasons: tuple[str, ...]) -> bool:
    return any(
        reason == prefix or reason.startswith(prefix)
        for reason in materiality_reasons
        for prefix in _HARD_MATERIAL_REASON_PREFIXES
    )


@dataclass(frozen=True)
class AdmissionDecision:
    admitted: bool
    reason: str  # safe, human-readable diagnostic — never raw feed content beyond the matched phrase itself


def _identified_subject_companies(
    text: str, title: str, matched_companies: tuple[str, ...],
) -> list[str]:
    """Entity identity, decided WITHOUT consulting materiality: for each
    matched company, is it unambiguously the story's subject? A rival-
    entity phrase excludes a company outright. An ambiguous/common-word
    alias needs BOTH title placement and company-action language. A
    non-ambiguous company needs title placement OR company-action
    language — a bare description-only mention with neither is not
    identity, just an incidental reference."""
    if not matched_companies:
        return []
    title_companies = set(match_companies(title))
    # A historical/biographical background marker anywhere in the text
    # (see _HISTORICAL_BACKGROUND_PATTERNS's own comment) vetoes the
    # action-language route for every company this call evaluates —
    # never the in_title route, so a company genuinely named in the
    # headline is always unaffected.
    historical_background = _has_historical_background_framing(text)
    identified: list[str] = []
    for company in matched_companies:
        if _contains_any(text, _RIVAL_ENTITY_EXCLUSION_PHRASES.get(company, ())):
            continue
        in_title = company in title_companies
        has_action_language = (
            not historical_background and _company_is_grammatical_actor(text, company)
        )
        if company in _AMBIGUOUS_ALIAS_COMPANIES:
            if in_title and has_action_language:
                identified.append(company)
        elif in_title or has_action_language:
            identified.append(company)
    return identified


def assess_admission(
    title: str, description: str | None,
    matched_companies: tuple[str, ...], matched_themes: tuple[str, ...],
    materiality_reasons: tuple[str, ...],
) -> AdmissionDecision:
    """The precision-first admission gate. Called only after the
    caller's own pre-existing fail-closed check (at least one company or
    theme match already exists) — this function decides whether that
    match is actually admission-worthy, i.e. whether the company is the
    genuine SUBJECT of a corporate/financial/operational/regulatory/
    strategic/supply-chain/technology development (for a company match),
    or the theme match reflects substantive, non-consumer-format
    relevance (for a theme-only match) — never merely that its name or a
    taxonomy keyword appears somewhere in the text.

    Identity precedes materiality: _identified_subject_companies() below
    decides identity using ONLY placement/action-language/rival-entity
    signals, never materiality_reasons. anchor_evidence (a genuine
    corporate-event signal — see _HARD_MATERIAL_REASON_PREFIXES) is
    consulted exactly once, afterward, and only to excuse an already-
    identified subject's story from an otherwise-disqualifying
    consumer-format phrase (requirement: a consumer-format exception
    needs BOTH unambiguous subject identity AND a genuine anchor — never
    identity by itself, and never a bare quantity like a discount,
    price, ranking, percentage, or spec standing in for one).

    Taxonomy connection is NOT required universally: a genuine material
    development about an already-identified tracked-company subject
    qualifies on its own, with or without taxonomy vocabulary — that is
    what admits a plain, non-consumer-format company_subject story below
    regardless of anchor_evidence.
    """
    text = _combined_text(title, description)
    identified_companies = _identified_subject_companies(text, title, matched_companies)

    consumer_format_hit = _matched_consumer_format(text)
    if consumer_format_hit:
        if identified_companies and _has_anchor_evidence(materiality_reasons):
            return AdmissionDecision(True, f"company_subject:{identified_companies[0]}")
        return AdmissionDecision(False, f"consumer_editorial_format:{consumer_format_hit}")

    # Plaintiff-law-firm solicitation exclusion (Signals admission
    # precision fix, P0) — same exception shape as consumer-format
    # above: an already-identified subject's genuinely material,
    # independently-quantified story is never suppressed merely because
    # a law-firm-solicitation phrase also appears in it.
    law_firm_hit = _matched_law_firm_solicitation(text)
    if law_firm_hit:
        if identified_companies and _has_anchor_evidence(materiality_reasons):
            return AdmissionDecision(True, f"company_subject:{identified_companies[0]}")
        return AdmissionDecision(False, f"law_firm_solicitation:{law_firm_hit}")

    # Personnel/leadership/governance-announcement exclusion (Signals
    # admission precision fix, P0) — a leadership change directly
    # coupled to a disclosed transaction, funded program, procurement,
    # or capacity/capital-allocation event remains eligible via anchor_
    # evidence. Unlike the consumer-format/law-firm exceptions above,
    # this one is also theme-aware: the fixture this rule specifically
    # targets (a government-agency source with NO tracked-company
    # match at all, e.g. "DAF"/"Space Force") can only ever be rescued
    # through a matched theme, never an identified company — a company-
    # only exception, copied unchanged from the consumer-format shape,
    # would permanently block every concrete, funded-program-linked
    # agency announcement that names no tracked issuer. A theme-only
    # rescue still requires the SAME genuine anchor_evidence bar as the
    # company path — never theme membership alone.
    personnel_hit = _matched_personnel_announcement(text)
    if personnel_hit:
        if identified_companies and _has_anchor_evidence(materiality_reasons):
            return AdmissionDecision(True, f"company_subject:{identified_companies[0]}")
        if not identified_companies and matched_themes and _has_anchor_evidence(materiality_reasons):
            return AdmissionDecision(True, f"theme_subject:{matched_themes[0]}")
        return AdmissionDecision(False, f"personnel_announcement:{personnel_hit}")

    if matched_companies:
        if identified_companies:
            return AdmissionDecision(True, f"company_subject:{identified_companies[0]}")
        if not matched_themes:
            return AdmissionDecision(False, "company_mention_not_subject_worthy")

    if matched_themes:
        # Substantive theme relevance: a theme-keyword match with no
        # company subject still qualifies (this is what "relevant but
        # not yet material" — Watchlist — is for), as long as it isn't
        # itself shaped like a consumer format (already checked above)
        # or already excluded when paired with company mentions that
        # failed their own subject bar.
        return AdmissionDecision(True, f"theme_subject:{matched_themes[0]}")

    return AdmissionDecision(False, "no_qualifying_subject_evidence")
