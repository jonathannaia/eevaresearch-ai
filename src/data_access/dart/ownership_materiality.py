"""Ownership-change materiality gate (Radar Calibration milestone, Korea
DART pilot). A narrow noise-reduction exception for the `ownership_change`
rule category only — never a general materiality score, and never applied
to any other category (earnings, capex, financing, market rumor response,
risk disclosure, amendment).

Two decisions this module makes, both deterministic and explainable:

1. If a document-level material marker (tender offer, pledge, merger,
   controlling-shareholder change — see MATERIAL_OWNERSHIP_MARKERS) is
   present in the filing's title or extracted excerpt, the change is
   always treated as worth a human look, regardless of any numeric
   percentage-point delta.
2. Otherwise, if a before/after ownership percentage can be reliably
   parsed from the extracted excerpt, the absolute percentage-point delta
   is compared against OWNERSHIP_MATERIALITY_THRESHOLD_PP — a pilot
   calibration setting, not a financial or investment rule. Below the
   threshold, the filing is treated as routine and never assumed material
   just because parsing failed.

Percentage-delta extraction is pattern-matched against two real document
shapes, both grounded in real cached Radar pilot excerpts (Samsung
Electronics, verified during the Milestone-6 calibration pass) — never a
generic financial-table parser and never a guess. A shape this module
doesn't recognize returns None rather than a wrong number.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Pilot calibration setting only — not a financial/investment materiality
# threshold. Chosen because both real "unchanged"/"negligible" candidates
# observed in the cache (0.00pp and ~0.003pp deltas) sit well below it,
# while still being small enough not to require a real ownership shift to
# cross it. Configurable — pass a different value to assess_ownership_materiality.
OWNERSHIP_MATERIALITY_THRESHOLD_PP = 0.05

# Standard, documented Korean securities-regulation disclosure terms for
# control/strategic events that should bypass the numeric threshold
# entirely. None of these were observed in the current 520-filing cache
# (there is no tender offer, pledge, or merger event in this pilot's
# window) — each is a well-established term from Korea's Financial
# Investment Services and Capital Markets Act / DART's own documented
# disclosure-type taxonomy, the same "standard, not observed" convention
# already used in dart_rules.py's own lexicon — not invented or guessed.
# Two categories the brief asked for ("strategic investment", "major new
# beneficial owner") are deliberately NOT included: neither has a single
# unambiguous Korean term that reliably signals the concept without risk
# of false-firing on unrelated filings (e.g. "제3자배정" is a capital-raise
# mechanism that would actually surface under the `financing` category,
# not `ownership_change`) — omitted rather than guessed; see
# design/DECISIONS.md for the full reasoning.
MATERIAL_OWNERSHIP_MARKERS: dict[str, tuple[str, ...]] = {
    "controlling_shareholder_change": ("최대주주변경", "경영권변동"),
    "tender_offer": ("공개매수",),
    "pledge_or_collateral": ("질권설정", "질권해지"),
    "compulsory_acquisition_or_merger": ("합병", "주식의포괄적교환", "완전자회사"),
}


@dataclass(frozen=True)
class OwnershipMaterialityResult:
    outcome: str  # "material_marker" | "material_threshold" | "not_material"
    detail: str
    delta_percentage_points: float | None
    matched_marker: str | None = None


def find_material_marker(text: str) -> tuple[str, str] | None:
    """Returns (category, matched_term) for the first configured marker
    found in the given text, or None. Plain substring search — same
    approach as dart_rules.evaluate_report_name, deliberately no
    normalization/fuzzy matching."""
    for category, terms in MATERIAL_OWNERSHIP_MARKERS.items():
        for term in terms:
            if term in text:
                return category, term
    return None


def _extract_daeryang_bogyu_delta(text: str) -> float | None:
    """대량보유상황보고서(일반) shape — verified against the real cached
    excerpt for Samsung rcept_no 20260724000625: '...보유주식등의 수 및
    보유비율 보유주식등의 수 보유비율 직전 보고서 {n} {p} 이번 보고서 {n}
    {p} 주요계약체결...'. Anchored on the compound word '보유비율'
    immediately preceding '직전 보고서' so this does not accidentally
    match the document's second, unrelated '주요계약체결' percentage
    block (which is preceded by the bare word '비율', not '보유비율')."""
    match = re.search(r"보유비율\s*직전\s*보고서\s+[\d,]+\s+([\d.]+)\s+이번\s*보고서\s+[\d,]+\s+([\d.]+)", text)
    if not match:
        return None
    try:
        before, after = float(match.group(1)), float(match.group(2))
    except ValueError:
        return None
    return abs(after - before)


def _extract_choedae_jujoo_delta(text: str) -> float | None:
    """최대주주등소유주식변동신고서 shape — verified against two real
    cached excerpts (Samsung rcept_no 20260721801260, an increase, and
    20260731801296, a decrease): the filing states its own computed
    aggregate delta directly under a '증감' (change) section, e.g.
    '...증감 보통주식 {n} {p} 종류주식 {n} {p} 증권예탁증권 {n} {p} 합계
    {n} {p}'. Takes the '합계' (total) line immediately following '증감'
    — the earlier '합계' lines under the '직전'/'이번' snapshot sections
    are deliberately not matched, since '증감.*?합계' (non-greedy) finds
    the first '합계' after '증감', which is the already-computed total
    delta, not a raw snapshot total. The count and percentage are signed
    ('-171,178', '-0.01') when the holding decreased — a real decrease
    filing (20260731801296) exposed a bug where the original pattern
    only matched unsigned digits and silently fell through to "no delta
    found" on any decrease. abs() is applied by the caller regardless of
    sign, since only the magnitude of the change matters here."""
    match = re.search(r"증감.*?합계\s+-?[\d,]+\s+(-?[\d.]+)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def extract_ownership_delta_pp(excerpt: str | None) -> float | None:
    """Best-effort, pattern-matched only. Returns None (never a guess) if
    the excerpt is missing or matches neither known real document shape.
    Always a non-negative magnitude — a real decrease filing
    (20260731801296) showed the '증감' shape can report a signed delta
    (e.g. -0.01), and only the size of the change determines materiality
    here, not its direction."""
    if not excerpt:
        return None
    for extractor in (_extract_daeryang_bogyu_delta, _extract_choedae_jujoo_delta):
        delta = extractor(excerpt)
        if delta is not None:
            return abs(delta)
    return None


def assess_ownership_materiality(
    title: str,
    excerpt: str | None,
    threshold_pp: float = OWNERSHIP_MATERIALITY_THRESHOLD_PP,
) -> OwnershipMaterialityResult:
    """The ownership-change materiality gate. Only meaningful for
    candidates whose matched_rules include an `ownership_change:` entry —
    callers are responsible for that scoping (see radar_pipeline.py)."""
    marker = find_material_marker(title)
    if marker is None and excerpt:
        marker = find_material_marker(excerpt)
    if marker is not None:
        category, term = marker
        return OwnershipMaterialityResult(
            outcome="material_marker",
            detail=f"Ownership change needs review — material marker matched: {category} ({term})",
            delta_percentage_points=None,
            matched_marker=f"{category}:{term}",
        )

    delta = extract_ownership_delta_pp(excerpt)
    if delta is None:
        # Extraction unavailable or the excerpt didn't match a known
        # shape — no marker either, so per the pilot's "do not assume a
        # material change" rule this is treated the same as a routine,
        # sub-threshold update rather than promoted on ambiguous grounds.
        return OwnershipMaterialityResult(
            outcome="not_material",
            detail="Not material · routine ownership update",
            delta_percentage_points=None,
        )

    if delta >= threshold_pp:
        return OwnershipMaterialityResult(
            outcome="material_threshold",
            detail=f"Ownership change ≥ {threshold_pp} percentage points",
            delta_percentage_points=delta,
        )
    return OwnershipMaterialityResult(
        outcome="not_material",
        detail="Not material · routine ownership update",
        delta_percentage_points=delta,
    )
