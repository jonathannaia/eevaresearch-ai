"""Paid professional access, phase 1 — the access decision
(src/logic/account_access.py). Pure: no database, no Streamlit, no
clock. Every test constructs the entitlement it is about.

The decision is the security boundary for the whole feature, so these
tests are written to pin down the two properties that matter more than
any individual branch: nothing grants access except an ACTIVE,
unexpired, tiered entitlement; and a failure to read the store is never
answered as "this account has no entitlement"."""
from __future__ import annotations

import dataclasses

import pytest

from src.config.settings import Settings
from src.logic.account_access import AccessDecision, AccessReason, evaluate_account_access
from src.models.account_access import AccessStatus, AccessTier, AccountEntitlement

_NOW = "2026-06-01T12:00:00+00:00"
_USER = "analyst@example.test"
_ADMIN = "owner@example.test"


def _settings(**overrides) -> Settings:
    base = Settings()
    defaults = {
        "access_enforcement_enabled": True,
        "admin_emails": frozenset({_ADMIN}),
        "private_beta_allowed_emails": frozenset(),
    }
    return dataclasses.replace(base, **{**defaults, **overrides})


def _entitlement(**overrides) -> AccountEntitlement:
    base = {
        "email": _USER,
        "status": AccessStatus.ACTIVE,
        "tier": AccessTier.PAID,
        "requested_at": None,
        "decided_at": _NOW,
        "decided_by": _ADMIN,
        "expires_at": None,
        "updated_at": _NOW,
    }
    return AccountEntitlement(**{**base, **overrides})


# --- the two properties that matter most ------------------------------------

@pytest.mark.parametrize("status", [s for s in AccessStatus if s is not AccessStatus.ACTIVE])
def test_no_status_other_than_active_ever_grants_access(status):
    """Exhaustive over the enum, so adding a status without deciding
    what it means cannot accidentally grant access — the new member
    arrives in this parametrize list and has to be dealt with."""
    decision = evaluate_account_access(_settings(), _USER, _entitlement(status=status), _NOW)
    assert decision.allowed is False, status


def test_an_unreadable_store_is_not_reported_as_no_entitlement():
    """The distinction this feature would be dishonest without. Telling
    somebody their request was never received, when in fact a database
    read failed, is a worse error than saying nothing specific."""
    read_failed = evaluate_account_access(
        _settings(), _USER, None, _NOW, entitlement_read_failed=True
    )
    genuinely_absent = evaluate_account_access(_settings(), _USER, None, _NOW)

    assert read_failed.allowed is False and genuinely_absent.allowed is False
    assert read_failed.reason is AccessReason.ENTITLEMENT_UNREADABLE
    assert genuinely_absent.reason is AccessReason.NO_ENTITLEMENT
    assert read_failed.reason is not genuinely_absent.reason


# --- dormant by default -----------------------------------------------------

def test_enforcement_is_off_by_default_so_the_migration_changes_nothing():
    """The property that makes this deployable. A real Settings() with
    nothing configured must let an account with no entitlement row
    through, because on the deploy that creates the table every account
    has no row."""
    assert Settings().access_enforcement_enabled is False
    decision = evaluate_account_access(
        dataclasses.replace(Settings(), admin_emails=frozenset()), _USER, None, _NOW
    )
    assert decision.allowed is True
    assert decision.reason is AccessReason.ENFORCEMENT_DISABLED


def test_enforcement_disabled_allows_even_a_revoked_account():
    """Dormant means dormant: while the flag is off the entitlement
    table is recorded state and nothing more. A revoked row must not
    start denying access before enforcement is deliberately switched
    on, or the flag would not actually be a flag."""
    decision = evaluate_account_access(
        _settings(access_enforcement_enabled=False),
        _USER, _entitlement(status=AccessStatus.REVOKED), _NOW,
    )
    assert decision.allowed is True and decision.reason is AccessReason.ENFORCEMENT_DISABLED


# --- administrators ---------------------------------------------------------

def test_an_admin_is_allowed_in_during_a_total_entitlement_outage():
    """The operator's remedy must stay reachable. admin_emails comes
    from the environment, so it still resolves when the database does
    not — without this, enforcement plus a database incident locks the
    one person who can fix it out of the page that fixes it."""
    decision = evaluate_account_access(
        _settings(), _ADMIN, None, _NOW, entitlement_read_failed=True
    )
    assert decision.allowed is True and decision.reason is AccessReason.ADMIN


def test_admin_bypass_is_case_and_whitespace_insensitive():
    decision = evaluate_account_access(_settings(), f"  {_ADMIN.upper()} ", None, _NOW)
    assert decision.allowed is True and decision.reason is AccessReason.ADMIN


def test_a_non_admin_is_not_rescued_by_the_admin_path():
    decision = evaluate_account_access(_settings(), _USER, None, _NOW)
    assert decision.allowed is False and decision.reason is AccessReason.NO_ENTITLEMENT


# --- break-glass allowlist --------------------------------------------------

def test_the_env_allowlist_still_works_as_break_glass():
    """Restoring one person's access without a database write, for the
    case where the database write is what is broken."""
    decision = evaluate_account_access(
        _settings(private_beta_allowed_emails=frozenset({_USER})),
        _USER, None, _NOW, entitlement_read_failed=True,
    )
    assert decision.allowed is True and decision.reason is AccessReason.BREAK_GLASS_ALLOWLIST


