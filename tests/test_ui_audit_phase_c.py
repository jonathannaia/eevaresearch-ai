"""UI/product audit Phase C (design/DECISIONS.md) — "simpler editorial
product UX" pass on Dashboard, Research, and Radar Inbox: one clear
primary action above the fold per page, progressive disclosure for
operational/secondary detail, and evidence-taxonomy display quieted at
the answer level without losing rigor. Every test here is a pure
rendering/content/order check via AppTest — no data loading, navigation,
auth, worker, or database code is touched by this phase, and none of that
is exercised here."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.models.models import FilingEvent

HARNESS_DIR = Path(__file__).parent / "apptest_pages"
REPO_ROOT = Path(__file__).parent.parent


def _flatten(node, out: list[tuple[str, str | None]]) -> None:
    """Document-order (type, label) pairs for every leaf-ish element —
    structural containers (Block/SpecialBlock/Column) are skipped since
    they carry no label of their own, but their children are still
    visited in order. st.link_button has no dedicated AppTest element
    class in this Streamlit version — it's an UnknownElement with
    type=="link_button" (confirmed via introspection), so that combo is
    special-cased to its own "LinkButton" marker for readable assertions."""
    cls = type(node).__name__
    if cls == "UnknownElement" and getattr(node, "type", "") == "link_button":
        out.append(("LinkButton", getattr(node, "label", None)))
    elif cls not in ("Block", "SpecialBlock", "Column", "UnknownElement"):
        out.append((cls, getattr(node, "label", None) or getattr(node, "value", None)))
    children = getattr(node, "children", None)
    if children:
        for k in sorted(children.keys(), key=lambda x: (isinstance(x, str), x)):
            _flatten(children[k], out)


def _ordered(at) -> list[tuple[str, str | None]]:
    out: list[tuple[str, str | None]] = []
    _flatten(at.main, out)
    return out


def _text_excluding_stylesheet(at) -> str:
    """Every page's markdown list includes the full compiled stylesheet as
    its own element (loaded once via src/ui/ui.py::load_css()) — its CSS
    source text would otherwise false-positive-match class-name substring
    checks against the page's actual rendered content."""
    return " ".join(m.value for m in at.markdown if not m.value.startswith("<style>"))


def _index_of(ordered: list[tuple[str, str | None]], cls: str, contains: str) -> int:
    for i, (c, label) in enumerate(ordered):
        if c == cls and label is not None and contains in str(label):
            return i
    raise AssertionError(f"no {cls} containing {contains!r} found in {ordered}")


# ============================== DASHBOARD ==============================

def _run_dashboard():
    at = AppTest.from_file(str(HARNESS_DIR / "dashboard_page.py"), default_timeout=15)
    at.run()
    return at


def test_dashboard_has_no_market_overview_page_title():
    at = _run_dashboard()
    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    assert "Market Overview" not in all_text
    assert "Theme leadership, signals, catalysts, and watchlist changes." not in all_text


def test_dashboard_todays_read_is_the_first_section_on_the_page():
    at = _run_dashboard()
    ordered = _ordered(at)
    # First Markdown after the stylesheet block itself.
    first_content_index = next(i for i, (c, v) in enumerate(ordered) if c == "Markdown" and v and "<style>" not in v)
    assert "Today" in str(ordered[first_content_index][1])


def test_dashboard_has_no_dividers_between_modules():
    at = _run_dashboard()
    assert at.get("divider") == []


def test_dashboard_primary_and_secondary_cta_are_visually_distinct():
    at = _run_dashboard()
    all_text = " ".join(m.value for m in at.markdown)
    # Primary: filled midnight-blue pill with the shared glow recipe.
    assert 'href="#capital-rotation-snapshot"' in all_text
    primary_start = all_text.index('href="#capital-rotation-snapshot"')
    primary_chunk = all_text[primary_start : primary_start + 400]
    assert "background:var(--invert-bg)" in primary_chunk
    assert "box-shadow" in primary_chunk
    # Secondary: quiet ghost text link — no fill, no border, no glow.
    assert 'href="#priority-signals"' in all_text
    ghost_start = all_text.index('href="#priority-signals"')
    ghost_chunk = all_text[ghost_start : ghost_start + 400]
    assert "background:var(--invert-bg)" not in ghost_chunk
    assert "box-shadow" not in ghost_chunk
    assert "color:var(--text-3)" in ghost_chunk


