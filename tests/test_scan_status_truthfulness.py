"""Scan-status truthfulness: a provider tick's recorded outcome must match
what the scan actually observed.

Before this, `_run_provider_tick`'s success branch wrote
`last_successful_at=completed_at, failure_code=None` whenever
`service.run_scan(...)` RETURNED — regardless of what it returned. Only a
raised exception produced a failure row. Per-company and per-day failures
are caught inside the scan and returned normally, so a tick in which every
issuer failed recorded a clean, current success.

The fix stores two facts taken from the report's own structured tuples —
`len(companies)` attempted and `len(observed_companies)` observed — and
derives the outcome from them. `observed_companies` is the providers' own
"we looked properly" set: it includes an issuer that legitimately had
nothing new and excludes one whose identity did not resolve or whose fetch
failed. For EDINET it is all-or-nothing because that scan is day-keyed, so
a failed day cannot masquerade as 27 quiet issuers.

Every test here builds a REAL provider ScanReport — the dataclass the
pipeline itself returns — not a stand-in with invented attributes. Only
the network is absent.
"""
from __future__ import annotations

import types
from datetime import datetime, timedelta, timezone

import pytest

from scripts import radar_worker
from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.dart import radar_pipeline as dart_pipeline
from src.data_access.edgar import edgar_pipeline
from src.data_access.edinet import edinet_pipeline
from src.data_access.state_db.scan_status_repository import (
    COVERAGE_VALIDATION_OBSERVED_NOT_SUBSET,
    SCAN_OUTCOME_FULL_COVERAGE,
    SCAN_OUTCOME_INVALID,
    SCAN_OUTCOME_NO_WORK,
    SCAN_OUTCOME_UNKNOWN,
    SCAN_OUTCOME_PARTIAL_COVERAGE,
    SCAN_OUTCOME_RAISED,
    SCAN_OUTCOME_NO_COVERAGE,
    ProviderScanStatus,
)
from src.logic.radar_freshness import (
    ATTEMPT_INVALID_NOTE,
    ATTEMPT_NO_COVERAGE_NOTE,
    ATTEMPT_NO_WORK_NOTE,
    ATTEMPT_PARTIAL_NOTE,
    ATTEMPT_RAISED_NOTE,
    ATTEMPT_UNKNOWN_NOTE,
    compute_radar_freshness,
)

_NOW = "2026-10-26T12:00:00+00:00"


def _worker_settings(tmp_path) -> Settings:
    return Settings(
        cache_dir=tmp_path, db_backend="sqlite", state_db_path=tmp_path / "state.db",
        dart_api_key="k", translation_api_key="k", edgar_user_agent="t (t@example.test)",
        edinet_subscription_key="k",
    )


def _scan_status_repo(settings: Settings):
    return backend_factory.get_scan_status_repository(settings)


# --- real provider ScanReport builders -------------------------------------
#
# One per provider, because the three dataclasses genuinely differ:
# DART carries bgn_de/end_de and translations_completed; EDGAR and EDINET
# carry bgn_date/end_date. Constructing each real class is the point — a
# shared fake would not catch a field rename or a provider whose report
# lacks observed_companies.

def _dart_report(*, companies, observed, filings=0, candidates=0, warnings=()):
    return dart_pipeline.ScanReport(
        scan_id="scan-dart", started_at=_NOW, completed_at=_NOW,
        companies=tuple(companies), source="OpenDART / DART",
        bgn_de="20261019", end_de="20261026",
        filings_discovered=filings, new_filing_events=0, candidates_detected=candidates,
        candidates_processed=0, candidates_deferred=0, documents_retrieved=0,
        documents_extracted=0, translations_completed=0, already_seen_count=0,
        no_data_count=0, errors_by_category={}, cache_hits=0, warnings=tuple(warnings),
        observed_companies=tuple(observed),
    )


def _edgar_report(*, companies, observed, filings=0, candidates=0, warnings=()):
    return edgar_pipeline.ScanReport(
        scan_id="scan-edgar", started_at=_NOW, completed_at=_NOW,
        companies=tuple(companies), source="SEC EDGAR",
        bgn_date="2026-10-19", end_date="2026-10-26",
        filings_discovered=filings, new_filing_events=0, candidates_detected=candidates,
        candidates_processed=0, candidates_deferred=0, documents_retrieved=0,
        documents_extracted=0, already_seen_count=0, no_data_count=0,
        errors_by_category={}, cache_hits=0, warnings=tuple(warnings),
        observed_companies=tuple(observed),
    )


def _edinet_report(*, companies, observed, filings=0, candidates=0, warnings=()):
    return edinet_pipeline.ScanReport(
        scan_id="scan-edinet", started_at=_NOW, completed_at=_NOW,
        companies=tuple(companies), source="EDINET",
        bgn_date="2026-10-19", end_date="2026-10-26",
        filings_discovered=filings, new_filing_events=0, candidates_detected=candidates,
        candidates_processed=0, candidates_deferred=0, documents_retrieved=0,
        documents_extracted=0, already_seen_count=0, no_data_count=0,
        errors_by_category={}, cache_hits=0, warnings=tuple(warnings),
        observed_companies=tuple(observed),
    )


_BUILDERS = {
    "dart": ("OpenDART / DART", _dart_report),
    "edgar": ("SEC EDGAR", _edgar_report),
    "edinet": ("EDINET", _edinet_report),
}


def _tick(monkeypatch, tmp_path, provider_key, report, *, repo=None, settings=None):
    """Drive the real _run_provider_tick with a real report."""
    settings = settings or _worker_settings(tmp_path)
    repo = repo if repo is not None else _scan_status_repo(settings)
    # Own lock dir: the production default is a shared system-temp path.
    monkeypatch.setattr(radar_worker, "_LOCK_DIR", tmp_path / "locks")
    monkeypatch.setitem(
        radar_worker._SERVICE_MODULES, provider_key,
        types.SimpleNamespace(run_scan=lambda settings, candidate_repository=None: report),
    )
    radar_worker._run_provider_tick(provider_key, settings, repo)
    return repo


# --- the five scan-result semantics, per provider ---------------------------

@pytest.mark.parametrize("provider_key", ["dart", "edgar", "edinet"])
def test_clean_success_with_zero_new_filings_advances_last_successful_at(provider_key, tmp_path, monkeypatch):
    """"We looked properly and there was nothing new" is a success.

    This is the case most at risk from a naive fix: gating on
    items_discovered would wrongly treat a quiet week as a failure.
    """
    display, build = _BUILDERS[provider_key]
    report = build(companies=["A", "B"], observed=["A", "B"], filings=0, candidates=0)

    repo = _tick(monkeypatch, tmp_path, provider_key, report)
    status = repo.get_scan_status(display)

    assert status.last_successful_at is not None
    assert status.last_successful_at == status.completed_at
    assert status.outcome == SCAN_OUTCOME_FULL_COVERAGE
    assert (status.issuers_eligible, status.issuers_observed) == (2, 2)
    assert status.issuers_unobserved == 0
    assert status.items_discovered == 0  # genuinely quiet, and still a success


