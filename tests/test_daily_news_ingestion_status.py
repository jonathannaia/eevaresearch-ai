"""Reader-facing Daily News ingestion status (reader-facing product
truthfulness pass) — what the worker recorded, what it does not
establish, and the distinction between an empty view and un-ingested
coverage.

Every test here is a pure classification check, a cache-contract check,
a sanitized-read check, or an AppTest render check. No fetch, no network,
no worker run, no pipeline call, and no admission/ranking/publication
logic is exercised or changed — the last section asserts exactly that.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.data_access.backend_factory import BackendConfigurationError
from src.data_access.state_db.daily_news_scan_status_repository import (
    DailyNewsFeedScanStatus,
    DailyNewsWorkerStatus,
)
from src.logic.daily_news_freshness import (
    ATTEMPT_FAILED_MESSAGE,
    DEFAULT_INTERVAL_MINUTES,
    EDITORIAL_COVERAGE_RECORDED,
    LAST_SUCCESS_ALSO_STALE_MESSAGE,
    MARKET_NEWS_COVERAGE_UNKNOWN_NOTE,
    NO_EARLIER_RUN_MESSAGE,
    QUIET_FEED_CONFIRMED_MESSAGE,
    QUIET_FEED_UNCONFIRMABLE_MESSAGE,
    STALE_THRESHOLD_MULTIPLIER,
    UNKNOWN_MESSAGE,
    DailyNewsIngestionStatus,
    classify_ingestion,
    effective_interval_minutes,
    empty_feed_explanation,
    failed_feed_count,
    stale_threshold_minutes,
)
from src.ui.components import daily_news_status
from src.ui.components.daily_news_status import (
    PersistedIngestionFacts,
    current_ingestion_status,
    ingestion_status_html,
    read_persisted_facts,
)
from src.ui.pages.daily_news import _NO_HIGH_SIGNAL_EMPTY_STATE, _NO_WATCHLIST_EMPTY_STATE

_HARNESS = Path(__file__).parent / "apptest_pages" / "daily_news_page.py"
_REPO_ROOT = Path(__file__).parent.parent

_NOW = datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc)
_INTERVAL = 30
_THRESHOLD = _INTERVAL * STALE_THRESHOLD_MULTIPLIER  # 90 minutes


def _iso(minutes_ago: float) -> str:
    return (_NOW - timedelta(minutes=minutes_ago)).isoformat()


def _worker(**overrides) -> DailyNewsWorkerStatus:
    fields = dict(
        worker_key="daily_news",
        last_tick_started_at=_iso(10),
        last_tick_completed_at=_iso(9),
        last_reconciliation_at=_iso(600),
        last_failure_code=None,
        updated_at=_iso(9),
    )
    fields.update(overrides)
    return DailyNewsWorkerStatus(**fields)


def _feed(**overrides) -> DailyNewsFeedScanStatus:
    fields = dict(
        company_name="NVIDIA",
        last_attempt_at=_iso(9),
        last_fetch_success_at=_iso(9),
        last_story_published_at=_iso(9),
        last_failure_code=None,
        items_discovered_last_run=0,
        stories_published_last_run=0,
        updated_at=_iso(9),
    )
    fields.update(overrides)
    return DailyNewsFeedScanStatus(**fields)


def _clear_cache() -> None:
    daily_news_status._persisted_facts_cached.clear()


# ============================================================
# The five states.
# ============================================================


def test_recent_completed_tick_is_current():
    status = classify_ingestion(_worker(), _INTERVAL, now=_NOW)

    assert status.state == "current"
    assert "Last recorded run completed" in status.message


def test_a_feed_with_nothing_to_report_is_still_current():
    """A feed that fetched successfully and simply had nothing new is
    exactly as current as a busy one. Nothing here reads a story count
    or a publication date."""
    status = classify_ingestion(_worker(), _INTERVAL, failed_feeds=0, now=_NOW)

    assert status.state == "current"


def test_last_completed_tick_older_than_the_threshold_is_stale():
    status = classify_ingestion(
        _worker(last_tick_completed_at=_iso(_THRESHOLD + 1)), _INTERVAL, now=_NOW,
    )

    assert status.state == "stale"
    assert status.message.startswith("No recent run recorded")
    assert "The last recorded run completed" in status.message
    assert status.last_success_stale


def test_recorded_failure_code_is_a_failed_latest_attempt():
    status = classify_ingestion(_worker(last_failure_code="FETCH_FAILED"), _INTERVAL, now=_NOW)

    assert status.state == "attempt_failed"
    assert ATTEMPT_FAILED_MESSAGE in status.message


def test_missing_status_row_is_unknown_not_healthy_and_not_broken():
    status = classify_ingestion(None, _INTERVAL, now=_NOW)

    assert status.state == "unknown"
    assert status.message == UNKNOWN_MESSAGE


def test_a_tick_that_started_but_never_completed_is_unknown():
    status = classify_ingestion(
        _worker(last_tick_started_at=_iso(2), last_tick_completed_at=None), _INTERVAL, now=_NOW,
    )

    assert status.state == "unknown"


def test_partial_when_a_company_feed_failed_on_its_own_latest_attempt():
    status = classify_ingestion(_worker(), _INTERVAL, failed_feeds=1, now=_NOW)

    assert status.state == "partial"
    assert "Some company feeds could not be reached" in status.message


# ============================================================
# Threshold boundaries.
# ============================================================


def test_exactly_at_the_threshold_is_still_current():
    status = classify_ingestion(_worker(last_tick_completed_at=_iso(_THRESHOLD)), _INTERVAL, now=_NOW)

    assert status.state == "current"
    assert not status.last_success_stale


def test_one_minute_past_the_threshold_is_stale():
    status = classify_ingestion(_worker(last_tick_completed_at=_iso(_THRESHOLD + 1)), _INTERVAL, now=_NOW)

    assert status.state == "stale"


def test_the_boundary_moves_with_the_configured_interval():
    ten_minute_threshold = 10 * STALE_THRESHOLD_MULTIPLIER

    assert classify_ingestion(
        _worker(last_tick_completed_at=_iso(ten_minute_threshold)), 10, now=_NOW,
    ).state == "current"
    assert classify_ingestion(
        _worker(last_tick_completed_at=_iso(ten_minute_threshold + 1)), 10, now=_NOW,
    ).state == "stale"


def test_a_partial_run_past_the_threshold_reports_staleness_not_partialness():
    """Staleness is the more serious fact and is checked first: a run that
    completed hours ago is not described by which feeds it missed."""
    status = classify_ingestion(
        _worker(last_tick_completed_at=_iso(_THRESHOLD + 60)), _INTERVAL, failed_feeds=3, now=_NOW,
    )

    assert status.state == "stale"


def test_the_default_interval_is_used_only_for_a_missing_or_invalid_value():
    assert effective_interval_minutes(45) == 45
    assert effective_interval_minutes(None) == DEFAULT_INTERVAL_MINUTES
    assert effective_interval_minutes(0) == DEFAULT_INTERVAL_MINUTES
    assert effective_interval_minutes(-5) == DEFAULT_INTERVAL_MINUTES
    assert stale_threshold_minutes(None) == DEFAULT_INTERVAL_MINUTES * STALE_THRESHOLD_MULTIPLIER


def test_the_approved_threshold_is_three_intervals_with_a_ninety_minute_default():
    """The selected policy, asserted as behaviour: stale after 3x the
    configured interval, which is 90 minutes at the 30-minute default."""
    assert STALE_THRESHOLD_MULTIPLIER == 3
    assert DEFAULT_INTERVAL_MINUTES == 30
    assert stale_threshold_minutes(None) == 90
    assert stale_threshold_minutes(30) == 90
    assert stale_threshold_minutes(20) == 60

    # And the boundary it produces, end to end.
    assert classify_ingestion(_worker(last_tick_completed_at=_iso(90)), 30, now=_NOW).state == "current"
    assert classify_ingestion(_worker(last_tick_completed_at=_iso(91)), 30, now=_NOW).state == "stale"


def test_the_threshold_uses_the_interval_configured_for_this_process():
    """The dashboard cannot discover the worker's interval, so the
    threshold must follow the interval it is given and nothing else —
    including when that is a value the worker would never use."""
    recorded_at = _iso(200)

    assert classify_ingestion(_worker(last_tick_completed_at=recorded_at), 10, now=_NOW).state == "stale"
    assert classify_ingestion(_worker(last_tick_completed_at=recorded_at), 120, now=_NOW).state == "current"


# ============================================================
# Failed attempt plus stale last success — both facts preserved.
# ============================================================


def test_a_failed_attempt_never_presents_as_current_even_with_a_recent_success():
    status = classify_ingestion(
        _worker(last_tick_completed_at=_iso(1), last_failure_code="CONNECTION_FAILED"), _INTERVAL, now=_NOW,
    )

    assert status.state == "attempt_failed"
    assert "Last recorded run completed" in status.message
    assert not status.last_success_stale


def test_a_failed_attempt_on_top_of_a_stale_success_reports_both():
    status = classify_ingestion(
        _worker(last_tick_completed_at=_iso(_THRESHOLD + 120), last_failure_code="CONNECTION_FAILED"),
        _INTERVAL, now=_NOW,
    )

    assert status.state == "attempt_failed"
    # The failure.
    assert ATTEMPT_FAILED_MESSAGE in status.message
    # The age of the last recorded run, and that it is no longer recent.
    assert "Last recorded run completed" in status.message
    assert LAST_SUCCESS_ALSO_STALE_MESSAGE in status.message
    assert status.last_success_stale


def test_a_failed_attempt_at_the_boundary_does_not_call_the_last_success_stale():
    status = classify_ingestion(
        _worker(last_tick_completed_at=_iso(_THRESHOLD), last_failure_code="E"), _INTERVAL, now=_NOW,
    )

    assert status.state == "attempt_failed"
    assert not status.last_success_stale
    assert LAST_SUCCESS_ALSO_STALE_MESSAGE not in status.message


def test_a_failed_attempt_with_no_prior_success_says_so_explicitly():
    status = classify_ingestion(
        _worker(last_tick_completed_at=None, last_failure_code="CONNECTION_FAILED"), _INTERVAL, now=_NOW,
    )

    assert status.state == "attempt_failed"
    assert NO_EARLIER_RUN_MESSAGE in status.message
    assert not status.last_success_stale


def test_both_facts_survive_into_the_rendered_line():
    status = classify_ingestion(
        _worker(last_tick_completed_at=_iso(_THRESHOLD + 120), last_failure_code="E"), _INTERVAL, now=_NOW,
    )

    rendered = ingestion_status_html(status)
    assert ATTEMPT_FAILED_MESSAGE in rendered
    assert "Last recorded run completed" in rendered
    assert LAST_SUCCESS_ALSO_STALE_MESSAGE in rendered


# ============================================================
# No false healthy state.
# ============================================================


def test_an_old_success_is_never_rescued_by_a_recent_row_write():
    """`updated_at` is when the row was written, not when ingestion
    succeeded."""
    status = classify_ingestion(
        _worker(last_tick_completed_at=_iso(_THRESHOLD + 60), updated_at=_iso(0)), _INTERVAL, now=_NOW,
    )

    assert status.state == "stale"


def test_classification_never_reads_a_story_publication_date():
    """Worker health must not be inferred from content: the function
    signature cannot even accept a story date, and feed rows reach it
    only as a failure count."""
    import inspect

    params = set(inspect.signature(classify_ingestion).parameters)
    assert params == {"worker_status", "interval_minutes", "failed_feeds", "now", "editorial_coverage_recorded"}
    assert failed_feed_count({"a": _feed(last_story_published_at=_iso(1))}) == failed_feed_count(
        {"a": _feed(last_story_published_at=None)},
    )


def test_an_ancient_story_date_cannot_make_a_missing_status_look_healthy():
    assert classify_ingestion(None, _INTERVAL, failed_feeds=0, now=_NOW).state == "unknown"


def test_failed_feed_count_only_counts_rows_that_exist():
    assert failed_feed_count(None) == 0
    assert failed_feed_count({}) == 0
    assert failed_feed_count({"a": _feed(), "b": _feed(last_failure_code="E")}) == 1


def test_unparseable_and_naive_timestamps_are_handled_without_raising():
    assert classify_ingestion(_worker(last_tick_completed_at="not-a-date"), _INTERVAL, now=_NOW).state == "unknown"

    naive = _worker(last_tick_completed_at=_NOW.replace(tzinfo=None).isoformat())
    assert classify_ingestion(naive, _INTERVAL, now=_NOW).state == "current"


# ============================================================
# A recent tick establishes no coverage, and no quiet period.
# ============================================================


def test_editorial_coverage_is_not_recorded_today():
    """The premise every claim below rests on: the editorial lane
    persists no per-source status at all."""
    assert EDITORIAL_COVERAGE_RECORDED is False


def test_no_state_claims_complete_source_coverage():
    statuses = [
        classify_ingestion(_worker(), _INTERVAL, now=_NOW),
        classify_ingestion(_worker(), _INTERVAL, failed_feeds=1, now=_NOW),
        classify_ingestion(_worker(last_failure_code="X"), _INTERVAL, now=_NOW),
        classify_ingestion(_worker(last_tick_completed_at=_iso(10000)), _INTERVAL, now=_NOW),
        classify_ingestion(None, _INTERVAL, now=_NOW),
    ]

    for status in statuses:
        rendered = ingestion_status_html(status).lower()
        for overclaim in ("all sources", "every source", "all feeds", "every feed", "complete coverage", "up to date"):
            assert overclaim not in rendered, (status.state, overclaim)


def test_a_recent_tick_does_not_confirm_a_quiet_feed_while_editorial_coverage_is_unrecorded():
    status = classify_ingestion(_worker(), _INTERVAL, now=_NOW)

    assert status.state == "current"
    assert not status.confirms_quiet_feed
    assert empty_feed_explanation(status) == QUIET_FEED_UNCONFIRMABLE_MESSAGE
    # "cannot be confirmed as a quiet period" is the point; an
    # affirmative "reflects a quiet period" is what must never appear.
    assert "reflects a quiet period" not in empty_feed_explanation(status)
    assert "cannot be confirmed" in empty_feed_explanation(status)


def test_no_state_reachable_today_confirms_a_quiet_feed():
    for status in (
        classify_ingestion(_worker(), _INTERVAL, now=_NOW),
        classify_ingestion(_worker(), _INTERVAL, failed_feeds=1, now=_NOW),
        classify_ingestion(_worker(last_failure_code="X"), _INTERVAL, now=_NOW),
        classify_ingestion(_worker(last_tick_completed_at=_iso(10000)), _INTERVAL, now=_NOW),
        classify_ingestion(None, _INTERVAL, now=_NOW),
    ):
        assert not status.confirms_quiet_feed, status.state


def test_a_quiet_period_becomes_confirmable_only_once_coverage_is_recorded():
    """Forward-compatible, and the only branch that may ever say "quiet
    period": a current state AND per-source results for every lane."""
    recorded_current = classify_ingestion(_worker(), _INTERVAL, now=_NOW, editorial_coverage_recorded=True)
    recorded_stale = classify_ingestion(
        _worker(last_tick_completed_at=_iso(10000)), _INTERVAL, now=_NOW, editorial_coverage_recorded=True,
    )

    assert recorded_current.confirms_quiet_feed
    assert empty_feed_explanation(recorded_current) == QUIET_FEED_CONFIRMED_MESSAGE
    assert not recorded_stale.confirms_quiet_feed
    assert "may be a gap" in empty_feed_explanation(recorded_stale)


