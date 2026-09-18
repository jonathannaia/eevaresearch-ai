"""get_filing_evidence_excerpt (design §6) — a capped excerpt of ONE
filing already returned by search_filing_metadata this session, via the
adapter's cache-first document_service (a cached document is never
re-fetched; a miss performs the same single bounded fetch a human page
view would). Suppression is re-checked here as a second gate. The
excerpt is scrubbed for instruction-shaped text before it reaches the
model, and every returned excerpt becomes a resolvable EvidenceRecord
in the session registry. 5 calls per session, shared 20k-char budget."""
from __future__ import annotations

from src.mcp_agent.budgets import MAX_EXCERPT_CHARS
from src.mcp_agent.contracts import EvidenceExcerptResult, ToolErrorKind
from src.mcp_agent.tools._common import SOURCE_ADAPTERS, consume, guarded, register_evidence, reject, require_document
from src.mcp_agent.tools._context import ToolContext
from src.models.models import EvidenceLocation, ExtractionState, LocationKind

NAME = "get_filing_evidence_excerpt"


def run(ctx: ToolContext, document_id: str, max_chars: int = MAX_EXCERPT_CHARS) -> EvidenceExcerptResult:
    inputs = {"document_id": document_id, "max_chars": max_chars}
    try:
        max_chars = int(max_chars)
    except (TypeError, ValueError):
        return EvidenceExcerptResult(document_id=document_id, error=reject(ctx, NAME, inputs, ToolErrorKind.INVALID_INPUT, "max_chars must be an integer"))
    if not 1 <= max_chars <= MAX_EXCERPT_CHARS:
        return EvidenceExcerptResult(document_id=document_id, error=reject(ctx, NAME, inputs, ToolErrorKind.INVALID_INPUT, f"max_chars must be in 1..{MAX_EXCERPT_CHARS}"))
    if (error := consume(ctx, NAME, inputs, chars=max_chars)) is not None:
        return EvidenceExcerptResult(document_id=document_id, error=error)
    filing, error = require_document(ctx, NAME, inputs, document_id)
    if error is not None:
        return EvidenceExcerptResult(document_id=document_id, error=error)

    fetched, error = guarded(ctx, NAME, inputs, lambda: SOURCE_ADAPTERS[filing.source_name].fetch(ctx, filing))
    if error is not None:
        return EvidenceExcerptResult(document_id=document_id, error=error)
    if fetched.state is ExtractionState.PARSE_FAILED:
        return EvidenceExcerptResult(document_id=document_id, error=reject(ctx, NAME, inputs, ToolErrorKind.PARSE_FAILED, fetched.detail or "document could not be parsed"))
    if fetched.state is not ExtractionState.EXTRACTED:
        ctx.retrieval_error = True
        return EvidenceExcerptResult(document_id=document_id, error=reject(ctx, NAME, inputs, ToolErrorKind.RETRIEVAL_FAILED, f"{fetched.state.value}: {fetched.detail}"))
    if not fetched.text.strip():
        return EvidenceExcerptResult(document_id=document_id, error=reject(ctx, NAME, inputs, ToolErrorKind.PARSE_FAILED, "extraction produced an empty excerpt"))

    if fetched.location_section:
        locator, location = f"section:{fetched.location_section}", EvidenceLocation(kind=LocationKind.SECTION, section=fetched.location_section)
    elif fetched.source_member:
        locator, location = f"document-start prefix (member: {fetched.source_member})", None
    else:
        locator, location = "document-start prefix", None
    record, clean, flags = register_evidence(ctx, filing, fetched.text[:max_chars], locator, location, fetched.retrieved_at)
    ctx.audit(
        "evidence_excerpt_retrieved", inputs,
        f"evidence_id={record.evidence_id} chars={len(clean)} sha256={record.excerpt_sha256[:16]} injection_flags={','.join(flags) or '-'}",
        tool_name=NAME,
    )
    return EvidenceExcerptResult(
        document_id=document_id, evidence_id=record.evidence_id, excerpt=clean, excerpt_quality="extracted",
        retrieved_at=fetched.retrieved_at, evidence_location=location, evidence_source_member=fetched.source_member,
        source_url=record.source_url, source_date=record.source_date, source_tier=record.source_tier,
    )
