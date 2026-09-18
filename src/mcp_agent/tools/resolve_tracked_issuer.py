"""resolve_tracked_issuer (design §6) — resolves a filing's native id to a
real tracked issuer via the issuer registry. Never a fuzzy/LLM-side
match: an exact identifier match, or an exact case-insensitive name/
alias match as a fallback that names its candidates and returns
UNRESOLVED/AMBIGUOUS rather than guessing. 1 call per session."""
from __future__ import annotations

from src.config.issuer_registry import get_all_issuers, tracked_companies_from_issuer_registry
from src.mcp_agent.contracts import FILING_SOURCE_NAMES, IssuerResolution, ResolutionConfidence, ToolError, ToolErrorKind
from src.mcp_agent.tools._common import SOURCE_ADAPTERS, consume, guarded, reject
from src.mcp_agent.tools._context import ToolContext

NAME = "resolve_tracked_issuer"


def _identifier_matches(source_name: str, registered: str | None, native_id: str) -> bool:
    if not registered:
        return False
    if source_name == "SEC EDGAR":
        try:
            return int(registered) == int(native_id)
        except ValueError:
            return False
    return registered == native_id


def run(ctx: ToolContext, source_name: str, native_id: str | None = None, name_hint: str | None = None) -> IssuerResolution:
    inputs = {"source_name": source_name, "native_id": native_id, "name_hint": name_hint}
    if (error := consume(ctx, NAME, inputs)) is not None:
        return IssuerResolution(ResolutionConfidence.UNRESOLVED, error=error)
    if source_name not in FILING_SOURCE_NAMES:
        return IssuerResolution(ResolutionConfidence.UNRESOLVED, error=reject(ctx, NAME, inputs, ToolErrorKind.INVALID_INPUT, f"unknown source_name {source_name!r}"))
    native_id = (native_id or "").strip()
    name_hint = (name_hint or "").strip()
    if not native_id and not name_hint:
        return IssuerResolution(ResolutionConfidence.UNRESOLVED, error=reject(ctx, NAME, inputs, ToolErrorKind.INVALID_INPUT, "native_id or name_hint is required"))
    if native_id and not SOURCE_ADAPTERS[source_name].id_pattern.match(native_id):
        return IssuerResolution(ResolutionConfidence.UNRESOLVED, error=reject(ctx, NAME, inputs, ToolErrorKind.INVALID_INPUT, f"malformed native_id for {source_name}"))

    issuers, error = guarded(ctx, NAME, inputs, get_all_issuers)
    if error is not None:
        return IssuerResolution(ResolutionConfidence.UNRESOLVED, error=error)

    matches = [i for i in issuers if native_id and _identifier_matches(source_name, i.identifiers.get(source_name), native_id)]
    if not matches and name_hint:
        wanted = name_hint.lower()
        matches = [i for i in issuers if wanted in {i.legal_name.lower(), i.native_name.lower(), *(a.lower() for a in i.aliases)}]

    if not matches:
        result = IssuerResolution(ResolutionConfidence.UNRESOLVED)
    elif len(matches) > 1:
        result = IssuerResolution(ResolutionConfidence.AMBIGUOUS, candidates=tuple(sorted(i.issuer_id for i in matches)))
    else:
        issuer = matches[0]
        if issuer.issuer_id != ctx.scope.issuer_id:
            result = IssuerResolution(
                ResolutionConfidence.UNRESOLVED, candidates=(issuer.issuer_id,),
                error=reject(ctx, NAME, inputs, ToolErrorKind.UNAUTHORIZED, f"resolved issuer {issuer.issuer_id!r} is outside this session's scope"),
            )
        else:
            company = next((c for c in tracked_companies_from_issuer_registry(active_only=False) if c.name == issuer.legal_name), None)
            result = IssuerResolution(
                ResolutionConfidence.EXACT, issuer_id=issuer.issuer_id, tracked_company_name=issuer.legal_name,
                exchange=(company.exchange if company else issuer.primary_exchange),
                themes=tuple(company.themes if company else issuer.themes),
            )
    ctx.resolved_issuer = result
    ctx.audit("issuer_resolution_attempted", inputs, f"{result.resolution_confidence.value}:{result.issuer_id or ','.join(result.candidates) or '-'}", tool_name=NAME)
    return result
