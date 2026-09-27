"""Phase 2D — Recently Updated's Dashboard-facing preload seam.

Offline only: the Daily News repository factory, the reconciliation
pass, and the editorial visibility helper are all replaced with
counting fakes, so no database, connection, credential, or network is
involved. The counts below are the measurement that justifies the
hoist — they are asserted, not assumed.

Recently Updated consumes CANONICAL (already-reconciled) stories. The
raw representation Theme Activity consumes is a different thing and
must never be substituted here; test_dashboard_source_reads.py holds
the cross-section proof.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from src.config.settings import Settings
from src.data_access.daily_news import daily_news_backend, daily_news_pipeline
from src.models.daily_news_models import (
    EditorialStory,
    NewsMaterialityTier,
    NewsSourceReference,
    NewsStateTransition,
    NewsStory,
    NewsStoryStatus,
    SourceClass,
)
from src.ui.components import editorial_coverage, recently_updated

PUBLISHED_AT = "2026-09-20T12:00:00+00:00"
NOW = datetime(2026, 9, 20, 18, 0, tzinfo=timezone.utc)


def _settings(tmp_path) -> Settings:
    return Settings(db_backend="json", cache_dir=tmp_path)


def _story(story_id: str, company_name: str, headline: str, url: str) -> NewsStory:
    return NewsStory(
        id=story_id, company_name=company_name, ticker="TCK", theme_slug="ai-buildout",
        headline=headline, eeva_summary="Summary text.", is_fallback_summary=False,
        translation_unavailable=False, original_title=None,
        sources=(
            NewsSourceReference(
                publisher=company_name, source_class=SourceClass.OFFICIAL_COMPANY, url=url,
                title=headline, published_at=PUBLISHED_AT, retrieved_at=PUBLISHED_AT,
                original_language="English", excerpt_original="Summary text.",
            ),
        ),
        status=NewsStoryStatus.PUBLISHED,
        state_history=[NewsStateTransition(status=NewsStoryStatus.PUBLISHED, at=PUBLISHED_AT)],
        # Explicit tier: a legacy None is tiered at read time and would
        # land in Background, which this default preview excludes — the
        # fixture would then pass vacuously on an empty row list.
        materiality_tier=NewsMaterialityTier.HIGH_SIGNAL,
    )


def _editorial(story_id: str, headline: str, company: str) -> EditorialStory:
    return EditorialStory(
        id=story_id, headline=headline, publisher="Fictional Wire",
        source_url=f"https://example.test/{story_id}", published_at=PUBLISHED_AT,
        retrieved_at=PUBLISHED_AT, excerpt=None, matched_companies=(company,),
        matched_themes=("ai-buildout",), source_feed_id="feed-1",
        materiality_tier=NewsMaterialityTier.HIGH_SIGNAL,
    )


class _Counter:
    def __init__(self) -> None:
        self.repository_constructions = 0
        self.story_loads = 0
        self.reconciliations = 0
        self.editorial_visibility_calls = 0


@pytest.fixture
def counting_daily_news(monkeypatch):
    """Counts every construction/load/reconcile Recently Updated could
    perform. A preloaded render must leave all four counters at zero."""
    counter = _Counter()
    stored = {"s-loaded": _story("s-loaded", "Loaded Co", "Loaded from the repository", "https://example.test/loaded")}

    class _Repo:
        def load_stories(self):
            counter.story_loads += 1
            return dict(stored)

    def _get_repo(settings):
        counter.repository_constructions += 1
        return _Repo()

    def _select_canonical(stories, cache_dir):
        counter.reconciliations += 1
        return dict(stories)

    def _visible_editorial(settings):
        counter.editorial_visibility_calls += 1
        return (_editorial("e-loaded", "Editorial from the helper", "Loaded Co"),)

    monkeypatch.setattr(daily_news_backend, "get_daily_news_repository", _get_repo)
    monkeypatch.setattr(daily_news_pipeline, "select_canonical_stories", _select_canonical)
    monkeypatch.setattr(editorial_coverage, "get_visible_editorial_stories", _visible_editorial)
    monkeypatch.setattr(recently_updated, "get_visible_editorial_stories", _visible_editorial)
    return counter


def _titles(rows) -> list[str]:
    return [r.title for r in rows]


# --- preloaded: nothing is constructed, nothing is loaded ----------------

def test_preloaded_daily_news_stories_construct_no_repository(tmp_path, counting_daily_news):
    preloaded = {"s-pre": _story("s-pre", "Preloaded Co", "Preloaded headline", "https://example.test/pre")}

    rows = recently_updated._load_daily_news_rows(_settings(tmp_path), NOW, preloaded)

    assert counting_daily_news.repository_constructions == 0
    assert counting_daily_news.story_loads == 0
    assert _titles(rows) == ["Preloaded headline"]


def test_preloaded_stories_are_not_reconciled_a_second_time(tmp_path, counting_daily_news):
    """The caller already ran select_canonical_stories(); running it
    again here would be a redundant pass over an already-canonical set."""
    preloaded = {"s-pre": _story("s-pre", "Preloaded Co", "Preloaded headline", "https://example.test/pre")}

    recently_updated._load_daily_news_rows(_settings(tmp_path), NOW, preloaded)

    assert counting_daily_news.reconciliations == 0


def test_preloaded_editorial_stories_call_no_visibility_helper(tmp_path, counting_daily_news):
    preloaded = (_editorial("e-pre", "Preloaded editorial headline", "Preloaded Co"),)

    rows = recently_updated._load_editorial_rows(_settings(tmp_path), NOW, preloaded)

    assert counting_daily_news.editorial_visibility_calls == 0
    assert counting_daily_news.repository_constructions == 0
    assert _titles(rows) == ["Preloaded editorial headline"]


def test_both_preloaded_through_the_public_selection_reads_nothing(tmp_path, counting_daily_news):
    rows = recently_updated._select_recently_updated_rows(
        _settings(tmp_path), now=NOW,
        preloaded_daily_news_stories={"s-pre": _story("s-pre", "Preloaded Co", "Preloaded headline", "https://example.test/pre")},
        preloaded_editorial_stories=(_editorial("e-pre", "Preloaded editorial headline", "Preloaded Co"),),
    )

    assert counting_daily_news.repository_constructions == 0
    assert counting_daily_news.story_loads == 0
    assert counting_daily_news.reconciliations == 0
    assert counting_daily_news.editorial_visibility_calls == 0
    assert sorted(_titles(rows)) == ["Preloaded editorial headline", "Preloaded headline"]


# --- unpreloaded: the existing path is byte-for-byte unchanged -----------

def test_without_preloaded_stories_it_loads_and_reconciles_exactly_as_before(tmp_path, counting_daily_news):
    rows = recently_updated._load_daily_news_rows(_settings(tmp_path), NOW)

    assert counting_daily_news.repository_constructions == 1
    assert counting_daily_news.story_loads == 1
    assert counting_daily_news.reconciliations == 1
    assert _titles(rows) == ["Loaded from the repository"]


def test_without_preloaded_editorial_it_calls_the_visibility_helper_as_before(tmp_path, counting_daily_news):
    rows = recently_updated._load_editorial_rows(_settings(tmp_path), NOW)

    assert counting_daily_news.editorial_visibility_calls == 1
    assert _titles(rows) == ["Editorial from the helper"]


def test_an_empty_preloaded_mapping_is_honored_not_treated_as_absent(tmp_path, counting_daily_news):
    """{} is a real answer ("no stories"), distinct from None ("load
    them yourself") — conflating them would silently restore the load."""
    rows = recently_updated._load_daily_news_rows(_settings(tmp_path), NOW, {})

    assert rows == []
    assert counting_daily_news.repository_constructions == 0
    assert counting_daily_news.story_loads == 0


def test_an_empty_preloaded_editorial_tuple_is_honored_not_treated_as_absent(tmp_path, counting_daily_news):
    rows = recently_updated._load_editorial_rows(_settings(tmp_path), NOW, ())

    assert rows == []
    assert counting_daily_news.editorial_visibility_calls == 0


# --- preloaded and unpreloaded select the same rows ----------------------

def test_preloaded_and_unpreloaded_paths_select_identical_daily_news_rows(tmp_path, counting_daily_news):
    settings = _settings(tmp_path)
    loaded = recently_updated._load_daily_news_rows(settings, NOW)

    same_input = {"s-loaded": _story("s-loaded", "Loaded Co", "Loaded from the repository", "https://example.test/loaded")}
    preloaded = recently_updated._load_daily_news_rows(settings, NOW, same_input)

    assert [(r.title, r.company_name, r.source_url) for r in loaded] \
        == [(r.title, r.company_name, r.source_url) for r in preloaded]


def test_preloaded_and_unpreloaded_paths_select_identical_editorial_rows(tmp_path, counting_daily_news):
    settings = _settings(tmp_path)
    loaded = recently_updated._load_editorial_rows(settings, NOW)
    preloaded = recently_updated._load_editorial_rows(
        settings, NOW, (_editorial("e-loaded", "Editorial from the helper", "Loaded Co"),)
    )

    assert [(r.title, r.company_name, r.source_url) for r in loaded] \
        == [(r.title, r.company_name, r.source_url) for r in preloaded]


# --- existing gates still apply to preloaded input -----------------------

def test_a_background_tier_preloaded_editorial_story_is_still_excluded(tmp_path, counting_daily_news):
    background = EditorialStory(
        id="e-bg", headline="Background editorial headline", publisher="Fictional Wire",
        source_url="https://example.test/bg", published_at=PUBLISHED_AT, retrieved_at=PUBLISHED_AT,
        excerpt=None, matched_companies=("Preloaded Co",), matched_themes=("ai-buildout",),
        source_feed_id="feed-1", materiality_tier=NewsMaterialityTier.BACKGROUND,
    )

    rows = recently_updated._load_editorial_rows(_settings(tmp_path), NOW, (background,))

    assert rows == []


def test_a_non_published_preloaded_story_is_still_excluded(tmp_path, counting_daily_news):
    from dataclasses import replace

    draft = replace(
        _story("s-draft", "Draft Co", "Draft headline", "https://example.test/draft"),
        status=NewsStoryStatus.DISCOVERED,
    )

    rows = recently_updated._load_daily_news_rows(_settings(tmp_path), NOW, {"s-draft": draft})

    assert rows == []


# === Phase 2E: display-only work deferred past the five-row slice ========
#
# Only PREVIEW_COUNT rows are ever shown, but filtering, identity,
# ordering and same-headline merging must run over every eligible
# candidate and story to decide which those are. The eager path formatted
# a display date, rewrote a source URL and composed an EDINET instruction
# for all of them and then discarded all but five.
#
# Two separate claims are proven below, and they are not the same claim:
#
#   1. a render now performs display-only work at most once per RENDERED
#      row, instead of once per eligible row; and
#   2. the five rows it renders are IDENTICAL, field by field, to the
#      first five the eager path returns.
#
# Claim 1 carries a non-vacuity baseline: the eager path is still present
# as _select_recently_updated_rows() and is asserted to make hundreds of
# the same calls on the same fixture, so the bounds cannot pass trivially.

from src.logic import filing_display as _filing_display  # noqa: E402
from src.logic.market_map import REGION_SOURCE  # noqa: E402
from src.models.models import CandidateSignal, CandidateStatus, FilingEvent  # noqa: E402

_EDINET = "EDINET"
_LARGE_PER_SOURCE = 120


class _CallCounter:
    """Counts calls to one module attribute, restoring it afterwards."""

    def __init__(self, module, name: str) -> None:
        self._module, self._name = module, name
        self._original = getattr(module, name)
        self.count = 0

    def __enter__(self) -> "_CallCounter":
        def _counted(*args, **kwargs):
            self.count += 1
            return self._original(*args, **kwargs)

        setattr(self._module, self._name, _counted)
        return self

    def __exit__(self, *exc) -> None:
        setattr(self._module, self._name, self._original)


def _large_filing(index: int, source: str) -> FilingEvent:
    filed = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc) - timedelta(minutes=index)
    return FilingEvent(
        rcept_no=f"{source}-{index:05d}", corp_code=f"C{index:05d}",
        corp_name=f"Company {index % 40}", flr_nm=f"Company {index % 40}", stock_code="0001",
        report_nm=f"Synthetic filing title {index}", rcept_dt=filed.date().isoformat(),
        source_name=source, original_language="Japanese" if source == _EDINET else "English",
        theme_slug="ai-buildout", filed_at=filed.isoformat(), retrieved_at=filed.isoformat(),
        source_url=f"https://example.test/{source}/{index}",
    )


