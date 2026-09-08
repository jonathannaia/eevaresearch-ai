"""Private-beta access foundation, Phase 1 — pure decision logic only (see
design/DECISIONS.md). No Streamlit import, no I/O, no network calls: this
module only turns a Settings snapshot + an optional email into an allow/deny
decision. There is no identity/sign-in wiring yet this phase — callers pass
`email=None` today; the `email` parameter exists so a later phase can supply
a real signed-in identity without changing this function's contract.

The allowlist is authorization only, never authentication — this module
cannot verify that a caller-supplied email actually belongs to the caller.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.config.settings import Settings


class BetaGateReason(str, Enum):
    AUTH_DISABLED = "AUTH_DISABLED"
    SIGN_IN_REQUIRED = "SIGN_IN_REQUIRED"
    ALLOWED_EMAIL = "ALLOWED_EMAIL"
    INVITE_REQUIRED = "INVITE_REQUIRED"
    EMPTY_ALLOWLIST = "EMPTY_ALLOWLIST"


@dataclass(frozen=True)
class BetaGateDecision:
    allowed: bool
    reason: BetaGateReason


def evaluate_beta_gate(settings: Settings, email: str | None) -> BetaGateDecision:
    if not settings.private_beta_auth_enabled:
        return BetaGateDecision(allowed=True, reason=BetaGateReason.AUTH_DISABLED)

    normalized_email = (email or "").strip().lower()
    if not normalized_email:
        return BetaGateDecision(allowed=False, reason=BetaGateReason.SIGN_IN_REQUIRED)

    if not settings.private_beta_allowed_emails:
        return BetaGateDecision(allowed=False, reason=BetaGateReason.EMPTY_ALLOWLIST)

    if normalized_email in settings.private_beta_allowed_emails:
        return BetaGateDecision(allowed=True, reason=BetaGateReason.ALLOWED_EMAIL)

    return BetaGateDecision(allowed=False, reason=BetaGateReason.INVITE_REQUIRED)
