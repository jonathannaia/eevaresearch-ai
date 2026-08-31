"""EevaResearch Phase 4, Step 4A-2 (design/DECISIONS.md) — a pure,
deterministic factory mapping one already-selected Radar
`CandidateSignal` (paired with the `LeadSelectionResult` that qualified
it — see `src.logic.research_lead_selection`) into a `ResearchCaseBundle`
containing exactly one factual `ResearchCase`, exactly one
copied-provenance `ResearchEvidenceItem`, and zero assertions.

This module performs no I/O of any kind: no file/JSON/SQLite/Postgres
access, no persistence call, no network/source fetch/scan, no LLM/
model/translation/entity-resolution call, no UI call, no random value,
no subprocess, no environment-variable read, and no system-clock read —
every timestamp in the constructed bundle comes from the candidate's own
already-recorded `state_history`, never generated here. It never
mutates its inputs, never re-runs `select_research_lead()` (the selector
remains the sole authority on lead eligibility — this factory only
re-derives the deterministic case ID as a consistency check against a
possibly stale/mismatched/fabricated `selection.case_id`), and never
calls `validate_research_case_bundle()` — that stays at the persistence
boundary (Phase 4 Step 3B) so this function stays a pure mapper.

Deterministic-ID compatibility (mirrors Step 4A-1's own documented
choice exactly): the real, canonical factories are
`src.data_access.research_store.build_case_id()` and
`.build_evidence_id()`. Both live in `src.data_access` (a data-access/
persistence package) that this step's approval explicitly forbids
importing here, to keep this module free of any I/O-capable package
coupling regardless of whether the specific imported function is pure.
`_build_case_id`/`_build_evidence_id` below are small, deliberately
minimal, byte-for-byte-compatible duplicates of those exact algorithms —
verified directly against the real factories in
tests/test_research_lead_factory.py. If either real algorithm ever
changes, the corresponding compatibility test fails and this copy must
be updated to match; neither is an independent ID scheme."""
from __future__ import annotations

import hashlib

from src.logic.research_case_validation import ResearchCaseBundle, build_research_case_bundle
from src.logic.research_lead_selection import LeadPriority, LeadSelectionResult
from src.models.models import CandidateSignal, CandidateStatus, ExtractionState, FilingEvent, Translation
from src.models.research_case import ResearchCase, ResearchCaseStatus, ResearchEvidenceItem

# Case/evidence-ID compatibility constant — must stay identical to
# src.data_access.research_store._ID_DIGEST_CHARS. See module docstring.
_ID_DIGEST_CHARS = 24


def _nonblank(value: object) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _build_case_id(trigger_source_id: str, created_at: str) -> str:
    """Byte-for-byte compatible with
    research_store.build_case_id("radar", trigger_source_id, created_at)
    — see module docstring for why this is a deliberate, tested
    duplicate rather than an import. `trigger_source_type` is always the
    literal `"radar"` for every bundle this factory builds."""
    digest = hashlib.sha256(f"radar|{trigger_source_id}|{created_at}".encode("utf-8")).hexdigest()
    return f"case-{digest[:_ID_DIGEST_CHARS]}"


def _build_evidence_id(case_id: str, source_type: str, source_id: str, added_at: str) -> str:
    """Byte-for-byte compatible with research_store.build_evidence_id() —
    see module docstring."""
    digest = hashlib.sha256(f"{case_id}|{source_type}|{source_id}|{added_at}".encode("utf-8")).hexdigest()
    return f"evidence-{digest[:_ID_DIGEST_CHARS]}"


def _find_detected_at(state_history: object) -> str | None:
    """The candidate's own first supplied-order `CANDIDATE_DETECTED`
    transition's nonblank `.at` value — never `state_history[0]` merely
    because it is first, never a current timestamp. Returns None for a
    missing/malformed history, an absent detection transition, or a
    blank timestamp — the caller fails closed in every such case."""
    if not isinstance(state_history, (list, tuple)):
        return None
    for transition in state_history:
        status = getattr(transition, "status", None)
        if isinstance(status, CandidateStatus) and status is CandidateStatus.CANDIDATE_DETECTED:
            at = getattr(transition, "at", None)
            return at if _nonblank(at) else None
    return None