def _large_candidates(per_source: int = _LARGE_PER_SOURCE) -> dict:
    return {
        source: [
            CandidateSignal(
                id=f"cand-{source}-{i}", filing=_large_filing(i, source),
                matched_rules=["x"], confidence="Moderate", status=CandidateStatus.NEEDS_REVIEW,
            )
            for i in range(per_source)
        ]
        for source in REGION_SOURCE.values()
    }


def _large_stories(count: int = _LARGE_PER_SOURCE) -> dict:
    stories = {}
    for i in range(count):
        at = (datetime(2026, 9, 20, 6, 0, tzinfo=timezone.utc) - timedelta(minutes=i)).isoformat()
        story = _story(f"newsitem-{i:05d}", f"Company {i % 30}", f"Story headline {i}",
                       f"https://example.test/story/{i}")
        stories[story.id] = replace(
            story,
            sources=(replace(story.sources[0], published_at=at, retrieved_at=at),),
        )
    return stories


def _large_editorial(count: int = 40) -> tuple:
    return tuple(
        replace(
            _editorial(f"ed-{i:04d}", f"Editorial headline {i}", f"Company {i % 20}"),
            published_at=(datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc) - timedelta(minutes=i)).isoformat(),
        )
        for i in range(count)
    )


def _large_inputs():
    return _large_candidates(), _large_stories(), _large_editorial()