@pytest.mark.parametrize("provider_key", ["dart", "edgar", "edinet"])
def test_partial_success_advances_but_makes_the_shortfall_visible(provider_key, tmp_path, monkeypatch):
    display, build = _BUILDERS[provider_key]
    report = build(companies=["A", "B", "C"], observed=["A"], filings=1)

    repo = _tick(monkeypatch, tmp_path, provider_key, report)
    status = repo.get_scan_status(display)

    assert status.last_successful_at == status.completed_at, "a real observation did occur"
    assert status.outcome == SCAN_OUTCOME_PARTIAL_COVERAGE
    assert (status.issuers_eligible, status.issuers_observed) == (3, 1)
    assert status.issuers_unobserved == 2


@pytest.mark.parametrize("provider_key", ["dart", "edgar", "edinet"])
def test_total_failure_returned_normally_preserves_last_successful_at(provider_key, tmp_path, monkeypatch):
    """The original defect. run_scan RETURNS, so the success branch runs —
    but nothing was observed, so the prior known-good timestamp must stand.
    """
    display, build = _BUILDERS[provider_key]
    settings = _worker_settings(tmp_path)
    repo = _scan_status_repo(settings)

    # A genuine earlier success to protect.
    good = build(companies=["A", "B"], observed=["A", "B"])
    _tick(monkeypatch, tmp_path, provider_key, good, repo=repo, settings=settings)
    earlier = repo.get_scan_status(display).last_successful_at
    assert earlier is not None

    # Now every issuer fails: errors are returned, not raised.
    bad = build(companies=["A", "B"], observed=[], warnings=("A: boom", "B: boom"))
    _tick(monkeypatch, tmp_path, provider_key, bad, repo=repo, settings=settings)
    status = repo.get_scan_status(display)

    assert status.last_successful_at == earlier, "a failed tick must not advance the success clock"
    assert status.completed_at != earlier, "but the attempt itself is still recorded"
    assert status.outcome == SCAN_OUTCOME_NO_COVERAGE
    assert (status.issuers_eligible, status.issuers_observed) == (2, 0)
    assert status.failure_code is None, "nothing raised; this is a returned failure"


@pytest.mark.parametrize("provider_key", ["dart", "edgar", "edinet"])
def test_no_eligible_work_is_represented_explicitly_not_as_success(provider_key, tmp_path, monkeypatch):
    display, build = _BUILDERS[provider_key]
    report = build(companies=[], observed=[])

    repo = _tick(monkeypatch, tmp_path, provider_key, report)
    status = repo.get_scan_status(display)

    assert status.outcome == SCAN_OUTCOME_NO_WORK
    assert status.last_successful_at is None, "no work is not a success"
    assert status.completed_at is not None, "the attempt is still recorded"
    assert (status.issuers_eligible, status.issuers_observed) == (0, 0)


@pytest.mark.parametrize("provider_key", ["dart", "edgar", "edinet"])
def test_a_raised_exception_keeps_the_existing_failure_behaviour(provider_key, tmp_path, monkeypatch):
    display, build = _BUILDERS[provider_key]
    settings = _worker_settings(tmp_path)
    repo = _scan_status_repo(settings)

    good = build(companies=["A"], observed=["A"])
    _tick(monkeypatch, tmp_path, provider_key, good, repo=repo, settings=settings)
    earlier = repo.get_scan_status(display).last_successful_at

    def _raise(settings, candidate_repository=None):
        raise ConnectionError("provider down")

    monkeypatch.setattr(radar_worker, "_LOCK_DIR", tmp_path / "locks")
    monkeypatch.setitem(radar_worker._SERVICE_MODULES, provider_key, types.SimpleNamespace(run_scan=_raise))
    radar_worker._run_provider_tick(provider_key, settings, repo)
    status = repo.get_scan_status(display)

    assert status.failure_code == "ConnectionError"
    assert status.last_successful_at == earlier
    assert status.outcome == SCAN_OUTCOME_RAISED


# --- the EDINET-specific trap ----------------------------------------------

def test_edinet_day_fetch_failure_is_a_total_failure_not_27_quiet_issuers(tmp_path, monkeypatch):
    """EDINET's scan is day-keyed: a failed day returns zero rows, which
    would make every tracked issuer look quiet. Its observed_companies is
    all-or-nothing for exactly that reason, so one count works for all
    three providers without pretending EDINET has per-company requests.
    """
    report = _edinet_report(companies=[f"JP-{i}" for i in range(27)], observed=[], filings=0)

    repo = _tick(monkeypatch, tmp_path, "edinet", report)
    status = repo.get_scan_status("EDINET")

    assert status.issuers_eligible == 27
    assert status.issuers_observed == 0
    assert status.outcome == SCAN_OUTCOME_NO_COVERAGE
    assert status.last_successful_at is None


# --- the derivation itself, as a pure function -----------------------------

def _status(**over) -> ProviderScanStatus:
    base = dict(
        provider="SEC EDGAR", cursor_value=None, started_at=_NOW, completed_at=_NOW,
        last_successful_at=None, items_discovered=0, candidates_created=0,
        skipped_unresolved_count=0, failure_code=None, updated_at=_NOW,
        issuers_eligible=None, issuers_observed=None,
        coverage_validation_code=None,
    )
    base.update(over)
    return ProviderScanStatus(**base)


@pytest.mark.parametrize("attempted,observed,failure,expected", [
    (2, 2, None, SCAN_OUTCOME_FULL_COVERAGE),
    (3, 1, None, SCAN_OUTCOME_PARTIAL_COVERAGE),
    (2, 0, None, SCAN_OUTCOME_NO_COVERAGE),
    (0, 0, None, SCAN_OUTCOME_NO_WORK),
    (2, 2, "ConnectionError", SCAN_OUTCOME_RAISED),
    (0, 0, "ValueError", SCAN_OUTCOME_RAISED),
    (None, None, None, SCAN_OUTCOME_UNKNOWN),
    (2, None, None, SCAN_OUTCOME_UNKNOWN),
    (None, 2, None, SCAN_OUTCOME_UNKNOWN),
    (2, 3, None, SCAN_OUTCOME_INVALID),
    (-1, 0, None, SCAN_OUTCOME_INVALID),
    (2, -1, None, SCAN_OUTCOME_INVALID),
])
def test_outcome_is_derived_from_stored_facts(attempted, observed, failure, expected):
    assert _status(issuers_eligible=attempted, issuers_observed=observed, failure_code=failure).outcome == expected


