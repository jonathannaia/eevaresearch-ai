"""cik_resolver — fully mocked EdgarClient, zero network calls. Covers
the two-step resolution (bulk lookup + submissions cross-check) and the
CIK-mismatch case where a candidate CIK fails cross-check and must not
be accepted."""
from __future__ import annotations

from unittest.mock import MagicMock

from src.data_access.edgar.cik_resolver import load_cached_ciks, resolve_and_cache
from src.data_access.edgar.errors import EdgarApiError


def _client(bulk: dict, submissions_by_cik: dict[str, dict]) -> MagicMock:
    client = MagicMock()
    client.fetch_company_tickers.return_value = bulk
    client.get_submissions.side_effect = lambda cik: submissions_by_cik.get(cik, {"tickers": []})
    return client


def test_resolves_ticker_when_bulk_and_submissions_agree(tmp_path):
    bulk = {"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"}}
    client = _client(bulk, {"0001045810": {"tickers": ["NVDA"]}})

    result = resolve_and_cache(client, ["NVDA"], tmp_path)

    assert result.resolved["NVDA"].cik == "0001045810"
    assert result.resolved["NVDA"].company_name == "NVIDIA CORP"
    assert result.missing_tickers == ()


def test_cik_cross_check_mismatch_leaves_ticker_unresolved(tmp_path):
    # Bulk file points NVDA at a CIK, but that CIK's own submissions
    # metadata doesn't list NVDA as one of its tickers — must not accept.
    bulk = {"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"}}
    client = _client(bulk, {"0001045810": {"tickers": ["SOMETHING-ELSE"]}})

    result = resolve_and_cache(client, ["NVDA"], tmp_path)

    assert "NVDA" not in result.resolved
    assert result.missing_tickers == ("NVDA",)


def test_ticker_not_in_bulk_file_is_missing(tmp_path):
    client = _client({}, {})
    result = resolve_and_cache(client, ["NVDA"], tmp_path)
    assert result.missing_tickers == ("NVDA",)


def test_submissions_cross_check_error_leaves_ticker_unresolved(tmp_path):
    bulk = {"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"}}
    client = MagicMock()
    client.fetch_company_tickers.return_value = bulk
    client.get_submissions.side_effect = EdgarApiError(500, "server error")

    result = resolve_and_cache(client, ["NVDA"], tmp_path)

    assert result.missing_tickers == ("NVDA",)


def test_bulk_fetch_failure_returns_error_and_preserves_existing_cache(tmp_path):
    bulk_client = _client({"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"}}, {"0001045810": {"tickers": ["NVDA"]}})
    resolve_and_cache(bulk_client, ["NVDA"], tmp_path)

    failing_client = MagicMock()
    failing_client.fetch_company_tickers.side_effect = EdgarApiError(500, "down")

    result = resolve_and_cache(failing_client, ["MU"], tmp_path)

    assert result.error is not None
    assert result.resolved["NVDA"].cik == "0001045810"  # preserved from before
    assert result.missing_tickers == ("MU",)


def test_result_persists_to_cache_and_is_reloadable(tmp_path):
    bulk = {"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"}}
    client = _client(bulk, {"0001045810": {"tickers": ["NVDA"]}})
    resolve_and_cache(client, ["NVDA"], tmp_path)

    reloaded = load_cached_ciks(tmp_path)
    assert reloaded["NVDA"].cik == "0001045810"


def test_load_cached_ciks_empty_when_no_cache(tmp_path):
    assert load_cached_ciks(tmp_path) == {}


def test_load_cached_ciks_tolerates_corrupt_file(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "edgar_ciks.json").write_text("{not valid json", encoding="utf-8")
    assert load_cached_ciks(tmp_path) == {}


def test_resolving_multiple_tickers_only_missing_ones_stay_unresolved(tmp_path):
    bulk = {
        "0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
        "1": {"cik_str": 723125, "ticker": "MU", "title": "MICRON TECHNOLOGY INC"},
    }
    client = _client(bulk, {
        "0001045810": {"tickers": ["NVDA"]},
        "0000723125": {"tickers": ["MU"]},
    })

    result = resolve_and_cache(client, ["NVDA", "MU", "COHR"], tmp_path)

    assert set(result.resolved.keys()) == {"NVDA", "MU"}
    assert result.missing_tickers == ("COHR",)
