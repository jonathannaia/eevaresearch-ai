"""retrieve_filing_table_or_locator (design §6) — an anchored window of
a filing at a caller-hinted section/table/caption. The hint is advisory;
the server does its own anchored search (EDGAR: the real
edgar_rules.iter_item_header_positions over the cached excerpt; other
sources: a case-insensitive caption search). Draws on the SAME
character budget as get_filing_evidence_excerpt.

Honest limitation: this repo's document caches persist EXCERPTS, not raw
document bytes, so anchoring runs over the cached excerpt rather than
the full document — re-anchoring the whole filing would require a new
fetch path the design forbids the agent from triggering."""
from __future__ import annotations

from src.data_access.edgar import edgar_rules
from src.mcp_agent.budgets import MAX_EXCERPT_CHARS
from src.mcp_agent.contracts import TableLocatorResult, ToolErrorKind
from src.mcp_agent.tools._common import consume, fetch_stored_excerpt, guarded, register_evidence, reject, require_document
from src.mcp_agent.tools._context import ToolContext
from src.models.models import EvidenceLocation, ExtractionState, LocationKind

NAME = "retrieve_filing_table_or_locator"
MAX_HINT_CHARS = 120
_CONTEXT_BEFORE = 200


def _kind_for(hint: str) -> LocationKind:
    lowered = hint.lower()
    if "table" in lowered:
        return LocationKind.TABLE
    if any(k in lowered for k in ("item", "section", "note", "part")):
        return LocationKind.SECTION
    return LocationKind.PARAGRAPH


def run(ctx: ToolContext, document_id: str, locator_hint: str) -> TableLocatorResult:
    inputs = {"document_id": document_id, "locator_hint": locator_hint}
    hint = (locator_hint or "").strip()
    if not hint or len(hint) > MAX_HINT_CHARS:
        return TableLocatorResult(document_id=document_id, error=reject(ctx, NAME, inputs, ToolErrorKind.INVALID_INPUT, f"locator_hint must be 1..{MAX_HINT_CHARS} chars"))
    # Reserve the worst case before touching anything; the actual window
    # size is what gets consumed below.
    if (error := ctx.budget.check(NAME, MAX_EXCERPT_CHARS)) is not None:
        return TableLocatorResult(document_id=document_id, error=reject(ctx, NAME, inputs, error.kind, error.detail))
    filing, error = require_document(ctx, NAME, inputs, document_id)
    if error is not None:
        return TableLocatorResult(document_id=document_id, error=error)

    fetched, error = guarded(ctx, NAME, inputs, lambda: fetch_stored_excerpt(ctx, filing))
    if error is not None:
        return TableLocatorResult(document_id=document_id, error=error)
    if fetched.state is not ExtractionState.EXTRACTED or not fetched.text.strip():
        kind = ToolErrorKind.PARSE_FAILED if fetched.state is ExtractionState.PARSE_FAILED or not fetched.text.strip() else ToolErrorKind.RETRIEVAL_FAILED
        if kind is ToolErrorKind.RETRIEVAL_FAILED:
            ctx.retrieval_error = True
        return TableLocatorResult(document_id=document_id, error=reject(ctx, NAME, inputs, kind, fetched.detail or fetched.state.value))

    text = fetched.text
    window: str | None = None
    location: EvidenceLocation | None = None
    if filing.source_name == "SEC EDGAR":
        for label, position in edgar_rules.iter_item_header_positions(text):
            if hint.lower() in label.lower():
                window = text[position: position + MAX_EXCERPT_CHARS]
                location = EvidenceLocation(kind=LocationKind.SECTION, section=label)
                break
    if window is None:
        index = text.lower().find(hint.lower())
        if index >= 0:
            start = max(0, index - _CONTEXT_BEFORE)
            window = text[start: start + MAX_EXCERPT_CHARS]
            kind = _kind_for(hint)
            location = EvidenceLocation(kind=kind, table=hint) if kind is LocationKind.TABLE else EvidenceLocation(kind=kind, section=hint)

    if window is None or not window.strip():
        consume(ctx, NAME, inputs, chars=0)
        ctx.audit("filing_locator_not_found", inputs, "found=False", tool_name=NAME)
        return TableLocatorResult(document_id=document_id, found=False)
    if (error := consume(ctx, NAME, inputs, chars=len(window))) is not None:
        return TableLocatorResult(document_id=document_id, error=error)
    locator = f"{location.kind.value}:{location.section or location.table}"
    record, clean, flags = register_evidence(ctx, filing, window, locator, location, fetched.retrieved_at)
    ctx.audit("filing_locator_retrieved", inputs, f"evidence_id={record.evidence_id} chars={len(clean)} locator={locator} injection_flags={','.join(flags) or '-'}", tool_name=NAME)
    return TableLocatorResult(document_id=document_id, found=True, locator=location, excerpt=clean, evidence_id=record.evidence_id)