def test_dashboard_theme_health_uses_direction_dot_not_status_pill():
    at = _run_dashboard()
    all_text = _text_excluding_stylesheet(at)
    # er-status-tag was the tinted-pill treatment this phase replaces with
    # the shared direction_dot_html glyph+text marker (er-dir/er-dir-glyph)
    # — nothing else on Dashboard renders er-status-tag as an actual
    # element (the class's own CSS *definition* is excluded above).
    assert "er-status-tag" not in all_text
    assert all_text.count("er-dir-glyph") >= 5  # one per theme in Theme Health


def test_dashboard_breadth_measure_is_labeled():
    at = _run_dashboard()
    all_text = " ".join(m.value for m in at.markdown)
    assert all_text.count(">Breadth<") == 5  # one explicit label per Theme Health card


def test_dashboard_keeps_every_module_and_priority_signals_is_no_longer_emphasized():
    """Phase C's product rule (one clear primary element per page) demotes
    Priority Signals back to the same standard weight as Capital
    Rotation/Catalysts — Today's Read is the page's one primary element."""
    at = _run_dashboard()
    all_text = " ".join(m.value for m in at.markdown)
    for heading in ("Today's Read", "Theme Health", "Priority Signals", "Capital Rotation", "Next Catalysts", "Watchlist Changes"):
        assert heading in all_text
    todays_read_heavy = '<div class="er-section-label" style="color:var(--text); font-weight:600; font-size:0.92rem;">Today\'s Read</div>'
    assert todays_read_heavy in all_text
    assert '>Priority Signals</div>' in all_text or 'Priority Signals' in all_text
    assert 'style="color:var(--text); font-weight:600; font-size:0.92rem;">Priority Signals' not in all_text


# ============================== RESEARCH ==============================

def _run_research():
    at = AppTest.from_file(str(HARNESS_DIR / "research_page.py"), default_timeout=15)
    at.run()
    return at


def test_research_has_no_duplicate_thread_start_paths():
    at = _run_research()
    all_text = " ".join(m.value for m in at.markdown)
    assert "Start a research thread" not in all_text
    assert "New thread" not in all_text
    button_labels = {b.label for b in at.button}
    assert "New thread" not in button_labels


def test_research_shows_one_clear_prompt_with_input_directly_beneath_it():
    at = _run_research()
    all_text = " ".join(m.value for m in at.markdown)
    assert "What would you like to investigate?" in all_text
    assert len(at.text_input) == 1
    assert at.text_input[0].placeholder == "Ask about a company, theme, filing, catalyst, or market move."
    assert "Ask" in {b.label for b in at.button}

    ordered = _ordered(at)
    prompt_idx = _index_of(ordered, "Markdown", "What would you like to investigate?")
    input_idx = next(i for i, (c, _) in enumerate(ordered) if c == "TextInput")
    ask_idx = _index_of(ordered, "Button", "Ask")
    suggested_idx = _index_of(ordered, "Markdown", "Or try one of these")
    assert prompt_idx < input_idx < ask_idx < suggested_idx


def test_research_suggested_questions_render_as_tertiary_ghost_links():
    at = _run_research()
    # Same shared cta-tertiary-* treatment as every other low-emphasis
    # action in the app, not a large bordered button competing with the
    # input — verified at the container-key level (Streamlit turns this
    # into a st-key-cta-tertiary-suggested-* class picked up by the
    # existing generic ghost-link CSS rule).
    keys = {b.key for b in at.button}
    assert any(k and k.startswith("suggested-") for k in keys)


