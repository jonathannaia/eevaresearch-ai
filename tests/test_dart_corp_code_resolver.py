"""corp_code_resolver — resolution against a fake DartClient, on-disk
cache read/write, merge-with-existing-cache, and the "never raise, never
guess" failure paths. No network, no real client."""
from __future__ import annotations

from src.data_access.dart.client import CorpCodeRecord
from src.data_access.dart.corp_code_resolver import (
    load_cached_corp_codes,
    resolve_and_cache,
)
from src.data_access.dart.errors import DartConfigError


class _FakeDartClient:
    def __init__(self, records=None, error: Exception | None = None):
        self._records = records or []
        self._error = error

    def fetch_all_corp_codes(self):
        if self._error:
            raise self._error
        return self._records


_SAMSUNG = CorpCodeRecord("00126380", "삼성전자", "Samsung Electronics", "005930", "20260101")
_SK_HYNIX = CorpCodeRecord("00164779", "SK하이닉스", "SK Hynix", "000660", "20260101")


def test_resolve_and_cache_resolves_known_codes(tmp_path):
    client = _FakeDartClient(records=[_SAMSUNG, _SK_HYNIX])

    result = resolve_and_cache(client, ["005930", "000660"], tmp_path)

    assert result.error is None
    assert result.missing_krx_codes == ()
    assert result.resolved["005930"].corp_code == "00126380"
    assert result.resolved["000660"].corp_code == "00164779"
    assert result.resolved["005930"].source == "OpenDART corpCode.xml"


def test_resolve_and_cache_writes_to_disk_and_can_be_reloaded(tmp_path):
    client = _FakeDartClient(records=[_SAMSUNG])
    resolve_and_cache(client, ["005930"], tmp_path)

    reloaded = load_cached_corp_codes(tmp_path)

    assert reloaded["005930"].corp_code == "00126380"


def test_resolve_and_cache_reports_missing_krx_code_without_guessing(tmp_path):
    client = _FakeDartClient(records=[_SAMSUNG])  # SK Hynix not in the bulk file this run

    result = resolve_and_cache(client, ["005930", "000660"], tmp_path)

    assert "000660" in result.missing_krx_codes
    assert "000660" not in result.resolved


def test_resolve_and_cache_treats_ambiguous_match_as_missing_not_a_guess(tmp_path):
    duplicate = CorpCodeRecord("00999999", "삼성전자유사", "Samsung Lookalike", "005930", "20260101")
    client = _FakeDartClient(records=[_SAMSUNG, duplicate])

    result = resolve_and_cache(client, ["005930"], tmp_path)

    assert "005930" in result.missing_krx_codes
    assert "005930" not in result.resolved


def test_resolve_and_cache_never_raises_on_client_error(tmp_path):
    client = _FakeDartClient(error=DartConfigError("no key configured"))

    result = resolve_and_cache(client, ["005930"], tmp_path)

    assert result.error is not None
    assert "005930" in result.missing_krx_codes


def test_resolve_and_cache_falls_back_to_existing_cache_on_error(tmp_path):
    resolve_and_cache(_FakeDartClient(records=[_SAMSUNG]), ["005930"], tmp_path)

    failing_client = _FakeDartClient(error=DartConfigError("key revoked"))
    result = resolve_and_cache(failing_client, ["000660"], tmp_path)

    assert result.error is not None
    assert result.resolved["005930"].corp_code == "00126380"  # prior result preserved, not lost


def test_resolve_and_cache_merges_with_previously_resolved_companies(tmp_path):
    resolve_and_cache(_FakeDartClient(records=[_SAMSUNG]), ["005930"], tmp_path)

    result = resolve_and_cache(_FakeDartClient(records=[_SAMSUNG, _SK_HYNIX]), ["000660"], tmp_path)

    assert result.resolved["005930"].corp_code == "00126380"
    assert result.resolved["000660"].corp_code == "00164779"


def test_load_cached_corp_codes_returns_empty_when_no_cache_file(tmp_path):
    assert load_cached_corp_codes(tmp_path) == {}


def test_load_cached_corp_codes_returns_empty_on_corrupt_cache_file(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "dart_corp_codes.json").write_text("{not valid json", encoding="utf-8")

    assert load_cached_corp_codes(tmp_path) == {}