def test_a_positive_state_always_carries_the_unrecorded_coverage_caveat():
    for status in (
        classify_ingestion(_worker(), _INTERVAL, now=_NOW),
        classify_ingestion(_worker(), _INTERVAL, failed_feeds=1, now=_NOW),
    ):
        assert MARKET_NEWS_COVERAGE_UNKNOWN_NOTE in ingestion_status_html(status)


# ============================================================
# Wording discipline.
# ============================================================


def test_no_state_claims_that_new_content_arrived():
    """A recorded run is not arrival. Nothing in any rendered line may
    suggest the feed received something."""
    for status in (
        classify_ingestion(_worker(), _INTERVAL, now=_NOW),
        classify_ingestion(_worker(), _INTERVAL, failed_feeds=1, now=_NOW),
        classify_ingestion(_worker(last_tick_completed_at=_iso(10000)), _INTERVAL, now=_NOW),
    ):
        rendered = ingestion_status_html(status).lower()
        for arrival_claim in ("recently ingested", "newly", "arrived", "fresh", "new item", "new content"):
            assert arrival_claim not in rendered, (status.state, arrival_claim)
        assert "run" in rendered


def test_a_reported_failure_is_not_described_as_a_proven_non_completion():
    """`last_failure_code` records a reported failure. Nothing stored
    establishes that the tick failed to finish, so no message may say
    so."""
    for status in (
        classify_ingestion(_worker(last_failure_code="E"), _INTERVAL, now=_NOW),
        classify_ingestion(_worker(last_tick_completed_at=None, last_failure_code="E"), _INTERVAL, now=_NOW),
    ):
        rendered = ingestion_status_html(status).lower()
        assert "reported a failure" in rendered
        for unproven in ("did not finish", "never finished", "crashed", "died", "incomplete run", "aborted"):
            assert unproven not in rendered, (status.state, unproven)