def test_research_answer_shows_one_quiet_metadata_line_not_three_pills():
    at = _run_research()
    suggested_button = next(b for b in at.button if b.label == "What is happening in AI networking and photonics?")
    suggested_button.click().run()
    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    # The old per-answer badges rendered via st.badge, which serializes as
    # literal ":gray-badge[...]" markdown directives — gone now that this
    # is one plain muted text line instead.
    assert ":gray-badge[Confidence" not in all_text
    assert "Confidence:" in all_text
    assert "Freshness:" in all_text


_TRANSLATED_SPINE_SCRIPT = """
from src.models.models import ClaimType
from src.ui.components.evidence_spine import evidence_spine_row

evidence_spine_row(
    "MATCHING_CLAIM_TEXT", ClaimType.INTERPRETATION, has_source=True,
    is_first=True, is_last=False, answer_claim_type=ClaimType.INTERPRETATION,
)
evidence_spine_row(
    "EXCEPTION_CLAIM_TEXT", ClaimType.UNCERTAINTY, has_source=True,
    is_first=False, is_last=True, answer_claim_type=ClaimType.INTERPRETATION,
)
"""


def test_evidence_spine_row_suppresses_chip_only_when_claim_matches_answer_type():
    at = AppTest.from_string(_TRANSLATED_SPINE_SCRIPT, default_timeout=10)
    at.run()
    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    # Matching claim: no repeated "Interpretation" chip.
    matching_idx = all_text.index("MATCHING_CLAIM_TEXT")
    matching_row = all_text[max(0, matching_idx - 200) : matching_idx]
    assert "er-chip" not in matching_row
    # Exception claim (Uncertainty inside an Interpretation answer): its
    # own chip still renders.
    exception_idx = all_text.index("EXCEPTION_CLAIM_TEXT")
    exception_row = all_text[max(0, exception_idx - 200) : exception_idx]
    assert "er-chip" in exception_row
    assert "Uncertainty" in exception_row


def test_evidence_spine_row_default_behavior_unchanged_when_answer_type_not_passed():
    """No `answer_claim_type` passed (the pre-Phase-C default) — chip
    always renders, exactly as before this parameter existed."""
    script = """
from src.models.models import ClaimType
from src.ui.components.evidence_spine import evidence_spine_row

evidence_spine_row("NO_ANSWER_TYPE_CLAIM", ClaimType.FACT, has_source=True, is_first=True, is_last=True)
"""
    at = AppTest.from_string(script, default_timeout=10)
    at.run()
    assert not at.exception
    all_text = " ".join(m.value for m in at.markdown)
    idx = all_text.index("NO_ANSWER_TYPE_CLAIM")
    assert "er-chip" in all_text[max(0, idx - 200) : idx]


# ============================== RADAR INBOX ==============================

def _seed_corp_codes(cache_dir) -> None:
    # Both tracked DART companies must be resolved for dart_readiness.ready
    # to be True at all (see radar_service.radar_readiness) — matching
    # tests/test_radar_inbox_page.py's own fixture exactly.
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "005930": {"corp_code": "00126380", "corp_name": "삼성전자", "source": "OpenDART corpCode.xml", "retrieved_at": "2026-08-01T00:00:00+00:00"},
        "000660": {"corp_code": "00164779", "corp_name": "SK 하이닉스", "source": "OpenDART corpCode.xml", "retrieved_at": "2026-08-01T00:00:00+00:00"},
    }
    (cache_dir / "dart_corp_codes.json").write_text(json.dumps(payload), encoding="utf-8")


def _filing(rcept_no: str, report_nm: str) -> FilingEvent:
    return FilingEvent(
        rcept_no=rcept_no, corp_code="00126380", corp_name="삼성전자", stock_code="005930",
        report_nm=report_nm, rcept_dt="20260812", flr_nm="삼성전자", theme_slug="memory",
        source_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
    )


