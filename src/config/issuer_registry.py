"""Issuer Registry — Phase A (design/ISSUER_REGISTRY_FOUNDATION.md).

Two static collections:

- `SEED_ISSUERS`: every entry in `src.config.tracked_companies.
  TRACKED_COMPANIES`, converted losslessly into `Issuer` records. Generated
  programmatically from the live tuple (never hand-transcribed) so it can
  never drift out of sync with — or introduce a transcription error
  against — the registry every existing pipeline already depends on.
- `DISCOVERY_STUBS`: the portfolio-map seed-universe companies not already
  covered by `SEED_ISSUERS`, recorded as unverified `Issuer` proposals
  (`coverage_state=DISCOVERED`) with no identifiers invented for any of
  them. Structurally excluded from `tracked_companies_from_issuer_registry`
  below and therefore from every existing source pipeline — see that
  function's own docstring for why that exclusion is structural, not a
  filter that could be forgotten.

Nothing in this module is imported by any existing pipeline, page, or
component — it is purely additive. `src/config/tracked_companies.py` is
untouched by this phase."""
from __future__ import annotations

from src.config.tracked_companies import TrackedCompany, get_tracked_companies
from src.models.issuer import CoverageState, Issuer, LifecycleState

# TrackedCompany.source -> a short, stable namespace prefix for issuer_id
# construction. Deliberately a local mapping, not derived from anything
# dynamic — matches TrackedCompany's own "known, closed set of three
# sources" reality in this phase.
_SOURCE_ID_PREFIX: dict[str, str] = {
    "OpenDART / DART": "dart",
    "SEC EDGAR": "edgar",
    "EDINET": "edinet",
}

# TrackedCompany.exchange -> the jurisdiction that exchange is domiciled
# in. Deliberately describes *listing* jurisdiction only, not legal
# domicile — several tracked companies are foreign private issuers whose
# real domicile differs from their listing exchange's country (e.g. Arm,
# Nokia, Nebius, Tower Semiconductor — each already documented as such in
# that company's own TrackedCompany.notes, carried forward verbatim into
# Issuer.notes below). Re-deriving true legal domicile from free-text
# notes reliably isn't attempted here — that would be exactly the kind of
# invented precision this phase avoids; readers needing true domicile
# should read the migrated `notes` field for the four known exceptions.
_LISTING_JURISDICTION_BY_EXCHANGE: dict[str, str] = {
    "KRX": "South Korea (listing exchange)",
    "NASDAQ": "United States (listing exchange)",
    "NYSE": "United States (listing exchange)",
    "NYSE American": "United States (listing exchange)",
    "TSE": "Japan (listing exchange)",
}


def _issuer_id_for_tracked_company(tc: TrackedCompany) -> str:
    return f"{_SOURCE_ID_PREFIX[tc.source]}:{tc.krx_code}"


def _issuer_from_tracked_company(tc: TrackedCompany) -> Issuer:
    return Issuer(
        issuer_id=_issuer_id_for_tracked_company(tc),
        legal_name=tc.name,
        native_name=tc.native_name,
        country_or_jurisdiction=_LISTING_JURISDICTION_BY_EXCHANGE.get(tc.exchange, "Unconfirmed"),
        coverage_state=CoverageState.SEED if tc.active else CoverageState.REJECTED,
        lifecycle_state=LifecycleState.ACTIVE if tc.active else LifecycleState.MONITORING,
        primary_ticker=tc.krx_code,
        primary_exchange=tc.exchange,
        # Only carried forward when already resolved in the static tuple
        # (true today only for the five hardcoded EDINET entries) — never
        # invented for the DART/EDGAR entries whose real identifier is
        # resolved lazily at runtime from each source's own resolver
        # cache, exactly matching TrackedCompany.corp_code's own
        # "None means not yet resolved" convention.
        identifiers={tc.source: tc.corp_code} if tc.corp_code else {},
        themes=tc.themes,
        subthemes=tc.subthemes,
        evidence_confidence="Verified via existing tracked-company registry (pre-Phase-A)",
        discovered_via="Migrated from src.config.tracked_companies (Phase A)",
        normalization_status="",  # empty is valid here — this is the already-verified path, see module docstring
        notes=tc.notes,
    )


SEED_ISSUERS: tuple[Issuer, ...] = tuple(
    _issuer_from_tracked_company(tc) for tc in get_tracked_companies(active_only=False)
)


