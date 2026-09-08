"""edinet_code_resolver — fully mocked EdinetClient, zero network calls.

Fixture shape below was validated against the real, official EDINET
code-list response retrieved via one live, credentialed request on
2026-08-17 (Gate 2): a ZIP containing a single member
`EdinetcodeDlInfo.csv`, cp932-encoded, with a summary/metadata row at
physical row 0, the real column-header row at physical row 1, and data
starting at physical row 2. The five company rows below use each
company's REAL, publicly-listed EDINET code and securities code as
confirmed live in Gate 2 (SoftBank Group, Kioxia Holdings, Furukawa
Electric, FANUC, ispace) — this is public company-identifier data, not a
credential, and is included only to validate the resolver's real-shaped
matching logic; every other column (filer type, listed classification,
capital, fiscal year end, address, industry, corporate number) uses
clearly synthetic placeholder values, not real production data. This is
five representative rows, never the full ~11,382-row production list."""
from __future__ import annotations

import io
import zipfile
from unittest.mock import MagicMock

from src.data_access.edinet import edinet_code_resolver
from src.data_access.edinet.errors import EdinetError

_REAL_HEADER = (
    "ＥＤＩＮＥＴコード,提出者種別,上場区分,連結の有無,資本金,決算日,"
    "提出者名,提出者名（英字）,提出者名（ヨミ）,所在地,提出者業種,証券コード,提出者法人番号"
)

# (edinet_code, securities_code, filer_name_jp, filer_name_en) — real,
# publicly-listed values confirmed live in Gate 2. filer_name_en is
# blank for ispace, matching the real row observed live.
_ISSUERS = [
    ("E02778", "99840", "ソフトバンクグループ株式会社", "SoftBank Group Corp."),
    ("E35948", "285A0", "キオクシアホールディングス株式会社", "Kioxia Holdings Corporation"),
    ("E01332", "58010", "古河電気工業株式会社", "Furukawa Electric Co., Ltd."),
    ("E01946", "69540", "ファナック株式会社", "FANUC CORPORATION"),
    ("E37584", "93480", "株式会社ｉｓｐａｃｅ", ""),
]


def _data_row(edinet_code: str, sec_code: str, name_jp: str, name_en: str, corp_number: str = "0000000000000") -> str:
    # Synthetic placeholder values in every non-essential column.
    fields = [edinet_code, "内国法人・組合", "上場", "有", "1", "3月31日", name_jp, name_en, "テスト", "東京都", "その他", sec_code, corp_number]
    return ",".join(f'"{f}"' for f in fields)


def _summary_row(count: int, date_text: str = "2026年08月17日現在") -> str:
    return f"ダウンロード実行日,{date_text},件数,{count}件"


def _sample_csv(issuers=_ISSUERS, declared_count: int | None = None) -> str:
    count = len(issuers) if declared_count is None else declared_count
    lines = [_summary_row(count), _REAL_HEADER] + [_data_row(*issuer) for issuer in issuers]
    return "\n".join(lines)


def _zip_csv(csv_text: str, filename: str = edinet_code_resolver.REAL_CODE_LIST_CSV_MEMBER_NAME, encoding: str = "cp932") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(filename, csv_text.encode(encoding))
    return buf.getvalue()


def _client(fetch_result) -> MagicMock:
    client = MagicMock()
    if isinstance(fetch_result, Exception):
        client.fetch_code_list.side_effect = fetch_result
    else:
        client.fetch_code_list.return_value = fetch_result
    return client


# --- 1. Decoder: cp932 -> shift_jis -> utf-8 ---

def test_extract_csv_text_decodes_real_cp932_encoded_content():
    zip_bytes = _zip_csv(_sample_csv(), encoding="cp932")
    text = edinet_code_resolver._extract_csv_text(zip_bytes)
    assert "ソフトバンクグループ株式会社" in text


def test_extract_csv_text_falls_back_to_shift_jis():
    # Shift-JIS-only text (no cp932-exclusive characters) still decodes
    # successfully under the cp932 codec (cp932 is a superset), so this
    # proves the chain's ordering doesn't break plain Shift-JIS content.
    zip_bytes = _zip_csv(_sample_csv(), encoding="shift_jis")
    text = edinet_code_resolver._extract_csv_text(zip_bytes)
    assert "ファナック株式会社" in text


def test_extract_csv_text_falls_back_to_utf8():
    zip_bytes = _zip_csv(_sample_csv(), encoding="utf-8")
    text = edinet_code_resolver._extract_csv_text(zip_bytes)
    assert "ｉｓｐａｃｅ" in text


