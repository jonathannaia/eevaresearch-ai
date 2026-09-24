"""Each Radar funnel stage is counted, and counted as what it says.

Two assignments in scripts/radar_worker.py stored a value its column
name denied:

    items_discovered   = report.candidates_detected   # post-filter
    candidates_created = report.candidates_processed  # not creation

The first made a quiet source and a source whose rows we threw away the
same observation -- every count below the matcher reads zero either way,
which is precisely the question a throughput investigation has to
answer. The second stored retrieval/extraction work under a name that
reads as creation, and has already been misread as "no writes happened"
during a live check.

Both are fixed, and both are pinned here two ways: behaviourally, with
fake reports whose every count differs so an assertion can only pass for
the field it names, and at source level, so the pairing cannot quietly
come back.

The EDINET half is a real scan, not a fake: it is the one provider that
queries a whole day's document list and matches tracked companies
afterwards, so it is the one provider that can tell an empty response
from a full one none of whose rows are ours. EDGAR and DART query per
tracked company and have no such stage -- asserted here, so nobody later
"fixes" the asymmetry by inventing a number for them."""
from __future__ import annotations

import tempfile
import types
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.config.settings import Settings
from src.config.tracked_companies import TrackedCompany
from src.data_access import backend_factory
from src.data_access.dart import scan_service as dart_scan
from src.data_access.dart.client import DisclosureRecord
from src.data_access.edgar import scan_service as edgar_scan
from src.data_access.edinet import scan_service as edinet_scan

import scripts.radar_worker as radar_worker

WORKER_SOURCE = Path(__file__).parent.parent / "scripts" / "radar_worker.py"


# --- fixtures -------------------------------------------------------------------

_EDINET_CO = TrackedCompany(
    name="Acme Test Co", exchange="TSE", krx_code="1234", source="EDINET",
    themes=("ai-buildout",), corp_code="E00001",
)
_EDGAR_CO = TrackedCompany(
    name="NVIDIA", exchange="NASDAQ", krx_code="NVDA", source="SEC EDGAR",
    themes=("ai-buildout",), corp_code="0001045810",
)
_DART_CO = TrackedCompany(
    name="Samsung Electronics", exchange="KRX", krx_code="005930", source="OpenDART / DART",
    themes=("memory",), corp_code="00126380",
)


def _edinet_row(doc_id="S100TEST1", edinet_code="E00001"):
    return {
        "docID": doc_id, "docTypeCode": "120", "ordinanceCode": "010", "formCode": "030",
        "filerName": "Acme Test Co", "docDescription": "Test Filing", "edinetCode": edinet_code,
        "secCode": "1234", "submitDateTime": "2026-08-17 09:00", "issuerEdinetCode": "",
        "withdrawalStatus": "", "docInfoEditStatus": "", "disclosureStatus": "",
    }


def _edinet_envelope(results):
    return {
        "metadata": {"status": "200", "message": "OK", "resultset": {"count": len(results)}},
        "results": results,
    }


def _edinet_client(rows_by_date: dict):
    client = MagicMock()
    client.get_document_list.side_effect = lambda d, type_=2: rows_by_date.get(d, _edinet_envelope([]))
    return client


def _edinet_client_every_day(rows):
    client = MagicMock()
    client.get_document_list.side_effect = lambda d, type_=2: _edinet_envelope(rows)
    return client


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _edgar_client(accessions):
    client = MagicMock()
    n = len(accessions)
    client.get_submissions.return_value = {"filings": {"recent": {
        "accessionNumber": list(accessions),
        "filingDate": [_today()] * n,
        "form": ["8-K"] * n,
        "primaryDocument": ["doc.htm"] * n,
        "primaryDocDescription": ["desc"] * n,
    }}}
    return client


def _dart_client(records):
    client = MagicMock()
    client.search_disclosures.side_effect = lambda corp_code, bgn_de, end_de, page_no=1, page_count=100: (
        (list(records), len(records)) if page_no == 1 else ([], len(records))
    )
    return client


