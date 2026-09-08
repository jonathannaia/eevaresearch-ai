"""radar_service — the Radar Inbox wiring layer. radar_readiness and
get_radar_companies are pure config/cache reads, no network.
run_scan/process_candidate_now's core scan/extraction/translation
behavior is already exercised end-to-end via test_radar_pipeline.py's
mocked-client tests — the tests below cover only Durable-State
Phase 4C-1's addition to those two wrappers: the optional, additive
`candidate_repository` parameter and its threading through to
radar_pipeline.run_pipeline/process_single_candidate. No test here makes
a real network call — either the pipeline-layer function is
monkeypatched directly, or radar_service._client/_translation_provider
are replaced with a MagicMock/fake, mirroring
tests/test_edgar_service.py's own Phase 4A pattern."""
from __future__ import annotations

import io
import json
import zipfile
from unittest.mock import MagicMock

from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.dart import radar_service
from src.data_access.dart.client import DisclosureRecord


def _settings(cache_dir, dart_key=None, translation_key=None) -> Settings:
    return Settings(dart_api_key=dart_key, translation_api_key=translation_key, cache_dir=cache_dir)


def _seed_corp_codes(cache_dir, krx_codes: list[str]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        krx: {"corp_code": f"corp-{krx}", "corp_name": "Test Co", "source": "OpenDART corpCode.xml", "retrieved_at": "2026-08-10T00:00:00+00:00"}
        for krx in krx_codes
    }
    (cache_dir / "dart_corp_codes.json").write_text(json.dumps(payload), encoding="utf-8")


_CORE_EXPANSION_DART_KRX_CODES = ["011070", "012450", "047810", "454910", "240810", "056190", "036540", "067310"]
_CORE_EXPANSION_DART_NAMES = {
    "LG Innotek Co., Ltd.", "Hanwha Aerospace Co., Ltd.", "Korea Aerospace Industries, Ltd.",
    "Doosan Robotics Inc.", "Wonik IPS Co., Ltd.", "SFA Engineering Corporation",
    "SFA Semicon Co., Ltd", "Hana Micron Inc.",
}


def test_readiness_reports_missing_keys_and_unresolved_companies(tmp_path):
    readiness = radar_service.radar_readiness(_settings(tmp_path))

    assert not readiness.dart_key_configured
    assert not readiness.translation_key_configured
    # Core Issuer Expansion batch (2026-09-04) added 8 more DART
    # companies, all correctly unresolved (corp_code left unset per that
    # batch's own scope — see tracked_companies.py's own comment).
    assert set(readiness.unresolved_companies) == {"Samsung Electronics", "SK Hynix"} | _CORE_EXPANSION_DART_NAMES
    assert not readiness.ready


def test_readiness_ready_when_keys_present_and_all_companies_resolved(tmp_path):
    _seed_corp_codes(tmp_path, ["005930", "000660"] + _CORE_EXPANSION_DART_KRX_CODES)

    readiness = radar_service.radar_readiness(_settings(tmp_path, dart_key="dart-key", translation_key="deepl-key"))

    assert readiness.dart_key_configured
    assert readiness.translation_key_configured
    assert readiness.unresolved_companies == ()
    assert readiness.ready


def test_readiness_flags_partially_resolved_companies(tmp_path):
    _seed_corp_codes(tmp_path, ["005930"] + _CORE_EXPANSION_DART_KRX_CODES)  # SK Hynix (000660) left unresolved

    readiness = radar_service.radar_readiness(_settings(tmp_path, dart_key="dart-key", translation_key="deepl-key"))

    assert readiness.unresolved_companies == ("SK Hynix",)
    assert not readiness.ready


def test_get_radar_companies_fills_in_resolved_corp_codes(tmp_path):
    _seed_corp_codes(tmp_path, ["005930", "000660"])

    companies = radar_service.get_radar_companies(tmp_path)

    by_krx = {c.krx_code: c for c in companies}
    assert by_krx["005930"].corp_code == "corp-005930"
    assert by_krx["000660"].corp_code == "corp-000660"


def test_get_radar_companies_leaves_unresolved_companies_with_none_corp_code(tmp_path):
    companies = radar_service.get_radar_companies(tmp_path)
    assert all(c.corp_code is None for c in companies)


# ---------------------------------------------------------------------------
# Durable-State Phase 4C-1 — run_scan/process_candidate_now's additive,
# optional candidate_repository parameter, threaded through to
# radar_pipeline.run_pipeline/process_single_candidate.
# ---------------------------------------------------------------------------


def test_run_scan_omits_candidate_repository_by_default(tmp_path, monkeypatch):
    captured = {}

    def _fake_run_pipeline(client, translation_provider, companies, cache_dir, lookback_days=None, max_candidates_to_process=None, candidate_repository=None, filing_candidate_shadow_enabled=False):
        captured["candidate_repository"] = candidate_repository
        return "sentinel-report"

    monkeypatch.setattr(radar_service.radar_pipeline, "run_pipeline", _fake_run_pipeline)

    result = radar_service.run_scan(_settings(tmp_path, dart_key="dart-key", translation_key="deepl-key"))

    assert result == "sentinel-report"
    assert captured["candidate_repository"] is None


