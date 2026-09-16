"""Dashboard "Latest" (Recently Updated) — beta-blocker data-integrity fix
(design/DECISIONS.md). Proves the newest-item selection is entirely live,
data-driven, and never seeded/hard-coded:

  - a newer Radar CandidateSignal outranks an older Daily News item;
  - a newer eligible Daily News (editorial) item outranks an older Radar
    item;
  - a non-qualifying (archived/rejected), future-dated, invalid-dated,
    demo/seed, or unpublished item can never win;
  - a stale seed-style headline (the exact reported "Larry Ellison
    cancels plan to sell Oracle stock" class of item) never wins once a
    newer real item exists;
  - duplicates collapse to one row, and an exact-timestamp tie always
    resolves the same way (deterministic).

Uses `recently_updated._select_recently_updated_rows()` directly for
ordering/exclusion proofs (fast, exact, no DOM-order fragility), and the
real Dashboard AppTest harness for the two end-to-end rendering proofs
(empty state; the literal reported regression scenario)."""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.data_access.daily_news import daily_news_store, editorial_story_store
from src.data_access.dart import candidate_store
from src.models.daily_news_models import (
    EditorialStory, NewsMaterialityTier, NewsSourceReference, NewsStateTransition, NewsStory, NewsStoryStatus, SourceClass,
)
from src.models.models import CandidateSignal, CandidateStatus, FilingEvent
from src.ui.components import recently_updated

DASHBOARD_HARNESS = Path(__file__).parent / "apptest_pages" / "dashboard_page.py"

_CANDIDATE_FILENAME_BY_SOURCE = {"OpenDART / DART": "dart_candidates.json", "SEC EDGAR": "edgar_candidates.json"}


def _settings(tmp_path) -> Settings:
    return Settings(db_backend="json", cache_dir=tmp_path)


def _candidate(
    rcept_no: str, corp_name: str, report_nm: str, source_name: str = "SEC EDGAR",
    rcept_dt: str = "2026-09-01", status: CandidateStatus = CandidateStatus.NEEDS_REVIEW, is_demo: bool = False,
) -> CandidateSignal:
    filing = FilingEvent(
        rcept_no=rcept_no, corp_code="0000320193", corp_name=corp_name, stock_code="ORCL",
        report_nm=report_nm, rcept_dt=rcept_dt, flr_nm=corp_name, source_name=source_name,
        source_url=f"https://example.invalid/{rcept_no}", retrieved_at=f"{rcept_dt}T00:00:00+00:00",
        is_demo=is_demo,
    )
    return CandidateSignal(id=f"cand-{rcept_no}", filing=filing, matched_rules=["test_rule"], confidence="Moderate", status=status)


def _seed_candidate(cache_dir, candidate: CandidateSignal) -> None:
    filename = _CANDIDATE_FILENAME_BY_SOURCE[candidate.filing.source_name]
    candidate_store.upsert_new_candidates(cache_dir, [candidate], filename)


def _news_story(story_id: str, company_name: str, headline: str, published_at: str, status: NewsStoryStatus = NewsStoryStatus.PUBLISHED) -> NewsStory:
    return NewsStory(
        id=story_id, company_name=company_name, ticker="ORCL", theme_slug="ai-buildout",
        headline=headline, eeva_summary="Summary.", is_fallback_summary=False,
        translation_unavailable=False, original_title=None,
        sources=(
            NewsSourceReference(
                publisher=company_name, source_class=SourceClass.OFFICIAL_COMPANY,
                url=f"https://example.invalid/{story_id}", title=headline,
                published_at=published_at, retrieved_at=published_at, original_language="English",
            ),
        ),
        status=status,
        state_history=[NewsStateTransition(status=status, at=published_at)],
    )


def _editorial_story(
    story_id: str, headline: str, published_at: str, matched_companies: tuple[str, ...] = ("Oracle Corporation",),
    materiality_tier: NewsMaterialityTier | None = None,
) -> EditorialStory:
    return EditorialStory(
        id=story_id, headline=headline, publisher="CNBC", source_url=f"https://example.invalid/{story_id}",
        published_at=published_at, retrieved_at=published_at, excerpt=None,
        matched_companies=matched_companies, matched_themes=(), source_feed_id="cnbc-technology-rss",
        materiality_tier=materiality_tier,
    )


# --- Newer Radar item replaces an older Daily News item ------------------