def _render_bounded(settings):
    """Exactly what render_recently_updated() does to produce its rows."""
    candidates, stories, editorial = _large_inputs()
    return [
        recently_updated._materialize_row(source)
        for source in recently_updated._select_row_sources(
            settings, preloaded_by_source=candidates,
            preloaded_daily_news_stories=stories, preloaded_editorial_stories=editorial,
        )[:recently_updated.PREVIEW_COUNT]
    ]


def _eager_rows(settings):
    candidates, stories, editorial = _large_inputs()
    return recently_updated._select_recently_updated_rows(
        settings, preloaded_by_source=candidates,
        preloaded_daily_news_stories=stories, preloaded_editorial_stories=editorial,
    )


# --- 1. large-fixture equivalence ----------------------------------------

def _row_fields(row):
    return (
        row.company_name, row.title, row.display_date, row.source_url,
        row.source_label, row.edinet_instruction, row.original_language,
        row.translation_document_id, row.sort_key,
    )


def test_the_five_rendered_rows_are_identical_to_the_eager_paths_first_five(tmp_path):
    settings = _settings(tmp_path)

    eager = _eager_rows(settings)
    bounded = _render_bounded(settings)

    assert len(eager) > 300, "fixture must be large enough for the bound to mean something"
    assert len(bounded) == recently_updated.PREVIEW_COUNT
    assert [_row_fields(r) for r in bounded] == [_row_fields(r) for r in eager[:recently_updated.PREVIEW_COUNT]]
    assert bounded == eager[:recently_updated.PREVIEW_COUNT]