def _dart_record(rcept_no, report_nm="주요사항보고서(유상증자결정)"):
    return DisclosureRecord(
        corp_cls="Y", corp_name="삼성전자", corp_code="00126380", stock_code="005930",
        report_nm=report_nm, rcept_no=rcept_no, flr_nm="삼성전자", rcept_dt="20260810", rm="",
    )


# --- 1. EDINET: the stage no other provider has ---------------------------------

def test_edinet_counts_rows_that_match_no_tracked_company():
    """The whole point. Rows came back, none were ours -- so every count
    below the matcher is zero while normalized_rows_fetched is not.
    Before this field the two cases were one observation."""
    rows = [_edinet_row(doc_id=f"S100X{i}", edinet_code="E99999") for i in range(4)]
    result = edinet_scan.scan(_edinet_client_every_day(rows), [_EDINET_CO], Path(tempfile.mkdtemp()), lookback_days=1)

    assert result.normalized_rows_fetched > 0
    assert len(result.new_filing_events) == 0
    assert result.already_seen_count == 0
    assert len(result.new_candidate_signals) == 0


def test_edinet_zero_rows_is_distinguishable_from_unmatched_rows():
    """Same downstream zeros, different upstream number -- which is the
    distinction the whole change exists to make."""
    empty = edinet_scan.scan(_edinet_client({}), [_EDINET_CO], Path(tempfile.mkdtemp()), lookback_days=1)
    unmatched_rows = [_edinet_row(doc_id=f"S100Y{i}", edinet_code="E99999") for i in range(3)]
    unmatched = edinet_scan.scan(
        _edinet_client_every_day(unmatched_rows), [_EDINET_CO], Path(tempfile.mkdtemp()), lookback_days=1,
    )

    assert empty.normalized_rows_fetched == 0
    assert unmatched.normalized_rows_fetched > 0
    # Identical below the matcher: without the new field these two runs
    # are indistinguishable in every recorded number.
    assert len(empty.new_filing_events) == len(unmatched.new_filing_events) == 0
    assert len(empty.new_candidate_signals) == len(unmatched.new_candidate_signals) == 0


def test_edinet_normalized_rows_is_never_below_what_it_feeds():
    """Every filing counted downstream came from one of these rows."""
    rows = [_edinet_row(doc_id="S100A1"), _edinet_row(doc_id="S100A2", edinet_code="E99999")]
    result = edinet_scan.scan(_edinet_client_every_day(rows), [_EDINET_CO], Path(tempfile.mkdtemp()), lookback_days=1)

    filings_discovered = len(result.new_filing_events) + result.already_seen_count
    assert result.normalized_rows_fetched >= filings_discovered
    assert result.normalized_rows_fetched >= len(result.new_candidate_signals)


def test_edinet_counts_rows_upstream_of_dedupe():
    """A second scan over the same rows: the cache suppresses them, the
    upstream count does not move."""
    cache = Path(tempfile.mkdtemp())
    rows = [_edinet_row(doc_id="S100B1")]
    first = edinet_scan.scan(_edinet_client_every_day(rows), [_EDINET_CO], cache, lookback_days=1)
    second = edinet_scan.scan(_edinet_client_every_day(rows), [_EDINET_CO], cache, lookback_days=1)

    assert len(first.new_filing_events) == 1
    assert len(second.new_filing_events) == 0          # deduped
    assert second.already_seen_count >= 1
    assert second.normalized_rows_fetched == first.normalized_rows_fetched


# --- 2. EDGAR and DART: discovery exceeds candidates, via dedupe ------------------

def test_edgar_filings_discovered_exceeds_candidates_detected():
    with tempfile.TemporaryDirectory() as d:
        client = _edgar_client([f"0001045810-26-00000{i}" for i in range(1, 5)])
        result = edgar_scan.scan(client, [_EDGAR_CO], Path(d), lookback_days=30)

    filings_discovered = len(result.new_filing_events) + result.already_seen_count
    assert filings_discovered > 0
    # Plain 8-Ks with no qualifying items: discovered, then dropped by
    # the rules. The gap between these two numbers is the whole funnel.
    assert filings_discovered >= len(result.new_candidate_signals)


