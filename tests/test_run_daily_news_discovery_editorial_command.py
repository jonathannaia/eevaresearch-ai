"""Editorial Daily News autonomy (design/DECISIONS.md) — narrow behavior
regression test for scripts/run_daily_news_discovery.py's own
`--editorial-only` command, which this workstream deliberately leaves
completely unmodified (zero lines of that file changed). This is not a
source-code/byte-identity check — it proves the manual command still
runs the real editorial_pipeline against a real (test) repository and
still routes correctly by flag, so a future change elsewhere (e.g. to
editorial_pipeline.py's own signature) would be caught here rather than
only by the worker's own tests."""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from scripts import run_daily_news_discovery
from src.config.settings import Settings
from src.data_access.daily_news import daily_news_backend, daily_news_pipeline, editorial_pipeline, rss_atom_client
from src.data_access.daily_news.rss_atom_client import FeedFetchResult, RawFeedEntry

_SPACEFORCE_URL = "https://www.spaceforce.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=1060&max=10"


def _settings(tmp_path) -> Settings:
    return Settings(db_backend="json", cache_dir=tmp_path)


def _mock_fetch(entries_by_url: dict[str, FeedFetchResult], monkeypatch) -> None:
    def _fake_fetch_entries(feed_url: str) -> FeedFetchResult:
        return entries_by_url.get(feed_url, FeedFetchResult(entries=(), failure_code=None))

    monkeypatch.setattr(rss_atom_client, "fetch_entries", _fake_fetch_entries)
    monkeypatch.setattr(daily_news_pipeline.rss_atom_client, "fetch_entries", _fake_fetch_entries)
    monkeypatch.setattr(editorial_pipeline.rss_atom_client, "fetch_entries", _fake_fetch_entries)


def test_editorial_only_flag_runs_editorial_discovery_and_persists_a_real_story(tmp_path, monkeypatch, capsys):
    # P0.3 (Signals admission precision fix): Space Force items are now
    # always routed through assess_admission(), so this CLI-routing/
    # persistence smoke test uses a substantive, clearly admissible
    # fixture (a named funded launch contract) rather than the now-
    # rejected evidence-free DARC-location announcement — see
    # tests/test_daily_news_worker.py's own dedicated Space Force
    # admission-behavior coverage for the rejection case.
    settings = _settings(tmp_path)
    monkeypatch.setattr(run_daily_news_discovery, "get_settings", lambda: settings)
    entry = RawFeedEntry(
        title="U.S. Space Force Awards $400 Million Launch Contract for National Security Satellite "
              "Constellation Mission",
        link="https://www.spaceforce.mil/News/Article-Display/Article/9900002/launch-contract/",
        published_at=datetime.now(timezone.utc).isoformat(),
        summary=(
            "The U.S. Space Force awarded a $400 million launch contract to support a national security "
            "satellite constellation mission, funding a dedicated launch vehicle and ground segment "
            "integration work."
        ),
        image_url=None, image_alt=None,
    )
    _mock_fetch({_SPACEFORCE_URL: FeedFetchResult(entries=(entry,), failure_code=None)}, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["run_daily_news_discovery", "--editorial-only"])

    exit_code = run_daily_news_discovery.main()

    assert exit_code == 0
    output = capsys.readouterr().out
    lines = output.splitlines()
    assert lines[0].startswith("Editorial Daily News discovery —")
    # The issuer header shares "Daily News discovery —" as a substring
    # of the editorial one ("Editorial Daily News discovery —"), so the
    # real proof is that the *issuer-only* header line never appears on
    # its own, not a naive substring check.
    assert not any(line.startswith("Daily News discovery —") for line in lines)

    editorial_repository = daily_news_backend.get_editorial_story_repository(settings)
    stories = editorial_repository.load_stories()
    assert len(stories) == 1
    assert next(iter(stories.values())).source_feed_id == "spaceforce-news-rss"


def test_no_flag_still_runs_issuer_discovery_not_editorial(tmp_path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    monkeypatch.setattr(run_daily_news_discovery, "get_settings", lambda: settings)
    _mock_fetch({}, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["run_daily_news_discovery"])

    exit_code = run_daily_news_discovery.main()

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Daily News discovery —" in output
    assert "Editorial Daily News discovery —" not in output
