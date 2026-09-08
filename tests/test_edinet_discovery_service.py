"""discovery_service — fully mocked EdinetClient, zero network calls.
Fixture document-list rows mirror test_edinet_scan_service.py's shape;
fixture code-list CSV mirrors test_edinet_code_resolver.py's shape. Both
are duplicated locally (not imported across test files) so this file
stays self-contained, matching this repo's existing per-file fixture
convention.

Real EDINET data used only where explicitly noted: the one real entry in
edinet_rules.DEFAULT_CODE_CATEGORY_MAP ("010:030000:120" ->
"annual_securities_report") is exercised directly, unmodified — proving
discovery reuses the real rule engine as-is, including its current
confidence ceiling (see the dedicated test below)."""
from __future__ import annotations

import io
import zipfile
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

from src.config.tracked_companies import TrackedCompany
from src.data_access.edinet import discovery_service, edinet_code_resolver
from src.data_access.edinet.errors import EdinetError

_TRACKED = TrackedCompany(
    name="Tracked Co", exchange="TSE", krx_code="1234", source="EDINET",
    themes=("ai-buildout",), corp_code="E00001",
)

# The one real, live-verified EDINET category mapping (edinet_rules.py) —
# used unmodified so this file proves discovery reuses the real rule
# engine, not a fictional injected map.
_REAL_ORDINANCE, _REAL_FORM, _REAL_DOC_TYPE = "010", "030000", "120"


def _row(
    doc_id="S100NEW1", edinet_code="E99999", sec_code="88880",
    ordinance=_REAL_ORDINANCE, form=_REAL_FORM, doc_type=_REAL_DOC_TYPE,
    submit_date_time="2026-08-17 09:00", filer_name="New Co Ltd", doc_description="Annual Securities Report",
    withdrawal_status="", doc_info_edit_status="", disclosure_status="",
):
    return {
        "docID": doc_id, "docTypeCode": doc_type, "ordinanceCode": ordinance, "formCode": form,
        "filerName": filer_name, "docDescription": doc_description, "edinetCode": edinet_code, "secCode": sec_code,
        "submitDateTime": submit_date_time, "issuerEdinetCode": "",
        "withdrawalStatus": withdrawal_status, "docInfoEditStatus": doc_info_edit_status, "disclosureStatus": disclosure_status,
    }


def _envelope(results, count=None, status="200"):
    count = len(results) if count is None else count
    return {"metadata": {"status": status, "message": "OK", "resultset": {"count": count}}, "results": results}


_CODE_LIST_HEADER = (
    "ＥＤＩＮＥＴコード,提出者種別,上場区分,連結の有無,資本金,決算日,"
    "提出者名,提出者名（英字）,提出者名（ヨミ）,所在地,提出者業種,証券コード,提出者法人番号"
)


def _code_list_data_row(edinet_code, sec_code, name_jp, name_en):
    fields = [edinet_code, "内国法人・組合", "上場", "有", "1", "3月31日", name_jp, name_en, "テスト", "東京都", "その他", sec_code, "0000000000000"]
    return ",".join(f'"{f}"' for f in fields)


def _code_list_zip(issuers) -> bytes:
    lines = [f"ダウンロード実行日,2026年08月17日現在,件数,{len(issuers)}件", _CODE_LIST_HEADER]
    lines += [_code_list_data_row(*i) for i in issuers]
    csv_text = "\n".join(lines)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(edinet_code_resolver.REAL_CODE_LIST_CSV_MEMBER_NAME, csv_text.encode("cp932"))
    return buf.getvalue()


def _client(document_list_by_date: dict, code_list_result=None) -> MagicMock:
    client = MagicMock()

    def _get_document_list(date_str, type_=2):
        return document_list_by_date.get(date_str, _envelope([]))

    client.get_document_list.side_effect = _get_document_list

    if isinstance(code_list_result, Exception):
        client.fetch_code_list.side_effect = code_list_result
    elif code_list_result is not None:
        client.fetch_code_list.return_value = code_list_result
    return client


_NEW_CO_CODE_LIST = _code_list_zip([("E99999", "88880", "新会社株式会社", "New Co Ltd.")])


# --- Tracked-company rows are excluded ---

def test_tracked_company_row_is_excluded(tmp_path):
    today = datetime.now(timezone.utc).date().isoformat()
    client = _client({today: _envelope([_row(edinet_code="E00001")])}, _NEW_CO_CODE_LIST)

    result = discovery_service.scan_for_discoveries(client, [_TRACKED], tmp_path, dates=date.fromisoformat(today))

    assert result.new_discoveries == ()


