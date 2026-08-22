"""Tests for the headless scan CLI (scripts/run_scan.py).

Defense in depth against a repeat of the incident documented in this
session's local dev note (not committed): every test in this module runs
under an autouse fixture that blocks the two network-capable paths this
CLI can reach — requests.Session.request (what DartClient/EdgarClient/
EdinetClient all funnel every HTTP call through) and
run_scan.build_r2_client (the real-R2-client factory). A test that
forgets to mock a source's run_scan() or the remote-cache client factory
therefore fails loudly with an AssertionError instead of silently
reaching a live provider — see test_missed_scan_mock_cannot_reach_network
below, which exercises this guard directly.

No real or realistic credential value appears anywhere in this file —
every fake value is an obviously-placeholder string used only to prove
it never leaks into CLI output.

Durable-State Phase 4A: main() gained an additive, optional,
default-None `edgar_candidate_repository` parameter — no new
environment variable, never read from get_settings(). The tests below
prove the CLI's real invocation (main() called with no arguments, as
`python -m scripts.run_scan` always does) makes its EDGAR run_scan()
call with zero extra keyword arguments — not merely
`candidate_repository=None` — so the default call shape is unchanged
byte-for-byte, and that DART/EDINET's run_scan() calls never receive
this parameter under any circumstance."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests

from src.config.settings import Settings
from src.data_access.remote_cache.interfaces import ObjectStorageClient
from scripts import run_scan


def _blocked_request(*_args, **_kwargs):
    raise AssertionError(
        "Network call blocked in tests/test_run_scan_cli.py — a run_scan()/build_r2_client mock is missing."
    )


@pytest.fixture(autouse=True)
def _block_all_network_calls(monkeypatch):
    """Blocks requests.Session.request (every HTTP verb funnels through
    it) and run_scan.build_r2_client for the whole module. Individual
    tests that legitimately exercise the remote-cache path re-patch
    build_r2_client within their own `with` block, which correctly
    overrides this default for that block's duration only."""
    monkeypatch.setattr(requests.sessions.Session, "request", _blocked_request)
    monkeypatch.setattr(run_scan, "build_r2_client", _blocked_request)


def _fake_report(source: str, bgn_field: str = "bgn_de", end_field: str = "end_de", **overrides):
    fields = dict(
        scan_id=f"scan-{source}-1", started_at="2026-08-19T00:00:00+00:00", completed_at="2026-08-19T00:00:05+00:00",
        companies=("Company A",), source=source, filings_discovered=3, new_filing_events=1,
        candidates_detected=1, candidates_processed=1, candidates_deferred=0, documents_retrieved=1,
        documents_extracted=1, already_seen_count=2, no_data_count=0, errors_by_category={}, cache_hits=0,
        warnings=(),
    )
    fields[bgn_field] = "20260812"
    fields[end_field] = "20260819"
    fields.update(overrides)
    return SimpleNamespace(**fields)


