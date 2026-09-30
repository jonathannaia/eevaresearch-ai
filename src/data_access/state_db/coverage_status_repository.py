"""Measured Radar coverage per (issuer, lane) — SQLite (Coverage Control
Plane, Milestone 1).

Two tables, deliberately separate:

`instrument_lane_coverage` is CURRENT STATE, one row per
(issuer_id, lane), updated in place. It is bounded by the curated
universe — 129 instruments across three lanes today — and never grows
with time.

`instrument_lane_coverage_events` is HISTORY, and is written only when
something genuinely changed. Ordinary Covered <-> NoData movement is
audit-silent by design: both are the same health class, and recording
every oscillation would turn a bounded table into a per-tick log. A
lane-wide incident (a provider-wide failure, or an EDINET tick whose day
fetches cannot be trusted) is ONE fact about the lane, so it is written
once with `scope="lane"` and `issuer_id=None` rather than fanned out
across every affected instrument.

The invariant this module exists to protect: only Covered and NoData may
advance `last_success_at`. Nothing else may, under any circumstance —
that is what keeps a failed scan from being read later as healthy
"no new filing".

Independent of the Postgres implementation by the same no-dialect-
abstraction convention the rest of this package follows: same external
contract, separately implemented, separately tested."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class InstrumentLaneCoverage:
    """One instrument's measured coverage on one lane, right now.

    Duplicated (not shared) with the Postgres package's identically
    shaped record, exactly as ProviderScanStatus already is — the two
    state_db packages deliberately share no code, and the worker
    duck-types whichever it is handed."""

    issuer_id: str
    lane: str
    expected: bool
    coverage_state: str
    last_attempt_at: str | None
    last_success_at: str | None
    last_no_data_at: str | None
    last_item_at: str | None
    last_material_item_at: str | None
    consecutive_empty_runs: int
    consecutive_failures: int
    failure_class: str | None
    blocking_reason: str
    updated_at: str


@dataclass(frozen=True)
class CoverageEvent:
    """One history row. `issuer_id` is None for a lane-scoped incident."""

    scope: str  # "lane" | "instrument"
    issuer_id: str | None
    lane: str
    from_state: str | None
    to_state: str
    failure_class: str | None
    blocking_reason: str
    detail: str
    at: str

_STATE_COLUMNS = (
    "issuer_id, lane, expected, coverage_state, last_attempt_at, last_success_at, "
    "last_no_data_at, last_item_at, last_material_item_at, consecutive_empty_runs, "
    "consecutive_failures, failure_class, blocking_reason, updated_at"
)


def _row_to_state(row: sqlite3.Row) -> InstrumentLaneCoverage:
    return InstrumentLaneCoverage(
        issuer_id=row["issuer_id"],
        lane=row["lane"],
        expected=bool(row["expected"]),
        coverage_state=row["coverage_state"],
        last_attempt_at=row["last_attempt_at"],
        last_success_at=row["last_success_at"],
        last_no_data_at=row["last_no_data_at"],
        last_item_at=row["last_item_at"],
        last_material_item_at=row["last_material_item_at"],
        consecutive_empty_runs=row["consecutive_empty_runs"],
        consecutive_failures=row["consecutive_failures"],
        failure_class=row["failure_class"],
        blocking_reason=row["blocking_reason"],
        updated_at=row["updated_at"],
    )


def _row_to_event(row: sqlite3.Row) -> CoverageEvent:
    return CoverageEvent(
        scope=row["scope"],
        issuer_id=row["issuer_id"],
        lane=row["lane"],
        from_state=row["from_state"],
        to_state=row["to_state"],
        failure_class=row["failure_class"],
        blocking_reason=row["blocking_reason"],
        detail=row["detail"],
        at=row["at"],
    )


def get_coverage_status(
    conn: sqlite3.Connection, issuer_id: str, lane: str,
) -> InstrumentLaneCoverage | None:
    row = conn.execute(
        f"SELECT {_STATE_COLUMNS} FROM instrument_lane_coverage WHERE issuer_id = ? AND lane = ?",
        (issuer_id, lane),
    ).fetchone()
    return _row_to_state(row) if row is not None else None


def get_all_coverage_statuses(
    conn: sqlite3.Connection,
) -> dict[tuple[str, str], InstrumentLaneCoverage]:
    """Every row, keyed by (issuer_id, lane). One query regardless of
    instrument count — the Coverage page reads the whole table."""
    rows = conn.execute(
        f"SELECT {_STATE_COLUMNS} FROM instrument_lane_coverage ORDER BY lane, issuer_id"
    ).fetchall()
    return {(row["issuer_id"], row["lane"]): _row_to_state(row) for row in rows}


def get_coverage_statuses_for_lane(
    conn: sqlite3.Connection, lane: str,
) -> tuple[InstrumentLaneCoverage, ...]:
    rows = conn.execute(
        f"SELECT {_STATE_COLUMNS} FROM instrument_lane_coverage WHERE lane = ? ORDER BY issuer_id",
        (lane,),
    ).fetchall()
    return tuple(_row_to_state(row) for row in rows)


def upsert_coverage_status(conn: sqlite3.Connection, status: InstrumentLaneCoverage) -> None:
    """Replaces the whole row for this (issuer_id, lane). Idempotent:
    applying the same status twice converges on the same row, which is
    what makes a replayed tick safe."""
    conn.execute(
        """
        INSERT INTO instrument_lane_coverage (
            issuer_id, lane, expected, coverage_state, last_attempt_at, last_success_at,
            last_no_data_at, last_item_at, last_material_item_at, consecutive_empty_runs,
            consecutive_failures, failure_class, blocking_reason, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (issuer_id, lane) DO UPDATE SET
            expected = excluded.expected,
            coverage_state = excluded.coverage_state,
            last_attempt_at = excluded.last_attempt_at,
            last_success_at = excluded.last_success_at,
            last_no_data_at = excluded.last_no_data_at,
            last_item_at = excluded.last_item_at,
            last_material_item_at = excluded.last_material_item_at,
            consecutive_empty_runs = excluded.consecutive_empty_runs,
            consecutive_failures = excluded.consecutive_failures,
            failure_class = excluded.failure_class,
            blocking_reason = excluded.blocking_reason,
            updated_at = excluded.updated_at
        """,
        (
            status.issuer_id, status.lane, 1 if status.expected else 0, status.coverage_state,
            status.last_attempt_at, status.last_success_at, status.last_no_data_at,
            status.last_item_at, status.last_material_item_at, status.consecutive_empty_runs,
            status.consecutive_failures, status.failure_class, status.blocking_reason,
            status.updated_at,
        ),
    )


def record_coverage_event(conn: sqlite3.Connection, event: CoverageEvent) -> None:
    """Appends one history row. Callers decide WHETHER an event is
    warranted (see radar_worker's own transition rules) — this function
    never suppresses one it is given."""
    conn.execute(
        """
        INSERT INTO instrument_lane_coverage_events (
            scope, issuer_id, lane, from_state, to_state, failure_class,
            blocking_reason, detail, at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.scope, event.issuer_id, event.lane, event.from_state, event.to_state,
            event.failure_class, event.blocking_reason, event.detail, event.at,
        ),
    )


def get_coverage_events(
    conn: sqlite3.Connection, lane: str | None = None,
) -> tuple[CoverageEvent, ...]:
    if lane is None:
        rows = conn.execute(
            "SELECT scope, issuer_id, lane, from_state, to_state, failure_class, "
            "blocking_reason, detail, at FROM instrument_lane_coverage_events ORDER BY id"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT scope, issuer_id, lane, from_state, to_state, failure_class, "
            "blocking_reason, detail, at FROM instrument_lane_coverage_events "
            "WHERE lane = ? ORDER BY id",
            (lane,),
        ).fetchall()
    return tuple(_row_to_event(row) for row in rows)