def test_outcome_reads_nothing_but_the_rows_own_fields():
    """Structural, not just repeatable: the property's body is inspected.

    Determinism alone would also hold for a property that read a clock
    consistently, so this asserts the stronger thing the name claims --
    the body makes no calls at all, touches no attribute other than
    `self`'s, and names nothing but this module's outcome constants.
    """
    import ast
    import inspect
    import textwrap

    module = ast.parse(textwrap.dedent(inspect.getsource(ProviderScanStatus.outcome.fget)))
    func = module.body[0]
    # The BODY only: the signature's return annotation is not a read.
    body = [n for stmt in func.body for n in ast.walk(stmt)]

    assert not [n for n in body if isinstance(n, ast.Call)], (
        "a call could reach a clock, a connection or another row"
    )
    for node in body:
        if isinstance(node, ast.Attribute):
            assert isinstance(node.value, ast.Name) and node.value.id == "self", (
                f"reads {ast.dump(node.value)}, not its own row"
            )
    names = {n.id for n in body if isinstance(n, ast.Name)} - {"self"}
    assert names and all(n.startswith("SCAN_OUTCOME_") for n in names), names

    s = _status(issuers_eligible=3, issuers_observed=1)
    assert s.outcome == s.outcome == SCAN_OUTCOME_PARTIAL_COVERAGE
    assert s.advances_last_successful is True
    assert _status(issuers_eligible=3, issuers_observed=0).advances_last_successful is False
    assert _status(issuers_eligible=0, issuers_observed=0).advances_last_successful is False


def test_a_row_written_before_the_change_reads_as_unknown_not_proven_no_work():
    """Pre-change rows carry NULL coverage. Unknown must not be rounded
    into `no_work`, which would assert that nothing was eligible -- a
    claim we cannot make about an attempt that never recorded coverage.
    """
    legacy = _status(last_successful_at="2026-01-01T00:00:00+00:00",
                     issuers_eligible=None, issuers_observed=None)
    assert legacy.outcome == SCAN_OUTCOME_UNKNOWN
    assert legacy.outcome != SCAN_OUTCOME_NO_WORK
    assert legacy.issuers_unobserved is None, "a shortfall is not derivable from unknown counts"
    assert legacy.last_successful_at == "2026-01-01T00:00:00+00:00", "history is preserved, not rewritten"


# --- persistence round trip ------------------------------------------------

def test_counts_survive_a_sqlite_round_trip(tmp_path):
    settings = _worker_settings(tmp_path)
    repo = _scan_status_repo(settings)
    repo.upsert_scan_status(_status(provider="EDINET", issuers_eligible=27, issuers_observed=4, items_discovered=9))

    read_back = repo.get_scan_status("EDINET")
    assert (read_back.issuers_eligible, read_back.issuers_observed) == (27, 4)
    assert read_back.outcome == SCAN_OUTCOME_PARTIAL_COVERAGE
    assert read_back.issuers_unobserved == 23


def test_an_upsert_overwrites_the_counts_rather_than_accumulating(tmp_path):
    settings = _worker_settings(tmp_path)
    repo = _scan_status_repo(settings)
    repo.upsert_scan_status(_status(provider="SEC EDGAR", issuers_eligible=90, issuers_observed=90))
    repo.upsert_scan_status(_status(provider="SEC EDGAR", issuers_eligible=90, issuers_observed=12))

    status = repo.get_scan_status("SEC EDGAR")
    assert (status.issuers_eligible, status.issuers_observed) == (90, 12)
    assert status.outcome == SCAN_OUTCOME_PARTIAL_COVERAGE


def test_the_sqlite_migration_adds_both_columns_at_the_current_version(tmp_path):
    from src.data_access.state_db import schema
    from src.data_access.state_db.connection import connect

    conn = connect(tmp_path / "fresh.db")
    assert schema.migrate(conn) == schema.CURRENT_SCHEMA_VERSION
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(provider_scan_status)").fetchall()}
    assert {"issuers_eligible", "issuers_observed"} <= cols


# --- operator display ------------------------------------------------------

