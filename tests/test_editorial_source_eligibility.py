"""Lane B source-lifecycle enforcement — source_registry.
is_eligible_for_polling()/eligible_for_polling() and the
run_editorial_discovery() gate that applies them before any fetch.

Mocked HTTP via monkeypatching rss_atom_client.fetch_entries, zero real
network calls, mirroring tests/test_editorial_pipeline.py's own
convention. Every feed is synthetic; nothing here reads a live source.

The documented semantics under test live in design/DAILY_NEWS_SOURCE_
ADMISSION_POLICY.md's own source-health/review lifecycle:
    pending_review -> verified -> (degraded <-> verified) -> failing -> retired
with DEGRADED "still polled", FAILING "excluded from active polling",
and RETIRED "never polled again, but the entry stays in the registry
(never deleted) for audit/provenance history".
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from src.data_access.daily_news import editorial_pipeline, feed_registry, source_registry
from src.data_access.daily_news.rss_atom_client import FeedFetchResult, RawFeedEntry
from src.data_access.daily_news.source_registry import (
    EDITORIAL_SOURCE_REGISTRY,
    RUNTIME_SOURCE_REGISTRY,
    DailyNewsSourceEntry,
    SourceCategory,
    SourceFormat,
    SourceHealthState,
    eligible_for_polling,
    is_eligible_for_polling,
)

_LICENSING = "Independent journalism test fixture."


def _source(
    source_id: str = "eligibility-test-rss",
    *,
    enabled: bool = True,
    health_state: SourceHealthState = SourceHealthState.VERIFIED,
    source_format: SourceFormat = SourceFormat.RSS_ATOM,
) -> DailyNewsSourceEntry:
    return DailyNewsSourceEntry(
        source_id=source_id, category=SourceCategory.INDEPENDENT_NEWS, format=source_format,
        canonical_url=f"https://www.cnbc.com/feeds/{source_id}.xml",
        domains=("www.cnbc.com",), jurisdiction="United States", enabled=enabled,
        health_state=health_state, attribution_label="CNBC",
        licensing_classification=_LICENSING, priority=1, issuer_agnostic=True, allowlisted=True,
    )


def _fresh_entry(title: str, link: str) -> RawFeedEntry:
    published = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    return RawFeedEntry(
        title=title, link=link, published_at=published,
        summary="Oracle Corporation reports strong AI cloud demand with revenue up 42%.",
    )


@pytest.fixture
def recording_fetch(monkeypatch):
    """Records every canonical_url the pipeline actually fetches."""
    calls: list[str] = []

    def _fake_fetch_entries(feed_url: str) -> FeedFetchResult:
        calls.append(feed_url)
        return FeedFetchResult(
            entries=(_fresh_entry(
                "Oracle Corporation reports strong AI cloud demand, revenue up 42%",
                "https://www.cnbc.com/2026/10/30/oracle-ai-cloud-demand.html",
            ),),
            failure_code=None,
        )

    monkeypatch.setattr(editorial_pipeline.rss_atom_client, "fetch_entries", _fake_fetch_entries)
    return calls


# --- the predicate itself -------------------------------------------------

def test_enabled_verified_rss_source_is_eligible():
    assert is_eligible_for_polling(_source()) is True


def test_disabled_source_is_not_eligible():
    assert is_eligible_for_polling(_source(enabled=False)) is False


@pytest.mark.parametrize("health_state", [SourceHealthState.FAILING, SourceHealthState.RETIRED])
def test_health_states_excluded_by_policy_are_not_eligible(health_state):
    """FAILING and RETIRED are the two states design/DAILY_NEWS_SOURCE_
    ADMISSION_POLICY.md explicitly removes from active polling."""
    assert is_eligible_for_polling(_source(health_state=health_state)) is False


@pytest.mark.parametrize(
    "health_state", [SourceHealthState.VERIFIED, SourceHealthState.DEGRADED, SourceHealthState.PENDING_REVIEW],
)
def test_health_states_the_policy_keeps_polling_remain_eligible(health_state):
    """DEGRADED is documented as "still polled; flagged for review".
    PENDING_REVIEW is not excluded by the policy at all, and its only
    documented exit (pending_review -> verified) requires a real fetch,
    so excluding it would strand every newly-admitted source. Lane A
    already polls its one PENDING_REVIEW entry today."""
    assert is_eligible_for_polling(_source(health_state=health_state)) is True


@pytest.mark.parametrize(
    "source_format",
    [SourceFormat.OFFICIAL_API, SourceFormat.OFFICIAL_HTML_LISTING, SourceFormat.LICENSED_FEED],
)
def test_formats_with_no_fetch_client_are_not_eligible(source_format):
    """rss_atom_client is the only fetch client that exists, mirroring
    Lane A's own `entry.format == SourceFormat.RSS_ATOM` filter."""
    assert is_eligible_for_polling(_source(source_format=source_format)) is False


