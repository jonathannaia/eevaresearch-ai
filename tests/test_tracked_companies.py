"""Tracked-company registry (Korea DART + SEC EDGAR + EDINET pilots) —
presence, correct identifiers/theme mapping, source filtering, and the
corp_code/CIK-merge helpers."""
from src.config.tracked_companies import (
    TrackedCompany,
    get_tracked_companies,
    get_tracked_companies_for_source,
    with_resolved_ciks,
    with_resolved_corp_codes,
)


def test_registry_contains_all_three_pilot_cohorts():
    companies = get_tracked_companies()
    sources = {c.source for c in companies}
    names = {c.name for c in companies}
    assert sources == {"OpenDART / DART", "SEC EDGAR", "EDINET"}
    assert {"Samsung Electronics", "SK Hynix", "NVIDIA", "SoftBank Group Corp."}.issubset(names)
    assert len(names) == len(companies)  # no duplicate names


def test_get_tracked_companies_for_source_filters_dart_only():
    dart_companies = get_tracked_companies_for_source("OpenDART / DART")
    names = {c.name for c in dart_companies}
    assert names == {
        "Samsung Electronics", "SK Hynix",
        # Core Issuer Expansion batch (2026-09-04)
        "LG Innotek Co., Ltd.", "Hanwha Aerospace Co., Ltd.", "Korea Aerospace Industries, Ltd.",
        "Doosan Robotics Inc.", "Wonik IPS Co., Ltd.", "SFA Engineering Corporation",
        "SFA Semicon Co., Ltd", "Hana Micron Inc.",
    }


def test_get_tracked_companies_for_source_filters_edgar_only():
    edgar_companies = get_tracked_companies_for_source("SEC EDGAR")
    names = {c.name for c in edgar_companies}
    assert all(c.source == "SEC EDGAR" for c in edgar_companies)
    assert {"NVIDIA", "Micron Technology"}.issubset(names)
    assert "Samsung Electronics" not in names
    assert "SoftBank Group Corp." not in names


def test_edgar_cohort_identifiers_and_theme_mapping():
    companies = {c.name: c for c in get_tracked_companies_for_source("SEC EDGAR")}
    assert companies["NVIDIA"].krx_code == "NVDA"
    assert companies["NVIDIA"].themes[0] == "ai-buildout"
    assert companies["Micron Technology"].krx_code == "MU"
    assert companies["Micron Technology"].themes[0] == "memory"
    assert companies["Coherent Corp"].krx_code == "COHR"
    assert companies["Coherent Corp"].themes[0] == "photonics"
    assert companies["Rockwell Automation"].krx_code == "ROK"
    assert companies["Rockwell Automation"].themes[0] == "humanoids"
    assert companies["Rocket Lab"].krx_code == "RKLB"
    assert companies["Rocket Lab"].themes[0] == "space"
    for c in companies.values():
        assert c.corp_code is None  # never hardcoded — resolved separately (CIK)


def test_with_resolved_ciks_fills_in_matching_tickers_only():
    companies = get_tracked_companies_for_source("SEC EDGAR")
    resolved = with_resolved_ciks(companies, {"NVDA": "0001045810"})

    by_name = {c.name: c for c in resolved}
    assert by_name["NVIDIA"].corp_code == "0001045810"
    assert by_name["Micron Technology"].corp_code is None


def test_samsung_identifiers_and_theme_mapping():
    companies = {c.name: c for c in get_tracked_companies()}
    samsung = companies["Samsung Electronics"]
    assert samsung.exchange == "KRX"
    assert samsung.krx_code == "005930"
    assert samsung.source == "OpenDART / DART"
    assert samsung.themes[0] == "memory"
    assert "ai-buildout" in samsung.themes
    assert samsung.corp_code is None  # never hardcoded — resolved separately


def test_sk_hynix_identifiers_and_theme_mapping():
    companies = {c.name: c for c in get_tracked_companies()}
    hynix = companies["SK Hynix"]
    assert hynix.krx_code == "000660"
    assert hynix.themes[0] == "memory"
    assert hynix.corp_code is None


def test_get_tracked_companies_active_only_excludes_inactive():
    inactive = TrackedCompany(
        name="Inactive Co", exchange="KRX", krx_code="999999",
        source="OpenDART / DART", themes=("memory",), active=False,
    )
    all_companies = get_tracked_companies(active_only=False) + (inactive,)
    active_names = {c.name for c in all_companies if c.active}
    assert "Inactive Co" not in active_names


