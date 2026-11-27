"""The replaceable model-provider boundary for filing comparison.

Deliberately NOT the existing agent. `src/mcp_agent/agent_session.py`
wraps `claude_agent_sdk.query` and runs an autonomous session with tool
use, its own MCP server and a publication path. This component needs the
opposite: one bounded, single-shot call that can retrieve nothing, call
nothing and publish nothing. Those are different contracts, so this is a
separate seam rather than a second mode bolted onto that one.

Nothing here selects or names a paid model, and nothing estimates a
cost. `ComparisonProvider` is a Protocol with a `model_id` the caller
supplies; the only implementation in this module is a fake. Choosing a
real model, and any statement about what it would cost, both require
workload measurements and published rates that this branch does not
have — see the branch report's "remaining requirements".

Usage accounting mirrors `src/mcp_agent/budgets.py`'s design (injectable
clock, read-only `check` before a recording `consume`, typed error
rather than an exception) without reusing its table, whose entries are
MCP tool names and whose units are tool calls and excerpt characters.
The units here are calls, input characters and output characters.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class ProviderFailure(str, Enum):
    TIMEOUT = "timeout"
    TRANSPORT = "transport"
    REFUSED = "refused"
    MALFORMED_OUTPUT = "malformed_output"
    UNCONFIGURED = "unconfigured"


class ProviderError(Exception):
    """Raised only by a provider implementation. The service catches it
    and converts it into a refusal result — it never escapes to a
    caller, and a provider failure is never retried with a looser
    prompt or a looser schema."""

    def __init__(self, failure: ProviderFailure, detail: str = "") -> None:
        super().__init__(f"{failure.value}: {detail}" if detail else failure.value)
        self.failure = failure
        self.detail = detail


@dataclass(frozen=True)
class ProviderRequest:
    """What crosses the boundary. Plain text and a schema name — no
    tools, no tool definitions, no system-level capability grant. A
    provider implementation that offers the model any tool would be
    violating this contract, which is why the shape has nowhere to put
    one: no retrieval, no browsing, no publication path, no callback.

    `max_output_chars` is a REQUEST to the provider, not an enforcement
    point. Whether a provider honours it is outside this module's
    control, which is why the service also rejects an oversized
    response — see `UsageLimits.max_output_chars`."""

    model_id: str
    system_prompt: str
    user_prompt: str
    schema_name: str
    max_output_chars: int


@dataclass(frozen=True)
class ProviderResponse:
    """The raw structured payload, before any validation. `payload` is a
    plain dict so a provider can return parsed JSON without this module
    knowing anything about the vendor's SDK."""

    payload: dict
    input_chars: int
    output_chars: int
    model_id: str


class ComparisonProvider(Protocol):
    """One call, one structured answer. Every test in this package uses
    a fake implementation; no test requires an API key, and importing
    this module never reads credentials or opens a connection."""

    model_id: str

    def complete(self, request: ProviderRequest) -> ProviderResponse: ...


# --- usage accounting -----------------------------------------------------

DEFAULT_MAX_CALLS = 1
DEFAULT_MAX_INPUT_CHARS = 24_000
DEFAULT_MAX_OUTPUT_CHARS = 8_000
DEFAULT_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class UsageLimits:
    """Configurable per deployment. Defaults are deliberately small: one
    call per request, and an input cap that comfortably exceeds two
    600-character excerpts plus the prompt, so a pathological input is a
    refusal rather than an expensive call.

    The three caps are not equivalent in what they can prevent:

      * `max_calls` and `max_input_chars` are checked BEFORE the
        provider is called, so exceeding either costs nothing.
      * `max_output_chars` is sent to the provider as a request AND
        re-checked on the response. The re-check can only discard an
        oversized answer; by the time it runs the provider has already
        produced those characters and any associated cost is already
        incurred. It is an output-acceptance rule, not a spend control.

    `timeout_seconds` is carried for a real provider implementation to
    apply; nothing in this module enforces it, because nothing here
    performs I/O."""

    max_calls: int = DEFAULT_MAX_CALLS
    max_input_chars: int = DEFAULT_MAX_INPUT_CHARS
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


# One account exists per `compare_filings` call and is discarded with
# it. Nothing in this module aggregates across requests, processes or
# deployments.
ACCOUNTING_SCOPE = "single_request_in_memory"


