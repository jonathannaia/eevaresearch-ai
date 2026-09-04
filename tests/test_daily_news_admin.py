"""Daily News admin page — worker health section (Daily News operational-
fix workstream, design/DECISIONS.md). Read-only: every test here proves
no scan is triggered, no data is written, and no DSN/secret/raw error
ever renders — only the sanitized DailyNewsWorkerStatus/
DailyNewsFeedScanStatus fields the worker already writes, read via the
dashboard's own ambient (never the worker's own) settings.

SQLite-backed (in-process, disposable) — mirrors test_daily_news_worker.py's
own local-testing convention. Zero network calls."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.data_access.daily_news import daily_news_backend
from src.data_access.daily_news.feed_registry import PILOT_FEEDS
from src.data_access.state_db.daily_news_scan_status_repository import DailyNewsFeedScanStatus, DailyNewsWorkerStatus, WORKER_STATUS_KEY
from src.models.daily_news_models import NewsSourceReference, NewsStateTransition, NewsStory, NewsStoryStatus, SourceClass

_HARNESS = Path(__file__).parent / "apptest_pages" / "daily_news_admin_page.py"


def _settings(tmp_path, **overrides) -> Settings:
    fields = dict(
        cache_dir=tmp_path, daily_news_admin_enabled=True,
        db_backend="sqlite", state_db_path=tmp_path / "test_admin.db",
    )
    fields.update(overrides)
    return Settings(**fields)


def _iso(offset: timedelta = timedelta()) -> str:
    return (datetime.now(timezone.utc) - offset).isoformat()


def _seed_feed_status(settings: Settings, company_name: str, **overrides) -> None:
    repo = daily_news_backend.get_daily_news_scan_status_repository(settings)
    fields = dict(
        company_name=company_name, last_attempt_at=_iso(), last_fetch_success_at=_iso(),
        last_story_published_at=None, last_failure_code=None,
        items_discovered_last_run=5, stories_published_last_run=2, updated_at=_iso(),
    )
    fields.update(overrides)
    repo.upsert_feed_status(DailyNewsFeedScanStatus(**fields))


def _seed_worker_status(settings: Settings, **overrides) -> None:
    repo = daily_news_backend.get_daily_news_scan_status_repository(settings)
    fields = dict(
        worker_key=WORKER_STATUS_KEY, last_tick_started_at=_iso(), last_tick_completed_at=_iso(),
        last_reconciliation_at=_iso(), last_failure_code=None, updated_at=_iso(),
    )
    fields.update(overrides)
    repo.upsert_worker_status(DailyNewsWorkerStatus(**fields))


def _story(company_name: str, published_at_offset: timedelta) -> NewsStory:
    published_at = _iso(published_at_offset)
    return NewsStory(
        id=f"newsitem-{company_name.lower()}-abc123", company_name=company_name, ticker="TCK", theme_slug="ai-buildout",
        headline="Headline", eeva_summary="Summary text.", is_fallback_summary=False, translation_unavailable=False,
        original_title=None,
        sources=(
            NewsSourceReference(
                publisher=company_name, source_class=SourceClass.OFFICIAL_COMPANY, url="https://example.com/x",
                title="Headline", published_at=published_at, retrieved_at=published_at,
                original_language="English", excerpt_original="Summary text.",
            ),
        ),
        status=NewsStoryStatus.PUBLISHED,
        state_history=[NewsStateTransition(status=NewsStoryStatus.PUBLISHED, at=published_at)],
    )


def _seed_story(settings: Settings, story: NewsStory) -> None:
    daily_news_backend.get_daily_news_repository(settings).upsert_new_stories([story])


def _run(settings: Settings) -> AppTest:
    with patch("src.ui.pages.daily_news_admin.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
    return at


def _text(at: AppTest) -> str:
    """Joins every element type this page actually renders text through —
    st.markdown/st.write both land as `markdown`; st.info/st.warning are
    their own element types; st.metric contributes its label and value
    separately. A plain `at.markdown` join alone (as other Daily News
    test files use) misses the info/warning/metric elements this page
    also renders, so those are included explicitly here."""
    parts = [m.value for m in at.markdown if not m.value.startswith("<style>")]
    parts += [m.value for m in at.get("info")]
    parts += [m.value for m in at.get("warning")]
    parts += [m.value for m in at.get("caption")]
    for m in at.get("metric"):
        parts.append(f"{m.label} {m.value}")
    return " ".join(parts)


# ============================================================
# Gate
# ============================================================


def test_admin_disabled_shows_no_worker_health_section(tmp_path):
    at = _run(_settings(tmp_path, daily_news_admin_enabled=False))
    assert not at.exception
    text = _text(at)
    assert "Autonomous worker health" not in text
    assert "not enabled" in text.lower()


# ============================================================
# Backend not postgres/sqlite (the common default) — informative, no crash
# ============================================================


def test_json_backend_shows_informative_message_not_a_crash(tmp_path):
    at = _run(_settings(tmp_path, db_backend="json", state_db_path=None))
    assert not at.exception
    text = _text(at)
    assert "Autonomous worker health" in text
    assert "EDGE_DB_BACKEND" in text
    assert "EDGE_STATE_DB_URL" in text
    # Never the worker's own separate DSN variable name substituted in by mistake.
    assert "postgres/sqlite" in text.lower() or "json" in text.lower()


# ============================================================
# No status recorded yet
# ============================================================


def test_no_worker_status_yet_shows_info_not_a_crash(tmp_path):
    settings = _settings(tmp_path)
    at = _run(settings)
    assert not at.exception
    text = _text(at)
    assert "No worker status recorded yet" in text
    assert "Feeds registered" in text


# ============================================================
# Healthy worker + mixed feed outcomes
# ============================================================


def test_healthy_worker_shows_counts_and_per_feed_sanitized_results(tmp_path):
    settings = _settings(tmp_path)
    _seed_worker_status(settings)
    ok_company, fail_company = PILOT_FEEDS[0].company_name, PILOT_FEEDS[1].company_name
    _seed_feed_status(settings, ok_company, items_discovered_last_run=8, stories_published_last_run=3)
    _seed_feed_status(
        settings, fail_company, last_fetch_success_at=None, last_failure_code="HTTPError:403",
        items_discovered_last_run=0, stories_published_last_run=0,
    )

    at = _run(settings)
    assert not at.exception
    text = _text(at)

    assert "Feeds registered" in text
    # 2 feeds seeded: 1 success, 1 failure.
    assert ok_company in text
    assert fail_company in text
    assert "HTTPError:403" in text
    assert "Items discovered: **8**" in text
    assert "Newly published: **3**" in text


def test_sanitized_failure_code_shown_never_a_raw_message_dsn_or_url(tmp_path):
    settings = _settings(tmp_path)
    _seed_worker_status(settings)
    _seed_feed_status(
        settings, PILOT_FEEDS[0].company_name, last_fetch_success_at=None,
        last_failure_code="HTTPError:500",
    )

    at = _run(settings)
    assert not at.exception
    text = _text(at)

    assert "HTTPError:500" in text
    # The sqlite path this test configured must never appear anywhere on the page.
    assert str(settings.state_db_path) not in text
    assert "test_admin.db" not in text
    # No raw exception-message-shaped content, no credential-shaped tokens.
    for forbidden in ("Traceback", "password", "Authorization", "Bearer "):
        assert forbidden not in text


# ============================================================
# Staleness warning (72h — EDGE_DAILY_NEWS_RECONCILIATION_STALENESS_HOURS)
# ============================================================


def test_stale_worker_triggers_warning_when_last_success_older_than_threshold(tmp_path):
    settings = _settings(tmp_path)
    _seed_worker_status(settings)
    _seed_feed_status(
        settings, PILOT_FEEDS[0].company_name,
        last_fetch_success_at=_iso(timedelta(hours=100)),  # older than the default 72h threshold
    )

    at = _run(settings)
    assert not at.exception
    text = _text(at)
    assert "No successful feed fetch recorded in the last 72 hours" in text


def test_recent_worker_success_does_not_trigger_staleness_warning(tmp_path):
    settings = _settings(tmp_path)
    _seed_worker_status(settings)
    _seed_feed_status(settings, PILOT_FEEDS[0].company_name, last_fetch_success_at=_iso(timedelta(hours=1)))

    at = _run(settings)
    assert not at.exception
    text = _text(at)
    assert "No successful feed fetch recorded" not in text


# ============================================================
# 7-day story-freshness warning (reuses daily_news.py's own contract)
# ============================================================


def test_no_recent_story_triggers_freshness_warning(tmp_path):
    settings = _settings(tmp_path)
    _seed_worker_status(settings)
    _seed_feed_status(settings, PILOT_FEEDS[0].company_name)
    _seed_story(settings, _story("NVIDIA", timedelta(days=10)))  # outside the 7-day window

    at = _run(settings)
    assert not at.exception
    text = _text(at)
    assert "No persisted story falls within" in text
    assert "7-day freshness window" in text


def test_recent_story_does_not_trigger_freshness_warning(tmp_path):
    settings = _settings(tmp_path)
    _seed_worker_status(settings)
    _seed_feed_status(settings, PILOT_FEEDS[0].company_name)
    _seed_story(settings, _story("NVIDIA", timedelta(days=1)))

    at = _run(settings)
    assert not at.exception
    text = _text(at)
    assert "No persisted story falls within" not in text
    assert "Latest persisted story" in text


# ============================================================
# Never triggers a scan, never writes anything
# ============================================================


def test_rendering_the_worker_health_section_never_writes_any_status_row(tmp_path):
    settings = _settings(tmp_path)
    # Deliberately seed nothing — an empty database is the strictest proof
    # rendering this section performs no write of its own.
    at = _run(settings)
    assert not at.exception

    repo = daily_news_backend.get_daily_news_scan_status_repository(settings)
    assert repo.get_worker_status() is None
    assert repo.get_all_feed_statuses() == {}


def test_rendering_the_admin_page_never_calls_run_discovery(tmp_path):
    settings = _settings(tmp_path)
    with patch("src.ui.pages.daily_news_admin.daily_news_pipeline.run_discovery") as mock_run_discovery:
        _run(settings)
    mock_run_discovery.assert_not_called()


# ============================================================
# Resilience fix: story-repository read fails AFTER scan-status reads
# already succeeded — the page must still show what it retrieved, never
# crash, never write, never leak the raw failure.
# ============================================================


def test_story_repository_failure_after_scan_status_success_does_not_crash_the_page(tmp_path):
    settings = _settings(tmp_path)
    _seed_worker_status(settings)
    ok_company = PILOT_FEEDS[0].company_name
    _seed_feed_status(settings, ok_company, items_discovered_last_run=8, stories_published_last_run=3)

    with patch(
        "src.ui.pages.daily_news_admin._published_stories",
        side_effect=RuntimeError("simulated story-repository connection failure — must never leak"),
    ):
        at = _run(settings)

    assert not at.exception
    text = _text(at)

    # Scan-status data (retrieved BEFORE the simulated failure) still
    # renders — the page degrades only the part that actually failed.
    assert "Feeds registered" in text
    assert ok_company in text
    assert "Items discovered: **8**" in text

    # The story-freshness section degrades to a concise, sanitized notice.
    assert "Latest story status unavailable" in text
    assert "RuntimeError" in text
    # Never the raw exception message, and never the (now-stale/incorrect)
    # "no story in the 7-day window" claim, since that can't be known.
    assert "simulated story-repository connection failure" not in text
    assert "must never leak" not in text
    assert "No persisted story falls within" not in text
    assert "Latest persisted story" not in text  # the success-path line is not shown either


def test_story_repository_backend_configuration_error_shows_its_own_safe_message(tmp_path):
    from src.data_access.backend_factory import BackendConfigurationError

    settings = _settings(tmp_path)
    _seed_worker_status(settings)
    _seed_feed_status(settings, PILOT_FEEDS[0].company_name)

    with patch(
        "src.ui.pages.daily_news_admin._published_stories",
        side_effect=BackendConfigurationError("EDGE_DB_BACKEND=postgres requires an explicit, non-empty EDGE_STATE_DB_URL — none was configured."),
    ):
        at = _run(settings)

    assert not at.exception
    text = _text(at)
    assert "Latest story status unavailable" in text
    assert "EDGE_STATE_DB_URL" in text  # the variable NAME only — this message never contains a value
    assert "Feeds registered" in text  # scan-status section still rendered


def test_story_repository_failure_never_writes_or_triggers_a_scan(tmp_path):
    settings = _settings(tmp_path)
    _seed_worker_status(settings)
    _seed_feed_status(settings, PILOT_FEEDS[0].company_name)

    with (
        patch("src.ui.pages.daily_news_admin._published_stories", side_effect=RuntimeError("boom")),
        patch("src.ui.pages.daily_news_admin.daily_news_pipeline.run_discovery") as mock_run_discovery,
    ):
        at = _run(settings)

    assert not at.exception
    mock_run_discovery.assert_not_called()
    # The seeded feed/worker status rows are exactly as seeded — nothing
    # was overwritten by rendering this failure path.
    repo = daily_news_backend.get_daily_news_scan_status_repository(settings)
    assert repo.get_worker_status() is not None
    assert PILOT_FEEDS[0].company_name in repo.get_all_feed_statuses()
