"""Provider scan-status/cursor persistence (Durable-State Phase 4M-0) —
the isolated Postgres counterpart to
state_db/scan_status_repository.py, identical shape, independently
implemented (no shared code, per this package's existing
no-dialect-abstraction constraint). See that module's own docstring for
the full design rationale."""
from __future__ import annotations

from dataclasses import dataclass

import psycopg

from src.data_access.postgres_state_db.connection import transaction


# What one provider tick established about ISSUER COVERAGE — derived from
# recorded facts, never asserted by a writer.
#
# These are coverage outcomes, not request outcomes. The counts below
# measure tracked issuers, so an outcome never claims anything about how
# many HTTP requests a provider made or how many of them failed.
SCAN_OUTCOME_FULL_COVERAGE = "full_coverage"        # every eligible issuer observed
SCAN_OUTCOME_PARTIAL_COVERAGE = "partial_coverage"  # some, but not all, observed
SCAN_OUTCOME_NO_COVERAGE = "no_coverage"            # none observed, though work was eligible
SCAN_OUTCOME_NO_WORK = "no_work"                    # nothing was eligible to scan
SCAN_OUTCOME_RAISED = "raised"                      # the scan call itself raised
SCAN_OUTCOME_UNKNOWN = "unknown"                    # coverage was not recorded for this attempt
SCAN_OUTCOME_INVALID = "invalid"                    # the recorded counts contradict each other

# Why coverage could not be described for an attempt, recorded durably on
# the row instead of being left to a log line on an ephemeral disk.
#
# A fixed, sanitized code chosen by the writer from this module -- never
# provider-supplied text, never a parsed warning string. It exists so a
# provider that returns a self-contradictory report stays distinguishable
# from a row whose coverage was simply never recorded; both would
# otherwise read as `unknown`.
COVERAGE_VALIDATION_OBSERVED_NOT_SUBSET = "observed_not_subset"

# Outcomes that mean the latest ATTEMPT did not establish full coverage.
# Freshness is about the last SUCCESS; these are about the last attempt,
# and an operator needs both — a provider can hold a recent successful
# timestamp while its most recent attempt observed nothing at all.
_ATTEMPT_SHORTFALL_OUTCOMES = frozenset({
    SCAN_OUTCOME_PARTIAL_COVERAGE, SCAN_OUTCOME_NO_COVERAGE,
    SCAN_OUTCOME_NO_WORK, SCAN_OUTCOME_RAISED, SCAN_OUTCOME_UNKNOWN,
    SCAN_OUTCOME_INVALID,
})


@dataclass(frozen=True)
class ProviderScanStatus:
    provider: str
    cursor_value: str | None
    started_at: str | None
    completed_at: str | None
    last_successful_at: str | None
    items_discovered: int
    candidates_created: int
    skipped_unresolved_count: int
    failure_code: str | None
    updated_at: str
    # Issuer coverage for the LATEST ATTEMPT. Both are counts of tracked
    # issuers, taken from the scan report's own structured tuples -- never
    # from parsing a warning string.
    #
    #   issuers_eligible: tracked issuers this attempt was expected to cover
    #                     (len(report.companies))
    #   issuers_observed: tracked issuers it actually observed
    #                     (len(report.observed_companies))
    #
    # `observed_companies` is each provider's own "we looked properly"
    # set: it INCLUDES an issuer that legitimately had nothing new and
    # EXCLUDES one whose identity did not resolve or whose fetch failed.
    #
    # EDINET deliberately reports this all-or-nothing, because its scan is
    # day-keyed: it asks each calendar day for every filer and infers an
    # issuer's absence from that day's rows, so one failed day makes the
    # whole day-range unsound. `no_coverage` for EDINET therefore means
    # "coverage could not be established for this day range" -- NOT that
    # every request failed, and NOT that every issuer failed individually.
    #
    # None means NOT RECORDED, which is what every row written before this
    # change carries. It is deliberately distinct from 0: unknown coverage
    # must never be read as proven absence of work.
    issuers_eligible: int | None = None
    issuers_observed: int | None = None
    # Set when this attempt's coverage could not be described at all --
    # e.g. the report's observed issuers were not a subset of its
    # eligible ones. Carries one of this module's COVERAGE_VALIDATION_*
    # codes, which is why the counts above are NULL: it records WHY they
    # are unknown, so `invalid` stays distinguishable from `unknown`.
    # None on every attempt whose coverage was describable, including one
    # that legitimately covered nothing.
    coverage_validation_code: str | None = None

    @property
    def outcome(self) -> str:
        """What this row establishes about the latest attempt's coverage.

        Pure: a function of stored fields only, no clock and no I/O, so a
        row can never claim an outcome its own counts contradict.

        Precedence, most authoritative first:
          1. `failure_code` -- set only when the scan call RAISED, which
             cannot be described in terms of coverage never determined.
          2. `coverage_validation_code` -- the writer established that
             this attempt's coverage was not describable. Checked before
             the counts because those are NULL in exactly this case, and
             NULL alone would read as the much weaker `unknown`.
          3. the counts, with unknown before any count rule so a
             pre-change row is never reinterpreted.
        """
        if self.failure_code is not None:
            return SCAN_OUTCOME_RAISED
        if self.coverage_validation_code is not None:
            return SCAN_OUTCOME_INVALID
        if self.issuers_eligible is None or self.issuers_observed is None:
            return SCAN_OUTCOME_UNKNOWN
        # Invariant: observed issuers are a subset of eligible ones, so the
        # observed count can never exceed it and neither can be negative.
        # A row that violates this is reported as invalid rather than
        # being rounded into any success state.
        if (
            self.issuers_eligible < 0
            or self.issuers_observed < 0
            or self.issuers_observed > self.issuers_eligible
        ):
            return SCAN_OUTCOME_INVALID
        if self.issuers_eligible == 0:
            return SCAN_OUTCOME_NO_WORK
        if self.issuers_observed == 0:
            return SCAN_OUTCOME_NO_COVERAGE
        if self.issuers_observed < self.issuers_eligible:
            return SCAN_OUTCOME_PARTIAL_COVERAGE
        return SCAN_OUTCOME_FULL_COVERAGE

    @property
    def issuers_unobserved(self) -> int | None:
        """Eligible issuers this attempt did not observe. None when
        coverage was not recorded, because a shortfall is not derivable
        from unknown counts."""
        if self.issuers_eligible is None or self.issuers_observed is None:
            return None
        if self.outcome == SCAN_OUTCOME_INVALID:
            return None
        return self.issuers_eligible - self.issuers_observed

    @property
    def latest_attempt_fell_short(self) -> bool:
        """Whether the LATEST ATTEMPT failed to establish full coverage.

        Independent of freshness on purpose: `last_successful_at` records
        the last success, and a provider may hold a recent one while its
        most recent attempt covered nothing.
        """
        return self.outcome in _ATTEMPT_SHORTFALL_OUTCOMES

    @property
    def advances_last_successful(self) -> bool:
        """Whether an attempt recording these counts may move
        last_successful_at. At least one observed issuer is required, and
        the counts must be internally consistent."""
        return self.outcome in (SCAN_OUTCOME_FULL_COVERAGE, SCAN_OUTCOME_PARTIAL_COVERAGE)


