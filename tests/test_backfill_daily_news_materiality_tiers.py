"""scripts/backfill_daily_news_materiality_tiers.py — the manual,
one-shot legacy materiality_tier backfill.

Fully offline: a small in-memory fake repository stands in for the real
DailyNewsRepositoryProtocol, so no database, connection, credential,
network, or production data is involved. Fixture content is invented.

Two claims are proven here and they are different:

  1. the backfill WRITES THE SAME VALUE the Dashboard already computes
     at read time — asserted against effective_issuer_tier() itself,
     including a non-vacuous BACKGROUND case, because a stored
     BACKGROUND removes a row from Recently Updated exactly as today's
     fallback does; and
  2. the run is bounded, idempotent, race-safe, failure-safe, and emits
     nothing but fixed-name aggregate integers.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from scripts import backfill_daily_news_materiality_tiers as backfill
from src.data_access.daily_news import daily_news_pipeline
from src.data_access.daily_news.daily_news_backend import DailyNewsUpdateOutcome
from src.models.daily_news_models import (
    NewsMaterialityTier,
    NewsSourceReference,
    NewsStateTransition,
    NewsStory,
    NewsStoryStatus,
    SourceClass,
)

AT = "2026-09-20T12:00:00+00:00"

# Two invented headlines whose CURRENT classifier outcomes differ. The
# equivalence tests below assert the real outcome rather than assuming
# it, so a classifier revision changes what these mean without silently
# weakening any assertion.
NAMED_MATERIAL_HEADLINE = (
    "Fictional Components Corporation announces a definitive agreement to acquire a supplier"
)
ISSUER_NOT_NAMED_HEADLINE = "An unrelated market commentary naming nobody in particular"


def _story(
    story_id: str, *, headline: str = NAMED_MATERIAL_HEADLINE,
    tier: NewsMaterialityTier | None = None, sources: tuple | None = None,
    company: str = "Fictional Components Corporation", ticker: str | None = "TCKR",
    status: NewsStoryStatus = NewsStoryStatus.PUBLISHED,
) -> NewsStory:
    default_sources = (
        NewsSourceReference(
            publisher=company, source_class=SourceClass.OFFICIAL_COMPANY,
            url=f"https://example.test/{story_id}", title=headline,
            published_at=AT, retrieved_at=AT, original_language="English",
            excerpt_original="The company said the arrangement covers supply over a multi-year period.",
        ),
    )
    return NewsStory(
        id=story_id, company_name=company, ticker=ticker, theme_slug="ai-buildout",
        headline=headline, eeva_summary="A neutral fixture summary.", is_fallback_summary=False,
        translation_unavailable=False, original_title=None,
        sources=default_sources if sources is None else sources,
        status=status,
        state_history=[NewsStateTransition(status=status, at=AT)],
        materiality_tier=tier,
    )


class _FakeRepository:
    """In-memory DailyNewsRepositoryProtocol stand-in. Records every
    update attempt so a dry run can be proven to make none."""

    def __init__(self, stories, *, versions=None, update_outcome=None, update_error=None):
        self.stories = {s.id: s for s in stories}
        self.versions = versions or {s.id: 1 for s in stories}
        self.update_calls: list[str] = []
        self._update_outcome = update_outcome
        self._update_error = update_error
        self.get_story_hook = None

    def load_stories(self):
        return dict(self.stories)

    def get_story(self, story_id):
        if self.get_story_hook is not None:
            return self.get_story_hook(story_id)
        return self.stories.get(story_id)

    def get_story_version(self, story_id):
        return self.versions.get(story_id)

    def upsert_new_stories(self, new_stories):  # pragma: no cover — unused here
        raise AssertionError("the backfill must never upsert")

    def update_story(self, story, expected_version=None):
        self.update_calls.append(story.id)
        if self._update_error is not None:
            raise self._update_error
        if self._update_outcome is not None:
            return DailyNewsUpdateOutcome(status=self._update_outcome, current=None)
        self.stories[story.id] = story
        self.versions[story.id] = (self.versions.get(story.id) or 0) + 1
        return DailyNewsUpdateOutcome(status="updated", current=story)


# --- 1. classifier equivalence with the live read fallback ---------------

@pytest.mark.parametrize("headline", [NAMED_MATERIAL_HEADLINE, ISSUER_NOT_NAMED_HEADLINE])
def test_proposed_tier_equals_the_read_fallback(headline):
    """The value written must be the value every read path already
    computes. Asserted against effective_issuer_tier() itself, so the
    two constructions cannot drift."""
    story = _story("s1", headline=headline)

    proposed_tier, _ = backfill._proposed_classification(story)

    assert proposed_tier == daily_news_pipeline.effective_issuer_tier(story)


def test_a_background_case_is_covered_non_vacuously():
    """A stored BACKGROUND removes a row from Recently Updated exactly
    as today's fallback does, so the BACKGROUND path must be exercised
    for real — not merely assumed to exist."""
    background_story = _story("s-bg", headline=ISSUER_NOT_NAMED_HEADLINE)
    material_story = _story("s-hs", headline=NAMED_MATERIAL_HEADLINE)

    background_tier, background_reasons = backfill._proposed_classification(background_story)
    material_tier, _ = backfill._proposed_classification(material_story)

    assert background_tier == NewsMaterialityTier.BACKGROUND
    assert background_tier == daily_news_pipeline.effective_issuer_tier(background_story)
    assert background_reasons  # the fallback's reasons are real, not empty
    assert material_tier != background_tier  # non-vacuity: the two really differ


def test_reasons_are_the_classifier_s_own_and_are_persisted():
    story = _story("s1")
    expected_tier, expected_reasons = backfill._proposed_classification(story)
    repository = _FakeRepository([story])

    backfill.run_backfill(repository, write=True, limit=10)

    stored = repository.stories["s1"]
    assert stored.materiality_tier == expected_tier
    assert stored.materiality_reasons == expected_reasons
    assert stored.materiality_tier == daily_news_pipeline.effective_issuer_tier(story)


def test_the_read_fallback_still_reads_and_never_writes():
    """The backfill must not have changed how the Dashboard reads.

    Checks the EXECUTABLE body, not the docstring — the docstring now
    points operators at the backfill script, which is a documentation
    change and must not fail this guard."""
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(daily_news_pipeline.effective_issuer_tier)))
    function = tree.body[0]
    body = function.body[1:] if ast.get_docstring(function) else function.body
    rendered = "\n".join(ast.unparse(node) for node in body)

    # still a pure read: no repository, no write, no persistence
    for forbidden in ("update_story", "upsert", "repository", "backfill", "save", "commit"):
        assert forbidden not in rendered, forbidden
    # still short-circuits on a stored tier, and still discards reasons
    assert "if story.materiality_tier is not None" in rendered
    assert "return story.materiality_tier" in rendered
    assert "tier, _ = classify_issuer_story" in rendered

    # and behaviourally: a stored tier wins without reclassifying
    stored = _story("s", headline=ISSUER_NOT_NAMED_HEADLINE, tier=NewsMaterialityTier.HIGH_SIGNAL)
    assert daily_news_pipeline.effective_issuer_tier(stored) == NewsMaterialityTier.HIGH_SIGNAL


# --- 2. dry run writes nothing ------------------------------------------

def test_dry_run_invokes_no_update_even_with_eligible_candidates():
    repository = _FakeRepository([_story(f"s{i}") for i in range(5)])

    summary = backfill.run_backfill(repository, write=False, limit=10)

    assert repository.update_calls == []
    assert summary.written == 0
    assert summary.mode == "dry_run"
    assert summary.eligible_total == 5
    assert summary.eligible_processed == 5
    assert summary.proposed_total == 5
    assert all(story.materiality_tier is None for story in repository.stories.values())


# --- 3. a stored tier is never overwritten -------------------------------

def test_a_stored_tier_is_never_overwritten():
    """Including a stored BACKGROUND whose content the current
    classifier would tier differently."""
    stored = _story("s-stored", headline=NAMED_MATERIAL_HEADLINE,
                    tier=NewsMaterialityTier.BACKGROUND)
    would_be, _ = backfill._proposed_classification(stored)
    repository = _FakeRepository([stored])

    summary = backfill.run_backfill(repository, write=True, limit=10)

    assert would_be != NewsMaterialityTier.BACKGROUND  # non-vacuity: they really differ
    assert repository.update_calls == []
    assert repository.stories["s-stored"].materiality_tier == NewsMaterialityTier.BACKGROUND
    assert summary.skipped_already_tiered == 1
    assert summary.eligible_total == 0


# --- 4. idempotency ------------------------------------------------------

def test_a_second_run_writes_nothing_and_finds_nothing_eligible():
    repository = _FakeRepository([_story(f"s{i}") for i in range(4)])

    first = backfill.run_backfill(repository, write=True, limit=10)
    calls_after_first = len(repository.update_calls)
    second = backfill.run_backfill(repository, write=True, limit=10)

    assert first.written == 4
    assert second.written == 0
    assert second.eligible_total == 0
    assert second.skipped_already_tiered == 4
    assert len(repository.update_calls) == calls_after_first  # no further attempts


# --- 5. races, conflicts, missing records --------------------------------

def test_a_story_tiered_by_another_process_between_read_and_write_is_skipped():
    story = _story("s1")
    repository = _FakeRepository([story])
    repository.get_story_hook = lambda _id: replace(story, materiality_tier=NewsMaterialityTier.WATCHLIST)

    summary = backfill.run_backfill(repository, write=True, limit=10)

    assert repository.update_calls == []  # never even attempted
    assert summary.skipped_raced == 1
    assert summary.written == 0


def test_an_optimistic_lock_conflict_is_a_safe_skip_and_is_not_retried():
    repository = _FakeRepository([_story("s1")], update_outcome="conflict")

    summary = backfill.run_backfill(repository, write=True, limit=10)

    assert len(repository.update_calls) == 1  # attempted once, never retried
    assert summary.skipped_raced == 1
    assert summary.written == 0


def test_a_missing_record_at_write_time_is_a_safe_skip():
    repository = _FakeRepository([_story("s1")])
    repository.get_story_hook = lambda _id: None

    summary = backfill.run_backfill(repository, write=True, limit=10)

    assert repository.update_calls == []
    assert summary.skipped_missing == 1
    assert summary.written == 0


def test_a_not_found_outcome_is_a_safe_skip():
    repository = _FakeRepository([_story("s1")], update_outcome="not_found")

    summary = backfill.run_backfill(repository, write=True, limit=10)

    assert summary.skipped_missing == 1
    assert summary.written == 0


# --- 6. failure handling -------------------------------------------------

def test_a_classification_failure_writes_nothing_and_is_counted_by_class(monkeypatch):
    monkeypatch.setattr(
        backfill, "_proposed_classification",
        lambda story: (_ for _ in ()).throw(ValueError("boom-with-sensitive-payload")),
    )
    repository = _FakeRepository([_story("s1")])

    summary = backfill.run_backfill(repository, write=True, limit=10, failure_budget=99)

    assert repository.update_calls == []
    assert summary.failures_by_exception_class == {"ValueError": 1}
    assert summary.written == 0
    assert repository.stories["s1"].materiality_tier is None  # still eligible for a rerun


def test_the_failure_budget_stops_the_run(monkeypatch):
    monkeypatch.setattr(
        backfill, "_proposed_classification",
        lambda story: (_ for _ in ()).throw(ValueError("boom")),
    )
    repository = _FakeRepository([_story(f"s{i}") for i in range(20)])

    summary = backfill.run_backfill(repository, write=True, limit=20, failure_budget=3)

    assert summary.stopped_on_failure_budget is True
    assert summary.failures == 3
    assert summary.classification_failures == 3
    assert summary.write_failures == 0
    assert summary.scan_complete is False  # stopped at the budget, did not grind through all 20
    assert summary.written == 0


def test_without_a_failure_budget_breach_the_run_completes():
    repository = _FakeRepository([_story(f"s{i}") for i in range(4)])

    summary = backfill.run_backfill(repository, write=True, limit=10, failure_budget=3)

    assert summary.stopped_on_failure_budget is False
    assert summary.written == 4


# --- 6b. classifier-output validation before any write -------------------

@pytest.mark.parametrize("bad_result,expected_class", [
    (("High Signal", ()), "MalformedClassificationError"),          # a str, not the enum
    ((None, ()), "MalformedClassificationError"),                    # no tier at all
    ((NewsMaterialityTier.HIGH_SIGNAL, ["a"]), "MalformedClassificationError"),   # list, not tuple
    ((NewsMaterialityTier.HIGH_SIGNAL, (1, 2)), "MalformedClassificationError"),  # non-str reasons
])
def test_a_malformed_classifier_result_writes_nothing_and_is_counted(
    monkeypatch, bad_result, expected_class,
):
    """The repositories persist tier.value and json.dumps(list(reasons)),
    so a malformed result would either raise deep inside the write or
    store something unreadable. It is caught before any write and
    counted like any other per-story failure."""
    monkeypatch.setattr(backfill, "_proposed_classification", lambda story: bad_result)
    repository = _FakeRepository([_story("s1")])

    summary = backfill.run_backfill(repository, write=True, limit=10, failure_budget=99)

    assert repository.update_calls == []
    assert summary.failures_by_exception_class == {expected_class: 1}
    assert summary.classification_failures == 1
    assert summary.written == 0
    assert repository.stories["s1"].materiality_tier is None  # still eligible
    _assert_invariants(summary)


def test_a_well_formed_classifier_result_passes_validation():
    """Non-vacuity for the four cases above: the real classifier's own
    output must satisfy the same contract."""
    tier, reasons = backfill._validated_classification(_story("s1"))

    assert isinstance(tier, NewsMaterialityTier)
    assert isinstance(reasons, tuple)
    assert all(isinstance(reason, str) for reason in reasons)


# --- 7. skips and bounds -------------------------------------------------

def test_a_story_with_no_sources_is_skipped_and_never_written():
    repository = _FakeRepository([_story("s-bare", sources=()), _story("s-ok")])

    summary = backfill.run_backfill(repository, write=True, limit=10)

    assert summary.skipped_no_sources == 1
    assert summary.eligible_total == 1
    assert repository.update_calls == ["s-ok"]
    assert repository.stories["s-bare"].materiality_tier is None


@pytest.mark.parametrize("limit,expected_written", [(0, 0), (1, 1), (3, 3), (10, 8)])
def test_the_limit_bounds_writes_deterministically(limit, expected_written):
    repository = _FakeRepository([_story(f"s{i:02d}") for i in range(8)])

    summary = backfill.run_backfill(repository, write=True, limit=limit)

    assert summary.written == expected_written
    assert len(repository.update_calls) == expected_written


def test_the_default_limit_is_conservative_and_never_unbounded():
    assert isinstance(backfill.DEFAULT_LIMIT, int)
    assert 0 < backfill.DEFAULT_LIMIT <= 100
    assert backfill._parse_args([]).limit == backfill.DEFAULT_LIMIT
    assert backfill._parse_args([]).write is False  # dry run is the default


# --- 8. invariants -------------------------------------------------------

def _assert_invariants(summary) -> None:
    """Every invariant the operational schema promises, checked
    literally, for whichever mode produced this summary."""
    # discovery population
    assert summary.candidates_seen == (
        summary.skipped_already_tiered + summary.skipped_no_sources + summary.eligible_total
    )
    # processing population never exceeds discovery
    assert summary.eligible_processed <= summary.eligible_total
    # every processed candidate either produced a proposal or failed to classify
    assert summary.eligible_processed == summary.proposed_total + summary.classification_failures
    # remaining is exact only when the scan completed
    if summary.scan_complete:
        assert summary.eligible_remaining == summary.eligible_total - summary.eligible_processed
    else:
        assert summary.eligible_remaining is None
    if summary.mode == "dry_run":
        assert summary.written == 0
    else:
        # every processed candidate is accounted for exactly once
        assert summary.eligible_processed == (
            summary.written + summary.skipped_raced + summary.skipped_missing + summary.failures
        )


def test_invariants_hold_for_a_mixed_population_dry_run():
    repository = _FakeRepository([
        _story("s-untiered-1"),
        _story("s-untiered-2", headline=ISSUER_NOT_NAMED_HEADLINE),
        _story("s-tiered", tier=NewsMaterialityTier.HIGH_SIGNAL),
        _story("s-bare", sources=()),
    ])

    summary = backfill.run_backfill(repository, write=False, limit=10)

    _assert_invariants(summary)
    assert summary.candidates_seen == 4
    assert summary.skipped_already_tiered == 1
    assert summary.skipped_no_sources == 1
    assert summary.eligible_total == 2
    assert summary.eligible_processed == 2
    assert summary.eligible_remaining == 0


def test_invariants_hold_for_a_mixed_population_write_run():
    repository = _FakeRepository([
        _story("s1"), _story("s2", headline=ISSUER_NOT_NAMED_HEADLINE),
        _story("s-tiered", tier=NewsMaterialityTier.WATCHLIST), _story("s-bare", sources=()),
    ])

    summary = backfill.run_backfill(repository, write=True, limit=10)

    _assert_invariants(summary)
    assert summary.written == 2


def test_a_limit_bounded_run_reports_an_exact_remaining_count():
    """--limit bounds PROCESSING, not scanning, so the discovery
    counters still describe the whole store and remaining is exact."""
    repository = _FakeRepository([_story(f"s{i:02d}") for i in range(8)])

    summary = backfill.run_backfill(repository, write=True, limit=3)

    _assert_invariants(summary)
    assert summary.candidates_seen == 8
    assert summary.eligible_total == 8
    assert summary.eligible_processed == 3
    assert summary.eligible_remaining == 5
    assert summary.limit_reached is True
    assert summary.scan_complete is True
    assert summary.written == 3


def test_a_failure_budget_stop_reports_remaining_as_unknown(monkeypatch):
    """An early stop must never hide inside an apparently whole-store
    aggregate: the scan ended before discovery finished, so no exact
    remaining count can honestly be given."""
    monkeypatch.setattr(
        backfill, "_proposed_classification",
        lambda story: (_ for _ in ()).throw(ValueError("boom")),
    )
    repository = _FakeRepository([_story(f"s{i:02d}") for i in range(20)])

    summary = backfill.run_backfill(repository, write=True, limit=20, failure_budget=2)

    _assert_invariants(summary)
    assert summary.scan_complete is False
    assert summary.eligible_remaining is None
    assert summary.candidates_seen < 20  # the scan really did stop early
    assert 'eligible_remaining="unknown"' in summary.render(batch=1, git_commit="0" * 40)


def test_a_write_failure_is_counted_and_leaves_the_record_eligible():
    """A write failure consumes a processed candidate AFTER its proposal
    was counted; a classification failure consumes one INSTEAD of a
    proposal. Tracking them in separate buckets is what makes both
    invariants literally true at once — and the record must stay
    eligible for a later, manually approved rerun."""
    repository = _FakeRepository([_story("s1")], update_error=RuntimeError("boom-with-sensitive-payload"))

    summary = backfill.run_backfill(repository, write=True, limit=10, failure_budget=99)

    _assert_invariants(summary)
    assert summary.failures_by_exception_class == {"RuntimeError": 1}
    assert summary.proposed_total == 1
    assert summary.classification_failures == 0
    assert summary.write_failures == 1
    assert summary.written == 0
    assert repository.stories["s1"].materiality_tier is None  # still eligible


def test_a_run_over_an_empty_store_is_trivially_consistent():
    summary = backfill.run_backfill(_FakeRepository([]), write=True, limit=10)

    _assert_invariants(summary)
    assert summary.candidates_seen == 0
    assert summary.eligible_remaining == 0


# --- 9. aggregate-only output --------------------------------------------

def test_the_rendered_output_carries_only_fixed_aggregate_fields():
    repository = _FakeRepository([_story("s1")])
    summary = backfill.run_backfill(repository, write=True, limit=10)

    rendered = summary.render(batch=1, git_commit="0" * 40)

    for field_name in (
        "event=", "mode=", "git_commit=", "batch=", "limit=", "candidates_seen=",
        "skipped_already_tiered=", "skipped_no_sources=",
        "eligible_total=", "eligible_processed=", "eligible_remaining=",
        "proposed_high_signal=", "proposed_watchlist=", "proposed_background=",
        "written=", "skipped_raced=", "skipped_missing=",
        "classification_failures=", "write_failures=", "failures_by_exception_class=",
        "scan_complete=", "limit_reached=", "stopped_on_failure_budget=", "elapsed_ms=",
    ):
        assert field_name in rendered, field_name

    # every rendered value is a fixed token, an integer, or a float —
    # never free text that could carry content
    import re as _re

    for key, value in _re.findall(r'(\w+)="?([^"\s]*)"?', rendered):
        assert _re.fullmatch(r"[A-Za-z0-9_.,=\-]*", value), (key, value)


def test_no_seeded_sensitive_material_reaches_the_output():
    secret_company = "Distinctive" + "CompanyName"
    secret_headline = "Distinctive" + "HeadlineText"
    secret_url = "https://distinctive" + "host.test/path"
    story = NewsStory(
        id="newsitem-distinctive-id", company_name=secret_company, ticker="ZZTOP",
        theme_slug="ai-buildout", headline=secret_headline,
        eeva_summary="DistinctiveSummaryText", is_fallback_summary=False,
        translation_unavailable=False, original_title=None,
        sources=(NewsSourceReference(
            publisher=secret_company, source_class=SourceClass.OFFICIAL_COMPANY, url=secret_url,
            title=secret_headline, published_at=AT, retrieved_at=AT,
            original_language="English", excerpt_original="DistinctiveExcerptText"),),
        status=NewsStoryStatus.PUBLISHED,
        state_history=[NewsStateTransition(status=NewsStoryStatus.PUBLISHED, at=AT)],
        materiality_tier=None,
    )
    repository = _FakeRepository([story], update_error=RuntimeError("DistinctiveExceptionMessage"))

    summary = backfill.run_backfill(repository, write=True, limit=10, failure_budget=99)
    rendered = summary.render(batch=1, git_commit="0" * 40)

    assert "failures_by_exception_class=" in rendered  # non-vacuity: a failure was recorded
    assert summary.failures_by_exception_class == {"RuntimeError": 1}
    for needle in (
        secret_company, secret_headline, secret_url, "ZZTOP", "newsitem-distinctive-id",
        "DistinctiveSummaryText", "DistinctiveExcerptText", "DistinctiveExceptionMessage",
        "distinctive", "Traceback",
    ):
        assert needle.lower() not in rendered.lower(), needle


def test_the_output_is_one_line_with_no_per_story_events():
    repository = _FakeRepository([_story(f"s{i}") for i in range(12)])
    summary = backfill.run_backfill(repository, write=True, limit=12)

    rendered = summary.render(batch=1, git_commit="0" * 40)

    assert "\n" not in rendered
    assert rendered.count('event="materiality_backfill"') == 1


def test_dry_run_output_reports_written_zero():
    repository = _FakeRepository([_story(f"s{i}") for i in range(3)])
    summary = backfill.run_backfill(repository, write=False, limit=10)

    assert 'mode="dry_run"' in summary.render(batch=1, git_commit="0" * 40)
    assert "written=0" in summary.render(batch=1, git_commit="0" * 40)


# --- 10. unsupported backend is a no-op ----------------------------------

@pytest.mark.parametrize("backend", ["json", "", None, "JSON", "mongo"])
def test_an_unsupported_backend_exits_zero_without_acting(monkeypatch, capsys, backend):
    from src.config.settings import Settings

    monkeypatch.setattr(backfill, "get_settings", lambda: Settings(db_backend=backend))

    def _explode(_settings):
        raise AssertionError("no repository may be constructed for an unsupported backend")

    monkeypatch.setattr(backfill.daily_news_backend, "get_daily_news_repository", _explode)

    assert backfill.main([]) == 0
    assert "Exiting without changing anything" in capsys.readouterr().out


@pytest.mark.parametrize("backend", ["sqlite", "postgres", "  Postgres  "])
def test_a_supported_backend_runs_the_backfill(monkeypatch, capsys, backend):
    from src.config.settings import Settings

    repository = _FakeRepository([_story("s1")])
    monkeypatch.setattr(backfill, "get_settings", lambda: Settings(db_backend=backend))
    monkeypatch.setattr(backfill.daily_news_backend, "get_daily_news_repository", lambda _s: repository)

    assert backfill.main([]) == 0

    out = capsys.readouterr().out
    assert 'event="materiality_backfill"' in out
    assert 'mode="dry_run"' in out
    assert repository.update_calls == []  # default is still a dry run


# --- 11. the Dashboard read result is unchanged --------------------------

def test_recently_updated_selects_identical_rows_before_and_after_backfill(tmp_path):
    """The only difference a backfill makes is that later reads use the
    stored fast path instead of recomputing the same tier."""
    from src.config.settings import Settings
    from src.ui.components import recently_updated

    settings = Settings(db_backend="json", cache_dir=tmp_path)
    stories = {
        s.id: s for s in (
            _story("s-material-1"),
            _story("s-material-2"),
            _story("s-background", headline=ISSUER_NOT_NAMED_HEADLINE),
        )
    }
    before = recently_updated._news_row_sources(settings, _now(), dict(stories))

    repository = _FakeRepository(list(stories.values()))
    backfill.run_backfill(repository, write=True, limit=10)
    after = recently_updated._news_row_sources(settings, _now(), repository.load_stories())

    assert [s.identity_key for s in before] == [s.identity_key for s in after]
    assert [s.title for s in before] == [s.title for s in after]
    assert [s.sort_key for s in before] == [s.sort_key for s in after]
    # non-vacuity: the backfill really did store tiers, and the
    # background story really was excluded from the rows both times
    assert all(s.materiality_tier is not None for s in repository.load_stories().values())
    assert len(before) == 2


def _now():
    from datetime import datetime, timezone

    return datetime(2026, 9, 21, tzinfo=timezone.utc)