def test_extract_csv_text_raises_typed_error_only_after_all_decoders_fail():
    # Bytes invalid in cp932, shift_jis, AND utf-8.
    invalid_bytes = b"\xff\xfe\x00\x01\x02\x81\x00"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("EdinetcodeDlInfo.csv", invalid_bytes)
    try:
        edinet_code_resolver._extract_csv_text(buf.getvalue())
        assert False, "expected EdinetParseError"
    except edinet_code_resolver.EdinetParseError as e:
        assert "cp932" in str(e) and "shift_jis" in str(e) and "utf-8" in str(e)


def test_extract_csv_text_rejects_non_zip_bytes():
    try:
        edinet_code_resolver._extract_csv_text(b"not a zip file at all")
        assert False, "expected EdinetParseError"
    except edinet_code_resolver.EdinetParseError:
        pass


# --- 2. CSV structure: summary row, header row, data rows, count validation ---

def test_parse_summary_row_extracts_declared_count_from_real_shape():
    summary = edinet_code_resolver.parse_summary_row("ダウンロード実行日,2026年08月17日現在,件数,11382件")
    assert summary.declared_count == 11382


def test_parse_summary_row_returns_none_count_for_malformed_line():
    summary = edinet_code_resolver.parse_summary_row("not a real summary line at all")
    assert summary.declared_count is None


def test_parse_summary_row_returns_none_count_for_empty_line():
    summary = edinet_code_resolver.parse_summary_row("")
    assert summary.declared_count is None


def test_parse_code_list_csv_parses_all_rows_with_correct_row_offsets():
    rows, warnings = edinet_code_resolver.parse_code_list_csv(_sample_csv())
    assert warnings == ()
    assert len(rows) == 5
    assert rows[0]["edinet_code"] == "E02778"
    assert rows[0]["securities_code"] == "99840"


def test_parse_code_list_csv_missing_header_row_fails_safely():
    # Fewer than 2 physical lines — only a summary line, no header.
    rows, warnings = edinet_code_resolver.parse_code_list_csv(_summary_row(0))
    assert rows == []
    assert "missing the header row" in warnings[0]


def test_parse_code_list_csv_empty_text_fails_safely():
    rows, warnings = edinet_code_resolver.parse_code_list_csv("")
    assert rows == []
    assert warnings


def test_parse_code_list_csv_malformed_summary_row_still_parses_data_with_a_warning():
    csv_text = "\n".join(["not a real summary line", _REAL_HEADER] + [_data_row(*i) for i in _ISSUERS])
    rows, warnings = edinet_code_resolver.parse_code_list_csv(csv_text)
    assert len(rows) == 5
    assert "not validated" in warnings[0]


def test_parse_code_list_csv_missing_summary_row_shifts_everything_and_fails_safely():
    # No summary line at all — the real header row lands at physical
    # position 0, which this module always treats as the summary
    # (positionally fixed contract), so the real data winds up
    # mis-parsed against a header row that doesn't exist -> no rows.
    csv_text = "\n".join([_REAL_HEADER] + [_data_row(*i) for i in _ISSUERS])
    rows, warnings = edinet_code_resolver.parse_code_list_csv(csv_text)
    assert rows == []
    assert warnings


def test_parse_code_list_csv_header_missing_expected_columns_fails_safely():
    csv_text = "\n".join([_summary_row(1), "colA,colB,colC", _data_row(*_ISSUERS[0])])
    rows, warnings = edinet_code_resolver.parse_code_list_csv(csv_text)
    assert rows == []
    assert "expected columns" in warnings[0]


def test_parse_code_list_csv_count_mismatch_fails_safely():
    # Declared count (99) doesn't match the 5 real data rows present.
    csv_text = _sample_csv(declared_count=99)
    rows, warnings = edinet_code_resolver.parse_code_list_csv(csv_text)
    assert rows == []
    assert "declared 99" in warnings[0] and "5 were parsed" in warnings[0]


def test_parse_code_list_csv_count_match_succeeds_with_no_warnings():
    csv_text = _sample_csv(declared_count=5)
    rows, warnings = edinet_code_resolver.parse_code_list_csv(csv_text)
    assert len(rows) == 5
    assert warnings == ()


# --- 3. Real header mapping: exact Japanese headers, optional English name ---

def test_ispace_shaped_row_with_blank_english_name_still_resolves():
    rows, warnings = edinet_code_resolver.parse_code_list_csv(_sample_csv())
    ispace_row = next(r for r in rows if r["edinet_code"] == "E37584")
    assert ispace_row["filer_name_en"] == ""
    assert ispace_row["filer_name"] == "株式会社ｉｓｐａｃｅ"


def test_japanese_filer_name_values_preserved_exactly():
    rows, _ = edinet_code_resolver.parse_code_list_csv(_sample_csv())
    softbank_row = next(r for r in rows if r["edinet_code"] == "E02778")
    assert softbank_row["filer_name"] == "ソフトバンクグループ株式会社"


