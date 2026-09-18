"""search_filing_metadata (design §6) — title/metadata only, never
excerpt text, read from the filing-event store the existing scans already
populated (never a fresh scan triggered by the agent). Every row carries
suppression computed by the real deterministic DART gates server-side,
so the model is never asked to infer materiality from a title. 3 calls
per session, 50 rows each."""
from __future__ import annotations

from datetime import timedelta

from src.config.issuer_registry import get_all_issuers
from src.data_access import backend_factory
from src.mcp_agent.budgets import ROW_CAPS
from src.mcp_agent.contracts import (
    FILING_SOURCE_NAMES,
    FilingMetadataResult,
    FilingMetadataRow,
    ResolutionConfidence,
    Suppression,
    ToolErrorKind,
)
from src.mcp_agent.tools._common import SOURCE_ADAPTERS, consume, filing_date, guarded, reject, suppression_for
from src.mcp_agent.tools._context import ToolContext, scope_check

NAME = "search_filing_metadata"


def run(ctx: ToolContext, issuer_id: str, source_name: str, lookback_days: int) -> FilingMetadataResult:
    inputs = {"issuer_id": issuer_id, "source_name": source_name, "lookback_days": lookback_days}
    if (error := consume(ctx, NAME, inputs)) is not None:
        return FilingMetadataResult(error=error)
    if (error := scope_check(ctx, issuer_id)) is not None:
        return FilingMetadataResult(error=reject(ctx, NAME, inputs, error.kind, error.detail))
    if source_name not in FILING_SOURCE_NAMES or source_name != ctx.scope.source_name:
        return FilingMetadataResult(error=reject(ctx, NAME, inputs, ToolErrorKind.UNAUTHORIZED, "source_name is outside this session's scope"))
    try:
        lookback = SOURCE_ADAPTERS[source_name].clamp_lookback(int(lookback_days))
    except (TypeError, ValueError):
        return FilingMetadataResult(error=reject(ctx, NAME, inputs, ToolErrorKind.INVALID_INPUT, "lookback_days must be an integer"))
    resolved = ctx.resolved_issuer
    if resolved is None or resolved.resolution_confidence is not ResolutionConfidence.EXACT:
        return FilingMetadataResult(error=reject(ctx, NAME, inputs, ToolErrorKind.INVALID_INPUT, "call resolve_tracked_issuer first (exact resolution required)"))

    issuers, error = guarded(ctx, NAME, inputs, get_all_issuers)
    if error is not None:
        return FilingMetadataResult(error=error)
    native_id = next((i.identifiers.get(source_name) for i in issuers if i.issuer_id == issuer_id), None)
    if not native_id:
        return FilingMetadataResult(error=reject(ctx, NAME, inputs, ToolErrorKind.NOT_FOUND, f"no {source_name} identifier registered for {issuer_id}"))

    events, error = guarded(ctx, NAME, inputs, lambda: backend_factory.get_filing_event_repository(ctx.settings, source_name).load_filing_events())
    if error is not None:
        return FilingMetadataResult(error=error)

    adapter = SOURCE_ADAPTERS[source_name]
    cutoff = ctx.now().date() - timedelta(days=lookback)
    rows: list[tuple] = []
    for filing in events:
        if not adapter.native_id_matches(filing, native_id):
            continue
        filed = filing_date(filing)
        if filed is None or filed < cutoff:
            continue
        evaluation = adapter.evaluate(filing)
        matched = tuple(evaluation.matched_rules)
        suppression, detail = suppression_for(source_name, filing, matched)
        rows.append((filed, filing, FilingMetadataRow(
            document_id=filing.rcept_no, source_name=source_name, title=filing.report_nm, filed_at=filing.rcept_dt,
            matched_rules=matched, confidence=evaluation.confidence, suppression=suppression, suppression_detail=detail,
        )))
    rows.sort(key=lambda r: (r[0], r[1].rcept_no), reverse=True)
    rows = rows[: ROW_CAPS[NAME]]
    for _, filing, row in rows:
        ctx.filing_events_by_id[row.document_id] = filing
        ctx.metadata_rows_by_id[row.document_id] = row
    result = FilingMetadataResult(rows=tuple(r[2] for r in rows), suppressed_count=sum(1 for r in rows if r[2].suppression is not Suppression.NONE))
    ctx.audit("filing_metadata_searched", inputs, f"rows={len(result.rows)} suppressed={result.suppressed_count} lookback={lookback}", tool_name=NAME)
    return result
