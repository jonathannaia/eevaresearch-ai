"""Issuer Registry — Phase A (design/ISSUER_REGISTRY_FOUNDATION.md).

Covers the eight invariants the Phase A approval required: lossless
seed-issuer coverage, compatibility-adapter equivalence, identifier/source
fidelity, stub exclusion from the compatibility path, unique issuer IDs,
ontology validity, and known-conflict metadata. (The eighth — "existing
EDGAR/DART/EDINET/review-actions/Signal-eligibility tests still pass
unchanged" — is a full-suite run, not a test in this file; see the Phase A
final report.)"""
from src.config.issuer_registry import (
    DISCOVERY_STUBS,
    SEED_ISSUERS,
    get_all_issuers,
    tracked_companies_from_issuer_registry,
)
from src.config.ontology import KNOWN_CATEGORY_CONFLICTS, is_valid_layer, is_valid_theme
from src.config.tracked_companies import get_tracked_companies
from src.models.issuer import CoverageState


# --- 1. Every existing TrackedCompany record has a corresponding active seed issuer ---

def test_every_tracked_company_has_a_corresponding_seed_issuer():
    tracked = get_tracked_companies(active_only=False)
    seed_by_key = {(i.primary_exchange, i.primary_ticker): i for i in SEED_ISSUERS}
    assert len(tracked) == len(SEED_ISSUERS)
    for tc in tracked:
        issuer = seed_by_key.get((tc.exchange, tc.krx_code))
        assert issuer is not None, f"no seed issuer for {tc.name} ({tc.source}/{tc.krx_code})"
        assert issuer.legal_name == tc.name
        assert issuer.coverage_state == (CoverageState.SEED if tc.active else CoverageState.REJECTED)


def test_seed_issuer_count_matches_current_tracked_companies_registry():
    # Not hardcoded to a specific number here — see the Phase A final
    # report for today's actual count and how it compares to the
    # approval message's assumed count.
    assert len(SEED_ISSUERS) == len(get_tracked_companies(active_only=False))


# --- 2. Compatibility adapter produces an equivalent tracked-company universe ---

def test_compatibility_adapter_equals_existing_registry_active_only():
    assert tracked_companies_from_issuer_registry(active_only=True) == get_tracked_companies(active_only=True)


def test_compatibility_adapter_equals_existing_registry_including_inactive():
    assert (
        tracked_companies_from_issuer_registry(active_only=False)
        == get_tracked_companies(active_only=False)
    )


# --- 3. Existing source identifiers and source assignments survive migration exactly ---

def test_edinet_identifiers_survive_migration_exactly():
    edinet_issuers = {i.legal_name: i for i in SEED_ISSUERS if "EDINET" in i.identifiers}
    assert edinet_issuers["SoftBank Group Corp."].identifiers["EDINET"] == "E02778"
    assert edinet_issuers["Kioxia Holdings Corporation"].identifiers["EDINET"] == "E35948"
    assert edinet_issuers["Furukawa Electric Co., Ltd."].identifiers["EDINET"] == "E01332"
    assert edinet_issuers["FANUC CORPORATION"].identifiers["EDINET"] == "E01946"
    assert edinet_issuers["ispace, inc."].identifiers["EDINET"] == "E37584"
    assert len(edinet_issuers) == 5


def test_dart_and_edgar_seed_issuers_have_no_invented_identifiers():
    # corp_code/CIK is None on every DART/EDGAR TrackedCompany entry today
    # (resolved lazily at runtime) — the migration must not invent one.
    for issuer in SEED_ISSUERS:
        if "EDINET" not in issuer.identifiers:
            assert issuer.identifiers == {}


def test_seed_issuer_source_assignment_matches_issuer_id_prefix():
    by_ticker = {(tc.source, tc.krx_code) for tc in get_tracked_companies(active_only=False)}
    for issuer in SEED_ISSUERS:
        prefix, ticker = issuer.issuer_id.split(":", 1)
        source = {"dart": "OpenDART / DART", "edgar": "SEC EDGAR", "edinet": "EDINET"}[prefix]
        assert (source, ticker) in by_ticker


def test_seed_issuer_themes_and_subthemes_survive_migration_exactly():
    tracked_by_key = {(tc.source, tc.krx_code): tc for tc in get_tracked_companies(active_only=False)}
    for issuer in SEED_ISSUERS:
        source = {"dart": "OpenDART / DART", "edgar": "SEC EDGAR", "edinet": "EDINET"}[
            issuer.issuer_id.split(":", 1)[0]
        ]
        tc = tracked_by_key[(source, issuer.primary_ticker)]
        assert issuer.themes == tc.themes
        assert issuer.subthemes == tc.subthemes


# --- 4. Unverified discovery stubs cannot appear in the compatibility output ---

