"""Daily News Source-Expansion & Ingestion Design, Batch 1 — a typed,
UNUSED foundation model for a future EDGAR/DART/EDINET material-event
Daily News adapter (design doc §4). Nothing in this module is imported
or constructed by daily_news_pipeline.py, scripts/daily_news_worker.py,
daily_news_backend.py, any UI page, any translation provider, or any
EDGAR/DART/EDINET scan_service/pipeline/document_service module — see
tests/test_daily_news_expansion_models.py's own isolation tests for the
mechanical proof.

Every model here is a pure, frozen dataclass or str Enum: no I/O, no
network call, no database access, no wiring to any live source, worker,
or pipeline. Field names mirror this project's existing
NewsStory/NewsSourceReference convention (src/models/daily_news_models.py)
wherever an equivalent concept already exists, rather than inventing new
naming for the same idea — but this module has zero import of
src.models.daily_news_models or src.models.models, preserving the
existing Daily-News-vs-Radar type-level decoupling this project already
established for daily_news_models.py itself.

Category vocabulary is deliberately reused, not reinvented, from this
project's own real, already-live, independently-verified EDGAR/DART/
EDINET rule modules (edgar_rules.py / dart_rules.py / edinet_rules.py) —
all three are pure, side-effect-free "routing input only" modules with
zero import of src.models.models or any I/O-performing module, so
importing them here for category-vocabulary reuse introduces no worker/
pipeline/database coupling.

EDINET correction (explicit, approved scope for this batch): a filing
candidate whose source_system is EDINET may only ever carry
status=SHADOW or status=SUPPRESSED — never CANDIDATE or PUBLISHED —
enforced by validate_filing_candidate() below. EDINET's Annual Securities
Report category is deliberately EXCLUDED from
_EDINET_ALLOWED_CATEGORIES entirely (not merely shadow-restricted): only
"share_buyback_status" and "extraordinary_report" (the EDINET
Extraordinary Report, 臨時報告書 — see
src/data_access/edinet/material_event_shadow.py's own real, live-verified
title-plus-triplet eligibility rule) are modeled as Daily-News-relevant
EDINET categories at all in this batch.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse

from src.data_access.dart import dart_rules
from src.data_access.edgar import edgar_rules


class FilingSourceSystem(str, Enum):
    EDGAR = "EDGAR"
    DART = "DART"
    EDINET = "EDINET"


class FilingCandidateStatus(str, Enum):
    """Mirrors NewsStoryStatus's own PUBLISHED/SUPPRESSED vocabulary
    (daily_news_models.py) plus the two states unique to a filing-
    derived candidate's own lifecycle — see design doc §4/§6.
    SHADOW: internal observation only, never displayed anywhere (same
        posture as material_event_shadow.py's own ShadowMatch).
    CANDIDATE: has cleared shadow-mode evidence thresholds but is not
        yet published — a later, separately-approved batch's own review
        step, not built by this batch.
    PUBLISHED: gated by validate_filing_candidate()'s strict URL/
        provenance requirement below.
    SUPPRESSED: requires a non-empty suppression_reason.
    """

    SHADOW = "shadow"
    CANDIDATE = "candidate"
    PUBLISHED = "published"
    SUPPRESSED = "suppressed"


class FilingCandidateValidationError(ValueError):
    """Raised only by assert_valid_filing_candidate() — never a raw/
    unsanitized value, always the same violation strings
    validate_filing_candidate() returns."""


# EDGAR: reuse the real, live-verified category vocabulary from
# edgar_rules.py exactly — never a parallel, hand-maintained duplicate
# that could silently drift from the real rule module.
def _edgar_allowed_categories() -> frozenset[str]:
    return frozenset(edgar_rules.FORM_TYPE_CATEGORIES.values()) | frozenset(
        edgar_rules.EIGHT_K_ITEM_CATEGORIES.values()
    )


# DART: reuse the real, live-verified category vocabulary from
# dart_rules.py exactly (the dict's own top-level keys, e.g. "earnings",
# "capex_or_facility_investment") — same reuse discipline as EDGAR above.
def _dart_allowed_categories() -> frozenset[str]:
    return frozenset(dart_rules.KOREAN_KEYWORD_LEXICON.keys())


# EDINET: deliberately NOT the full edinet_rules.EDINET_CATEGORIES
# taxonomy (most of that is an unbacked structural placeholder, per that
# module's own docstring) and deliberately NOT "annual_securities_report"
# — this batch's own explicit correction. Only the two categories real
# enough to model as future shadow-only candidates: the live-mapped
# share_buyback_status rule (edinet_rules.DEFAULT_CODE_CATEGORY_MAP) and
# the live-verified Extraordinary Report shadow-eligibility rule
# (material_event_shadow.py) — named "extraordinary_report" here since
# that module itself exposes no category string of its own (it returns a
# bool via is_eligible_extraordinary_report(), not a category label).
_EDINET_ALLOWED_CATEGORIES: frozenset[str] = frozenset({"share_buyback_status", "extraordinary_report"})


def validate_filing_event_category(source_system: FilingSourceSystem, category: str) -> bool:
    """Fail-closed: an unrecognized (source_system, category) pair is
    never valid, and an unrecognized source_system itself is never
    valid. Pure function, no I/O."""
    if source_system == FilingSourceSystem.EDGAR:
        return category in _edgar_allowed_categories()
    if source_system == FilingSourceSystem.DART:
        return category in _dart_allowed_categories()
    if source_system == FilingSourceSystem.EDINET:
        return category in _EDINET_ALLOWED_CATEGORIES
    return False


def _is_https_url(url: str | None) -> bool:
    if not url or not url.strip():
        return False
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(parsed.netloc)


@dataclass(frozen=True)
class FilingProvenance:
    """Immutable, verbatim record of exactly where and when this
    candidate's underlying filing document was retrieved — never
    derived, never a display-layer value. source_form_code is
    source-system-specific free text: EDGAR's own form type + item
    number (e.g. "8-K item 1.01"), DART's own matched rule category (e.g.
    "capex_or_facility_investment"), or EDINET's own
    ordinanceCode:formCode:docTypeCode triplet (e.g. "010:053000:180") —
    each system's own real shape differs, so this is stored as free text
    rather than forcing one shape onto all three."""

    source_system: FilingSourceSystem
    retrieved_at: str  # ISO 8601, when EevaResearch fetched this filing's metadata
    source_form_code: str


@dataclass(frozen=True)
class FilingDerivedNewsCandidate:
    """Typed, UNUSED foundation model — see module docstring. Every
    instance is immutable; a status transition (e.g. shadow -> candidate)
    is modeled, in a later batch, as constructing a new instance, the
    same "frozen dataclass, no in-place mutation" convention
    NewsSourceReference (daily_news_models.py) already uses.

    title_native and provenance are the untouchable original-evidence
    fields. A translation is always a separate, additive pair of fields
    (title_translated / translation_language / translation_retrieved_at)
    — never an in-place overwrite of title_native — mirroring
    NewsStory.original_title/translation_unavailable's own existing
    precedent exactly (see also the translation contract in the approved
    design doc §5)."""

    doc_id: str  # source-native document id: EDGAR accession number, DART rcept_no, or EDINET docID
    source_system: FilingSourceSystem
    company_name: str  # must match a real TrackedCompany.name (tracked_companies.py) — same convention as NewsStory.company_name
    issuer_identifier: str  # source-native id at filing time: EDGAR CIK, DART corp_code, or EDINET code — never re-derived
    filing_date: str  # ISO 8601, source-claimed filing/publication date
    title_native: str  # immutable, verbatim native-language title — never rewritten
    event_category: str  # validated against validate_filing_event_category() — never a free-form guess
    provenance: FilingProvenance
    dedupe_key: str  # normalized (company + calendar date + normalized title) — see design doc §4; not computed here, caller-supplied
    status: FilingCandidateStatus
    official_document_url: str | None = None  # required, HTTPS, before status may be PUBLISHED — see validate_filing_candidate()
    title_translated: str | None = None  # never overwrites title_native
    translation_language: str | None = None  # e.g. "English" — required together with title_translated
    translation_retrieved_at: str | None = None  # ISO 8601; None until a real translation is attached
    suppression_reason: str | None = None  # required non-empty when status == SUPPRESSED


def validate_filing_candidate(candidate: FilingDerivedNewsCandidate) -> tuple[str, ...]:
    """Pure, single-candidate validation — returns every violation found
    (empty tuple = valid), never raises, mirrors
    source_registry.validate_source_entry()'s own "collect every
    violation, don't stop at the first" convention exactly."""
    violations: list[str] = []

    try:
        FilingSourceSystem(candidate.source_system)
    except ValueError:
        violations.append(f"unsupported source_system: {candidate.source_system!r}")

    try:
        FilingCandidateStatus(candidate.status)
    except ValueError:
        violations.append(f"unsupported status: {candidate.status!r}")

    if not candidate.doc_id or not candidate.doc_id.strip():
        violations.append("doc_id must be non-empty")
    if not candidate.company_name or not candidate.company_name.strip():
        violations.append("company_name must be non-empty")
    if not candidate.issuer_identifier or not candidate.issuer_identifier.strip():
        violations.append("issuer_identifier must be non-empty")
    if not candidate.filing_date or not candidate.filing_date.strip():
        violations.append("filing_date must be non-empty")
    if not candidate.title_native or not candidate.title_native.strip():
        violations.append("title_native must be non-empty")
    if not candidate.dedupe_key or not candidate.dedupe_key.strip():
        violations.append("dedupe_key must be non-empty")

    if not validate_filing_event_category(candidate.source_system, candidate.event_category):
        violations.append(
            f"unrecognized event_category {candidate.event_category!r} for source_system "
            f"{candidate.source_system!r}"
        )

    if not candidate.provenance.source_form_code or not candidate.provenance.source_form_code.strip():
        violations.append("provenance.source_form_code must be non-empty")
    if not candidate.provenance.retrieved_at or not candidate.provenance.retrieved_at.strip():
        violations.append("provenance.retrieved_at must be non-empty")

    if candidate.status == FilingCandidateStatus.SUPPRESSED:
        if not candidate.suppression_reason or not candidate.suppression_reason.strip():
            violations.append("suppression_reason must be non-empty when status == SUPPRESSED")

    if candidate.status == FilingCandidateStatus.PUBLISHED:
        if not _is_https_url(candidate.official_document_url):
            violations.append("a PUBLISHED candidate must have a non-empty https:// official_document_url")

    # EDINET correction (explicit, approved scope for this batch): every
    # EDINET filing candidate stays shadow_only in this batch — never
    # promoted to CANDIDATE or PUBLISHED here, regardless of category.
    if candidate.source_system == FilingSourceSystem.EDINET and candidate.status in (
        FilingCandidateStatus.CANDIDATE, FilingCandidateStatus.PUBLISHED,
    ):
        violations.append(
            "EDINET filing candidates must remain shadow_only in this batch — "
            f"status {candidate.status!r} is not permitted yet"
        )

    if candidate.title_translated is not None:
        if not candidate.translation_language or not candidate.translation_language.strip():
            violations.append("translation_language is required when title_translated is set")

    return tuple(violations)


def assert_valid_filing_candidate(candidate: FilingDerivedNewsCandidate) -> None:
    violations = validate_filing_candidate(candidate)
    if violations:
        raise FilingCandidateValidationError("; ".join(violations))