def _seed_filing_events(cache_dir, filings: list[FilingEvent]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {"seen_receipt_numbers": [f.rcept_no for f in filings], "filing_events": [asdict(f) for f in filings], "candidate_signals": []}
    (cache_dir / "dart_filing_events.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _run_radar(tmp_path, **settings_overrides):
    # edgar_user_agent/edinet_subscription_key explicitly nulled — Settings
    # falls back to os.getenv for any field not given here, and this
    # repo's real local .env has genuine values for both (Gate 5
    # test-isolation defect this pattern guards against elsewhere in the
    # suite; see tests/test_radar_inbox_page.py's own _unconfigured_settings).
    settings = Settings(
        dart_api_key="dart-key", translation_api_key="deepl-key",
        edgar_user_agent=None, edinet_subscription_key=None,
        cache_dir=tmp_path, **settings_overrides,
    )
    with patch("src.ui.pages.radar_inbox.get_settings", return_value=settings):
        at = AppTest.from_file(str(HARNESS_DIR / "radar_inbox_page.py"), default_timeout=15)
        at.run()
    return at


def test_radar_default_view_is_needs_your_decision_with_all_filings_beside_it(tmp_path):
    _seed_corp_codes(tmp_path)
    _seed_filing_events(tmp_path, [_filing("20260812000001", "일반 공고")])
    at = _run_radar(tmp_path)
    assert not at.exception
    radio = at.radio(key="radar-view-mode")
    assert radio.options == ["Needs your decision", "All filings"]
    assert radio.value == "Needs your decision"


def test_radar_ingestion_status_is_one_merged_collapsed_disclosure(tmp_path):
    _seed_corp_codes(tmp_path)
    _seed_filing_events(tmp_path, [_filing("20260812000002", "일반 공고")])
    at = _run_radar(tmp_path)
    assert not at.exception
    expander_titles = {e.label for e in at.expander}
    assert "Ingestion status" in expander_titles
    # The two previously-separate expanders no longer exist as such.
    assert "Data controls (local/admin)" not in expander_titles
    assert not any("Continuous worker status only" in t for t in expander_titles)
    # Content from both is preserved, just merged under the one header.
    all_text = " ".join(m.value for m in at.markdown)
    assert "Source scans can take time and are intended for local/admin use." in all_text
    assert "Continuous worker status" in all_text
    assert "not a candidate feed" in all_text


def test_radar_clear_all_filters_is_still_present_and_functional(tmp_path):
    _seed_corp_codes(tmp_path)
    _seed_filing_events(tmp_path, [_filing("20260812000003", "일반 공고")])
    at = _run_radar(tmp_path)
    # This filing has no CandidateSignal, so the default "Needs your
    # decision" view is empty — switch to "All filings" to reach the
    # filter row (same pattern as the existing Radar Inbox test suite).
    at.radio(key="radar-view-mode").set_value("All filings")
    at.run()
    assert not at.exception
    clear_buttons = [b for b in at.button if b.label == "Clear all filters"]
    assert len(clear_buttons) == 1
    assert clear_buttons[0].key == "radar-clear-filters-btn"


def test_radar_pagination_controls_render_after_results_not_above(tmp_path):
    _seed_corp_codes(tmp_path)
    filings = [_filing(f"2026081200{i:04d}", f"공시 {i}") for i in range(25)]
    _seed_filing_events(tmp_path, filings)
    at = _run_radar(tmp_path)
    at.radio(key="radar-view-mode").set_value("All filings")
    at.run()
    assert not at.exception

    ordered = _ordered(at)
    first_result_idx = next(i for i, (c, _) in enumerate(ordered) if c == "LinkButton")
    next_button_idx = _index_of(ordered, "Button", "Next →")
    page_summary_idx = next(
        i for i, (c, v) in enumerate(ordered) if c == "Markdown" and v and "Page 1 of" in str(v)
    )
    assert first_result_idx < next_button_idx
    assert first_result_idx < page_summary_idx
