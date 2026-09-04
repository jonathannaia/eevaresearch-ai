"""Tracked-company registry for live radar sourcing (Korea DART + SEC
EDGAR pilots).

Deliberately separate from src/models/models.py's Ticker — a
TrackedCompany is a source-configuration record ("which real company do
we scan, from which source, resolved how"), not a research/thesis
record. Shared across both pilots via the same `krx_code`/`corp_code`
field slots (reused, not renamed, per the milestone-8 decision to keep
DART's field names completely unchanged — see design/DECISIONS.md):

    source == "OpenDART / DART": krx_code = 6-digit KRX stock code,
        corp_code = DART's internal 8-digit issuer code
    source == "SEC EDGAR": krx_code = ticker, corp_code = normalized
        10-digit SEC CIK

corp_code/cik are intentionally NOT hardcoded here for DART or EDGAR —
DART's internal corp_code and SEC's CIK both need resolving against each
source's own official bulk lookup, and guessing either from memory would
break the same verify-against-real-data discipline the rest of this app
follows. DART's corp_code is resolved via
src/data_access/dart/corp_code_resolver.py; SEC's CIK is resolved via
src/data_access/edgar/cik_resolver.py (which additionally cross-checks
the resolved CIK against real submissions metadata before accepting it —
see that module's own docstring). Both cache to data/cache/ (gitignored,
never committed) — this module only defines what's known without a live
call.

EDINET pilot (Gate 7) deliberately breaks that "never hardcode" pattern
for its five entries below, and only because each value was already
independently live-verified across two separate prior gates before this
one — not guessed, not carried forward from a single source:
`corp_code` (EDINET code) and `krx_code` (here holding EDINET's own
5-character source-native securities code, e.g. "99840" — NOT the bare
4-character TSE code, and NOT the same meaning `krx_code` carries for
DART/EDGAR) both came from the real EDINET code-list artifact (Gate 2,
2026-08-17); the SoftBank Group entry's exact identifiers were further
independently confirmed against a live EDINET document-list record
(Gate 6, docID S100YGH5). No EDINET resolver cache is consulted at
runtime for these five entries — they need none.
"""
from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class TrackedCompany:
    name: str
    exchange: str  # e.g. "KRX", "NASDAQ", "NYSE", "TSE"
    krx_code: str  # KRX stock code (DART), ticker (EDGAR), or 5-char EDINET securities code (EDINET) — see module docstring
    source: str  # "OpenDART / DART", "SEC EDGAR", or "EDINET"
    themes: tuple[str, ...]  # theme slugs (src/data_access side), primary first
    subthemes: tuple[str, ...] = ()
    active: bool = True
    notes: str = ""
    # Populated separately at runtime from each source's own resolver
    # cache — see module docstring — EXCEPT the five EDINET entries
    # below, which are hardcoded because they were already live-verified
    # (see module docstring). None means "not yet resolved."
    corp_code: str | None = None  # DART corp_code, SEC CIK (EDGAR), or EDINET code (EDINET)
    # Source-native legal name, preserved verbatim — distinct from `name`
    # (a curated display label, which may or may not itself be sourced
    # from the same place; see each EDINET entry's own `notes` for which
    # is which). Empty for DART/EDGAR, which have never needed this
    # distinction. Additive, default-preserving field (Gate 7) — no
    # existing entry or caller needed to change.
    native_name: str = ""


