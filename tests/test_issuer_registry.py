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
    # Core Issuer Expansion batch (2026-09-04) — 8 more hardcoded EDINET
    # identifiers, same live-verified-hardcoding convention.
    assert edinet_issuers["Tokyo Electron Limited"].identifiers["EDINET"] == "E02652"
    assert edinet_issuers["Advantest Corporation"].identifiers["EDINET"] == "E01950"
    assert edinet_issuers["Disco Corporation"].identifiers["EDINET"] == "E01506"
    assert edinet_issuers["Shin-Etsu Chemical Co., Ltd."].identifiers["EDINET"] == "E00776"
    assert edinet_issuers["SUMCO Corporation"].identifiers["EDINET"] == "E02103"
    assert edinet_issuers["Ibiden Co., Ltd."].identifiers["EDINET"] == "E00775"
    assert edinet_issuers["Mitsubishi Electric Corporation"].identifiers["EDINET"] == "E01739"
    assert edinet_issuers["Renesas Electronics Corporation"].identifiers["EDINET"] == "E02081"
    assert len(edinet_issuers) == 13


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
    # Core Issuer Expansion batch (2026-09-04) exception, explicitly
    # flagged before implementation and approved as a known, reported
    # finding rather than silently worked around: Quanta Services (PWR),
    # nVent Electric (NVT), Arista Networks (ANET), and Cisco Systems
    # (CSCO) were already present as Daily-News-only DISCOVERY_STUBS
    # entries and are now ALSO real, verified TrackedCompany/SEED_ISSUERS
    # entries — the two lists are no longer disjoint for exactly these
    # four tickers. Their own now-redundant DISCOVERY_STUBS entries were
    # deliberately left untouched (out of this batch's strict scope) —
    # see tracked_companies.py's own batch comment. Every other stub
    # ticker (including LG Innotek's own stub, "011070.KS" — a different
    # literal string from this batch's DART krx_code "011070", so it
    # never collided in the first place) remains correctly excluded.
    _known_redundant_tickers = {"PWR", "NVT", "ANET", "CSCO"}
    stub_tickers = {i.primary_ticker for i in DISCOVERY_STUBS} - _known_redundant_tickers
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
    # Core Issuer Expansion batch (2026-09-04) exception — same four
    # already-flagged, already-approved name collisions as
    # test_discovery_stubs_never_appear_in_compatibility_output above.
    from src.config.tracked_companies import get_tracked_companies_for_source

    _known_redundant_names = {
        "Quanta Services, Inc.", "nVent Electric plc", "Arista Networks, Inc.", "Cisco Systems, Inc.",
    }
    for source in ("OpenDART / DART", "SEC EDGAR", "EDINET"):
        names = {c.name for c in get_tracked_companies_for_source(source)}
        stub_names = {i.legal_name for i in DISCOVERY_STUBS} - _known_redundant_names
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

def test_seed_issuer_count_is_100_after_the_filings_radar_expansion_batch_2():
    # Was "exactly 32 after the INDI/AIP/CEVA batch" through that batch;
    # renamed and updated (Gate 7.1's own established discipline for a
    # stale count/name) after the Core Issuer Expansion batch
    # (2026-09-04) added 30 more (32 + 30 = 62), again after the Filings
    # Radar issuer-expansion batch (2026-09-04) added 19 more SEC EDGAR
    # issuers (62 + 19 = 81), and again after Filings Radar
    # issuer-expansion batch 2 (2026-09-04) added 19 more still
    # (81 + 19 = 100).
    assert len(SEED_ISSUERS) == 100


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
    # Core Issuer Expansion batch (2026-09-04) exception — see
    # test_discovery_stubs_never_appear_in_compatibility_output's own
    # comment for the four already-flagged, already-approved collisions
    # (unrelated to INDI/AIP/CEVA, which this test is actually about).
    _known_redundant_tickers = {"PWR", "NVT", "ANET", "CSCO"}
    compat_tickers = {c.krx_code for c in tracked_companies_from_issuer_registry(active_only=False)}
    assert (stub_tickers - _known_redundant_tickers).isdisjoint(compat_tickers)


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
    # True as of the Quanta-only addition (added only to DISCOVERY_STUBS,
    # TRACKED_COMPANIES/SEED_ISSUERS stayed at 32 at that point in this
    # file's own history). The Core Issuer Expansion batch (2026-09-04)
    # later added 30 more for an unrelated reason (32 + 30 = 62), the
    # Filings Radar issuer-expansion batch (2026-09-04) added 19 more
    # still (62 + 19 = 81), and Filings Radar issuer-expansion batch 2
    # (2026-09-04) added 19 more still (81 + 19 = 100) — see
    # test_quanta_is_now_also_a_real_tracked_company_via_the_core_expansion_batch
    # below for Quanta's own, now-changed status specifically.
    assert len(get_tracked_companies(active_only=False)) == 100
    assert len(SEED_ISSUERS) == 100