def test_discovery_stubs_never_appear_in_compatibility_output():
    stub_tickers = {i.primary_ticker for i in DISCOVERY_STUBS}
    compat_tickers = {c.krx_code for c in tracked_companies_from_issuer_registry(active_only=False)}
    assert stub_tickers.isdisjoint(compat_tickers)


def test_discovery_stubs_are_all_coverage_state_discovered():
    for issuer in DISCOVERY_STUBS:
        assert issuer.coverage_state == CoverageState.DISCOVERED


def test_discovery_stubs_have_no_identifiers():
    for issuer in DISCOVERY_STUBS:
        assert issuer.identifiers == {}, f"{issuer.issuer_id} must not carry an invented identifier"


def test_discovery_stubs_all_have_a_non_empty_normalization_status():
    for issuer in DISCOVERY_STUBS:
        assert issuer.normalization_status.strip() != ""


def test_get_tracked_companies_for_source_is_untouched_by_discovery_stubs():
    # tracked_companies.py itself imports nothing from this module — this
    # test exists to make that invariant explicit and regression-checked,
    # not because there's any code path that could plausibly break it.
    from src.config.tracked_companies import get_tracked_companies_for_source

    for source in ("OpenDART / DART", "SEC EDGAR", "EDINET"):
        names = {c.name for c in get_tracked_companies_for_source(source)}
        stub_names = {i.legal_name for i in DISCOVERY_STUBS}
        assert names.isdisjoint(stub_names)


# --- 5. No duplicate stable issuer IDs ---

def test_no_duplicate_issuer_ids_across_seed_and_stubs():
    all_ids = [issuer.issuer_id for issuer in get_all_issuers()]
    assert len(all_ids) == len(set(all_ids))


def test_seed_and_stub_issuer_id_namespaces_never_collide():
    seed_ids = {i.issuer_id for i in SEED_ISSUERS}
    stub_ids = {i.issuer_id for i in DISCOVERY_STUBS}
    assert seed_ids.isdisjoint(stub_ids)
    assert all(i.startswith("stub:") for i in stub_ids)
    assert all(i.startswith(("dart:", "edgar:", "edinet:")) for i in seed_ids)


# --- 6. Every registered theme/layer value is valid per the new ontology ---

def test_every_seed_issuer_theme_is_a_valid_primary_theme():
    for issuer in SEED_ISSUERS:
        for theme in issuer.themes:
            assert is_valid_theme(theme), f"{issuer.issuer_id} has invalid theme {theme!r}"


def test_every_stub_issuer_theme_and_layer_is_valid():
    for issuer in DISCOVERY_STUBS:
        for theme in issuer.themes:
            assert is_valid_theme(theme), f"{issuer.issuer_id} has invalid theme {theme!r}"
        for layer in issuer.supply_chain_layers:
            assert is_valid_layer(layer), f"{issuer.issuer_id} has invalid layer {layer!r}"
        assert len(issuer.supply_chain_layers) >= 1  # every stub carries its seed-list category as a layer


# --- 7. Known category conflicts are explicit unresolved metadata ---

def test_known_category_conflicts_cover_the_four_required_items():
    subjects = " ".join(c.subject for c in KNOWN_CATEGORY_CONFLICTS)
    assert "MRVL" in subjects
    assert "TSEM" in subjects
    assert "networking-interconnect" in subjects or "interconnect-switching" in subjects
    assert "Kioxia" in subjects and "285A" in subjects


def test_known_category_conflicts_are_all_marked_unresolved():
    for conflict in KNOWN_CATEGORY_CONFLICTS:
        assert conflict.status.startswith("Unresolved")


def test_mrvl_and_tsem_registry_themes_are_unchanged_by_ontology_module():
    # The conflict is documented, not silently fixed — MRVL/TSEM keep
    # their existing tracked_companies.py themes exactly.
    by_ticker = {i.primary_ticker: i for i in SEED_ISSUERS}
    assert by_ticker["MRVL"].themes == ("photonics",)
    assert by_ticker["TSEM"].themes == ("ai-buildout",)


# --- INDI/AIP/CEVA batch (2026-08-20) — grew the registry from 29 to 32 ---

def test_seed_issuer_count_is_32_after_the_indi_aip_ceva_batch():
    assert len(SEED_ISSUERS) == 32


def test_indi_aip_ceva_appear_exactly_once_each_in_seed_issuers():
    tickers = [i.primary_ticker for i in SEED_ISSUERS]
    for ticker in ("INDI", "AIP", "CEVA"):
        assert tickers.count(ticker) == 1


def test_indi_aip_ceva_still_pass_the_lossless_migration_and_compat_invariants():
    # These three flow through the exact same generated-not-hand-authored
    # path as every other SEED_ISSUERS entry — the existing
    # test_every_tracked_company_has_a_corresponding_seed_issuer and
    # compatibility-adapter-equality tests above already re-verify this
    # for all 32 (dynamic, not hardcoded to a stale count); this test
    # exists only to make the specific claim explicit for this batch.
    compat = tracked_companies_from_issuer_registry(active_only=True)
    real_tickers = {c.krx_code for c in compat}
    assert {"INDI", "AIP", "CEVA"} <= real_tickers