def test_rendered_line_never_leaks_a_failure_code_or_technical_detail():
    status = classify_ingestion(_worker(last_failure_code="HTTP_503_BACKEND_UNAVAILABLE"), _INTERVAL, now=_NOW)

    rendered = ingestion_status_html(status)
    assert "HTTP_503_BACKEND_UNAVAILABLE" not in rendered
    assert "503" not in rendered


def test_no_message_claims_continuous_or_automatic_updating():
    """Mirrors the prohibited-words sweep design/DECISIONS.md records for
    the Radar freshness line (Phase F1), over every message this module
    can produce."""
    prohibited = ("live", "real-time", "automatic", "continuous", "autonomous", "scheduled", "updating")
    for status in (
        classify_ingestion(_worker(), _INTERVAL, now=_NOW),
        classify_ingestion(_worker(), _INTERVAL, failed_feeds=1, now=_NOW),
        classify_ingestion(_worker(last_failure_code="X"), _INTERVAL, now=_NOW),
        classify_ingestion(_worker(last_tick_completed_at=_iso(10000), last_failure_code="X"), _INTERVAL, now=_NOW),
        classify_ingestion(_worker(last_tick_completed_at=None, last_failure_code="X"), _INTERVAL, now=_NOW),
        classify_ingestion(_worker(last_tick_completed_at=_iso(10000)), _INTERVAL, now=_NOW),
        classify_ingestion(None, _INTERVAL, now=_NOW),
    ):
        rendered = ingestion_status_html(status).lower()
        for word in prohibited:
            assert word not in rendered, (status.state, word)