def test_quanta_is_now_also_a_real_tracked_company_via_the_core_expansion_batch():
    # Supersedes the former "excluded from the compatibility adapter"/
    # "excluded from every source selection path" claims, both true only
    # through the Quanta-only DISCOVERY_STUBS addition. The Core Issuer
    # Expansion batch (2026-09-04), a separate and later, explicitly-
    # approved action, added Quanta Services as a real, verified
    # TrackedCompany/SEED_ISSUERS entry too — flagged before
    # implementation as a known, approved stub/tracked-company overlap
    # (its own DISCOVERY_STUBS entry was deliberately left untouched, per
    # that batch's strict scope, and is now redundant).
    compat_tickers = {c.krx_code for c in tracked_companies_from_issuer_registry(active_only=False)}
    assert "PWR" in compat_tickers
    from src.config.tracked_companies import get_tracked_companies_for_source

    edgar_names = {c.name for c in get_tracked_companies_for_source("SEC EDGAR", active_only=False)}
    assert "Quanta Services, Inc." in edgar_names
    # It also still exists, unchanged, as its own separate DISCOVERY_STUBS entry.
    stub_tickers = {i.primary_ticker for i in DISCOVERY_STUBS}
    assert "PWR" in stub_tickers


# --- nVent Electric plc (2026-08-31) — a second Daily News-only
# DISCOVERY_STUBS entry, never added to tracked_companies.py/TRACKED_COMPANIES ---

def test_discovery_stubs_grew_by_exactly_one_for_nvent_electric():
    # DISCOVERY_STUBS grew again later (to 25) for the unrelated
    # Arista/Cisco addition below, so this asserts "at least nVent is
    # present" rather than an exact total tied to this batch alone.
    assert len(DISCOVERY_STUBS) >= 23
    nvent_matches = [i for i in DISCOVERY_STUBS if i.primary_ticker == "NVT"]
    assert len(nvent_matches) == 1
    nvent = nvent_matches[0]
    assert nvent.legal_name == "nVent Electric plc"
    assert nvent.coverage_state == CoverageState.DISCOVERED
    assert nvent.identifiers == {}
    assert nvent.themes == ("ai-buildout",)
    assert nvent.subthemes == ("power-cooling",)


def test_tracked_company_and_seed_issuer_counts_are_unaffected_by_nvent():
    # True as of the nVent-only addition — see the matching Quanta test
    # above for why this now asserts 100, not 32.
    assert len(get_tracked_companies(active_only=False)) == 100
    assert len(SEED_ISSUERS) == 100


def test_nvent_is_now_also_a_real_tracked_company_via_the_core_expansion_batch():
    # Supersedes the former exclusion claims — see the matching Quanta
    # test above for the full explanation.
    compat_tickers = {c.krx_code for c in tracked_companies_from_issuer_registry(active_only=False)}
    assert "NVT" in compat_tickers
    from src.config.tracked_companies import get_tracked_companies_for_source

    edgar_names = {c.name for c in get_tracked_companies_for_source("SEC EDGAR", active_only=False)}
    assert "nVent Electric plc" in edgar_names
    stub_tickers = {i.primary_ticker for i in DISCOVERY_STUBS}
    assert "NVT" in stub_tickers


# --- Arista Networks + Cisco Systems (2026-08-31) — two more Daily
# News-only DISCOVERY_STUBS entries, never added to
# tracked_companies.py/TRACKED_COMPANIES ---

def test_discovery_stubs_grew_by_exactly_two_for_arista_and_cisco():
    assert len(DISCOVERY_STUBS) == 25
    arista_matches = [i for i in DISCOVERY_STUBS if i.primary_ticker == "ANET"]
    cisco_matches = [i for i in DISCOVERY_STUBS if i.primary_ticker == "CSCO"]
    assert len(arista_matches) == 1
    assert len(cisco_matches) == 1
    arista, cisco = arista_matches[0], cisco_matches[0]
    assert arista.legal_name == "Arista Networks, Inc."
    assert cisco.legal_name == "Cisco Systems, Inc."
    for issuer in (arista, cisco):
        assert issuer.coverage_state == CoverageState.DISCOVERED
        assert issuer.identifiers == {}
        assert issuer.themes == ("ai-buildout",)
        assert issuer.supply_chain_layers == ("interconnect",)


def test_tracked_company_and_seed_issuer_counts_are_unaffected_by_arista_and_cisco():
    # True as of the Arista/Cisco-only addition — see the matching Quanta
    # test above for why this now asserts 100, not 32.
    assert len(get_tracked_companies(active_only=False)) == 100
    assert len(SEED_ISSUERS) == 100


def test_arista_and_cisco_are_now_also_real_tracked_companies_via_the_core_expansion_batch():
    # Supersedes the former exclusion claims — see the matching Quanta
    # test above for the full explanation.
    compat_tickers = {c.krx_code for c in tracked_companies_from_issuer_registry(active_only=False)}
    assert "ANET" in compat_tickers
    assert "CSCO" in compat_tickers
    from src.config.tracked_companies import get_tracked_companies_for_source

    edgar_names = {c.name for c in get_tracked_companies_for_source("SEC EDGAR", active_only=False)}
    assert "Arista Networks, Inc." in edgar_names
    assert "Cisco Systems, Inc." in edgar_names
    stub_tickers = {i.primary_ticker for i in DISCOVERY_STUBS}
    assert {"ANET", "CSCO"} <= stub_tickers