class FakeR2Client(ObjectStorageClient):
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_object(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    def get_object(self, key: str):
        return self.objects.get(key)


def _disabled_settings(tmp_path) -> Settings:
    return Settings(remote_cache_enabled=False, cache_dir=tmp_path)


def _remote_enabled_settings(tmp_path) -> Settings:
    return Settings(
        remote_cache_enabled=True, r2_access_key_id="placeholder-key-id", r2_secret_access_key="placeholder-secret",
        r2_bucket="placeholder-bucket", r2_endpoint="https://example.invalid", cache_dir=tmp_path,
    )


def _mock_all_sources_ok():
    return (
        patch.object(run_scan.radar_service, "run_scan", return_value=_fake_report("OpenDART / DART")),
        patch.object(run_scan.edgar_service, "run_scan", return_value=_fake_report("SEC EDGAR", "bgn_date", "end_date")),
        patch.object(run_scan.edinet_service, "run_scan", return_value=_fake_report("EDINET", "bgn_date", "end_date")),
    )


# --- Normal success ---


def test_main_returns_zero_when_all_sources_succeed(tmp_path, capsys):
    settings = _disabled_settings(tmp_path)
    dart_p, edgar_p, edinet_p = _mock_all_sources_ok()
    with patch.object(run_scan, "get_settings", return_value=settings), dart_p, edgar_p, edinet_p:
        exit_code = run_scan.main()

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "DART: ok" in out
    assert "EDGAR: ok" in out
    assert "EDINET: ok" in out
    assert "scan_id=scan-OpenDART / DART-1" in out


def test_main_includes_result_counts_for_ci_diagnosis(tmp_path, capsys):
    settings = _disabled_settings(tmp_path)
    with (
        patch.object(run_scan, "get_settings", return_value=settings),
        patch.object(run_scan.radar_service, "run_scan", return_value=_fake_report("OpenDART / DART", new_filing_events=7, candidates_detected=4)),
        patch.object(run_scan.edgar_service, "run_scan", return_value=_fake_report("SEC EDGAR", "bgn_date", "end_date")),
        patch.object(run_scan.edinet_service, "run_scan", return_value=_fake_report("EDINET", "bgn_date", "end_date")),
    ):
        run_scan.main()

    out = capsys.readouterr().out
    assert "new_filing_events=7" in out
    assert "candidates_detected=4" in out


def test_main_reports_errors_by_category_without_failing(tmp_path, capsys):
    """A source completing with per-item errors already recorded inside
    its own ScanReport (e.g. one company hit a transient error) is a
    normal completed scan in this pipeline's own vocabulary — not a CLI
    failure. Matches scan_service.scan()'s own "continue for the
    remaining companies rather than aborting" design. The summary must
    still clearly surface the counts."""
    settings = _disabled_settings(tmp_path)
    report_with_errors = _fake_report(
        "OpenDART / DART", errors_by_category={"scan_error": 1}, warnings=("Company B: DART request timed out.",),
    )
    with (
        patch.object(run_scan, "get_settings", return_value=settings),
        patch.object(run_scan.radar_service, "run_scan", return_value=report_with_errors),
        patch.object(run_scan.edgar_service, "run_scan", return_value=_fake_report("SEC EDGAR", "bgn_date", "end_date")),
        patch.object(run_scan.edinet_service, "run_scan", return_value=_fake_report("EDINET", "bgn_date", "end_date")),
    ):
        exit_code = run_scan.main()

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "errors_by_category={'scan_error': 1}" in out
    assert "Company B: DART request timed out." in out


# --- Durable-State Phase 4A: optional edgar_candidate_repository ---


def test_main_default_call_omits_candidate_repository_from_edgar_run_scan(tmp_path, capsys):
    settings = _disabled_settings(tmp_path)
    captured = {}

    def _spy_edgar_run_scan(settings_arg, **kwargs):
        captured["kwargs"] = kwargs
        return _fake_report("SEC EDGAR", "bgn_date", "end_date")

    with (
        patch.object(run_scan, "get_settings", return_value=settings),
        patch.object(run_scan.radar_service, "run_scan", return_value=_fake_report("OpenDART / DART")),
        patch.object(run_scan.edgar_service, "run_scan", side_effect=_spy_edgar_run_scan),
        patch.object(run_scan.edinet_service, "run_scan", return_value=_fake_report("EDINET", "bgn_date", "end_date")),
    ):
        exit_code = run_scan.main()  # the CLI's real, argument-free invocation

    assert exit_code == 0
    assert captured["kwargs"] == {}  # not {"candidate_repository": None} — omitted entirely


def test_main_threads_a_supplied_edgar_candidate_repository_to_edgar_only(tmp_path):
    settings = _disabled_settings(tmp_path)
    sentinel_repo = object()  # identity check only — a synthetic/local-test-only caller, never the CLI
    edgar_captured, dart_captured, edinet_captured = {}, {}, {}

    def _spy_edgar_run_scan(settings_arg, **kwargs):
        edgar_captured["kwargs"] = kwargs
        return _fake_report("SEC EDGAR", "bgn_date", "end_date")

    def _spy_dart_run_scan(settings_arg, **kwargs):
        dart_captured["kwargs"] = kwargs
        return _fake_report("OpenDART / DART")

    def _spy_edinet_run_scan(settings_arg, **kwargs):
        edinet_captured["kwargs"] = kwargs
        return _fake_report("EDINET", "bgn_date", "end_date")

    with (
        patch.object(run_scan, "get_settings", return_value=settings),
        patch.object(run_scan.radar_service, "run_scan", side_effect=_spy_dart_run_scan),
        patch.object(run_scan.edgar_service, "run_scan", side_effect=_spy_edgar_run_scan),
        patch.object(run_scan.edinet_service, "run_scan", side_effect=_spy_edinet_run_scan),
    ):
        exit_code = run_scan.main(edgar_candidate_repository=sentinel_repo)

    assert exit_code == 0
    assert edgar_captured["kwargs"] == {"candidate_repository": sentinel_repo}
    # DART/EDINET never receive it, even when supplied for EDGAR.
    assert dart_captured["kwargs"] == {}
    assert edinet_captured["kwargs"] == {}


# --- Intended cache fallback / disabled remote cache still counts as success ---


def test_main_returns_zero_when_remote_cache_disabled_local_fallback(tmp_path, capsys):
    settings = _disabled_settings(tmp_path)
    dart_p, edgar_p, edinet_p = _mock_all_sources_ok()
    with patch.object(run_scan, "get_settings", return_value=settings), dart_p, edgar_p, edinet_p:
        exit_code = run_scan.main()

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "remote cache: skipped" in out


def test_main_returns_zero_when_remote_sync_succeeds(tmp_path, capsys):
    settings = _remote_enabled_settings(tmp_path)
    (tmp_path / "dart_candidates.json").write_text("{}", encoding="utf-8")
    fake_client = FakeR2Client()
    dart_p, edgar_p, edinet_p = _mock_all_sources_ok()
    with (
        patch.object(run_scan, "get_settings", return_value=settings),
        dart_p, edgar_p, edinet_p,
        patch.object(run_scan, "build_r2_client", return_value=fake_client),
    ):
        exit_code = run_scan.main()

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "remote cache: synced" in out


# --- Source-level unexpected exception ---


def test_main_returns_nonzero_when_one_source_raises(tmp_path, capsys):
    settings = _disabled_settings(tmp_path)
    with (
        patch.object(run_scan, "get_settings", return_value=settings),
        patch.object(run_scan.radar_service, "run_scan", side_effect=RuntimeError("simulated unexpected failure")),
        patch.object(run_scan.edgar_service, "run_scan", return_value=_fake_report("SEC EDGAR", "bgn_date", "end_date")),
        patch.object(run_scan.edinet_service, "run_scan", return_value=_fake_report("EDINET", "bgn_date", "end_date")),
    ):
        exit_code = run_scan.main()

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "DART: FAILED" in captured.out
    assert "simulated unexpected failure" in captured.err
    # The other two sources still ran — one source failing must not
    # abort the rest, matching the existing pipeline's own per-item
    # graceful-continue philosophy extended one level up.
    assert "EDGAR: ok" in captured.out
    assert "EDINET: ok" in captured.out


def test_main_returns_nonzero_when_all_sources_raise(tmp_path):
    settings = _disabled_settings(tmp_path)
    with (
        patch.object(run_scan, "get_settings", return_value=settings),
        patch.object(run_scan.radar_service, "run_scan", side_effect=RuntimeError("dart down")),
        patch.object(run_scan.edgar_service, "run_scan", side_effect=RuntimeError("edgar down")),
        patch.object(run_scan.edinet_service, "run_scan", side_effect=RuntimeError("edinet down")),
    ):
        exit_code = run_scan.main()

    assert exit_code == 1


# --- Explicit remote-sync failure now causes exit 1 ---


def test_main_remote_sync_failure_causes_exit_one_but_keeps_source_summaries(tmp_path, capsys):
    settings = _remote_enabled_settings(tmp_path)

    def _raising_factory(_settings):
        raise ConnectionError(f"simulated: could not reach {settings.r2_endpoint}/{settings.r2_bucket}")

    dart_p, edgar_p, edinet_p = _mock_all_sources_ok()
    with (
        patch.object(run_scan, "get_settings", return_value=settings),
        dart_p, edgar_p, edinet_p,
        patch.object(run_scan, "build_r2_client", side_effect=_raising_factory),
    ):
        exit_code = run_scan.main()

    assert exit_code == 1
    captured = capsys.readouterr()
    # Source summaries are still printed even though remote sync failed.
    assert "DART: ok" in captured.out
    assert "EDGAR: ok" in captured.out
    assert "EDINET: ok" in captured.out
    assert "remote cache: sync failed (ConnectionError)" in captured.out
    assert "remote cache sync failed" in captured.err
    # Sanitization: the endpoint/bucket URL embedded in the raw exception
    # message must never appear anywhere in stdout or stderr — only the
    # exception's class name is safe to print.
    combined = captured.out + captured.err
    assert settings.r2_endpoint not in combined
    assert settings.r2_bucket not in combined
    assert "could not reach" not in combined  # the raw str(exc) text itself


def test_main_remote_sync_failure_when_disabled_is_not_reachable(tmp_path):
    """Sanity check on the gating itself: with remote caching disabled,
    build_r2_client is never even attempted, so its failure mode is moot
    — covered separately by the "skipped" tests above."""
    settings = _disabled_settings(tmp_path)
    dart_p, edgar_p, edinet_p = _mock_all_sources_ok()
    with patch.object(run_scan, "get_settings", return_value=settings), dart_p, edgar_p, edinet_p:
        # build_r2_client stays blocked (autouse fixture) and is not called.
        exit_code = run_scan.main()
    assert exit_code == 0


# --- No credential/URL leakage ---


def test_main_never_prints_credential_values(tmp_path, capsys):
    settings = Settings(
        remote_cache_enabled=False, cache_dir=tmp_path,
        dart_api_key="placeholder-dart-key", translation_api_key="placeholder-deepl-key",
        edgar_user_agent="EevaResearch test@example.com", edinet_subscription_key="placeholder-edinet-key",
    )
    dart_p, edgar_p, edinet_p = _mock_all_sources_ok()
    with patch.object(run_scan, "get_settings", return_value=settings), dart_p, edgar_p, edinet_p:
        run_scan.main()

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "placeholder-dart-key" not in combined
    assert "placeholder-deepl-key" not in combined
    assert "placeholder-edinet-key" not in combined


def test_main_never_prints_r2_credentials_or_endpoint_on_success(tmp_path, capsys):
    settings = _remote_enabled_settings(tmp_path)
    (tmp_path / "dart_candidates.json").write_text("{}", encoding="utf-8")
    fake_client = FakeR2Client()
    dart_p, edgar_p, edinet_p = _mock_all_sources_ok()
    with (
        patch.object(run_scan, "get_settings", return_value=settings),
        dart_p, edgar_p, edinet_p,
        patch.object(run_scan, "build_r2_client", return_value=fake_client),
    ):
        run_scan.main()

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert settings.r2_access_key_id not in combined
    assert settings.r2_secret_access_key not in combined
    assert settings.r2_endpoint not in combined


# --- Proof the network guard itself works ---


def test_missed_scan_mock_cannot_reach_network():
    """Proves the autouse network guard actually blocks a real DART HTTP
    call — the exact failure mode of the incident documented in this
    session's local dev note, where a missing mock let the real pipeline
    reach DART's live API. Calls DartClient.search_disclosures() (the
    same method scan_service.scan() calls per page) directly, rather
    than through the full run_scan() pipeline: an empty tmp_path has no
    resolved corp_code cache, so the pipeline itself would skip every
    company before ever reaching the network — a real but incidental
    safety net that would mask whether this guard is doing anything.
    Calling the client method directly removes that ambiguity."""
    from src.data_access.dart.client import DartClient

    client = DartClient(api_key="placeholder-dart-key")
    with pytest.raises(AssertionError, match="Network call blocked"):
        client.search_disclosures(corp_code="00126380", bgn_de="20260101", end_de="20260101")


def test_missed_remote_cache_mock_cannot_reach_network(tmp_path):
    """Same proof for the remote-cache path: build_r2_client is blocked
    by default, so a test that forgets to patch it fails loudly rather
    than attempting a real R2/boto3 connection."""
    settings = _remote_enabled_settings(tmp_path)
    with pytest.raises(AssertionError, match="Network call blocked"):
        run_scan.build_r2_client(settings)