def test_each_state_opens_with_distinct_wording_so_color_is_never_the_signal():
    """No separate state label exists, so each message must be
    distinguishable from the others by its own opening words."""
    openings = []
    for status in (
        classify_ingestion(_worker(), _INTERVAL, now=_NOW),
        classify_ingestion(_worker(), _INTERVAL, failed_feeds=1, now=_NOW),
        classify_ingestion(_worker(last_failure_code="X"), _INTERVAL, now=_NOW),
        classify_ingestion(_worker(last_tick_completed_at=_iso(10000)), _INTERVAL, now=_NOW),
        classify_ingestion(None, _INTERVAL, now=_NOW),
    ):
        openings.append(status.message.split(".")[0].strip().lower())

    assert len(set(openings)) == len(openings), openings


def test_the_rendered_line_is_the_message_itself_plus_the_coverage_caveat_only():
    """The renderer adds the unrecorded-coverage caveat to the two
    states that report a recent run, and nothing else to any state."""
    current = classify_ingestion(_worker(), _INTERVAL, now=_NOW)
    unknown = classify_ingestion(None, _INTERVAL, now=_NOW)

    assert ingestion_status_html(current).count(current.message) == 1
    assert MARKET_NEWS_COVERAGE_UNKNOWN_NOTE in ingestion_status_html(current)
    assert unknown.message in ingestion_status_html(unknown)
    assert MARKET_NEWS_COVERAGE_UNKNOWN_NOTE not in ingestion_status_html(unknown)