def test_translation_control_eligibility_is_identical_for_the_rendered_rows(tmp_path):
    settings = _settings(tmp_path)

    eager = _eager_rows(settings)[:recently_updated.PREVIEW_COUNT]
    bounded = _render_bounded(settings)

    assert [recently_updated._can_translate(r) for r in bounded] \
        == [recently_updated._can_translate(r) for r in eager]
    assert [recently_updated._row_identity_key(r) for r in bounded] \
        == [recently_updated._row_identity_key(r) for r in eager]


def test_the_full_eager_ordering_is_unchanged_by_the_refactor(tmp_path):
    """_select_recently_updated_rows is now _select_row_sources plus
    materialization; the two must agree row for row, not only in the
    first five."""
    settings = _settings(tmp_path)
    candidates, stories, editorial = _large_inputs()

    rows = recently_updated._select_recently_updated_rows(
        settings, preloaded_by_source=candidates,
        preloaded_daily_news_stories=stories, preloaded_editorial_stories=editorial,
    )
    sources = recently_updated._select_row_sources(
        settings, preloaded_by_source=candidates,
        preloaded_daily_news_stories=stories, preloaded_editorial_stories=editorial,
    )

    assert len(rows) == len(sources)
    assert [r.title for r in rows] == [s.title for s in sources]
    assert [r.company_name for r in rows] == [s.company_name for s in sources]
    assert [r.sort_key for r in rows] == [s.sort_key for s in sources]


# --- 2. deferred-work call counts ----------------------------------------

def test_a_render_formats_at_most_one_date_per_rendered_row(tmp_path):
    settings = _settings(tmp_path)

    with _CallCounter(recently_updated, "fmt_datetime_local") as counter:
        shown = _render_bounded(settings)

    assert counter.count <= len(shown) == recently_updated.PREVIEW_COUNT


def test_a_render_builds_at_most_one_edinet_instruction_per_rendered_edinet_row(tmp_path):
    settings = _settings(tmp_path)

    with _CallCounter(_filing_display, "edinet_source_instruction") as counter:
        shown = _render_bounded(settings)

    rendered_edinet_rows = sum(1 for r in shown if r.edinet_instruction)
    assert counter.count <= max(rendered_edinet_rows, 1)
    assert counter.count <= recently_updated.PREVIEW_COUNT


def test_a_render_rewrites_at_most_one_source_url_per_rendered_row(tmp_path):
    settings = _settings(tmp_path)

    with _CallCounter(recently_updated, "public_source_url") as counter:
        _render_bounded(settings)

    assert counter.count <= recently_updated.PREVIEW_COUNT


def test_the_eager_path_still_performs_this_work_for_every_row(tmp_path):
    """Non-vacuity baseline. Without it, the three bounds above would
    pass even if the fixture produced no rows at all — and they would
    also have passed before this change if the eager path had never been
    doing the work in the first place."""
    settings = _settings(tmp_path)

    with _CallCounter(recently_updated, "fmt_datetime_local") as dates, \
            _CallCounter(_filing_display, "edinet_source_instruction") as instructions, \
            _CallCounter(recently_updated, "public_source_url") as urls:
        rows = _eager_rows(settings)

    assert len(rows) > 300
    assert dates.count > 300
    assert instructions.count >= _LARGE_PER_SOURCE
    assert urls.count >= _LARGE_PER_SOURCE
    # and the bounded path is smaller by orders of magnitude
    assert dates.count > 50 * recently_updated.PREVIEW_COUNT


