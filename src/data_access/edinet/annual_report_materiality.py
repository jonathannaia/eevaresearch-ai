"""First-pass annual-report evidence gate (EDINET only).

Scope is deliberately narrow: this module judges ONLY whether the
evidence `document_extractor.py` actually selected for an
`annual_securities_report` filing is issuer-specific enough to be worth a
human look. It is NOT an economic-materiality model, and it is NOT the
ownership/large-shareholding materiality gate — that one remains
explicitly forbidden for this pilot and absent from the EDINET pipeline
(see edinet_pipeline.py's own module docstring).

Why this exists — the demonstrated failure it closes. Before the
section-aware selection added to document_extractor.py, an EDINET annual
securities report's excerpt was the first 600 characters of the LARGEST
`honbun` member of the ZIP package. For a 有価証券報告書 the largest body
member is reliably the financial-statements section, whose opening text
is the statutory preamble naming the accounting regulations the
statements were prepared under. That preamble is identical across every
issuer and every year, so a candidate built on it carries no
issuer-specific information at all while still reaching a reviewer as
`Needs review`. Document type alone was sufficient to manufacture a
signal; this gate makes it necessary but not sufficient.

Two decisions, in this order (mirrors the two-decision shape
dart/ownership_materiality.py and dart/equity_transaction_materiality.py
already established for their own categories):

1. A preferred, issuer-specific section was actually selected
   (`location_section` is set by document_extractor's bounded, heading-
   anchored search) -> `preferred_section`. A boilerplate marker
   occurring INSIDE an otherwise issuer-specific selected section never
   rejects it: the marker list below identifies accounting-policy
   preamble, and a passing mention of 【経理の状況】 inside, say,
   【事業等のリスク】 is a cross-reference, not the evidence itself.
2. Otherwise -> `not_material`, with a reason naming exactly why (no
   excerpt at all, a matched boilerplate marker, or simply no preferred
   section found within the extractor's bounded member scan). Never
   "assume material because parsing was inconclusive" — the same rule
   dart/ownership_materiality.py already established for its own gate.

Deliberate wording discipline: the accepting outcome is phrased as
evidence-location qualification ("a preferred section was selected"),
never as a claim that the filing is economically material. Whether the
selected section actually says anything that moves a thesis remains a
human judgment, and this module must never imply otherwise.

Translation is never consulted. This module's only text input is
`excerpt_original` — the Japanese the extractor actually persisted — so a
translation failure, a missing provider, or a degraded translation can
neither create nor rescue a candidate. That independence is structural,
not conventional: there is no parameter here through which a translation
could be passed.

Pure functions only: no I/O, no network, no cache, no persistence, no
document fetching. The caller (edinet_pipeline.process_candidate) is
responsible for scoping this gate to the `annual_securities_report`
category and for persisting the outcome.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.data_access.edinet.document_extractor import MAX_ANNUAL_REPORT_MEMBERS_SCANNED

# Accounting-policy / formatting boilerplate markers. These are statutory
# form headings and the name of the Cabinet Office regulation governing
# consolidated-statement presentation — text that appears verbatim in
# every Japanese annual securities report regardless of issuer, which is
# precisely why their presence (absent a preferred section) marks an
# excerpt as carrying no issuer-specific content. They are NOT report
# content specific to any filer, and none of them is used as, or derived
# from, a checked-in copy of a real filing body.
BOILERPLATE_MARKERS: tuple[str, ...] = (
    "連結財務諸表の用語、様式及び作成方法",
    "連結財務諸表及び財務諸表の作成方法について",
    "【経理の状況】",
    "監査報告書",
)

OUTCOME_PREFERRED_SECTION = "preferred_section"
OUTCOME_NOT_MATERIAL = "not_material"

_NO_EXCERPT_DETAIL = "No annual-report excerpt was available to assess."
_NO_PREFERRED_SECTION_DETAIL = (
    "No preferred issuer-specific annual-report section found in the first "
    f"{MAX_ANNUAL_REPORT_MEMBERS_SCANNED} safe body members."
)


@dataclass(frozen=True)
class AnnualReportMaterialityResult:
    """`detail` is ALWAYS non-empty, on every branch — edinet_pipeline
    persists it verbatim into CandidateSignal.materiality_assessment, and
    a suppressed candidate whose suppression carries no stated reason
    would be exactly the silent, unauditable drop this design is meant to
    avoid."""

    outcome: str  # OUTCOME_PREFERRED_SECTION | OUTCOME_NOT_MATERIAL
    detail: str
    matched_section: str | None = None
    matched_boilerplate_marker: str | None = None


def find_boilerplate_marker(text: str) -> str | None:
    """Returns the first configured boilerplate marker present in `text`,
    or None. Plain substring search — the same convention
    dart/ownership_materiality.find_material_marker and
    edinet_rules.evaluate_document already use, never a regex over an
    undocumented document shape."""
    for marker in BOILERPLATE_MARKERS:
        if marker in text:
            return marker
    return None


def assess_annual_report_materiality(
    excerpt_original: str | None,
    location_section: str | None,
) -> AnnualReportMaterialityResult:
    """Judges ONE already-extracted annual-report excerpt. See the module
    docstring for the two-decision order and for why the accepting
    outcome is deliberately phrased as evidence-location qualification
    rather than a materiality claim.

    `excerpt_original` is the Japanese text the extractor persisted;
    `location_section` is the preferred heading it anchored on, or None
    when its bounded search found no preferred section and it fell back
    to legacy whole-member selection. Deliberately takes no translation
    argument of any kind."""
    if location_section:
        # Decision 1. Evaluated against the SELECTED section only: a
        # boilerplate marker inside an issuer-specific section is a
        # cross-reference, not the evidence, and must not reject it.
        return AnnualReportMaterialityResult(
            outcome=OUTCOME_PREFERRED_SECTION,
            detail=f"Preferred annual-report section selected: {location_section}.",
            matched_section=location_section,
        )

    text = (excerpt_original or "").strip()
    if not text:
        return AnnualReportMaterialityResult(outcome=OUTCOME_NOT_MATERIAL, detail=_NO_EXCERPT_DETAIL)

    marker = find_boilerplate_marker(text)
    if marker is not None:
        return AnnualReportMaterialityResult(
            outcome=OUTCOME_NOT_MATERIAL,
            detail=f"Selected annual-report evidence matched boilerplate marker: {marker}.",
            matched_boilerplate_marker=marker,
        )

    return AnnualReportMaterialityResult(outcome=OUTCOME_NOT_MATERIAL, detail=_NO_PREFERRED_SECTION_DETAIL)