def _row_to_status(row: dict) -> ProviderScanStatus:
    return ProviderScanStatus(
        provider=row["provider"],
        cursor_value=row["cursor_value"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        last_successful_at=row["last_successful_at"],
        items_discovered=row["items_discovered"],
        candidates_created=row["candidates_created"],
        skipped_unresolved_count=row["skipped_unresolved_count"],
        failure_code=row["failure_code"],
        updated_at=row["updated_at"],
        issuers_eligible=row["issuers_eligible"],
        issuers_observed=row["issuers_observed"],
        coverage_validation_code=row["coverage_validation_code"],
    )


def get_scan_status(conn: psycopg.Connection, provider: str) -> ProviderScanStatus | None:
    row = conn.execute(
        "SELECT * FROM provider_scan_status WHERE provider = %s", (provider,),
    ).fetchone()
    return _row_to_status(row) if row is not None else None


def get_all_scan_statuses(conn: psycopg.Connection) -> dict[str, ProviderScanStatus]:
    rows = conn.execute("SELECT * FROM provider_scan_status").fetchall()
    return {row["provider"]: _row_to_status(row) for row in rows}


def upsert_scan_status(conn: psycopg.Connection, status: ProviderScanStatus) -> None:
    """Insert-or-replace by `provider` — the worker always writes the
    complete, current record for that provider in one call; there is no
    partial-field update."""
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO provider_scan_status (
                provider, cursor_value, started_at, completed_at, last_successful_at,
                items_discovered, candidates_created, skipped_unresolved_count, failure_code, updated_at,
                issuers_eligible, issuers_observed, coverage_validation_code
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (provider) DO UPDATE SET
                cursor_value = excluded.cursor_value,
                started_at = excluded.started_at,
                completed_at = excluded.completed_at,
                last_successful_at = excluded.last_successful_at,
                items_discovered = excluded.items_discovered,
                candidates_created = excluded.candidates_created,
                skipped_unresolved_count = excluded.skipped_unresolved_count,
                failure_code = excluded.failure_code,
                updated_at = excluded.updated_at,
                issuers_eligible = excluded.issuers_eligible,
                issuers_observed = excluded.issuers_observed,
                coverage_validation_code = excluded.coverage_validation_code
            """,
            (
                status.provider, status.cursor_value, status.started_at, status.completed_at,
                status.last_successful_at, status.items_discovered, status.candidates_created,
                status.skipped_unresolved_count, status.failure_code, status.updated_at,
                status.issuers_eligible, status.issuers_observed,
                status.coverage_validation_code,
            ),
        )
