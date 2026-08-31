"""Coverage page — AppTest-level render checks. Runs against the isolated
harness in tests/apptest_pages/coverage_page.py (same pattern as every
other page's AppTest suite) — no live server, no cache/network access; the
page reads only the static Issuer Registry already covered in
test_issuer_coverage.py."""
from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

_HARNESS = Path(__file__).parent / "apptest_pages" / "coverage_page.py"


def _run():
    at = AppTest.from_file(str(_HARNESS), default_timeout=15)
    at.run()
    return at


def test_coverage_page_renders_without_exception():
    at = _run()
    assert not at.exception


def test_coverage_page_shows_title_and_static_view_note():
    at = _run()
    all_text = " ".join(m.value for m in at.markdown)
    assert "Coverage" in all_text
    assert "Registry view — static local configuration" in all_text
    assert "not a live scan-status feed" in all_text


def test_coverage_page_summary_metrics_match_registry_counts():
    at = _run()
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Active seed issuers"] == "32"
    assert metrics["Discovery proposals"] == "25"
    assert metrics["Scan-eligible"] == "32"
    assert metrics["Unverified / excluded"] == "25"


def test_coverage_page_shows_both_seed_and_discovery_tables_with_expected_row_counts():
    at = _run()
    assert len(at.dataframe) == 2
    seed_table, discovery_table = at.dataframe[0].value, at.dataframe[1].value
    assert len(seed_table) == 32
    assert len(discovery_table) == 25


def test_coverage_page_seed_table_never_contains_a_discovery_stub_name():
    at = _run()
    seed_table = at.dataframe[0].value
    seed_companies = set(seed_table["Company"])
    assert "Boost Run" not in seed_companies
    assert "FOCI" not in seed_companies


def test_coverage_page_discovery_table_shows_not_eligible_and_discovered():
    at = _run()
    discovery_table = at.dataframe[1].value
    assert set(discovery_table["Scan eligibility"]) == {"Not eligible"}
    assert set(discovery_table["Coverage state"]) == {"Discovered"}


def test_coverage_page_seed_table_shows_eligible_only():
    at = _run()
    seed_table = at.dataframe[0].value
    assert set(seed_table["Scan eligibility"]) == {"Eligible"}


def test_coverage_page_discovery_queue_is_inside_an_expander():
    # AppTest on this Streamlit version doesn't expose an expander's
    # expanded/collapsed state directly — this confirms the discovery
    # queue is wrapped in one at all (the collapsed-by-default behavior
    # itself is set explicitly in coverage.py's own st.expander(...,
    # expanded=False) call).
    at = _run()
    labels = [e.label for e in at.expander]
    assert any("discovery proposals" in label for label in labels)


def test_coverage_page_shows_known_category_conflicts_and_derived_notes():
    at = _run()
    all_text = " ".join(m.value for m in at.markdown)
    assert "MRVL" in all_text
    assert "TSEM" in all_text
    assert "Kioxia" in all_text
    assert "networking-interconnect" in all_text or "interconnect-switching" in all_text
    assert "Taiwan" in all_text and "Germany" in all_text and "Sweden" in all_text
    assert "BURUN" in all_text and "SHT.ST" in all_text and "P4O" in all_text


def test_coverage_page_has_no_action_buttons():
    # Observability-only, unlike Radar Inbox — no scan/process/publish/
    # monitor/exclude control of any kind should exist on this page.
    at = _run()
    button_labels = {b.label for b in at.button}
    forbidden_substrings = ("scan", "process", "publish", "monitor", "exclude", "add to coverage")
    for label in button_labels:
        lowered = label.lower()
        assert not any(f in lowered for f in forbidden_substrings), f"unexpected action button: {label!r}"