# --- 3. the EDINET date is formatted once per materialized row -----------

def test_edinet_filing_date_is_computed_once_per_materialized_row(tmp_path):
    """The eager builder called _filing_display_date twice for every
    EDINET filing: once for display_date and again to supply the
    instruction's filed-date clause."""
    settings = _settings(tmp_path)
    candidates = _large_candidates()
    edinet_count = len(candidates[_EDINET])

    with _CallCounter(recently_updated, "_filing_display_date") as counter:
        rows = recently_updated._load_filing_rows(
            settings, datetime(2026, 9, 21, tzinfo=timezone.utc), candidates,
        )

    assert len(rows) == sum(len(v) for v in candidates.values())
    assert counter.count == len(rows)  # exactly one per row, EDINET included
    assert counter.count < len(rows) + edinet_count  # the old path would have been this


def test_the_edinet_instruction_text_is_unchanged(tmp_path):
    settings = _settings(tmp_path)
    candidates = {_EDINET: _large_candidates()[_EDINET][:1]}

    row = recently_updated._load_filing_rows(
        settings, datetime(2026, 9, 21, tzinfo=timezone.utc), candidates,
    )[0]
    candidate = candidates[_EDINET][0]
    expected = _filing_display.edinet_source_instruction(
        candidate.filing, candidate, recently_updated._filing_display_date(candidate.filing),
    )

    assert row.edinet_instruction == expected
    assert row.edinet_instruction.startswith("Search EDINET")


# --- 4. same-headline merge fidelity -------------------------------------

def test_a_same_headline_group_below_the_top_five_still_merges_its_labels(tmp_path):
    """A joint announcement arrives as one story per issuer. The later
    issuers can sit far below the five-row window, and their names must
    still reach the merged row's label — so the merge cannot be applied
    to a pre-sliced list."""
    settings = _settings(tmp_path)
    joint = "AMD, Cisco and HUMAIN expand their joint buildout"
    stories = {}
    for rank, company in enumerate(("Alpha Co", "Bravo Co", "Charlie Co")):
        # minute 0, then minutes 60 and 61 — the 2nd and 3rd sit well
        # below the newest five once the filler stories below are added.
        offset = 0 if rank == 0 else 60 + rank
        at = (datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc) - timedelta(minutes=offset)).isoformat()
        story = _story(f"newsitem-joint-{rank}", company, joint, f"https://example.test/joint/{rank}")
        stories[story.id] = replace(
            story, sources=(replace(story.sources[0], published_at=at, retrieved_at=at),),
        )
    for i in range(30):  # filler strictly between the first and the other two
        at = (datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc) - timedelta(minutes=i + 1)).isoformat()
        story = _story(f"newsitem-filler-{i:03d}", f"Filler Co {i}", f"Filler headline {i}",
                       f"https://example.test/filler/{i}")
        stories[story.id] = replace(
            story, sources=(replace(story.sources[0], published_at=at, retrieved_at=at),),
        )

    shown = [
        recently_updated._materialize_row(s)
        for s in recently_updated._select_row_sources(
            settings, preloaded_daily_news_stories=stories, preloaded_editorial_stories=(),
        )[:recently_updated.PREVIEW_COUNT]
    ]
    eager = recently_updated._select_recently_updated_rows(
        settings, preloaded_daily_news_stories=stories, preloaded_editorial_stories=(),
    )

    merged_row = next(r for r in shown if r.title == joint)
    assert merged_row.company_name == "Alpha Co, Bravo Co, Charlie Co"
    assert merged_row.company_name == next(r for r in eager if r.title == joint).company_name

    # Non-vacuity: the two later issuers must genuinely sit outside the
    # five-row window BEFORE merging, or this proves nothing about
    # merging ahead of the slice.
    unmerged = recently_updated._filing_row_sources(settings, datetime.now(timezone.utc)) + \
        recently_updated._news_row_sources(settings, datetime.now(timezone.utc), stories)
    unmerged.sort(key=lambda r: (r.sort_key.timestamp(), r.identity_key), reverse=True)
    joint_positions = [i for i, r in enumerate(unmerged) if r.title == joint]
    assert joint_positions[0] < recently_updated.PREVIEW_COUNT
    assert all(p >= recently_updated.PREVIEW_COUNT for p in joint_positions[1:]), joint_positions
    assert sum(1 for r in shown if r.title == joint) == 1


# --- 5. tie-break fidelity -----------------------------------------------

