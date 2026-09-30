"""Research Case selection funnel — aggregate rejection-reason
observability.

Covers the additive `rejection_reason_counts` field on
`ResearchLeadOrchestrationResult` and the `rejection_reason_histogram()`
rendering the Radar worker's own research-case summary line now carries.

Two properties matter more than the counts themselves and are asserted
directly rather than implied:

  * SAFETY — the rendered string can only ever contain fixed literals
    from a closed allowlist. No issuer name, ticker, document id, URL,
    excerpt, provider payload or exception string can reach it, even
    when every one of those fields on the candidate is poisoned with a
    marker (see test_no_unsafe_field_can_reach_the_formatted_histogram).
  * NON-INTERFERENCE — aggregation is observability only. Every
    pre-existing selection decision, counter and bundle is byte-for-byte
    what it was before, including when aggregation itself fails.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from scripts import radar_worker
from src.config.settings import Settings
from src.data_access import backend_factory
from src.logic import research_lead_orchestration
from src.logic.research_lead_orchestration import (
    ResearchLeadOrchestrationConfig,
    ResearchLeadOrchestrationResult,
    prepare_research_case_bundles,
    rejection_reason_histogram,
)
from src.logic.research_lead_selection import (
    REJECTION_REASONS,
    LeadPriority,
    LeadSelectionResult,
)
from src.models.models import (
    CandidateSignal,
    CandidateStatus,
    ExtractionState,
    FilingEvent,
    StateTransition,
)

# Every original field of ResearchLeadOrchestrationResult, i.e. the
# result shape before this observability patch. Used to prove the change
# is strictly additive.
_ORIGINAL_RESULT_FIELDS = (
    "bundles",
    "evaluated_count",
    "skipped_count",
    "not_qualified_count",
    "already_existing_count",
    "membership_check_failed_count",
    "factory_rejected_count",
    "validation_rejected_count",
    "config_valid",
)


def _filing(**overrides) -> FilingEvent:
    defaults = dict(
        rcept_no="acc-1", corp_code="0000320193", corp_name="Apple Inc.", stock_code="AAPL",
        report_nm="8-K", rcept_dt="2026-08-15", flr_nm="Apple Inc.", source_name="SEC EDGAR",
        source_url="https://example.com/filing", retrieved_at="2026-08-15T01:00:00+00:00",
        original_language="English",
    )
    defaults.update(overrides)
    return FilingEvent(**defaults)


def _candidate(**overrides) -> CandidateSignal:
    defaults = dict(
        id="edgar-cand-1", filing=_filing(), matched_rules=["financing_or_debt:2.03", "material_agreement:1.01"],
        confidence="High", status=CandidateStatus.NEEDS_REVIEW, extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="The company entered into a financing agreement.",
        state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at="2026-08-15T00:00:00+00:00")],
    )
    defaults.update(overrides)
    return CandidateSignal(**defaults)


def _config(**overrides) -> ResearchLeadOrchestrationConfig:
    defaults = dict(as_of_date="2026-08-20", lookback_days=30, max_candidates=5)
    defaults.update(overrides)
    return ResearchLeadOrchestrationConfig(**defaults)


def _no_existing(_ids):
    return set()


# --- One rejected candidate ---------------------------------------------------


def test_one_rejected_candidate_reports_its_own_safe_reason_and_count():
    """A single candidate outside the lookback window renders exactly
    that reason at count 1 — the real shape the production diagnosis
    needs in order to choose a selection-policy repair."""
    stale = _candidate(id="edgar-cand-stale", filing=_filing(rcept_dt="2026-01-01"))

    result = prepare_research_case_bundles([stale], _no_existing, _config())

    assert result.not_qualified_count == 1
    assert rejection_reason_histogram(result) == "receipt_date_outside_lookback:1"


def test_a_candidate_failing_several_gates_reports_every_gate():
    """Reason OCCURRENCES, not candidates: one candidate blocked by
    three gates contributes three counts, so the sum may exceed
    not_qualified_count. Deliberate — a repair cannot be chosen from a
    single reason when several apply."""
    blocked = _candidate(
        id="edgar-cand-blocked",
        extraction_state=ExtractionState.PENDING,
        excerpt_original="",
        filing=_filing(rcept_dt="2026-01-01"),
    )

    result = prepare_research_case_bundles([blocked], _no_existing, _config())

    assert result.not_qualified_count == 1
    assert rejection_reason_histogram(result) == (
        "blank_original_excerpt:1,excerpt_not_extracted:1,receipt_date_outside_lookback:1"
    )


# --- Multiple rejected candidates, stable ordering ----------------------------


def test_multiple_rejections_render_in_stable_ascending_reason_order():
    candidates = [
        _candidate(id="c-1", filing=_filing(rcept_no="a1", rcept_dt="2026-01-01")),
        _candidate(id="c-2", filing=_filing(rcept_no="a2", rcept_dt="2026-01-02")),
        _candidate(id="c-3", filing=_filing(rcept_no="a3"), extraction_state=ExtractionState.PENDING),
    ]

    result = prepare_research_case_bundles(candidates, _no_existing, _config())

    assert result.not_qualified_count == 3
    # Alphabetical by reason, never by count and never by insertion
    # order: excerpt_not_extracted precedes receipt_date_outside_lookback
    # even though it is the rarer reason and was seen last.
    assert rejection_reason_histogram(result) == (
        "excerpt_not_extracted:1,receipt_date_outside_lookback:2"
    )


def test_histogram_is_byte_identical_across_repeated_and_reordered_runs():
    """Determinism is the whole point of a logged aggregate: the same
    batch must render identically every tick, and input order must not
    change the rendering."""
    candidates = [
        _candidate(id="c-1", filing=_filing(rcept_no="a1", rcept_dt="2026-01-01")),
        _candidate(id="c-2", filing=_filing(rcept_no="a2"), extraction_state=ExtractionState.PENDING),
        _candidate(id="c-3", filing=_filing(rcept_no="a3", rcept_dt="2026-01-03")),
    ]

    first = rejection_reason_histogram(prepare_research_case_bundles(candidates, _no_existing, _config()))
    again = rejection_reason_histogram(prepare_research_case_bundles(candidates, _no_existing, _config()))
    reversed_order = rejection_reason_histogram(
        prepare_research_case_bundles(list(reversed(candidates)), _no_existing, _config())
    )

    assert first == again == reversed_order


# --- Non-qualification outcomes are never counted as rejection reasons --------


def test_accepted_candidate_contributes_no_rejection_reason():
    result = prepare_research_case_bundles([_candidate()], _no_existing, _config())

    assert result.bundles
    assert result.not_qualified_count == 0
    assert rejection_reason_histogram(result) == "none"


def test_already_existing_case_is_not_a_qualification_rejection():
    """`existing` means the selector qualified the candidate and the
    bulk membership read found its case already present — a different
    outcome from NOT_QUALIFIED, and it must not appear in the
    histogram."""
    candidate = _candidate()
    qualified = prepare_research_case_bundles([candidate], _no_existing, _config())
    existing_case_id = qualified.bundles[0].case.id

    result = prepare_research_case_bundles([candidate], lambda _ids: {existing_case_id}, _config())

    assert result.already_existing_count == 1
    assert result.not_qualified_count == 0
    assert rejection_reason_histogram(result) == "none"


def test_membership_check_failure_is_not_a_qualification_rejection():
    def _broken(_ids):
        raise RuntimeError("membership backend unavailable")

    result = prepare_research_case_bundles([_candidate()], _broken, _config())

    assert result.membership_check_failed_count == 1
    assert result.not_qualified_count == 0
    assert rejection_reason_histogram(result) == "none"


def test_factory_and_validation_rejections_are_not_qualification_rejections(monkeypatch):
    monkeypatch.setattr(research_lead_orchestration, "build_research_case_bundle_from_lead", lambda *_a: None)

    result = prepare_research_case_bundles([_candidate()], _no_existing, _config())

    assert result.factory_rejected_count == 1
    assert result.not_qualified_count == 0
    assert rejection_reason_histogram(result) == "none"


def test_write_rejection_happens_after_this_module_and_cannot_be_counted():
    """Write rejection is the worker's own insert result, produced from
    `result.bundles` after prepare_research_case_bundles has already
    returned — structurally unable to reach the histogram."""
    result = prepare_research_case_bundles([_candidate()], _no_existing, _config())

    assert result.bundles
    assert rejection_reason_histogram(result) == "none"


def test_skipped_candidates_never_reach_the_selector_or_the_histogram():
    """Wrong source / wrong status are filtered before the selector, so
    they are `skipped`, never `not_qualified`, and contribute no
    reason — the same distinction the result dataclass already draws."""
    candidates = [
        _candidate(id="c-wrong-source", filing=_filing(source_name="OpenDART / DART")),
        _candidate(id="c-wrong-status", status=CandidateStatus.EXTRACTED),
        "not-a-candidate",
    ]

    result = prepare_research_case_bundles(candidates, _no_existing, _config())

    assert result.skipped_count == 3
    assert result.not_qualified_count == 0
    assert rejection_reason_histogram(result) == "none"


# --- No rejections ------------------------------------------------------------


def test_empty_batch_renders_none():
    assert rejection_reason_histogram(prepare_research_case_bundles([], _no_existing, _config())) == "none"


def test_invalid_config_renders_none_not_unavailable():
    """An invalid config short-circuits before any selector call, so
    there are genuinely zero rejection reasons. `config_valid=False`
    already carries that signal separately; the histogram must not
    invent an aggregation failure that did not happen."""
    result = prepare_research_case_bundles([_candidate()], _no_existing, _config(max_candidates=0))

    assert result.config_valid is False
    assert rejection_reason_histogram(result) == "none"


# --- Aggregation failure fails closed ----------------------------------------


def test_aggregation_failure_renders_unavailable_and_preserves_every_outcome(monkeypatch):
    """The guard's whole purpose: a raise inside aggregation degrades
    the log field to `unavailable` and changes nothing else."""
    healthy = prepare_research_case_bundles(
        [_candidate(), _candidate(id="c-stale", filing=_filing(rcept_no="a2", rcept_dt="2026-01-01"))],
        _no_existing, _config(),
    )

    def _boom(_counts, _selection):
        raise RuntimeError("aggregation exploded")

    monkeypatch.setattr(research_lead_orchestration, "_record_rejection_reasons", _boom)
    degraded = prepare_research_case_bundles(
        [_candidate(), _candidate(id="c-stale", filing=_filing(rcept_no="a2", rcept_dt="2026-01-01"))],
        _no_existing, _config(),
    )

    assert degraded.rejection_reason_counts is None
    assert rejection_reason_histogram(degraded) == "unavailable"
    # Every pre-existing field identical to the healthy run.
    for field in _ORIGINAL_RESULT_FIELDS:
        assert getattr(degraded, field) == getattr(healthy, field), field


def test_malformed_stored_counts_render_unavailable_rather_than_raising():
    for malformed in (
        "receipt_date_outside_lookback:1",
        (("receipt_date_outside_lookback",),),
        (("receipt_date_outside_lookback", -1),),
        (("receipt_date_outside_lookback", "3"),),
        (("receipt_date_outside_lookback", True),),
        42,
    ):
        result = dataclasses.replace(
            prepare_research_case_bundles([], _no_existing, _config()),
            rejection_reason_counts=malformed,
        )
        assert rejection_reason_histogram(result) == "unavailable", malformed


def test_histogram_never_raises_for_an_arbitrary_object():
    class _Hostile:
        @property
        def rejection_reason_counts(self):
            raise RuntimeError("attribute access exploded")

    assert rejection_reason_histogram(_Hostile()) == "unavailable"
    assert rejection_reason_histogram(object()) == "unavailable"
    assert rejection_reason_histogram(None) == "unavailable"


# --- Safety: only closed-vocabulary literals can ever be rendered ------------


def test_no_unsafe_field_can_reach_the_formatted_histogram():
    """Every free-text and identifier field on the candidate carries a
    distinctive marker. The candidate fails several gates, so its
    reasons are aggregated — and no marker may appear in the output."""
    poison = "UNSAFE-MARKER-9f3a"
    hostile = _candidate(
        id=f"cand-{poison}",
        filing=_filing(
            rcept_no=f"doc-{poison}",
            corp_code=f"corp-{poison}",
            corp_name=f"Issuer {poison} Inc.",
            stock_code=poison,
            report_nm=f"Form 8-K {poison}",
            flr_nm=f"Filer {poison}",
            source_url=f"https://example.com/{poison}",
            rcept_dt="2026-01-01",
        ),
        matched_rules=[f"{poison}:{poison}"],
        excerpt_original=f"Confidential body text {poison}.",
    )

    result = prepare_research_case_bundles([hostile], _no_existing, _config())
    rendered = rejection_reason_histogram(result)

    assert result.not_qualified_count == 1
    assert poison not in rendered
    assert poison.lower() not in rendered.lower()


def test_every_rendered_token_belongs_to_the_closed_vocabulary():
    """Structural guarantee rather than a spot check: whatever the
    batch, each emitted `reason:count` pair's reason is an allowlisted
    literal."""
    candidates = [
        _candidate(id="c-1", filing=_filing(rcept_no="a1", rcept_dt="2026-01-01")),
        _candidate(id="c-2", filing=_filing(rcept_no="a2"), extraction_state=ExtractionState.PENDING),
        _candidate(id="c-3", filing=_filing(rcept_no="a3"), confidence="Low"),
        _candidate(id="c-4", filing=_filing(rcept_no="a4"), matched_rules=[]),
        _candidate(id="c-5", filing=_filing(rcept_no="a5"), state_history=[]),
    ]

    rendered = rejection_reason_histogram(prepare_research_case_bundles(candidates, _no_existing, _config()))

    assert rendered not in ("none", "unavailable")
    for pair in rendered.split(","):
        reason, _, count = pair.rpartition(":")
        assert reason in research_lead_orchestration._HISTOGRAM_VOCABULARY, reason
        assert count.isdigit(), pair


def test_an_unrecognized_selector_reason_is_counted_as_other_not_passed_through(monkeypatch):
    """Forward safety: if the selector's vocabulary grows later, an
    unknown token must be absorbed into `other`, never emitted."""
    leaky = LeadSelectionResult(
        priority=LeadPriority.NOT_QUALIFIED,
        reasons=("receipt_date_outside_lookback", "brand_new_reason_with Issuer Name Inc."),
        normalized_categories=(),
        case_id=None,
    )
    monkeypatch.setattr(research_lead_orchestration, "select_research_lead", lambda *_a: leaky)

    rendered = rejection_reason_histogram(
        prepare_research_case_bundles([_candidate()], _no_existing, _config())
    )

    assert rendered == "other:1,receipt_date_outside_lookback:1"
    assert "Issuer Name" not in rendered


def test_a_qualified_selection_with_a_blank_case_id_never_contributes_category_tokens():
    """The one rejection this module makes itself. A QUALIFIED
    selection's `reasons` carry a `category:<slug>` entry derived from
    source-controlled matched_rules, so they must be replaced by a fixed
    token rather than counted."""
    counts: dict[str, int] = {}
    qualified_but_unusable = LeadSelectionResult(
        priority=LeadPriority.QUALIFIED,
        reasons=("source_recognized", "category:UNSAFE-MARKER", "priority_qualified"),
        normalized_categories=("UNSAFE-MARKER",),
        case_id="",
    )

    research_lead_orchestration._record_rejection_reasons(counts, qualified_but_unusable)

    assert counts == {"qualified_without_case_id": 1}
    assert "UNSAFE-MARKER" not in ",".join(counts)


def test_closed_vocabulary_matches_the_selector_module_and_is_all_fixed_literals():
    assert REJECTION_REASONS
    assert "receipt_date_outside_lookback" in REJECTION_REASONS
    assert "excerpt_not_extracted" in REJECTION_REASONS
    # Success-path tokens must never be treated as rejection reasons.
    assert "receipt_date_within_lookback" not in REJECTION_REASONS
    assert "priority_qualified" not in REJECTION_REASONS
    for reason in REJECTION_REASONS:
        assert isinstance(reason, str)
        assert reason and reason.replace("_", "").isalnum() and reason.islower()


# --- The change is strictly additive ------------------------------------------


def test_result_shape_is_the_original_fields_plus_exactly_one_new_defaulted_field():
    fields = dataclasses.fields(ResearchLeadOrchestrationResult)
    names = tuple(field.name for field in fields)

    assert names[: len(_ORIGINAL_RESULT_FIELDS)] == _ORIGINAL_RESULT_FIELDS
    assert names[len(_ORIGINAL_RESULT_FIELDS):] == ("rejection_reason_counts",)
    # The new field is the only one with a default, so every existing
    # construction of this dataclass keeps working unchanged.
    defaulted = [f.name for f in fields if f.default is not dataclasses.MISSING]
    assert defaulted == ["rejection_reason_counts"]


def test_selection_outcomes_are_unchanged_across_a_representative_batch():
    """Pins every pre-existing counter and the surviving bundle for a
    mixed batch, so a future change to aggregation cannot quietly move
    a selection decision."""
    candidates = [
        _candidate(id="c-ok"),
        _candidate(id="c-stale", filing=_filing(rcept_no="a2", rcept_dt="2026-01-01")),
        _candidate(id="c-wrong-source", filing=_filing(rcept_no="a3", source_name="EDINET")),
        _candidate(id="c-low", filing=_filing(rcept_no="a4"), confidence="Low"),
    ]

    result = prepare_research_case_bundles(candidates, _no_existing, _config())

    assert result.evaluated_count == 3
    assert result.skipped_count == 1
    assert result.not_qualified_count == 2
    assert result.already_existing_count == 0
    assert result.membership_check_failed_count == 0
    assert result.factory_rejected_count == 0
    assert result.validation_rejected_count == 0
    assert result.config_valid is True
    assert len(result.bundles) == 1
    assert result.bundles[0].case.trigger_source_id == "c-ok"
    # ...and the additive field alongside them.
    assert rejection_reason_histogram(result) == "confidence_not_qualified:1,receipt_date_outside_lookback:1"


def test_results_remain_equality_comparable_and_deterministic():
    a = prepare_research_case_bundles([_candidate(filing=_filing(rcept_dt="2026-01-01"))], _no_existing, _config())
    b = prepare_research_case_bundles([_candidate(filing=_filing(rcept_dt="2026-01-01"))], _no_existing, _config())

    assert a == b
    assert a.rejection_reason_counts == (("receipt_date_outside_lookback", 1),)


# --- Canonicalization: an equal-valued str subclass cannot inject -------------


class _HostileReason(str):
    """Compares and hashes as its allowlisted value, but renders as
    something else — the exact shape an allowlist based on `in` lets
    through and a canonical mapping does not."""

    def __str__(self) -> str:  # pragma: no cover - only reached on a leak
        return "INJECTED-PAYLOAD"

    def __format__(self, _spec: str) -> str:  # pragma: no cover - only reached on a leak
        return "INJECTED-PAYLOAD"


class _HostileCount(int):
    def __str__(self) -> str:  # pragma: no cover - only reached on a leak
        return "INJECTED-PAYLOAD"

    def __format__(self, _spec: str) -> str:  # pragma: no cover - only reached on a leak
        return "INJECTED-PAYLOAD"

    def __radd__(self, _other):  # defeats laundering via `0 + count`
        return self


def _result_with(counts):
    return ResearchLeadOrchestrationResult(
        bundles=(), evaluated_count=0, skipped_count=0, not_qualified_count=0,
        already_existing_count=0, membership_check_failed_count=0,
        factory_rejected_count=0, validation_rejected_count=0, config_valid=True,
        rejection_reason_counts=counts,
    )


def test_hostile_str_subclass_renders_the_canonical_token_not_its_own_format():
    hostile = _HostileReason("receipt_date_outside_lookback")
    # Precondition: it really does pass a naive allowlist check.
    assert hostile in research_lead_orchestration._HISTOGRAM_VOCABULARY
    assert f"{hostile}" == "INJECTED-PAYLOAD"

    rendered = rejection_reason_histogram(_result_with(((hostile, 2),)))

    assert rendered == "receipt_date_outside_lookback:2"
    assert "INJECTED" not in rendered


def test_hostile_int_subclass_renders_a_plain_integer():
    hostile = _HostileCount(4)
    assert f"{hostile}" == "INJECTED-PAYLOAD"

    rendered = rejection_reason_histogram(
        _result_with((("receipt_date_outside_lookback", hostile),))
    )

    assert rendered == "receipt_date_outside_lookback:4"
    assert "INJECTED" not in rendered


def test_hostile_str_subclass_is_canonicalized_at_aggregation_time_too():
    counts: dict[str, int] = {}
    selection = LeadSelectionResult(
        priority=LeadPriority.NOT_QUALIFIED,
        reasons=(_HostileReason("excerpt_not_extracted"),),
        normalized_categories=(), case_id=None,
    )

    research_lead_orchestration._record_rejection_reasons(counts, selection)

    assert counts == {"excerpt_not_extracted": 1}
    # The stored key is a plain str, not the subclass instance.
    stored_key = next(iter(counts))
    assert type(stored_key) is str
    assert f"{stored_key}" == "excerpt_not_extracted"


def test_unhashable_and_hostile_eq_reasons_become_other():
    class _ExplodingEq(str):
        def __hash__(self):
            raise RuntimeError("hash exploded")

    for reason in ([], {}, object(), None, 7, _ExplodingEq("excerpt_not_extracted")):
        counts: dict[str, int] = {}
        selection = LeadSelectionResult(
            priority=LeadPriority.NOT_QUALIFIED, reasons=(reason,),
            normalized_categories=(), case_id=None,
        )
        research_lead_orchestration._record_rejection_reasons(counts, selection)
        assert counts == {"other": 1}, reason


# --- Priority semantics match the selection call site ------------------------


def test_raw_string_priority_is_classified_like_the_selector_call_site():
    """`LeadPriority` subclasses `str`, so prepare_research_case_bundles'
    own `==` check treats the raw string as a rejection. The aggregator
    must agree, or the histogram would disagree with the decision it
    reports on."""
    assert LeadPriority.NOT_QUALIFIED == "NOT_QUALIFIED"

    class _RawPriority:
        priority = "NOT_QUALIFIED"
        reasons = ("receipt_date_outside_lookback",)
        case_id = None

    counts: dict[str, int] = {}
    research_lead_orchestration._record_rejection_reasons(counts, _RawPriority())

    assert counts == {"receipt_date_outside_lookback": 1}
    assert "qualified_without_case_id" not in counts


def test_a_priority_whose_equality_raises_is_not_counted_as_a_reason():
    class _ExplodingPriority:
        def __eq__(self, _other):
            raise RuntimeError("eq exploded")

    class _Selection:
        priority = _ExplodingPriority()
        reasons = ("receipt_date_outside_lookback",)
        case_id = None

    counts: dict[str, int] = {}
    research_lead_orchestration._record_rejection_reasons(counts, _Selection())

    assert counts == {"other": 1}


# --- Malformed selections record `other`, never qualified_without_case_id ----


@pytest.mark.parametrize("selection", [None, object(), "not-a-selection", 42, [], {"priority": "x"}])
def test_malformed_selection_records_other_not_qualified_without_case_id(selection):
    counts: dict[str, int] = {}

    research_lead_orchestration._record_rejection_reasons(counts, selection)

    assert counts == {"other": 1}
    assert "qualified_without_case_id" not in counts


def test_high_signal_enum_with_blank_case_id_is_also_case_two():
    """The genuine case-2 path keeps its distinct label for every real
    LeadPriority member — the malformed fix must not collapse it into
    `other`. QUALIFIED is covered above; this pins HIGH_SIGNAL."""
    counts: dict[str, int] = {}
    research_lead_orchestration._record_rejection_reasons(
        counts,
        LeadSelectionResult(
            priority=LeadPriority.HIGH_SIGNAL, reasons=("category:x",),
            normalized_categories=("x",), case_id="",
        ),
    )

    assert counts == {"qualified_without_case_id": 1}


# --- Worker integration: both real summary emitters carry the field ---------
#
# These exercise scripts/radar_worker.py's OWN research-case steps against
# a real in-file SQLite backend, not the formatter in isolation: the
# patch exists solely so one production tick prints this field, so a
# formatter-only test would leave its single deliverable unprotected.
# `_run_edgar_research_case_step` and `_run_source_research_case_step`
# are separate emitters and are pinned separately. No scan, network or
# worker process is involved — the steps are called directly.


def _worker_settings(tmp_path):
    ambient = Settings(
        radar_live_scan_enabled=True,
        radar_worker_db_backend="sqlite",
        radar_worker_state_db_path=tmp_path / "state.db",
        radar_worker_state_db_url=None,
        edgar_auto_publish_enabled=False,
    )
    return radar_worker._build_worker_settings(ambient)


_WORKER_LANES = {
    "edgar": "SEC EDGAR",
    "dart": "OpenDART / DART",
    "edinet": "EDINET",
}


def _lane_candidate(provider_key, rcept_no="acc-1", rcept_dt="2026-08-15"):
    display_source = _WORKER_LANES[provider_key]
    filing = FilingEvent(
        rcept_no=rcept_no, corp_code=f"code-{rcept_no}", corp_name="Example Corp", stock_code="X",
        report_nm="Material Disclosure", rcept_dt=rcept_dt, flr_nm="Example Corp",
        source_name=display_source, source_url="https://example.com/filing",
        retrieved_at=rcept_dt + "T01:00:00+00:00", original_language="English",
    )
    return CandidateSignal(
        id=f"{provider_key}-cand-{rcept_no}", filing=filing,
        matched_rules=["financing_or_debt:2.03", "material_agreement:1.01"],
        confidence="High", status=CandidateStatus.NEEDS_REVIEW,
        extraction_state=ExtractionState.EXTRACTED,
        excerpt_original="The company entered into a financing agreement.",
        state_history=[StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at=rcept_dt + "T00:00:00+00:00")],
    )


def _run_lane_step(provider_key, worker_settings):
    repo = backend_factory.get_candidate_repository(worker_settings, _WORKER_LANES[provider_key])
    if provider_key == "edgar":
        return radar_worker._run_edgar_research_case_step(worker_settings, repo)
    return radar_worker._run_source_research_case_step(provider_key, worker_settings, repo)


def _seed(worker_settings, provider_key, *candidates):
    repo = backend_factory.get_candidate_repository(worker_settings, _WORKER_LANES[provider_key])
    repo.upsert_new_candidates(list(candidates))


@pytest.fixture
def _fixed_as_of(monkeypatch):
    monkeypatch.setattr(radar_worker, "_current_utc_date", lambda: "2026-08-20")


@pytest.mark.parametrize("provider_key", ["edgar", "dart", "edinet"])
def test_worker_summary_carries_rejection_reasons_for_every_lane(tmp_path, provider_key, _fixed_as_of):
    """Presence check on the real emitter for all three lanes, on the
    no-rejection path."""
    worker_settings = _worker_settings(tmp_path)
    _seed(worker_settings, provider_key, _lane_candidate(provider_key))

    summary, _candidates, _cases = _run_lane_step(provider_key, worker_settings)

    assert "rejection_reasons=" in summary
    assert "rejection_reasons=none" in summary
    assert "created=1" in summary


@pytest.mark.parametrize("provider_key", ["edgar", "dart", "edinet"])
def test_worker_summary_reports_the_real_histogram_when_candidates_are_rejected(
    tmp_path, provider_key, _fixed_as_of,
):
    """Value check, not just presence: a stale candidate must surface
    the exact reason a production tick would print. EDINET's own
    5-day lookback makes 2026-01-01 stale on every lane."""
    worker_settings = _worker_settings(tmp_path)
    _seed(
        worker_settings, provider_key,
        _lane_candidate(provider_key, rcept_no="acc-old", rcept_dt="2026-01-01"),
    )

    summary, _candidates, cases = _run_lane_step(provider_key, worker_settings)

    assert "rejection_reasons=receipt_date_outside_lookback:1" in summary
    assert "not_qualified=1" in summary
    assert "created=0" in summary
    assert cases == ()


@pytest.mark.parametrize("provider_key", ["edgar", "dart", "edinet"])
def test_worker_summary_field_order_is_stable(tmp_path, provider_key, _fixed_as_of):
    """Placement is fixed: rejection_reasons follows write_rejected, so
    a log reader can rely on the position across ticks and lanes."""
    worker_settings = _worker_settings(tmp_path)
    _seed(worker_settings, provider_key, _lane_candidate(provider_key))

    summary, _candidates, _cases = _run_lane_step(provider_key, worker_settings)

    assert " write_rejected=0 rejection_reasons=none" in summary
    assert summary.startswith(f"{provider_key.upper()}: research cases — ")


def test_both_worker_emitters_are_covered_by_these_tests():
    """Guards the guard: if a third research-case summary emitter is
    added to radar_worker.py, this fails rather than silently leaving
    it unpinned."""
    source = Path(radar_worker.__file__).read_text()
    emitters = source.count('research cases — evaluated=')
    assert emitters == 2, f"expected 2 research-case summary emitters, found {emitters}"
    assert source.count("rejection_reasons={rejection_reason_histogram(result)}") == emitters


# --- Newest-first evaluation window, through the real worker steps ----------
#
# Repair A changes only the ORDER in which eligible NEEDS_REVIEW
# candidates enter the bounded evaluation window. These exercise
# radar_worker's own research-case steps against a real in-file SQLite
# backend so the ordering claim is proven end-to-end, per lane, rather
# than only at the pure-orchestration layer.


def _lane_candidate_dated(provider_key, rcept_no, rcept_dt, *, iso_timestamp_date=None):
    """Same shape as _lane_candidate, with an explicit receipt date.
    Dashed ISO here for every lane in the ordering cases: those are about
    ORDERING, and a lane's own date representation is Repair B's subject,
    not this patch's. The DART-native case is covered separately below.

    `iso_timestamp_date` supplies the ISO calendar date used for
    `retrieved_at` and for the CANDIDATE_DETECTED transition, and is
    required whenever `rcept_dt` is NOT dashed ISO. `_lane_candidate`
    derives both timestamps from `rcept_dt`, which is convenient while
    the two shapes coincide but does not mirror production: a scan sets
    `retrieved_at` and the detection timestamp independently, in ISO,
    and only `rcept_dt` ever carries a lane's source-native shape. A
    fixture that let a compact receipt date leak into those two fields
    would be exercising a record that cannot occur."""
    candidate = _lane_candidate(provider_key, rcept_no=rcept_no, rcept_dt=rcept_dt)
    if iso_timestamp_date is None:
        return candidate
    return dataclasses.replace(
        candidate,
        filing=dataclasses.replace(candidate.filing, retrieved_at=f"{iso_timestamp_date}T01:00:00+00:00"),
        state_history=[
            StateTransition(status=CandidateStatus.CANDIDATE_DETECTED, at=f"{iso_timestamp_date}T00:00:00+00:00"),
        ],
    )


@pytest.mark.parametrize("provider_key", ["edgar", "edinet"])
def test_worker_reaches_a_fresh_candidate_ahead_of_stale_ones(tmp_path, provider_key, _fixed_as_of):
    """The production livelock, end-to-end: five expired candidates would
    previously have filled the whole window every tick. Newest-first must
    reach the in-window candidate queued behind them.

    2026-08-19 is inside every lane's lookback (EDGAR/DART 30 days,
    EDINET 5) relative to the fixed as-of date of 2026-08-20."""
    worker_settings = _worker_settings(tmp_path)
    stale = [_lane_candidate_dated(provider_key, f"acc-old-{i}", f"2026-01-{i + 1:02d}") for i in range(5)]
    fresh = _lane_candidate_dated(provider_key, "acc-fresh", "2026-08-19")
    _seed(worker_settings, provider_key, *stale, fresh)

    summary, _candidates, cases = _run_lane_step(provider_key, worker_settings)

    assert "created=1" in summary
    assert len(cases) == 1
    assert cases[0].trigger_source_id == fresh.id
    # The safe histogram still renders, and now reports the stale
    # candidates that shared the window rather than filling it.
    assert "rejection_reasons=" in summary
    assert "receipt_date_outside_lookback" in summary


@pytest.mark.parametrize("provider_key", ["edgar", "edinet"])
def test_worker_creates_nothing_when_every_candidate_is_stale(tmp_path, provider_key, _fixed_as_of):
    """The informative non-success signature: newest-first cannot help
    when the whole pool is expired. Ordering is not a substitute for
    fresh input, and this pins that honestly."""
    worker_settings = _worker_settings(tmp_path)
    _seed(
        worker_settings, provider_key,
        *[_lane_candidate_dated(provider_key, f"acc-old-{i}", f"2026-01-{i + 1:02d}") for i in range(6)],
    )

    summary, _candidates, cases = _run_lane_step(provider_key, worker_settings)

    assert "created=0" in summary
    assert "not_qualified=5" in summary
    assert cases == ()
    assert "rejection_reasons=receipt_date_outside_lookback:5" in summary


def test_dart_native_compact_date_still_reports_invalid_receipt_date(tmp_path, _fixed_as_of):
    """DART is inside the shared ordering policy, and this patch is NOT
    its date repair.

    The fixture deliberately carries DART's source-native compact
    receipt-date shape, which Research Case selection does not accept, so
    `invalid_receipt_date` remains a valid observed outcome until Repair
    B. This asserts only what is observed here — it makes no claim about
    what the production DART pool contains, which may be historically
    mixed, nor that reordering cannot change DART's histogram.

    Only `rcept_dt` is compact. `retrieved_at` and the detection
    timestamp stay independently valid ISO, exactly as a real scan builds
    them — so the rejection is attributable to the receipt date alone and
    to nothing incidental about the fixture. The compact dates are
    themselves real calendar days, so the gate under test is the format
    contract, not an out-of-range value."""
    worker_settings = _worker_settings(tmp_path)
    compact = [
        _lane_candidate_dated(
            "dart", f"acc-compact-{i}",
            f"202608{i + 10:02d}",                 # DART-native compact, a real day
            iso_timestamp_date=f"2026-08-{i + 10:02d}",  # ISO, as a scan would set it
        )
        for i in range(5)
    ]
    _seed(worker_settings, "dart", *compact)

    summary, _candidates, cases = _run_lane_step("dart", worker_settings)

    assert "created=0" in summary
    assert cases == ()
    assert "rejection_reasons=" in summary
    assert "invalid_receipt_date" in summary
    # Attribution: the receipt-date format is the only thing wrong with
    # these records, so no other gate may be claiming them.
    for incidental in ("excerpt_not_extracted", "blank_original_excerpt", "no_rule_categories",
                       "missing_candidate_detected_timestamp", "blank_source_url", "other"):
        assert incidental not in summary


def test_dart_ordering_policy_is_shared_not_special_cased(tmp_path, _fixed_as_of):
    """DART takes the same newest-first window as the other lanes — no
    lane-specific branch. Proven with dashed-ISO fixtures so the date
    gate is out of the way and ordering alone is observable."""
    worker_settings = _worker_settings(tmp_path)
    stale = [_lane_candidate_dated("dart", f"acc-old-{i}", f"2026-01-{i + 1:02d}") for i in range(5)]
    fresh = _lane_candidate_dated("dart", "acc-fresh", "2026-08-19")
    _seed(worker_settings, "dart", *stale, fresh)

    summary, _candidates, cases = _run_lane_step("dart", worker_settings)

    assert "created=1" in summary
    assert len(cases) == 1
    assert cases[0].trigger_source_id == fresh.id