def test_edgar_rescan_moves_rows_into_already_seen_not_out_of_discovery():
    with tempfile.TemporaryDirectory() as d:
        client = _edgar_client(["0001045810-26-000001", "0001045810-26-000002"])
        first = edgar_scan.scan(client, [_EDGAR_CO], Path(d), lookback_days=30)
        second = edgar_scan.scan(client, [_EDGAR_CO], Path(d), lookback_days=30)

    first_discovered = len(first.new_filing_events) + first.already_seen_count
    second_discovered = len(second.new_filing_events) + second.already_seen_count
    assert len(second.new_filing_events) == 0
    assert second.already_seen_count == first_discovered
    assert second_discovered == first_discovered


def test_dart_filings_discovered_exceeds_candidates_detected(tmp_path):
    records = [_dart_record(f"2026081000000{i}") for i in range(1, 4)]
    result = dart_scan.scan(_dart_client(records), [_DART_CO], tmp_path)

    filings_discovered = len(result.new_filing_events) + result.already_seen_count
    assert filings_discovered == 3
    assert filings_discovered >= len(result.new_candidate_signals)


def test_dart_rescan_moves_rows_into_already_seen(tmp_path):
    records = [_dart_record("20260810000001")]
    first = dart_scan.scan(_dart_client(records), [_DART_CO], tmp_path)
    second = dart_scan.scan(_dart_client(records), [_DART_CO], tmp_path)

    assert len(first.new_filing_events) == 1
    assert len(second.new_filing_events) == 0
    assert second.already_seen_count == 1


def test_edgar_and_dart_have_no_whole_market_row_count():
    """Asserted, not assumed. Both query per tracked company, so "rows
    that matched no tracked company" is a stage they do not have, and
    filings_discovered is already their earliest truthful count. If
    either ever grows this field it must come with a real pre-match
    fetch, not a copied number."""
    with tempfile.TemporaryDirectory() as d:
        edgar_result = edgar_scan.scan(_edgar_client(["0001045810-26-000001"]), [_EDGAR_CO], Path(d), lookback_days=30)
    assert not hasattr(edgar_result, "normalized_rows_fetched")

    with tempfile.TemporaryDirectory() as d:
        dart_result = dart_scan.scan(_dart_client([_dart_record("20260810000001")]), [_DART_CO], Path(d))
    assert not hasattr(dart_result, "normalized_rows_fetched")


# --- 3. the worker stores each count under its own name --------------------------

@dataclass
class _Report:
    """Every count distinct, so an assertion can only pass for the field
    it names."""
    filings_discovered: int = 9
    new_filing_events: int = 6
    already_seen_count: int = 3
    candidates_detected: int = 2
    candidates_processed: int = 1
    warnings: tuple = ()
    end_date: str = "2026-09-23"


@dataclass
class _EdinetReport(_Report):
    normalized_rows_fetched: int = 812
    deferred_status_count: int = 4


def _worker_settings(tmp_path) -> Settings:
    ambient = Settings(radar_worker_db_backend="sqlite", radar_worker_state_db_path=tmp_path / "state.db")
    return radar_worker._build_worker_settings(ambient)


def _run(provider: str, tmp_path, monkeypatch, report):
    worker_settings = _worker_settings(tmp_path)
    monkeypatch.setitem(
        radar_worker._SERVICE_MODULES, provider,
        types.SimpleNamespace(run_scan=lambda settings, candidate_repository=None: report),
    )
    repo = backend_factory.get_scan_status_repository(worker_settings)
    radar_worker._run_provider_tick(provider, worker_settings, repo)
    return repo


