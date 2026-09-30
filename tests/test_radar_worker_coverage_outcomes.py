"""Ordered coverage classification, lane-wide precedence and bounded
history (Coverage Control Plane, Milestone 1).

Offline only: an in-memory fake repository and synthetic report objects.
No database, connection, credential, network or scan runs here — these
assert the WORKER's decision rules, which are the part a real database
cannot check for us.

The rule under test that matters most: a trustworthy no-data issuer is
in BOTH `no_data_companies` and `observed_companies` by construction, so
the order of evaluation is what decides whether it persists as NoData or
is silently flattened into Covered."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from scripts import radar_worker


class _FakeCoverageRepo:
    """Records exactly what the worker wrote, in order."""

    def __init__(self) -> None:
        self.statuses: dict[tuple[str, str], object] = {}
        self.events: list[object] = []

    def get_all_coverage_statuses(self) -> dict:
        return dict(self.statuses)

    def upsert_coverage_status(self, status) -> None:
        self.statuses[(status.issuer_id, status.lane)] = status

    def record_coverage_event(self, event) -> None:
        self.events.append(event)

    def get_coverage_events(self, lane: str | None = None) -> tuple:
        if lane is None:
            return tuple(self.events)
        return tuple(e for e in self.events if e.lane == lane)

    def state(self, issuer_id, lane="edgar"):
        return self.statuses[(issuer_id, lane)].coverage_state

    def row(self, issuer_id, lane="edgar"):
        return self.statuses[(issuer_id, lane)]


@dataclass(frozen=True)
class _Report:
    observed_companies: tuple[str, ...] = ()
    no_data_companies: tuple[str, ...] = ()
    companies_with_new_items: tuple[str, ...] = ()
    companies_with_new_material_items: tuple[str, ...] = ()


NOW = "2026-09-29T12:00:00+00:00"
LATER = "2026-09-29T13:00:00+00:00"


def _instruments(mapping):
    """{issuer_id: _LaneInstrument} from a compact {id: (name, active, resolved)}."""
    return {
        issuer_id: radar_worker._LaneInstrument(
            company_name=name, active=active, identity_resolved=resolved,
        )
        for issuer_id, (name, active, resolved) in mapping.items()
    }


def _patch_lane(monkeypatch, mapping):
    instruments = _instruments(mapping)
    monkeypatch.setattr(radar_worker, "_lane_instruments", lambda lane, settings: dict(instruments))
    return instruments


@pytest.fixture
def expected(monkeypatch):
    """Three synthetic EDGAR instruments, all active; the third has
    unresolved identity."""
    mapping = {
        "edgar:AAA": ("Alpha Corp", True, True),
        "edgar:BBB": ("Beta Corp", True, True),
        "edgar:CCC": ("Gamma Corp", True, False),
    }
    _patch_lane(monkeypatch, mapping)
    return mapping


# --- the ordered rules -----------------------------------------------------

def test_an_issuer_in_both_observed_and_no_data_persists_no_data_never_covered(expected):
    """The precedence case. A trustworthy no-data outcome IS an
    observation, so the issuer is legitimately in both sets."""
    repo = _FakeCoverageRepo()
    report = _Report(observed_companies=("Alpha Corp",), no_data_companies=("Alpha Corp",))

    radar_worker._apply_coverage_outcomes(repo, "edgar", None, report, NOW)

    assert repo.state("edgar:AAA") == "NoData"
    row = repo.row("edgar:AAA")
    assert row.last_success_at == NOW        # no-data still advances success
    assert row.last_no_data_at == NOW
    assert row.consecutive_empty_runs == 1


def test_an_issuer_observed_but_not_no_data_persists_covered(expected):
    repo = _FakeCoverageRepo()
    report = _Report(observed_companies=("Alpha Corp",), companies_with_new_items=("Alpha Corp",))

    radar_worker._apply_coverage_outcomes(repo, "edgar", None, report, NOW)

    row = repo.row("edgar:AAA")
    assert row.coverage_state == "Covered"
    assert row.last_success_at == NOW
    assert row.last_item_at == NOW
    assert row.last_no_data_at is None


def test_an_issuer_in_neither_set_persists_failing_untrusted(expected):
    repo = _FakeCoverageRepo()

    radar_worker._apply_coverage_outcomes(repo, "edgar", None, _Report(), NOW)

    row = repo.row("edgar:AAA")
    assert row.coverage_state == "Failing"
    assert row.blocking_reason == "untrusted_outcome"
    assert row.last_success_at is None
    assert row.consecutive_failures == 1


def test_unresolved_identity_persists_unmapped_not_failing_and_wins_over_no_data(expected):
    """Identity is checked first. Gamma is unresolved AND named in
    no_data_companies — the EDINET-complement shape — and must not be
    recorded as healthy no-data."""
    repo = _FakeCoverageRepo()
    report = _Report(observed_companies=("Gamma Corp",), no_data_companies=("Gamma Corp",))

    radar_worker._apply_coverage_outcomes(repo, "edgar", None, report, NOW)

    row = repo.row("edgar:CCC")
    assert row.coverage_state == "Unmapped"
    assert row.blocking_reason == "identity_unresolved"
    assert row.last_success_at is None


def test_only_covered_and_no_data_ever_advance_last_success_at(expected):
    repo = _FakeCoverageRepo()
    report = _Report(observed_companies=("Alpha Corp", "Beta Corp"), no_data_companies=("Beta Corp",))

    radar_worker._apply_coverage_outcomes(repo, "edgar", None, report, NOW)

    assert repo.row("edgar:AAA").last_success_at == NOW   # Covered
    assert repo.row("edgar:BBB").last_success_at == NOW   # NoData
    assert repo.row("edgar:CCC").last_success_at is None  # Unmapped


# --- lane-wide precedence --------------------------------------------------

def test_provider_wide_failure_marks_every_expected_issuer_and_advances_no_success(expected):
    repo = _FakeCoverageRepo()

    radar_worker._apply_lane_failure(repo, "edgar", None, "provider_error", "EdgarError", NOW)

    assert {repo.state(i) for i in expected} == {"Failing"}
    assert all(repo.row(i).blocking_reason == "provider_error" for i in expected)
    assert all(repo.row(i).last_success_at is None for i in expected)


def test_provider_wide_failure_writes_one_lane_event_not_one_per_issuer(expected):
    repo = _FakeCoverageRepo()

    radar_worker._apply_lane_failure(repo, "edgar", None, "provider_error", "EdgarError", NOW)

    assert len(repo.events) == 1
    event = repo.events[0]
    assert event.scope == "lane"
    assert event.issuer_id is None
    assert (event.from_state, event.to_state) == ("lane_healthy", "lane_failing")


def test_edinet_empty_observed_is_lane_untrusted_and_ignores_the_no_data_complement(monkeypatch):
    """The defect this milestone exists to prevent: EDINET derives
    no-data by complement, so a failed day fetch names every company."""
    _patch_lane(monkeypatch, {"edinet:X": ("Xco", True, True), "edinet:Y": ("Yco", True, True)})
    repo = _FakeCoverageRepo()
    report = _Report(observed_companies=(), no_data_companies=("Xco", "Yco"))

    radar_worker._apply_coverage_outcomes(repo, "edinet", None, report, NOW)

    assert {repo.state(i, "edinet") for i in ("edinet:X", "edinet:Y")} == {"Failing"}
    assert all(repo.row(i, "edinet").blocking_reason == "lane_untrusted" for i in ("edinet:X", "edinet:Y"))
    assert all(repo.row(i, "edinet").last_success_at is None for i in ("edinet:X", "edinet:Y"))
    assert len(repo.events) == 1 and repo.events[0].scope == "lane"


def test_lane_wide_failure_overrides_a_fully_populated_per_company_result(monkeypatch):
    _patch_lane(monkeypatch, {"edinet:X": ("Xco", True, True)})
    repo = _FakeCoverageRepo()
    # Xco looks perfectly healthy per-company, but the lane is untrusted.
    report = _Report(observed_companies=(), no_data_companies=(), companies_with_new_items=("Xco",))

    radar_worker._apply_coverage_outcomes(repo, "edinet", None, report, NOW)

    assert repo.state("edinet:X", "edinet") == "Failing"
    assert repo.row("edinet:X", "edinet").last_success_at is None


# --- bounded history -------------------------------------------------------

def test_covered_to_no_data_and_back_is_audit_silent_but_keeps_distinct_state(expected):
    repo = _FakeCoverageRepo()
    covered = _Report(observed_companies=("Alpha Corp",))
    no_data = _Report(observed_companies=("Alpha Corp",), no_data_companies=("Alpha Corp",))

    radar_worker._apply_coverage_outcomes(repo, "edgar", None, covered, NOW)
    before = len(repo.events)
    radar_worker._apply_coverage_outcomes(repo, "edgar", None, no_data, LATER)
    assert repo.state("edgar:AAA") == "NoData"
    radar_worker._apply_coverage_outcomes(repo, "edgar", None, covered, LATER)
    assert repo.state("edgar:AAA") == "Covered"

    # No event for either direction, but the timestamps stayed distinct.
    assert len(repo.events) == before
    assert repo.row("edgar:AAA").last_no_data_at == LATER
    assert repo.row("edgar:AAA").consecutive_empty_runs == 0


def test_repeated_identical_ticks_emit_no_further_events(expected):
    repo = _FakeCoverageRepo()
    report = _Report(observed_companies=("Alpha Corp", "Beta Corp"))

    for _ in range(10):
        radar_worker._apply_coverage_outcomes(repo, "edgar", None, report, NOW)

    assert len(repo.statuses) == 3
    assert repo.events == []


def test_a_sustained_lane_failure_emits_exactly_one_event(expected):
    repo = _FakeCoverageRepo()

    for _ in range(25):
        radar_worker._apply_lane_failure(repo, "edgar", None, "provider_error", "EdgarError", NOW)

    assert len(repo.events) == 1


def test_lane_recovery_emits_one_lane_event_and_rows_move_individually(expected):
    repo = _FakeCoverageRepo()
    radar_worker._apply_lane_failure(repo, "edgar", None, "provider_error", "EdgarError", NOW)
    repo.events.clear()

    recovery = _Report(
        observed_companies=("Alpha Corp", "Beta Corp"), no_data_companies=("Beta Corp",),
    )
    radar_worker._apply_coverage_outcomes(repo, "edgar", None, recovery, LATER)

    lane_events = [e for e in repo.events if e.scope == "lane"]
    assert len(lane_events) == 1
    assert (lane_events[0].from_state, lane_events[0].to_state) == ("lane_failing", "lane_healthy")
    # Issuers that recovered INTO health are covered by that one lane
    # event. Gamma did not recover into health — it came out of the lane
    # incident into its own identity problem — so it keeps its own event.
    instrument_events = [e for e in repo.events if e.scope == "instrument"]
    assert [(e.issuer_id, e.to_state) for e in instrument_events] == [("edgar:CCC", "Unmapped")]
    # Rows moved to their own real outcomes, not to one shared state.
    assert repo.state("edgar:AAA") == "Covered"
    assert repo.state("edgar:BBB") == "NoData"
    assert repo.state("edgar:CCC") == "Unmapped"


def test_an_instrument_transition_out_of_health_emits_an_instrument_event(expected):
    repo = _FakeCoverageRepo()
    radar_worker._apply_coverage_outcomes(repo, "edgar", None, _Report(observed_companies=("Alpha Corp",)), NOW)
    repo.events.clear()

    radar_worker._apply_coverage_outcomes(repo, "edgar", None, _Report(), LATER)

    instrument_events = [e for e in repo.events if e.scope == "instrument" and e.issuer_id == "edgar:AAA"]
    assert len(instrument_events) == 1
    assert instrument_events[0].to_state == "Failing"


def test_an_unresolvable_scan_name_is_recorded_and_never_treated_as_no_data(expected):
    repo = _FakeCoverageRepo()
    report = _Report(observed_companies=("Alpha Corp", "Totally Unknown Co"))

    radar_worker._apply_coverage_outcomes(repo, "edgar", None, report, NOW)

    integrity = [e for e in repo.events if e.scope == "integrity"]
    assert len(integrity) == 1
    assert integrity[0].issuer_id is None
    assert integrity[0].to_state == "integrity_present"
    assert integrity[0].blocking_reason == "unresolvable_scan_name"
    # The raw provider name never reaches the row — only a digest.
    assert "Totally Unknown Co" not in integrity[0].detail
    assert len(repo.statuses) == 3          # never silently added as an instrument


# --- HIGH-1: inactive registry issuers become NotExpected ------------------
#
# The registry is the authoritative universe. A company that goes
# inactive stops appearing in the lane service's active list, so if it
# were simply skipped its row would freeze on its last state and then
# age into Stale — reported forever as a gap for an instrument nobody
# expects to cover.

@pytest.fixture
def mixed_activity(monkeypatch):
    return _patch_lane(monkeypatch, {
        "edgar:AAA": ("Alpha Corp", True, True),
        "edgar:ZZZ": ("Zeta Corp", False, True),   # in the registry, no longer scanned
    })


def test_an_inactive_registry_issuer_is_persisted_as_not_expected(mixed_activity):
    repo = _FakeCoverageRepo()

    radar_worker._apply_coverage_outcomes(
        repo, "edgar", None, _Report(observed_companies=("Alpha Corp",)), NOW,
    )

    row = repo.row("edgar:ZZZ")
    assert row.coverage_state == "NotExpected"
    assert row.expected is False
    assert repo.state("edgar:AAA") == "Covered"   # the active one is unaffected


def test_not_expected_preserves_prior_timestamps_and_counters(monkeypatch):
    """Leaving the scanned universe is not an observation, so neither
    attempt nor success may move."""
    _patch_lane(monkeypatch, {"edgar:AAA": ("Alpha Corp", True, True)})
    repo = _FakeCoverageRepo()
    radar_worker._apply_coverage_outcomes(
        repo, "edgar", None,
        _Report(observed_companies=("Alpha Corp",), no_data_companies=("Alpha Corp",)), NOW,
    )
    before = repo.row("edgar:AAA")

    _patch_lane(monkeypatch, {"edgar:AAA": ("Alpha Corp", False, True)})
    radar_worker._apply_coverage_outcomes(repo, "edgar", None, _Report(), LATER)

    after = repo.row("edgar:AAA")
    assert after.coverage_state == "NotExpected"
    assert after.last_attempt_at == before.last_attempt_at
    assert after.last_success_at == before.last_success_at
    assert after.last_no_data_at == before.last_no_data_at
    assert after.consecutive_empty_runs == before.consecutive_empty_runs


def test_crossing_into_not_expected_emits_exactly_one_event_then_stays_silent(monkeypatch):
    _patch_lane(monkeypatch, {"edgar:AAA": ("Alpha Corp", True, True)})
    repo = _FakeCoverageRepo()
    radar_worker._apply_coverage_outcomes(repo, "edgar", None, _Report(observed_companies=("Alpha Corp",)), NOW)
    repo.events.clear()

    _patch_lane(monkeypatch, {"edgar:AAA": ("Alpha Corp", False, True)})
    for _ in range(5):
        radar_worker._apply_coverage_outcomes(repo, "edgar", None, _Report(), LATER)

    crossings = [e for e in repo.events if e.to_state == "NotExpected"]
    assert len(crossings) == 1
    assert crossings[0].scope == "instrument"
    assert crossings[0].issuer_id == "edgar:AAA"


def test_a_reactivated_issuer_returns_to_normal_outcome_evaluation(monkeypatch):
    _patch_lane(monkeypatch, {"edgar:AAA": ("Alpha Corp", False, True)})
    repo = _FakeCoverageRepo()
    radar_worker._apply_coverage_outcomes(repo, "edgar", None, _Report(), NOW)
    assert repo.state("edgar:AAA") == "NotExpected"

    _patch_lane(monkeypatch, {"edgar:AAA": ("Alpha Corp", True, True)})
    radar_worker._apply_coverage_outcomes(
        repo, "edgar", None, _Report(observed_companies=("Alpha Corp",)), LATER,
    )

    row = repo.row("edgar:AAA")
    assert row.coverage_state == "Covered"
    assert row.expected is True
    assert row.last_success_at == LATER


def test_a_lane_wide_failure_still_marks_inactive_issuers_not_expected(mixed_activity):
    repo = _FakeCoverageRepo()

    radar_worker._apply_lane_failure(repo, "edgar", None, "provider_error", "EdgarError", NOW)

    assert repo.state("edgar:AAA") == "Failing"
    assert repo.state("edgar:ZZZ") == "NotExpected"
    assert repo.row("edgar:ZZZ").last_attempt_at is None


# --- HIGH-3 / MEDIUM-1: the unknown-name integrity condition ---------------

def _integrity(repo):
    return [e for e in repo.events if e.scope == "integrity"]


def test_a_first_unknown_name_records_one_integrity_event(expected):
    repo = _FakeCoverageRepo()

    radar_worker._apply_coverage_outcomes(
        repo, "edgar", None, _Report(observed_companies=("Alpha Corp", "Ghost Co")), NOW,
    )

    events = _integrity(repo)
    assert len(events) == 1
    assert events[0].scope == "integrity"        # never "instrument" with a null issuer
    assert events[0].issuer_id is None
    assert (events[0].from_state, events[0].to_state) == ("integrity_clear", "integrity_present")
    assert "Ghost Co" not in events[0].detail    # digest only, never the raw name


def test_an_exact_replay_of_the_same_unknown_name_writes_nothing_further(expected):
    repo = _FakeCoverageRepo()
    report = _Report(observed_companies=("Alpha Corp", "Ghost Co"))

    radar_worker._apply_coverage_outcomes(repo, "edgar", None, report, NOW)
    radar_worker._apply_coverage_outcomes(repo, "edgar", None, report, NOW)

    assert len(_integrity(repo)) == 1


def test_a_persistent_unknown_name_does_not_write_one_event_per_tick(expected):
    repo = _FakeCoverageRepo()
    report = _Report(observed_companies=("Alpha Corp", "Ghost Co"))

    for _ in range(30):
        radar_worker._apply_coverage_outcomes(repo, "edgar", None, report, NOW)

    assert len(_integrity(repo)) == 1


def test_a_changed_unknown_set_records_one_further_event(expected):
    repo = _FakeCoverageRepo()
    radar_worker._apply_coverage_outcomes(
        repo, "edgar", None, _Report(observed_companies=("Ghost Co",)), NOW,
    )
    radar_worker._apply_coverage_outcomes(
        repo, "edgar", None, _Report(observed_companies=("Ghost Co", "Phantom Inc")), LATER,
    )

    events = _integrity(repo)
    assert len(events) == 2
    assert (events[1].from_state, events[1].to_state) == ("integrity_present", "integrity_present")
    assert events[0].detail != events[1].detail


def test_the_unknown_set_is_order_independent(expected):
    repo = _FakeCoverageRepo()
    radar_worker._apply_coverage_outcomes(
        repo, "edgar", None, _Report(observed_companies=("Ghost Co", "Phantom Inc")), NOW,
    )
    radar_worker._apply_coverage_outcomes(
        repo, "edgar", None, _Report(observed_companies=("Phantom Inc", "Ghost Co")), LATER,
    )

    assert len(_integrity(repo)) == 1


def test_a_cleared_unknown_set_records_one_clearing_event(expected):
    repo = _FakeCoverageRepo()
    radar_worker._apply_coverage_outcomes(
        repo, "edgar", None, _Report(observed_companies=("Alpha Corp", "Ghost Co")), NOW,
    )
    radar_worker._apply_coverage_outcomes(
        repo, "edgar", None, _Report(observed_companies=("Alpha Corp",)), LATER,
    )

    events = _integrity(repo)
    assert len(events) == 2
    assert (events[1].from_state, events[1].to_state) == ("integrity_present", "integrity_clear")
    assert events[1].detail == ""


def test_a_clean_lane_never_records_an_integrity_event(expected):
    repo = _FakeCoverageRepo()

    for _ in range(5):
        radar_worker._apply_coverage_outcomes(
            repo, "edgar", None, _Report(observed_companies=("Alpha Corp",)), NOW,
        )

    assert _integrity(repo) == []


# --- MEDIUM-2: a genuine failure during lane recovery keeps its history ----

def test_an_independent_failure_on_a_recovery_tick_still_emits_its_own_event(expected):
    """Only the recovery OF the lane incident is attributable to the
    lane. An issuer that independently fails on the same tick would
    otherwise never appear in history: the next tick sees no change."""
    repo = _FakeCoverageRepo()
    radar_worker._apply_lane_failure(repo, "edgar", None, "provider_error", "EdgarError", NOW)
    repo.events.clear()

    # Alpha recovers to Covered; Beta is independently untrustworthy.
    radar_worker._apply_coverage_outcomes(
        repo, "edgar", None, _Report(observed_companies=("Alpha Corp",)), LATER,
    )

    assert repo.state("edgar:AAA") == "Covered"
    assert repo.state("edgar:BBB") == "Failing"
    assert repo.row("edgar:BBB").blocking_reason == "untrusted_outcome"

    lane_events = [e for e in repo.events if e.scope == "lane"]
    instrument_events = [e for e in repo.events if e.scope == "instrument"]
    assert len(lane_events) == 1
    # Alpha's recovery is covered by the lane event and stays silent;
    # Beta's own failure and Gamma's identity problem do not.
    assert {e.issuer_id for e in instrument_events} == {"edgar:BBB", "edgar:CCC"}
