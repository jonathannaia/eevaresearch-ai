"""get_validated_relationship_context (design §6, §14) — the CURRENT,
derived relationship-edge status for an issuer, read-only, fail-closed.

Phase-0 finding at implementation time: feat/supply-chain-relationship-
seed-v1 is NOT merged into main, so src.config.relationship_seed_loader
does not exist here. This tool therefore returns a typed UNAVAILABLE
result with a REGISTRY_UNAVAILABLE error, which publication_policy row 8
treats as "cannot confirm no contradiction exists" — never as "no
relationship exists" and never invented. The loader is resolved by a
guarded import so that, once the branch merges, the same tool starts
returning real edges without a code change; any unrecognized registry
shape still degrades to UNAVAILABLE. 2 calls per session."""
from __future__ import annotations

import importlib
import importlib.util

from src.mcp_agent.contracts import (
    RelationshipContextResult,
    RelationshipContextStatus,
    RelationshipEdgeContext,
    ToolError,
    ToolErrorKind,
)
from src.mcp_agent.tools._common import consume, reject
from src.mcp_agent.tools._context import ToolContext, scope_check

NAME = "get_validated_relationship_context"
_LOADER_MODULE = "src.config.relationship_seed_loader"


def _unavailable(ctx: ToolContext, inputs: dict, detail: str) -> RelationshipContextResult:
    result = RelationshipContextResult(
        RelationshipContextStatus.UNAVAILABLE, detail=detail, error=ToolError(ToolErrorKind.REGISTRY_UNAVAILABLE, detail),
    )
    ctx.last_relationship = result
    ctx.audit("relationship_context_retrieved", inputs, f"unavailable: {detail}", tool_name=NAME)
    return result


def _enum_value(value) -> str:
    return str(getattr(value, "value", value))


def _map_edges(registry, issuer_id: str, counterparty_issuer_id: str | None) -> tuple[RelationshipEdgeContext, ...]:
    edges = []
    for edge in getattr(registry, "edges", ()):
        subject, obj = edge.subject_issuer_id, edge.object_issuer_id
        if issuer_id not in (subject, obj):
            continue
        counterparty = obj if subject == issuer_id else subject
        if counterparty_issuer_id and counterparty != counterparty_issuer_id:
            continue
        edges.append(RelationshipEdgeContext(
            relationship_type=_enum_value(edge.relationship_type), status=_enum_value(edge.status),
            direction="issuer_to_counterparty" if subject == issuer_id else "counterparty_to_issuer",
            confidence=_enum_value(getattr(edge, "confidence", "")), counterparty_issuer_id=counterparty,
            evidence_ids=tuple(getattr(edge, "evidence_ids", ())),
        ))
    return tuple(edges)


def run(ctx: ToolContext, issuer_id: str, counterparty_issuer_id: str | None = None) -> RelationshipContextResult:
    inputs = {"issuer_id": issuer_id, "counterparty_issuer_id": counterparty_issuer_id}
    if (error := consume(ctx, NAME, inputs)) is not None:
        return RelationshipContextResult(RelationshipContextStatus.UNAVAILABLE, error=error)
    if (error := scope_check(ctx, issuer_id)) is not None:
        return RelationshipContextResult(RelationshipContextStatus.UNAVAILABLE, error=reject(ctx, NAME, inputs, error.kind, error.detail))
    if importlib.util.find_spec(_LOADER_MODULE) is None:
        return _unavailable(ctx, inputs, "relationship registry module not present on this branch (feat/supply-chain-relationship-seed-v1 not merged)")
    try:
        loader = importlib.import_module(_LOADER_MODULE)
        registry = loader.load_relationship_registry_or_none()
        if registry is None:
            return _unavailable(ctx, inputs, "relationship registry failed to load")
        edges = _map_edges(registry, issuer_id, (counterparty_issuer_id or "").strip() or None)
    except Exception as exc:  # noqa: BLE001 — never invent relationship data
        return _unavailable(ctx, inputs, f"relationship registry shape unrecognized: {type(exc).__name__}")
    result = RelationshipContextResult(RelationshipContextStatus.AVAILABLE, edges=edges)
    ctx.last_relationship = result
    ctx.audit("relationship_context_retrieved", inputs, f"available: edges={len(edges)}", tool_name=NAME)
    return result