def test_column_map_uses_real_japanese_header_text_not_english_keys():
    assert edinet_code_resolver.DEFAULT_CODE_LIST_COLUMN_MAP["edinet_code"] == "ＥＤＩＮＥＴコード"
    assert edinet_code_resolver.DEFAULT_CODE_LIST_COLUMN_MAP["securities_code"] == "証券コード"
    assert edinet_code_resolver.DEFAULT_CODE_LIST_COLUMN_MAP["filer_name"] == "提出者名"
    assert edinet_code_resolver.DEFAULT_CODE_LIST_COLUMN_MAP["filer_name_en"] == "提出者名（英字）"
    assert edinet_code_resolver.DEFAULT_CODE_LIST_COLUMN_MAP["filer_corporate_number"] == "提出者法人番号"


# --- 4. Securities-code normalization for lookup ---

def test_normalize_lookup_code_appends_zero_to_numeric_4_char_input():
    assert edinet_code_resolver._normalize_lookup_code("9984") == "99840"


def test_normalize_lookup_code_appends_zero_to_alphanumeric_4_char_input():
    assert edinet_code_resolver._normalize_lookup_code("285A") == "285A0"


def test_normalize_lookup_code_passes_through_exact_5_char_input():
    assert edinet_code_resolver._normalize_lookup_code("99840") == "99840"


def test_normalize_lookup_code_uppercases_and_strips():
    assert edinet_code_resolver._normalize_lookup_code(" 285a ") == "285A0"


# --- resolve_and_cache: end-to-end with all five real-shaped issuers ---

def test_resolve_and_cache_resolves_all_five_synthetic_verified_mappings(tmp_path):
    client = _client(_zip_csv(_sample_csv()))

    result = edinet_code_resolver.resolve_and_cache(client, ["9984", "285A", "5801", "6954", "9348"], tmp_path)

    assert result.error is None
    assert result.missing_codes == ()
    assert result.ambiguous_codes == ()
    assert result.resolved["9984"].edinet_code == "E02778"
    assert result.resolved["9984"].securities_code == "99840"
    assert result.resolved["285A"].edinet_code == "E35948"
    assert result.resolved["285A"].securities_code == "285A0"
    assert result.resolved["5801"].edinet_code == "E01332"
    assert result.resolved["6954"].edinet_code == "E01946"
    assert result.resolved["9348"].edinet_code == "E37584"
    assert result.resolved["9348"].filer_name_en == ""  # ispace


def test_resolve_and_cache_accepts_already_5_character_input():
    client = _client(_zip_csv(_sample_csv()))
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        result = edinet_code_resolver.resolve_and_cache(client, ["99840"], Path(d))
    assert result.resolved["99840"].edinet_code == "E02778"


def test_resolve_and_cache_reports_missing_codes(tmp_path):
    client = _client(_zip_csv(_sample_csv()))

    result = edinet_code_resolver.resolve_and_cache(client, ["0000"], tmp_path)

    assert result.missing_codes == ("0000",)
    assert "0000" not in result.resolved


def test_resolve_and_cache_reports_ambiguous_codes_without_guessing_which_match(tmp_path):
    duplicated = _ISSUERS + [("E99999", "99840", "別会社", "Different Co.")]  # same securities_code as SoftBank
    client = _client(_zip_csv(_sample_csv(issuers=duplicated)))

    result = edinet_code_resolver.resolve_and_cache(client, ["9984"], tmp_path)

    assert result.ambiguous_codes == ("9984",)
    assert "9984" not in result.resolved


def test_resolve_and_cache_never_raises_on_client_error(tmp_path):
    client = _client(EdinetError("boom"))

    result = edinet_code_resolver.resolve_and_cache(client, ["9984"], tmp_path)

    assert result.error == "boom"
    assert result.missing_codes == ("9984",)


def test_resolve_and_cache_never_raises_on_malformed_zip_payload(tmp_path):
    client = _client(b"not a zip at all")

    result = edinet_code_resolver.resolve_and_cache(client, ["9984"], tmp_path)

    assert result.error is not None
    assert result.missing_codes == ("9984",)


def test_resolve_and_cache_never_raises_on_count_mismatch(tmp_path):
    client = _client(_zip_csv(_sample_csv(declared_count=99)))

    result = edinet_code_resolver.resolve_and_cache(client, ["9984"], tmp_path)

    assert result.error is not None
    assert result.missing_codes == ("9984",)


def test_resolve_and_cache_persists_and_round_trips(tmp_path):
    client = _client(_zip_csv(_sample_csv()))
    edinet_code_resolver.resolve_and_cache(client, ["9984"], tmp_path)

    cached = edinet_code_resolver.load_cached_codes(tmp_path)

    assert cached["9984"].edinet_code == "E02778"
    assert cached["9984"].filer_corporate_number == "0000000000000"


