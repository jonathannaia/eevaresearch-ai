"""Human-review decision recording (Stage 2A) — a single, source-agnostic
seam a future Radar Inbox UI action calls into. Pure orchestration over
the existing candidate_store API; no source-specific logic, mirroring
signal_promotion.py's own separation of concerns.

Reuses the two currently-unused CandidateSignal fields identified in the
Stage 1 audit — `reviewed_at`/`reviewed_note` — finally writing them.
Every decision is additive: a new StateTransition is appended, never
replacing prior history, so a candidate's full review trail (including
any earlier reviewer decision this function itself recorded) stays
intact — changing a decision later is expected, not prevented.

Durable-State Phase 2B: `settings` is an additive, optional parameter.
Omitted (the default, and every existing caller before this phase),
behavior is byte-for-byte identical to before — reads/writes go straight
through `candidate_store.py` against `cache_dir`/`filename`, exactly as
always. Supplied with `settings.db_backend == "sqlite"`, this function
instead reads/writes through `backend_factory.get_candidate_repository()`
— the SQLite-backed store — using `expected_version` for the same
optimistic-concurrency guarantee `state_db.candidate_repository.
update_candidate()` already provides: a stale concurrent write is
rejected explicitly (`conflict`), never silently overwritten.

Durable-State Phase 4J-1 extends the same repository-backed branch to
`settings.db_backend == "postgres"` — no new logic, since
`backend_factory.get_candidate_repository()` already returns a
`PostgresCandidateRepository` for that backend, exposing the identical
`get_candidate`/`get_candidate_version`/`update_candidate` shape as its
SQLite counterpart. Selection stays explicit and test-only: reachable
only when a caller passes an explicit `settings` with
`db_backend="postgres"` and a non-empty `state_db_url` (see
`backend_factory._require_postgres_connection`'s own fail-closed
behavior otherwise) — this function never calls `get_settings()` and
never reads an ambient `EDGE_*` environment variable itself. Any other
`settings.db_backend` value (unset/blank/"json"/unrecognized) behaves
exactly like `settings=None`."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.dart import candidate_store
from src.models.models import CandidateSignal, CandidateStatus, StateTransition

# The only statuses a human reviewer may set through this function. Any
# other CandidateStatus (pipeline-internal states, or the automated
# NOT_MATERIAL gate) is rejected before anything is loaded or written —
# fail closed, never guess which status was intended.
_PERMITTED_REVIEWER_STATUSES = frozenset({
    CandidateStatus.PUBLISHED, CandidateStatus.MONITORING, CandidateStatus.DISMISSED,
})


def record_review_decision(
    cache_dir: Path,
    candidate_id: str,
    filename: str,
    status: CandidateStatus,
    note: str = "",
    *,
    settings: Settings | None = None,
) -> CandidateSignal | None:
    """Records one human reviewer decision for one already-persisted
    candidate. `filename` is the caller's responsibility (same routing
    the existing "Prepare analyst view" action already does by candidate
    ID prefix — see radar_inbox.py) — this function has no source
    awareness of its own, matching candidate_store.py's own design.

    `settings` is additive and optional (Durable-State Phase 2B; extended
    to Postgres in Phase 4J-1) — see this module's own docstring.
    Omitted, this function is byte-for-byte identical to its
    pre-Phase-2B behavior, reading/writing through `cache_dir`/`filename`
    directly. Supplied with `settings.db_backend` of `"sqlite"` or
    `"postgres"`, `cache_dir`/`filename` are used only to derive the
    source (via `backend_factory.source_for_candidate_filename`); the
    actual read/write goes through the matching backend-specific
    candidate repository, with optimistic-concurrency protection in
    both cases.

    Returns the updated CandidateSignal, or None if `candidate_id` isn't
    found — no write occurs in that case. On the SQLite/Postgres path,
    None is also returned (and no write occurs) if a concurrent writer
    already changed the candidate since this call last read it (an
    optimistic-concurrency conflict) — the newer, concurrent decision is
    never overwritten; the caller's existing "None means show an error,
    don't treat as success" handling (see radar_inbox.py) already covers
    this case correctly without a new return shape. Raises ValueError,
    before any load or write, if `status` isn't one of the three
    permitted reviewer outcomes."""
    if status not in _PERMITTED_REVIEWER_STATUSES:
        raise ValueError(
            "record_review_decision only accepts a reviewer outcome of "
            "PUBLISHED, MONITORING, or DISMISSED — got "
            f"{status!r}."
        )

    now = datetime.now(timezone.utc).isoformat()
    detail = note if note else f"Reviewer decision: {status.value}"

    backend = (settings.db_backend or "json").strip().lower() if settings is not None else "json"
    use_repository_backend = backend in ("sqlite", "postgres")
    if use_repository_backend:
        source = backend_factory.source_for_candidate_filename(filename)
        repo = backend_factory.get_candidate_repository(settings, source)
        # Version captured from THIS call's own read, before any other
        # mutation — passed explicitly to update_candidate() below so the
        # write is a real compare-and-swap against what this caller
        # actually observed, not a version re-fetched fresh at write
        # time (which would provide no conflict protection at all; see
        # SqliteCandidateRepository's own docstring).
        candidate = repo.get_candidate(candidate_id)
        if candidate is None:
            return None
        expected_version = repo.get_candidate_version(candidate_id)
        candidate.status = status
        candidate.reviewed_at = now
        candidate.reviewed_note = note
        candidate.state_history.append(StateTransition(status=status, at=now, detail=detail))
        outcome = repo.update_candidate(candidate, expected_version=expected_version)
        return outcome.current if outcome.status == "updated" else None

    store = candidate_store.load_candidates(cache_dir, filename)
    candidate = store.get(candidate_id)
    if candidate is None:
        return None

    candidate.status = status
    candidate.reviewed_at = now
    candidate.reviewed_note = note
    candidate.state_history.append(StateTransition(status=status, at=now, detail=detail))

    candidate_store.update_candidate(cache_dir, candidate, filename)
    return candidate
