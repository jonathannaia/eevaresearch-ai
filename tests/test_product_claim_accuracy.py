"""Reader-facing product truthfulness pass — the doc pages (Methodology,
About, Disclaimer) and the Radar Signals header must describe only
capabilities this build actually has.

Each test names the code that settles the claim, so a future change to
the capability fails the copy test rather than silently making the copy
false:

  * filing venues — src/logic/market_map.REGION_SOURCE (the same mapping
    the live Dashboard regional brief uses), and the data_access package
    list, which holds an adapter for exactly edgar/dart/edinet;
  * demo vs live — src/data_access/container.py (market pricing is
    DemoMarketDataProvider), scripts/radar_worker.py and
    scripts/daily_news_worker.py (live scheduled ingestion);
  * summaries — src/data_access/daily_news/summary_grounding.py
    (extractive, no model);
  * novelty — src/logic/signal_decision_policy.py (states in its own
    reason strings that it does not verify novelty);
  * review/publication — the worker's structural auto-publish=False and
    src/logic/agent_mode.RELEASE_MAX_MODE;
  * freshness indicators — src/logic/radar_freshness.py and
    src/logic/daily_news_freshness.py are the only two, and
    src/ui/components/freshness.py now has no caller.

Pure rendering and source-inspection checks only. No fetch, worker,
pipeline, admission, ranking, or publication behavior is exercised.
"""
from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.data_access.container import get_repositories
from src.data_access.daily_news.summary_grounding import generate_summary
from src.data_access.translation.deepl_provider import DeepLProvider
from src.logic.agent_mode import RELEASE_MAX_MODE
from src.logic.market_map import REGION_SOURCE, REGIONS_ALL
from src.ui.pages import about as about_page
from src.ui.pages import daily_news as daily_news_page
from src.ui.pages import methodology as methodology_page
from src.ui.pages.about import count_word
from src.ui.components.radar_status import evidence_review_label

_PAGES = Path(__file__).parent / "apptest_pages"
_REPO_ROOT = Path(__file__).parent.parent

_METHODOLOGY = _PAGES / "methodology_page.py"
_ABOUT = _PAGES / "about_page.py"
_DISCLAIMER = _PAGES / "disclaimer_page.py"
_SIGNALS = _PAGES / "signals_page.py"

# Named in the copy before this pass, with no adapter anywhere in the
# codebase: src/data_access/ has edgar/, dart/ and edinet/ and nothing
# else, and regional_brief.py renders China as "not connected" for
# exactly this reason.
_VENUES_WITHOUT_AN_ADAPTER = ("TDnet", "CNINFO", "HKEX", "HKEXnews")


def _text(path: Path) -> str:
    at = AppTest.from_file(str(path), default_timeout=20)
    at.run()
    assert not at.exception, path.name
    return " ".join(m.value for m in at.main.get("markdown") if not m.value.startswith("<style>"))


def _markdowns(path: Path) -> list[str]:
    at = AppTest.from_file(str(path), default_timeout=20)
    at.run()
    assert not at.exception, path.name
    return [m.value for m in at.main.get("markdown") if not m.value.startswith("<style>")]


def _page_source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8")



# ============================================================
# Venues — only the ones with an adapter.
# ============================================================


def test_no_doc_page_names_a_filing_venue_without_an_adapter():
    for path in (_METHODOLOGY, _ABOUT, _DISCLAIMER):
        text = _text(path)
        for venue in _VENUES_WITHOUT_AN_ADAPTER:
            assert venue.lower() not in text.lower(), (path.name, venue)


def test_the_only_adapters_in_the_codebase_are_the_ones_the_copy_names():
    """If a fourth adapter package ever lands, this fails and the copy
    (which derives its list from REGION_SOURCE) has to be revisited
    together with that mapping."""
    data_access = _REPO_ROOT / "src" / "data_access"
    adapter_dirs = {
        child.name for child in data_access.iterdir()
        if child.is_dir() and (child / "scan_service.py").exists()
    }

    assert adapter_dirs == {"edgar", "dart", "edinet"}
    assert len(REGION_SOURCE) == len(adapter_dirs)