def _stub(
    *,
    seed_ticker: str,
    legal_name: str,
    country_or_jurisdiction: str,
    primary_exchange: str | None,
    seed_category: str,
    theme: str | None,
    layer: str,
    normalization_status: str,
) -> Issuer:
    return Issuer(
        issuer_id=f"stub:{seed_ticker}",
        legal_name=legal_name,
        country_or_jurisdiction=country_or_jurisdiction,
        coverage_state=CoverageState.DISCOVERED,
        lifecycle_state=LifecycleState.ACTIVE,
        primary_ticker=seed_ticker,
        primary_exchange=primary_exchange,
        identifiers={},  # never invented — see module docstring
        themes=(theme,) if theme else (),
        supply_chain_layers=(layer,),
        evidence_confidence="Unverified — pending live confirmation",
        discovered_via=f"Portfolio-map seed list, category: {seed_category} (2026-08-20)",
        discovered_at="2026-08-20",
        normalization_status=normalization_status,
        notes=f"Seed-list entry, category '{seed_category}'. Not yet independently verified.",
    )


_UNCONFIRMED_JURISDICTION = "Unconfirmed — no exchange suffix given, jurisdiction not independently verified"
_UNRECOGNIZED_TICKER_FORMAT = "Unconfirmed — ticker/exchange format not recognized"