def test_newer_radar_candidate_outranks_an_older_daily_news_story(tmp_path):
    daily_news_store.upsert_new_stories(tmp_path, [
        _news_story("s-old", "Oracle Corporation", "Old Oracle official update", "2026-08-01T00:00:00+00:00"),
    ])
    _seed_candidate(tmp_path, _candidate("acc-new", "Oracle Corporation", "Oracle announces new AI cloud deal", rcept_dt="2026-09-10"))

    rows = recently_updated._select_recently_updated_rows(_settings(tmp_path))

    assert rows[0].title == "Oracle announces new AI cloud deal"


# --- Newer eligible Daily News (editorial) item replaces an older Radar item ---


def test_newer_editorial_daily_news_story_outranks_an_older_radar_candidate(tmp_path):
    _seed_candidate(tmp_path, _candidate("acc-old", "Oracle Corporation", "Old Oracle 10-Q filing", rcept_dt="2026-08-01"))
    now = datetime.now(timezone.utc)
    editorial_story_store.upsert_new_stories(tmp_path, [
        _editorial_story("editorial-new", "Oracle Corporation reports strong AI cloud demand", (now - timedelta(hours=1)).isoformat()),
    ])

    rows = recently_updated._select_recently_updated_rows(_settings(tmp_path), now)

    assert rows[0].title == "Oracle Corporation reports strong AI cloud demand"
    assert rows[0].source_label == "Signals"


# --- Non-qualifying / future-dated / demo / unpublished items can never win ---


def test_archived_dismissed_candidate_is_excluded(tmp_path):
    _seed_candidate(tmp_path, _candidate("acc-dismissed", "Oracle Corporation", "Dismissed candidate", status=CandidateStatus.DISMISSED))

    rows = recently_updated._select_recently_updated_rows(_settings(tmp_path))

    assert rows == []


def test_not_material_candidate_is_excluded(tmp_path):
    _seed_candidate(tmp_path, _candidate("acc-not-material", "Oracle Corporation", "Not material candidate", status=CandidateStatus.NOT_MATERIAL))

    rows = recently_updated._select_recently_updated_rows(_settings(tmp_path))

    assert rows == []


def test_future_dated_candidate_is_excluded_never_wins_the_top_spot(tmp_path):
    now = datetime.now(timezone.utc)
    future = (now + timedelta(days=2)).strftime("%Y-%m-%d")
    _seed_candidate(tmp_path, _candidate("acc-future", "Oracle Corporation", "Impossible future filing", rcept_dt=future))
    _seed_candidate(tmp_path, _candidate("acc-real", "Oracle Corporation", "Real recent filing", rcept_dt=now.strftime("%Y-%m-%d")))

    rows = recently_updated._select_recently_updated_rows(_settings(tmp_path), now)

    titles = [r.title for r in rows]
    assert "Impossible future filing" not in titles
    assert "Real recent filing" in titles


def test_future_dated_daily_news_story_is_excluded(tmp_path):
    now = datetime.now(timezone.utc)
    future = (now + timedelta(hours=9)).isoformat()  # mirrors the exact naive-KST-mislabeled-as-UTC bug shape
    daily_news_store.upsert_new_stories(tmp_path, [_news_story("s-future", "Oracle Corporation", "Impossible future story", future)])

    rows = recently_updated._select_recently_updated_rows(_settings(tmp_path), now)

    assert rows == []


def test_invalid_dated_candidate_is_excluded(tmp_path):
    candidate = _candidate("acc-invalid", "Oracle Corporation", "Invalid date candidate")
    broken_filing = replace(candidate.filing, rcept_dt="not-a-date", retrieved_at="")
    broken_candidate = replace(candidate, filing=broken_filing)
    _seed_candidate(tmp_path, broken_candidate)

    rows = recently_updated._select_recently_updated_rows(_settings(tmp_path))

    assert rows == []


def test_stale_seed_demo_filing_is_excluded(tmp_path):
    _seed_candidate(tmp_path, _candidate("acc-demo", "Oracle Corporation", "Demo seed filing", is_demo=True))

    rows = recently_updated._select_recently_updated_rows(_settings(tmp_path))

    assert rows == []


def test_unpublished_daily_news_story_is_excluded(tmp_path):
    now = datetime.now(timezone.utc)
    daily_news_store.upsert_new_stories(tmp_path, [
        _news_story("s-draft", "Oracle Corporation", "Not yet published story", now.isoformat(), status=NewsStoryStatus.DISCOVERED),
    ])

    rows = recently_updated._select_recently_updated_rows(_settings(tmp_path), now)

    assert rows == []