def _recent(minutes_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def test_a_total_failure_shows_as_stale_rather_than_freshly_refreshed():
    """The product consequence. The freshness line keys off
    last_successful_at, so gating that field is what makes the operator
    message truthful — no display logic change required.
    """
    statuses = {"SEC EDGAR": _status(
        last_successful_at=_recent(600), completed_at=_recent(1), issuers_eligible=90, issuers_observed=0,
    )}
    freshness = compute_radar_freshness("ok", statuses, ("SEC EDGAR",), 60)

    assert freshness.state == "all_stale"
    assert "may be out of date" in freshness.message


def test_a_partial_tick_is_shown_as_refreshed_and_qualified():
    statuses = {"SEC EDGAR": _status(
        last_successful_at=_recent(5), completed_at=_recent(5), issuers_eligible=90, issuers_observed=12,
    )}
    freshness = compute_radar_freshness("ok", statuses, ("SEC EDGAR",), 60)

    assert freshness.state == "all_recent"
    assert freshness.attempt_note == ATTEMPT_PARTIAL_NOTE


def test_a_clean_tick_carries_no_partial_note():
    statuses = {"SEC EDGAR": _status(
        last_successful_at=_recent(5), completed_at=_recent(5), issuers_eligible=90, issuers_observed=90,
    )}
    freshness = compute_radar_freshness("ok", statuses, ("SEC EDGAR",), 60)

    assert freshness.state == "all_recent"
    assert freshness.attempt_note is None


def test_no_eligible_work_never_reads_as_a_completed_scan():
    statuses = {"EDINET": _status(provider="EDINET", last_successful_at=None, completed_at=_recent(1))}
    freshness = compute_radar_freshness("ok", statuses, ("EDINET",), 60)

    assert freshness.state == "no_scan_yet"


# --- 1. latest-attempt reporting is INDEPENDENT of freshness ---------------

def test_a_total_coverage_failure_right_after_a_recent_success_is_still_reported(tmp_path, monkeypatch):
    """The gap this closes.

    Freshness reads last_successful_at, which a failed attempt must
    preserve. So a provider that succeeded 5 minutes ago and then covered
    nothing still shows "last refreshed 5 minutes ago" -- correct, but on
    its own it hides that the newest attempt established no coverage.
    """
    settings = _worker_settings(tmp_path)
    repo = _scan_status_repo(settings)

    good = _edgar_report(companies=["A", "B"], observed=["A", "B"])
    _tick(monkeypatch, tmp_path, "edgar", good, repo=repo, settings=settings)
    recent_success = repo.get_scan_status("SEC EDGAR").last_successful_at

    bad = _edgar_report(companies=["A", "B"], observed=[])
    _tick(monkeypatch, tmp_path, "edgar", bad, repo=repo, settings=settings)
    status = repo.get_scan_status("SEC EDGAR")

    assert status.last_successful_at == recent_success, "freshness history is preserved"
    assert status.latest_attempt_fell_short is True

    freshness = compute_radar_freshness("ok", {"SEC EDGAR": status}, ("SEC EDGAR",), 60)
    assert freshness.state == "all_recent", "the newest DATA really is recent"
    assert freshness.attempt_note == ATTEMPT_NO_COVERAGE_NOTE, "and the failed attempt is still reported"


@pytest.mark.parametrize("eligible,observed,failure,code,expected_note", [
    (2, 0, None, None, ATTEMPT_NO_COVERAGE_NOTE),
    (3, 1, None, None, ATTEMPT_PARTIAL_NOTE),
    (0, 0, None, None, ATTEMPT_NO_WORK_NOTE),
    (None, None, "ConnectionError", None, ATTEMPT_RAISED_NOTE),
    (None, None, None, None, ATTEMPT_UNKNOWN_NOTE),
    (None, None, None, COVERAGE_VALIDATION_OBSERVED_NOT_SUBSET, ATTEMPT_INVALID_NOTE),
    (2, 3, None, None, ATTEMPT_INVALID_NOTE),
    (2, 2, None, None, None),
])
def test_each_attempt_outcome_has_its_own_report_line(eligible, observed, failure, code, expected_note):
    status = _status(
        last_successful_at=_recent(5), completed_at=_recent(1),
        issuers_eligible=eligible, issuers_observed=observed, failure_code=failure,
        coverage_validation_code=code,
    )
    freshness = compute_radar_freshness("ok", {"SEC EDGAR": status}, ("SEC EDGAR",), 60)
    assert freshness.attempt_note == expected_note


def test_the_attempt_note_is_reported_even_when_data_is_stale():
    """Independence in the other direction: a stale provider whose latest
    attempt also covered nothing gets both signals, not one."""
    status = _status(last_successful_at=_recent(600), completed_at=_recent(1),
                     issuers_eligible=5, issuers_observed=0)
    freshness = compute_radar_freshness("ok", {"SEC EDGAR": status}, ("SEC EDGAR",), 60)

    assert freshness.state == "all_stale"
    assert freshness.attempt_note == ATTEMPT_NO_COVERAGE_NOTE


def test_the_most_serious_shortfall_wins_across_sources():
    statuses = {
        "SEC EDGAR": _status(provider="SEC EDGAR", last_successful_at=_recent(5),
                             completed_at=_recent(1), issuers_eligible=9, issuers_observed=4),
        "EDINET": _status(provider="EDINET", last_successful_at=_recent(5),
                          completed_at=_recent(1), issuers_eligible=27, issuers_observed=0),
    }
    freshness = compute_radar_freshness("ok", statuses, ("SEC EDGAR", "EDINET"), 60)
    assert freshness.attempt_note == ATTEMPT_NO_COVERAGE_NOTE, "no_coverage outranks partial"


# --- 3. EDINET: incomplete day coverage is not "every request failed" ------

def test_edinet_no_coverage_does_not_claim_requests_or_issuers_failed():
    """The wording and the model must both stay honest. EDINET's scan is
    day-keyed; one unusable day leaves the whole range unsound. That is
    `no_coverage` -- not 27 issuer failures, and not a request count.
    """
    status = _status(provider="EDINET", issuers_eligible=27, issuers_observed=0,
                     last_successful_at=_recent(5), completed_at=_recent(1))

    assert status.outcome == SCAN_OUTCOME_NO_COVERAGE
    assert status.issuers_unobserved == 27, "27 issuers were left uncovered"
    # The note must not assert that requests or individual issuers failed.
    assert ATTEMPT_NO_COVERAGE_NOTE == "Latest scan attempt established no issuer coverage."
    for forbidden in ("request", "failed", "error"):
        assert forbidden not in ATTEMPT_NO_COVERAGE_NOTE.lower()


def test_the_count_fields_are_named_for_issuers_not_requests():
    fields = set(ProviderScanStatus.__dataclass_fields__)
    assert {"issuers_eligible", "issuers_observed"} <= fields
    assert not any("request" in f for f in fields)
    assert "attempted_count" not in fields and "observed_count" not in fields


# --- 4. _record_failure must not retain prior coverage --------------------

def test_a_raised_attempt_does_not_inherit_the_previous_attempts_coverage(tmp_path, monkeypatch):
    """The counts describe the LATEST attempt. Carrying the prior
    attempt's numbers forward would make a raised attempt assert it
    observed issuers it never reached.
    """
    settings = _worker_settings(tmp_path)
    repo = _scan_status_repo(settings)

    good = _edgar_report(companies=["A", "B", "C"], observed=["A", "B", "C"])
    _tick(monkeypatch, tmp_path, "edgar", good, repo=repo, settings=settings)
    before = repo.get_scan_status("SEC EDGAR")
    assert (before.issuers_eligible, before.issuers_observed) == (3, 3)

    def _raise(settings, candidate_repository=None):
        raise ConnectionError("down")

    monkeypatch.setattr(radar_worker, "_LOCK_DIR", tmp_path / "locks")
    monkeypatch.setitem(radar_worker._SERVICE_MODULES, "edgar", types.SimpleNamespace(run_scan=_raise))
    radar_worker._run_provider_tick("edgar", settings, repo)
    after = repo.get_scan_status("SEC EDGAR")

    assert after.failure_code == "ConnectionError"
    assert after.issuers_eligible is None, "coverage is unknown for a raised attempt"
    assert after.issuers_observed is None
    assert after.outcome == SCAN_OUTCOME_RAISED
    # Explicitly historical fields ARE preserved.
    assert after.last_successful_at == before.last_successful_at
    assert after.items_discovered == before.items_discovered
    assert after.cursor_value == before.cursor_value


# --- 5. invariants and report membership ----------------------------------

def test_observed_issuers_outside_the_eligible_set_are_durably_invalid(tmp_path, monkeypatch):
    """A report whose observed set is not a subset of its eligible set is
    not describable as coverage.

    Recording only NULL counts would make it indistinguishable from a row
    whose coverage was never recorded -- both would read `unknown`. The
    reason is persisted as a fixed code so the contradiction survives on
    the row instead of only in a log line on an ephemeral disk.
    """
    report = _edgar_report(companies=["A", "B"], observed=["A", "ZZZ"])

    repo = _tick(monkeypatch, tmp_path, "edgar", report)
    status = repo.get_scan_status("SEC EDGAR")

    assert status.outcome == SCAN_OUTCOME_INVALID
    assert status.outcome != SCAN_OUTCOME_UNKNOWN, "invalid must not be rounded into unknown"
    assert status.outcome not in (SCAN_OUTCOME_FULL_COVERAGE, SCAN_OUTCOME_PARTIAL_COVERAGE)
    assert status.coverage_validation_code == COVERAGE_VALIDATION_OBSERVED_NOT_SUBSET
    assert (status.issuers_eligible, status.issuers_observed) == (None, None)
    assert status.last_successful_at is None, "an undescribable attempt never advances success"
    assert status.advances_last_successful is False


def test_the_persisted_validation_code_is_a_sanitized_constant(tmp_path, monkeypatch):
    """Never provider-supplied text: a provider name or warning string in
    this column would both leak prose into the model and defeat matching
    on it."""
    report = _edgar_report(
        companies=["A"], observed=["<script>Totally Fake Corp"],
        warnings=("Totally Fake Corp not resolved",),
    )

    repo = _tick(monkeypatch, tmp_path, "edgar", report)
    code = repo.get_scan_status("SEC EDGAR").coverage_validation_code

    assert code == COVERAGE_VALIDATION_OBSERVED_NOT_SUBSET
    assert code == "observed_not_subset"
    assert "Fake" not in code and "<" not in code and "not resolved" not in code


def test_invalid_membership_survives_a_sqlite_round_trip(tmp_path):
    """Persisted and re-read, not merely derived in memory."""
    settings = _worker_settings(tmp_path)
    repo = _scan_status_repo(settings)
    repo.upsert_scan_status(_status(
        provider="SEC EDGAR", issuers_eligible=None, issuers_observed=None,
        coverage_validation_code=COVERAGE_VALIDATION_OBSERVED_NOT_SUBSET,
    ))

    back = repo.get_scan_status("SEC EDGAR")

    assert back.coverage_validation_code == COVERAGE_VALIDATION_OBSERVED_NOT_SUBSET
    assert back.outcome == SCAN_OUTCOME_INVALID
    assert back.latest_attempt_fell_short is True
    assert back.advances_last_successful is False


def test_a_later_valid_attempt_clears_the_validation_code(tmp_path, monkeypatch):
    """Invalid is a state of the LATEST attempt, not a scar on the row."""
    settings = _worker_settings(tmp_path)
    repo = _scan_status_repo(settings)

    _tick(monkeypatch, tmp_path, "edgar", _edgar_report(companies=["A", "B"], observed=["A", "ZZZ"]),
          repo=repo, settings=settings)
    assert repo.get_scan_status("SEC EDGAR").outcome == SCAN_OUTCOME_INVALID

    _tick(monkeypatch, tmp_path, "edgar", _edgar_report(companies=["A", "B"], observed=["A", "B"]),
          repo=repo, settings=settings)
    after = repo.get_scan_status("SEC EDGAR")

    assert after.coverage_validation_code is None
    assert after.outcome == SCAN_OUTCOME_FULL_COVERAGE
    assert after.last_successful_at is not None, "a valid attempt does advance success"


def test_a_raised_attempt_clears_stale_coverage_validation_state(tmp_path, monkeypatch):
    """A raised attempt established nothing about coverage, so it must not
    report the PREVIOUS attempt's contradiction as its own."""
    settings = _worker_settings(tmp_path)
    repo = _scan_status_repo(settings)

    _tick(monkeypatch, tmp_path, "edgar", _edgar_report(companies=["A", "B"], observed=["A", "ZZZ"]),
          repo=repo, settings=settings)
    assert repo.get_scan_status("SEC EDGAR").coverage_validation_code is not None

    def _raise(settings, candidate_repository=None):
        raise ConnectionError("down")

    monkeypatch.setattr(radar_worker, "_LOCK_DIR", tmp_path / "locks")
    monkeypatch.setitem(radar_worker._SERVICE_MODULES, "edgar",
                        types.SimpleNamespace(run_scan=_raise))
    radar_worker._run_provider_tick("edgar", settings, repo)
    after = repo.get_scan_status("SEC EDGAR")

    assert after.coverage_validation_code is None
    assert after.failure_code == "ConnectionError"
    assert after.outcome == SCAN_OUTCOME_RAISED, "raised outranks a cleared validation code"


def test_an_invalid_attempt_right_after_a_recent_success_shows_invalid_to_the_operator(tmp_path, monkeypatch):
    """The operator must see `invalid` -- not unknown, and not a success
    implied by a recent last_successful_at."""
    settings = _worker_settings(tmp_path)
    repo = _scan_status_repo(settings)

    _tick(monkeypatch, tmp_path, "edgar", _edgar_report(companies=["A", "B"], observed=["A", "B"]),
          repo=repo, settings=settings)
    recent_success = repo.get_scan_status("SEC EDGAR").last_successful_at

    _tick(monkeypatch, tmp_path, "edgar", _edgar_report(companies=["A", "B"], observed=["A", "ZZZ"]),
          repo=repo, settings=settings)
    status = repo.get_scan_status("SEC EDGAR")

    assert status.last_successful_at == recent_success, "freshness history is preserved"

    freshness = compute_radar_freshness("ok", {"SEC EDGAR": status}, ("SEC EDGAR",), 60)
    assert freshness.state == "all_recent", "the newest DATA really is recent"
    assert freshness.attempt_note == ATTEMPT_INVALID_NOTE
    assert freshness.attempt_note != ATTEMPT_UNKNOWN_NOTE, "invalid must not display as unknown"
    assert freshness.attempt_note is not None


def test_duplicate_names_in_a_report_do_not_inflate_coverage(tmp_path, monkeypatch):
    report = _edgar_report(companies=["A", "A", "B"], observed=["A", "A"])

    repo = _tick(monkeypatch, tmp_path, "edgar", report)
    status = repo.get_scan_status("SEC EDGAR")

    assert (status.issuers_eligible, status.issuers_observed) == (2, 1)
    assert status.outcome == SCAN_OUTCOME_PARTIAL_COVERAGE


@pytest.mark.parametrize("eligible,observed", [(2, 3), (-1, 0), (2, -1), (0, 1)])
def test_inconsistent_stored_counts_are_invalid_never_a_success(eligible, observed):
    """Numeric invalidity needs no validation code -- the counts alone
    contradict each other, so the count rules still decide."""
    status = _status(issuers_eligible=eligible, issuers_observed=observed)
    assert status.coverage_validation_code is None
    assert status.outcome == SCAN_OUTCOME_INVALID
    assert status.advances_last_successful is False
    assert status.issuers_unobserved is None
    assert status.latest_attempt_fell_short is True


@pytest.mark.parametrize("failure,code,expected", [
    ("ConnectionError", COVERAGE_VALIDATION_OBSERVED_NOT_SUBSET, SCAN_OUTCOME_RAISED),
    (None, COVERAGE_VALIDATION_OBSERVED_NOT_SUBSET, SCAN_OUTCOME_INVALID),
    (None, None, SCAN_OUTCOME_UNKNOWN),
])
def test_outcome_precedence_is_raised_then_invalid_then_the_count_rules(failure, code, expected):
    """A raised scan determined nothing, so it outranks a validation code;
    a validation code outranks the NULL counts it explains."""
    assert _status(issuers_eligible=None, issuers_observed=None,
                   failure_code=failure, coverage_validation_code=code).outcome == expected


def test_only_consistent_observed_coverage_advances_the_success_clock():
    assert _status(issuers_eligible=3, issuers_observed=3).advances_last_successful is True
    assert _status(issuers_eligible=3, issuers_observed=1).advances_last_successful is True
    assert _status(issuers_eligible=3, issuers_observed=0).advances_last_successful is False
    assert _status(issuers_eligible=0, issuers_observed=0).advances_last_successful is False
    assert _status(issuers_eligible=None, issuers_observed=None).advances_last_successful is False
    assert _status(issuers_eligible=3, issuers_observed=3, failure_code="E").advances_last_successful is False


# --- 2. upgrade from a POPULATED pre-change database ----------------------

def test_sqlite_upgrade_from_a_populated_pre_change_row_preserves_history(tmp_path):
    """A pre-change database with a real success recorded must migrate
    without rewriting that success, and its coverage must read unknown.
    """
    import sqlite3

    from src.data_access.state_db import schema
    from src.data_access.state_db.connection import connect

    db = tmp_path / "legacy.db"
    conn = connect(db)
    # Build the schema as it stood before this change, then stop.
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (0)")
    conn.commit()
    for target, statements in schema._MIGRATIONS:
        if target > 23:
            break
        for statement in statements:
            conn.execute(statement)
        conn.execute("UPDATE schema_version SET version = ?", (target,))
    conn.commit()
    assert schema.get_schema_version(conn) == 23
    cols_before = {r["name"] for r in conn.execute("PRAGMA table_info(provider_scan_status)").fetchall()}
    assert "issuers_eligible" not in cols_before

    conn.execute(
        "INSERT INTO provider_scan_status (provider, cursor_value, started_at, completed_at, "
        "last_successful_at, items_discovered, candidates_created, skipped_unresolved_count, "
        "failure_code, updated_at) VALUES "
        "('SEC EDGAR', '2026-01-05', '2026-01-05T00:00:00+00:00', '2026-01-05T00:05:00+00:00', "
        "'2026-01-05T00:05:00+00:00', 7, 3, 0, NULL, '2026-01-05T00:05:00+00:00')"
    )
    conn.commit()

    assert schema.migrate(conn) == schema.CURRENT_SCHEMA_VERSION

    row = conn.execute("SELECT * FROM provider_scan_status WHERE provider = 'SEC EDGAR'").fetchone()
    assert row["last_successful_at"] == "2026-01-05T00:05:00+00:00", "historical success not rewritten"
    assert row["items_discovered"] == 7 and row["candidates_created"] == 3
    assert row["issuers_eligible"] is None and row["issuers_observed"] is None
    assert row["coverage_validation_code"] is None, (
        "a pre-change row records no contradiction, so it is unknown -- not invalid"
    )

    status = backend_factory.get_scan_status_repository(
        Settings(cache_dir=tmp_path, db_backend="sqlite", state_db_path=db)
    ).get_scan_status("SEC EDGAR")
    assert status.outcome == SCAN_OUTCOME_UNKNOWN
    assert status.outcome != SCAN_OUTCOME_NO_WORK
    assert status.outcome != SCAN_OUTCOME_INVALID
    assert status.coverage_validation_code is None
    assert status.last_successful_at == "2026-01-05T00:05:00+00:00"


def test_a_post_migration_tick_fills_in_coverage_on_the_legacy_row(tmp_path, monkeypatch):
    """Unknown is a transitional state, not a permanent one."""
    settings = _worker_settings(tmp_path)
    repo = _scan_status_repo(settings)
    repo.upsert_scan_status(_status(provider="SEC EDGAR", last_successful_at="2026-01-05T00:05:00+00:00",
                                    issuers_eligible=None, issuers_observed=None))
    assert repo.get_scan_status("SEC EDGAR").outcome == SCAN_OUTCOME_UNKNOWN

    _tick(monkeypatch, tmp_path, "edgar", _edgar_report(companies=["A"], observed=["A"]),
          repo=repo, settings=settings)
    status = repo.get_scan_status("SEC EDGAR")

    assert status.outcome == SCAN_OUTCOME_FULL_COVERAGE
    assert (status.issuers_eligible, status.issuers_observed) == (1, 1)


def test_nulls_survive_a_sqlite_round_trip_as_none_not_zero(tmp_path):
    settings = _worker_settings(tmp_path)
    repo = _scan_status_repo(settings)
    repo.upsert_scan_status(_status(provider="EDINET", issuers_eligible=None, issuers_observed=None))

    read_back = repo.get_scan_status("EDINET")
    assert read_back.issuers_eligible is None, "NULL must not read back as 0"
    assert read_back.issuers_observed is None
    assert read_back.outcome == SCAN_OUTCOME_UNKNOWN


# --- Postgres: mirrored semantics, fresh migration, populated upgrade -----
#
# These skip without the local disposable container (see
# tests/_postgres_test_support.py). Postgres is the deployed backend, so
# the mirrored dataclass and the V26 migration must be exercised, not
# assumed equivalent to SQLite.

from tests._postgres_test_support import pg_isolated_connection  # noqa: E402,F401


def test_postgres_fresh_migration_adds_all_three_nullable_columns(pg_isolated_connection):
    from src.data_access.postgres_state_db import schema as postgres_schema

    conn = pg_isolated_connection
    assert postgres_schema.migrate(conn) == postgres_schema.CURRENT_SCHEMA_VERSION

    rows = conn.execute(
        "SELECT column_name, is_nullable FROM information_schema.columns "
        "WHERE table_name = 'provider_scan_status' "
        "AND column_name IN ('issuers_eligible', 'issuers_observed', 'coverage_validation_code')"
    ).fetchall()
    found = {r["column_name"]: r["is_nullable"] for r in rows}
    assert found == {
        "issuers_eligible": "YES",
        "issuers_observed": "YES",
        "coverage_validation_code": "YES",
    }, "all three columns must exist and be nullable, so a pre-change row reads unknown"


def test_postgres_upgrade_from_a_populated_pre_change_row_preserves_history(pg_isolated_connection):
    from src.data_access.postgres_state_db import scan_status_repository as pg_repo
    from src.data_access.postgres_state_db import schema as postgres_schema

    conn = pg_isolated_connection
    # Build the schema as it stood before this change, then stop.
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (0)")
    for target, statements in postgres_schema._MIGRATIONS:
        if target > 25:
            break
        for statement in statements:
            conn.execute(statement)
        conn.execute("UPDATE schema_version SET version = %s", (target,))
    conn.commit()
    assert postgres_schema.get_schema_version(conn) == 25

    conn.execute(
        "INSERT INTO provider_scan_status (provider, cursor_value, started_at, completed_at, "
        "last_successful_at, items_discovered, candidates_created, skipped_unresolved_count, "
        "failure_code, updated_at) VALUES "
        "('EDINET', '2026-01-05', '2026-01-05T00:00:00+00:00', '2026-01-05T00:05:00+00:00', "
        "'2026-01-05T00:05:00+00:00', 11, 4, 0, NULL, '2026-01-05T00:05:00+00:00')"
    )
    conn.commit()

    assert postgres_schema.migrate(conn) == postgres_schema.CURRENT_SCHEMA_VERSION

    row = conn.execute("SELECT * FROM provider_scan_status WHERE provider = 'EDINET'").fetchone()
    assert row["last_successful_at"] == "2026-01-05T00:05:00+00:00", "historical success not rewritten"
    assert row["items_discovered"] == 11 and row["candidates_created"] == 4
    assert row["issuers_eligible"] is None and row["issuers_observed"] is None
    assert row["coverage_validation_code"] is None

    status = pg_repo.get_scan_status(conn, "EDINET")
    assert status.outcome == pg_repo.SCAN_OUTCOME_UNKNOWN, "a legacy row is unknown, never invalid"
    assert status.coverage_validation_code is None
    assert status.last_successful_at == "2026-01-05T00:05:00+00:00"


def test_postgres_repository_round_trip_and_mirrored_outcomes(pg_isolated_connection):
    from src.data_access.postgres_state_db import scan_status_repository as pg_repo
    from src.data_access.postgres_state_db import schema as postgres_schema
    from src.data_access.state_db import scan_status_repository as sqlite_repo

    conn = pg_isolated_connection
    postgres_schema.migrate(conn)

    def _pg_status(**over):
        base = dict(
            provider="EDINET", cursor_value=None, started_at=_NOW, completed_at=_NOW,
            last_successful_at=None, items_discovered=0, candidates_created=0,
            skipped_unresolved_count=0, failure_code=None, updated_at=_NOW,
            issuers_eligible=None, issuers_observed=None,
            coverage_validation_code=None,
        )
        base.update(over)
        return pg_repo.ProviderScanStatus(**base)

    # NULL round trip: must come back as None, never 0.
    pg_repo.upsert_scan_status(conn, _pg_status())
    assert pg_repo.get_scan_status(conn, "EDINET").issuers_eligible is None
    assert pg_repo.get_scan_status(conn, "EDINET").outcome == pg_repo.SCAN_OUTCOME_UNKNOWN

    # Real counts round trip, and an upsert overwrites rather than accumulates.
    pg_repo.upsert_scan_status(conn, _pg_status(issuers_eligible=27, issuers_observed=27))
    pg_repo.upsert_scan_status(conn, _pg_status(issuers_eligible=27, issuers_observed=4))
    back = pg_repo.get_scan_status(conn, "EDINET")
    assert (back.issuers_eligible, back.issuers_observed) == (27, 4)
    assert back.issuers_unobserved == 23

    # Mirrored semantics: the two independently-implemented dataclasses
    # must derive identical outcomes for identical inputs. The packages
    # deliberately share no code, so equivalence needs proving.
    # Invalid membership must round trip through Postgres too, and stay
    # distinguishable from the unknown row written a few lines above.
    pg_repo.upsert_scan_status(conn, _pg_status(
        coverage_validation_code=pg_repo.COVERAGE_VALIDATION_OBSERVED_NOT_SUBSET))
    invalid = pg_repo.get_scan_status(conn, "EDINET")
    assert invalid.coverage_validation_code == COVERAGE_VALIDATION_OBSERVED_NOT_SUBSET
    assert invalid.outcome == pg_repo.SCAN_OUTCOME_INVALID
    assert invalid.outcome != pg_repo.SCAN_OUTCOME_UNKNOWN
    assert invalid.advances_last_successful is False

    # A later valid attempt clears it on this backend as well.
    pg_repo.upsert_scan_status(conn, _pg_status(issuers_eligible=2, issuers_observed=2))
    cleared = pg_repo.get_scan_status(conn, "EDINET")
    assert cleared.coverage_validation_code is None
    assert cleared.outcome == pg_repo.SCAN_OUTCOME_FULL_COVERAGE

    # The sanitized code constant must be identical across the two
    # independently-implemented packages, or a row written by one would be
    # classified differently by the other.
    assert pg_repo.COVERAGE_VALIDATION_OBSERVED_NOT_SUBSET == COVERAGE_VALIDATION_OBSERVED_NOT_SUBSET
    for name in ("SCAN_OUTCOME_INVALID", "SCAN_OUTCOME_UNKNOWN", "SCAN_OUTCOME_RAISED",
                 "SCAN_OUTCOME_NO_WORK", "SCAN_OUTCOME_NO_COVERAGE",
                 "SCAN_OUTCOME_PARTIAL_COVERAGE", "SCAN_OUTCOME_FULL_COVERAGE"):
        assert getattr(pg_repo, name) == getattr(sqlite_repo, name), name

    cases = [(2, 2, None, None), (3, 1, None, None), (2, 0, None, None), (0, 0, None, None),
             (None, None, None, None), (2, 3, None, None), (-1, 0, None, None),
             (2, 2, "ConnectionError", None),
             (None, None, None, COVERAGE_VALIDATION_OBSERVED_NOT_SUBSET),
             (None, None, "ConnectionError", COVERAGE_VALIDATION_OBSERVED_NOT_SUBSET)]
    for eligible, observed, failure, code in cases:
        common = dict(
            provider="EDINET", cursor_value=None, started_at=_NOW, completed_at=_NOW,
            last_successful_at=None, items_discovered=0, candidates_created=0,
            skipped_unresolved_count=0, failure_code=failure, updated_at=_NOW,
            issuers_eligible=eligible, issuers_observed=observed,
            coverage_validation_code=code,
        )
        pg = pg_repo.ProviderScanStatus(**common)
        sq = sqlite_repo.ProviderScanStatus(**common)
        assert pg.outcome == sq.outcome, f"outcome diverged for {(eligible, observed, failure, code)}"
        assert pg.issuers_unobserved == sq.issuers_unobserved
        assert pg.advances_last_successful == sq.advances_last_successful
        assert pg.latest_attempt_fell_short == sq.latest_attempt_fell_short


# --- the deployed-backend acceptance test ---------------------------------
#
# Everything above proves the model, the migration and the repository on
# Postgres, and the DISPLAY on SQLite. That left one gap: Postgres is the
# deployed backend, and `radar_freshness` keys its note table on the
# SQLITE module's outcome constants while Radar Inbox hands it POSTGRES
# ProviderScanStatus objects. The constants are asserted equal above, so
# the lookup was safe by composition -- but composition is not an
# executed assertion on the deployed path, which is what this closes.
#
# Scope, stated plainly: this exercises the real Postgres repository and
# the real freshness function against rows that made a round trip through
# Postgres. It does NOT render the Radar Inbox page and does NOT prove a
# full page render. The page-level Postgres coverage lives in
# tests/test_radar_inbox_postgres_candidates.py, which fails and hangs
# identically at base f803dca for reasons unrelated to this change.

# Fixed instants, so neither recency nor the note depends on the clock.
# 30 minutes of age against a 60-minute interval is well inside the
# 3x stale threshold, so every case below is unambiguously "all_recent".
_PG_EVAL_NOW = datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)
_PG_RECENT_SUCCESS = "2026-10-26T11:30:00+00:00"
_PG_INTERVAL_MINUTES = 60


