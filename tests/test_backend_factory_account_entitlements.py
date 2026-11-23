"""Paid professional access, phase 1 — the entitlement repository's
three-backend selection and behavioural parity
(src/data_access/backend_factory.py).

The JSON backend exists for local development, same as every other
repository in that module. It is included here rather than trusted,
because the gate's security properties must not depend on which
backend is configured: a JSON store that admitted everyone while
Postgres denied them would make local testing actively misleading.

Postgres is covered by its own file against a real container
(test_account_entitlement_repository_postgres.py); the parity asserted
here is between SQLite and JSON, which both run without one."""
from __future__ import annotations

import dataclasses

import pytest

from src.config.settings import Settings
from src.data_access import backend_factory
from src.models.account_access import (
    AccessRequest,
    AccessStatus,
    AccessTier,
    EntitlementConflictError,
)

_T1 = "2026-06-01T10:00:00+00:00"
_T2 = "2026-06-02T10:00:00+00:00"
_USER = "analyst@example.test"
_ADMIN = "owner@example.test"


def _settings(tmp_path, backend: str) -> Settings:
    overrides = {"db_backend": backend, "cache_dir": tmp_path}
    if backend == "sqlite":
        overrides["state_db_path"] = tmp_path / "state.db"
    return dataclasses.replace(Settings(), **overrides)


def _repository(tmp_path, backend: str):
    return backend_factory.get_account_entitlement_repository(_settings(tmp_path, backend))


# Both run with no external service. "json" is also what an unset
# EDGE_DB_BACKEND resolves to, so it is the backend a developer gets by
# default — which is exactly why it is held to the same contract.
_BACKENDS = ("sqlite", "json")


def test_an_unset_backend_resolves_to_the_json_store(tmp_path):
    repository = backend_factory.get_account_entitlement_repository(
        dataclasses.replace(Settings(), db_backend="", cache_dir=tmp_path)
    )
    assert isinstance(repository, backend_factory.JsonAccountEntitlementRepository)


@pytest.mark.parametrize("backend", _BACKENDS)
def test_an_unknown_account_has_no_entitlement_in_every_backend(tmp_path, backend):
    """The property the whole gate rests on. An empty store denies
    everyone — the opposite of the open beta's empty allowlist."""
    assert _repository(tmp_path, backend).get_entitlement("nobody@example.test") is None


@pytest.mark.parametrize("backend", _BACKENDS)
def test_a_missing_store_file_is_not_an_error_it_is_no_access(tmp_path, backend):
    """A fresh deployment has no rows. That must read as "nobody is
    entitled", not as a crash — the distinction between absent and
    unreadable is the caller's to make, and absent is not a failure."""
    repository = _repository(tmp_path, backend)
    assert repository.get_entitlement(_USER) is None
    assert repository.list_entitlements() == []


@pytest.mark.parametrize("backend", _BACKENDS)
def test_the_request_approve_cycle_behaves_identically(tmp_path, backend):
    repository = _repository(tmp_path, backend)
    requested = repository.request_access(
        email="Analyst@Example.Test",
        request=AccessRequest(reason="Tracking memory capex.", organisation="Example Capital", role="analyst"),
        now=_T1,
    )
    assert requested.email == _USER, "every backend normalizes the email"
    assert requested.status is AccessStatus.REQUESTED and requested.tier is None

    queue = repository.list_entitlements(statuses=(AccessStatus.REQUESTED,))
    assert [e.email for e in queue] == [_USER]

    approved = repository.set_status(
        email=_USER, status=AccessStatus.ACTIVE, tier=AccessTier.TRIAL, actor=_ADMIN,
        now=_T2, expires_at="2026-06-16T10:00:00+00:00", detail="approved",
        expected_status=AccessStatus.REQUESTED,
    )
    assert approved.status is AccessStatus.ACTIVE
    assert approved.tier is AccessTier.TRIAL
    assert approved.decided_by == _ADMIN
    assert approved.expires_at == "2026-06-16T10:00:00+00:00"
    assert approved.requested_at == _T1, "the request survives the decision in every backend"

    assert repository.list_entitlements(statuses=(AccessStatus.REQUESTED,)) == []


@pytest.mark.parametrize("backend", _BACKENDS)
def test_the_audit_trail_is_written_by_every_backend(tmp_path, backend):
    repository = _repository(tmp_path, backend)
    repository.request_access(_USER, AccessRequest(reason="Evaluating."), _T1)
    repository.set_status(
        email=_USER, status=AccessStatus.ACTIVE, tier=AccessTier.PAID,
        actor=_ADMIN, now=_T2, detail="approved",
    )
    events = repository.list_events(_USER)
    assert [(e.from_status, e.to_status) for e in events] == [
        (None, AccessStatus.REQUESTED),
        (AccessStatus.REQUESTED, AccessStatus.ACTIVE),
    ]
    assert events[1].actor == _ADMIN


@pytest.mark.parametrize("backend", _BACKENDS)
def test_every_backend_raises_the_same_conflict_type(tmp_path, backend):
    """A caller writing `except EntitlementConflictError` cannot know
    which backend is configured. A per-backend exception class would
    mean the admin page's conflict handling silently stopped working
    the day production moved from one to the other."""
    repository = _repository(tmp_path, backend)
    repository.request_access(_USER, AccessRequest(reason="Evaluating."), _T1)
    repository.set_status(
        email=_USER, status=AccessStatus.ACTIVE, tier=AccessTier.PAID, actor=_ADMIN, now=_T2
    )
    with pytest.raises(EntitlementConflictError):
        repository.request_access(_USER, AccessRequest(reason="Again."), _T2)
    with pytest.raises(EntitlementConflictError):
        repository.set_status(
            email=_USER, status=AccessStatus.DENIED, tier=None, actor=_ADMIN, now=_T2,
            expected_status=AccessStatus.REQUESTED,
        )


@pytest.mark.parametrize("backend", _BACKENDS)
def test_an_empty_status_filter_returns_nothing_in_every_backend(tmp_path, backend):
    repository = _repository(tmp_path, backend)
    repository.request_access(_USER, AccessRequest(reason="Evaluating."), _T1)
    assert repository.list_entitlements(statuses=()) == []
    assert len(repository.list_entitlements(statuses=None)) == 1


def test_the_json_store_rejects_an_unrecognized_status_rather_than_defaulting(tmp_path):
    """Same discipline as both database backends: access is never
    granted because a stored value failed to parse. Asserted separately
    because corrupting the store is backend-specific."""
    import json

    repository = _repository(tmp_path, "json")
    repository.request_access(_USER, AccessRequest(reason="Evaluating."), _T1)
    path = tmp_path / "account_entitlements.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["entitlements"][_USER]["status"] = "superuser"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        repository.get_entitlement(_USER)