def test_methodology_names_every_connected_venue_from_the_registry():
    text = _text(_METHODOLOGY)

    for source in REGION_SOURCE.values():
        assert source in text, source
    for region in REGION_SOURCE:
        assert region in text, region


def test_about_names_every_connected_venue_from_the_registry():
    text = _text(_ABOUT)

    for source in REGION_SOURCE.values():
        assert source in text, source


def test_methodology_states_an_uncovered_region_as_not_connected():
    uncovered = [region for region in REGIONS_ALL if region not in REGION_SOURCE]
    assert uncovered, "REGIONS_ALL no longer carries a region without coverage"

    text = _text(_METHODOLOGY)
    for region in uncovered:
        assert region in text
    assert "not connected" in text.lower()


def test_the_rendered_venue_list_follows_the_registry():
    """Behavioural proof that the copy is derived: change the registry
    the page reads and the sentence changes with it, naming the new
    venues and no longer naming the old ones."""
    fake = {"Atlantis": "ATL-DISCLOSE", "Narnia": "NAR-FILE"}

    with patch.object(methodology_page, "REGION_SOURCE", fake), patch.object(
        methodology_page, "REGIONS_ALL", (*fake, "Elsewhere"),
    ):
        text = _text(_METHODOLOGY)

    assert "ATL-DISCLOSE (Atlantis)" in text
    assert "NAR-FILE (Narnia)" in text
    assert "SEC EDGAR" not in text
    # And the uncovered region still reports as not connected.
    assert "Elsewhere" in text
    assert "not connected" in text.lower()


def test_the_rendered_venue_list_follows_the_registry_on_about_too():
    fake = {"Atlantis": "ATL-DISCLOSE"}

    with patch.object(about_page, "REGION_SOURCE", fake):
        text = _text(_ABOUT)

    assert "ATL-DISCLOSE" in text
    assert "SEC EDGAR" not in text


# ============================================================
# Demo vs live.
# ============================================================


def test_market_pricing_is_still_the_demo_provider_so_the_copy_must_say_so():
    """The claim being tested is "pricing is illustrative". It holds only
    while the container wires the demo provider — if that changes, this
    test fails and the copy should be updated, not the assertion."""
    assert type(get_repositories().market_data_provider).__name__ == "DemoMarketDataProvider"

    for path in (_METHODOLOGY, _ABOUT, _DISCLAIMER):
        assert "illustrative" in _text(path).lower(), path.name


def test_no_doc_page_claims_the_whole_build_is_demo_or_has_no_live_source():
    forbidden = (
        "every filing referenced is demo data",
        "no live filing feed is connected",
        "no live source is connected",
        "demo mode is active",
        "mock/demo data only",
        "no live news ingestion",
        "every figure and filing shown is demo data",
    )
    for path in (_METHODOLOGY, _ABOUT, _DISCLAIMER):
        text = _text(path).lower()
        for claim in forbidden:
            assert claim not in text, (path.name, claim)


def test_the_live_ingestion_workers_the_copy_relies_on_exist():
    assert (_REPO_ROOT / "scripts" / "radar_worker.py").exists()
    assert (_REPO_ROOT / "scripts" / "daily_news_worker.py").exists()

    for path in (_METHODOLOGY, _ABOUT, _DISCLAIMER):
        assert "schedule" in _text(path).lower(), path.name


# ============================================================
# Summaries — extractive, never model-written.
# ============================================================


def test_no_doc_page_claims_generated_analysis_or_an_ai_analyst():
    forbidden = (
        "ai analyst", "our ai ", "written by a model", "generated summary", "generates a summary",
        "llm", "gpt", "chatbot", "ask our ai",
    )
    for path in (_METHODOLOGY, _ABOUT, _DISCLAIMER):
        text = _text(path).lower()
        for claim in forbidden:
            assert claim not in text, (path.name, claim)


