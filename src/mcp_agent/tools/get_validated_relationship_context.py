"""get_validated_relationship_context (design §6, §14) — the CURRENT,
derived relationship-edge status for an issuer, read-only, fail-closed.

src.config.relationship_seed_loader does not exist on main: the seed,
loader and models live on the unmerged feat/supply-chain-relationship-
seed-v1. So this tool returns a typed UNAVAILABLE result with a
REGISTRY_UNAVAILABLE error, which the publication policy treats as
"cannot confirm no contradiction exists" — never as "no relationship
exists", and never invented.

This file previously claimed the tool would "start returning real edges
without a code change" once that branch merged. It would not have, and
the way it failed mattered. The branch's actual signature is
`load_relationship_registry_or_none(path) -> tuple[registry | None,
str | None]`: it requires a path and returns a two-tuple. The old call
site passed no path and treated the result as the registry itself.
Merged as written, the missing argument raised TypeError, which the
blanket except turned into UNAVAILABLE — safe, but by accident. The
trap was the obvious repair: pass a path and nothing else, and the
returned TUPLE is not None, so the None-check passes and
`getattr(tuple, "edges", ())` yields an empty tuple — AVAILABLE with
zero edges. The agent would then read "this issuer has no validated
relationships" when the truth was "twelve exist and we failed to read
them". That is the one answer this tool must never give.

So the contract is now explicit rather than duck-typed: the loader is
called with the seed path, the two-tuple is unpacked, and a registry is
only accepted if it genuinely carries an `edges` collection. Anything
else — module absent, path absent, load error, unexpected return shape,
missing attribute — is UNAVAILABLE. If the branch's contract changes
again before it merges, this still degrades closed, by construction and
not by luck. 2 calls per session."""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

from src.config.settings import PROJECT_ROOT
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
# Same location the seed branch's own tests resolve, kept here because
# the loader itself declares no default path.
_SEED_PATH: Path = PROJECT_ROOT / "config" / "eevaresearch_relationship_seed.yaml"


def _unavailable(ctx: ToolContext, inputs: dict, detail: str) -> RelationshipContextResult:
    result = RelationshipContextResult(
        RelationshipContextStatus.UNAVAILABLE, detail=detail, error=ToolError(ToolErrorKind.REGISTRY_UNAVAILABLE, detail),
    )
    ctx.last_relationship = result
    ctx.audit("relationship_context_retrieved", inputs, f"unavailable: {detail}", tool_name=NAME)
    return result


def _enum_value(value) -> str:
    return str(getattr(value, "value", value))


class _RegistryContractError(Exception):
    """The loader returned something this tool will not interpret. Raised
    only to be caught by run()'s own except and turned into UNAVAILABLE —
    it must never propagate to the agent."""


def _unpack_load_result(result) -> tuple[object | None, str | None]:
    """The seed branch's loader returns `(registry | None, error | None)`.
    Accept exactly that, and reject anything else rather than guessing:
    a bare object here used to sail through the `is None` check and
    present as a registry with no edges."""
    if isinstance(result, tuple):
        if len(result) != 2:
            raise _RegistryContractError(f"loader returned a {len(result)}-tuple, expected 2")
        registry, error = result
        return registry, (str(error) if error is not None else None)
    raise _RegistryContractError(f"loader returned {type(result).__name__}, expected a 2-tuple")


def _require_registry(registry):
    """A registry is only usable if it actually carries an edge
    collection. `getattr(obj, "edges", ())` is what made an unreadable
    registry indistinguishable from an issuer with no relationships, so
    the default is gone and absence is an error."""
    edges = getattr(registry, "edges", None)
    if edges is None:
        raise _RegistryContractError(f"{type(registry).__name__} has no 'edges' attribute")
    if not isinstance(edges, (tuple, list)):
        raise _RegistryContractError(f"'edges' is {type(edges).__name__}, expected a sequence")
    return registry


def _map_edges(registry, issuer_id: str, counterparty_issuer_id: str | None) -> tuple[RelationshipEdgeContext, ...]:
    edges = []
    for edge in registry.edges:
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
    if not _SEED_PATH.is_file():
        return _unavailable(ctx, inputs, "relationship seed file absent")
    try:
        loader = importlib.import_module(_LOADER_MODULE)
        registry, load_error = _unpack_load_result(loader.load_relationship_registry_or_none(_SEED_PATH))
        if registry is None:
            return _unavailable(ctx, inputs, f"relationship registry failed to load: {load_error or 'unspecified'}")
        edges = _map_edges(_require_registry(registry), issuer_id, (counterparty_issuer_id or "").strip() or None)
    except Exception as exc:  # noqa: BLE001 — never invent relationship data
        return _unavailable(ctx, inputs, f"relationship registry shape unrecognized: {type(exc).__name__}")
    result = RelationshipContextResult(RelationshipContextStatus.AVAILABLE, edges=edges)
    ctx.last_relationship = result
    ctx.audit("relationship_context_retrieved", inputs, f"available: edges={len(edges)}", tool_name=NAME)
    return result