TRACKED_COMPANIES: tuple[TrackedCompany, ...] = (
    TrackedCompany(
        name="Samsung Electronics",
        exchange="KRX",
        krx_code="005930",
        source="OpenDART / DART",
        themes=("memory", "ai-buildout"),
        subthemes=("dram", "hbm", "compute-accelerators"),
        notes="Korea Memory / AI Buildout radar pilot cohort.",
    ),
    TrackedCompany(
        name="SK Hynix",
        exchange="KRX",
        krx_code="000660",
        source="OpenDART / DART",
        themes=("memory", "ai-buildout"),
        subthemes=("dram", "hbm"),
        notes="Korea Memory / AI Buildout radar pilot cohort.",
    ),
    # SEC EDGAR pilot cohort (milestone 8) — one company per theme,
    # approved by the user, bounded to exactly these five. `exchange`
    # here is a display label only (never used for any API call — CIK
    # resolution goes by ticker against SEC's own official mapping file),
    # based on current public listing knowledge, not independently
    # verified against a live source the way corp_code/CIK resolution is.
    TrackedCompany(
        name="NVIDIA",
        exchange="NASDAQ",
        krx_code="NVDA",
        source="SEC EDGAR",
        themes=("ai-buildout",),
        subthemes=("compute-accelerators",),
        notes="SEC EDGAR pilot cohort — AI Buildout.",
    ),
    TrackedCompany(
        name="Micron Technology",
        exchange="NASDAQ",
        krx_code="MU",
        source="SEC EDGAR",
        themes=("memory",),
        subthemes=("dram", "hbm"),
        notes="SEC EDGAR pilot cohort — Memory.",
    ),
    TrackedCompany(
        name="Coherent Corp",
        exchange="NYSE",
        krx_code="COHR",
        source="SEC EDGAR",
        themes=("photonics",),
        subthemes=("optical-components", "interconnect"),
        notes="SEC EDGAR pilot cohort — Photonics.",
    ),
    TrackedCompany(
        name="Rockwell Automation",
        exchange="NYSE",
        krx_code="ROK",
        source="SEC EDGAR",
        themes=("humanoids",),
        subthemes=("industrial-automation",),
        notes="SEC EDGAR pilot cohort — Humanoids / industrial automation.",
    ),
    TrackedCompany(
        name="Rocket Lab",
        exchange="NASDAQ",
        krx_code="RKLB",
        source="SEC EDGAR",
        themes=("space",),
        subthemes=("launch",),
        notes="SEC EDGAR pilot cohort — Space.",
    ),
    # Radar expansion, Phase 1 (registry-only) — three companies added
    # after a controlled, explicitly-approved live cik_resolver.py
    # validation (2026-08-19), not a hardcoded guess. corp_code is left
    # unset here, same as every other EDGAR entry above — the CIK is
    # already resolved into data/cache/edgar_ciks.json and will populate
    # automatically via with_resolved_ciks() the next time EDGAR
    # companies are loaded, exactly like NVIDIA/Micron/Coherent/Rockwell/
    # Rocket Lab already do.
    TrackedCompany(
        name="Advanced Micro Devices",
        exchange="NASDAQ",
        krx_code="AMD",
        source="SEC EDGAR",
        themes=("ai-buildout",),
        subthemes=("compute-accelerators",),
        notes=(
            "SEC EDGAR pilot cohort — AI Buildout. CIK verified via a "
            "controlled cik_resolver.resolve_and_cache() validation, "
            "2026-08-19 (ticker cross-checked against SEC's own "
            "submissions metadata; cached CIK 0000002488)."
        ),
    ),
    TrackedCompany(
        name="SanDisk Corp",
        exchange="NASDAQ",
        krx_code="SNDK",
        source="SEC EDGAR",
        themes=("memory",),
        notes=(
            "SEC EDGAR pilot cohort — Memory. CIK verified via a "
            "controlled cik_resolver.resolve_and_cache() validation, "
            "2026-08-19 (ticker cross-checked against SEC's own "
            "submissions metadata; cached CIK 0002023554)."
        ),
    ),
    TrackedCompany(
        name="Lumentum Holdings Inc.",
        exchange="NASDAQ",
        krx_code="LITE",
        source="SEC EDGAR",
        themes=("photonics",),
        notes=(
            "SEC EDGAR pilot cohort — Photonics. CIK verified via a "
            "controlled cik_resolver.resolve_and_cache() validation, "
            "2026-08-19 (ticker cross-checked against SEC's own "
            "submissions metadata; cached CIK 0001633978)."
        ),
    ),
    # Radar expansion, Phase 1 (registry-only), second batch — four
    # companies added after a controlled, explicitly-approved live
    # cik_resolver.py validation (2026-08-19). corp_code left unset,
    # same as every other EDGAR entry above — resolves automatically via
    # with_resolved_ciks() from the already-cached CIK.
    TrackedCompany(
        name="Intel Corp.",
        exchange="NASDAQ",
        krx_code="INTC",
        source="SEC EDGAR",
        themes=("ai-buildout",),
        subthemes=("compute-accelerators",),
        notes=(
            "SEC EDGAR pilot cohort — AI Buildout. CIK verified via a "
            "controlled cik_resolver.resolve_and_cache() validation, "
            "2026-08-19 (ticker cross-checked against SEC's own "
            "submissions metadata; cached CIK 0000050863)."
        ),
    ),
    TrackedCompany(
        name="Arm Holdings plc",
        exchange="NASDAQ",
        krx_code="ARM",
        source="SEC EDGAR",
        themes=("ai-buildout",),
        subthemes=("compute-accelerators",),
        notes=(
            "SEC EDGAR pilot cohort — AI Buildout. CIK verified via a "
            "controlled cik_resolver.resolve_and_cache() validation, "
            "2026-08-19 (ticker cross-checked against SEC's own "
            "submissions metadata; cached CIK 0001973239). SEC-registered "
            "as \"ARM HOLDINGS PLC /UK\" — a UK-domiciled foreign private "
            "issuer; cross-check passed against its real submissions "
            "metadata regardless."
        ),
    ),
    TrackedCompany(
        name="Applied Optoelectronics, Inc.",
        exchange="NASDAQ",
        krx_code="AAOI",
        source="SEC EDGAR",
        themes=("photonics",),
        notes=(
            "SEC EDGAR pilot cohort — Photonics. CIK verified via a "
            "controlled cik_resolver.resolve_and_cache() validation, "
            "2026-08-19 (ticker cross-checked against SEC's own "
            "submissions metadata; cached CIK 0001158114)."
        ),
    ),
    TrackedCompany(
        name="Corning Inc.",
        exchange="NYSE",
        krx_code="GLW",
        source="SEC EDGAR",
        themes=("photonics",),
        notes=(
            "SEC EDGAR pilot cohort — Photonics. CIK verified via a "
            "controlled cik_resolver.resolve_and_cache() validation, "
            "2026-08-19 (ticker cross-checked against SEC's own "
            "submissions metadata; cached CIK 0000024741)."
        ),
    ),
    # Radar expansion, Phase 1 (registry-only), third batch — four
    # companies added after a controlled, explicitly-approved live
    # cik_resolver.py validation (2026-08-19). corp_code left unset,
    # same as every other EDGAR entry above. Subthemes deliberately left
    # unset for all four (no `subthemes=` argument given, so each
    # defaults to `()`) — a subtheme decision (e.g. "ai-infrastructure",
    # "networking-interconnect") is explicitly deferred, not guessed.
    TrackedCompany(
        name="Nebius Group N.V.",
        exchange="NASDAQ",
        krx_code="NBIS",
        source="SEC EDGAR",
        themes=("ai-buildout",),
        notes=(
            "SEC EDGAR pilot cohort — AI Buildout. CIK verified via a "
            "controlled cik_resolver.resolve_and_cache() validation, "
            "2026-08-19 (ticker cross-checked against SEC's own "
            "submissions metadata; cached CIK 0001513845). SEC-registered "
            "as a Netherlands-domiciled N.V.; cross-check passed against "
            "its real submissions metadata regardless."
        ),
    ),
    TrackedCompany(
        name="Penguin Solutions, Inc.",
        exchange="NASDAQ",
        krx_code="PENG",
        source="SEC EDGAR",
        themes=("ai-buildout",),
        notes=(
            "SEC EDGAR pilot cohort — AI Buildout. CIK verified via a "
            "controlled cik_resolver.resolve_and_cache() validation, "
            "2026-08-19 (ticker cross-checked against SEC's own "
            "submissions metadata; cached CIK 0001616533)."
        ),
    ),
    TrackedCompany(
        name="Marvell Technology, Inc.",
        exchange="NASDAQ",
        krx_code="MRVL",
        source="SEC EDGAR",
        themes=("photonics",),
        notes=(
            "SEC EDGAR pilot cohort — Photonics. CIK verified via a "
            "controlled cik_resolver.resolve_and_cache() validation, "
            "2026-08-19 (ticker cross-checked against SEC's own "
            "submissions metadata; cached CIK 0001835632)."
        ),
    ),
    TrackedCompany(
        name="MaxLinear, Inc.",
        exchange="NASDAQ",
        krx_code="MXL",
        source="SEC EDGAR",
        themes=("photonics",),
        notes=(
            "SEC EDGAR pilot cohort — Photonics. CIK verified via a "
            "controlled cik_resolver.resolve_and_cache() validation, "
            "2026-08-19 (ticker cross-checked against SEC's own "
            "submissions metadata; cached CIK 0001288469)."
        ),
    ),
    # Radar expansion, Phase 1 (registry-only), fourth batch — six
    # companies added after a controlled, explicitly-approved live
    # cik_resolver.py validation (2026-08-19). corp_code left unset,
    # same as every other EDGAR entry above. Subthemes deliberately
    # reuse existing data/seed/themes.json vocabulary where a real match
    # exists (interconnect-switching under photonics; compute-
    # accelerators and power-cooling under ai-buildout) rather than
    # inventing near-duplicate strings — see the theme-model audit this
    # batch was approved from. semiconductor-test is the one genuinely
    # new subtheme, added to themes.json alongside this entry.
    TrackedCompany(
        name="Nokia Corp",
        exchange="NYSE",
        krx_code="NOK",
        source="SEC EDGAR",
        themes=("photonics",),
        subthemes=("interconnect-switching",),
        notes=(
            "SEC EDGAR pilot cohort — Photonics. CIK verified via a "
            "controlled cik_resolver.resolve_and_cache() validation, "
            "2026-08-19 (ticker cross-checked against SEC's own "
            "submissions metadata; cached CIK 0000924613). SEC-registered "
            "as a Finland-domiciled entity; cross-check passed against "
            "its real submissions metadata regardless."
        ),
    ),
    TrackedCompany(
        name="Tower Semiconductor Ltd",
        exchange="NASDAQ",
        krx_code="TSEM",
        source="SEC EDGAR",
        themes=("ai-buildout",),
        subthemes=("compute-accelerators",),
        notes=(
            "SEC EDGAR pilot cohort — AI Buildout. CIK verified via a "
            "controlled cik_resolver.resolve_and_cache() validation, "
            "2026-08-19 (ticker cross-checked against SEC's own "
            "submissions metadata; cached CIK 0000928876). SEC-registered "
            "as an Israel-domiciled entity; cross-check passed against "
            "its real submissions metadata regardless."
        ),
    ),
    TrackedCompany(
        name="Aehr Test Systems",
        exchange="NASDAQ",
        krx_code="AEHR",
        source="SEC EDGAR",
        themes=("ai-buildout",),
        subthemes=("semiconductor-test",),
        notes=(
            "SEC EDGAR pilot cohort — AI Buildout. CIK verified via a "
            "controlled cik_resolver.resolve_and_cache() validation, "
            "2026-08-19 (ticker cross-checked against SEC's own "
            "submissions metadata; cached CIK 0001040470)."
        ),
    ),
    TrackedCompany(
        name="Trio-Tech International",
        exchange="NYSE American",
        krx_code="TRT",
        source="SEC EDGAR",
        themes=("ai-buildout",),
        subthemes=("semiconductor-test",),
        notes=(
            "SEC EDGAR pilot cohort — AI Buildout. CIK verified via a "
            "controlled cik_resolver.resolve_and_cache() validation, "
            "2026-08-19 (ticker cross-checked against SEC's own "
            "submissions metadata; cached CIK 0000732026)."
        ),
    ),
    TrackedCompany(
        name="Navitas Semiconductor Corp",
        exchange="NASDAQ",
        krx_code="NVTS",
        source="SEC EDGAR",
        themes=("ai-buildout",),
        subthemes=("power-cooling",),
        notes=(
            "SEC EDGAR pilot cohort — AI Buildout. CIK verified via a "
            "controlled cik_resolver.resolve_and_cache() validation, "
            "2026-08-19 (ticker cross-checked against SEC's own "
            "submissions metadata; cached CIK 0001821769)."
        ),
    ),
    TrackedCompany(
        name="Bloom Energy Corp",
        exchange="NYSE",
        krx_code="BE",
        source="SEC EDGAR",
        themes=("ai-buildout",),
        subthemes=("power-cooling",),
        notes=(
            "SEC EDGAR pilot cohort — AI Buildout. CIK verified via a "
            "controlled cik_resolver.resolve_and_cache() validation, "
            "2026-08-19 (ticker cross-checked against SEC's own "
            "submissions metadata; cached CIK 0001664703)."
        ),
    ),
    # EDINET (Japan) pilot cohort (Gate 7) — one company per theme,
    # `krx_code`/`corp_code` hardcoded rather than left for runtime
    # resolution because both were already independently live-verified
    # (see module docstring). `name` is a curated English display label;
    # `native_name` is the exact Japanese legal name from the real
    # EDINET code-list artifact (Gate 2, 2026-08-17) — for four of the
    # five, `name` itself is ALSO that same code list's own English
    # filer-name field (real source evidence, not a curated guess); only
    # ispace's `name` is curated, since its code-list English-name field
    # was observed blank (confirmed live, Gate 2/Gate 6) — see its own
    # note below.
    TrackedCompany(
        name="SoftBank Group Corp.",
        native_name="ソフトバンクグループ株式会社",
        exchange="TSE",
        krx_code="99840",
        source="EDINET",
        themes=("ai-buildout",),
        corp_code="E02778",
        notes=(
            "EDINET pilot cohort — AI Buildout. `name` is the EDINET code "
            "list's own English filer name (Gate 2), not curated. "
            "corp_code/krx_code confirmed live twice: Gate 2 code-list "
            "resolution and Gate 6 document-list observation (docID S100YGH5)."
        ),
    ),
    TrackedCompany(
        name="Kioxia Holdings Corporation",
        native_name="キオクシアホールディングス株式会社",
        exchange="TSE",
        krx_code="285A0",
        source="EDINET",
        themes=("memory",),
        corp_code="E35948",
        notes=(
            "EDINET pilot cohort — Memory. `name` is the EDINET code "
            "list's own English filer name (Gate 2), not curated. "
            "corp_code/krx_code confirmed live via Gate 2 code-list resolution."
        ),
    ),
    TrackedCompany(
        name="Furukawa Electric Co., Ltd.",
        native_name="古河電気工業株式会社",
        exchange="TSE",
        krx_code="58010",
        source="EDINET",
        themes=("photonics",),
        corp_code="E01332",
        notes=(
            "EDINET pilot cohort — Photonics. `name` is the EDINET code "
            "list's own English filer name (Gate 2), not curated. "
            "corp_code/krx_code confirmed live via Gate 2 code-list resolution."
        ),
    ),
    TrackedCompany(
        name="FANUC CORPORATION",
        native_name="ファナック株式会社",
        exchange="TSE",
        krx_code="69540",
        source="EDINET",
        themes=("humanoids",),
        corp_code="E01946",
        notes=(
            "EDINET pilot cohort — Humanoids / industrial automation. `name` "
            "is the EDINET code list's own English filer name (Gate 2), not "
            "curated. corp_code/krx_code confirmed live via Gate 2 code-list "
            "resolution."
        ),
    ),
    TrackedCompany(
        name="ispace, inc.",
        native_name="株式会社ｉｓｐａｃｅ",
        exchange="TSE",
        krx_code="93480",
        source="EDINET",
        themes=("space",),
        corp_code="E37584",
        notes=(
            "EDINET pilot cohort — Space. Unlike the other four EDINET "
            "entries, `name` here is a CURATED display label, not sourced "
            "from the EDINET code list — that list's English-name field for "
            "this filer was observed blank (confirmed live, Gate 2/Gate 6); "
            "`native_name` (the Japanese legal name) IS real source "
            "evidence. corp_code/krx_code confirmed live via Gate 2 "
            "code-list resolution."
        ),
    ),
    # Radar expansion — INDI/AIP/CEVA, added after a controlled,
    # explicitly-approved bounded live cik_resolver.resolve_and_cache()
    # gate (2026-08-20; exactly 4 SEC requests: one company_tickers.json
    # bulk lookup + one submissions cross-check per ticker, all three
    # resolved and cross-check-passed). corp_code left unset here, same
    # as every other EDGAR entry above — the CIK is already resolved
    # into data/cache/edgar_ciks.json and populates automatically via
    # with_resolved_ciks() the next time EDGAR companies are loaded.
    #
    # `subthemes` is left unset for all three: none of the theme/layer
    # assignments requested for this batch (automotive-sensing,
    # soc-interconnect, edge-ai-connectivity) matched an existing
    # tracked-company subtheme string closely enough to reuse without
    # misrepresenting the issuer (e.g. the existing "interconnect"
    # subtheme means optical/photonic switching fabric elsewhere in this
    # registry, not Arteris's on-chip Network-on-Chip IP) — flagged as a
    # reported conflict rather than silently reused or invented. The
    # intended subtheme and a supply-chain-layer classification are
    # recorded in each entry's own `notes` below as an explicitly
    # informal, auditable research classification — not a structured
    # registry/Issuer field in this phase (TrackedCompany/Issuer carry no
    # supply-chain-layer field of their own yet for SEED_ISSUERS entries;
    # see design/DECISIONS.md).
    TrackedCompany(
        name="indie Semiconductor, Inc.",
        exchange="NASDAQ",
        krx_code="INDI",
        source="SEC EDGAR",
        themes=("humanoids",),
        notes=(
            "SEC EDGAR pilot cohort — Humanoids. CIK verified via a "
            "controlled cik_resolver.resolve_and_cache() bounded gate, "
            "2026-08-20 (ticker cross-checked against SEC's own "
            "submissions metadata; cached CIK 0001841925). `exchange` is "
            "a display label only, not part of the live-verified gate "
            "(same convention as every other EDGAR entry above). "
            "Intended subtheme (not applied — no accurate existing match, "
            "see module docstring): automotive-sensing. Informal "
            "supply-chain-layer classification (not a structured field): "
            "edge-physical-ai, compute-hardware. Automotive sensing/"
            "perception exposure — radar, vision, in-cabin sensing, and "
            "edge processing; classification may overlap physical AI and "
            "automotive ADAS."
        ),
    ),
    TrackedCompany(
        name="Arteris, Inc.",
        exchange="NASDAQ",
        krx_code="AIP",
        source="SEC EDGAR",
        themes=("ai-buildout",),
        notes=(
            "SEC EDGAR pilot cohort — AI Buildout. CIK verified via a "
            "controlled cik_resolver.resolve_and_cache() bounded gate, "
            "2026-08-20 (ticker cross-checked against SEC's own "
            "submissions metadata; cached CIK 0001667011). `exchange` is "
            "a display label only, not part of the live-verified gate. "
            "Intended subtheme (not applied — no accurate existing match, "
            "see module docstring): soc-interconnect. Informal "
            "supply-chain-layer classification (not a structured field): "
            "compute-hardware, software-infrastructure. Semiconductor "
            "design infrastructure — Network-on-Chip IP, SoC integration, "
            "custom silicon, and AI-chip design complexity."
        ),
    ),
    TrackedCompany(
        name="CEVA INC",
        exchange="NASDAQ",
        krx_code="CEVA",
        source="SEC EDGAR",
        themes=("ai-buildout",),
        notes=(
            "SEC EDGAR pilot cohort — AI Buildout. CIK verified via a "
            "controlled cik_resolver.resolve_and_cache() bounded gate, "
            "2026-08-20 (ticker cross-checked against SEC's own "
            "submissions metadata; cached CIK 0001173489). `exchange` is "
            "a display label only, not part of the live-verified gate. "
            "Intended subtheme (not applied — no accurate existing match, "
            "see module docstring): edge-ai-connectivity. Informal "
            "supply-chain-layer classification (not a structured field): "
            "edge-physical-ai, software-infrastructure. Edge AI, "
            "connectivity IP, DSP, sensor fusion, and smart-edge "
            "semiconductor design."
        ),
    ),
    # Core Issuer Expansion batch (weekend beta, 2026-09-04) — 30 net-new
    # active issuers (14 SEC EDGAR, 8 OpenDART / DART, 8 EDINET), added
    # after a bounded, read-only, live official-source identifier
    # verification pass (see design/DECISIONS.md's own Gate entry and
    # verified_core_expansion_candidates.md for the full evidence record
    # of every ID below). Simmtech was deliberately excluded from this
    # batch — a real parent/holding-company/subsidiary split was found
    # across three distinct DART entities and needs a separate decision,
    # not a guess.
    #
    # EDGAR (14): corp_code left unset for every entry here, same
    # convention as every other EDGAR entry above. Unlike the INDI/AIP/
    # CEVA batch, this verification pass ran against a scratchpad-only
    # cache directory (per this batch's own explicit no-resolver-call
    # constraint), so data/cache/edgar_ciks.json does NOT yet contain
    # these CIKs — a separate, later, explicitly-approved cik_resolver.
    # resolve_and_cache() run against the real cache is still required
    # before the worker can scan these companies. The CIK recorded in
    # each entry's own notes below is this session's own live-verified
    # value (SEC company_tickers.json + submissions cross-check,
    # 2026-09-04), kept for audit traceability only — never read by any
    # pipeline from `notes`.
    TrackedCompany(
        name="Cisco Systems, Inc.", exchange="NASDAQ", krx_code="CSCO", source="SEC EDGAR",
        themes=("ai-buildout",), subthemes=("interconnect-switching",),
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — AI Buildout. CIK "
            "verified live (SEC company_tickers.json + submissions "
            "cross-check): 0000858877. `interconnect-switching` reused "
            "from the existing Nokia entry's own subtheme — same product "
            "category (networking/switching hardware), not a stretch. "
            "Already present as issuer_registry.DISCOVERY_STUBS's "
            "'stub:CSCO' (Daily-News-verified, no CIK) — that stub entry "
            "is now redundant but was left untouched per this batch's "
            "strict scope; flagged for a future cleanup decision."
        ),
    ),
    TrackedCompany(
        name="Arista Networks, Inc.", exchange="NASDAQ", krx_code="ANET", source="SEC EDGAR",
        themes=("ai-buildout",), subthemes=("interconnect-switching",),
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — AI Buildout. CIK "
            "verified live: 0001596532. `interconnect-switching` reused "
            "for the same reason as the Cisco entry above — AI-datacenter "
            "switching hardware. Already present as DISCOVERY_STUBS's "
            "'stub:ANET' (Daily-News-verified, no CIK) — now redundant, "
            "left untouched per this batch's strict scope."
        ),
    ),
    TrackedCompany(
        name="Quanta Services, Inc.", exchange="NYSE", krx_code="PWR", source="SEC EDGAR",
        themes=("ai-buildout",), subthemes=("power-cooling",),
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — AI Buildout. CIK "
            "verified live: 0001050915. `power-cooling` reused from the "
            "existing Navitas/Bloom Energy entries — grid/power "
            "infrastructure for AI datacenter buildout. Already present "
            "as DISCOVERY_STUBS's 'stub:PWR' (Daily-News-verified, no "
            "CIK) — now redundant, left untouched per this batch's "
            "strict scope."
        ),
    ),
    TrackedCompany(
        name="nVent Electric plc", exchange="NYSE", krx_code="NVT", source="SEC EDGAR",
        themes=("ai-buildout",), subthemes=("power-cooling",),
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — AI Buildout. CIK "
            "verified live: 0001720635. `power-cooling` matches the "
            "subtheme already recorded on this same company's own "
            "DISCOVERY_STUBS 'stub:NVT' entry (Daily-News-verified, no "
            "CIK) — that stub is now redundant, left untouched per this "
            "batch's strict scope. Informal supply-chain-layer "
            "classification (not a structured field): power-infrastructure, "
            "thermal-management."
        ),
    ),
    TrackedCompany(
        name="Applied Materials, Inc.", exchange="NASDAQ", krx_code="AMAT", source="SEC EDGAR",
        themes=("ai-buildout",),
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — AI Buildout. CIK "
            "verified live: 0000006951. No existing subtheme accurately "
            "represents general semiconductor deposition/etch equipment "
            "(the existing `semiconductor-test` subtheme is specifically "
            "about post-fabrication device test, a different step) — "
            "left unset rather than misapplied. Informal supply-chain-"
            "layer classification (not a structured field): "
            "semiconductor-equipment."
        ),
    ),
    TrackedCompany(
        name="Lam Research Corp", exchange="NASDAQ", krx_code="LRCX", source="SEC EDGAR",
        themes=("ai-buildout",),
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — AI Buildout. CIK "
            "verified live: 0000707549. Same subtheme reasoning as the "
            "Applied Materials entry above — left unset. Informal "
            "supply-chain-layer classification (not a structured field): "
            "semiconductor-equipment."
        ),
    ),
    TrackedCompany(
        name="KLA Corp", exchange="NASDAQ", krx_code="KLAC", source="SEC EDGAR",
        themes=("ai-buildout",),
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — AI Buildout. CIK "
            "verified live: 0000319201. Process-control/inspection "
            "(metrology) equipment — deliberately NOT tagged "
            "`semiconductor-test` despite the superficial similarity; "
            "that existing subtheme (Aehr/Trio-Tech) specifically means "
            "post-fabrication device test, not wafer metrology, and "
            "reusing it here would misrepresent the company. Left unset. "
            "Informal supply-chain-layer classification (not a "
            "structured field): semiconductor-equipment."
        ),
    ),
    TrackedCompany(
        name="Entegris, Inc.", exchange="NASDAQ", krx_code="ENTG", source="SEC EDGAR",
        themes=("ai-buildout",),
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — AI Buildout. CIK "
            "verified live: 0001101302. Specialty materials/filtration "
            "for chip manufacturing — no existing subtheme fits; left "
            "unset. Informal supply-chain-layer classification (not a "
            "structured field): semiconductor-materials."
        ),
    ),
    TrackedCompany(
        name="Amkor Technology, Inc.", exchange="NASDAQ", krx_code="AMKR", source="SEC EDGAR",
        themes=("ai-buildout",),
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — AI Buildout. CIK "
            "verified live: 0001047127. Largest US-listed OSAT/advanced-"
            "packaging provider — no existing subtheme fits; left unset. "
            "Informal supply-chain-layer classification (not a "
            "structured field): advanced-packaging."
        ),
    ),
    TrackedCompany(
        name="MKS Inc", exchange="NASDAQ", krx_code="MKSI", source="SEC EDGAR",
        themes=("ai-buildout",),
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — AI Buildout. "
            "Legal name confirmed live as 'MKS INC' (formerly MKS "
            "Instruments) via SEC's own company_tickers.json entry. CIK "
            "verified live: 0001049502. Process-control instrumentation/"
            "specialty gases — no existing subtheme fits; left unset. "
            "Informal supply-chain-layer classification (not a "
            "structured field): semiconductor-equipment, "
            "semiconductor-materials."
        ),
    ),
    TrackedCompany(
        name="Vertiv Holdings Co", exchange="NYSE", krx_code="VRT", source="SEC EDGAR",
        themes=("ai-buildout",), subthemes=("power-cooling",),
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — AI Buildout. CIK "
            "verified live: 0001674101. Datacenter power/cooling systems "
            "integrator — `power-cooling` reused, same as Navitas/Bloom "
            "Energy/Quanta/nVent above. Informal supply-chain-layer "
            "classification (not a structured field): "
            "power-infrastructure, thermal-management."
        ),
    ),
    TrackedCompany(
        name="Teradyne, Inc", exchange="NASDAQ", krx_code="TER", source="SEC EDGAR",
        themes=("ai-buildout", "humanoids"), subthemes=("semiconductor-test", "industrial-automation"),
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — AI Buildout and "
            "Humanoids (two themes, mirroring Samsung's own existing "
            "combined-theme entry). CIK verified live: 0000097210. "
            "`semiconductor-test` is a direct, accurate fit (Teradyne is "
            "a major ATE/semiconductor-test-equipment maker); "
            "`industrial-automation` reused from Rockwell Automation's "
            "own entry — Teradyne owns Universal Robots and MiR "
            "(collaborative/mobile robots). Informal supply-chain-layer "
            "classification (not a structured field): "
            "semiconductor-equipment, edge-physical-ai."
        ),
    ),
    TrackedCompany(
        name="Astera Labs, Inc.", exchange="NASDAQ", krx_code="ALAB", source="SEC EDGAR",
        themes=("ai-buildout",),
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — AI Buildout. CIK "
            "verified live: 0001736297. PCIe/CXL connectivity "
            "semiconductors for AI-datacenter fabric — deliberately NOT "
            "tagged `interconnect` despite the superficial match; that "
            "existing subtheme (Coherent Corp) specifically means "
            "optical/photonic interconnect fabric elsewhere in this "
            "registry (same caution the Arteris entry above already "
            "documents for its own on-chip NoC IP), and Astera Labs is "
            "electrical/protocol-layer connectivity, a different "
            "technology. Left unset. Informal supply-chain-layer "
            "classification (not a structured field): interconnect."
        ),
    ),
    TrackedCompany(
        name="Redwire Corp", exchange="NYSE", krx_code="RDW", source="SEC EDGAR",
        themes=("space",),
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — Space. CIK "
            "verified live: 0001819810. Space-hardware manufacturer "
            "(distinct from Rocket Lab's own launch-provider focus) — "
            "Rocket Lab's only subtheme, `launch`, does not fit; left "
            "unset. No dedicated ontology supply-chain layer exists for "
            "space manufacturing."
        ),
    ),
    # OpenDART / DART, Korea (8): corp_code left unset, same convention
    # as Samsung/SK Hynix above — same scratchpad-only-cache caveat as
    # the EDGAR batch; a separate, later corp_code_resolver.
    # resolve_and_cache() run against the real cache is still required.
    TrackedCompany(
        name="LG Innotek Co., Ltd.", exchange="KRX", krx_code="011070", source="OpenDART / DART",
        themes=("ai-buildout",),
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — AI Buildout. "
            "corp_code verified live (DART corpCode.xml bulk fetch, "
            "single clean match): 00105961. Camera modules/optics and IC "
            "substrates — theme assignment is a judgment call (this "
            "company's own pre-existing issuer_registry.DISCOVERY_STUBS "
            "'011070.KS' entry explicitly left `theme=None` for the same "
            "reason, calling the fit unclear); AI Buildout chosen since "
            "substrates directly feed AI-chip packaging. That stub entry "
            "is now redundant but was left untouched per this batch's "
            "strict scope; flagged for a future cleanup decision. No "
            "existing subtheme fits; left unset. Informal supply-chain-"
            "layer classification (not a structured field): "
            "advanced-packaging."
        ),
    ),
    TrackedCompany(
        name="Hanwha Aerospace Co., Ltd.", exchange="KRX", krx_code="012450", source="OpenDART / DART",
        themes=("space",),
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — Space. corp_code "
            "verified live: 00126566. Fills Korea's zero-space gap. No "
            "existing subtheme fits (`launch` is Rocket Lab-specific to "
            "launch vehicles; Hanwha Aerospace is broader — space, "
            "defense, gas turbines); left unset."
        ),
    ),
    TrackedCompany(
        name="Korea Aerospace Industries, Ltd.", exchange="KRX", krx_code="047810", source="OpenDART / DART",
        themes=("space",),
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — Space. corp_code "
            "verified live: 00309503. DART's bulk file returned two raw "
            "matches for 'Korea Aerospace' — 'Korea Aerospace University "
            "Industry-Cooperation Foundation' (a university research "
            "foundation, no stock_code, not a company) was excluded; "
            "this entry is the genuine, separately-listed manufacturer "
            "(stock_code 047810). Second, independent Korean space name "
            "alongside Hanwha Aerospace. No existing subtheme fits; left "
            "unset."
        ),
    ),
    TrackedCompany(
        name="Doosan Robotics Inc.", exchange="KRX", krx_code="454910", source="OpenDART / DART",
        themes=("humanoids",), subthemes=("industrial-automation",),
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — Humanoids. "
            "corp_code verified live: 01105153. Fills Korea's zero-"
            "humanoids gap. `industrial-automation` reused directly from "
            "Rockwell Automation's own entry — collaborative robotics is "
            "the same product category. Recently listed (high stock-code "
            "number consistent with a 2023 IPO) — verify current listing "
            "status before the first live scan."
        ),
    ),
    TrackedCompany(
        name="Wonik IPS Co., Ltd.", exchange="KRX", krx_code="240810", source="OpenDART / DART",
        themes=("ai-buildout",),
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — AI Buildout. "
            "corp_code verified live: 01135941. Fills Korea's zero-"
            "semiconductor-equipment gap (deposition/etch equipment). No "
            "existing subtheme fits; left unset. Informal supply-chain-"
            "layer classification (not a structured field): "
            "semiconductor-equipment."
        ),
    ),
    TrackedCompany(
        name="SFA Engineering Corporation", exchange="KRX", krx_code="056190", source="OpenDART / DART",
        themes=("ai-buildout",),
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — AI Buildout. "
            "corp_code verified live: 00358271. Display/secondary-"
            "battery/semiconductor process equipment group. Confirmed a "
            "genuinely separate legal entity from 'SFA Semicon Co., Ltd' "
            "below (different corp_code and stock_code; same corporate-"
            "group name prefix only) — not a duplicate. No existing "
            "subtheme fits; left unset. Informal supply-chain-layer "
            "classification (not a structured field): "
            "semiconductor-equipment."
        ),
    ),
    TrackedCompany(
        name="SFA Semicon Co., Ltd", exchange="KRX", krx_code="036540", source="OpenDART / DART",
        themes=("ai-buildout",),
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — AI Buildout. "
            "corp_code verified live: 00301246. Semiconductor packaging/"
            "test (OSAT) — see the SFA Engineering entry above for the "
            "confirmed-not-a-duplicate note. `semiconductor-test` was "
            "deliberately NOT applied despite this company doing test "
            "services alongside packaging — kept consistent with the "
            "conservative choice made for KLA above, to avoid a mixed "
            "judgment call across this batch; left unset. Informal "
            "supply-chain-layer classification (not a structured field): "
            "advanced-packaging."
        ),
    ),
    TrackedCompany(
        name="Hana Micron Inc.", exchange="KRX", krx_code="067310", source="OpenDART / DART",
        themes=("ai-buildout",),
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — AI Buildout. "
            "corp_code verified live: 00445054. The real listed entity's "
            "registered English name has no space ('HanaMicronInc.') — "
            "an initial space-separated name search missed it and "
            "returned only two unrelated, unlisted shell entities ('Hana "
            "micron 2nd Co.,Ltd', 'Hanamicron 3rd Co.,Ltd', both "
            "stock_code empty), correctly excluded; a corrected search "
            "found the real, listed parent. No existing subtheme fits; "
            "left unset. Informal supply-chain-layer classification (not "
            "a structured field): advanced-packaging."
        ),
    ),
    # EDINET, Japan (8): corp_code hardcoded directly, matching the
    # existing five-entry EDINET exception to the "never hardcode" rule
    # (see module docstring) — each value was independently live-verified
    # this session against the real, official EDINET code-list artifact
    # (bulk fetch, 2026-09-04; 11,392 real records). krx_code holds
    # EDINET's own 5-character source-native securities code, same
    # convention as the five existing EDINET entries.
    TrackedCompany(
        name="Tokyo Electron Limited", exchange="TSE", krx_code="80350", source="EDINET",
        themes=("ai-buildout",), corp_code="E02652",
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — AI Buildout. "
            "EDINET code and securities code verified live against the "
            "official EDINET code-list CSV (single clean match after "
            "excluding a separately-listed subsidiary, 'Tokyo Electron "
            "Device Limited', EDINET code E02955, securities code 27600 "
            "— a distinct legal entity, not this company). Japan's "
            "largest semiconductor equipment maker. No existing subtheme "
            "fits; left unset. Informal supply-chain-layer "
            "classification (not a structured field): "
            "semiconductor-equipment."
        ),
    ),
    TrackedCompany(
        name="Advantest Corporation", exchange="TSE", krx_code="68570", source="EDINET",
        themes=("ai-buildout",), subthemes=("semiconductor-test",), corp_code="E01950",
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — AI Buildout. "
            "EDINET code/securities code verified live, single clean "
            "match. Major ATE/semiconductor-test-equipment maker — "
            "`semiconductor-test` is a direct, accurate fit."
        ),
    ),
    TrackedCompany(
        name="Disco Corporation", exchange="TSE", krx_code="61460", source="EDINET",
        themes=("ai-buildout",), corp_code="E01506",
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — AI Buildout. "
            "EDINET code/securities code verified live, single clean "
            "match. Dicing/grinding equipment critical to advanced "
            "packaging. No existing subtheme fits; left unset. Informal "
            "supply-chain-layer classification (not a structured field): "
            "semiconductor-equipment, advanced-packaging."
        ),
    ),
    TrackedCompany(
        name="Shin-Etsu Chemical Co., Ltd.", exchange="TSE", krx_code="40630", source="EDINET",
        themes=("ai-buildout", "memory"), corp_code="E00776",
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — AI Buildout and "
            "Memory. EDINET code/securities code verified live, single "
            "clean match. Major silicon-wafer/specialty-materials maker "
            "— fills Japan's zero-materials-layer gap. No existing "
            "subtheme fits; left unset. Informal supply-chain-layer "
            "classification (not a structured field): "
            "semiconductor-materials."
        ),
    ),
    TrackedCompany(
        name="SUMCO Corporation", exchange="TSE", krx_code="34360", source="EDINET",
        themes=("ai-buildout", "memory"), corp_code="E02103",
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — AI Buildout and "
            "Memory. EDINET code/securities code verified live, single "
            "clean match. Second Japanese silicon-wafer name alongside "
            "Shin-Etsu Chemical — depth, not just breadth. No existing "
            "subtheme fits; left unset. Informal supply-chain-layer "
            "classification (not a structured field): "
            "semiconductor-materials."
        ),
    ),
    TrackedCompany(
        name="Ibiden Co., Ltd.", exchange="TSE", krx_code="40620", source="EDINET",
        themes=("ai-buildout",), corp_code="E00775",
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — AI Buildout. "
            "EDINET code/securities code verified live, single clean "
            "match. IC-substrate maker — fills Japan's zero-packaging-"
            "layer gap. No existing subtheme fits; left unset. Informal "
            "supply-chain-layer classification (not a structured field): "
            "advanced-packaging."
        ),
    ),
    TrackedCompany(
        name="Mitsubishi Electric Corporation", exchange="TSE", krx_code="65030", source="EDINET",
        themes=("humanoids",), subthemes=("industrial-automation",), corp_code="E01739",
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — Humanoids. "
            "EDINET code/securities code verified live, single clean "
            "match. `industrial-automation` reused directly from "
            "Rockwell Automation/Doosan Robotics — factory automation/"
            "robotics is the same product category. Deepens Japan's "
            "previously single-issuer (FANUC) humanoids coverage."
        ),
    ),
    TrackedCompany(
        name="Renesas Electronics Corporation", exchange="TSE", krx_code="67230", source="EDINET",
        themes=("ai-buildout",), corp_code="E02081",
        notes=(
            "Core Issuer Expansion batch (2026-09-04) — AI Buildout. "
            "EDINET code/securities code verified live, single clean "
            "match. Deliberately NOT tagged `compute-accelerators` "
            "despite being a chipmaker — that existing subtheme (NVIDIA/"
            "AMD/Samsung/SK Hynix/Intel/Arm/Tower Semiconductor) "
            "specifically means AI/GPU-style accelerator silicon "
            "elsewhere in this registry; Renesas makes general-purpose "
            "embedded/automotive/industrial MCUs, a different product "
            "category, and reusing the tag would misrepresent it. Left "
            "unset. Informal supply-chain-layer classification (not a "
            "structured field): compute-hardware. Deepens Japan's "
            "previously single-issuer (SoftBank, a holding company) "
            "AI Buildout coverage with an actual chipmaker."
        ),
    ),
)