def test_with_resolved_corp_codes_fills_in_matching_krx_codes_only():
    companies = get_tracked_companies()
    resolved = with_resolved_corp_codes(companies, {"005930": "00126380"})

    by_name = {c.name: c for c in resolved}
    assert by_name["Samsung Electronics"].corp_code == "00126380"
    assert by_name["SK Hynix"].corp_code is None  # not in the mapping — passes through unresolved


def test_with_resolved_corp_codes_does_not_mutate_originals():
    # DART/EDGAR entries start at None; EDINET entries start already
    # hardcoded (Gate 7) — the invariant this test checks is "no
    # original value changed," not "every value is None."
    companies = get_tracked_companies()
    original_corp_codes = {c.name: c.corp_code for c in companies}
    with_resolved_corp_codes(companies, {"005930": "00126380"})
    assert {c.name: c.corp_code for c in companies} == original_corp_codes


# --- EDINET pilot cohort (Gate 7) ---

def test_get_tracked_companies_for_source_filters_edinet_only():
    edinet_companies = get_tracked_companies_for_source("EDINET")
    names = {c.name for c in edinet_companies}
    assert names == {
        "SoftBank Group Corp.", "Kioxia Holdings Corporation", "Furukawa Electric Co., Ltd.",
        "FANUC CORPORATION", "ispace, inc.",
        # Core Issuer Expansion batch (2026-09-04)
        "Tokyo Electron Limited", "Advantest Corporation", "Disco Corporation",
        "Shin-Etsu Chemical Co., Ltd.", "SUMCO Corporation", "Ibiden Co., Ltd.",
        "Mitsubishi Electric Corporation", "Renesas Electronics Corporation",
    }


def test_edinet_cohort_has_exactly_thirteen_entries():
    # Was "exactly five" through Gate 7; the Core Issuer Expansion batch
    # (2026-09-04) added 8 more (5 + 8 = 13) — renamed rather than left
    # stale, same discipline Gate 7.1 already established for this file.
    assert len(get_tracked_companies_for_source("EDINET")) == 13


def test_edinet_cohort_direct_edinet_code_mapping():
    companies = {c.name: c for c in get_tracked_companies_for_source("EDINET")}
    assert companies["SoftBank Group Corp."].corp_code == "E02778"
    assert companies["Kioxia Holdings Corporation"].corp_code == "E35948"
    assert companies["Furukawa Electric Co., Ltd."].corp_code == "E01332"
    assert companies["FANUC CORPORATION"].corp_code == "E01946"
    assert companies["ispace, inc."].corp_code == "E37584"


def test_edinet_cohort_preserves_source_native_five_character_securities_codes():
    companies = {c.name: c for c in get_tracked_companies_for_source("EDINET")}
    # Real EDINET securities codes are 5 characters (4-char TSE code +
    # trailing "0", confirmed live Gate 2) — never the bare 4-char code.
    assert companies["SoftBank Group Corp."].krx_code == "99840"
    assert companies["Kioxia Holdings Corporation"].krx_code == "285A0"  # alphanumeric preserved exactly
    assert companies["Furukawa Electric Co., Ltd."].krx_code == "58010"
    assert companies["FANUC CORPORATION"].krx_code == "69540"
    assert companies["ispace, inc."].krx_code == "93480"
    for c in companies.values():
        assert len(c.krx_code) == 5


def test_edinet_cohort_theme_mapping():
    companies = {c.name: c for c in get_tracked_companies_for_source("EDINET")}
    assert companies["SoftBank Group Corp."].themes[0] == "ai-buildout"
    assert companies["Kioxia Holdings Corporation"].themes[0] == "memory"
    assert companies["Furukawa Electric Co., Ltd."].themes[0] == "photonics"
    assert companies["FANUC CORPORATION"].themes[0] == "humanoids"
    assert companies["ispace, inc."].themes[0] == "space"
    # "no secondary themes added" held for exactly the five Gate 7
    # entries; the Core Issuer Expansion batch (2026-09-04) deliberately
    # does add subthemes for a few of its own entries (e.g. Advantest,
    # Mitsubishi Electric) — scoped to only the original five here rather
    # than asserted across the whole, now-larger EDINET cohort.
    for name in ("SoftBank Group Corp.", "Kioxia Holdings Corporation", "Furukawa Electric Co., Ltd.", "FANUC CORPORATION", "ispace, inc."):
        assert companies[name].subthemes == ()