# --- Non-tracked, rule-matched row becomes a discovery ---

def test_non_tracked_matched_row_becomes_a_discovery_with_real_confidence(tmp_path):
    today = date.fromisoformat("2026-08-17")
    client = _client({today.isoformat(): _envelope([_row()])}, _NEW_CO_CODE_LIST)

    result = discovery_service.scan_for_discoveries(client, [_TRACKED], tmp_path, dates=today)

    assert len(result.new_discoveries) == 1
    d = result.new_discoveries[0]
    assert d.edinet_code == "E99999"
    assert d.company_name == "New Co Ltd."
    assert d.doc_id == "S100NEW1"
    assert d.matched_rule == "annual_securities_report:010:030000:120"
    assert d.confidence == "Moderate"  # the real rule engine's actual current output — see the dedicated test below
    assert d.filing_date == "2026-08-17"
    assert result.skipped_unresolved == ()


def test_confidence_persisted_is_never_filtered_to_high_only(tmp_path):
    # Documents the approved Phase 1 behavior: Moderate matches ARE kept,
    # not silently dropped.
    today = date.fromisoformat("2026-08-17")
    client = _client({today.isoformat(): _envelope([_row()])}, _NEW_CO_CODE_LIST)

    result = discovery_service.scan_for_discoveries(client, [_TRACKED], tmp_path, dates=today)

    assert result.new_discoveries[0].confidence == "Moderate"


def test_real_rule_engine_never_produces_high_confidence_for_a_single_match(tmp_path):
    # Honest limitation, flagged before implementation: evaluate_document()
    # hardcodes "Moderate" for any single-code match; "High" only comes
    # from merge_evaluations() combining 2+ distinct categories, which
    # nothing in this scan path does. This test locks that in so it can't
    # silently regress (or silently start passing for the wrong reason).
    today = date.fromisoformat("2026-08-17")
    client = _client({today.isoformat(): _envelope([_row()])}, _NEW_CO_CODE_LIST)

    result = discovery_service.scan_for_discoveries(client, [_TRACKED], tmp_path, dates=today)

    assert all(d.confidence != "High" for d in result.new_discoveries)


# --- Non-tracked, unmatched row is excluded ---

def test_non_tracked_unmatched_row_is_excluded(tmp_path):
    today = date.fromisoformat("2026-08-17")
    client = _client({today.isoformat(): _envelope([_row(ordinance="999", form="999", doc_type="999")])}, _NEW_CO_CODE_LIST)

    result = discovery_service.scan_for_discoveries(client, [_TRACKED], tmp_path, dates=today)

    assert result.new_discoveries == ()


# --- Dedup ---

def test_dedup_by_edinet_code_and_doc_id_across_runs(tmp_path):
    today = date.fromisoformat("2026-08-17")
    client = _client({today.isoformat(): _envelope([_row()])}, _NEW_CO_CODE_LIST)

    first = discovery_service.scan_for_discoveries(client, [_TRACKED], tmp_path, dates=today)
    second = discovery_service.scan_for_discoveries(client, [_TRACKED], tmp_path, dates=today)

    assert len(first.new_discoveries) == 1
    assert second.new_discoveries == ()
    assert second.already_seen_count == 1


# --- Name-resolution failure: skip and log, never guess ---

def test_unresolvable_edinet_code_is_skipped_and_logged_not_guessed(tmp_path):
    today = date.fromisoformat("2026-08-17")
    empty_code_list = _code_list_zip([("E00000", "00000", "別会社", "Other Co.")])  # doesn't contain E99999
    client = _client({today.isoformat(): _envelope([_row()])}, empty_code_list)

    result = discovery_service.scan_for_discoveries(client, [_TRACKED], tmp_path, dates=today)

    assert result.new_discoveries == ()
    assert result.skipped_unresolved == ("E99999",)


def test_resolution_client_error_skips_rather_than_guesses(tmp_path):
    today = date.fromisoformat("2026-08-17")
    client = _client({today.isoformat(): _envelope([_row()])}, EdinetError("boom"))

    result = discovery_service.scan_for_discoveries(client, [_TRACKED], tmp_path, dates=today)

    assert result.new_discoveries == ()
    assert result.skipped_unresolved == ("E99999",)
    assert any("boom" in e for e in result.errors)


# --- Batched resolution: one fetch_code_list call per run, not one per row ---

