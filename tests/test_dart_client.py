"""DartClient — fully mocked, zero network calls, no API key required.
Response shapes mirror OpenDART's real documented format (see
src/data_access/dart/client.py's module docstring for the verified
sources), reproduced here as fixtures rather than hit live."""
from __future__ import annotations

import io
import zipfile
from unittest.mock import MagicMock

import pytest

from src.data_access.dart.client import DartClient
from src.data_access.dart.errors import (
    DartApiError,
    DartConfigError,
    DartParseError,
    DartRateLimitError,
    DartTimeoutError,
)


def _corp_code_zip_bytes(entries: list[tuple[str, str, str, str]]) -> bytes:
    """entries: list of (corp_code, corp_name, stock_code, corp_eng_name)."""
    rows = "".join(
        f"<list><corp_code>{cc}</corp_code><corp_name>{name}</corp_name>"
        f"<corp_eng_name>{eng}</corp_eng_name><stock_code>{stock}</stock_code>"
        f"<modify_date>20260101</modify_date></list>"
        for cc, name, stock, eng in entries
    )
    xml = f'<?xml version="1.0" encoding="UTF-8"?><result>{rows}</result>'.encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("CORPCODE.xml", xml)
    return buf.getvalue()


def _error_xml_bytes(status: str, message: str) -> bytes:
    return f'<?xml version="1.0" encoding="UTF-8"?><result><status>{status}</status><message>{message}</message></result>'.encode("utf-8")


def _mock_response(status_code: int = 200, content: bytes = b"", json_payload=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.headers = {}
    if json_payload is not None:
        resp.json.return_value = json_payload
    else:
        resp.json.side_effect = ValueError("no json body")
    return resp


def test_client_raises_config_error_without_api_key():
    client = DartClient(api_key=None, session=MagicMock())
    with pytest.raises(DartConfigError):
        client.fetch_all_corp_codes()


def test_fetch_all_corp_codes_parses_valid_zip():
    session = MagicMock()
    zip_bytes = _corp_code_zip_bytes([
        ("00126380", "삼성전자", "005930", "Samsung Electronics"),
        ("00164779", "SK하이닉스", "000660", "SK Hynix"),
    ])
    session.get.return_value = _mock_response(200, zip_bytes)
    client = DartClient(api_key="fake-key", session=session)

    records = client.fetch_all_corp_codes()

    assert len(records) == 2
    assert records[0].corp_code == "00126380"
    assert records[0].stock_code == "005930"
    assert records[1].corp_eng_name == "SK Hynix"


def test_fetch_all_corp_codes_raises_typed_error_on_invalid_key():
    session = MagicMock()
    session.get.return_value = _mock_response(200, _error_xml_bytes("010", "등록되지 않은 키입니다."))
    client = DartClient(api_key="bad-key", session=session)

    with pytest.raises(DartApiError) as exc_info:
        client.fetch_all_corp_codes()
    assert exc_info.value.status == "010"


def test_fetch_all_corp_codes_raises_rate_limit_error_on_status_020():
    session = MagicMock()
    session.get.return_value = _mock_response(200, _error_xml_bytes("020", "요청 제한을 초과하였습니다."))
    client = DartClient(api_key="fake-key", session=session)

    with pytest.raises(DartRateLimitError):
        client.fetch_all_corp_codes()


def test_fetch_all_corp_codes_raises_parse_error_on_garbage_body():
    session = MagicMock()
    session.get.return_value = _mock_response(200, b"not a zip and not xml either")
    client = DartClient(api_key="fake-key", session=session)

    with pytest.raises(DartParseError):
        client.fetch_all_corp_codes()


def test_search_disclosures_parses_valid_list_response():
    session = MagicMock()
    payload = {
        "status": "000", "message": "정상", "page_no": 1, "page_count": 10,
        "total_count": 1, "total_page": 1,
        "list": [{
            "corp_cls": "Y", "corp_name": "삼성전자", "corp_code": "00126380",
            "stock_code": "005930", "report_nm": "분기보고서", "rcept_no": "20260115000123",
            "flr_nm": "삼성전자", "rcept_dt": "20260115", "rm": "",
        }],
    }
    session.get.return_value = _mock_response(200, json_payload=payload)
    client = DartClient(api_key="fake-key", session=session)

    records, total = client.search_disclosures("00126380", "20260101", "20260131")

    assert total == 1
    assert len(records) == 1
    assert records[0].rcept_no == "20260115000123"
    assert records[0].report_nm == "분기보고서"


def test_search_disclosures_returns_empty_result_on_no_data_status():
    # DART's status "013" ("no data found") is a normal empty search
    # result, not an error — a narrow date window legitimately matching
    # nothing must not be reported as an API failure.
    session = MagicMock()
    session.get.return_value = _mock_response(200, json_payload={"status": "013", "message": "조회된 데이터가 없습니다."})
    client = DartClient(api_key="fake-key", session=session)

    records, total = client.search_disclosures("00126380", "20260101", "20260131")

    assert records == []
    assert total == 0


def test_search_disclosures_page_count_is_capped_at_100():
    session = MagicMock()
    session.get.return_value = _mock_response(200, json_payload={"status": "000", "message": "정상", "list": [], "total_count": 0})
    client = DartClient(api_key="fake-key", session=session)

    client.search_disclosures("00126380", "20260101", "20260131", page_count=500)

    _, kwargs = session.get.call_args
    assert kwargs["params"]["page_count"] == "100"


def test_get_raises_typed_timeout_error():
    import requests

    session = MagicMock()
    session.get.side_effect = requests.Timeout()
    client = DartClient(api_key="fake-key", session=session)

    with pytest.raises(DartTimeoutError):
        client.search_disclosures("00126380", "20260101", "20260131")


def test_fetch_document_zip_returns_raw_bytes_on_success():
    session = MagicMock()
    session.get.return_value = _mock_response(200, b"PK\x03\x04fake-zip-bytes")
    client = DartClient(api_key="fake-key", session=session)

    result = client.fetch_document_zip("20260115000123")

    assert result.startswith(b"PK")


def test_fetch_document_zip_raises_parse_error_on_non_zip_body():
    session = MagicMock()
    session.get.return_value = _mock_response(200, b"<html>error page</html>")
    client = DartClient(api_key="fake-key", session=session)

    with pytest.raises(DartParseError):
        client.fetch_document_zip("20260115000123")


def test_viewer_url_uses_real_dart_viewer_pattern():
    assert DartClient.viewer_url("20260115000123") == "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260115000123"