# ============================================================
# Cache contract.
# ============================================================


def test_settings_is_excluded_from_the_cache_key_by_the_underscore_convention():
    """Streamlit hashes every argument whose name does not start with an
    underscore. Settings can carry a DSN, so it must be the excluded
    one, and the identity must come from the two plain arguments."""
    import inspect

    params = list(inspect.signature(daily_news_status._persisted_facts_cached).parameters)

    assert params[-1].startswith("_")
    assert [p for p in params if not p.startswith("_")] == ["backend", "database_digest"]


def test_the_cache_identity_covers_every_dependency_that_can_vary(tmp_path):
    """Traced dependencies of get_daily_news_scan_status_repository():
    db_backend, state_db_path (sqlite), state_db_url (postgres). Each one
    must change the cache identity; cache_dir, which that accessor never
    reads, must not."""
    base = Settings(cache_dir=tmp_path, db_backend="postgres", state_db_url="postgresql://u:p@host-a/db")
    identity = daily_news_status._database_identity

    assert identity(base) != identity(replace(base, db_backend="sqlite", state_db_path=str(tmp_path / "a.db")))
    assert identity(base) != identity(replace(base, state_db_url="postgresql://u:p@host-b/db"))
    assert identity(replace(base, db_backend="sqlite", state_db_path="/tmp/a.db")) != identity(
        replace(base, db_backend="sqlite", state_db_path="/tmp/b.db"),
    )
    # cache_dir is not a dependency of this read at all.
    assert identity(base) == identity(replace(base, cache_dir=tmp_path / "elsewhere"))


def test_two_different_databases_never_share_a_cached_entry(tmp_path):
    _clear_cache()
    a = Settings(cache_dir=tmp_path, db_backend="postgres", state_db_url="postgresql://u:p@host-a/db")
    b = Settings(cache_dir=tmp_path, db_backend="postgres", state_db_url="postgresql://u:p@host-b/db")

    def _facts_for(settings):
        marker = "A" if "host-a" in (settings.state_db_url or "") else "B"
        return PersistedIngestionFacts(worker_status=_worker(last_failure_code=marker), failed_feeds=0)

    with patch.object(daily_news_status, "read_persisted_facts", side_effect=_facts_for):
        first = daily_news_status._persisted_facts_cached(*daily_news_status._database_identity(a), a)
        second = daily_news_status._persisted_facts_cached(*daily_news_status._database_identity(b), b)

    assert first.worker_status.last_failure_code == "A"
    assert second.worker_status.last_failure_code == "B"
    _clear_cache()