@pytest.mark.parametrize("eligible,observed,failure,code,expected_note,expected_outcome", [
    (None, None, None, COVERAGE_VALIDATION_OBSERVED_NOT_SUBSET,
     ATTEMPT_INVALID_NOTE, SCAN_OUTCOME_INVALID),
    (2, 0, None, None, ATTEMPT_NO_COVERAGE_NOTE, SCAN_OUTCOME_NO_COVERAGE),
    (None, None, "ConnectionError", None, ATTEMPT_RAISED_NOTE, SCAN_OUTCOME_RAISED),
    (None, None, None, None, ATTEMPT_UNKNOWN_NOTE, SCAN_OUTCOME_UNKNOWN),
    (3, 1, None, None, ATTEMPT_PARTIAL_NOTE, SCAN_OUTCOME_PARTIAL_COVERAGE),
])
def test_postgres_rows_drive_the_freshness_note_even_when_data_is_recent(
    pg_isolated_connection, eligible, observed, failure, code, expected_note, expected_outcome,
):
    """A Postgres-persisted row, re-read, must still report its latest
    attempt's shortfall while the newest DATA is recent.

    The row is written and read back through the real Postgres
    repository, and the object handed to `compute_radar_freshness` is the
    one the repository RETURNED -- not a locally built stand-in and not a
    mocked outcome.
    """
    from src.data_access.postgres_state_db import scan_status_repository as pg_repo
    from src.data_access.postgres_state_db import schema as postgres_schema

    conn = pg_isolated_connection
    postgres_schema.migrate(conn)

    pg_repo.upsert_scan_status(conn, pg_repo.ProviderScanStatus(
        provider="SEC EDGAR",
        cursor_value="2026-10-26",
        started_at="2026-10-26T11:29:00+00:00",
        completed_at="2026-10-26T11:59:00+00:00",
        last_successful_at=_PG_RECENT_SUCCESS,
        items_discovered=0,
        candidates_created=0,
        skipped_unresolved_count=0,
        failure_code=failure,
        updated_at="2026-10-26T11:59:00+00:00",
        issuers_eligible=eligible,
        issuers_observed=observed,
        coverage_validation_code=code,
    ))

    status = pg_repo.get_scan_status(conn, "SEC EDGAR")

    # It really is the Postgres dataclass that came back out of Postgres.
    assert type(status).__module__ == "src.data_access.postgres_state_db.scan_status_repository"
    assert status.coverage_validation_code == code
    assert (status.issuers_eligible, status.issuers_observed) == (eligible, observed)
    assert status.last_successful_at == _PG_RECENT_SUCCESS
    assert status.outcome == expected_outcome

    freshness = compute_radar_freshness(
        "ok", {"SEC EDGAR": status}, ("SEC EDGAR",), _PG_INTERVAL_MINUTES, now=_PG_EVAL_NOW,
    )

    assert freshness.state == "all_recent", "the newest DATA is recent, and says so"
    assert freshness.attempt_note == expected_note, (
        "a recent success must not hide the latest attempt's shortfall"
    )
    assert freshness.attempt_note is not None
    assert expected_note not in freshness.message, (
        "the note is a SEPARATE line, never folded into the freshness message"
    )


