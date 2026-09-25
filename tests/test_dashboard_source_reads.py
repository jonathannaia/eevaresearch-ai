"""Phase 2B — Dashboard per-source repository read counts, and the pure
visibility derivation they now share.

Offline only: backend_factory's repository constructors are replaced
with counting fakes, so no database, connection, credential, or network
is involved. The counts below are the measurement that justifies (or
refutes) the hoist — they are asserted, not assumed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

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


# === Phase 2D: one shared Daily News/editorial snapshot per render =======
#
# Offline only, same discipline as above: the Daily News repository
# factory, the reconciliation pass and the editorial visibility helper
# are replaced with counting fakes. The counts are the measurement that
# justifies the hoist — Recently Updated and Theme Activity each used to
# load the Daily News store for themselves, twice per render.

from types import SimpleNamespace  # noqa: E402

from src.data_access.daily_news import daily_news_backend, daily_news_pipeline  # noqa: E402
from src.models.daily_news_models import (  # noqa: E402
    EditorialStory,
    NewsMaterialityTier,
    NewsSourceReference,
    NewsStateTransition,
    NewsStory,
    NewsStoryStatus,
    SourceClass,
)
from src.ui.components import editorial_coverage, recent_theme_activity, recently_updated  # noqa: E402
from src.ui.pages import dashboard  # noqa: E402

# Anchored to the current time, not a fixed calendar date:
# load_theme_activity_rows() applies its real 14-day window against
# datetime.now(), so a hardcoded published_at would silently fall out of
# the window and turn these assertions vacuous on a later run.
_NOW = datetime.now(timezone.utc)
_PUBLISHED_AT = (_NOW - timedelta(hours=1)).isoformat()

# The localized-duplicate pair the two representations disagree about:
# both stories are real and both are persisted, so the RAW set holds two.
# Read-time reconciliation collapses them to the preferred (English) one,
# so the CANONICAL set holds one.
_GLOBAL_ID = "newsitem-global"
_LOCALIZED_ID = "newsitem-localized"


def _news_story(story_id: str, company_name: str, headline: str, language: str) -> NewsStory:
    return NewsStory(
        id=story_id, company_name=company_name, ticker="TCK", theme_slug="ai-buildout",
        headline=headline, eeva_summary="Summary text.", is_fallback_summary=False,
        translation_unavailable=False, original_title=None,
        sources=(
            NewsSourceReference(
                publisher=company_name, source_class=SourceClass.OFFICIAL_COMPANY,
                url=f"https://example.test/{story_id}", title=headline,
                published_at=_PUBLISHED_AT, retrieved_at=_PUBLISHED_AT,
                original_language=language, excerpt_original="Summary text.",
            ),
        ),
        status=NewsStoryStatus.PUBLISHED,
        state_history=[NewsStateTransition(status=NewsStoryStatus.PUBLISHED, at=_PUBLISHED_AT)],
        # Explicit tier: a legacy None tiers at read time into Background,
        # which Recently Updated's default preview excludes — the
        # cross-section assertions below would then pass vacuously.
        materiality_tier=NewsMaterialityTier.HIGH_SIGNAL,
    )


class _SnapshotCounter:
    def __init__(self) -> None:
        self.repository_constructions = 0
        self.story_loads = 0
        self.reconciliations = 0
        self.editorial_visibility_calls = 0


@pytest.fixture
def counting_daily_news(monkeypatch, tmp_path):
    """Counts every Daily News read a render could perform, and supplies
    a raw set the reconciled set demonstrably differs from."""
    counter = _SnapshotCounter()
    raw = {
        _GLOBAL_ID: _news_story(_GLOBAL_ID, "Global Co", "Global headline", "English"),
        _LOCALIZED_ID: _news_story(_LOCALIZED_ID, "Global Co", "Manchette localisée", "French"),
    }

    class _Repo:
        def load_stories(self):
            counter.story_loads += 1
            return dict(raw)

    def _get_repo(settings):
        counter.repository_constructions += 1
        return _Repo()

    def _select_canonical(stories, cache_dir):
        """Stands in for the real reconciliation pass: collapses the
        localized side of the pair away, leaving the preferred one."""
        counter.reconciliations += 1
        return {sid: s for sid, s in stories.items() if sid != _LOCALIZED_ID}

    def _visible_editorial(settings):
        counter.editorial_visibility_calls += 1
        return (EditorialStory(
            id="editorial-1", headline="Editorial headline", publisher="Fictional Wire",
            source_url="https://example.test/editorial-1", published_at=_PUBLISHED_AT,
            retrieved_at=_PUBLISHED_AT, excerpt=None, matched_companies=("Global Co",),
            matched_themes=("ai-buildout",), source_feed_id="feed-1",
            materiality_tier=NewsMaterialityTier.HIGH_SIGNAL,
        ),)

    for module in (daily_news_backend, recently_updated.daily_news_backend,
                   recent_theme_activity.daily_news_backend):
        monkeypatch.setattr(module, "get_daily_news_repository", _get_repo)
    for module in (daily_news_pipeline, recently_updated.daily_news_pipeline):
        monkeypatch.setattr(module, "select_canonical_stories", _select_canonical)
    monkeypatch.setattr(editorial_coverage, "get_visible_editorial_stories", _visible_editorial)
    monkeypatch.setattr(recently_updated, "get_visible_editorial_stories", _visible_editorial)
    return counter


def _snapshot_settings(tmp_path) -> Settings:
    return Settings(db_backend="json", cache_dir=tmp_path)


# --- the snapshot itself: each store read exactly once -------------------

def test_the_snapshot_reads_each_store_exactly_once(counting_daily_news, tmp_path):
    dashboard._load_daily_news_snapshot(_snapshot_settings(tmp_path))

    assert counting_daily_news.repository_constructions == 1
    assert counting_daily_news.story_loads == 1
    assert counting_daily_news.reconciliations == 1
    assert counting_daily_news.editorial_visibility_calls == 1


def test_the_snapshot_carries_both_representations_separately(counting_daily_news, tmp_path):
    snapshot = dashboard._load_daily_news_snapshot(_snapshot_settings(tmp_path))

    assert set(snapshot.raw_stories) == {_GLOBAL_ID, _LOCALIZED_ID}
    assert set(snapshot.canonical_stories) == {_GLOBAL_ID}
    assert [s.id for s in snapshot.editorial_stories] == ["editorial-1"]


def test_a_failing_daily_news_store_degrades_without_affecting_editorial(monkeypatch, tmp_path):
    def _failing_repo(settings):
        raise RuntimeError("fictional backend failure")

    monkeypatch.setattr(daily_news_backend, "get_daily_news_repository", _failing_repo)
    monkeypatch.setattr(
        editorial_coverage, "get_visible_editorial_stories",
        lambda settings: (EditorialStory(
            id="editorial-1", headline="Editorial headline", publisher="Fictional Wire",
            source_url="https://example.test/editorial-1", published_at=_PUBLISHED_AT,
            retrieved_at=_PUBLISHED_AT, excerpt=None, matched_companies=("Global Co",),
            matched_themes=("ai-buildout",), source_feed_id="feed-1",
        ),),
    )

    snapshot = dashboard._load_daily_news_snapshot(_snapshot_settings(tmp_path))

    assert snapshot.raw_stories == {}
    assert snapshot.canonical_stories == {}
    assert [s.id for s in snapshot.editorial_stories] == ["editorial-1"]


def test_a_failing_editorial_store_degrades_without_affecting_daily_news(
    monkeypatch, counting_daily_news, tmp_path,
):
    def _failing(settings):
        raise RuntimeError("fictional editorial failure")

    monkeypatch.setattr(editorial_coverage, "get_visible_editorial_stories", _failing)

    snapshot = dashboard._load_daily_news_snapshot(_snapshot_settings(tmp_path))

    assert snapshot.editorial_stories == ()
    assert set(snapshot.raw_stories) == {_GLOBAL_ID, _LOCALIZED_ID}
    assert set(snapshot.canonical_stories) == {_GLOBAL_ID}


def test_the_snapshot_is_per_render_not_a_shared_cache(counting_daily_news, tmp_path):
    """Nothing survives the render that built it: a second render pays
    for its own read, exactly as a render should."""
    settings = _snapshot_settings(tmp_path)

    dashboard._load_daily_news_snapshot(settings)
    dashboard._load_daily_news_snapshot(settings)

    assert counting_daily_news.story_loads == 2


# --- the two sections consume the snapshot, and read nothing themselves --

def test_both_sections_sharing_one_snapshot_perform_one_store_read(counting_daily_news, tmp_path):
    settings = _snapshot_settings(tmp_path)
    snapshot = dashboard._load_daily_news_snapshot(settings)

    class _Ctx:
        class theme_repository:
            @staticmethod
            def get_all_themes():
                return [SimpleNamespace(slug="ai-buildout", name="AI Buildout")]

    recent_theme_activity.load_theme_activity_rows(
        _Ctx(), settings, preloaded_daily_news_stories=snapshot.raw_stories,
    )
    recently_updated._select_recently_updated_rows(
        settings, now=_NOW,
        preloaded_daily_news_stories=snapshot.canonical_stories,
        preloaded_editorial_stories=snapshot.editorial_stories,
    )

    # One construction, one load, one reconcile, one visibility call in
    # total — all four performed by the snapshot, none by either section.
    assert counting_daily_news.repository_constructions == 1
    assert counting_daily_news.story_loads == 1
    assert counting_daily_news.reconciliations == 1
    assert counting_daily_news.editorial_visibility_calls == 1


def test_without_the_snapshot_the_two_sections_read_the_store_twice(counting_daily_news, tmp_path):
    """The behaviour being removed — asserted, so the improvement above
    is a measured difference rather than an assumed one."""
    settings = _snapshot_settings(tmp_path)

    class _Ctx:
        class theme_repository:
            @staticmethod
            def get_all_themes():
                return [SimpleNamespace(slug="ai-buildout", name="AI Buildout")]

    recent_theme_activity.load_theme_activity_rows(_Ctx(), settings)
    recently_updated._select_recently_updated_rows(settings, now=_NOW)

    assert counting_daily_news.repository_constructions == 2
    assert counting_daily_news.story_loads == 2


# --- the representations do not leak across the two sections -------------

def test_theme_activity_counts_the_raw_set_and_recently_updated_shows_the_canonical_one(
    counting_daily_news, tmp_path,
):
    settings = _snapshot_settings(tmp_path)
    snapshot = dashboard._load_daily_news_snapshot(settings)

    class _Ctx:
        class theme_repository:
            @staticmethod
            def get_all_themes():
                return [SimpleNamespace(slug="ai-buildout", name="AI Buildout")]

    rows = recent_theme_activity.load_theme_activity_rows(
        _Ctx(), settings, preloaded_daily_news_stories=snapshot.raw_stories,
    )
    updated = recently_updated._select_recently_updated_rows(
        settings, now=_NOW,
        preloaded_daily_news_stories=snapshot.canonical_stories,
        preloaded_editorial_stories=snapshot.editorial_stories,
    )

    # Theme Activity counts BOTH sides of the localized pair — its
    # 14-day per-theme totals have always been over every persisted
    # story, and collapsing them here would quietly change the number.
    assert [r.count for r in rows] == [2]

    # Recently Updated shows only the preferred side — the localized
    # headline is exactly what reconciliation exists to hide from it.
    titles = [r.title for r in updated]
    assert "Global headline" in titles
    assert "Manchette localisée" not in titles


def test_feeding_recently_updated_the_raw_set_would_show_the_localized_duplicate(
    counting_daily_news, tmp_path,
):
    """Non-vacuity guard for the assertion above: the localized headline
    is absent because the CANONICAL set was passed, not because the row
    could never appear at all."""
    settings = _snapshot_settings(tmp_path)
    snapshot = dashboard._load_daily_news_snapshot(settings)

    rows = recently_updated._select_recently_updated_rows(
        settings, now=_NOW,
        preloaded_daily_news_stories=snapshot.raw_stories,
        preloaded_editorial_stories=(),
    )

    assert "Manchette localisée" in [r.title for r in rows]


def test_feeding_theme_activity_the_canonical_set_would_undercount(
    counting_daily_news, tmp_path,
):
    """Non-vacuity guard for the count above: 2 is the raw total, and
    the canonical set really does produce a different, lower number."""
    settings = _snapshot_settings(tmp_path)
    snapshot = dashboard._load_daily_news_snapshot(settings)

    class _Ctx:
        class theme_repository:
            @staticmethod
            def get_all_themes():
                return [SimpleNamespace(slug="ai-buildout", name="AI Buildout")]

    rows = recent_theme_activity.load_theme_activity_rows(
        _Ctx(), settings, preloaded_daily_news_stories=snapshot.canonical_stories,
    )

    assert [r.count for r in rows] == [1]
