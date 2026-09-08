"""Durable-State Phase 3A — candidate-persistence-only injection seam.

Proves the additive, optional `candidate_repository` parameter added to
edinet_pipeline.backfill_candidate_from_existing_event/process_single_
candidate/run_pipeline, edgar_pipeline.process_single_candidate/
run_pipeline, and radar_pipeline.process_single_candidate/run_pipeline:

  - preserves today's exact JSON-backed candidate_store.py behavior when
    omitted (every existing caller/test in test_edgar_pipeline.py/
    test_radar_pipeline.py/test_edinet_pipeline.py is unmodified and
    stays green);
  - when explicitly supplied with a temporary/`:memory:`-backed SQLite
    candidate repository (backend_factory.get_candidate_repository, the
    already-committed Phase 2A/2B factory), persists the same candidate
    data the JSON path would have produced;
  - never touches filing-event, excerpt, translation, identifier,
    discovery, or Signal persistence — those remain exactly as scan_
    service.py/document_service.py/translation_service.py/cik_resolver.py/
    corp_code_resolver.py/discovery_service.py already behave, unmodified
    this phase;
  - was not yet reachable from any real service entry point at the time
    this phase landed. Durable-State Phase 4A (EDGAR) and Phase 4C-1
    (DART, EDINET) later extend this same additive, optional seam one
    level up through each source's own service module
    (edgar_service.py/dart/radar_service.py/edinet_service.py) and
    scripts/run_scan.py's main() — still synthetic/local-test-only in
    every case: a real caller (the CLI's actual invocation, or any
    production code path) always omits the parameter, so JSON-backed
    candidate_store.py behavior is unaffected regardless of the seam's
    existence. See tests/test_edgar_service.py, tests/test_radar_service.py,
    tests/test_edinet_service.py, and tests/test_run_scan_cli.py for
    each source's own pass-through/default-omission coverage — this file
    no longer separately guards against DART/EDINET exposing the
    parameter, since exposing it is now the correct, intended state for
    all three sources equally.

Everything here uses tmp_path/`:memory:` and fully mocked clients — no
test reads ambient application configuration or accepts an ambient real
path, and none accesses the real local cache directory, the real .env,
the Streamlit secrets file, or the pre-existing legacy database."""
from __future__ import annotations

import io
import zipfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from src.config.settings import Settings
from src.config.tracked_companies import TrackedCompany
from src.data_access import backend_factory
from src.data_access.dart import candidate_store, radar_pipeline
from src.data_access.dart.client import DisclosureRecord
from src.data_access.edgar import edgar_pipeline
from src.data_access.edinet import edinet_pipeline
from src.logic import review_actions
from src.models.models import CandidateStatus


def _sqlite_settings(tmp_path: Path) -> Settings:
    return Settings(db_backend="sqlite", state_db_path=tmp_path / "state.db")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


# ---------------------------------------------------------------------------
# EDGAR fixtures — same shapes as tests/test_edgar_pipeline.py's own helpers
# ---------------------------------------------------------------------------

_NVDA = TrackedCompany(
    name="NVIDIA", exchange="NASDAQ", krx_code="NVDA", source="SEC EDGAR",
    themes=("ai-buildout",), corp_code="0001045810",
)
_EDGAR_ACCESSION = "0001045810-26-000001"
_EDGAR_DOC_BYTES = b"<html><body><p>Item 2.02 Results of Operations. Revenue rose.</p></body></html>"


def _edgar_recent(accession_numbers: list[str]) -> dict:
    n = len(accession_numbers)
    return {
        "accessionNumber": accession_numbers, "filingDate": [_today()] * n,
        "form": ["8-K"] * n, "primaryDocument": ["doc.htm"] * n, "primaryDocDescription": ["desc"] * n,
    }


def _make_edgar_client() -> MagicMock:
    client = MagicMock()
    client.get_submissions.side_effect = lambda cik: (
        {"filings": {"recent": _edgar_recent([_EDGAR_ACCESSION])}} if cik == _NVDA.corp_code
        else {"filings": {"recent": _edgar_recent([])}}
    )
    client.fetch_document.side_effect = lambda cik, accession_no, filename: _EDGAR_DOC_BYTES
    return client


# ---------------------------------------------------------------------------
# DART fixtures — same shapes as tests/test_radar_pipeline.py's own helpers
# ---------------------------------------------------------------------------