def test_resolution_is_batched_into_one_call_for_multiple_new_codes(tmp_path):
    today = date.fromisoformat("2026-08-17")
    rows = [_row(doc_id="S100A", edinet_code="E99999"), _row(doc_id="S100B", edinet_code="E88888", sec_code="77770")]
    code_list = _code_list_zip([("E99999", "88880", "新会社株式会社", "New Co Ltd."), ("E88888", "77770", "別会社株式会社", "Another Co Ltd.")])
    client = _client({today.isoformat(): _envelope(rows)}, code_list)

    result = discovery_service.scan_for_discoveries(client, [_TRACKED], tmp_path, dates=today)

    assert len(result.new_discoveries) == 2
    assert client.fetch_code_list.call_count == 1


# --- Isolation: existing tracked caches are never touched ---

def test_discovery_never_writes_tracked_filing_events_cache(tmp_path):
    today = date.fromisoformat("2026-08-17")
    client = _client({today.isoformat(): _envelope([_row()])}, _NEW_CO_CODE_LIST)

    discovery_service.scan_for_discoveries(client, [_TRACKED], tmp_path, dates=today)

    assert not (tmp_path / "edinet_filing_events.json").exists()
    assert not (tmp_path / "edinet_candidates.json").exists()


def test_discovery_writes_only_its_own_cache_file(tmp_path):
    today = date.fromisoformat("2026-08-17")
    client = _client({today.isoformat(): _envelope([_row()])}, _NEW_CO_CODE_LIST)

    discovery_service.scan_for_discoveries(client, [_TRACKED], tmp_path, dates=today)

    assert (tmp_path / "edinet_discovered_candidates.json").exists()
    assert (tmp_path / "edinet_discovery_codes.json").exists()


# --- Required, explicit `dates` — no lookback default ---

def test_dates_is_a_required_positional_style_argument():
    import inspect
    sig = inspect.signature(discovery_service.scan_for_discoveries)
    assert sig.parameters["dates"].default is inspect.Parameter.empty


def test_empty_dates_tuple_raises_typed_error(tmp_path):
    client = _client({})
    try:
        discovery_service.scan_for_discoveries(client, [_TRACKED], tmp_path, dates=())
        assert False, "expected ValueError"
    except ValueError:
        pass
    assert client.get_document_list.call_count == 0


# --- Exactly one get_document_list call per given date ---

def test_makes_exactly_one_get_document_list_call_per_explicit_date(tmp_path):
    d1, d2 = date(2026, 8, 16), date(2026, 8, 17)
    client = _client({d1.isoformat(): _envelope([]), d2.isoformat(): _envelope([])})

    discovery_service.scan_for_discoveries(client, [_TRACKED], tmp_path, dates=(d1, d2))

    assert client.get_document_list.call_count == 2


def test_running_scan_and_discovery_independently_makes_independent_call_sets(tmp_path):
    # Explicit proof of the approved design tradeoff: discovery does NOT
    # share a call with a tracked scan — each independent run makes its
    # own one-call-per-date set.
    from src.data_access.edinet import scan_service
    d1 = date(2026, 8, 17)
    client = _client({d1.isoformat(): _envelope([_row(edinet_code="E00001")])}, _NEW_CO_CODE_LIST)

    scan_service.scan(client, [_TRACKED], tmp_path, dates=d1)
    assert client.get_document_list.call_count == 1

    discovery_service.scan_for_discoveries(client, [_TRACKED], tmp_path, dates=d1)
    assert client.get_document_list.call_count == 2  # a second, independent call — not reused from the tracked scan above


# --- Round trip ---

def test_load_discoveries_empty_when_no_cache(tmp_path):
    assert discovery_service.load_discoveries(tmp_path) == ()


def test_load_discoveries_round_trips_after_scan(tmp_path):
    today = date.fromisoformat("2026-08-17")
    client = _client({today.isoformat(): _envelope([_row()])}, _NEW_CO_CODE_LIST)

    discovery_service.scan_for_discoveries(client, [_TRACKED], tmp_path, dates=today)
    reloaded = discovery_service.load_discoveries(tmp_path)

    assert len(reloaded) == 1
    assert reloaded[0].doc_id == "S100NEW1"


def test_load_discoveries_never_calls_the_client(tmp_path):
    # Proves the review-UI's only data path (load_discoveries) is
    # genuinely read-only — no client is even passed to it.
    import inspect
    assert "client" not in inspect.signature(discovery_service.load_discoveries).parameters