def test_identical_timestamps_keep_the_identity_key_ordering(tmp_path):
    settings = _settings(tmp_path)
    at = "2026-09-20T12:00:00+00:00"
    stories = {}
    for suffix in ("ccc", "aaa", "bbb"):  # deliberately not insertion-ordered
        story = _story(f"newsitem-{suffix}", f"Company {suffix}", f"Headline {suffix}",
                       f"https://example.test/{suffix}")
        stories[story.id] = replace(
            story, sources=(replace(story.sources[0], published_at=at, retrieved_at=at),),
        )

    sources = recently_updated._select_row_sources(
        settings, preloaded_daily_news_stories=stories, preloaded_editorial_stories=(),
    )
    eager = recently_updated._select_recently_updated_rows(
        settings, preloaded_daily_news_stories=stories, preloaded_editorial_stories=(),
    )

    assert [s.identity_key for s in sources] == [
        "recently-updated-signals:newsitem-ccc",
        "recently-updated-signals:newsitem-bbb",
        "recently-updated-signals:newsitem-aaa",
    ]
    assert [s.title for s in sources] == [r.title for r in eager]


# --- 6. standalone behaviour is unchanged --------------------------------

def test_select_recently_updated_rows_still_returns_complete_rows(tmp_path):
    settings = _settings(tmp_path)

    rows = _eager_rows(settings)

    assert len(rows) > 300
    assert all(isinstance(r, recently_updated._Row) for r in rows)
    assert all(r.display_date for r in rows)
    assert all(r.source_url for r in rows)
    assert any(r.edinet_instruction for r in rows)


def test_row_source_identity_keys_match_row_identity_keys(tmp_path):
    """_select_row_sources sorts on _RowSource.identity_key while
    render_recently_updated keys its containers off _row_identity_key of
    the materialized row. The two must agree or the sort and the DOM
    keys would disagree."""
    settings = _settings(tmp_path)
    candidates, stories, editorial = _large_inputs()

    sources = recently_updated._select_row_sources(
        settings, preloaded_by_source=candidates,
        preloaded_daily_news_stories=stories, preloaded_editorial_stories=editorial,
    )

    assert len(sources) > 300
    for source in sources:
        assert source.identity_key == recently_updated._row_identity_key(
            recently_updated._materialize_row(source)
        )


def test_an_editorial_story_without_a_source_url_still_gets_a_stable_identity(tmp_path):
    """The last-resort fallback is the one place a display date is still
    formatted during selection."""
    settings = _settings(tmp_path)
    story = replace(_editorial("ed-no-url", "Headline with no URL", "Company X"), source_url="")

    sources = recently_updated._editorial_row_sources(
        settings, NOW, (story,),
    )

    assert len(sources) == 1
    assert sources[0].identity_key == recently_updated._row_identity_key(
        recently_updated._materialize_row(sources[0])
    )
    assert "Headline with no URL" in sources[0].identity_key


# --- 7. the real render function, not just the composition ---------------

def test_render_recently_updated_itself_performs_the_bounded_work(tmp_path):
    """The tests above exercise the composition render_recently_updated
    uses. This one drives the actual function, so the bound cannot be
    satisfied by a helper the renderer does not call."""
    settings = _settings(tmp_path)
    candidates, stories, editorial = _large_inputs()

    with _CallCounter(recently_updated, "fmt_datetime_local") as dates, \
            _CallCounter(_filing_display, "edinet_source_instruction") as instructions, \
            _CallCounter(recently_updated, "public_source_url") as urls:
        recently_updated.render_recently_updated(
            settings, preloaded_by_source=candidates,
            preloaded_daily_news_stories=stories, preloaded_editorial_stories=editorial,
        )

    assert dates.count <= recently_updated.PREVIEW_COUNT
    assert instructions.count <= recently_updated.PREVIEW_COUNT
    assert urls.count <= recently_updated.PREVIEW_COUNT


def test_render_recently_updated_constructs_no_repository(tmp_path):
    """Unchanged Phase 2B/2D guarantee: with every input preloaded, the
    renderer touches no backend at all. Phase 2E must not reintroduce a
    read while materializing."""
    settings = _settings(tmp_path)
    candidates, stories, editorial = _large_inputs()

    def _explode(*args, **kwargs):
        raise AssertionError("render must not construct a repository")

    with _CallCounter(recently_updated, "fmt_datetime_local"):
        original = recently_updated.backend_factory.get_candidate_repository
        recently_updated.backend_factory.get_candidate_repository = _explode
        try:
            recently_updated.render_recently_updated(
                settings, preloaded_by_source=candidates,
                preloaded_daily_news_stories=stories, preloaded_editorial_stories=editorial,
            )
        finally:
            recently_updated.backend_factory.get_candidate_repository = original