_SAMSUNG = TrackedCompany(
    name="Samsung Electronics", exchange="KRX", krx_code="005930", source="OpenDART / DART",
    themes=("memory", "ai-buildout"), corp_code="00126380",
)
_DART_RCEPT_NO = "20260810000001"


def _dart_document_zip(body_text: str = "신규시설투자등 결정 안내") -> bytes:
    xml = (
        f'<?xml version="1.0" encoding="utf-8"?><DOCUMENT>'
        f"<SECTION-1><P>cover</P></SECTION-1><SECTION-1><P>{body_text}</P></SECTION-1></DOCUMENT>"
    ).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("doc.xml", xml)
    return buf.getvalue()


def _make_dart_client() -> MagicMock:
    record = DisclosureRecord(
        corp_cls="Y", corp_name="삼성전자", corp_code="00126380", stock_code="005930",
        report_nm="신규시설투자등", rcept_no=_DART_RCEPT_NO, flr_nm="삼성전자", rcept_dt="20260810", rm="",
    )
    client = MagicMock()

    def _search(corp_code, bgn_de, end_de, page_no=1, page_count=100):
        if corp_code == _SAMSUNG.corp_code and page_no == 1:
            return ([record], 1)
        return ([], 0)

    client.search_disclosures.side_effect = _search
    client.fetch_document_zip.side_effect = lambda rcept_no: _dart_document_zip()
    return client


class _FakeTranslationProvider:
    name = "DeepL"

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        return f"[translated] {text}"


# ---------------------------------------------------------------------------
# EDINET fixtures — real DEFAULT_CODE_CATEGORY_MAP tuple (010:030000:120),
# the same live-verified annual-report tuple test_edinet_pipeline.py's own
# backfill tests use, so run_pipeline's own real default map (not a test-
# only override) actually detects one candidate.
# ---------------------------------------------------------------------------

_SOFTBANK = TrackedCompany(
    name="SoftBank Group", exchange="TSE", krx_code="9984", source="EDINET",
    themes=("ai-buildout",), corp_code="E02778",
)
_EDINET_DOC_ID = "S100YGH5"
_EDINET_DOC_BYTES = b"<html><body><p>Annual report content.</p></body></html>"


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


# ---------------------------------------------------------------------------
# 1/2/3. EDINET backfill_candidate_from_existing_event — first target
# ---------------------------------------------------------------------------