def test_edinet_cohort_preserves_japanese_legal_names_exactly():
    companies = {c.name: c for c in get_tracked_companies_for_source("EDINET")}
    assert companies["SoftBank Group Corp."].native_name == "ソフトバンクグループ株式会社"
    assert companies["Kioxia Holdings Corporation"].native_name == "キオクシアホールディングス株式会社"
    assert companies["Furukawa Electric Co., Ltd."].native_name == "古河電気工業株式会社"
    assert companies["FANUC CORPORATION"].native_name == "ファナック株式会社"
    assert companies["ispace, inc."].native_name == "株式会社ｉｓｐａｃｅ"


def test_edinet_cohort_corp_code_is_hardcoded_not_none():
    # Unlike DART/EDGAR (never hardcoded, always runtime-resolved), the
    # EDINET cohort's identifiers were already independently live-verified
    # (Gate 2/Gate 6) — see tracked_companies.py's own module docstring.
    for c in get_tracked_companies_for_source("EDINET"):
        assert c.corp_code is not None


def test_ispace_english_name_is_curated_not_claimed_as_source_evidence():
    # The real EDINET code list's English-name field for ispace was
    # observed blank (Gate 2/Gate 6) — `name` here must not be presented
    # as if it came from that source; only `native_name` is source
    # evidence for this entry.
    ispace = next(c for c in get_tracked_companies_for_source("EDINET") if c.corp_code == "E37584")
    assert ispace.name == "ispace, inc."
    assert "curated" in ispace.notes.lower()
    assert ispace.native_name == "株式会社ｉｓｐａｃｅ"


def test_edinet_cohort_exchange_is_tse():
    for c in get_tracked_companies_for_source("EDINET"):
        assert c.exchange == "TSE"


def test_native_name_defaults_to_empty_for_non_edinet_entries():
    for c in get_tracked_companies_for_source("SEC EDGAR") + get_tracked_companies_for_source("OpenDART / DART"):
        assert c.native_name == ""


# --- Radar expansion — INDI/AIP/CEVA batch (2026-08-20, bounded live gate) ---

def test_indi_aip_ceva_present_exactly_once_each_and_active():
    companies = get_tracked_companies(active_only=True)
    by_ticker = {c.krx_code: c for c in companies}
    for ticker in ("INDI", "AIP", "CEVA"):
        matches = [c for c in companies if c.krx_code == ticker]
        assert len(matches) == 1, f"{ticker} must appear exactly once"
        assert by_ticker[ticker].active is True
        assert by_ticker[ticker].source == "SEC EDGAR"


def test_indi_aip_ceva_legal_names_and_themes():
    by_ticker = {c.krx_code: c for c in get_tracked_companies(active_only=True)}
    assert by_ticker["INDI"].name == "indie Semiconductor, Inc."
    assert by_ticker["INDI"].themes == ("humanoids",)
    assert by_ticker["AIP"].name == "Arteris, Inc."
    assert by_ticker["AIP"].themes == ("ai-buildout",)
    assert by_ticker["CEVA"].name == "CEVA INC"
    assert by_ticker["CEVA"].themes == ("ai-buildout",)


def test_indi_aip_ceva_subthemes_left_unset_with_intent_recorded_in_notes():
    # No existing tracked-company subtheme accurately represented any of
    # the three proposed classifications — reported as a conflict rather
    # than silently reused or invented (see tracked_companies.py's own
    # comment above this batch).
    by_ticker = {c.krx_code: c for c in get_tracked_companies(active_only=True)}
    assert by_ticker["INDI"].subthemes == ()
    assert by_ticker["AIP"].subthemes == ()
    assert by_ticker["CEVA"].subthemes == ()
    assert "automotive-sensing" in by_ticker["INDI"].notes
    assert "soc-interconnect" in by_ticker["AIP"].notes
    assert "edge-ai-connectivity" in by_ticker["CEVA"].notes


def test_indi_aip_ceva_corp_code_not_hardcoded():
    # Same convention as every other EDGAR entry — resolved lazily from
    # data/cache/edgar_ciks.json via with_resolved_ciks(), never stored
    # statically.
    by_ticker = {c.krx_code: c for c in get_tracked_companies(active_only=True)}
    for ticker in ("INDI", "AIP", "CEVA"):
        assert by_ticker[ticker].corp_code is None


def test_active_tracked_company_count_is_exactly_81():
    # Was "exactly 32" before the Core Issuer Expansion batch
    # (2026-09-04), which added 30 net-new active issuers
    # (14 EDGAR + 8 DART + 8 EDINET; 32 + 30 = 62). The Filings Radar
    # issuer-expansion batch (2026-09-04) then added 19 more SEC EDGAR
    # issuers (62 + 19 = 81).
    assert len(get_tracked_companies(active_only=True)) == 81


