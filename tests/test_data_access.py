"""Data-access tests: demo repositories satisfy their interfaces, real seed
data parses into the right shapes, and a missing/malformed seed file
degrades to an empty result rather than raising."""
from src.config.settings import get_settings
from src.data_access.container import AppContext, get_repositories
from src.data_access.demo.signal_repository import DemoSignalRepository
from src.data_access.interfaces import (
    CatalystRepository,
    EvidenceRepository,
    MarketDataProvider,
    ResearchAnswerProvider,
    SignalRepository,
    ThemeRepository,
    TickerRepository,
)
from src.data_access.loaders import load_seed_json


def test_get_repositories_returns_interface_implementations():
    ctx = get_repositories()
    assert isinstance(ctx, AppContext)
    assert isinstance(ctx.theme_repository, ThemeRepository)
    assert isinstance(ctx.ticker_repository, TickerRepository)
    assert isinstance(ctx.evidence_repository, EvidenceRepository)
    assert isinstance(ctx.catalyst_repository, CatalystRepository)
    assert isinstance(ctx.signal_repository, SignalRepository)
    assert isinstance(ctx.market_data_provider, MarketDataProvider)
    assert isinstance(ctx.research_answer_provider, ResearchAnswerProvider)


def test_theme_repository_reads_five_fixed_themes():
    ctx = get_repositories()
    themes = ctx.theme_repository.get_all_themes()
    slugs = {t.slug for t in themes}
    assert slugs == {"ai-buildout", "humanoids", "space", "memory", "photonics"}
    assert all(len(t.subthemes) >= 1 for t in themes)


def test_theme_repository_get_theme_missing_returns_none():
    ctx = get_repositories()
    assert ctx.theme_repository.get_theme("not-a-real-theme") is None


def test_ticker_repository_only_has_demo_ticker():
    ctx = get_repositories()
    tickers = ctx.ticker_repository.get_all_tickers()
    assert len(tickers) == 1
    assert tickers[0].symbol == "DEMO"
    assert tickers[0].is_demo is True
    assert ctx.ticker_repository.get_ticker("demo").symbol == "DEMO"  # case-insensitive


def test_ticker_repository_filters_by_theme():
    ctx = get_repositories()
    assert len(ctx.ticker_repository.get_tickers_for_theme("photonics")) == 1
    assert ctx.ticker_repository.get_tickers_for_theme("space") == []


def test_evidence_repository_all_records_are_demo_with_no_url():
    ctx = get_repositories()
    evidence = ctx.evidence_repository.get_recent_evidence(limit=100)
    assert len(evidence) > 0
    for e in evidence:
        assert e.is_demo is True
        assert e.source_name == "EevaResearch Demo Data"
        assert e.source_url is None


def test_evidence_repository_filters_by_ticker_and_theme():
    ctx = get_repositories()
    assert all(e.ticker_symbol == "DEMO" for e in ctx.evidence_repository.get_evidence_for_ticker("DEMO"))
    assert all(e.theme_slug == "memory" for e in ctx.evidence_repository.get_evidence_for_theme("memory"))


def test_catalyst_repository_upcoming_sorted_by_date():
    ctx = get_repositories()
    upcoming = ctx.catalyst_repository.get_upcoming_catalysts(limit=100)
    dates = [c.date for c in upcoming]
    assert dates == sorted(dates)


def test_demo_signal_repository_filters_by_theme():
    """DemoSignalRepository itself, tested directly rather than via
    get_repositories() — the container now wires signal_repository to
    RadarSignalRepository (real DART/EDINET data), so this repository is
    no longer reachable through the container at all; it's exercised
    directly here purely to confirm the demo/seed data implementation
    still behaves correctly in isolation."""
    repo = DemoSignalRepository(get_settings())
    all_signals = repo.get_all_signals()
    photonics_signals = repo.get_signals_for_theme("photonics")
    assert len(photonics_signals) < len(all_signals)
    assert all(s.theme_slug == "photonics" for s in photonics_signals)


def test_market_data_provider_has_one_metric_per_theme():
    ctx = get_repositories()
    metrics = ctx.market_data_provider.get_rotation_metrics()
    assert {m.theme_slug for m in metrics} == {"ai-buildout", "humanoids", "space", "memory", "photonics"}
    assert ctx.market_data_provider.get_rotation_metric_for_theme("memory") is not None


def test_market_data_provider_no_fabricated_ticker_leaders():
    ctx = get_repositories()
    for m in ctx.market_data_provider.get_rotation_metrics():
        assert set(m.leaders) <= {"DEMO"}
        assert set(m.laggards) <= {"DEMO"}


def test_research_answer_provider_matches_suggested_questions():
    ctx = get_repositories()
    questions = ctx.research_answer_provider.get_suggested_questions()
    assert len(questions) == 5
    answer = ctx.research_answer_provider.get_answer(questions[0])
    assert answer.question == questions[0]
    assert answer.is_demo is True


def test_research_answer_provider_falls_back_for_unknown_question():
    ctx = get_repositories()
    answer = ctx.research_answer_provider.get_answer("some question nobody seeded")
    assert answer.is_demo is True
    assert "Demo mode" in answer.what_happened


def test_load_seed_json_missing_file_returns_default_not_raises():
    result = load_seed_json("/nonexistent/dir", "missing.json", default=[])
    assert result == []