@pytest.mark.parametrize("provider,display", [
    ("edgar", "SEC EDGAR"), ("dart", "OpenDART / DART"), ("edinet", "EDINET"),
])
def test_status_stores_discovery_and_creation_under_their_own_names(provider, display, tmp_path, monkeypatch):
    status = _run(provider, tmp_path, monkeypatch, _Report()).get_scan_status(display)

    assert status is not None
    assert status.items_discovered == 9       # filings_discovered, not candidates_detected (2)
    assert status.candidates_created == 2     # candidates_detected, not candidates_processed (1)


@pytest.mark.parametrize("provider,display", [
    ("edgar", "SEC EDGAR"), ("dart", "OpenDART / DART"), ("edinet", "EDINET"),
])
def test_status_never_stores_the_old_wrong_values(provider, display, tmp_path, monkeypatch):
    """The behavioural half of the regression guard: with every count
    distinct, reinstating either old pairing changes these numbers."""
    report = _Report()
    status = _run(provider, tmp_path, monkeypatch, report).get_scan_status(display)

    assert status.items_discovered != report.candidates_detected
    assert status.candidates_created != report.candidates_processed


def test_zero_discovery_and_zero_candidates_stay_distinguishable(tmp_path, monkeypatch):
    """A tick that found nothing and a tick that found rows but created
    nothing must not write the same row."""
    nothing = _run("edgar", tmp_path / "a", monkeypatch, _Report(
        filings_discovered=0, new_filing_events=0, already_seen_count=0, candidates_detected=0,
    )).get_scan_status("SEC EDGAR")
    found = _run("edgar", tmp_path / "b", monkeypatch, _Report(
        filings_discovered=11, new_filing_events=11, already_seen_count=0, candidates_detected=0,
    )).get_scan_status("SEC EDGAR")

    assert nothing.items_discovered == 0
    assert found.items_discovered == 11
    assert nothing.candidates_created == found.candidates_created == 0


# --- 4. the log line carries every stage -----------------------------------------

def test_log_line_reports_each_stage_separately(tmp_path, monkeypatch, capsys):
    _run("edgar", tmp_path, monkeypatch, _Report())
    line = capsys.readouterr().out

    assert "filings_discovered=9" in line
    assert "new_filing_events=6" in line
    assert "already_seen=3" in line
    assert "candidates_detected=2" in line
    assert "candidates_processed=1" in line
    assert "skipped_unresolved=0" in line


def test_log_line_carries_edinet_only_stages(tmp_path, monkeypatch, capsys):
    _run("edinet", tmp_path, monkeypatch, _EdinetReport())
    line = capsys.readouterr().out

    assert "normalized_rows_fetched=812" in line
    assert "deferred_status=4" in line
    assert "filings_discovered=9" in line


@pytest.mark.parametrize("provider", ["edgar", "dart"])
def test_log_line_claims_no_pre_match_count_for_edgar_or_dart(provider, tmp_path, monkeypatch, capsys):
    """Silence is the honest output: neither provider has the stage."""
    _run(provider, tmp_path, monkeypatch, _Report())
    line = capsys.readouterr().out

    assert "normalized_rows_fetched" not in line
    assert "deferred_status" not in line


# --- 5. source-level regression guard --------------------------------------------

def test_worker_source_never_pairs_a_name_with_the_wrong_count():
    source = WORKER_SOURCE.read_text(encoding="utf-8")
    code = "\n".join(l for l in source.splitlines() if not l.lstrip().startswith("#"))

    assert "items_discovered=report.candidates_detected" not in code.replace(" ", "")
    assert "candidates_created=report.candidates_processed" not in code.replace(" ", "")
    assert "items_discovered=report.filings_discovered" in code.replace(" ", "")
    assert "candidates_created=report.candidates_detected" in code.replace(" ", "")


def test_edinet_report_carries_both_observability_fields():
    from src.data_access.edinet import edinet_pipeline

    names = edinet_pipeline.ScanReport.__dataclass_fields__
    assert "normalized_rows_fetched" in names
    assert "deferred_status_count" in names