def test_run_scan_passes_through_an_explicitly_supplied_repository(tmp_path, monkeypatch):
    captured = {}
    sentinel_repo = object()  # identity check only — no method on it is ever called this test

    def _fake_run_pipeline(client, translation_provider, companies, cache_dir, lookback_days=None, max_candidates_to_process=None, candidate_repository=None, filing_candidate_shadow_enabled=False):
        captured["candidate_repository"] = candidate_repository
        return "sentinel-report"

    monkeypatch.setattr(radar_service.radar_pipeline, "run_pipeline", _fake_run_pipeline)

    radar_service.run_scan(
        _settings(tmp_path, dart_key="dart-key", translation_key="deepl-key"), candidate_repository=sentinel_repo,
    )

    assert captured["candidate_repository"] is sentinel_repo


def test_process_candidate_now_omits_candidate_repository_by_default(tmp_path, monkeypatch):
    captured = {}

    def _fake_process_single_candidate(client, translation_provider, candidate_id, cache_dir, candidate_repository=None):
        captured["candidate_repository"] = candidate_repository
        return None

    monkeypatch.setattr(radar_service.radar_pipeline, "process_single_candidate", _fake_process_single_candidate)

    result = radar_service.process_candidate_now(
        _settings(tmp_path, dart_key="dart-key", translation_key="deepl-key"), "cand-1",
    )

    assert result is None
    assert captured["candidate_repository"] is None


def test_process_candidate_now_passes_through_an_explicitly_supplied_repository(tmp_path, monkeypatch):
    captured = {}
    sentinel_repo = object()

    def _fake_process_single_candidate(client, translation_provider, candidate_id, cache_dir, candidate_repository=None):
        captured["candidate_repository"] = candidate_repository
        return None

    monkeypatch.setattr(radar_service.radar_pipeline, "process_single_candidate", _fake_process_single_candidate)

    radar_service.process_candidate_now(
        _settings(tmp_path, dart_key="dart-key", translation_key="deepl-key"), "cand-1", candidate_repository=sentinel_repo,
    )

    assert captured["candidate_repository"] is sentinel_repo


# --- Real end-to-end proof, one level up from radar_pipeline.py's own
# Phase 3A equivalence tests: a mocked-client run_scan() call, with an
# injected synthetic local SQLite repository, produces the same one
# candidate a default/omitted JSON-backed call would. ---

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


def _make_dart_client(samsung_corp_code: str) -> MagicMock:
    record = DisclosureRecord(
        corp_cls="Y", corp_name="삼성전자", corp_code=samsung_corp_code, stock_code="005930",
        report_nm="신규시설투자등", rcept_no=_DART_RCEPT_NO, flr_nm="삼성전자", rcept_dt="20260810", rm="",
    )
    client = MagicMock()

    def _search(corp_code, bgn_de, end_de, page_no=1, page_count=100):
        if corp_code == samsung_corp_code and page_no == 1:
            return ([record], 1)
        return ([], 0)

    client.search_disclosures.side_effect = _search
    client.fetch_document_zip.side_effect = lambda rcept_no: _dart_document_zip()
    return client


class _FakeTranslationProvider:
    name = "DeepL"

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        return f"[translated] {text}"


def test_run_scan_injected_sqlite_repository_produces_equivalent_candidate(tmp_path, monkeypatch):
    from src.data_access.state_db.identifier_repository import ResolvedIdentifierRecord, upsert_resolved_identifier

    samsung_corp_code = "corp-005930"
    monkeypatch.setattr(radar_service, "_client", lambda settings: _make_dart_client(samsung_corp_code))
    monkeypatch.setattr(radar_service, "_translation_provider", lambda settings: _FakeTranslationProvider())
    settings = Settings(
        dart_api_key="dart-key", translation_api_key="deepl-key", cache_dir=tmp_path,
        db_backend="sqlite", state_db_path=tmp_path / "state.db",
    )
    repo = backend_factory.get_candidate_repository(settings, "OpenDART / DART")
    # Durable-State Phase 4M-0: run_scan() now resolves identifiers from
    # the *same* configured backend (see radar_service.get_radar_companies'
    # own docstring) — a SQLite-backend scan must seed the SQLite
    # identifier repository, not the on-disk JSON cache, matching
    # test_edgar_service.py's own sibling fix for the same phase.
    id_repo = backend_factory.get_identifier_repository(settings, "OpenDART / DART")
    for krx_code in ("005930", "000660"):
        upsert_resolved_identifier(
            id_repo.conn, "OpenDART / DART", krx_code,
            ResolvedIdentifierRecord(
                identifier=f"corp-{krx_code}", display_name="Test Co",
                resolution_method="synthetic-test-fixture", retrieved_at="2026-08-10T00:00:00+00:00",
            ),
        )

    report = radar_service.run_scan(settings, candidate_repository=repo)

    assert report.candidates_detected == 1
    stored = repo.load_candidates()
    assert len(stored) == 1
    candidate = next(iter(stored.values()))
    assert candidate.filing.rcept_no == _DART_RCEPT_NO
    assert candidate.filing.corp_code == samsung_corp_code
    # The SQLite path never touched the JSON candidate store or the JSON
    # identifier cache this call.
    assert not (tmp_path / "dart_candidates.json").exists()
    assert not (tmp_path / "dart_corp_codes.json").exists()