# === Daily News population counters (instrumentation only) ===============
#
# Production measures recently_updated.news_sources at ~2.0s, and a local
# reproduction attributes essentially all of it to read-time tiering of
# stories whose materiality_tier was never stored (~1.84ms each, against
# ~1.8us for a story that short-circuits on a stored tier). The durations
# cannot say how many such stories production holds. These two aggregate
# integers answer that, and must not influence anything else.

from src.ui import render_timing  # noqa: E402

_COUNTER_TOTAL = "recently_updated.news_stories_total"
_COUNTER_UNTIERED = "recently_updated.news_stories_untiered"


def _counters_for(settings, stories) -> dict:
    """Runs the loop inside a render so the counters have somewhere to
    go, and returns what it recorded."""
    captured = {}
    with render_timing.page_render("dashboard"):
        render_timing.mark_setup_complete()
        recently_updated._news_row_sources(settings, NOW, stories)
        captured.update(render_timing._current().counters)
    return captured


def _tiered(story_id: str, tier) -> object:
    return replace(_story(story_id, "Fictional Co", "Fictional Co announces a supplier agreement",
                          f"https://example.test/{story_id}"), materiality_tier=tier)


# --- population shapes ---------------------------------------------------

def test_an_empty_population_counts_zero(tmp_path):
    counters = _counters_for(_settings(tmp_path), {})

    assert counters.get(_COUNTER_TOTAL, 0) == 0
    assert counters.get(_COUNTER_UNTIERED, 0) == 0


def test_an_all_untiered_population_counts_every_story_as_untiered(tmp_path):
    stories = {f"s{i}": _tiered(f"s{i}", None) for i in range(9)}

    counters = _counters_for(_settings(tmp_path), stories)

    assert counters[_COUNTER_TOTAL] == 9
    assert counters[_COUNTER_UNTIERED] == 9


def test_a_fully_tiered_population_counts_no_untiered_stories(tmp_path):
    stories = {f"s{i}": _tiered(f"s{i}", NewsMaterialityTier.HIGH_SIGNAL) for i in range(6)}

    counters = _counters_for(_settings(tmp_path), stories)

    assert counters[_COUNTER_TOTAL] == 6
    assert counters[_COUNTER_UNTIERED] == 0


def test_a_mixed_population_counts_exactly_the_untiered_stories(tmp_path):
    stories = {}
    for i in range(10):
        tier = None if i % 3 == 0 else NewsMaterialityTier.WATCHLIST
        stories[f"s{i}"] = _tiered(f"s{i}", tier)

    counters = _counters_for(_settings(tmp_path), stories)

    assert counters[_COUNTER_TOTAL] == 10
    assert counters[_COUNTER_UNTIERED] == 4  # i = 0, 3, 6, 9
    assert counters[_COUNTER_UNTIERED] == sum(
        1 for s in stories.values() if s.materiality_tier is None
    )


# --- total counts stories ENTERING the loop, before any filter -----------

def test_total_counts_stories_that_every_gate_excludes(tmp_path):
    """The counter measures the population the loop must walk, not the
    rows it yields. Both pre-tier gates are covered: a non-published
    story, and one with no sources (which never reaches the tier check
    at all yet is still part of the population)."""
    stories = {
        "kept": _tiered("kept", NewsMaterialityTier.HIGH_SIGNAL),
        "draft": replace(_tiered("draft", NewsMaterialityTier.HIGH_SIGNAL),
                         status=NewsStoryStatus.DISCOVERED),
        "bare": replace(_tiered("bare", None), sources=()),
    }

    counters = _counters_for(_settings(tmp_path), stories)
    rows = recently_updated._news_row_sources(_settings(tmp_path), NOW, stories)

    assert counters[_COUNTER_TOTAL] == 3
    assert counters[_COUNTER_UNTIERED] == 1  # the sourceless one
    assert len(rows) == 1  # only "kept" becomes a row


def test_total_equals_the_number_of_stories_supplied(tmp_path):
    stories = {f"s{i}": _tiered(f"s{i}", None if i % 2 else NewsMaterialityTier.WATCHLIST)
               for i in range(17)}

    counters = _counters_for(_settings(tmp_path), stories)

    assert counters[_COUNTER_TOTAL] == len(stories) == 17


# --- the stored field, never the classifier's answer ---------------------