def test_edgar_ciks_cache_already_resolves_indi_aip_ceva_with_no_network_call():
    # Reads the real, already-populated data/cache/edgar_ciks.json left
    # by the prior, separately-approved bounded live resolution gate —
    # a plain local file read, zero network calls.
    from src.config.settings import get_settings
    from src.data_access.edgar import cik_resolver

    cached = cik_resolver.load_cached_ciks(get_settings().cache_dir)
    assert cached["INDI"].cik == "0001841925"
    assert cached["AIP"].cik == "0001667011"
    assert cached["CEVA"].cik == "0001173489"


def test_indi_aip_ceva_resolve_via_with_resolved_ciks_using_cached_mapping():
    from src.config.settings import get_settings
    from src.data_access.edgar import cik_resolver

    cached = cik_resolver.load_cached_ciks(get_settings().cache_dir)
    resolved_map = {ticker: record.cik for ticker, record in cached.items()}
    edgar_companies = get_tracked_companies_for_source("SEC EDGAR")
    resolved = with_resolved_ciks(edgar_companies, resolved_map)
    by_ticker = {c.krx_code: c for c in resolved}
    assert by_ticker["INDI"].corp_code == "0001841925"
    assert by_ticker["AIP"].corp_code == "0001667011"
    assert by_ticker["CEVA"].corp_code == "0001173489"


# --- Core Issuer Expansion batch (weekend beta, 2026-09-04) ---
# 30 net-new active issuers (14 SEC EDGAR, 8 OpenDART / DART, 8 EDINET),
# added after a bounded, read-only, live official-source identifier
# verification pass. See tracked_companies.py's own batch comment and
# design/DECISIONS.md's matching Gate entry for the full evidence record.

_EDGAR_BATCH_TICKERS = (
    "CSCO", "ANET", "PWR", "NVT", "AMAT", "LRCX", "KLAC", "ENTG", "AMKR", "MKSI", "VRT", "TER", "ALAB", "RDW",
)
_DART_BATCH_TICKERS = ("011070", "012450", "047810", "454910", "240810", "056190", "036540", "067310")
_EDINET_BATCH_TICKERS_TO_CODES = {
    "80350": "E02652", "68570": "E01950", "61460": "E01506", "40630": "E00776",
    "34360": "E02103", "40620": "E00775", "65030": "E01739", "67230": "E02081",
}


def test_core_expansion_batch_present_exactly_once_each_and_active():
    companies = get_tracked_companies(active_only=True)
    by_ticker = {c.krx_code: c for c in companies}
    all_tickers = _EDGAR_BATCH_TICKERS + _DART_BATCH_TICKERS + tuple(_EDINET_BATCH_TICKERS_TO_CODES)
    assert len(all_tickers) == 30
    for ticker in all_tickers:
        matches = [c for c in companies if c.krx_code == ticker]
        assert len(matches) == 1, f"{ticker} must appear exactly once"
        assert by_ticker[ticker].active is True


def test_core_expansion_batch_edgar_source_and_corp_code_not_hardcoded():
    by_ticker = {c.krx_code: c for c in get_tracked_companies(active_only=True)}
    for ticker in _EDGAR_BATCH_TICKERS:
        assert by_ticker[ticker].source == "SEC EDGAR"
        # Unlike INDI/AIP/CEVA, this batch's live verification ran
        # against a scratchpad-only cache, never data/cache/edgar_ciks.json
        # — corp_code is correctly still unresolved; a separate,
        # explicitly-approved resolver gate against the real cache is
        # required before these companies are scan-ready.
        assert by_ticker[ticker].corp_code is None


def test_core_expansion_batch_dart_source_and_corp_code_not_hardcoded():
    by_ticker = {c.krx_code: c for c in get_tracked_companies(active_only=True)}
    for ticker in _DART_BATCH_TICKERS:
        assert by_ticker[ticker].source == "OpenDART / DART"
        assert by_ticker[ticker].corp_code is None


def test_core_expansion_batch_edinet_source_and_corp_code_hardcoded():
    by_ticker = {c.krx_code: c for c in get_tracked_companies(active_only=True)}
    for krx_code, edinet_code in _EDINET_BATCH_TICKERS_TO_CODES.items():
        assert by_ticker[krx_code].source == "EDINET"
        # EDINET is the one source where hardcoding is the established
        # convention (see module docstring) — these 8 codes were
        # independently live-verified this session against the real
        # official EDINET code-list artifact.
        assert by_ticker[krx_code].corp_code == edinet_code