def test_the_summary_path_is_still_extractive_so_the_copy_must_say_so():
    """Behavioural premise: the summary is a span of the source's own
    text, so every word of it appears in the input."""
    description = "Acme reported record quarterly revenue of $4.2 billion. Shipments rose across every region."
    result = generate_summary("Acme Reports Results", description, has_valid_source_url=True)

    assert result.eeva_summary
    assert result.eeva_summary in description

    text = _text(_ABOUT).lower()
    assert "extract of the source's own words" in text


def test_extractive_summaries_and_machine_translation_are_distinguished():
    """Two different mechanisms with two different obligations: the
    summary is the publisher's own words (no model), while a translated
    headline is machine-generated text. The earlier blanket "no model
    writes anything you read here" was wrong about the second.

    Behavioural premise for both: the summary is a span of its input, and
    the page really is wired to a machine-translation provider."""
    description = "Acme opened a second plant in Ohio. Capacity doubles next year."
    assert generate_summary("Acme Expands", description, has_valid_source_url=True).eeva_summary in description
    assert daily_news_page.DeepLProvider is DeepLProvider

    text = _text(_DISCLAIMER).lower()
    assert "no model writes anything" not in text
    # Extractive.
    assert "is extractive" in text
    assert "nothing is rewritten and no model is involved" in text
    # Machine translation, named as such.
    assert "machine translation" in text
    assert "machine-generated text" in text
    assert "original-language headline stays the authoritative version" in text


def test_no_doc_page_claims_a_shipped_language_model_analysis_or_customer_api():
    """Neither exists: no module calls a language model, and no HTTP API
    is served to customers anywhere in this repo. Planned work must not
    be described as shipped."""
    text = " ".join(_text(path).lower() for path in (_METHODOLOGY, _ABOUT, _DISCLAIMER))

    for unshipped in (
        "our api", "customer api", "public api", "api key for", "api access",
        "ai-generated analysis", "model-written", "llm analysis", "ai analysis",
    ):
        assert unshipped not in text, unshipped
    assert "no filing is summarized, interpreted, or analyzed by a language model" in text


# ============================================================
# Novelty and review.
# ============================================================


def test_no_doc_page_claims_a_novelty_check_against_prior_coverage():
    for path in (_METHODOLOGY, _ABOUT, _DISCLAIMER):
        text = _text(path).lower()
        assert "checked against prior coverage" not in text, path.name
        assert "if the material has already been reported, it is not a signal" not in text, path.name


def test_methodology_states_what_the_repeat_checks_actually_do():
    text = _text(_METHODOLOGY).lower()

    assert "detection categories" in text
    assert "no check against outside press coverage" in text


def test_review_state_is_described_as_unmarked_not_as_uninspected():
    """Behavioural premise: the review label is driven entirely by
    whether `reviewed_at` was set — a record of being MARKED reviewed,
    carrying no information about whether a person looked at the item.
    So the copy may claim neither."""
    from src.models.models import CandidateStatus

    # evidence_review_label() reads exactly two fields; a stand-in
    # carrying only those exercises precisely the semantics under test.
    unmarked = SimpleNamespace(status=CandidateStatus.MONITORING, reviewed_at="")
    marked = SimpleNamespace(status=CandidateStatus.MONITORING, reviewed_at="2026-11-01T00:00:00+00:00")
    needs_review = SimpleNamespace(status=CandidateStatus.NEEDS_REVIEW, reviewed_at="")

    assert evidence_review_label(unmarked) == "Not yet reviewed"
    assert evidence_review_label(marked) == "Reviewed"
    assert evidence_review_label(needs_review) == "Manual review needed"

    text = _text(_DISCLAIMER).lower()
    assert "has not been marked reviewed" in text
    assert "not evidence that nobody has looked at it" in text
    # The previous, overreaching formulation must not come back.
    assert "nobody has reviewed it yet" not in text
    for overclaim in ("reviewed by an analyst", "analyst-reviewed", "human-verified", "expert review"):
        assert overclaim not in text


def test_contrary_evidence_copy_does_not_imply_an_automated_search():
    promotion = _page_source("src/logic/signal_promotion.py")
    assert 'contrary_evidence="Not yet reviewed."' in promotion

    text = _text(_METHODOLOGY).lower()
    assert "nothing searches for contrary evidence automatically" in text


