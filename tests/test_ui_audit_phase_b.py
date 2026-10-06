"""UI/product audit Phase B (design/DECISIONS.md) — low-risk hierarchy and
progressive-disclosure improvements: Radar Inbox's not-configured dump
collapses into an expander, empty/single-option filters stop rendering as
dead controls, Signals' original-language filing text collapses behind an
expander, Dashboard gets button-style CTAs and a visual-hierarchy pass,
Company gets a template-preview notice, and the stylesheet load gets an
mtime-keyed cache. Every test here is a pure rendering/content check via
AppTest — no data loading, no navigation, no auth, no worker, no database
code is touched by this phase, and none of that is exercised here."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from tests.configured_test_settings import configured_settings
from tests.no_network import block_network

HARNESS_DIR = Path(__file__).parent / "apptest_pages"
REPO_ROOT = Path(__file__).parent.parent


# --- Radar Inbox: not-configured dump collapses into an expander ---

def test_radar_inbox_not_configured_state_collapses_detail_into_expander(tmp_path):
    from unittest.mock import patch

    from src.config.settings import Settings

    settings = Settings(
        dart_api_key=None, translation_api_key=None, edgar_user_agent=None,
        edinet_subscription_key=None, cache_dir=tmp_path,
    )
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(HARNESS_DIR / "radar_inbox_page.py"), default_timeout=10)
        at.run()

    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    # Title preserved exactly (existing tests assert this literal string).
    assert "Latest Filings is not configured" in all_text
    # Full diagnostic content is preserved, unchanged, somewhere on the page.
    assert "EDGE_DART_API_KEY is not configured." in all_text
    assert "EDGE_EDINET_SUBSCRIPTION_KEY is not configured." in all_text
    assert "Add the missing configuration to your local .env and restart the app." in all_text
    # ...but now behind a collapsed expander, not the top-level message.
    labels = [e.label for e in at.expander]
    assert "Configuration details" in labels


# --- Coverage: Layer filter hidden when empty, expander labels dynamic ---

def test_coverage_layer_filter_absent_when_no_seed_issuer_has_a_layer():
    at = AppTest.from_file(str(HARNESS_DIR / "coverage_page.py"), default_timeout=10)
    at.run()
    assert not at.exception
    multiselect_labels = [m.label for m in at.multiselect]
    assert "Theme" in multiselect_labels
    assert "Source" in multiselect_labels
    assert "Jurisdiction" in multiselect_labels
    # Today's seed data has no populated supply_chain_layers values.
    assert "Layer" not in multiselect_labels


def test_coverage_expander_labels_show_dynamic_counts():
    at = AppTest.from_file(str(HARNESS_DIR / "coverage_page.py"), default_timeout=10)
    at.run()
    assert not at.exception
    labels = [e.label for e in at.expander]
    assert "26 discovery proposals — not active coverage" in labels
    assert "4 known category conflicts" in labels


def _seed_two_theme_candidates(cache_dir) -> None:
    """Two PUBLISHED candidates on different themes, written through the
    real candidate store so the signals page builds them exactly as it
    would in production."""
    from datetime import datetime, timezone

    from src.data_access.dart import candidate_store
    from src.models.models import (
        CandidateSignal, CandidateStatus, ExtractionState, FilingEvent, StateTransition,
    )

    now = datetime.now(timezone.utc).isoformat()

    def _candidate(cid: str, corp: str, theme: str, rcept: str, rule: str, confidence: str) -> CandidateSignal:
        filing = FilingEvent(
            rcept_no=rcept, corp_code=f"code-{cid}", corp_name=corp, stock_code="X",
            report_nm="신규시설투자등", rcept_dt="2026-08-12", flr_nm=corp,
            source_name="OpenDART / DART", source_url="https://example.test/f",
            retrieved_at=now, theme_slug=theme,
        )
        return CandidateSignal(
            id=cid, filing=filing, matched_rules=[rule], confidence=confidence,
            status=CandidateStatus.PUBLISHED, extraction_state=ExtractionState.EXTRACTED,
            excerpt_original="신규시설투자 결정 공시입니다.",
            state_history=[StateTransition(status=CandidateStatus.PUBLISHED, at=now)],
        )

    candidates = {
        "cand-phase-b-1": _candidate(
            "cand-phase-b-1", "SK Hynix", "memory", "20260812000301",
            "capex_or_facility_investment:facility_investment:신규시설투자", "High",
        ),
        "cand-phase-b-2": _candidate(
            "cand-phase-b-2", "Samsung Electronics", "ai-buildout", "20260812000302",
            "supply_or_sales_contract:supply_or_sales_contract:공급계약", "Moderate",
        ),
    }
    candidate_store.save_candidates(cache_dir, candidates, "dart_candidates.json")


# --- Signals: Direction/Time horizon filters hidden at a single option ---

def test_signals_page_hides_single_option_direction_and_horizon_filters(tmp_path, monkeypatch):
    """Isolated: seeds its own candidates into a tmp_path cache and
    patches container.get_settings, so it no longer reads the developer's
    real data/cache/themes.json + edgar_candidates.json (gitignored, so
    this previously failed on any fresh checkout). Readiness fields come
    from configured_settings() rather than Settings() defaults, so none
    of them is inherited from a local .env; outbound sockets are blocked
    for the whole test, installed before AppTest is constructed.

    The condition under test is seeded explicitly rather than inherited
    from whatever real signals happen to exist: TWO candidates on
    DIFFERENT themes and with different strengths (so Theme and Strength
    each offer more than one option and render), which under the current
    signal mapping share a single Direction and a single Time horizon
    (so those two filters stay hidden)."""
    block_network(monkeypatch)
    _seed_two_theme_candidates(tmp_path)

    settings = configured_settings(tmp_path)
    with patch("src.data_access.container.get_settings", return_value=settings):
        at = AppTest.from_file(str(HARNESS_DIR / "signals_page.py"), default_timeout=10)
        at.run()
    assert not at.exception
    multiselect_labels = [m.label for m in at.multiselect]
    assert "Theme" in multiselect_labels
    assert "Strength" in multiselect_labels
    # Today's real Radar signals all share one Direction ("Emerging") and
    # one Time horizon ("Multi-week") value.
    assert "Direction" not in multiselect_labels
    assert "Time horizon" not in multiselect_labels
    # Clear filters control is unaffected.
    assert any(b.label == "Clear filters" for b in at.button)


# --- Signal card: original-language filing text collapses into an expander ---

_TRANSLATED_SIGNAL_CARD_SCRIPT = """
from src.models.models import Direction, Horizon, Signal, Strength
from src.ui.components.cards import signal_card

