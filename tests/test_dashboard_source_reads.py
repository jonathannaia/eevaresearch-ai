"""Phase 2B — Dashboard per-source repository read counts, and the pure
visibility derivation they now share.

Offline only: backend_factory's repository constructors are replaced
with counting fakes, so no database, connection, credential, or network
is involved. The counts below are the measurement that justifies (or
refutes) the hoist — they are asserted, not assumed.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.config.settings import Settings
from src.data_access import backend_factory
from src.logic.filing_visibility import (
    not_material_rcept_nos,
    not_material_rcept_nos_from_candidates,
)
from src.models.models import CandidateSignal, CandidateStatus, FilingEvent


def _filing(rcept_no: str, source: str, theme: str = "ai-buildout") -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code="C1", corp_name="Synthetic Co", flr_nm="Synthetic Co",
        stock_code="0001", report_nm="Synthetic filing", rcept_dt="2026-09-20",
        source_name=source, original_language="English", theme_slug=theme,
    )


def _candidate(rcept_no: str, source: str, status: CandidateStatus) -> CandidateSignal:
    return CandidateSignal(
        id=f"cand-{rcept_no}", filing=_filing(rcept_no, source), matched_rules=["x"],
        confidence="Moderate", status=status,
    )


class _Counter:
    def __init__(self) -> None:
        self.filing_loads: list[str] = []
        self.candidate_loads: list[str] = []


@pytest.fixture
def counting_backend(monkeypatch):
    """Counts every repository construction by source, and returns a
    small, fixed synthetic dataset for each."""
    counter = _Counter()

    class _FilingRepo:
        def __init__(self, source: str) -> None:
            self.source = source

        def load_filing_events(self):
            counter.filing_loads.append(self.source)
            return [_filing(f"{self.source}-1", self.source), _filing(f"{self.source}-2", self.source)]

    class _CandidateRepo:
        def __init__(self, source: str) -> None:
            self.source = source

        def load_candidates(self):
            counter.candidate_loads.append(self.source)
            return {
                f"c-{self.source}-1": _candidate(f"{self.source}-1", self.source, CandidateStatus.NOT_MATERIAL),
                f"c-{self.source}-2": _candidate(f"{self.source}-2", self.source, CandidateStatus.NEEDS_REVIEW),
            }

    monkeypatch.setattr(backend_factory, "get_filing_event_repository", lambda s, src: _FilingRepo(src))
    monkeypatch.setattr(backend_factory, "get_candidate_repository", lambda s, src: _CandidateRepo(src))
    return counter


def _settings() -> Settings:
    return Settings(db_backend="postgres", state_db_url="postgresql://fictional/db")


# --- the hoisted read: one load per source, once -------------------------

def test_load_source_reads_loads_each_source_exactly_once(counting_backend):
    from src.ui.pages.dashboard import _load_source_reads

    reads = _load_source_reads(_settings())

    assert sorted(counting_backend.filing_loads) == ["EDINET", "OpenDART / DART", "SEC EDGAR"]
    assert sorted(counting_backend.candidate_loads) == ["EDINET", "OpenDART / DART", "SEC EDGAR"]
    assert len(counting_backend.filing_loads) == 3
    assert len(counting_backend.candidate_loads) == 3
    assert set(reads) == {"EDINET", "OpenDART / DART", "SEC EDGAR"}


def test_each_source_read_carries_filings_candidates_and_its_own_exclusion_set(counting_backend):
    from src.ui.pages.dashboard import _load_source_reads

    reads = _load_source_reads(_settings())

    for source, read in reads.items():
        assert [f.rcept_no for f in read.filings] == [f"{source}-1", f"{source}-2"]
        assert len(read.candidates) == 2  # Recently Updated renders from these
        assert read.not_material_ids == frozenset({f"{source}-1"})  # only the NOT_MATERIAL one


def test_a_failing_source_degrades_to_an_empty_pair_without_affecting_others(monkeypatch):
    from src.ui.pages.dashboard import _load_source_reads

    def _filing_repo(settings, source):
        if source == "EDINET":
            raise RuntimeError("fictional backend failure")

        class _R:
            def load_filing_events(self):
                return [_filing("ok-1", source)]
        return _R()

    class _CandidateRepo:
        def load_candidates(self):
            return {}

    monkeypatch.setattr(backend_factory, "get_filing_event_repository", _filing_repo)
    monkeypatch.setattr(backend_factory, "get_candidate_repository", lambda s, src: _CandidateRepo())

    reads = _load_source_reads(_settings())

    assert reads["EDINET"].filings == []
    assert reads["EDINET"].candidates == []
    assert reads["EDINET"].not_material_ids == frozenset()
    assert [f.rcept_no for f in reads["SEC EDGAR"].filings] == ["ok-1"]


# --- Regional Brief consumes preloaded data, loading nothing -------------

def test_regional_brief_with_preloaded_data_constructs_no_repository(counting_backend):
    from src.ui.components.regional_brief import _load_recent_filings

    preloaded = ([_filing("A", "SEC EDGAR"), _filing("B", "SEC EDGAR")], frozenset({"A"}))
    filings = _load_recent_filings("SEC EDGAR", _settings(), preloaded)

    assert counting_backend.filing_loads == []
    assert counting_backend.candidate_loads == []
    assert [f.rcept_no for f in filings] == ["B"]  # "A" suppressed as NOT_MATERIAL


def test_regional_brief_without_preloaded_data_behaves_exactly_as_before(counting_backend):
    from src.ui.components.regional_brief import _load_recent_filings

    filings = _load_recent_filings("SEC EDGAR", _settings())

    assert counting_backend.filing_loads == ["SEC EDGAR"]
    assert counting_backend.candidate_loads == ["SEC EDGAR"]
    assert [f.rcept_no for f in filings] == ["SEC EDGAR-2"]


def test_preloaded_and_unpreloaded_paths_select_the_same_filings(counting_backend):
    from src.ui.components.regional_brief import _load_recent_filings

    loaded = _load_recent_filings("SEC EDGAR", _settings())
    preloaded_input = (
        [_filing("SEC EDGAR-1", "SEC EDGAR"), _filing("SEC EDGAR-2", "SEC EDGAR")],
        frozenset({"SEC EDGAR-1"}),
    )
    preloaded = _load_recent_filings("SEC EDGAR", _settings(), preloaded_input)

    assert [f.rcept_no for f in loaded] == [f.rcept_no for f in preloaded]


# --- the visibility derivation is reused, not recomputed -----------------

def test_the_pure_derivation_matches_the_loading_one(counting_backend):
    settings = _settings()
    loading = not_material_rcept_nos(settings, "SEC EDGAR")

    candidates = backend_factory.get_candidate_repository(settings, "SEC EDGAR").load_candidates()
    pure = not_material_rcept_nos_from_candidates(candidates.values())

    assert pure == loading == frozenset({"SEC EDGAR-1"})


def test_the_pure_derivation_performs_no_repository_load(counting_backend):
    candidates = [
        _candidate("x-1", "SEC EDGAR", CandidateStatus.NOT_MATERIAL),
        _candidate("x-2", "SEC EDGAR", CandidateStatus.NEEDS_REVIEW),
    ]
    counting_backend.candidate_loads.clear()

    result = not_material_rcept_nos_from_candidates(candidates)

    assert result == frozenset({"x-1"})
    assert counting_backend.candidate_loads == []


def test_repeated_derivation_over_one_read_costs_one_load(counting_backend):
    settings = _settings()
    candidates = list(
        backend_factory.get_candidate_repository(settings, "SEC EDGAR").load_candidates().values()
    )
    assert len(counting_backend.candidate_loads) == 1

    for _ in range(5):
        assert not_material_rcept_nos_from_candidates(candidates) == frozenset({"SEC EDGAR-1"})

    assert len(counting_backend.candidate_loads) == 1  # still one


def test_an_unreachable_store_still_yields_an_empty_exclusion_set(monkeypatch):
    def _raise(settings, source):
        raise RuntimeError("fictional failure")

    monkeypatch.setattr(backend_factory, "get_candidate_repository", _raise)

    assert not_material_rcept_nos(_settings(), "SEC EDGAR") == frozenset()


# --- theme activity is asked for only what it displays -------------------

def test_dashboard_requests_only_the_displayed_number_of_theme_rows():
    from src.ui.components.recent_theme_activity import MAX_ROWS
    from src.ui.pages import dashboard

    assert dashboard.THEME_ACTIVITY_MAX_ROWS == MAX_ROWS == 4


# --- the whole-render target: exactly 3 filing loads and 3 candidate loads ---

def test_one_dashboard_render_performs_exactly_three_filing_and_three_candidate_loads(counting_backend):
    """The Phase 2B acceptance target, measured rather than assumed.

    Drives every source-wide consumer the way render() does: one shared
    read, then each section fed from it. Before this change the same
    sequence performed 6 filing loads and 9 candidate loads."""
    from src.ui.components.recently_updated import _load_filing_rows
    from src.ui.components.regional_brief import _load_recent_filings
    from src.ui.components.recent_theme_activity import _load_filing_items
    from src.ui.pages.dashboard import _candidate_inputs, _load_source_reads, _regional_inputs

    settings = _settings()
    reads = _load_source_reads(settings)                 # the ONE shared read
    regional = _regional_inputs(reads)
    candidates_by_source = _candidate_inputs(reads)

    for source in regional:                              # Regional Brief
        _load_recent_filings(source, settings, regional[source])
    _load_filing_items(settings, regional)               # Recent Theme Activity
    _load_filing_rows(settings, datetime.now(timezone.utc), candidates_by_source)  # Recently Updated

    assert len(counting_backend.filing_loads) == 3
    assert len(counting_backend.candidate_loads) == 3
    assert sorted(counting_backend.filing_loads) == ["EDINET", "OpenDART / DART", "SEC EDGAR"]
    assert sorted(counting_backend.candidate_loads) == ["EDINET", "OpenDART / DART", "SEC EDGAR"]


def test_each_preloaded_consumer_adds_zero_further_loads(counting_backend):
    from src.ui.components.recently_updated import _load_filing_rows
    from src.ui.components.regional_brief import _load_recent_filings
    from src.ui.components.recent_theme_activity import _load_filing_items
    from src.ui.pages.dashboard import _candidate_inputs, _load_source_reads, _regional_inputs

    settings = _settings()
    reads = _load_source_reads(settings)
    counting_backend.filing_loads.clear()
    counting_backend.candidate_loads.clear()

    for source, preloaded in _regional_inputs(reads).items():
        _load_recent_filings(source, settings, preloaded)
    _load_filing_items(settings, _regional_inputs(reads))
    _load_filing_rows(settings, datetime.now(timezone.utc), _candidate_inputs(reads))

    assert counting_backend.filing_loads == []
    assert counting_backend.candidate_loads == []


# --- Recent Theme Activity: preloaded vs legacy ----------------------------

def test_theme_activity_preloaded_path_constructs_no_repository(counting_backend):
    from src.ui.components.recent_theme_activity import _load_filing_items
    from src.ui.pages.dashboard import _load_source_reads, _regional_inputs

    regional = _regional_inputs(_load_source_reads(_settings()))
    counting_backend.filing_loads.clear()
    counting_backend.candidate_loads.clear()

    _load_filing_items(_settings(), regional)

    assert counting_backend.filing_loads == []
    assert counting_backend.candidate_loads == []


def test_theme_activity_legacy_path_still_loads_per_source(counting_backend):
    from src.ui.components.recent_theme_activity import _load_filing_items

    _load_filing_items(_settings())

    assert len(counting_backend.filing_loads) == 3
    assert len(counting_backend.candidate_loads) == 3


def test_theme_activity_preloaded_and_legacy_produce_identical_items(counting_backend):
    from src.ui.components.recent_theme_activity import _load_filing_items
    from src.ui.pages.dashboard import _load_source_reads, _regional_inputs

    legacy = _load_filing_items(_settings())
    regional = _regional_inputs(_load_source_reads(_settings()))
    preloaded = _load_filing_items(_settings(), regional)

    assert [(i.theme_slug, i.company_name, i.timestamp) for i in preloaded] == [
        (i.theme_slug, i.company_name, i.timestamp) for i in legacy
    ]


# --- Recently Updated: preloaded vs legacy ---------------------------------

def test_recently_updated_preloaded_path_constructs_no_repository(counting_backend):
    from src.ui.components.recently_updated import _load_filing_rows
    from src.ui.pages.dashboard import _candidate_inputs, _load_source_reads

    candidates_by_source = _candidate_inputs(_load_source_reads(_settings()))
    counting_backend.candidate_loads.clear()

    _load_filing_rows(_settings(), datetime.now(timezone.utc), candidates_by_source)

    assert counting_backend.candidate_loads == []


def test_recently_updated_legacy_path_still_loads_per_source(counting_backend):
    from src.ui.components.recently_updated import _load_filing_rows

    _load_filing_rows(_settings(), datetime.now(timezone.utc))

    assert len(counting_backend.candidate_loads) == 3


def test_recently_updated_preloaded_and_legacy_produce_the_same_rows_in_order(counting_backend):
    from src.ui.components.recently_updated import _load_filing_rows
    from src.ui.pages.dashboard import _candidate_inputs, _load_source_reads

    now = datetime.now(timezone.utc)
    legacy = _load_filing_rows(_settings(), now)
    candidates_by_source = _candidate_inputs(_load_source_reads(_settings()))
    preloaded = _load_filing_rows(_settings(), now, candidates_by_source)

    assert [(r.company_name, r.title, r.sort_key) for r in preloaded] == [
        (r.company_name, r.title, r.sort_key) for r in legacy
    ]