def test_the_same_database_is_read_once_within_the_ttl(tmp_path):
    _clear_cache()
    settings = Settings(cache_dir=tmp_path, db_backend="sqlite", state_db_path=str(tmp_path / "a.db"))
    calls = []

    def _facts(_settings):
        calls.append(1)
        return PersistedIngestionFacts(worker_status=None, failed_feeds=0)

    with patch.object(daily_news_status, "read_persisted_facts", side_effect=_facts):
        for _ in range(3):
            daily_news_status._persisted_facts_cached(*daily_news_status._database_identity(settings), settings)

    assert len(calls) == 1
    _clear_cache()


def test_the_dsn_never_appears_in_the_cache_key_or_the_output(tmp_path):
    secret = "postgresql://realuser:realpassword@internal-host:5432/proddb"
    settings = Settings(cache_dir=tmp_path, db_backend="postgres", state_db_url=secret)

    backend, digest = daily_news_status._database_identity(settings)

    for leak in ("realuser", "realpassword", "internal-host", "proddb", secret):
        assert leak not in backend
        assert leak not in digest
    assert len(digest) == 16

    rendered = ingestion_status_html(
        classify_ingestion(None, settings.daily_news_scan_interval_minutes, now=_NOW),
    )
    for leak in ("realuser", "realpassword", "internal-host", "proddb"):
        assert leak not in rendered


def test_an_unconfigured_backend_still_produces_a_stable_identity(tmp_path):
    backend, digest = daily_news_status._database_identity(Settings(cache_dir=tmp_path))

    assert backend == "json"
    assert digest == "unconfigured"


def test_what_is_cached_is_the_rows_not_the_classification(tmp_path):
    """So a cached entry can never hold a state past its own staleness
    boundary: the age is recomputed against the caller's clock on every
    render. The same cached row classifies current now and stale later."""
    _clear_cache()
    settings = Settings(cache_dir=tmp_path, db_backend="sqlite", state_db_path=str(tmp_path / "a.db"))
    facts = PersistedIngestionFacts(worker_status=_worker(last_tick_completed_at=_iso(0)), failed_feeds=0)

    with patch.object(daily_news_status, "read_persisted_facts", return_value=facts):
        now_status = current_ingestion_status(settings, now=_NOW)
        later_status = current_ingestion_status(settings, now=_NOW + timedelta(minutes=_THRESHOLD + 1))

    assert now_status.state == "current"
    assert later_status.state == "stale"
    _clear_cache()


def test_the_cache_ttl_is_far_below_the_staleness_threshold_it_feeds():
    """The only thing the TTL bounds is how old the underlying rows may
    be: 60 seconds against a 90-minute threshold, so a state can be at
    most that late to change."""
    assert daily_news_status._STATUS_CACHE_TTL_SECONDS == 60
    assert daily_news_status._STATUS_CACHE_TTL_SECONDS / 60 < stale_threshold_minutes(_INTERVAL) / 10


def test_the_status_read_is_not_user_or_session_scoped():
    """A cross-session st.cache_data cache is only correct because this
    data is deployment-wide. Behavioural: Settings built twice from the
    same environment are equal, so nothing per-viewer can enter the
    identity; and the component reaches for no per-session Streamlit
    state."""
    import ast

    from src.config.settings import get_settings

    assert get_settings() == get_settings()

    component = (_REPO_ROOT / "src" / "ui" / "components" / "daily_news_status.py").read_text(encoding="utf-8")
    names = {node.attr for node in ast.walk(ast.parse(component)) if isinstance(node, ast.Attribute)}
    assert "session_state" not in names
    assert "experimental_user" not in names


# ============================================================
# Reading the persisted status — every failure resolves to unknown.
# ============================================================


def test_json_backend_resolves_to_unknown_rather_than_raising(tmp_path):
    facts = read_persisted_facts(Settings(cache_dir=tmp_path))

    assert facts.worker_status is None
    assert classify_ingestion(facts.worker_status, None, now=_NOW).state == "unknown"


def test_a_backend_configuration_error_resolves_to_unknown(tmp_path):
    with patch.object(
        daily_news_status.daily_news_backend,
        "get_daily_news_scan_status_repository",
        side_effect=BackendConfigurationError("no"),
    ):
        assert read_persisted_facts(Settings(cache_dir=tmp_path)).worker_status is None


