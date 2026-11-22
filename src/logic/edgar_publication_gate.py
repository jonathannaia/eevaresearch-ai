"""Whether one extracted EDGAR candidate may be published autonomously.

Deny by default, and restrictive at every layer. Five conditions must
ALL hold; any one absent, unreadable or unrecognized means no.

    1. the lane is SEC EDGAR
    2. the filing's form type is 424B5
    3. the existing deterministic policy already routed it to PUBLISH
    4. the deployment's explicit setting is on
    5. the database-backed control is not off

This exists because the previous safety mechanism was a blanket
`edgar_auto_publish_enabled=False` forced inside the worker. That was
correct while nothing could publish, but unlocking it wholesale would
admit EVERY future `SignalRoute.PUBLISH` — not just the one cohort
anybody reviewed. A rule added later for an 8-K, or a widened 424B5
pattern, would start publishing the moment it shipped, silently. This
module is the narrowing: the cohort is named here, in code, and a route
alone is never sufficient.

Pure: no I/O, no database, no settings lookup of its own. Callers hand
it the facts, which keeps the decision testable without a worker, a
connection, or an environment.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.data_access.edgar.edgar_rules import normalize_form_type
from src.logic.signal_decision_policy import SignalRoute

# The cohort, stated once. Widening it is a deliberate code change that
# shows up in review as a change to this line, not as a side effect of
# adding a rule somewhere else.
AUTONOMOUS_PUBLICATION_LANE = "SEC EDGAR"
AUTONOMOUS_PUBLICATION_FORMS: frozenset[str] = frozenset({"424B5"})

# Reasons are a closed vocabulary. They are safe to log and safe to show
# an operator: no issuer, document id, URL, exception text or database
# detail can travel through them.
REASON_ALLOWED = "allowed"
REASON_LANE_NOT_ELIGIBLE = "lane_not_eligible"
REASON_FORM_NOT_IN_COHORT = "form_not_in_cohort"
REASON_ROUTE_NOT_PUBLISH = "route_not_publish"
REASON_SETTING_DISABLED = "setting_disabled"
REASON_CONTROL_OFF = "control_off"


@dataclass(frozen=True)
class PublicationGateDecision:
    allowed: bool
    reason: str


def may_publish_autonomously(
    *,
    source_name: str | None,
    form_type: str | None,
    route: SignalRoute | None,
    setting_enabled: bool,
    control_enabled: bool,
) -> PublicationGateDecision:
    """The whole gate. Ordered so the most specific denial wins, which
    makes an operator's "why did this not publish?" answerable from one
    reason string.

    `control_enabled` is what the caller resolved from the database
    control; the caller is responsible for passing False on any error,
    absence or malformed row (see edgar_publication_control_repository).
    This function never reads it itself, so it cannot be the place a
    database failure is accidentally treated as permission."""
    if (source_name or "") != AUTONOMOUS_PUBLICATION_LANE:
        return PublicationGateDecision(False, REASON_LANE_NOT_ELIGIBLE)
    if normalize_form_type(form_type or "") not in AUTONOMOUS_PUBLICATION_FORMS:
        return PublicationGateDecision(False, REASON_FORM_NOT_IN_COHORT)
    if route is not SignalRoute.PUBLISH:
        return PublicationGateDecision(False, REASON_ROUTE_NOT_PUBLISH)
    if not setting_enabled:
        return PublicationGateDecision(False, REASON_SETTING_DISABLED)
    if not control_enabled:
        return PublicationGateDecision(False, REASON_CONTROL_OFF)
    return PublicationGateDecision(True, REASON_ALLOWED)