def test_load_cached_codes_empty_when_no_cache(tmp_path):
    assert edinet_code_resolver.load_cached_codes(tmp_path) == {}


def test_load_cached_codes_tolerates_corrupt_cache_file(tmp_path):
    (tmp_path / "edinet_codes.json").write_text("{not valid json", encoding="utf-8")
    assert edinet_code_resolver.load_cached_codes(tmp_path) == {}


def test_resolve_and_cache_preserves_previously_resolved_codes_across_runs(tmp_path):
    client = _client(_zip_csv(_sample_csv()))
    edinet_code_resolver.resolve_and_cache(client, ["9984"], tmp_path)

    second = edinet_code_resolver.resolve_and_cache(client, ["6954"], tmp_path)

    assert "9984" in second.resolved
    assert "6954" in second.resolved


# --- resolve_edinet_codes_by_code: additive, discovery-only sibling of
# resolve_and_cache — reverse direction (edinet_code -> name), separate
# cache file. Every test below uses this file's existing fixtures
# unchanged (_zip_csv, _sample_csv, _client, _ISSUERS). ---

def test_resolve_edinet_codes_by_code_resolves_by_edinet_code_not_securities_code(tmp_path):
    client = _client(_zip_csv(_sample_csv()))

    result = edinet_code_resolver.resolve_edinet_codes_by_code(client, ["E02778", "E37584"], tmp_path)

    assert result.error is None
    assert result.resolved["E02778"].filer_name_en == "SoftBank Group Corp."
    assert result.resolved["E02778"].securities_code == "99840"
    assert result.resolved["E37584"].filer_name == "株式会社ｉｓｐａｃｅ"
    assert result.resolved["E37584"].filer_name_en == ""  # ispace, real observed blank field


def test_resolve_edinet_codes_by_code_reports_missing_codes(tmp_path):
    client = _client(_zip_csv(_sample_csv()))

    result = edinet_code_resolver.resolve_edinet_codes_by_code(client, ["E00000"], tmp_path)

    assert result.missing_codes == ("E00000",)
    assert "E00000" not in result.resolved


def test_resolve_edinet_codes_by_code_reports_ambiguous_codes_without_guessing(tmp_path):
    duplicated = _ISSUERS + [("E02778", "12340", "別会社", "Different Co.")]  # same edinet_code as SoftBank
    client = _client(_zip_csv(_sample_csv(issuers=duplicated)))

    result = edinet_code_resolver.resolve_edinet_codes_by_code(client, ["E02778"], tmp_path)

    assert result.ambiguous_codes == ("E02778",)
    assert "E02778" not in result.resolved


def test_resolve_edinet_codes_by_code_never_raises_on_client_error(tmp_path):
    client = _client(EdinetError("boom"))

    result = edinet_code_resolver.resolve_edinet_codes_by_code(client, ["E02778"], tmp_path)

    assert result.error == "boom"
    assert result.missing_codes == ("E02778",)


def test_resolve_edinet_codes_by_code_persists_to_its_own_cache_file(tmp_path):
    client = _client(_zip_csv(_sample_csv()))
    edinet_code_resolver.resolve_edinet_codes_by_code(client, ["E02778"], tmp_path)

    assert (tmp_path / "edinet_discovery_codes.json").exists()
    assert not (tmp_path / "edinet_codes.json").exists()

    cached = edinet_code_resolver.load_cached_discovery_codes(tmp_path)
    assert cached["E02778"].edinet_code == "E02778"


def test_resolve_edinet_codes_by_code_does_not_affect_resolve_and_caches_own_cache(tmp_path):
    client = _client(_zip_csv(_sample_csv()))
    edinet_code_resolver.resolve_and_cache(client, ["9984"], tmp_path)
    before = (tmp_path / "edinet_codes.json").read_text(encoding="utf-8")

    edinet_code_resolver.resolve_edinet_codes_by_code(client, ["E01946"], tmp_path)

    after = (tmp_path / "edinet_codes.json").read_text(encoding="utf-8")
    assert after == before  # byte-identical — the new function never touches this file
    assert "E01946" not in edinet_code_resolver.load_cached_codes(tmp_path)  # only in the discovery cache


def test_load_cached_discovery_codes_empty_when_no_cache(tmp_path):
    assert edinet_code_resolver.load_cached_discovery_codes(tmp_path) == {}


def test_load_cached_discovery_codes_tolerates_corrupt_cache_file(tmp_path):
    (tmp_path / "edinet_discovery_codes.json").write_text("{not valid json", encoding="utf-8")
    assert edinet_code_resolver.load_cached_discovery_codes(tmp_path) == {}
