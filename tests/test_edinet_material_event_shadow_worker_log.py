"""EDINET Extraordinary Report shadow-observation workstream (design/
DECISIONS.md) — the Radar worker's own bounded, EDINET-only, flag-gated
log line in scripts/radar_worker.py::_run_provider_tick(). Follows
tests/test_radar_worker_dart_edinet_research_case_integration.py's own
established conventions exactly: a real in-file SQLite backend, a fake
`run_scan` service module (types.SimpleNamespace), no real worker process
or live network call. Never invokes translation, never creates a
CandidateSignal, never writes a Radar item/theme match/research case/
company-discovery record — the fake run_scan report is the only source of
`shadow_material_event_matches` data this file ever supplies."""
from __future__ import annotations

import types

from scripts import radar_worker
from src.config.settings import Settings
from src.data_access import backend_factory
from src.data_access.edinet.material_event_shadow import ShadowMatch


def _ambient_settings(**overrides) -> Settings:
    fields = dict(
        radar_live_scan_enabled=True,
        radar_worker_db_backend="sqlite",
        radar_worker_state_db_path=None,
        radar_worker_state_db_url=None,
        edgar_auto_publish_enabled=False,
        edinet_material_event_lexicon_enabled=False,
    )
    fields.update(overrides)
    return Settings(**fields)


def _worker_settings(tmp_path, **overrides) -> Settings:
    ambient = _ambient_settings(radar_worker_db_backend="sqlite", radar_worker_state_db_path=tmp_path / "state.db", **overrides)
    return radar_worker._build_worker_settings(ambient)


def _scan_status_repo(worker_settings):
    return backend_factory.get_scan_status_repository(worker_settings)


def _fake_edinet_report(shadow_matches=()):
    return types.SimpleNamespace(
        candidates_detected=0, candidates_processed=0, warnings=(), end_date="2026-09-03",
        shadow_material_event_matches=shadow_matches,
    )


def _set_edinet_run_scan(monkeypatch, report):
    monkeypatch.setitem(
        radar_worker._SERVICE_MODULES, "edinet",
        types.SimpleNamespace(run_scan=lambda settings, candidate_repository=None: report),
    )


_SAMPLE_MATCH = ShadowMatch(
    doc_id="S100SHADOW", issuer_name="Fictional Test Co.", title="臨時報告書", triplet="010:053000:180",
)


def test_flag_false_prints_no_shadow_log_line_even_with_matches_present(tmp_path, monkeypatch, capsys):
    # A report that DOES carry shadow matches (proving the worker itself
    # decides whether to log, not the report's own presence/absence of
    # data) — with the flag off, nothing shadow-related may print.
    worker_settings = _worker_settings(tmp_path, edinet_material_event_lexicon_enabled=False)
    _set_edinet_run_scan(monkeypatch, _fake_edinet_report(shadow_matches=(_SAMPLE_MATCH,)))
    scan_status_repo = _scan_status_repo(worker_settings)

    radar_worker._run_provider_tick("edinet", worker_settings, scan_status_repo)

    out = capsys.readouterr().out
    assert "edinet_material_event_shadow_matches" not in out
    assert "shadow match" not in out


def test_flag_true_with_zero_matches_prints_the_zero_count_line(tmp_path, monkeypatch, capsys):
    worker_settings = _worker_settings(tmp_path, edinet_material_event_lexicon_enabled=True)
    _set_edinet_run_scan(monkeypatch, _fake_edinet_report(shadow_matches=()))
    scan_status_repo = _scan_status_repo(worker_settings)

    radar_worker._run_provider_tick("edinet", worker_settings, scan_status_repo)

    out = capsys.readouterr().out
    assert "EDINET: edinet_material_event_shadow_matches=0" in out
    assert "shadow match" not in out


def test_flag_true_with_matches_prints_the_count_and_bounded_detail_lines(tmp_path, monkeypatch, capsys):
    worker_settings = _worker_settings(tmp_path, edinet_material_event_lexicon_enabled=True)
    _set_edinet_run_scan(monkeypatch, _fake_edinet_report(shadow_matches=(_SAMPLE_MATCH,)))
    scan_status_repo = _scan_status_repo(worker_settings)

    radar_worker._run_provider_tick("edinet", worker_settings, scan_status_repo)

    out = capsys.readouterr().out
    assert "EDINET: edinet_material_event_shadow_matches=1" in out
    assert "docID=S100SHADOW" in out
    assert "issuer=Fictional Test Co." in out
    assert "title=臨時報告書" in out
    assert "triplet=010:053000:180" in out


def test_shadow_log_list_is_capped(tmp_path, monkeypatch, capsys):
    many_matches = tuple(
        ShadowMatch(doc_id=f"S100SHADOW{i}", issuer_name=f"Co {i}", title="臨時報告書", triplet="010:053000:180")
        for i in range(9)
    )
    worker_settings = _worker_settings(tmp_path, edinet_material_event_lexicon_enabled=True)
    _set_edinet_run_scan(monkeypatch, _fake_edinet_report(shadow_matches=many_matches))
    scan_status_repo = _scan_status_repo(worker_settings)

    radar_worker._run_provider_tick("edinet", worker_settings, scan_status_repo)

    out = capsys.readouterr().out
    assert "EDINET: edinet_material_event_shadow_matches=9" in out  # the true count is always reported
    assert out.count("shadow match —") == radar_worker._SHADOW_MATERIAL_EVENT_LOG_CAP  # but detail lines are capped
    assert "S100SHADOW5" not in out  # the 6th+ entries never print their own detail line


def test_shadow_log_never_contains_the_subscription_key_or_raw_document_text(tmp_path, monkeypatch, capsys):
    worker_settings = _worker_settings(
        tmp_path, edinet_material_event_lexicon_enabled=True, edinet_subscription_key="super-secret-key-value",
    )
    _set_edinet_run_scan(monkeypatch, _fake_edinet_report(shadow_matches=(_SAMPLE_MATCH,)))
    scan_status_repo = _scan_status_repo(worker_settings)

    radar_worker._run_provider_tick("edinet", worker_settings, scan_status_repo)

    out = capsys.readouterr().out
    assert "super-secret-key-value" not in out


def test_flag_true_for_edgar_or_dart_provider_key_never_prints_the_edinet_shadow_line(tmp_path, monkeypatch, capsys):
    # The gate is provider_key == "edinet" specifically — even with the
    # flag set, an EDGAR/DART tick must never print this EDINET-only line.
    worker_settings = _worker_settings(tmp_path, edinet_material_event_lexicon_enabled=True)
    monkeypatch.setitem(
        radar_worker._SERVICE_MODULES, "edgar",
        types.SimpleNamespace(run_scan=lambda settings, candidate_repository=None: types.SimpleNamespace(
            candidates_detected=0, candidates_processed=0, warnings=(), end_de="2026-09-03",
        )),
    )
    scan_status_repo = _scan_status_repo(worker_settings)

    radar_worker._run_provider_tick("edgar", worker_settings, scan_status_repo)

    out = capsys.readouterr().out
    assert "edinet_material_event_shadow_matches" not in out
    assert "shadow match" not in out