def test_a_stored_tier_counts_as_tiered_even_when_content_would_classify_differently(tmp_path):
    """The whole point of reading the stored field BEFORE
    effective_issuer_tier(): the fallback classifier returns a real tier
    for an untiered story, so inferring 'untiered' from its result would
    count nothing at all. Here a story whose headline never names its
    issuer would fall back to BACKGROUND, but it carries a stored
    HIGH_SIGNAL — it must count as tiered, and the stored tier must
    still win for display."""
    unnamed = replace(
        _story("s-stored", "Fictional Co", "An unrelated headline naming nobody in particular",
               "https://example.test/s-stored"),
        materiality_tier=NewsMaterialityTier.HIGH_SIGNAL,
    )
    from src.data_access.daily_news import daily_news_pipeline

    fallback_tier = daily_news_pipeline.classify_issuer_story(
        unnamed.headline, unnamed.sources[0].excerpt_original, unnamed.sources[0].source_class,
        issuer_names=daily_news_pipeline.issuer_name_forms(unnamed.company_name, unnamed.ticker),
    )[0]

    counters = _counters_for(_settings(tmp_path), {"s-stored": unnamed})
    rows = recently_updated._news_row_sources(_settings(tmp_path), NOW, {"s-stored": unnamed})

    assert fallback_tier == NewsMaterialityTier.BACKGROUND  # non-vacuity: they really differ
    assert daily_news_pipeline.effective_issuer_tier(unnamed) == NewsMaterialityTier.HIGH_SIGNAL
    assert counters[_COUNTER_TOTAL] == 1
    assert counters[_COUNTER_UNTIERED] == 0  # stored, therefore tiered
    assert len(rows) == 1  # and the stored tier still wins for display


# --- the counters change nothing ----------------------------------------

def test_the_counters_do_not_change_the_rows_produced(tmp_path):
    settings = _settings(tmp_path)
    stories = {f"s{i}": _tiered(f"s{i}", None if i % 2 else NewsMaterialityTier.HIGH_SIGNAL)
               for i in range(12)}

    inside_render = None
    with render_timing.page_render("dashboard"):
        render_timing.mark_setup_complete()
        inside_render = recently_updated._news_row_sources(settings, NOW, stories)
    outside_render = recently_updated._news_row_sources(settings, NOW, stories)

    assert [s.identity_key for s in inside_render] == [s.identity_key for s in outside_render]
    assert [s.sort_key for s in inside_render] == [s.sort_key for s in outside_render]
    assert [s.title for s in inside_render] == [s.title for s in outside_render]


def test_counting_is_a_no_op_for_standalone_callers(tmp_path):
    """Outside a render there is nowhere to record, and the function
    must behave exactly as before — no raise, and the same rows."""
    settings = _settings(tmp_path)
    stories = {f"s{i}": _tiered(f"s{i}", NewsMaterialityTier.HIGH_SIGNAL) for i in range(4)}

    outside = recently_updated._news_row_sources(settings, NOW, stories)
    with render_timing.page_render("dashboard"):
        render_timing.mark_setup_complete()
        inside = recently_updated._news_row_sources(settings, NOW, stories)

    assert len(outside) == 4
    assert [s.identity_key for s in outside] == [s.identity_key for s in inside]


def test_an_untiered_story_is_counted_even_when_the_fallback_excludes_it(tmp_path):
    """The counter measures the population that had to be classified,
    not the rows that survived — an untiered story the fallback rules
    send to Background still paid the full classification cost, which is
    exactly the cost being investigated."""
    stories = {f"s{i}": _tiered(f"s{i}", None) for i in range(4)}

    counters = _counters_for(_settings(tmp_path), stories)
    rows = recently_updated._news_row_sources(_settings(tmp_path), NOW, stories)

    assert counters[_COUNTER_TOTAL] == 4
    assert counters[_COUNTER_UNTIERED] == 4
    assert rows == []  # all fell to Background through the fallback path


def test_no_story_content_reaches_the_emitted_timing_record(tmp_path, caplog):
    """The counters are aggregate integers. A real headline, company,
    ticker, URL, excerpt, id or tier name must never appear in the
    record — only fixed internal counter names and totals."""
    import logging

    settings = _settings(tmp_path)
    secret_company = "Distinctive" + "CompanyName"
    secret_headline = "Distinctive" + "HeadlineText"
    secret_url = "https://distinctive" + "host.test/path"
    story = replace(
        _story("newsitem-distinctive-id", secret_company, secret_headline, secret_url),
        ticker="ZZTOP", materiality_tier=None,
    )

    logger = logging.getLogger(render_timing.LOGGER_NAME)
    render_timing._ensure_logger_configured()
    logger.addHandler(caplog.handler)
    try:
        with render_timing.page_render("dashboard"):
            render_timing.mark_setup_complete()
            with render_timing.step("recently_updated.news_sources"):
                recently_updated._news_row_sources(settings, NOW, {story.id: story})
    finally:
        logger.removeHandler(caplog.handler)

    emitted = "\n".join(r.getMessage() for r in caplog.records)
    assert "counters=" in emitted  # non-vacuity: a record really was emitted
    for needle in (secret_company, secret_headline, secret_url, "ZZTOP",
                   "newsitem-distinctive-id", "distinctive"):
        assert needle.lower() not in emitted.lower(), needle