def test_core_expansion_batch_legal_names():
    by_ticker = {c.krx_code: c for c in get_tracked_companies(active_only=True)}
    expected_names = {
        "CSCO": "Cisco Systems, Inc.", "ANET": "Arista Networks, Inc.", "PWR": "Quanta Services, Inc.",
        "NVT": "nVent Electric plc", "AMAT": "Applied Materials, Inc.", "LRCX": "Lam Research Corp",
        "KLAC": "KLA Corp", "ENTG": "Entegris, Inc.", "AMKR": "Amkor Technology, Inc.", "MKSI": "MKS Inc",
        "VRT": "Vertiv Holdings Co", "TER": "Teradyne, Inc", "ALAB": "Astera Labs, Inc.", "RDW": "Redwire Corp",
        "011070": "LG Innotek Co., Ltd.", "012450": "Hanwha Aerospace Co., Ltd.",
        "047810": "Korea Aerospace Industries, Ltd.", "454910": "Doosan Robotics Inc.",
        "240810": "Wonik IPS Co., Ltd.", "056190": "SFA Engineering Corporation",
        "036540": "SFA Semicon Co., Ltd", "067310": "Hana Micron Inc.",
        "80350": "Tokyo Electron Limited", "68570": "Advantest Corporation", "61460": "Disco Corporation",
        "40630": "Shin-Etsu Chemical Co., Ltd.", "34360": "SUMCO Corporation", "40620": "Ibiden Co., Ltd.",
        "65030": "Mitsubishi Electric Corporation", "67230": "Renesas Electronics Corporation",
    }
    for ticker, expected_name in expected_names.items():
        assert by_ticker[ticker].name == expected_name


def test_core_expansion_batch_themes_use_only_existing_primary_themes():
    from src.config.tracked_companies import TRACKED_COMPANIES

    all_tickers = _EDGAR_BATCH_TICKERS + _DART_BATCH_TICKERS + tuple(_EDINET_BATCH_TICKERS_TO_CODES)
    valid_themes = {"ai-buildout", "humanoids", "space", "memory", "photonics"}
    batch = [c for c in TRACKED_COMPANIES if c.krx_code in all_tickers]
    assert len(batch) == 30
    for c in batch:
        assert c.themes, f"{c.name} has no theme"
        assert set(c.themes) <= valid_themes, f"{c.name} uses an unrecognized theme: {c.themes}"


def test_core_expansion_batch_subthemes_only_reuse_existing_vocabulary_or_stay_unset():
    from src.config.tracked_companies import TRACKED_COMPANIES

    all_tickers = _EDGAR_BATCH_TICKERS + _DART_BATCH_TICKERS + tuple(_EDINET_BATCH_TICKERS_TO_CODES)
    # Every subtheme string already in use anywhere in the registry
    # before this batch (i.e. no new subtheme string was invented).
    pre_existing_subthemes = {
        "dram", "hbm", "compute-accelerators", "industrial-automation", "interconnect",
        "interconnect-switching", "launch", "optical-components", "power-cooling", "semiconductor-test",
    }
    batch = [c for c in TRACKED_COMPANIES if c.krx_code in all_tickers]
    for c in batch:
        assert set(c.subthemes) <= pre_existing_subthemes, f"{c.name} uses an invented subtheme: {c.subthemes}"


def test_core_expansion_batch_no_duplicate_identifiers_within_batch_or_against_existing_registry():
    companies = get_tracked_companies(active_only=False)
    # No duplicate krx_code within any single source.
    by_source: dict[str, list[str]] = {}
    for c in companies:
        by_source.setdefault(c.source, []).append(c.krx_code)
    for source, codes in by_source.items():
        assert len(codes) == len(set(codes)), f"{source} has a duplicate krx_code"
    # No duplicate corp_code among entries that have one set (EDINET only, today).
    corp_codes = [c.corp_code for c in companies if c.corp_code is not None]
    assert len(corp_codes) == len(set(corp_codes))
    # No duplicate company names anywhere in the registry.
    names = [c.name for c in companies]
    assert len(names) == len(set(names))


def test_core_expansion_batch_no_simmtech_entry_was_added():
    # Explicit guard: Simmtech was found to be a real parent/holding/
    # subsidiary split across three distinct DART entities during
    # verification and was deliberately excluded pending a separate
    # decision — must never appear anywhere in the registry.
    companies = get_tracked_companies(active_only=False)
    simmtech_tickers = {"222800", "036710"}  # Simmtech Co., Ltd. / Simmtech Holdings Co., Ltd.
    for c in companies:
        assert "simmtech" not in c.name.lower()
        assert c.krx_code not in simmtech_tickers