# Every seed-list ticker from the portfolio map that does not already
# match a `SEED_ISSUERS` entry (cross-checked against
# src/config/tracked_companies.py by ticker/native-code, not guessed) —
# see design/ISSUER_REGISTRY_FOUNDATION.md's normalization report for the
# full match/no-match reasoning per entry. `theme` is left None wherever
# the seed category doesn't map cleanly onto one of ontology.PRIMARY_THEMES
# (a supply-chain layer can be relevant across multiple themes — asserting
# one would be a guess); `layer` always reflects the seed list's own
# category grouping, which is given information, not inferred.
DISCOVERY_STUBS: tuple[Issuer, ...] = (
    _stub(
        seed_ticker="SIVE.ST", legal_name="SIVE.ST (name not given in seed list)",
        country_or_jurisdiction="Sweden (inferred from ticker suffix — unverified)",
        primary_exchange="Nasdaq Stockholm (inferred from ticker suffix — unverified)",
        seed_category="Interconnect/photonics", theme="photonics", layer="interconnect",
        normalization_status=(
            "No Swedish (Finansinspektionen) source adapter exists. Company "
            "identity, legal name, and any identifier are entirely unverified — "
            "only the raw ticker was given."
        ),
    ),
    _stub(
        seed_ticker="AMPG", legal_name="AMPG (name not given in seed list)",
        country_or_jurisdiction=_UNCONFIRMED_JURISDICTION,
        primary_exchange=None,
        seed_category="Interconnect/photonics", theme="photonics", layer="interconnect",
        normalization_status=(
            "Bare ticker, no exchange suffix given — jurisdiction, listing "
            "exchange, and legal name are all unverified."
        ),
    ),
    _stub(
        seed_ticker="3363.TW", legal_name="FOCI",
        country_or_jurisdiction="Taiwan (inferred from ticker suffix — unverified)",
        primary_exchange="Taiwan Stock Exchange (inferred from ticker suffix — unverified)",
        seed_category="Interconnect/photonics", theme="photonics", layer="interconnect",
        normalization_status=(
            "No Taiwan (TWSE/MOPS) source adapter exists. 'FOCI' is the name "
            "given in the seed list, not independently confirmed as the exact "
            "official legal name."
        ),
    ),
    _stub(
        seed_ticker="ADTN", legal_name="ADTN (name not given in seed list)",
        country_or_jurisdiction=_UNCONFIRMED_JURISDICTION,
        primary_exchange=None,
        seed_category="Interconnect/photonics", theme="photonics", layer="interconnect",
        normalization_status=(
            "Bare ticker, no exchange suffix given. Ticker format is consistent "
            "with a US-listed issuer (which would be SEC EDGAR-eligible), but "
            "this is not independently verified, and no CIK is recorded."
        ),
    ),
    _stub(
        seed_ticker="3037.TW", legal_name="Unimicron",
        country_or_jurisdiction="Taiwan (inferred from ticker suffix — unverified)",
        primary_exchange="Taiwan Stock Exchange (inferred from ticker suffix — unverified)",
        seed_category="Advanced packaging", theme=None, layer="advanced-packaging",
        normalization_status="No Taiwan (TWSE/MOPS) source adapter exists.",
    ),
    _stub(
        seed_ticker="011070.KS", legal_name="LG Innotek",
        country_or_jurisdiction="South Korea (inferred from ticker suffix — unverified)",
        primary_exchange="KRX (inferred from ticker suffix — unverified)",
        seed_category="Advanced packaging", theme=None, layer="advanced-packaging",
        normalization_status=(
            "Korea-listed — plausibly OpenDART/DART-eligible like the two "
            "existing DART entries, but corp_code has not been resolved and "
            "krx_code has not been independently confirmed against DART's own "
            "corpCode.xml."
        ),
    ),
    _stub(
        seed_ticker="UMC", legal_name="UMC (name not given in seed list)",
        country_or_jurisdiction=_UNCONFIRMED_JURISDICTION,
        primary_exchange=None,
        seed_category="Foundry/manufacturing", theme=None, layer="semiconductor-foundry",
        normalization_status=(
            "Bare ticker. Plausibly United Microelectronics Corp, which trades "
            "as a US ADR (SEC EDGAR-eligible) alongside its Taiwan primary "
            "listing — not independently verified, and no CIK is recorded."
        ),
    ),
    _stub(
        seed_ticker="3105.TW", legal_name="WIN Semiconductors",
        country_or_jurisdiction="Taiwan (inferred from ticker suffix — unverified)",
        primary_exchange="Taiwan Stock Exchange (inferred from ticker suffix — unverified)",
        seed_category="Foundry/manufacturing", theme=None, layer="semiconductor-foundry",
        normalization_status="No Taiwan (TWSE/MOPS) source adapter exists.",
    ),
    _stub(
        seed_ticker="6451.TW", legal_name="Shunsin",
        country_or_jurisdiction="Taiwan (inferred from ticker suffix — unverified)",
        primary_exchange="Taiwan Stock Exchange (inferred from ticker suffix — unverified)",
        seed_category="Foundry/manufacturing", theme=None, layer="semiconductor-foundry",
        normalization_status="No Taiwan (TWSE/MOPS) source adapter exists.",
    ),
    _stub(
        seed_ticker="XFAB.PA", legal_name="XFAB.PA (name not given in seed list)",
        country_or_jurisdiction="France (inferred from ticker suffix — unverified)",
        primary_exchange="Euronext Paris (inferred from ticker suffix — unverified)",
        seed_category="Foundry/manufacturing", theme=None, layer="semiconductor-foundry",
        normalization_status=(
            "No French (AMF) source adapter exists. No legal name was given in "
            "the seed list — 'X-FAB' is a plausible reading of the ticker but "
            "is not asserted here as a verified legal name."
        ),
    ),
    _stub(
        seed_ticker="LPK.DE", legal_name="LPKF",
        country_or_jurisdiction="Germany (inferred from ticker suffix — unverified)",
        primary_exchange="Deutsche Börse / XETRA (inferred from ticker suffix — unverified)",
        seed_category="Equipment/test", theme=None, layer="semiconductor-equipment",
        normalization_status="No German (Bundesanzeiger) source adapter exists.",
    ),
    _stub(
        seed_ticker="PDFS", legal_name="PDFS (name not given in seed list)",
        country_or_jurisdiction=_UNCONFIRMED_JURISDICTION,
        primary_exchange=None,
        seed_category="Equipment/test", theme=None, layer="semiconductor-equipment",
        normalization_status=(
            "Bare ticker, no exchange suffix given. Ticker format is consistent "
            "with a US-listed issuer (plausibly PDF Solutions, which would be "
            "SEC EDGAR-eligible) but this is not independently verified, and no "
            "CIK is recorded."
        ),
    ),
    _stub(
        seed_ticker="M7U.DE", legal_name="Nynomic",
        country_or_jurisdiction="Germany (inferred from ticker suffix — unverified)",
        primary_exchange="Deutsche Börse / XETRA (inferred from ticker suffix — unverified)",
        seed_category="Materials/components", theme=None, layer="semiconductor-materials",
        normalization_status="No German (Bundesanzeiger) source adapter exists.",
    ),
    _stub(
        seed_ticker="BURUN", legal_name="Boost Run",
        country_or_jurisdiction=_UNRECOGNIZED_TICKER_FORMAT,
        primary_exchange=None,
        seed_category="Materials/components", theme=None, layer="semiconductor-materials",
        normalization_status=(
            "EXPLICITLY FLAGGED AS AMBIGUOUS. 'BURUN' does not match any "
            "exchange-suffix convention seen elsewhere in the seed list — the "
            "listing exchange, and even whether this is a standard public "
            "ticker, cannot be determined from what was given. No identifier, "
            "jurisdiction, or source adapter can be assigned until this is "
            "independently identified."
        ),
    ),
    _stub(
        seed_ticker="6830.TW", legal_name="Msscorps",
        country_or_jurisdiction="Taiwan (inferred from ticker suffix — unverified)",
        primary_exchange="Taiwan Stock Exchange (inferred from ticker suffix — unverified)",
        seed_category="Materials/components", theme=None, layer="semiconductor-materials",
        normalization_status="No Taiwan (TWSE/MOPS) source adapter exists.",
    ),
    _stub(
        seed_ticker="AXTI", legal_name="AXTI (name not given in seed list)",
        country_or_jurisdiction=_UNCONFIRMED_JURISDICTION,
        primary_exchange=None,
        seed_category="Materials/components", theme=None, layer="semiconductor-materials",
        normalization_status=(
            "Bare ticker, no exchange suffix given. Ticker format is consistent "
            "with a US-listed issuer (plausibly AXT, Inc.) but this is not "
            "independently verified, and no CIK is recorded."
        ),
    ),
    _stub(
        seed_ticker="P4O", legal_name="Plan Optik",
        country_or_jurisdiction=_UNRECOGNIZED_TICKER_FORMAT,
        primary_exchange=None,
        seed_category="Materials/components", theme=None, layer="semiconductor-materials",
        normalization_status=(
            "EXPLICITLY FLAGGED AS AMBIGUOUS. 'P4O' carries no recognizable "
            "exchange suffix (a German micro-cap listing is plausible given "
            "'Plan Optik' but is not confirmed) — listing exchange and any "
            "identifier are unverified."
        ),
    ),
    _stub(
        seed_ticker="IQE.L", legal_name="IQE.L (name not given in seed list)",
        country_or_jurisdiction="United Kingdom (inferred from ticker suffix — unverified)",
        primary_exchange="London Stock Exchange (inferred from ticker suffix — unverified)",
        seed_category="Materials/components", theme=None, layer="semiconductor-materials",
        normalization_status=(
            "No UK (FCA National Storage Mechanism) source adapter exists. "
            "'IQE plc' is a plausible reading of the ticker but is not "
            "asserted here as verified."
        ),
    ),
    _stub(
        seed_ticker="3103.T", legal_name="Unitika",
        country_or_jurisdiction="Japan (inferred from ticker suffix — unverified)",
        primary_exchange="TSE (inferred from ticker suffix — unverified)",
        seed_category="Materials/components", theme=None, layer="semiconductor-materials",
        normalization_status=(
            "Japan-listed — plausibly EDINET-eligible like the five existing "
            "EDINET entries, but no EDINET code has been resolved and the "
            "'materials/components' category fit is itself unconfirmed (Unitika "
            "is historically a textiles/materials conglomerate — worth "
            "double-checking relevance, not assuming it)."
        ),
    ),
    _stub(
        seed_ticker="FCEL", legal_name="FCEL (name not given in seed list)",
        country_or_jurisdiction=_UNCONFIRMED_JURISDICTION,
        primary_exchange=None,
        seed_category="Power/thermal", theme=None, layer="power-infrastructure",
        normalization_status=(
            "Bare ticker, no exchange suffix given. Ticker format is consistent "
            "with a US-listed issuer (plausibly FuelCell Energy) but this is "
            "not independently verified, and no CIK is recorded."
        ),
    ),
    _stub(
        seed_ticker="SHT.ST", legal_name="SHT.ST (name not given in seed list)",
        country_or_jurisdiction=_UNRECOGNIZED_TICKER_FORMAT,
        primary_exchange="Nasdaq Stockholm (inferred from ticker suffix — unverified)",
        seed_category="Power/thermal", theme=None, layer="power-infrastructure",
        normalization_status=(
            "EXPLICITLY FLAGGED AS AMBIGUOUS. 'SHT' is too generic a ticker "
            "fragment to confidently identify a single issuer from the seed "
            "list alone — company identity itself, not just its identifiers, "
            "is unresolved."
        ),
    ),
    # Quanta Services, Inc. — a Daily News-only discovery, not from the
    # 2026-08-20 portfolio-map seed list (see every other stub above), so
    # it is constructed directly rather than through _stub(), which
    # hardcodes that seed list's own discovered_via/discovered_at
    # provenance. A verified official RSS feed exists
    # (investors.quantaservices.com) — see design/DECISIONS.md — but no
    # CIK has been resolved and this issuer is not part of any
    # EDGAR/DART/EDINET scan universe. DISCOVERED coverage_state
    # structurally excludes it from tracked_companies_from_issuer_registry()
    # and therefore from every existing scan pipeline, same as every
    # other entry in this tuple.
    Issuer(
        issuer_id="stub:PWR",
        legal_name="Quanta Services, Inc.",
        country_or_jurisdiction="United States (listing exchange)",
        coverage_state=CoverageState.DISCOVERED,
        lifecycle_state=LifecycleState.ACTIVE,
        primary_ticker="PWR",
        primary_exchange="NYSE",
        identifiers={},  # no CIK resolved or cached — Daily News-only, never a Radar identifier
        themes=("ai-buildout",),
        supply_chain_layers=("power-infrastructure",),
        evidence_confidence="Official RSS feed verified live; company identity/ticker not independently cross-checked against SEC EDGAR",
        discovered_via="Daily News official-feed verification (design/DECISIONS.md)",
        discovered_at="2026-08-30",
        normalization_status=(
            "Daily News-only candidate. Not eligible for Radar filing scanning "
            "— no CIK resolved, not part of tracked_companies.py, structurally "
            "excluded via DISCOVERED coverage_state."
        ),
        notes="Daily News official-company source only; not eligible for Radar filing scanning.",
    ),
    # nVent Electric plc — a Daily News-only discovery, same shape and
    # reasoning as the Quanta Services entry above: constructed directly
    # (not via _stub()) since its provenance is a live Daily News feed
    # verification, not the 2026-08-20 portfolio-map seed list. Verified
    # official RSS feed at investors.nvent.com — see design/DECISIONS.md.
    # NYSE-listed but Irish-incorporated (the "plc" suffix); no CIK
    # resolved, not part of any EDGAR/DART/EDINET scan universe.
    Issuer(
        issuer_id="stub:NVT",
        legal_name="nVent Electric plc",
        country_or_jurisdiction="Ireland",
        coverage_state=CoverageState.DISCOVERED,
        lifecycle_state=LifecycleState.ACTIVE,
        primary_ticker="NVT",
        primary_exchange="NYSE",
        identifiers={},  # no CIK resolved or cached — Daily News-only, never a Radar identifier
        themes=("ai-buildout",),
        subthemes=("power-cooling",),
        supply_chain_layers=("power-infrastructure",),
        evidence_confidence="Official RSS feed verified live; company identity, NYSE ticker, and Irish incorporation verified through official investor-relations and SEC materials.",
        discovered_via="Daily News official-feed verification (design/DECISIONS.md)",
        normalization_status=(
            "Daily News-only candidate. Not eligible for Radar filing scanning "
            "— no CIK resolved, not part of tracked_companies.py, structurally "
            "excluded via DISCOVERED coverage_state."
        ),
        notes="Daily News official-company source only; not eligible for Radar filing scanning.",
    ),
    # Arista Networks, Inc. — a Daily News-only discovery, same shape and
    # reasoning as Quanta Services/nVent Electric above: constructed
    # directly (not via _stub()) since its provenance is a live Daily
    # News feed verification, not the 2026-08-20 portfolio-map seed
    # list. Verified official RSS feed at investors.arista.com — see
    # design/DECISIONS.md. No CIK resolved, not part of any
    # EDGAR/DART/EDINET scan universe.
    Issuer(
        issuer_id="stub:ANET",
        legal_name="Arista Networks, Inc.",
        country_or_jurisdiction="United States",
        coverage_state=CoverageState.DISCOVERED,
        lifecycle_state=LifecycleState.ACTIVE,
        primary_ticker="ANET",
        primary_exchange="NYSE",
        identifiers={},  # no CIK resolved or cached — Daily News-only, never a Radar identifier
        themes=("ai-buildout",),
        supply_chain_layers=("interconnect",),
        evidence_confidence="Official RSS feed verified live; company identity, NYSE ticker, and Delaware incorporation verified through official investor-relations and SEC materials.",
        discovered_via="Daily News official-feed verification (design/DECISIONS.md)",
        normalization_status=(
            "Daily News-only candidate. Not eligible for Radar filing scanning "
            "— no CIK resolved, not part of tracked_companies.py, structurally "
            "excluded via DISCOVERED coverage_state."
        ),
        notes="Daily News official-company source only; not eligible for Radar filing scanning.",
    ),
    # Cisco Systems, Inc. — a Daily News-only discovery, same shape and
    # reasoning as the entries above. Verified official RSS feed at
    # newsroom.cisco.com — see design/DECISIONS.md. The feed endpoint's
    # own path ends in ".json", but its raw response content was
    # confirmed live to be genuine RSS 2.0 XML
    # (`<?xml version="1.0"?><rss version="2.0">`), not JSON — never
    # classified by filename extension alone. No CIK resolved, not part
    # of any EDGAR/DART/EDINET scan universe.
    Issuer(
        issuer_id="stub:CSCO",
        legal_name="Cisco Systems, Inc.",
        country_or_jurisdiction="United States",
        coverage_state=CoverageState.DISCOVERED,
        lifecycle_state=LifecycleState.ACTIVE,
        primary_ticker="CSCO",
        primary_exchange="NASDAQ",
        identifiers={},  # no CIK resolved or cached — Daily News-only, never a Radar identifier
        themes=("ai-buildout",),
        supply_chain_layers=("interconnect",),
        evidence_confidence="Official RSS 2.0 feed verified live; company identity, NASDAQ ticker, and Delaware incorporation verified through official newsroom/investor-relations and SEC materials. The endpoint path ends in .json but the response content is RSS 2.0 XML.",
        discovered_via="Daily News official-feed verification (design/DECISIONS.md)",
        normalization_status=(
            "Daily News-only candidate. Not eligible for Radar filing scanning "
            "— no CIK resolved, not part of tracked_companies.py, structurally "
            "excluded via DISCOVERED coverage_state."
        ),
        notes="Daily News official-company source only; not eligible for Radar filing scanning.",
    ),
)