def test_eligible_for_polling_preserves_order_and_drops_only_the_ineligible():
    first = _source("first-rss")
    retired = _source("retired-rss", health_state=SourceHealthState.RETIRED)
    second = _source("second-rss")
    disabled = _source("disabled-rss", enabled=False)
    third = _source("third-rss")

    kept = eligible_for_polling((first, retired, second, disabled, third))

    assert [entry.source_id for entry in kept] == ["first-rss", "second-rss", "third-rss"]


# --- enforcement inside run_editorial_discovery --------------------------

def test_eligible_source_is_polled(tmp_path, recording_fetch):
    source = _source("polled-rss")

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert recording_fetch == [source.canonical_url]
    assert report.sources_polled == 1
    assert report.sources_skipped_ineligible == 0
    assert report.stories_published == 1


def test_disabled_source_is_not_polled_and_causes_zero_fetch_calls(tmp_path, recording_fetch):
    source = _source("disabled-rss", enabled=False)

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert recording_fetch == []
    assert report.sources_polled == 0
    assert report.sources_skipped_ineligible == 1
    assert report.items_fetched == 0
    assert report.stories_published == 0


@pytest.mark.parametrize("health_state", [SourceHealthState.FAILING, SourceHealthState.RETIRED])
def test_excluded_health_state_causes_zero_fetch_calls(tmp_path, recording_fetch, health_state):
    source = _source("excluded-rss", health_state=health_state)

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert recording_fetch == []
    assert report.sources_polled == 0
    assert report.sources_skipped_ineligible == 1
    assert report.items_fetched == 0


def test_degraded_source_is_still_polled(tmp_path, recording_fetch):
    """The policy's own wording for DEGRADED: "Still polled; flagged for
    review." A degraded source is not silently dropped."""
    source = _source("degraded-rss", health_state=SourceHealthState.DEGRADED)

    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(source,))

    assert recording_fetch == [source.canonical_url]
    assert report.sources_polled == 1
    assert report.sources_skipped_ineligible == 0


def test_a_source_returned_to_verified_is_polled_again(tmp_path, recording_fetch):
    """degraded <-> verified is the one documented recovery edge. An
    entry re-authored back to VERIFIED (or simply re-enabled) is polled
    again — and nothing in this module performs that transition on its
    own: it is always an explicit registry edit, modelled here by
    dataclasses.replace() standing in for that edit."""
    failing = _source("recovering-rss", health_state=SourceHealthState.FAILING)
    assert is_eligible_for_polling(failing) is False

    recovered = replace(failing, health_state=SourceHealthState.VERIFIED)
    report = editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(recovered,))

    assert recording_fetch == [recovered.canonical_url]
    assert report.sources_polled == 1


def test_retired_source_is_not_recovered_implicitly(tmp_path, recording_fetch):
    """RETIRED is terminal. Running discovery over a retired entry must
    never poll it, and must never mutate it back into a live state."""
    retired = _source("retired-rss", health_state=SourceHealthState.RETIRED)

    editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=(retired,))

    assert recording_fetch == []
    assert retired.health_state is SourceHealthState.RETIRED
    assert retired.enabled is True  # retirement is expressed by health_state, not by flipping `enabled`


def test_mixed_list_polls_only_the_eligible(tmp_path, recording_fetch):
    eligible_a = _source("eligible-a-rss")
    disabled = _source("disabled-rss", enabled=False)
    failing = _source("failing-rss", health_state=SourceHealthState.FAILING)
    retired = _source("retired-rss", health_state=SourceHealthState.RETIRED)
    inert_format = _source("api-rss", source_format=SourceFormat.OFFICIAL_API)
    eligible_b = _source("eligible-b-rss")

    report = editorial_pipeline.run_editorial_discovery(
        tmp_path, source_entries=(eligible_a, disabled, failing, retired, inert_format, eligible_b),
    )

    assert recording_fetch == [eligible_a.canonical_url, eligible_b.canonical_url]
    assert report.sources_polled == 2
    assert report.sources_skipped_ineligible == 4


