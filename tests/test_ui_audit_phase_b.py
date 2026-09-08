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

from streamlit.testing.v1 import AppTest

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
    assert "25 discovery proposals — not active coverage" in labels
    assert "4 known category conflicts" in labels


# --- Signals: Direction/Time horizon filters hidden at a single option ---

def test_signals_page_hides_single_option_direction_and_horizon_filters():
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