def get_tracked_companies(active_only: bool = True) -> tuple[TrackedCompany, ...]:
    if not active_only:
        return TRACKED_COMPANIES
    return tuple(c for c in TRACKED_COMPANIES if c.active)


def get_tracked_companies_for_source(source: str, active_only: bool = True) -> tuple[TrackedCompany, ...]:
    return tuple(c for c in get_tracked_companies(active_only) if c.source == source)


def with_resolved_corp_codes(
    companies: tuple[TrackedCompany, ...], resolved: dict[str, str],
) -> tuple[TrackedCompany, ...]:
    """Returns a new tuple with corp_code filled in from a
    {krx_code: corp_code} mapping (typically loaded from the on-disk
    resolver cache) — frozen dataclasses can't be mutated in place, so
    unresolved entries pass through unchanged rather than erroring."""
    return tuple(
        replace(c, corp_code=resolved[c.krx_code]) if c.krx_code in resolved else c
        for c in companies
    )


def with_resolved_ciks(
    companies: tuple[TrackedCompany, ...], resolved: dict[str, str],
) -> tuple[TrackedCompany, ...]:
    """SEC EDGAR equivalent of with_resolved_corp_codes — fills in
    `corp_code` (holding a CIK for EDGAR entries, see module docstring)
    from a {ticker: cik} mapping. Same reused-field-slot approach, same
    pass-through-unresolved behavior."""
    return tuple(
        replace(c, corp_code=resolved[c.krx_code]) if c.krx_code in resolved else c
        for c in companies
    )