def source_name_for_seed_issuer(issuer: Issuer) -> str:
    """Public accessor for the source-display-name a `SEED_ISSUERS` entry's
    `issuer_id` prefix maps back to (e.g. "dart:005930" -> "OpenDART /
    DART"). Returns "Unknown" for anything outside that prefix scheme —
    concretely, any `DISCOVERY_STUBS` entry, which uses the "stub:"
    namespace and has no single source by design (see module docstring)."""
    prefix = issuer.issuer_id.split(":", 1)[0]
    return _PREFIX_TO_SOURCE.get(prefix, "Unknown")


def get_seed_issuers() -> tuple[Issuer, ...]:
    return SEED_ISSUERS


def get_discovery_stubs() -> tuple[Issuer, ...]:
    return DISCOVERY_STUBS


def get_all_issuers() -> tuple[Issuer, ...]:
    """Seed issuers followed by discovery stubs — never merged into one
    coverage_state, always distinguishable by `.coverage_state`."""
    return SEED_ISSUERS + DISCOVERY_STUBS


def tracked_companies_from_issuer_registry(active_only: bool = True) -> tuple[TrackedCompany, ...]:
    """Converts `SEED_ISSUERS` back into `TrackedCompany` records —
    proof that the new Issuer model can represent the exact same universe
    every existing DART/EDGAR/EDINET pipeline already scans, without
    requiring any of those pipelines to change in this phase.

    Only ever reads `SEED_ISSUERS` — `DISCOVERY_STUBS` is structurally
    unreachable from this function, so an unverified stub can never flow
    into anything that calls this the way a real pipeline eventually
    might. No existing pipeline calls this function today; it exists to
    be tested against `tracked_companies.get_tracked_companies()`, not to
    be wired in."""
    companies = tuple(
        TrackedCompany(
            name=issuer.legal_name,
            exchange=issuer.primary_exchange or "",
            krx_code=issuer.primary_ticker or "",
            source=_source_from_issuer_id(issuer.issuer_id),
            themes=issuer.themes,
            subthemes=issuer.subthemes,
            active=issuer.coverage_state == CoverageState.SEED,
            notes=issuer.notes,
            corp_code=issuer.identifiers.get(_source_from_issuer_id(issuer.issuer_id)),
            native_name=issuer.native_name,
        )
        for issuer in SEED_ISSUERS
    )
    if not active_only:
        return companies
    return tuple(c for c in companies if c.active)


_PREFIX_TO_SOURCE = {v: k for k, v in _SOURCE_ID_PREFIX.items()}


def _source_from_issuer_id(issuer_id: str) -> str:
    prefix = issuer_id.split(":", 1)[0]
    return _PREFIX_TO_SOURCE[prefix]