# --- The exact reported regression: a stale seed-style headline never wins ---


def test_stale_seed_style_headline_never_wins_when_newer_live_data_exists(tmp_path):
    # Models the exact reported symptom: an old, seed-flagged headline
    # ("Larry Ellison cancels plan to sell Oracle stock"-style) must
    # never be the item shown once genuinely newer live data exists —
    # proven generically (is_demo=True, not by matching literal text),
    # since "Latest" must never depend on any specific headline string.
    _seed_candidate(tmp_path, _candidate("acc-seed", "Oracle Corporation", "Larry Ellison cancels plan to sell Oracle stock", rcept_dt="2026-01-01", is_demo=True))
    now = datetime.now(timezone.utc)
    daily_news_store.upsert_new_stories(tmp_path, [
        _news_story("s-newer", "Oracle Corporation", "Oracle posts quarterly cloud growth", now.isoformat()),
    ])

    rows = recently_updated._select_recently_updated_rows(_settings(tmp_path), now)

    titles = [r.title for r in rows]
    assert "Larry Ellison cancels plan to sell Oracle stock" not in titles
    assert rows[0].title == "Oracle posts quarterly cloud growth"


# --- Duplicate handling and deterministic tie-breaking --------------------


def test_duplicate_row_collapses_to_one(tmp_path):
    candidate = _candidate("acc-dup", "Oracle Corporation", "Duplicate-safe filing")
    _seed_candidate(tmp_path, candidate)
    # Seeding twice (e.g. a re-run writing the same id again) must never
    # produce two visible rows for the same real filing.
    _seed_candidate(tmp_path, candidate)

    rows = recently_updated._select_recently_updated_rows(_settings(tmp_path))

    assert len(rows) == 1


def test_exact_timestamp_tie_is_broken_deterministically(tmp_path):
    same_dt = "2026-09-01"
    _seed_candidate(tmp_path, _candidate("acc-a", "Alpha Corp", "Alpha filing", rcept_dt=same_dt))
    _seed_candidate(tmp_path, _candidate("acc-b", "Beta Corp", "Beta filing", rcept_dt=same_dt))

    order_1 = [r.title for r in recently_updated._select_recently_updated_rows(_settings(tmp_path))]
    order_2 = [r.title for r in recently_updated._select_recently_updated_rows(_settings(tmp_path))]

    assert order_1 == order_2  # same input, same order, every time


# --- Honest empty state, end-to-end -----------------------------------


def _run_dashboard(settings: Settings) -> AppTest:
    with patch("src.ui.pages.dashboard.get_settings", return_value=settings):
        at = AppTest.from_file(str(DASHBOARD_HARNESS), default_timeout=15)
        at.run()
    return at


def _text(at: AppTest) -> str:
    return " ".join(m.value for m in at.markdown if not m.value.startswith("<style>"))


def test_honest_empty_state_when_nothing_eligible(tmp_path):
    # Only a dismissed candidate and a future-dated story exist — nothing
    # eligible survives, so the page must show its own honest empty
    # state, never old/seed content standing in for a live result.
    _seed_candidate(tmp_path, _candidate("acc-dismissed-2", "Oracle Corporation", "Dismissed", status=CandidateStatus.DISMISSED))
    settings = _settings(tmp_path)

    at = _run_dashboard(settings)

    assert not at.exception
    assert "No recent updates available." in _text(at)
    assert "Larry Ellison" not in _text(at)


def test_end_to_end_newer_live_item_displays_and_stale_seed_headline_does_not(tmp_path):
    # Full-page reproduction of the exact reported bug: seeds a stale,
    # demo-flagged Oracle headline alongside a genuinely newer, real
    # Daily News item, renders the real Dashboard page, and asserts the
    # newer item is visible while the stale seed headline is not.
    _seed_candidate(tmp_path, _candidate("acc-seed-2", "Oracle Corporation", "Larry Ellison cancels plan to sell Oracle stock", rcept_dt="2026-01-01", is_demo=True))
    now = datetime.now(timezone.utc)
    daily_news_store.upsert_new_stories(tmp_path, [
        _news_story("s-e2e-newer", "Oracle Corporation", "Oracle announces new data center investment", now.isoformat()),
    ])
    settings = _settings(tmp_path)

    at = _run_dashboard(settings)

    assert not at.exception
    all_text = _text(at)
    assert "Oracle announces new data center investment" in all_text
    assert "Larry Ellison cancels plan to sell Oracle stock" not in all_text


