"""edgar_service — the SEC EDGAR pilot's wiring layer. edgar_readiness
and get_edgar_companies are pure config/cache reads, no network.
run_scan/process_candidate_now are thin wrappers whose core scan/
extraction behavior is already exercised end-to-end via
test_edgar_pipeline.py's mocked-client tests — the tests below cover
only Durable-State Phase 4A's addition to those two wrappers: the
optional, additive `candidate_repository` parameter and its threading
through to edgar_pipeline.run_pipeline/process_single_candidate. No test
here makes a real network call — either the pipeline-layer function is
monkeypatched directly, or edgar_service._client is replaced with a
MagicMock EdgarClient (same fixture shape as
tests/test_candidate_persistence_phase3a.py's own _make_edgar_client)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from src.config.settings import Settings
from src.config.tracked_companies import get_tracked_companies_for_source
from src.data_access import backend_factory
from src.data_access.edgar import edgar_service


def _settings(cache_dir, user_agent=None) -> Settings:
    return Settings(edgar_user_agent=user_agent, cache_dir=cache_dir)


def _seed_ciks(cache_dir, tickers: list[str]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        t: {"cik": f"000{i}045810", "company_name": "Test Co", "source": "test", "retrieved_at": "2026-08-17T00:00:00+00:00"}
        for i, t in enumerate(tickers)
    }
    (cache_dir / "edgar_ciks.json").write_text(json.dumps(payload), encoding="utf-8")


def test_readiness_reports_missing_user_agent_and_unresolved_companies(tmp_path):
    expected_names = {c.name for c in get_tracked_companies_for_source("SEC EDGAR")}

    readiness = edgar_service.edgar_readiness(_settings(tmp_path))

    assert not readiness.user_agent_configured
    assert set(readiness.unresolved_companies) == expected_names
    assert not readiness.ready


def test_readiness_ready_when_user_agent_present_and_all_companies_resolved(tmp_path):
    all_tickers = [c.krx_code for c in get_tracked_companies_for_source("SEC EDGAR")]
    _seed_ciks(tmp_path, all_tickers)

    readiness = edgar_service.edgar_readiness(_settings(tmp_path, user_agent="EevaResearch test@example.com"))

    assert readiness.user_agent_configured
    assert readiness.unresolved_companies == ()
    assert readiness.ready


def test_readiness_flags_partially_resolved_companies(tmp_path):
    _seed_ciks(tmp_path, ["NVDA"])

    readiness = edgar_service.edgar_readiness(_settings(tmp_path, user_agent="EevaResearch test@example.com"))

    assert "NVIDIA" not in readiness.unresolved_companies
    assert "Micron Technology" in readiness.unresolved_companies
    assert not readiness.ready


def test_get_edgar_companies_fills_in_resolved_ciks(tmp_path):
    _seed_ciks(tmp_path, ["NVDA"])

    companies = edgar_service.get_edgar_companies(tmp_path)

    by_ticker = {c.krx_code: c for c in companies}
    assert by_ticker["NVDA"].corp_code == "0000045810"
    assert by_ticker["MU"].corp_code is None


def test_get_edgar_companies_only_returns_edgar_source_companies(tmp_path):
    companies = edgar_service.get_edgar_companies(tmp_path)
    names = {c.name for c in companies}
    assert all(c.source == "SEC EDGAR" for c in companies)
    assert {"NVIDIA", "Micron Technology"}.issubset(names)
    assert "Samsung Electronics" not in names


# ---------------------------------------------------------------------------
# Durable-State Phase 4A — run_scan/process_candidate_now's additive,
# optional candidate_repository parameter, threaded through to
# edgar_pipeline.run_pipeline/process_single_candidate.
# ---------------------------------------------------------------------------


def test_run_scan_omits_candidate_repository_by_default(tmp_path, monkeypatch):
    captured = {}

    def _fake_run_pipeline(
        client,
        companies,
        cache_dir,
        lookback_days=None,
        max_candidates_to_process=None,
        candidate_repository=None,
        auto_publish_enabled=False,
    ):
        captured["candidate_repository"] = candidate_repository
        captured["auto_publish_enabled"] = auto_publish_enabled
        return "sentinel-report"

    monkeypatch.setattr(edgar_service.edgar_pipeline, "run_pipeline", _fake_run_pipeline)

    result = edgar_service.run_scan(_settings(tmp_path, user_agent="EevaResearch test@example.com"))

    assert result == "sentinel-report"
    assert captured["candidate_repository"] is None
    assert captured["auto_publish_enabled"] is False


def test_run_scan_passes_through_an_explicitly_supplied_repository(tmp_path, monkeypatch):
    captured = {}
    sentinel_repo = object()  # identity check only — no method on it is ever called this test

    def _fake_run_pipeline(
        client,
        companies,
        cache_dir,
        lookback_days=None,
        max_candidates_to_process=None,
        candidate_repository=None,
        auto_publish_enabled=False,
    ):
        captured["candidate_repository"] = candidate_repository
        captured["auto_publish_enabled"] = auto_publish_enabled
        return "sentinel-report"

    monkeypatch.setattr(edgar_service.edgar_pipeline, "run_pipeline", _fake_run_pipeline)

    edgar_service.run_scan(_settings(tmp_path, user_agent="EevaResearch test@example.com"), candidate_repository=sentinel_repo)

    assert captured["candidate_repository"] is sentinel_repo
    assert captured["auto_publish_enabled"] is False
    assert captured["auto_publish_enabled"] is False


def test_process_candidate_now_omits_candidate_repository_by_default(tmp_path, monkeypatch):
    captured = {}

    def _fake_process_single_candidate(
        client,
        candidate_id,
        cache_dir,
        candidate_repository=None,
        auto_publish_enabled=False,
    ):
        captured["candidate_repository"] = candidate_repository
        captured["auto_publish_enabled"] = auto_publish_enabled
        return None

    monkeypatch.setattr(edgar_service.edgar_pipeline, "process_single_candidate", _fake_process_single_candidate)

    result = edgar_service.process_candidate_now(_settings(tmp_path, user_agent="EevaResearch test@example.com"), "edgar-cand-1")

    assert result is None
    assert captured["candidate_repository"] is None
    assert captured["auto_publish_enabled"] is False


def test_process_candidate_now_passes_through_an_explicitly_supplied_repository(tmp_path, monkeypatch):
    captured = {}
    sentinel_repo = object()

    def _fake_process_single_candidate(
        client,
        candidate_id,
        cache_dir,
        candidate_repository=None,
        auto_publish_enabled=False,
    ):
        captured["candidate_repository"] = candidate_repository
        captured["auto_publish_enabled"] = auto_publish_enabled
        return None

    monkeypatch.setattr(edgar_service.edgar_pipeline, "process_single_candidate", _fake_process_single_candidate)

    edgar_service.process_candidate_now(
        _settings(tmp_path, user_agent="EevaResearch test@example.com"), "edgar-cand-1", candidate_repository=sentinel_repo,
    )

    assert captured["candidate_repository"] is sentinel_repo


# --- Real end-to-end proof, one level up from edgar_pipeline.py's own
# Phase 3A equivalence tests: a mocked-client run_scan() call, with an
# injected synthetic SQLite repository, produces the same one candidate
# a default/omitted JSON-backed call would. ---

_NVDA_ACCESSION = "0001045810-26-000001"
_NVDA_DOC_BYTES = b"<html><body><p>Item 2.02 Results of Operations. Revenue rose.</p></body></html>"


def _edgar_recent(accession_numbers: list[str], filing_date: str) -> dict:
    n = len(accession_numbers)
    return {
        "accessionNumber": accession_numbers, "filingDate": [filing_date] * n,
        "form": ["8-K"] * n, "primaryDocument": ["doc.htm"] * n, "primaryDocDescription": ["desc"] * n,
    }


def _make_edgar_client(nvda_cik: str, filing_date: str) -> MagicMock:
    client = MagicMock()
    client.get_submissions.side_effect = lambda cik: (
        {"filings": {"recent": _edgar_recent([_NVDA_ACCESSION], filing_date)}} if cik == nvda_cik
        else {"filings": {"recent": _edgar_recent([], filing_date)}}
    )
    client.fetch_document.side_effect = lambda cik, accession_no, filename: _NVDA_DOC_BYTES
    return client


def test_run_scan_default_omitted_repository_persists_json_candidate_store(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    _seed_ciks(tmp_path, ["NVDA"])
    filing_date = datetime.now(timezone.utc).date().isoformat()
    monkeypatch.setattr(edgar_service, "_client", lambda settings: _make_edgar_client("0000045810", filing_date))
    settings = _settings(tmp_path, user_agent="EevaResearch test@example.com")

    report = edgar_service.run_scan(settings)

    assert report.candidates_detected == 1
    assert (tmp_path / "edgar_candidates.json").exists()


def test_run_scan_injected_sqlite_repository_produces_equivalent_candidate(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    _seed_ciks(tmp_path, ["NVDA"])
    filing_date = datetime.now(timezone.utc).date().isoformat()
    monkeypatch.setattr(edgar_service, "_client", lambda settings: _make_edgar_client("0000045810", filing_date))
    settings = Settings(
        edgar_user_agent="EevaResearch test@example.com", cache_dir=tmp_path,
        db_backend="sqlite", state_db_path=tmp_path / "state.db",
    )
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")

    report = edgar_service.run_scan(settings, candidate_repository=repo)

    assert report.candidates_detected == 1
    assert len(repo.load_candidates()) == 1
    # The SQLite path never touched the JSON candidate store this call.
    assert not (tmp_path / "edgar_candidates.json").exists()


# ---------------------------------------------------------------------------
# Source guard — this phase's modified files must never reference real
# local state (same pattern as test_backend_factory_phase2b.py's and
# test_candidate_persistence_phase3a.py's own guards).
# ---------------------------------------------------------------------------

_PHASE4A_FILES = (
    Path(__file__).resolve(),
    Path(__file__).resolve().parent.parent / "src" / "data_access" / "edgar" / "edgar_service.py",
    Path(__file__).resolve().parent.parent / "scripts" / "run_scan.py",
)
_FORBIDDEN_REAL_STATE_REFERENCES = (
    "data/cache",
    "data/edge_research.db",
    ".streamlit/secrets.toml",
)


def _source_excluding_this_guards_own_string_list(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if path.name == "test_edgar_service.py":
        start = text.index("_FORBIDDEN_REAL_STATE_REFERENCES = (")
        end = text.index(")\n", start) + len(")\n")
        return text[:start] + text[end:]
    return text


def test_phase4a_files_never_reference_real_local_state():
    offenders = []
    for path in _PHASE4A_FILES:
        source = _source_excluding_this_guards_own_string_list(path)
        for forbidden in _FORBIDDEN_REAL_STATE_REFERENCES:
            if forbidden in source:
                offenders.append(f"{path.name}: contains {forbidden!r}")
    assert not offenders, offenders


def test_process_candidate_now_threads_explicit_auto_publish_setting(tmp_path, monkeypatch):
    captured = {}

    def _fake_process_single_candidate(
        client,
        candidate_id,
        cache_dir,
        candidate_repository=None,
        auto_publish_enabled=False,
    ):
        captured["auto_publish_enabled"] = auto_publish_enabled
        return None

    monkeypatch.setattr(
        edgar_service.edgar_pipeline,
        "process_single_candidate",
        _fake_process_single_candidate,
    )

    settings = _settings(tmp_path, user_agent="EevaResearch test@example.com")
    object.__setattr__(settings, "edgar_auto_publish_enabled", True)

    edgar_service.process_candidate_now(settings, "edgar-cand-1")

    assert captured["auto_publish_enabled"] is True