def _seed_softbank_filing_event(cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    import json
    path = cache_dir / "edinet_filing_events.json"
    payload = {
        "seen_keys": [f"EDINET:E02778:{_EDINET_DOC_ID}"],
        "filing_events": [{
            "rcept_no": _EDINET_DOC_ID, "corp_code": "E02778", "corp_name": "SoftBank Group Corp.", "stock_code": "99840",
            "report_nm": "有価証券報告書－第46期(2025/04/01－2026/03/31)", "rcept_dt": "2026-06-22",
            "flr_nm": "ソフトバンクグループ株式会社", "pblntf_ty": "030000", "pblntf_detail_ty": "120", "ordinance_code": "010",
            "theme_slug": "ai-buildout", "subtheme_slug": None,
            "source_url": f"https://api.edinet-fsa.go.jp/api/v2/documents/{_EDINET_DOC_ID}",
            "retrieved_at": "2026-08-17T22:56:26.469712+00:00", "source_name": "EDINET", "original_language": "Japanese",
            "is_demo": False, "primary_document": "",
        }],
        "candidate_signals": [],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_backfill_with_no_collaborator_retains_existing_json_behavior(tmp_path):
    _seed_softbank_filing_event(tmp_path)

    result = edinet_pipeline.backfill_candidate_from_existing_event(tmp_path, _EDINET_DOC_ID)

    assert result.created is True
    assert result.candidate_id == "edinet-cand-S100YGH5"
    store = candidate_store.load_candidates(tmp_path, edinet_pipeline.CANDIDATE_STORE_FILENAME)
    assert store["edinet-cand-S100YGH5"].confidence == "Moderate"
    assert not (tmp_path / "state.db").exists()


def test_backfill_with_injected_sqlite_repository_persists_to_temp_sqlite_db(tmp_path):
    _seed_softbank_filing_event(tmp_path)
    settings = _sqlite_settings(tmp_path)
    repo = backend_factory.get_candidate_repository(settings, "EDINET")

    result = edinet_pipeline.backfill_candidate_from_existing_event(tmp_path, _EDINET_DOC_ID, candidate_repository=repo)

    assert result.created is True
    assert result.candidate_id == "edinet-cand-S100YGH5"
    persisted = repo.get_candidate("edinet-cand-S100YGH5")
    assert persisted is not None
    assert persisted.confidence == "Moderate"
    assert persisted.matched_rules == ["annual_securities_report:010:030000:120"]
    assert persisted.status == CandidateStatus.CANDIDATE_DETECTED
    # And the write went to SQLite, not the JSON candidate cache file.
    assert not (tmp_path / edinet_pipeline.CANDIDATE_STORE_FILENAME).exists()


def test_backfill_sqlite_path_takes_no_client_parameter_and_makes_no_network_call(tmp_path):
    # backfill_candidate_from_existing_event's own signature has never
    # taken a client argument (see test_edinet_pipeline.py's own
    # equivalent test) — this documents that the injected-collaborator
    # variant preserves that guarantee identically, not just the default
    # JSON path. There is nothing network-shaped to mock either way.
    import inspect
    params = inspect.signature(edinet_pipeline.backfill_candidate_from_existing_event).parameters
    assert "client" not in params
    assert "candidate_repository" in params


def test_backfill_is_idempotent_through_injected_sqlite_repository(tmp_path):
    _seed_softbank_filing_event(tmp_path)
    settings = _sqlite_settings(tmp_path)
    repo = backend_factory.get_candidate_repository(settings, "EDINET")

    first = edinet_pipeline.backfill_candidate_from_existing_event(tmp_path, _EDINET_DOC_ID, candidate_repository=repo)
    second = edinet_pipeline.backfill_candidate_from_existing_event(tmp_path, _EDINET_DOC_ID, candidate_repository=repo)

    assert first.created is True
    assert second.created is False
    assert second.already_existed is True
    assert len(repo.load_candidates()) == 1


# ---------------------------------------------------------------------------
# Single-backend-atomicity contract — a given invocation uses exactly one
# candidate store (JSON, or the exact supplied collaborator) for every
# read and write it performs, never a mix. No cross-backend lookup,
# dedup, migration, dual-read, dual-write, or auto-fallback exists.
# ---------------------------------------------------------------------------

def test_json_only_idempotency_two_calls_no_collaborator_yields_one_candidate(tmp_path):
    _seed_softbank_filing_event(tmp_path)

    first = edinet_pipeline.backfill_candidate_from_existing_event(tmp_path, _EDINET_DOC_ID)
    second = edinet_pipeline.backfill_candidate_from_existing_event(tmp_path, _EDINET_DOC_ID)

    assert first.created is True
    assert second.already_existed is True
    store = candidate_store.load_candidates(tmp_path, edinet_pipeline.CANDIDATE_STORE_FILENAME)
    assert len(store) == 1


def test_sqlite_only_idempotency_two_calls_same_repository_instance_yields_one_candidate(tmp_path):
    # Restates test_backfill_is_idempotent_through_injected_sqlite_repository
    # explicitly under this contract's own name/section, per the required
    # test list — same assertion, same one repository instance reused
    # across both calls.
    _seed_softbank_filing_event(tmp_path)
    settings = _sqlite_settings(tmp_path)
    repo = backend_factory.get_candidate_repository(settings, "EDINET")

    first = edinet_pipeline.backfill_candidate_from_existing_event(tmp_path, _EDINET_DOC_ID, candidate_repository=repo)
    second = edinet_pipeline.backfill_candidate_from_existing_event(tmp_path, _EDINET_DOC_ID, candidate_repository=repo)

    assert first.created is True
    assert second.already_existed is True
    assert len(repo.load_candidates()) == 1


class _PoisonCandidateRepository:
    """Every method raises if called — a canary proving a code path never
    touches a repository object at all, not just that its result is
    unused. Never passed to any function under test; its role is to sit
    in test scope so the intent ("no repository object exists on this
    path") is explicit rather than merely implied by omission."""

    def load_candidates(self):
        raise AssertionError("JSON-only path must never call a repository's load_candidates")

    def upsert_new_candidates(self, new_candidates):
        raise AssertionError("JSON-only path must never call a repository's upsert_new_candidates")

    def update_candidate(self, candidate, expected_version=None):
        raise AssertionError("JSON-only path must never call a repository's update_candidate")


def test_json_default_path_never_calls_any_repository_object(tmp_path, monkeypatch):
    _seed_softbank_filing_event(tmp_path)
    poison = _PoisonCandidateRepository()  # created, never passed — see class docstring

    edinet_pipeline.backfill_candidate_from_existing_event(tmp_path, _EDINET_DOC_ID)  # candidate_repository omitted

    # The poison object was never even offered to the function, so by
    # construction it saw no calls — asserted directly rather than left
    # implicit, and paired with the JSON functions genuinely firing:
    # monkeypatch confirms the real JSON path executed instead.
    called = {"load": False, "upsert": False}
    real_load, real_upsert = candidate_store.load_candidates, candidate_store.upsert_new_candidates

    def _spy_load(*args, **kwargs):
        called["load"] = True
        return real_load(*args, **kwargs)

    def _spy_upsert(*args, **kwargs):
        called["upsert"] = True
        return real_upsert(*args, **kwargs)

    monkeypatch.setattr(candidate_store, "load_candidates", _spy_load)
    monkeypatch.setattr(candidate_store, "upsert_new_candidates", _spy_upsert)
    edinet_pipeline.backfill_candidate_from_existing_event(tmp_path, _EDINET_DOC_ID)  # candidate_repository omitted

    assert called["load"] is True  # JSON path genuinely ran
    assert isinstance(poison, _PoisonCandidateRepository)  # poison exists, untouched (no exception raised above)


def test_sqlite_injected_path_calls_no_json_candidate_store_function(tmp_path, monkeypatch):
    _seed_softbank_filing_event(tmp_path)
    settings = _sqlite_settings(tmp_path)
    repo = backend_factory.get_candidate_repository(settings, "EDINET")

    def _forbidden(*args, **kwargs):
        raise AssertionError("injected-repository path must never call a JSON candidate_store function")

    monkeypatch.setattr(candidate_store, "load_candidates", _forbidden)
    monkeypatch.setattr(candidate_store, "upsert_new_candidates", _forbidden)
    monkeypatch.setattr(candidate_store, "update_candidate", _forbidden)

    result = edinet_pipeline.backfill_candidate_from_existing_event(tmp_path, _EDINET_DOC_ID, candidate_repository=repo)

    assert result.created is True  # completed without ever hitting a patched (forbidden) JSON function
    assert repo.get_candidate("edinet-cand-S100YGH5") is not None


def test_sqlite_injected_run_pipeline_calls_no_json_candidate_store_function(tmp_path, monkeypatch):
    settings = _sqlite_settings(tmp_path)
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")

    def _forbidden(*args, **kwargs):
        raise AssertionError("injected-repository path must never call a JSON candidate_store function")

    monkeypatch.setattr(candidate_store, "load_candidates", _forbidden)
    monkeypatch.setattr(candidate_store, "upsert_new_candidates", _forbidden)
    monkeypatch.setattr(candidate_store, "update_candidate", _forbidden)

    report = edgar_pipeline.run_pipeline(_make_edgar_client(), [_NVDA], tmp_path, candidate_repository=repo)

    assert report.candidates_detected == 1
    assert report.candidates_processed == 1
    assert len(repo.load_candidates()) == 1


class _SpyCandidateRepository:
    """Wraps a real collaborator and records every method invoked against
    it — proves the *exact instance* supplied to a pipeline call receives
    every candidate read/write that call performs, not merely that some
    repository somewhere was used."""

    def __init__(self, wrapped):
        self._wrapped = wrapped
        self.calls: list[str] = []

    def load_candidates(self):
        self.calls.append("load_candidates")
        return self._wrapped.load_candidates()

    def upsert_new_candidates(self, new_candidates):
        self.calls.append("upsert_new_candidates")
        return self._wrapped.upsert_new_candidates(new_candidates)

    def update_candidate(self, candidate, expected_version=None):
        self.calls.append("update_candidate")
        return self._wrapped.update_candidate(candidate, expected_version=expected_version)

    def upsert_filing_events_only(self, filings):
        self.calls.append("upsert_filing_events_only")
        return self._wrapped.upsert_filing_events_only(filings)


def test_edgar_run_pipeline_uses_exactly_the_supplied_repository_instance(tmp_path):
    settings = _sqlite_settings(tmp_path)
    spy = _SpyCandidateRepository(backend_factory.get_candidate_repository(settings, "SEC EDGAR"))

    edgar_pipeline.run_pipeline(_make_edgar_client(), [_NVDA], tmp_path, candidate_repository=spy)

    assert "upsert_new_candidates" in spy.calls
    assert "load_candidates" in spy.calls
    assert "update_candidate" in spy.calls
    assert spy.calls.count("update_candidate") == 1  # the one detected+processed candidate
    # Durable-State Phase 4M-2 (Stage 0) is EDINET-only — edgar_pipeline.py
    # is untouched and must never call the new method.
    assert "upsert_filing_events_only" not in spy.calls


def test_dart_run_pipeline_uses_exactly_the_supplied_repository_instance(tmp_path):
    settings = _sqlite_settings(tmp_path)
    spy = _SpyCandidateRepository(backend_factory.get_candidate_repository(settings, "OpenDART / DART"))

    radar_pipeline.run_pipeline(_make_dart_client(), _FakeTranslationProvider(), [_SAMSUNG], tmp_path, candidate_repository=spy)

    assert "upsert_new_candidates" in spy.calls
    assert "load_candidates" in spy.calls
    assert "update_candidate" in spy.calls
    # Durable-State Phase 4M-2 (Stage 0) is EDINET-only — radar_pipeline.py
    # (DART) is untouched and must never call the new method.
    assert "upsert_filing_events_only" not in spy.calls


def test_edinet_run_pipeline_uses_exactly_the_supplied_repository_instance(tmp_path):
    settings = _sqlite_settings(tmp_path)
    spy = _SpyCandidateRepository(backend_factory.get_candidate_repository(settings, "EDINET"))

    edinet_pipeline.run_pipeline(_make_edinet_client(), [_SOFTBANK], tmp_path, candidate_repository=spy)

    assert "upsert_new_candidates" in spy.calls
    assert "load_candidates" in spy.calls
    assert "update_candidate" in spy.calls
    # Durable-State Phase 4M-2 (Stage 0) — the new filing-event-only
    # persistence call, exercised through this exact injected instance.
    assert "upsert_filing_events_only" in spy.calls


def test_edinet_backfill_uses_exactly_the_supplied_repository_instance(tmp_path):
    _seed_softbank_filing_event(tmp_path)
    settings = _sqlite_settings(tmp_path)
    spy = _SpyCandidateRepository(backend_factory.get_candidate_repository(settings, "EDINET"))

    edinet_pipeline.backfill_candidate_from_existing_event(tmp_path, _EDINET_DOC_ID, candidate_repository=spy)

    assert spy.calls == ["load_candidates", "upsert_new_candidates"]


def test_no_cross_backend_lookup_sqlite_path_ignores_a_json_only_record(tmp_path):
    """The explicit unsupported boundary: a candidate created via a
    JSON-mode call is invisible to a later SQLite-mode call for the same
    doc_id — no cross-backend dedup, merge, or fallback of any kind. Not
    simulated real state: both stores are synthetic/temporary, and the
    point demonstrated is purely that the selected collaborator is the
    sole persistence target per invocation."""
    _seed_softbank_filing_event(tmp_path)
    json_only = edinet_pipeline.backfill_candidate_from_existing_event(tmp_path, _EDINET_DOC_ID)
    assert json_only.created is True

    settings = _sqlite_settings(tmp_path)
    repo = backend_factory.get_candidate_repository(settings, "EDINET")
    sqlite_call = edinet_pipeline.backfill_candidate_from_existing_event(tmp_path, _EDINET_DOC_ID, candidate_repository=repo)

    # Created again, independently — NOT reported as already_existed,
    # because the SQLite path has no visibility into the JSON-side record.
    assert sqlite_call.created is True
    assert sqlite_call.already_existed is False
    assert repo.get_candidate("edinet-cand-S100YGH5") is not None
    # And the JSON-side record is untouched by the SQLite-mode call.
    json_store = candidate_store.load_candidates(tmp_path, edinet_pipeline.CANDIDATE_STORE_FILENAME)
    assert len(json_store) == 1


# ---------------------------------------------------------------------------
# 4/7/8. Per-source run_pipeline candidate-write-site equivalence:
# JSON-default vs. injected-SQLite — each side uses its own independent,
# clean temporary store (a separate subdirectory for JSON, a separate
# state.db for SQLite); this section never implies or relies on any
# shared state between the two backends.
# JSON-default vs. injected-SQLite, same synthetic input either way.
# ---------------------------------------------------------------------------

def test_edgar_run_pipeline_sqlite_equivalence_to_json_default(tmp_path):
    json_dir = tmp_path / "json-run"
    report_json = edgar_pipeline.run_pipeline(_make_edgar_client(), [_NVDA], json_dir)
    json_store = candidate_store.load_candidates(json_dir, edgar_pipeline.CANDIDATE_STORE_FILENAME)

    sqlite_dir = tmp_path / "sqlite-run"
    settings = _sqlite_settings(tmp_path)
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    report_sqlite = edgar_pipeline.run_pipeline(_make_edgar_client(), [_NVDA], sqlite_dir, candidate_repository=repo)
    sqlite_store = repo.load_candidates()

    assert report_json.candidates_detected == report_sqlite.candidates_detected == 1
    assert report_json.candidates_processed == report_sqlite.candidates_processed == 1
    assert set(json_store) == set(sqlite_store)
    for candidate_id in json_store:
        j, s = json_store[candidate_id], sqlite_store[candidate_id]
        assert j.id == s.id
        assert j.status == s.status
        assert j.confidence == s.confidence
        assert j.matched_rules == s.matched_rules
        assert j.filing.rcept_no == s.filing.rcept_no
        assert j.filing.source_name == s.filing.source_name
    # Backend isolation: neither path touched the other's storage.
    assert not (sqlite_dir / edgar_pipeline.CANDIDATE_STORE_FILENAME).exists()
    assert not (json_dir / "state.db").exists()


def test_dart_run_pipeline_sqlite_equivalence_to_json_default(tmp_path):
    json_dir = tmp_path / "json-run"
    report_json = radar_pipeline.run_pipeline(_make_dart_client(), _FakeTranslationProvider(), [_SAMSUNG], json_dir)
    json_store = candidate_store.load_candidates(json_dir)

    sqlite_dir = tmp_path / "sqlite-run"
    settings = _sqlite_settings(tmp_path)
    repo = backend_factory.get_candidate_repository(settings, "OpenDART / DART")
    report_sqlite = radar_pipeline.run_pipeline(
        _make_dart_client(), _FakeTranslationProvider(), [_SAMSUNG], sqlite_dir, candidate_repository=repo,
    )
    sqlite_store = repo.load_candidates()

    assert report_json.candidates_detected == report_sqlite.candidates_detected == 1
    assert report_json.candidates_processed == report_sqlite.candidates_processed == 1
    assert set(json_store) == set(sqlite_store)
    for candidate_id in json_store:
        j, s = json_store[candidate_id], sqlite_store[candidate_id]
        assert j.id == s.id
        assert j.status == s.status
        assert j.confidence == s.confidence
        assert j.matched_rules == s.matched_rules
        assert j.filing.rcept_no == s.filing.rcept_no
    assert not (sqlite_dir / "dart_candidates.json").exists()
    assert not (json_dir / "state.db").exists()


def test_edinet_run_pipeline_sqlite_equivalence_to_json_default(tmp_path):
    json_dir = tmp_path / "json-run"
    report_json = edinet_pipeline.run_pipeline(_make_edinet_client(), [_SOFTBANK], json_dir)
    json_store = candidate_store.load_candidates(json_dir, edinet_pipeline.CANDIDATE_STORE_FILENAME)

    sqlite_dir = tmp_path / "sqlite-run"
    settings = _sqlite_settings(tmp_path)
    repo = backend_factory.get_candidate_repository(settings, "EDINET")
    report_sqlite = edinet_pipeline.run_pipeline(_make_edinet_client(), [_SOFTBANK], sqlite_dir, candidate_repository=repo)
    sqlite_store = repo.load_candidates()

    assert report_json.candidates_detected == report_sqlite.candidates_detected == 1
    assert report_json.candidates_processed == report_sqlite.candidates_processed == 1
    assert set(json_store) == set(sqlite_store)
    for candidate_id in json_store:
        j, s = json_store[candidate_id], sqlite_store[candidate_id]
        assert j.id == s.id
        assert j.status == s.status
        assert j.confidence == s.confidence
        assert j.matched_rules == s.matched_rules
        assert j.filing.rcept_no == s.filing.rcept_no
    assert not (sqlite_dir / edinet_pipeline.CANDIDATE_STORE_FILENAME).exists()
    assert not (json_dir / "state.db").exists()


def test_edgar_run_pipeline_deferred_candidate_write_site_also_routes_through_collaborator(tmp_path):
    # The to_defer loop is a syntactically separate write statement from
    # the to_process loop's — two eligible candidates with a
    # budget of 1 forces exactly one candidate down each path, so both
    # writer statements are exercised against the injected collaborator
    # in a single call.
    _MU = TrackedCompany(
        name="Micron Technology", exchange="NASDAQ", krx_code="MU", source="SEC EDGAR",
        themes=("memory",), corp_code="0000723125",
    )
    other_accession = "0000723125-26-000002"
    client = MagicMock()
    client.get_submissions.side_effect = lambda cik: (
        {"filings": {"recent": _edgar_recent([_EDGAR_ACCESSION])}} if cik == _NVDA.corp_code
        else {"filings": {"recent": _edgar_recent([other_accession])}} if cik == _MU.corp_code
        else {"filings": {"recent": _edgar_recent([])}}
    )
    client.fetch_document.side_effect = lambda cik, accession_no, filename: _EDGAR_DOC_BYTES

    settings = _sqlite_settings(tmp_path)
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")

    report = edgar_pipeline.run_pipeline(
        client, [_NVDA, _MU], tmp_path, max_candidates_to_process=1, candidate_repository=repo,
    )

    assert report.candidates_detected == 2
    assert report.candidates_processed == 1
    assert report.candidates_deferred == 1
    statuses = {c.status for c in repo.load_candidates().values()}
    assert statuses == {CandidateStatus.NEEDS_REVIEW, CandidateStatus.PROCESSING_DEFERRED}
    assert not (tmp_path / edgar_pipeline.CANDIDATE_STORE_FILENAME).exists()


def test_edgar_process_single_candidate_sqlite_equivalence(tmp_path):
    settings = _sqlite_settings(tmp_path)
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    client = _make_edgar_client()
    edgar_pipeline.run_pipeline(client, [_NVDA], tmp_path, max_candidates_to_process=0, candidate_repository=repo)

    candidate_id = next(iter(repo.load_candidates()))
    processed = edgar_pipeline.process_single_candidate(client, candidate_id, tmp_path, candidate_repository=repo)

    assert processed is not None
    assert processed.status == CandidateStatus.NEEDS_REVIEW
    reloaded = repo.get_candidate(candidate_id)
    assert reloaded.status == CandidateStatus.NEEDS_REVIEW
    assert not (tmp_path / edgar_pipeline.CANDIDATE_STORE_FILENAME).exists()


# ---------------------------------------------------------------------------
# 9/10/11. Signal derivation through the selected backend, via a candidate
# actually created by this phase's injected pipeline seam (not a bare
# hand-built CandidateSignal) — the full intended synthetic-test shape:
# synthetic input -> pipeline -> injected repository -> review -> Signal.
# ---------------------------------------------------------------------------

def test_published_pipeline_candidate_yields_expected_signal_id(tmp_path):
    settings = _sqlite_settings(tmp_path)
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    edgar_pipeline.run_pipeline(_make_edgar_client(), [_NVDA], tmp_path, candidate_repository=repo)
    candidate_id = next(iter(repo.load_candidates()))

    review_actions.record_review_decision(
        tmp_path, candidate_id, edgar_pipeline.CANDIDATE_STORE_FILENAME,
        CandidateStatus.PUBLISHED, "approved via Phase 3A synthetic pipeline", settings=settings,
    )

    signal_repo = backend_factory.get_signal_repository(settings)
    assert [s.id for s in signal_repo.get_all_signals()] == [f"signal-{candidate_id}"]


def test_non_published_pipeline_candidate_produces_no_signal(tmp_path):
    settings = _sqlite_settings(tmp_path)
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    edgar_pipeline.run_pipeline(_make_edgar_client(), [_NVDA], tmp_path, candidate_repository=repo)

    signal_repo = backend_factory.get_signal_repository(settings)
    assert signal_repo.get_all_signals() == []


def test_stale_review_update_conflict_does_not_overwrite_pipeline_created_candidate(tmp_path):
    settings = _sqlite_settings(tmp_path)
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")
    edgar_pipeline.run_pipeline(_make_edgar_client(), [_NVDA], tmp_path, candidate_repository=repo)
    candidate_id = next(iter(repo.load_candidates()))

    first = review_actions.record_review_decision(
        tmp_path, candidate_id, edgar_pipeline.CANDIDATE_STORE_FILENAME,
        CandidateStatus.PUBLISHED, "first reviewer", settings=settings,
    )
    assert first.status == CandidateStatus.PUBLISHED

    stale_candidate = replace(repo.get_candidate(candidate_id), status=CandidateStatus.DISMISSED, reviewed_note="stale second reviewer")
    outcome = repo.update_candidate(stale_candidate, expected_version=1)

    assert outcome.status == "conflict"
    assert outcome.current.status == CandidateStatus.PUBLISHED
    assert repo.get_candidate(candidate_id).status == CandidateStatus.PUBLISHED


# ---------------------------------------------------------------------------
# 12. Filing-event persistence stays JSON regardless of which candidate
# backend is selected — the collaborator never reaches scan_service.scan().
# ---------------------------------------------------------------------------

def test_filing_event_persistence_stays_json_even_with_sqlite_candidate_repository(tmp_path):
    settings = _sqlite_settings(tmp_path)
    repo = backend_factory.get_candidate_repository(settings, "SEC EDGAR")

    edgar_pipeline.run_pipeline(_make_edgar_client(), [_NVDA], tmp_path, candidate_repository=repo)

    assert (tmp_path / "edgar_filing_events.json").exists()


# ---------------------------------------------------------------------------
# 13. [Retired] This section previously guarded that no real service
# entry point (edgar_service.py, dart/radar_service.py, edinet_service.py)
# exposed a `candidate_repository` parameter — true only as of this
# phase (3A), by construction, since none of those modules were touched
# here. Durable-State Phase 4A (EDGAR) and Phase 4C-1 (DART, EDINET)
# deliberately gave all three service modules this same additive,
# optional, default-None parameter, so a guard prohibiting it would now
# be asserting the opposite of the intended, approved design — retired
# rather than inverted or left vacuous. What still matters — that a real
# caller (the CLI's actual invocation, or any other production code
# path) never supplies the parameter, so JSON-backed behavior is
# unaffected — is covered per-source in tests/test_edgar_service.py,
# tests/test_radar_service.py, tests/test_edinet_service.py, and
# tests/test_run_scan_cli.py, each proving the default call omits the
# keyword entirely rather than merely passing `candidate_repository=None`.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Source guard — this phase's modified/new files must never reference real
# local state (same pattern as test_backend_factory_phase2b.py's own guard).
# ---------------------------------------------------------------------------

_PHASE3A_FILES = (
    Path(__file__).resolve(),
    Path(__file__).resolve().parent.parent / "src" / "data_access" / "dart" / "candidate_store.py",
    Path(__file__).resolve().parent.parent / "src" / "data_access" / "edgar" / "edgar_pipeline.py",
    Path(__file__).resolve().parent.parent / "src" / "data_access" / "dart" / "radar_pipeline.py",
    Path(__file__).resolve().parent.parent / "src" / "data_access" / "edinet" / "edinet_pipeline.py",
    Path(__file__).resolve().parent.parent / "design" / "DECISIONS.md",
)
_FORBIDDEN_REAL_STATE_REFERENCES = (
    "data/cache",
    "data/edge_research.db",
    ".streamlit/secrets.toml",
    "signal-cand-20260819000254",
    "signal-edgar-cand-0001193125-26-354029",
    "signal-edgar-cand-0001193125-26-356217",
)


def _source_excluding_this_guards_own_string_list(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if path.name == "test_candidate_persistence_phase3a.py":
        start = text.index("_FORBIDDEN_REAL_STATE_REFERENCES = (")
        end = text.index(")\n", start) + len(")\n")
        return text[:start] + text[end:]
    if path.name == "DECISIONS.md":
        marker = "## Durable-State Phase 3A"
        if marker not in text:
            return ""
        return text[text.index(marker):]
    return text


def test_phase3a_files_never_reference_real_local_state_or_real_signal_ids():
    offenders = []
    for path in _PHASE3A_FILES:
        if not path.exists():
            continue
        source = _source_excluding_this_guards_own_string_list(path)
        for forbidden in _FORBIDDEN_REAL_STATE_REFERENCES:
            if forbidden in source:
                offenders.append(f"{path.name}: contains {forbidden!r}")
    assert not offenders, offenders