def _translation_fields(excerpt_translation: object) -> tuple[str | None, str | None]:
    """Never fabricates a translation. A missing/malformed translation
    object, or one with a blank `translated_text`, collapses to
    `(None, None)`. A present, nonblank `translated_text` is retained
    with its `provider` only when `provider` is itself a real, nonblank
    string — otherwise the text is kept and the provider is `None`,
    honestly reflecting that a translation exists without a known
    source, rather than inventing one."""
    if not isinstance(excerpt_translation, Translation):
        return None, None
    translated_text = excerpt_translation.translated_text
    if not _nonblank(translated_text):
        return None, None
    provider = excerpt_translation.provider
    return translated_text, (provider if _nonblank(provider) else None)


def build_research_case_bundle_from_lead(
    candidate: CandidateSignal,
    selection: LeadSelectionResult,
) -> ResearchCaseBundle | None:
    """Returns `None` — never raises — for any invalid, ineligible,
    inconsistent, or malformed input combination. See module docstring
    for the full non-goal list. `select_research_lead()` is never called
    here; `selection` is trusted only as far as it can be independently
    re-verified (its own `case_id` is recomputed from `candidate` and
    compared exactly), and its `reasons`/`normalized_categories` are
    never read at all — they carry no information this factory is
    allowed to treat as evidence for an assertion (zero assertions are
    ever produced, in every case)."""
    if not isinstance(candidate, CandidateSignal):
        return None
    if not isinstance(selection, LeadSelectionResult):
        return None
    if not isinstance(selection.priority, LeadPriority):
        return None
    if selection.priority not in (LeadPriority.QUALIFIED, LeadPriority.HIGH_SIGNAL):
        return None
    if not _nonblank(selection.case_id):
        return None

    candidate_id = candidate.id
    if not _nonblank(candidate_id):
        return None

    detected_at = _find_detected_at(candidate.state_history)
    if detected_at is None:
        return None

    if selection.case_id != _build_case_id(candidate_id, detected_at):
        return None

    filing = candidate.filing
    if not isinstance(filing, FilingEvent):
        return None

    if not isinstance(candidate.extraction_state, ExtractionState) or candidate.extraction_state is not ExtractionState.EXTRACTED:
        return None
    if not _nonblank(candidate.excerpt_original):
        return None
    if not isinstance(candidate.status, CandidateStatus) or candidate.status is not CandidateStatus.NEEDS_REVIEW:
        return None

    required_filing_fields = (
        filing.source_name, filing.rcept_no, filing.corp_code, filing.corp_name,
        filing.report_nm, filing.source_url, filing.rcept_dt, filing.retrieved_at, filing.original_language,
    )
    if not all(_nonblank(value) for value in required_filing_fields):
        return None

    case = ResearchCase(
        id=selection.case_id,
        trigger_source_type="radar",
        trigger_source_id=candidate_id,
        trigger_source_name=filing.corp_name,
        trigger_summary=filing.report_nm,
        title=f"{filing.corp_name} — {filing.report_nm}",
        research_question=(
            f"What are the evidence-backed dependencies, relationships, or "
            f"second-order effects connected to this {filing.source_name} filing?"
        ),
        status=ResearchCaseStatus.OPEN,
        created_at=detected_at,
        version=1,
    )

    excerpt_translated, translation_provider = _translation_fields(candidate.excerpt_translation)

    evidence_item = ResearchEvidenceItem(
        id=_build_evidence_id(selection.case_id, filing.source_name, filing.rcept_no, detected_at),
        case_id=selection.case_id,
        source_type=filing.source_name,
        source_id=filing.rcept_no,
        source_url=filing.source_url,
        source_publisher_or_system=filing.source_name,
        source_date=filing.rcept_dt,
        retrieved_at=filing.retrieved_at,
        excerpt_original=candidate.excerpt_original,
        original_language=filing.original_language,
        added_at=detected_at,
        excerpt_translated=excerpt_translated,
        translation_provider=translation_provider,
    )

    return build_research_case_bundle(case, [evidence_item], [])