def test_registry_entries_are_not_mutated_by_a_discovery_run(tmp_path, recording_fetch):
    """Provenance: an ineligible entry stays in the caller's own tuple,
    unchanged, exactly as authored."""
    entries = (
        _source("eligible-rss"),
        _source("disabled-rss", enabled=False),
        _source("retired-rss", health_state=SourceHealthState.RETIRED),
    )
    before = tuple((e.source_id, e.enabled, e.health_state, e.format) for e in entries)

    editorial_pipeline.run_editorial_discovery(tmp_path, source_entries=entries)

    assert tuple((e.source_id, e.enabled, e.health_state, e.format) for e in entries) == before
    assert len(entries) == 3


def test_dry_run_applies_the_same_eligibility_gate(tmp_path, recording_fetch):
    report = editorial_pipeline.run_editorial_discovery(
        tmp_path,
        source_entries=(_source("retired-rss", health_state=SourceHealthState.RETIRED),),
        dry_run=True,
    )

    assert recording_fetch == []
    assert report.sources_polled == 0
    assert report.sources_skipped_ineligible == 1


def test_default_source_entries_are_gated_by_the_same_contract(tmp_path, recording_fetch):
    """scripts/run_daily_news_discovery.py passes no source_entries at
    all, so the default argument must go through the same gate — this is
    why enforcement lives in run_editorial_discovery() rather than in
    the worker's own source assembly."""
    report = editorial_pipeline.run_editorial_discovery(tmp_path)

    assert report.sources_polled == len(eligible_for_polling(EDITORIAL_SOURCE_REGISTRY))
    assert len(recording_fetch) == report.sources_polled


# --- no behavior change for today's real registries ----------------------

def test_every_editorial_registry_entry_is_eligible_today(tmp_path):
    """This patch disables nothing: the shipped editorial registry is
    entirely eligible, so sources_polled is unchanged for every
    existing caller and assertion."""
    assert len(eligible_for_polling(EDITORIAL_SOURCE_REGISTRY)) == len(EDITORIAL_SOURCE_REGISTRY)


def test_gated_registries_remain_controlled_by_their_own_allow_lists():
    """Eligibility is additional to the gated allow-lists, never a
    replacement: a gated entry is still only reachable when its own
    source_id is allow-listed, and the allow-list never promotes an
    ineligible entry."""
    from src.config.settings import Settings
    from src.data_access.daily_news import jp_kr_sources, market_news_sources

    empty = Settings(
        daily_news_enabled_market_news_sources=frozenset(),
        daily_news_enabled_jp_kr_sources=frozenset(),
    )
    assert market_news_sources.enabled_gated_sources(empty) == ()
    assert jp_kr_sources.enabled_gated_sources(empty) == ()

    allowed = Settings(
        daily_news_enabled_market_news_sources=frozenset({"light-reading-rss"}),
        daily_news_enabled_jp_kr_sources=frozenset(),
    )
    gated = market_news_sources.enabled_gated_sources(allowed)
    assert [e.source_id for e in gated] == ["light-reading-rss"]
    assert eligible_for_polling(gated) == gated


# --- Lane A is untouched -------------------------------------------------

def test_lane_a_feed_registry_is_unchanged_by_this_patch():
    """PILOT_FEEDS is still built from `entry.enabled and entry.format ==
    RSS_ATOM` alone — no health-state filtering was added to Lane A, so
    its one PENDING_REVIEW entry is still polled exactly as before."""
    expected = tuple(
        entry.source_id for entry in RUNTIME_SOURCE_REGISTRY
        if entry.enabled and entry.format == SourceFormat.RSS_ATOM
    )
    assert tuple(feed.source_id for feed in feed_registry.PILOT_FEEDS) == expected
    assert len(feed_registry.PILOT_FEEDS) == len(RUNTIME_SOURCE_REGISTRY)


def test_lane_a_still_polls_its_pending_review_entry():
    pending = [e for e in RUNTIME_SOURCE_REGISTRY if e.health_state is SourceHealthState.PENDING_REVIEW]
    assert pending, "fixture assumption: Lane A has at least one PENDING_REVIEW entry today"
    polled_ids = {feed.source_id for feed in feed_registry.PILOT_FEEDS}
    for entry in pending:
        assert entry.source_id in polled_ids


def test_source_registry_module_exposes_the_shared_predicate():
    """The predicate lives in the pure data module both lanes already
    import, so a future Lane A change can reuse it without a new
    vocabulary."""
    assert source_registry.is_eligible_for_polling is is_eligible_for_polling
    assert source_registry._HEALTH_STATES_EXCLUDED_FROM_POLLING == frozenset(
        {SourceHealthState.FAILING, SourceHealthState.RETIRED}
    )