def test_recently_updated_footer_links_use_the_current_signals_label():
    """Product-naming separation (design/DECISIONS.md) — the "Recently
    Updated" section's own footer link was missed in the original rename
    pass (a real, confirmed gap found by a later pre-push audit) and
    still read "View all Daily News →" until this fix.

    get_page("daily_news")/get_page("radar_inbox") only resolve to real
    Page objects when run through app.py's real entry point — the
    isolated per-page/per-component AppTest harness never populates
    st.session_state["_pages"], so these page_links never actually
    render there (same documented limitation as
    tests/test_themes_research_page.py::test_detail_back_link_says_all_
    research_theses). Checked at the source level instead."""
    source = (
        Path(__file__).parent.parent / "src" / "ui" / "components" / "recently_updated.py"
    ).read_text(encoding="utf-8")
    assert 'st.page_link(daily_news_page, label="View all Signals →")' in source
    assert "View all Daily News" not in source


# ============================================================
# Dashboard tier-aware preview (Signals precision follow-up, live-card
# audit, design/POST_MERGE_SIGNALS_LIVE_CARD_AUDIT_2026_09_16.md) —
# Background-tier editorial items must never appear in the default
# "Recently Updated" preview; Watchlist and High Signal are unaffected.
# ============================================================


def test_background_tier_editorial_story_is_excluded_from_the_default_preview(tmp_path):
    """The exact confirmed live shape (smartARM/Meta, Samsung One UI 9,
    L3Harris strategic-commentary — all Background) — must not appear."""
    editorial_story_store.upsert_new_stories(tmp_path, [
        _editorial_story(
            "editorial-background", "Meta-smartARM style Background item", datetime.now(timezone.utc).isoformat(),
            materiality_tier=NewsMaterialityTier.BACKGROUND,
        ),
    ])

    rows = recently_updated._select_recently_updated_rows(_settings(tmp_path))

    assert rows == []


def test_watchlist_tier_editorial_story_still_shown(tmp_path):
    editorial_story_store.upsert_new_stories(tmp_path, [
        _editorial_story(
            "editorial-watchlist", "Watchlist-tier editorial item", datetime.now(timezone.utc).isoformat(),
            materiality_tier=NewsMaterialityTier.WATCHLIST,
        ),
    ])

    rows = recently_updated._select_recently_updated_rows(_settings(tmp_path))

    assert len(rows) == 1
    assert rows[0].title == "Watchlist-tier editorial item"


def test_high_signal_tier_editorial_story_still_shown(tmp_path):
    editorial_story_store.upsert_new_stories(tmp_path, [
        _editorial_story(
            "editorial-high-signal", "High Signal-tier editorial item", datetime.now(timezone.utc).isoformat(),
            materiality_tier=NewsMaterialityTier.HIGH_SIGNAL,
        ),
    ])

    rows = recently_updated._select_recently_updated_rows(_settings(tmp_path))

    assert len(rows) == 1
    assert rows[0].title == "High Signal-tier editorial item"


def test_background_and_watchlist_items_together_only_watchlist_survives(tmp_path):
    """A mixed batch — proves the filter discriminates per-row, not
    per-run, and that a Background row never displaces a genuine
    Watchlist/High Signal row's own visibility."""
    now = datetime.now(timezone.utc)
    editorial_story_store.upsert_new_stories(tmp_path, [
        _editorial_story(
            "editorial-bg", "Background item", (now - timedelta(minutes=1)).isoformat(),
            materiality_tier=NewsMaterialityTier.BACKGROUND,
        ),
        _editorial_story(
            "editorial-wl", "Watchlist item", (now - timedelta(minutes=2)).isoformat(),
            materiality_tier=NewsMaterialityTier.WATCHLIST,
        ),
    ])

    rows = recently_updated._select_recently_updated_rows(_settings(tmp_path), now)

    titles = [r.title for r in rows]
    assert "Background item" not in titles
    assert "Watchlist item" in titles


def test_radar_candidate_row_is_unaffected_by_the_editorial_tier_filter(tmp_path):
    """Radar/filing rows carry no materiality_tier concept at all — the
    new filter lives only inside _load_editorial_rows() and must never
    touch _load_filing_rows()'s own, completely separate behavior."""
    _seed_candidate(tmp_path, _candidate("acc-unaffected", "Oracle Corporation", "Radar candidate row"))

    rows = recently_updated._select_recently_updated_rows(_settings(tmp_path))

    assert any(r.title == "Radar candidate row" for r in rows)