# ============================================================
# Autonomy.
# ============================================================


def test_the_daily_news_path_publishes_unattended_so_the_copy_must_say_so():
    """Behavioural premise: the Daily News story lifecycle has no review
    state at all — PUBLISHED is reachable from the pipeline's own
    statuses, and no status in that enum represents awaiting review. A
    product-wide "nothing is published autonomously" would be false."""
    from src.models.daily_news_models import NewsStoryStatus

    assert {s.value for s in NewsStoryStatus} == {"Discovered", "Summarized", "Published", "Suppressed"}
    assert not [s for s in NewsStoryStatus if "review" in s.value.lower()]

    text = _text(_DISCLAIMER).lower()
    assert "nothing is published autonomously" not in text
    assert "published unattended" in text
    assert "no human review step anywhere in that path" in text


def test_the_filing_derived_path_is_described_as_review_gated_and_separately():
    """Behavioural premise: the filing lifecycle DOES carry a review
    state, and the agent's release ceiling forbids publishing."""
    from src.models.models import CandidateStatus

    assert CandidateStatus.NEEDS_REVIEW in CandidateStatus
    assert RELEASE_MAX_MODE == "shadow"

    text = _text(_DISCLAIMER).lower()
    assert "the filing-derived path is different" in text
    assert "routed to a review state" in text
    assert "cannot publish at all" in text


def test_the_two_publication_paths_are_not_conflated_into_one_claim():
    """One sentence covering both would be wrong about one of them. The
    unattended claim and the review-gated claim must be separate
    statements."""
    markdowns = [m for m in _markdowns(_DISCLAIMER) if "unattended" in m.lower() or "review state" in m.lower()]

    unattended = [m for m in markdowns if "published unattended" in m.lower()]
    review_gated = [m for m in markdowns if "routed to a review state" in m.lower()]
    assert len(unattended) == 1
    assert len(review_gated) == 1
    assert unattended[0] != review_gated[0]


def test_disclaimer_no_longer_denies_the_autonomy_that_does_exist():
    """The previous copy said "no autonomous research loops" while two
    scheduled workers were running. It must not deny them."""
    text = _text(_DISCLAIMER).lower()

    assert "no autonomous research loops" not in text
    assert "unattended in the background" in text


def test_disclaimer_discloses_the_one_third_party_service_that_is_wired():
    assert daily_news_page.DeepLProvider is DeepLProvider

    text = _text(_DISCLAIMER)
    assert "DeepL" in text
    assert "no paid apis" not in text.lower()


# ============================================================
# Capabilities a reader is pointed at must exist.
# ============================================================


def test_about_does_not_point_a_reader_at_the_removed_research_page():
    """The Research chat page was removed outright. Behavioural premise:
    the module cannot be imported, so no route can render it."""
    import importlib.util

    assert importlib.util.find_spec("src.ui.pages.research") is None

    text = _text(_ABOUT).lower()
    assert "use research to ask a question" not in text
    assert "no question-answering assistant" in text


def test_no_doc_page_claims_a_conversational_agent():
    for path in (_METHODOLOGY, _ABOUT, _DISCLAIMER):
        text = _text(path).lower()
        assert "conversational agent" not in text, path.name


def test_about_theme_count_matches_the_repository_it_renders():
    """Derived from the repository the page itself renders, and spelled
    as a word so the sentence reads as prose. Seed a different number of
    themes and both the heading and the sentence follow."""
    expected = len(get_repositories().theme_repository.get_all_themes())
    text = _text(_ABOUT)

    assert f"{count_word(expected).capitalize()} themes" in text
    assert f"{count_word(expected)} technology investment themes" in text.lower()


def test_the_rendered_theme_count_follows_the_repository():
    """Behavioural proof the count is derived: render with a three-theme
    repository and the copy says three, in both places."""
    real = get_repositories()
    three = real.theme_repository.get_all_themes()[:3]
    assert len(three) == 3

    class _ThreeThemes:
        def get_all_themes(self):
            return three

    with patch.object(about_page, "get_repositories", return_value=replace(real, theme_repository=_ThreeThemes())):
        text = _text(_ABOUT)

    assert "Three themes" in text
    assert "three technology investment themes" in text
    assert "Five themes" not in text