def test_discovery_stubs_are_unaffected_by_the_indi_aip_ceva_batch():
    # 21 stubs before and after this specific batch — DISCOVERY_STUBS
    # grew again later (to 22) for the unrelated Quanta Services
    # addition below, so this asserts "at least the 21 from this batch
    # are present and untouched" rather than an exact total.
    assert len(DISCOVERY_STUBS) >= 21
    stub_tickers = {i.primary_ticker for i in DISCOVERY_STUBS}
    assert stub_tickers.isdisjoint({"INDI", "AIP", "CEVA"})
    compat_tickers = {c.krx_code for c in tracked_companies_from_issuer_registry(active_only=False)}
    assert stub_tickers.isdisjoint(compat_tickers)


# --- Quanta Services (2026-08-30) — a Daily News-only DISCOVERY_STUBS
# entry, never added to tracked_companies.py/TRACKED_COMPANIES ---

def test_discovery_stubs_grew_by_exactly_one_for_quanta_services():
    # DISCOVERY_STUBS grew again later (to 23) for the unrelated nVent
    # Electric addition below, so this asserts "at least Quanta is
    # present" rather than an exact total tied to this batch alone.
    assert len(DISCOVERY_STUBS) >= 22
    quanta_matches = [i for i in DISCOVERY_STUBS if i.primary_ticker == "PWR"]
    assert len(quanta_matches) == 1
    quanta = quanta_matches[0]
    assert quanta.legal_name == "Quanta Services, Inc."
    assert quanta.coverage_state == CoverageState.DISCOVERED
    assert quanta.identifiers == {}


def test_tracked_company_and_seed_issuer_counts_are_unaffected_by_quanta():
    # Quanta was added only to DISCOVERY_STUBS — TRACKED_COMPANIES and
    # SEED_ISSUERS (generated from it) must stay at exactly 32.
    assert len(get_tracked_companies(active_only=False)) == 32
    assert len(SEED_ISSUERS) == 32


def test_quanta_is_excluded_from_the_compatibility_adapter():
    compat_tickers = {c.krx_code for c in tracked_companies_from_issuer_registry(active_only=False)}
    assert "PWR" not in compat_tickers


def test_quanta_is_excluded_from_every_source_selection_path():
    # get_tracked_companies_for_source only ever reads TRACKED_COMPANIES
    # directly — DISCOVERY_STUBS is never merged into it — so this is a
    # structural guarantee, not a filter that could be forgotten. Proven
    # explicitly here for all three real sources.
    from src.config.tracked_companies import get_tracked_companies_for_source

    for source in ("SEC EDGAR", "OpenDART / DART", "EDINET"):
        tickers = {c.krx_code for c in get_tracked_companies_for_source(source, active_only=False)}
        assert "PWR" not in tickers
        names = {c.name for c in get_tracked_companies_for_source(source, active_only=False)}
        assert "Quanta Services, Inc." not in names


# --- nVent Electric plc (2026-08-31) — a second Daily News-only
# DISCOVERY_STUBS entry, never added to tracked_companies.py/TRACKED_COMPANIES ---

def test_discovery_stubs_grew_by_exactly_one_for_nvent_electric():
    assert len(DISCOVERY_STUBS) == 23
    nvent_matches = [i for i in DISCOVERY_STUBS if i.primary_ticker == "NVT"]
    assert len(nvent_matches) == 1
    nvent = nvent_matches[0]
    assert nvent.legal_name == "nVent Electric plc"
    assert nvent.coverage_state == CoverageState.DISCOVERED
    assert nvent.identifiers == {}
    assert nvent.themes == ("ai-buildout",)
    assert nvent.subthemes == ("power-cooling",)


def test_tracked_company_and_seed_issuer_counts_are_unaffected_by_nvent():
    # nVent was added only to DISCOVERY_STUBS — TRACKED_COMPANIES and
    # SEED_ISSUERS (generated from it) must stay at exactly 32.
    assert len(get_tracked_companies(active_only=False)) == 32
    assert len(SEED_ISSUERS) == 32


def test_nvent_is_excluded_from_the_compatibility_adapter():
    compat_tickers = {c.krx_code for c in tracked_companies_from_issuer_registry(active_only=False)}
    assert "NVT" not in compat_tickers


def test_nvent_is_excluded_from_every_source_selection_path():
    from src.config.tracked_companies import get_tracked_companies_for_source

    for source in ("SEC EDGAR", "OpenDART / DART", "EDINET"):
        tickers = {c.krx_code for c in get_tracked_companies_for_source(source, active_only=False)}
        assert "NVT" not in tickers
        names = {c.name for c in get_tracked_companies_for_source(source, active_only=False)}
        assert "nVent Electric plc" not in names
