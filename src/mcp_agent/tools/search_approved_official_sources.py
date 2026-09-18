"""search_approved_official_sources (design §6) — rows drawn ONLY from the
approved-source allowlist: an issuer's registered IR domain (from the
issuer registry, never guessed from a URL) or the explicit government/
regulator domain list (empty by default). No live crawl or fetch of any
IR page exists in this repo, and this tool adds none: it exposes the
allowlisted entry points, and every URL it returns is re-checked
against the allowlist before the response is built. 3 calls, 10 rows."""
from __future__ import annotations

from src.config.approved_sources import GOVERNMENT_REGULATOR_DOMAIN_ALLOWLIST, is_approved_official_domain, normalize_domain
from src.config.issuer_registry import get_all_issuers
from src.mcp_agent.budgets import ROW_CAPS
from src.mcp_agent.contracts import ApprovedSourceResult, ApprovedSourceRow, SourceTier, ToolErrorKind
from src.mcp_agent.tools._common import consume, guarded, reject
from src.mcp_agent.tools._context import ToolContext, scope_check

NAME = "search_approved_official_sources"
SOURCE_CATEGORIES: frozenset[str] = frozenset({"investor_relations", "government_regulator"})
MAX_QUERY_CHARS = 80


def run(ctx: ToolContext, issuer_id: str, source_category: str, query: str | None = None) -> ApprovedSourceResult:
    inputs = {"issuer_id": issuer_id, "source_category": source_category, "query": query}
    if (error := consume(ctx, NAME, inputs)) is not None:
        return ApprovedSourceResult(error=error)
    if (error := scope_check(ctx, issuer_id)) is not None:
        return ApprovedSourceResult(error=reject(ctx, NAME, inputs, error.kind, error.detail))
    if source_category not in SOURCE_CATEGORIES:
        return ApprovedSourceResult(error=reject(ctx, NAME, inputs, ToolErrorKind.INVALID_INPUT, f"source_category must be one of {sorted(SOURCE_CATEGORIES)}"))
    needle = (query or "").strip().lower()
    if len(needle) > MAX_QUERY_CHARS:
        return ApprovedSourceResult(error=reject(ctx, NAME, inputs, ToolErrorKind.INVALID_INPUT, f"query must be <= {MAX_QUERY_CHARS} chars"))

    rows: list[ApprovedSourceRow] = []
    if source_category == "investor_relations":
        issuers, error = guarded(ctx, NAME, inputs, get_all_issuers)
        if error is not None:
            return ApprovedSourceResult(error=error)
        issuer = next((i for i in issuers if i.issuer_id == issuer_id), None)
        if issuer is not None and issuer.ir_domain:
            url = f"https://{issuer.ir_domain.strip().lower()}/"
            if is_approved_official_domain(url, issuer.ir_domain):
                rows.append(ApprovedSourceRow(url=url, title=f"{issuer.legal_name} — investor relations", published_at="", domain=normalize_domain(url) or "", source_tier=SourceTier.ISSUER_IR))
    else:
        for domain in sorted(GOVERNMENT_REGULATOR_DOMAIN_ALLOWLIST):
            url = f"https://{domain}/"
            if is_approved_official_domain(url):
                rows.append(ApprovedSourceRow(url=url, title=domain, published_at="", domain=domain, source_tier=SourceTier.GOVERNMENT_REGULATOR))
    if needle:
        rows = [r for r in rows if needle in r.title.lower() or needle in r.domain]
    rows = rows[: ROW_CAPS[NAME]]
    ctx.audit("official_source_searched", inputs, f"rows={len(rows)}", tool_name=NAME)
    return ApprovedSourceResult(rows=tuple(rows))
