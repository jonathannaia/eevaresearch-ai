"""Autonomous Research Agent, Phase 5 — the Verified Updates page
(design §9.2), through the same AppTest harness convention every other
page uses (tests/apptest_pages/verified_updates_page.py). Renders only
from a seeded verified_update_store against tmp_path; the flag gates it
closed by default; nothing here (or in the page) touches the agent."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from src.config.settings import Settings
from src.data_access import verified_update_store
from src.models.verified_update import PUBLISHED_BY_AUTONOMOUS_AGENT, VERIFIED_FILING_FACT, VerifiedUpdate
from src.ui.pages import verified_updates

_HARNESS = Path(__file__).parent / "apptest_pages" / "verified_updates_page.py"


@pytest.fixture(autouse=True)
def _never_touch_the_agent(monkeypatch):
    import src.mcp_agent.tools.request_publication_decision as decide
    monkeypatch.setattr(decide, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("page must never call the agent")))


def _update(update_id: str = "vu-1") -> VerifiedUpdate:
    return VerifiedUpdate(
        id=update_id, candidate_id="cand-1", headline="CoreWeave reports an all-NVIDIA GPU fleet", issuer="CoreWeave, Inc.",
        entity_id="coreweave", fact_statement="All of the GPUs in its infrastructure are NVIDIA GPUs.", source_label="SEC EDGAR 10-Q",
        source_date="2026-09-10", source_url="https://www.sec.gov/Archives/edgar/data/1769628/000176962826000042/",
        source_document_id="0001769628-26-000042", excerpt_or_locator="section:Item 2",
        what_this_does_not_establish="Does not establish future GPU sourcing, pricing terms, or exclusivity duration.",
        label=VERIFIED_FILING_FACT, published_at="2026-09-17T12:00:00+00:00", published_by=PUBLISHED_BY_AUTONOMOUS_AGENT,
        evidence_ids=("ev-1",), audit_session_id="sess-1", factual_context=("Prior 10-K also named NVIDIA.",),
    )


def _run(settings: Settings) -> AppTest:
    with patch("src.ui.pages.verified_updates.get_settings", return_value=settings):
        at = AppTest.from_file(str(_HARNESS), default_timeout=10)
        at.run()
    assert not at.exception, at.exception
    return at


def _text(at: AppTest) -> str:
    """Rendered text only — the with_chrome() shell injects assets/styles.css
    as a <style> markdown element, which is excluded so its comments never
    masquerade as page content."""
    parts = []
    for kind in ("markdown", "caption", "info", "subheader", "text"):
        for el in at.get(kind):
            value = str(getattr(el, "value", ""))
            if not value.lstrip().startswith("<style"):
                parts.append(value)
    return "\n".join(parts)


def test_enabled_page_renders_each_verified_update_as_a_narrow_fact_card(tmp_path):
    verified_update_store.append_verified_updates(tmp_path, (_update(),))
    text = _text(_run(Settings(cache_dir=tmp_path, verified_updates_page_enabled=True)))
    assert VERIFIED_FILING_FACT in text
    assert "CoreWeave reports an all-NVIDIA GPU fleet" in text
    assert "All of the GPUs in its infrastructure are NVIDIA GPUs." in text
    assert "0001769628-26-000042" in text and "section:Item 2" in text
    assert "Prior 10-K also named NVIDIA." in text
    assert f"{verified_updates.DOES_NOT_ESTABLISH_LABEL}:* Does not establish future GPU sourcing" in text
    assert "ev-1" in text and PUBLISHED_BY_AUTONOMOUS_AGENT in text and "sess-1" in text
    for judgment in ("Direction", "Strength", "Horizon", "Interpretation"):
        assert judgment not in text


def test_page_is_closed_by_default_and_renders_nothing_from_the_store(tmp_path):
    verified_update_store.append_verified_updates(tmp_path, (_update(),))
    text = _text(_run(Settings(cache_dir=tmp_path)))
    assert verified_updates.NOT_ENABLED_TEXT in text
    assert "CoreWeave reports an all-NVIDIA GPU fleet" not in text


def test_enabled_page_with_an_empty_store_shows_the_empty_state(tmp_path):
    text = _text(_run(Settings(cache_dir=tmp_path, verified_updates_page_enabled=True)))
    assert verified_updates.EMPTY_TEXT in text
    assert VERIFIED_FILING_FACT not in text