def test_count_word_spells_small_counts_and_falls_back_beyond_ten():
    assert count_word(3) == "three"
    assert count_word(5) == "five"
    assert count_word(0) == "zero"
    assert count_word(11) == "11"
    assert count_word(-1) == "-1"


# ============================================================
# Freshness indicators — only the two that exist.
# ============================================================


def test_no_doc_page_claims_a_three_state_indicator_on_every_panel():
    for path in (_METHODOLOGY, _DISCLAIMER):
        text = _text(path).lower()
        assert "every panel carries its own fetch timestamp" not in text, path.name
        assert "one of three states" not in text, path.name
        assert "freshness indicator on each panel" not in text, path.name


def test_methodology_describes_the_two_worker_derived_freshness_lines():
    assert (_REPO_ROOT / "src" / "logic" / "radar_freshness.py").exists()
    assert (_REPO_ROOT / "src" / "logic" / "daily_news_freshness.py").exists()

    text = _text(_METHODOLOGY).lower()
    assert "last successful scan" in text
    assert "unknown" in text
    assert "not every panel carries an indicator" in text


def test_methodology_denies_that_a_completed_run_establishes_coverage():
    text = _text(_METHODOLOGY).lower()

    assert "neither line establishes that every source was reached" in text
    assert "not that the picture is complete" in text
    assert "never presented here as a confirmed quiet period" in text


def test_the_radar_signals_page_still_renders_without_the_chip():
    text = _text(_SIGNALS)

    assert "Radar Signals" in text
    assert "Live ·" not in text


def test_the_render_time_freshness_component_has_no_caller_left():
    """freshness_chip()'s timestamp default is datetime.now() at render
    time. Nothing may present that as a data freshness claim, so the
    component must stay uncalled until a caller passes a real persisted
    fetch timestamp."""
    callers = []
    for path in (_REPO_ROOT / "src").rglob("*.py"):
        if path.name == "freshness.py":
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and "components.freshness" in (node.module or ""):
                callers.append(str(path.relative_to(_REPO_ROOT)))

    assert callers == []


# ============================================================
# No ingestion, admission, ranking, or publication behavior changed.
# ============================================================


def test_the_copy_pages_import_nothing_that_fetches_or_persists():
    forbidden = (
        "rss_atom_client", "daily_news_store", "editorial_story_store", "daily_news_pipeline",
        "editorial_pipeline", "editorial_admission", "editorial_matching", "signal_promotion",
        "theme_auto_publish",
        "requests", "feedparser", "psycopg",
    )
    for relative in (
        "src/ui/pages/methodology.py",
        "src/ui/pages/about.py",
        "src/ui/pages/disclaimer.py",
    ):
        tree = ast.parse(_page_source(relative), filename=relative)
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom) and node.module:
                module = node.module
            elif isinstance(node, ast.Import):
                module = ",".join(alias.name for alias in node.names)
            if not module:
                continue
            for name in forbidden:
                assert name not in module, (relative, module, name)


def test_rendering_the_copy_pages_leaves_the_registries_untouched():
    """Methodology derives its counts from the two Daily News registries.
    Behavioural: the exact same tuple objects, with the same contents,
    before and after a full render of both pages."""
    from src.data_access.daily_news.feed_registry import PILOT_FEEDS
    from src.data_access.daily_news.source_registry import EDITORIAL_SOURCE_REGISTRY

    before = (PILOT_FEEDS, EDITORIAL_SOURCE_REGISTRY, len(PILOT_FEEDS), len(EDITORIAL_SOURCE_REGISTRY))

    _text(_METHODOLOGY)
    _text(_ABOUT)

    from src.data_access.daily_news.feed_registry import PILOT_FEEDS as after_feeds
    from src.data_access.daily_news.source_registry import EDITORIAL_SOURCE_REGISTRY as after_editorial

    assert after_feeds is before[0]
    assert after_editorial is before[1]
    assert (len(after_feeds), len(after_editorial)) == (before[2], before[3])