def test_postgres_full_coverage_row_reports_no_shortfall_note(pg_isolated_connection):
    """The negative control for the parametrization above: without this,
    every case could pass by always returning a note."""
    from src.data_access.postgres_state_db import scan_status_repository as pg_repo
    from src.data_access.postgres_state_db import schema as postgres_schema

    conn = pg_isolated_connection
    postgres_schema.migrate(conn)

    pg_repo.upsert_scan_status(conn, pg_repo.ProviderScanStatus(
        provider="SEC EDGAR", cursor_value="2026-10-26",
        started_at="2026-10-26T11:29:00+00:00", completed_at="2026-10-26T11:59:00+00:00",
        last_successful_at=_PG_RECENT_SUCCESS, items_discovered=0, candidates_created=0,
        skipped_unresolved_count=0, failure_code=None, updated_at="2026-10-26T11:59:00+00:00",
        issuers_eligible=4, issuers_observed=4, coverage_validation_code=None,
    ))

    status = pg_repo.get_scan_status(conn, "SEC EDGAR")
    assert status.outcome == SCAN_OUTCOME_FULL_COVERAGE

    freshness = compute_radar_freshness(
        "ok", {"SEC EDGAR": status}, ("SEC EDGAR",), _PG_INTERVAL_MINUTES, now=_PG_EVAL_NOW,
    )

    assert freshness.state == "all_recent"
    assert freshness.attempt_note is None, "full coverage reports no shortfall"
