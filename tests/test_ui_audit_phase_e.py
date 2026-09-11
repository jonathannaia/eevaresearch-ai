"""Phase E1 — Regional Brief (design/DASHBOARD_MARKET_MAP_PHASE_E.md): a
compact Regional Brief of real tracked-issuer filing titles for US/KR/JP
with an explicit "not connected" state for China. Every test here is a
pure rendering/content/source-inspection check via AppTest or direct
unit calls — no network call, worker, scheduler, or database migration
is exercised by this phase, and this file itself proves none was
introduced.

Dashboard triage redesign (design/DECISIONS.md): this file originally
also covered the Market Map tile grid's Dashboard-rendering behavior and
its src/logic/market_map.py grouping/selection functions
(group_companies_by_theme, company_selection_key,
find_company_by_selection_key). Those tests are deliberately removed
here, not left to fail silently — the Market Map component
(src/ui/components/market_map.py) was deleted from the Dashboard render
path (superseding commit ee685cb cleanly) in favor of Recent Theme
Activity, and the three grouping/selection functions it alone depended
on were removed as dead code from src/logic/market_map.py in the same
change. Recent Theme Activity's own equivalent coverage — Dashboard
rendering, ordering, "no new integration," and no-fabricated-language
guards — lives in tests/test_recent_theme_activity.py, not here.
REGION_SOURCE/jurisdiction_for_source (also in src/logic/market_map.py)
are unrelated to that removal — genuinely still used by several other,
unchanged modules — so their own coverage below is unchanged.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.logic.market_map import REGION_SOURCE
from src.models.models import FilingEvent
from src.ui.components import regional_brief

HARNESS_DIR = Path(__file__).parent / "apptest_pages"
REPO_ROOT = Path(__file__).parent.parent


def _run_dashboard():
    at = AppTest.from_file(str(HARNESS_DIR / "dashboard_page.py"), default_timeout=15)
    at.run()
    return at


def _text(at) -> str:
    return " ".join(m.value for m in at.markdown if not m.value.startswith("<style>"))


# ============================== NO NEW INTEGRATIONS ==============================

def test_regional_brief_introduces_no_quote_provider_network_or_secret_dependency():
    forbidden_substrings = (
        "yfinance", "alpha_vantage", "alphavantage", "polygon.io", "iexcloud", "finnhub",
        "marketstack", "twelvedata", "tiingo", "requests.get(", "requests.post(", "httpx.",
        "urllib.request", "boto3", "threading.Thread", "subprocess", "multiprocessing",
        "EDGE_RADAR_LIVE_SCAN_ENABLED", "os.environ",
    )
    source = (REPO_ROOT / "src" / "ui" / "components" / "regional_brief.py").read_text(encoding="utf-8")
    for forbidden in forbidden_substrings:
        assert forbidden not in source, f"regional_brief.py unexpectedly contains {forbidden!r}"


# ============================== REGIONAL BRIEF ==============================

def test_regional_brief_china_shows_explicit_not_connected_state():
    at = _run_dashboard()
    all_text = _text(at)
    assert "China coverage is not connected yet." in all_text
    assert "Eeva currently has no tracked China issuers or filing-source coverage." in all_text


def test_regional_brief_us_kr_jp_show_honest_empty_state_with_no_data():
    tmp_path = Path(tempfile.mkdtemp())
    settings = Settings(cache_dir=tmp_path)
    at = AppTest.from_file(str(HARNESS_DIR / "dashboard_page.py"), default_timeout=15)
    with patch("src.ui.pages.dashboard.get_settings", return_value=settings):
        at.run()
    assert not at.exception
    all_text = _text(at)
    assert "No recent tracked-issuer disclosures available." in all_text
    # Never fabricated in place of the honest empty state.
    assert "market news" not in all_text.lower()
    assert "market summary" not in all_text.lower()


def test_regional_brief_labels_content_as_disclosures_never_as_market_news():
    at = _run_dashboard()
    all_text = _text(at)
    assert "Recent issuer disclosures from tracked coverage" in all_text
    for forbidden_label in ("market news", "market summary", "regional market outlook"):
        assert forbidden_label not in all_text.lower()


def test_regional_brief_renders_real_filing_event_fields_with_a_date():
    """`render_regional_brief` must pull from `backend_factory.
    get_filing_event_repository` (the same read-only accessor Radar Inbox
    itself uses) rather than inventing content — proven by substituting a
    fake repository and checking its exact fields (issuer, title, date)
    flow through unmodified."""
    fake_filing = FilingEvent(
        rcept_no="0000320193-26-000079", corp_code="0000320193", corp_name="NVIDIA",
        stock_code="NVDA", report_nm="Test 8-K Filing", rcept_dt="2026-08-20",
        flr_nm="NVIDIA", source_name="SEC EDGAR", source_url="https://example.invalid/filing",
    )

    class _FakeRepo:
        def load_filing_events(self):
            return (fake_filing,)

    settings = Settings(cache_dir=Path(tempfile.mkdtemp()))
    with patch("src.data_access.backend_factory.get_filing_event_repository", return_value=_FakeRepo()):
        items = regional_brief._load_recent_filings("SEC EDGAR", settings)
    assert items == [fake_filing]

    at = AppTest.from_file(str(HARNESS_DIR / "dashboard_page.py"), default_timeout=15)
    with patch("src.data_access.backend_factory.get_filing_event_repository", return_value=_FakeRepo()):
        at.run()
    assert not at.exception
    all_text = _text(at)
    assert "Test 8-K Filing" in all_text
    assert "NVIDIA" in all_text
    assert "Aug 20, 2026" in all_text


def test_region_source_mapping_matches_the_three_real_filing_sources_only():
    assert set(REGION_SOURCE.values()) == {"SEC EDGAR", "OpenDART / DART", "EDINET"}
    assert "China" not in REGION_SOURCE


# ============================== EXISTING MODULES / ROUTES INTACT ==============================

def test_dashboard_has_no_capital_rotation_catalysts_or_todays_read():
    """Capital Rotation, Catalysts, and Today's Read were removed
    entirely (reader-facing data-integrity pass, design/DECISIONS.md) —
    no real live source exists in this build for market performance,
    breadth, rotation, or a catalyst calendar, and none was invented to
    replace them. Fixture-driven presence/absence checks for Theme
    Health and Priority Signals (which depend on whether any real
    published Theme/real signal exists) live in
    tests/test_dashboard_data_integrity.py, not here."""
    at = _run_dashboard()
    all_text = _text(at)
    for heading in ("Today's Read", "Capital Rotation", "Next Catalysts", "Watchlist Changes"):
        assert heading not in all_text
    assert at.expander == []
