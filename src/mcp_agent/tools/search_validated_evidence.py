"""search_validated_evidence (design §6) — a lookup over ALREADY-vetted
ResearchEvidenceItems in the research store, never a fresh retrieval.
Each returned row is registered in the session evidence registry (its
tier classified from its own source URL) so it can be cited as
factual_context and still be resolved by publication_policy row 7.
3 calls per session, 20 rows each.

Limitation: ResearchEvidenceItem carries no issuer field, so the issuer
filter here is scope authorization only; theme_slug/keyword are
best-effort substring filters over the item's own text fields."""
from __future__ import annotations

import hashlib

from src.config.approved_sources import classify_source_url
from src.data_access import research_store
from src.mcp_agent.budgets import ROW_CAPS
from src.mcp_agent.contracts import (
    EvidenceRecord,
    FreshnessStatus,
    ToolErrorKind,
    ValidatedEvidenceResult,
    ValidatedEvidenceRow,
)
from src.mcp_agent.tools._common import consume, guarded, reject
from src.mcp_agent.tools._context import ToolContext, scope_check
from src.mcp_agent.tools._untrusted import scrub_untrusted_text

NAME = "search_validated_evidence"
MAX_FILTER_CHARS = 80


def run(ctx: ToolContext, issuer_id: str, theme_slug: str | None = None, keyword: str | None = None) -> ValidatedEvidenceResult:
    inputs = {"issuer_id": issuer_id, "theme_slug": theme_slug, "keyword": keyword}
    if (error := consume(ctx, NAME, inputs)) is not None:
        return ValidatedEvidenceResult(error=error)
    if (error := scope_check(ctx, issuer_id)) is not None:
        return ValidatedEvidenceResult(error=reject(ctx, NAME, inputs, error.kind, error.detail))
    theme = (theme_slug or "").strip().lower()
    word = (keyword or "").strip().lower()
    if len(theme) > MAX_FILTER_CHARS or len(word) > MAX_FILTER_CHARS:
        return ValidatedEvidenceResult(error=reject(ctx, NAME, inputs, ToolErrorKind.INVALID_INPUT, f"filters must be <= {MAX_FILTER_CHARS} chars"))

    items, error = guarded(ctx, NAME, inputs, lambda: research_store.load_evidence_items(ctx.settings.cache_dir))
    if error is not None:
        return ValidatedEvidenceResult(error=error)

    selected = []
    for item in items.values():
        haystack = f"{item.excerpt_original}\n{item.source_id}\n{item.source_publisher_or_system}".lower()
        if word and word not in haystack:
            continue
        if theme and theme not in f"{item.source_type}\n{item.source_publisher_or_system}".lower():
            continue
        selected.append(item)
    selected.sort(key=lambda i: (i.source_date, i.id), reverse=True)
    selected = selected[: ROW_CAPS[NAME]]

    rows = []
    for item in selected:
        clean, _flags = scrub_untrusted_text(item.excerpt_original)
        ctx.evidence[item.id] = EvidenceRecord(
            evidence_id=item.id, session_id=ctx.scope.session_id, issuer_id=ctx.scope.issuer_id,
            source_tier=classify_source_url(item.source_url), source_name=item.source_publisher_or_system, source_url=item.source_url,
            source_document_id=item.source_id, source_date=item.source_date, excerpt_or_locator=f"validated-evidence:{item.id}",
            excerpt_sha256=hashlib.sha256(clean.encode("utf-8")).hexdigest(), retrieved_at=item.retrieved_at,
            confidence="validated", freshness_status=FreshnessStatus.CURRENT,
        )
        ctx.evidence_text[item.id] = clean
        rows.append(ValidatedEvidenceRow(
            evidence_item_id=item.id, case_id=item.case_id, source_type=item.source_type, source_id=item.source_id,
            source_url=item.source_url, source_date=item.source_date, excerpt_original=clean, retrieved_at=item.retrieved_at,
        ))
    ctx.audit("validated_evidence_searched", inputs, f"rows={len(rows)} of {len(items)}", tool_name=NAME)
    return ValidatedEvidenceResult(rows=tuple(rows))