def test_an_unexpected_read_error_resolves_to_unknown_without_leaking_it(tmp_path):
    with patch.object(
        daily_news_status.daily_news_backend,
        "get_daily_news_scan_status_repository",
        side_effect=RuntimeError("dbname=secret host=internal"),
    ):
        facts = read_persisted_facts(Settings(cache_dir=tmp_path))

    rendered = ingestion_status_html(classify_ingestion(facts.worker_status, None, now=_NOW))
    assert UNKNOWN_MESSAGE in rendered
    assert "secret" not in rendered
    assert "internal" not in rendered


def test_a_real_repository_read_is_classified_from_its_rows(tmp_path):
    class _Repo:
        def get_worker_status(self):
            return _worker(last_failure_code="FETCH_FAILED")

        def get_all_feed_statuses(self):
            return {"NVIDIA": _feed(last_failure_code="X")}

    with patch.object(
        daily_news_status.daily_news_backend, "get_daily_news_scan_status_repository", return_value=_Repo(),
    ):
        facts = read_persisted_facts(Settings(cache_dir=tmp_path))

    assert facts.failed_feeds == 1
    assert classify_ingestion(facts.worker_status, _INTERVAL, facts.failed_feeds, now=_NOW).state == "attempt_failed"


def test_a_cache_failure_still_produces_a_line(tmp_path):
    with patch.object(daily_news_status, "_persisted_facts_cached", side_effect=RuntimeError("no runtime")):
        assert current_ingestion_status(Settings(cache_dir=tmp_path)).state == "unknown"


# ============================================================
# The page: ingestion status, and views emptied by a filter.
# ============================================================


def _page_text(at: AppTest) -> str:
    return " ".join(m.value for m in at.main.get("markdown") if not m.value.startswith("<style>"))


def _run_page(tmp_path, status: DailyNewsIngestionStatus | None = None, tier: str | None = None) -> AppTest:
    patches = [patch("src.ui.pages.daily_news.get_settings", return_value=Settings(cache_dir=tmp_path))]
    if status is not None:
        patches.append(patch.object(daily_news_status, "current_ingestion_status", return_value=status))
    for p in patches:
        p.start()
    try:
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        if tier is not None:
            at.query_params["tier"] = tier
        at.run()
    finally:
        for p in reversed(patches):
            p.stop()
    assert not at.exception
    return at


def test_the_page_renders_the_status_line_on_an_empty_feed(tmp_path):
    stale = classify_ingestion(_worker(last_tick_completed_at=_iso(10000)), _INTERVAL, now=_NOW)

    assert "No recent run recorded" in _page_text(_run_page(tmp_path, stale))


def test_an_empty_feed_is_never_presented_as_a_confirmed_quiet_period(tmp_path):
    current = classify_ingestion(_worker(), _INTERVAL, now=_NOW)

    text = _page_text(_run_page(tmp_path, current))

    assert "Last recorded run completed" in text
    assert QUIET_FEED_UNCONFIRMABLE_MESSAGE in text
    assert "reflects a quiet period" not in text


def test_the_visible_result_message_and_the_ingestion_message_stay_separate(tmp_path):
    """Two statements about two different things: what matched the
    selection and window, and what ingestion recorded."""
    current = classify_ingestion(_worker(), _INTERVAL, now=_NOW)

    markdowns = [m.value for m in _run_page(tmp_path, current).main.get("markdown")]
    visible = [m for m in markdowns if "No recent company updates in the last 7 days." in m]
    ingestion = [m for m in markdowns if QUIET_FEED_UNCONFIRMABLE_MESSAGE in m]

    assert len(visible) == 1
    assert len(ingestion) == 1
    assert visible[0] != ingestion[0]
    assert QUIET_FEED_UNCONFIRMABLE_MESSAGE not in visible[0]
    assert "No recent company updates" not in ingestion[0]


def test_the_page_never_shows_an_unknown_status_as_ingested(tmp_path):
    text = _page_text(_run_page(tmp_path, DailyNewsIngestionStatus("unknown", UNKNOWN_MESSAGE)))

    assert UNKNOWN_MESSAGE in text
    assert "Last recorded run completed" not in text