def test_break_glass_does_not_apply_while_enforcement_is_off():
    """Not a behaviour claim about the allowlist so much as an ordering
    one: with enforcement off, nobody reaches the allowlist branch, so
    the reason reported is always ENFORCEMENT_DISABLED. Pinned because
    the reason strings drive what the UI says."""
    decision = evaluate_account_access(
        _settings(access_enforcement_enabled=False, private_beta_allowed_emails=frozenset({_USER})),
        _USER, None, _NOW,
    )
    assert decision.reason is AccessReason.ENFORCEMENT_DISABLED


# --- identity ---------------------------------------------------------------

@pytest.mark.parametrize("email", [None, "", "   "])
def test_no_identity_is_sign_in_required_not_no_entitlement(email):
    decision = evaluate_account_access(_settings(), email, None, _NOW)
    assert decision.allowed is False and decision.reason is AccessReason.SIGN_IN_REQUIRED


# --- the granting path ------------------------------------------------------

@pytest.mark.parametrize("tier", list(AccessTier))
def test_an_active_tiered_entitlement_grants_access_and_reports_its_tier(tier):
    decision = evaluate_account_access(_settings(), _USER, _entitlement(tier=tier), _NOW)
    assert decision.allowed is True
    assert decision.reason is AccessReason.ENTITLED
    assert decision.tier is tier


def test_active_with_no_tier_is_malformed_not_a_guessed_trial():
    """The app would not know what to show. Guessing TRIAL would hand
    somebody an arbitrary entitlement nobody granted."""
    decision = evaluate_account_access(_settings(), _USER, _entitlement(tier=None), _NOW)
    assert decision.allowed is False and decision.reason is AccessReason.ENTITLEMENT_MALFORMED


# --- expiry -----------------------------------------------------------------

def test_a_future_expiry_still_grants_access():
    decision = evaluate_account_access(
        _settings(), _USER,
        _entitlement(tier=AccessTier.TRIAL, expires_at="2026-06-15T00:00:00+00:00"), _NOW,
    )
    assert decision.allowed is True and decision.reason is AccessReason.ENTITLED


def test_a_past_expiry_denies_even_though_the_stored_status_says_active():
    """Nothing sweeps expiries on a schedule, so expiry is evaluated at
    decision time. Without this a trial would end whenever a job next
    ran, not when it actually ran out."""
    decision = evaluate_account_access(
        _settings(), _USER,
        _entitlement(tier=AccessTier.TRIAL, expires_at="2026-05-01T00:00:00+00:00"), _NOW,
    )
    assert decision.allowed is False and decision.reason is AccessReason.ENTITLEMENT_EXPIRED
    assert decision.tier is AccessTier.TRIAL, "the lapsed tier is reported so the UI can name it"


def test_expiry_exactly_at_now_is_expired():
    decision = evaluate_account_access(
        _settings(), _USER, _entitlement(tier=AccessTier.TRIAL, expires_at=_NOW), _NOW
    )
    assert decision.allowed is False and decision.reason is AccessReason.ENTITLEMENT_EXPIRED


@pytest.mark.parametrize("expires_at", [
    "not-a-timestamp",
    "2026-13-45T99:99:99+00:00",
    "",
    "2026-06-15T00:00:00",  # naive, against an aware `now`
])
def test_an_unusable_expiry_fails_closed_rather_than_never_expiring(expires_at):
    """Except for the empty string, which means "no expiry" — the
    distinction is between absent and corrupt. Treating a corrupt
    timestamp as "never expires" would turn a data problem into
    indefinite free access."""
    decision = evaluate_account_access(
        _settings(), _USER, _entitlement(tier=AccessTier.TRIAL, expires_at=expires_at), _NOW
    )
    if expires_at == "":
        assert decision.allowed is True, "absent expiry means no expiry"
    else:
        assert decision.allowed is False and decision.reason is AccessReason.ENTITLEMENT_EXPIRED


def test_timezone_offsets_are_compared_as_instants_not_strings():
    """09:00-05:00 is 14:00Z, which is after the 12:00Z `now`, even
    though the string '2026-06-01T09:00:00-05:00' sorts before it.
    String comparison would get this backwards and expire a live
    entitlement."""
    decision = evaluate_account_access(
        _settings(), _USER,
        _entitlement(tier=AccessTier.PAID, expires_at="2026-06-01T09:00:00-05:00"), _NOW,
    )
    assert decision.allowed is True


# --- the decision object cannot lie ----------------------------------------

def test_a_decision_that_contradicts_its_own_reason_is_not_constructible():
    """An AccessDecision(allowed=True) carrying a denial reason would be
    a silent security bug, so it raises at construction instead."""
    with pytest.raises(ValueError):
        AccessDecision(True, AccessReason.NO_ENTITLEMENT)
    with pytest.raises(ValueError):
        AccessDecision(False, AccessReason.ENTITLED)


@pytest.mark.parametrize("reason", list(AccessReason))
def test_every_reason_is_reachable_with_a_consistent_decision(reason):
    """Each reason can construct a decision with exactly one truth
    value, so the allow/deny set stays exhaustive over the enum."""
    allowed = [value for value in (True, False) if _constructs(reason, value)]
    assert len(allowed) == 1, f"{reason} is ambiguous or unconstructible"


def _constructs(reason: AccessReason, allowed: bool) -> bool:
    try:
        AccessDecision(allowed, reason)
    except ValueError:
        return False
    return True
