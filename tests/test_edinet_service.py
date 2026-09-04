"""edinet_service — the EDINET pilot's wiring layer. edinet_readiness
and get_edinet_companies are pure config/cache reads, no network.
run_scan/process_candidate_now's core scan/extraction behavior is
already exercised end-to-end via test_edinet_pipeline.py's mocked-client
tests — the tests below cover only Durable-State Phase 4C-1's addition
to those two wrappers: the optional, additive `candidate_repository`
parameter and its threading through to
edinet_pipeline.run_pipeline/process_single_candidate. No test here
makes a real network call — either the pipeline-layer function is
monkeypatched directly, or edinet_service._client is replaced with a
MagicMock EdinetClient, mirroring tests/test_edgar_service.py's own
Phase 4A pattern. Gate 7 note: get_edinet_companies now returns the five
live-verified EDINET cohort entries (see tracked_companies.py) — all
pre-resolved (corp_code hardcoded, not runtime-resolved, per that
module's docstring), so unresolved_companies stays trivially empty
either way."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.edinet import edinet_service


def _settings(cache_dir, subscription_key=None, translation_key=None) -> Settings:
    # translation_key defaults to None explicitly (not omitted) — omitting
    # it would fall back to Settings' own os.getenv default, which can
    # leak a real, locally-configured EDGE_TRANSLATION_API_KEY and mask
    # exactly the readiness gap the translation reliability workstream's
    # own tests below are proving.
    return Settings(edinet_subscription_key=subscription_key, translation_api_key=translation_key, cache_dir=cache_dir)


def test_readiness_reports_missing_subscription_key(tmp_path):
    readiness = edinet_service.edinet_readiness(_settings(tmp_path))

    assert not readiness.subscription_key_configured
    assert not readiness.ready


def test_readiness_ready_when_key_present_and_cohort_already_resolved(tmp_path):
    # The five EDINET cohort entries are hardcoded/pre-resolved (Gate 7),
    # so unresolved_companies is empty and readiness is driven entirely
    # by the two keys — same readiness shape DART/EDGAR reach once their
    # own companies are resolved.
    readiness = edinet_service.edinet_readiness(_settings(tmp_path, subscription_key="test-key", translation_key="deepl-key"))

    assert readiness.subscription_key_configured
    assert readiness.translation_key_configured
    assert readiness.unresolved_companies == ()
    assert readiness.ready


def test_readiness_not_ready_when_translation_key_missing_even_with_subscription_key(tmp_path):
    # Translation reliability workstream: before this, EdinetReadiness
    # never checked the translation key at all, so a scan could report
    # "ready" and then fail every excerpt translation silently. The
    # subscription key alone is no longer sufficient.
    readiness = edinet_service.edinet_readiness(_settings(tmp_path, subscription_key="test-key", translation_key=None))

    assert readiness.subscription_key_configured
    assert not readiness.translation_key_configured
    assert not readiness.ready


def test_readiness_checks_translation_key_without_making_any_provider_call(tmp_path):
    # edinet_readiness() takes no TranslationProvider/client argument at
    # all — this proves the gate is a pure settings read, structurally
    # incapable of making a network call, rather than a runtime credential
    # check against DeepL's own API.
    readiness = edinet_service.edinet_readiness(_settings(tmp_path, subscription_key="test-key", translation_key=None))
    assert readiness.translation_key_configured is False
    import inspect

    assert "provider" not in inspect.signature(edinet_service.edinet_readiness).parameters


def test_get_edinet_companies_returns_the_eighteen_live_verified_cohort_entries(tmp_path):
    companies = edinet_service.get_edinet_companies(tmp_path)
    names = {c.name for c in companies}
    assert names == {
        "SoftBank Group Corp.", "Kioxia Holdings Corporation", "Furukawa Electric Co., Ltd.",
        "FANUC CORPORATION", "ispace, inc.",
        # Core Issuer Expansion batch (2026-09-04)
        "Tokyo Electron Limited", "Advantest Corporation", "Disco Corporation",
        "Shin-Etsu Chemical Co., Ltd.", "SUMCO Corporation", "Ibiden Co., Ltd.",
        "Mitsubishi Electric Corporation", "Renesas Electronics Corporation",
        # EDINET Filings Radar issuer-expansion batch (2026-09-04)
        "SCREEN Holdings Co., Ltd.", "Nidec Corporation", "TDK Corporation",
        "Murata Manufacturing Co., Ltd.", "TOWA Corporation",
    }
    assert all(c.corp_code is not None for c in companies)


def test_get_edinet_companies_is_unaffected_by_cache_dir_contents(tmp_path):
    # No EDINET resolver cache is consulted at runtime for this cohort —
    # cache_dir has no bearing on which companies come back.
    companies_a = edinet_service.get_edinet_companies(tmp_path)
    companies_b = edinet_service.get_edinet_companies(tmp_path / "does-not-exist")
    assert companies_a == companies_b


def test_readiness_never_raises_without_a_configured_key(tmp_path):
    # Never makes a network call and never reads/validates the real
    # credential value — only checks presence (see errors.py's
    # EdinetConfigError docstring and the Settings field's own docstring).
    readiness = edinet_service.edinet_readiness(_settings(tmp_path, subscription_key=""))
    assert not readiness.subscription_key_configured


# ---------------------------------------------------------------------------
# Durable-State Phase 4C-1 — run_scan/process_candidate_now's additive,
# optional candidate_repository parameter, threaded through to
# edinet_pipeline.run_pipeline/process_single_candidate.
# ---------------------------------------------------------------------------


def test_run_scan_omits_candidate_repository_by_default(tmp_path, monkeypatch):
    captured = {}

    def _fake_run_pipeline(
        client, companies, cache_dir, lookback_days=None, max_candidates_to_process=None,
        candidate_repository=None, translation_provider=None, material_event_lexicon_enabled=False,
    ):
        captured["candidate_repository"] = candidate_repository
        return "sentinel-report"

    monkeypatch.setattr(edinet_service.edinet_pipeline, "run_pipeline", _fake_run_pipeline)

    result = edinet_service.run_scan(_settings(tmp_path, subscription_key="test-key"))

    assert result == "sentinel-report"
    assert captured["candidate_repository"] is None


def test_run_scan_passes_through_an_explicitly_supplied_repository(tmp_path, monkeypatch):
    captured = {}
    sentinel_repo = object()  # identity check only — no method on it is ever called this test

    def _fake_run_pipeline(
        client, companies, cache_dir, lookback_days=None, max_candidates_to_process=None,
        candidate_repository=None, translation_provider=None, material_event_lexicon_enabled=False,
    ):
        captured["candidate_repository"] = candidate_repository
        return "sentinel-report"

    monkeypatch.setattr(edinet_service.edinet_pipeline, "run_pipeline", _fake_run_pipeline)

    edinet_service.run_scan(_settings(tmp_path, subscription_key="test-key"), candidate_repository=sentinel_repo)

    assert captured["candidate_repository"] is sentinel_repo


def test_process_candidate_now_omits_candidate_repository_by_default(tmp_path, monkeypatch):
    captured = {}

    def _fake_process_single_candidate(client, candidate_id, cache_dir, candidate_repository=None, translation_provider=None):
        captured["candidate_repository"] = candidate_repository
        return None

    monkeypatch.setattr(edinet_service.edinet_pipeline, "process_single_candidate", _fake_process_single_candidate)

    result = edinet_service.process_candidate_now(_settings(tmp_path, subscription_key="test-key"), "edinet-cand-1")

    assert result is None
    assert captured["candidate_repository"] is None


def test_process_candidate_now_passes_through_an_explicitly_supplied_repository(tmp_path, monkeypatch):
    captured = {}
    sentinel_repo = object()

    def _fake_process_single_candidate(client, candidate_id, cache_dir, candidate_repository=None, translation_provider=None):
        captured["candidate_repository"] = candidate_repository
        return None

    monkeypatch.setattr(edinet_service.edinet_pipeline, "process_single_candidate", _fake_process_single_candidate)

    edinet_service.process_candidate_now(
        _settings(tmp_path, subscription_key="test-key"), "edinet-cand-1", candidate_repository=sentinel_repo,
    )

    assert captured["candidate_repository"] is sentinel_repo


# --- Real end-to-end proof, one level up from edinet_pipeline.py's own
# Phase 3A equivalence tests: a mocked-client run_scan() call, with an
# injected synthetic local SQLite repository, produces the same one
# candidate a default/omitted JSON-backed call would. Uses the real,
# live-verified SoftBank Group Corp. cohort entry (corp_code E02778,
# docID S100YGH5 — see tracked_companies.py) since get_edinet_companies
# has no unresolved-company path to seed around (Gate 7: hardcoded, not
# runtime-resolved). ---

_EDINET_DOC_ID = "S100YGH5"
_EDINET_DOC_BYTES = b"<html><body><p>Annual report content.</p></body></html>"


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _edinet_envelope(results: list[dict]) -> dict:
    return {"metadata": {"status": "200", "message": "OK", "resultset": {"count": len(results)}}, "results": results}


def _make_edinet_client() -> MagicMock:
    result = {
        "docID": _EDINET_DOC_ID, "docTypeCode": "120", "ordinanceCode": "010", "formCode": "030000",
        "filerName": "SoftBank Group Corp.", "docDescription": "Annual Securities Report",
        "edinetCode": "E02778", "secCode": "99840", "submitDateTime": "2026-06-22 09:00",
    }
    client = MagicMock()
    client.get_document_list.side_effect = lambda date_str, type_=2: (
        _edinet_envelope([result]) if date_str == _today() else _edinet_envelope([])
    )
    client.fetch_document.side_effect = lambda doc_id, type_: _EDINET_DOC_BYTES
    return client


def test_run_scan_injected_sqlite_repository_produces_equivalent_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(edinet_service, "_client", lambda settings: _make_edinet_client())
    settings = Settings(
        edinet_subscription_key="test-key", cache_dir=tmp_path,
        db_backend="sqlite", state_db_path=tmp_path / "state.db",
    )
    repo = backend_factory.get_candidate_repository(settings, "EDINET")

    report = edinet_service.run_scan(settings, candidate_repository=repo)

    assert report.candidates_detected == 1
    stored = repo.load_candidates()
    assert len(stored) == 1
    candidate = next(iter(stored.values()))
    assert candidate.filing.rcept_no == _EDINET_DOC_ID
    assert candidate.filing.corp_code == "E02778"
    # The SQLite path never touched the JSON candidate store this call.
    assert not (tmp_path / "edinet_candidates.json").exists()