def test_a_view_emptied_by_the_tier_filter_keeps_its_own_filter_message(tmp_path):
    """A non-empty feed with nothing in the selected tier is a filter
    outcome, not an ingestion outcome: the filter message stands on its
    own, and the empty-feed ingestion explanation belongs to the
    empty-feed path only."""
    from src.data_access.daily_news import daily_news_store
    from src.models.daily_news_models import NewsMaterialityTier
    from tests.test_daily_news_page import _story

    daily_news_store.upsert_new_stories(tmp_path, [
        _story(materiality_tier=NewsMaterialityTier.HIGH_SIGNAL, materiality_reasons=("quantified_anchor:x",)),
    ])
    current = classify_ingestion(_worker(), _INTERVAL, now=_NOW)

    markdowns = [m.value for m in _run_page(tmp_path, current, tier="watchlist").main.get("markdown")]
    filter_messages = [m for m in markdowns if _NO_WATCHLIST_EMPTY_STATE in m]

    assert len(filter_messages) == 1
    assert QUIET_FEED_UNCONFIRMABLE_MESSAGE not in filter_messages[0]
    assert "Last recorded run completed" not in filter_messages[0]
    # The ingestion line is still present once, at the top...
    assert len([m for m in markdowns if "Last recorded run completed" in m]) == 1
    # ...and the empty-feed explanation is not rendered for a filter miss.
    assert QUIET_FEED_UNCONFIRMABLE_MESSAGE not in " ".join(markdowns)


def test_a_filtered_high_signal_view_behaves_the_same_way(tmp_path):
    from src.data_access.daily_news import daily_news_store
    from src.models.daily_news_models import NewsMaterialityTier
    from tests.test_daily_news_page import _story

    daily_news_store.upsert_new_stories(tmp_path, [
        _story(materiality_tier=NewsMaterialityTier.WATCHLIST, materiality_reasons=("on_taxonomy_no_anchor:x",)),
    ])
    current = classify_ingestion(_worker(), _INTERVAL, now=_NOW)

    markdowns = [m.value for m in _run_page(tmp_path, current, tier="high-signals").main.get("markdown")]
    filter_messages = [m for m in markdowns if _NO_HIGH_SIGNAL_EMPTY_STATE in m]

    assert len(filter_messages) == 1
    assert QUIET_FEED_UNCONFIRMABLE_MESSAGE not in filter_messages[0]
    assert len([m for m in markdowns if "Last recorded run completed" in m]) == 1


def test_the_signals_page_still_reads_stories_through_its_existing_path(tmp_path):
    """Guard against the status line having changed how the feed itself is
    selected: reuses test_daily_news_page.py's own story factory, so a
    seeded story still renders exactly as that suite already proves."""
    from src.data_access.daily_news import daily_news_store
    from tests.test_daily_news_page import _story

    daily_news_store.upsert_new_stories(tmp_path, [_story()])

    assert "NVIDIA Announces Financial Results" in _page_text(_run_page(tmp_path))


# ============================================================
# No ingestion, admission, ranking, or publication behavior changed.
# ============================================================


def test_the_status_modules_never_import_a_pipeline_worker_or_store():
    import ast

    forbidden = (
        "daily_news_pipeline", "editorial_pipeline", "rss_atom_client", "daily_news_store",
        "editorial_story_store", "daily_news_worker", "editorial_admission", "editorial_matching",
        "materiality_classification", "summary_grounding", "dedup", "requests", "feedparser",
    )
    for relative in (
        "src/logic/daily_news_freshness.py",
        "src/ui/components/daily_news_status.py",
    ):
        tree = ast.parse((_REPO_ROOT / relative).read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom) and node.module:
                module = node.module
            elif isinstance(node, ast.Import):
                module = ",".join(alias.name for alias in node.names)
            if not module:
                continue
            for name in forbidden:
                assert name not in module, (relative, module, name)


def test_the_logic_module_stays_streamlit_free():
    import ast

    tree = ast.parse((_REPO_ROOT / "src/logic/daily_news_freshness.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all("streamlit" not in alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            assert "streamlit" not in (node.module or "")


def test_the_status_read_never_calls_a_write_method(tmp_path):
    """Behavioural: a repository double that raises on every write. The
    worker is the only writer of these tables."""
    calls: list[str] = []

    class _ReadOnlyOnly:
        def get_worker_status(self):
            calls.append("get_worker_status")
            return _worker()

        def get_all_feed_statuses(self):
            calls.append("get_all_feed_statuses")
            return {}

        def __getattr__(self, name):  # every other attribute, including writes
            raise AssertionError(f"status read attempted {name}()")

    with patch.object(
        daily_news_status.daily_news_backend,
        "get_daily_news_scan_status_repository",
        return_value=_ReadOnlyOnly(),
    ):
        facts = read_persisted_facts(Settings(cache_dir=tmp_path))

    assert facts.worker_status is not None
    assert calls == ["get_worker_status", "get_all_feed_statuses"]
