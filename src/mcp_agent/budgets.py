"""Per-session tool budgets (design §6 "rate/cost limit" column, §7.2,
§11) — the primary token/cost control, enforced in the MCP server
process AND mirrored in the SDK pre-tool hook (defense in depth). Pure
state machine with an injectable monotonic clock, so every limit is
unit-testable without sleeping.

An unknown tool name is rejected here too: the budget table doubles as
the tool allowlist, so a tool the design never named cannot even acquire
a budget, let alone run.
"""
from __future__ import annotations

import time
from collections.abc import Callable

from src.mcp_agent.contracts import ToolError, ToolErrorKind

TOOL_CALL_LIMITS: dict[str, int] = {
    "resolve_tracked_issuer": 1,
    "search_filing_metadata": 3,
    "get_filing_evidence_excerpt": 5,
    "retrieve_filing_table_or_locator": 5,
    "compare_with_prior_filing": 1,
    "search_validated_evidence": 3,
    "get_validated_relationship_context": 2,
    "search_approved_official_sources": 3,
    "save_private_research_packet": 1,
    "request_publication_decision": 1,
}
ALLOWED_TOOL_NAMES: frozenset[str] = frozenset(TOOL_CALL_LIMITS)

# Both excerpt-shaped tools draw on ONE shared character budget (design §6:
# "not a separate budget — prevents budget-splitting to evade the cap").
EXCERPT_CHAR_BUDGET_TOOLS: frozenset[str] = frozenset({
    "get_filing_evidence_excerpt", "retrieve_filing_table_or_locator",
})
MAX_EXCERPT_CHARS = 4_000
SESSION_EXCERPT_CHAR_BUDGET = 20_000
SESSION_TTL_SECONDS = 180.0

ROW_CAPS: dict[str, int] = {
    "search_filing_metadata": 50,
    "search_validated_evidence": 20,
    "search_approved_official_sources": 10,
}


class SessionBudget:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._started_at = clock()
        self.calls: dict[str, int] = {}
        self.excerpt_chars = 0

    def elapsed_seconds(self) -> float:
        return self._clock() - self._started_at

    def ttl_expired(self) -> bool:
        return self.elapsed_seconds() >= SESSION_TTL_SECONDS

    def check(self, tool_name: str, excerpt_chars: int = 0) -> ToolError | None:
        """Read-only: would this call be allowed? None means yes."""
        if tool_name not in ALLOWED_TOOL_NAMES:
            return ToolError(ToolErrorKind.INVALID_INPUT, f"tool not in allowlist: {tool_name!r}")
        if self.ttl_expired():
            return ToolError(ToolErrorKind.BUDGET_EXCEEDED, f"session ttl of {SESSION_TTL_SECONDS:.0f}s exceeded")
        used = self.calls.get(tool_name, 0)
        if used >= TOOL_CALL_LIMITS[tool_name]:
            return ToolError(ToolErrorKind.BUDGET_EXCEEDED, f"{tool_name}: call limit {TOOL_CALL_LIMITS[tool_name]} reached")
        if tool_name in EXCERPT_CHAR_BUDGET_TOOLS:
            if excerpt_chars < 0:
                return ToolError(ToolErrorKind.INVALID_INPUT, "excerpt_chars must be >= 0")
            if excerpt_chars > MAX_EXCERPT_CHARS:
                return ToolError(ToolErrorKind.INVALID_INPUT, f"excerpt request exceeds per-call cap of {MAX_EXCERPT_CHARS} chars")
            if self.excerpt_chars + excerpt_chars > SESSION_EXCERPT_CHAR_BUDGET:
                return ToolError(ToolErrorKind.BUDGET_EXCEEDED, f"session excerpt budget of {SESSION_EXCERPT_CHAR_BUDGET} chars exceeded")
        return None

    def consume(self, tool_name: str, excerpt_chars: int = 0) -> ToolError | None:
        """check() then record. Never records a rejected call."""
        error = self.check(tool_name, excerpt_chars)
        if error is not None:
            return error
        self.calls[tool_name] = self.calls.get(tool_name, 0) + 1
        if tool_name in EXCERPT_CHAR_BUDGET_TOOLS:
            self.excerpt_chars += excerpt_chars
        return None

    def snapshot(self) -> dict:
        return {
            "calls": dict(sorted(self.calls.items())),
            "excerpt_chars": self.excerpt_chars,
            "elapsed_seconds": round(self.elapsed_seconds(), 3),
        }
