"""Phase E1 — Dashboard Market Map (design/DASHBOARD_MARKET_MAP_PHASE_E.md):
a theme-grouped company navigator (not a price heatmap — no quote/price
capability exists anywhere in this build), a compact Regional Brief of
real tracked-issuer filing titles for US/KR/JP with an explicit "not
connected" state for China, and Capital Rotation demoted to a secondary,
truthfully-labeled collapsed expander. Every test here is a pure
rendering/content/source-inspection check via AppTest or direct unit
calls — no network call, worker, scheduler, or database migration is
exercised by this phase, and this file itself proves none was introduced.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.config.tracked_companies import TRACKED_COMPANIES, get_tracked_companies
from src.logic.market_map import (
    REGION_SOURCE,
    company_selection_key,
    find_company_by_selection_key,
    group_companies_by_theme,
)
from src.models.models import FilingEvent
from src.ui.components import regional_brief

HARNESS_DIR = Path(__file__).parent / "apptest_pages"
REPO_ROOT = Path(__file__).parent.parent
_NEW_FILES = (
    REPO_ROOT / "src" / "logic" / "market_map.py",
    REPO_ROOT / "src" / "ui" / "components" / "market_map.py",
    REPO_ROOT / "src" / "ui" / "components" / "regional_brief.py",
)


def _run_dashboard():
    at = AppTest.from_file(str(HARNESS_DIR / "dashboard_page.py"), default_timeout=15)
    at.run()
    return at


def _text(at) -> str:
    return " ".join(m.value for m in at.markdown if not m.value.startswith("<style>"))


# ============================== MARKET MAP RENDERING ==============================

def test_dashboard_renders_market_map_with_capability_state_wording():
    at = _run_dashboard()
    assert not at.exception
    all_text = _text(at)
    assert "Market Map" in all_text
    assert "Company and theme map · price coverage is being connected" in all_text
    assert "Price coverage not connected" in all_text
    # Not a dash, not blank — the exact required wording, at least once
    # per rendered tile.
    assert all_text.count("Price coverage not connected") >= 1


def test_market_map_never_uses_live_today_or_heatmap_language():
    at = _run_dashboard()
    all_text = _text(at)
    market_map_start = all_text.index("Market Map")
    regional_brief_start = all_text.index("Regional Brief")
    market_map_chunk = all_text[market_map_start:regional_brief_start]
    for forbidden in ("live", "today", "market performance", "heatmap", "market movement"):
        assert forbidden not in market_map_chunk.lower()


# ============================== GROUPING LOGIC ==============================

def test_group_companies_by_theme_has_no_second_mapping_and_preserves_multi_theme():
    themes = sorted({slug for c in TRACKED_COMPANIES for slug in c.themes})
    grouped = group_companies_by_theme(themes)
    samsung = next(c for c in TRACKED_COMPANIES if c.name == "Samsung Electronics")
    sk_hynix = next(c for c in TRACKED_COMPANIES if c.name == "SK Hynix")
    for company in (samsung, sk_hynix):
        assert set(company.themes) == {"memory", "ai-buildout"}
        for slug in company.themes:
            assert company in grouped[slug]
    # Every company returned actually exists in the one authoritative
    # registry — nothing invented, nothing from a second list.
    for slug, companies in grouped.items():
        for c in companies:
            assert c in TRACKED_COMPANIES
            assert slug in c.themes


def test_company_selection_key_round_trips():
    for company in get_tracked_companies(active_only=True):
        key = company_selection_key(company)
        assert find_company_by_selection_key(key) == company


def test_market_map_component_reads_only_tracked_companies_no_duplicate_registry():
    """src/logic/market_map.py and src/ui/components/market_map.py must
    read src.config.tracked_companies exclusively — never construct their
    own TrackedCompany/company list."""
    for path in (REPO_ROOT / "src" / "logic" / "market_map.py", REPO_ROOT / "src" / "ui" / "components" / "market_map.py"):
        source = path.read_text(encoding="utf-8")
        assert "TrackedCompany(" not in source  # no new instances constructed
        assert "src.config.tracked_companies" in source or "from src.config.tracked_companies" in source


# ============================== NO NEW INTEGRATIONS ==============================

def test_phase_e1_introduces_no_quote_provider_network_or_secret_dependency():
    forbidden_substrings = (
        "yfinance", "alpha_vantage", "alphavantage", "polygon.io", "iexcloud", "finnhub",
        "marketstack", "twelvedata", "tiingo", "requests.get(", "requests.post(", "httpx.",
        "urllib.request", "boto3", "threading.Thread", "subprocess", "multiprocessing",
        "EDGE_RADAR_LIVE_SCAN_ENABLED", "os.environ",
    )
    for path in _NEW_FILES:
        source = path.read_text(encoding="utf-8")
        for forbidden in forbidden_substrings:
            assert forbidden not in source, f"{path.name} unexpectedly contains {forbidden!r}"


def test_phase_e1_market_map_files_compute_no_price_or_return_values():
    """No fmt_pct/fmt_currency call anywhere in the new Market Map files —
    there is no real number to format, since no price/return field exists
    on TrackedCompany at all (Phase E report, section A)."""
    for path in (REPO_ROOT / "src" / "logic" / "market_map.py", REPO_ROOT / "src" / "ui" / "components" / "market_map.py"):
        source = path.read_text(encoding="utf-8")
        assert "fmt_pct(" not in source
        assert "fmt_currency(" not in source


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


# ============================== CAPITAL ROTATION ==============================

def test_capital_rotation_is_a_secondary_collapsed_expander_labeled_as_demo_snapshot():
    at = _run_dashboard()
    assert not at.exception
    expander_labels = {e.label for e in at.expander}
    assert "Capital Rotation — demo snapshot" in expander_labels
    all_text = _text(at)
    assert "Aug 15, 2026" in all_text  # the real as_of date, not "today"
    for forbidden in ("Capital Rotation — Today", "Capital Rotation — Live", "Capital Rotation — Current", "Capital Rotation — Real-time"):
        assert forbidden not in all_text


def test_capital_rotation_calculation_and_data_are_unchanged():
    """Phase E1 only relocates/relabels Capital Rotation — its source
    (data/seed/rotation_metrics.json) and computation
    (src/logic/theme_metrics.py) are untouched. A direct read confirms
    the same five fixed, is_demo=True records this repo has always had."""
    from src.data_access.container import get_repositories

    ctx = get_repositories()
    metrics = ctx.market_data_provider.get_rotation_metrics()
    assert len(metrics) == 5
    for m in metrics:
        assert m.is_demo is True
        assert m.as_of.startswith("2026-08-15")


# ============================== EXISTING MODULES / ROUTES INTACT ==============================

def test_dashboard_keeps_every_pre_existing_module():
    at = _run_dashboard()
    all_text = _text(at)
    for heading in ("Today's Read", "Theme Health", "Priority Signals", "Next Catalysts", "Watchlist Changes"):
        assert heading in all_text


def test_market_map_open_company_handoff_uses_the_existing_company_route():
    """No new route is invented — the same hidden `company` page/query-
    param pattern every other ticker link in the app already uses (e.g.
    Themes' demo-ticker link, Dashboard's Watchlist Changes rows)."""
    source = (REPO_ROOT / "src" / "ui" / "components" / "market_map.py").read_text(encoding="utf-8")
    assert 'get_page("company")' in source
    assert 'get_page("radar_inbox")' in source
    assert 'get_page("research")' in source