@dataclass
class UsageAccount:
    """Counts what was actually spent, in units this component can
    observe without vendor cooperation: calls and characters. Tokens and
    money are deliberately absent — converting either requires a
    tokenizer and a rate card this branch does not have, and a guessed
    number would be worse than none.

    --- Scope, stated precisely ---

    The scope is ONE request, in ONE process, in memory
    (`ACCOUNTING_SCOPE`). `compare_filings` constructs an account per
    call, so `max_calls=1` means "one provider call per comparison",
    never "one provider call in flight" and never "one provider call per
    day". N concurrent comparisons perform up to N provider calls, each
    inside its own budget, and that is the designed behaviour rather
    than a bypass: there is no shared counter to race on, and equally no
    aggregate ceiling. Concurrency therefore cannot evade this
    accounting, and this accounting cannot cap total spend.

    A deployment-wide cost control is a separate, unbuilt requirement.
    It would need durable shared state outside this object, and nothing
    here should be described as providing one."""

    limits: UsageLimits = field(default_factory=UsageLimits)
    calls: int = 0
    input_chars: int = 0
    output_chars: int = 0
    clock: Callable[[], float] = time.monotonic
    _started_at: float | None = None

    def __post_init__(self) -> None:
        if self._started_at is None:
            self._started_at = self.clock()

    def elapsed_seconds(self) -> float:
        return self.clock() - (self._started_at or self.clock())

    def check(self, input_chars: int) -> str | None:
        """Read-only. Returns a reason string when the call must not be
        made, None when it may. Checked BEFORE the provider is called,
        so an over-budget request costs nothing."""
        if self.calls >= self.limits.max_calls:
            return f"call limit of {self.limits.max_calls} reached"
        if input_chars > self.limits.max_input_chars:
            return f"input of {input_chars} chars exceeds limit of {self.limits.max_input_chars}"
        if self.input_chars + input_chars > self.limits.max_input_chars:
            return f"cumulative input would exceed limit of {self.limits.max_input_chars} chars"
        return None

    def record(self, input_chars: int, output_chars: int) -> None:
        self.calls += 1
        self.input_chars += input_chars
        self.output_chars += output_chars

    def snapshot(self) -> dict:
        """Observed usage plus the limits that produced it. The key names
        distinguish what was pre-authorized from what was merely
        requested of the provider, so a reader cannot mistake the output
        cap for a spend control."""
        return {
            "accounting_scope": ACCOUNTING_SCOPE,
            "cache_hit": False,
            "provider_calls": self.calls,
            "input_chars": self.input_chars,
            "output_chars": self.output_chars,
            "max_calls": self.limits.max_calls,
            # Enforced before the call: exceeding it costs nothing.
            "max_input_chars_enforced_precall": self.limits.max_input_chars,
            # Requested of the provider and re-checked afterwards; an
            # oversized response is discarded, not prevented.
            "requested_max_output_chars": self.limits.max_output_chars,
            "output_cap_enforcement": "post_response_rejection",
        }

    def cached_snapshot(self, cached_from_request_id: str) -> dict:
        """Usage for a cache hit: no provider call happened, so none is
        reported. The stored result's own figures are deliberately NOT
        copied forward — re-reporting an earlier request's characters
        here would inflate observed usage and attribute one request's
        spend to another."""
        return {
            "accounting_scope": ACCOUNTING_SCOPE,
            "cache_hit": True,
            "cached_from_request_id": cached_from_request_id,
            "provider_calls": 0,
            "input_chars": 0,
            "output_chars": 0,
            "max_calls": self.limits.max_calls,
            "max_input_chars_enforced_precall": self.limits.max_input_chars,
            "requested_max_output_chars": self.limits.max_output_chars,
            "output_cap_enforcement": "post_response_rejection",
        }


# --- the fake used by every test -----------------------------------------

@dataclass
class FakeComparisonProvider:
    """Deterministic stand-in. Returns a canned payload, or raises a
    chosen ProviderError, and records every request it was given so a
    test can assert on exactly what crossed the boundary — including
    that no instruction from a source document survived into it."""

    payload: dict | None = None
    failure: ProviderFailure | None = None
    model_id: str = "fake-model-v0"
    output_chars: int = 0
    requests: list[ProviderRequest] = field(default_factory=list)

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        if self.failure is not None:
            raise ProviderError(self.failure, "fake provider configured to fail")
        payload = self.payload if self.payload is not None else {}
        rendered = str(payload)
        return ProviderResponse(
            payload=payload,
            input_chars=len(request.system_prompt) + len(request.user_prompt),
            output_chars=self.output_chars or len(rendered),
            model_id=self.model_id,
        )