signal = Signal(
    id="sig-phase-b-translation-test", title="Report on Significant Matters",
    theme_slug="memory", subtheme_slug="dram", direction=Direction.EMERGING,
    strength=Strength.MODERATE, horizon=Horizon.MULTI_WEEK, evidence_count=1,
    interpretation="Test interpretation.", contrary_evidence="", validation_criteria="",
    invalidation_criteria="", related_tickers=[], last_updated="2026-08-20T00:00:00+00:00",
    is_demo=False, issuer="SK Hynix", source_name="OpenDART / DART",
    excerpt="ORIGINAL_KOREAN_MARKER_TEXT", excerpt_translated="TRANSLATED_ENGLISH_MARKER_TEXT",
    original_language="Korean",
)
signal_card(signal)
"""


def test_signal_card_collapses_original_filing_behind_expander_when_translated():
    at = AppTest.from_string(_TRANSLATED_SIGNAL_CARD_SCRIPT, default_timeout=10)
    at.run()
    assert not at.exception

    all_text = " ".join(m.value for m in at.markdown)
    # Translation stays visible at the top level, not gated.
    assert "TRANSLATED_ENGLISH_MARKER_TEXT" in all_text
    # Original filing text is preserved (nothing deleted)...
    assert "ORIGINAL_KOREAN_MARKER_TEXT" in all_text
    # ...but now behind a collapsed expander.
    labels = [e.label for e in at.expander]
    assert "Show original filing" in labels


_UNTRANSLATED_SIGNAL_CARD_SCRIPT = """
from src.models.models import Direction, Horizon, Signal, Strength
from src.ui.components.cards import signal_card

signal = Signal(
    id="sig-phase-b-no-translation-test", title="8-K filing",
    theme_slug="ai-buildout", subtheme_slug=None, direction=Direction.EMERGING,
    strength=Strength.STRONG, horizon=Horizon.MULTI_WEEK, evidence_count=1,
    interpretation="Test interpretation.", contrary_evidence="", validation_criteria="",
    invalidation_criteria="", related_tickers=[], last_updated="2026-08-19T00:00:00+00:00",
    is_demo=False, issuer="AMD", source_name="SEC EDGAR",
    excerpt="ENGLISH_ONLY_MARKER_TEXT", excerpt_translated=None,
)
signal_card(signal)
"""


def test_signal_card_does_not_add_an_expander_when_there_is_no_translation():
    """No-translation case is untouched: the original excerpt (which is
    the only excerpt) stays inline, exactly as before this phase."""
    at = AppTest.from_string(_UNTRANSLATED_SIGNAL_CARD_SCRIPT, default_timeout=10)
    at.run()
    assert not at.exception

    all_text = " ".join(m.value for m in at.markdown)
    assert "ENGLISH_ONLY_MARKER_TEXT" in all_text
    labels = [e.label for e in at.expander]
    assert "Show original filing" not in labels



# --- UI infrastructure: mtime-keyed CSS cache ---

def test_css_text_cache_reflects_file_edits_via_mtime_key():
    import time

    from src.ui import ui as ui_module

    original = ui_module._CSS_PATH.read_text(encoding="utf-8")
    try:
        first = ui_module._css_text()
        assert first == original

        # Touch the file with new content and a forced mtime bump so a
        # fast filesystem clock can't coincidentally reuse the same
        # cache key.
        ui_module._CSS_PATH.write_text(original + "\n/* phase-b-cache-test */\n", encoding="utf-8")
        new_mtime = ui_module._CSS_PATH.stat().st_mtime + 5
        import os

        os.utime(ui_module._CSS_PATH, (new_mtime, new_mtime))

        second = ui_module._css_text()
        assert "phase-b-cache-test" in second
        assert second != first
    finally:
        ui_module._CSS_PATH.write_text(original, encoding="utf-8")
        ui_module._css_text_cached.clear()
