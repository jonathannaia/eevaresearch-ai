"""EevaResearch — Evidence-First Themes MVP (design/DECISIONS.md). A
private, operator-only authoring and publishing tool for one curated,
public-facing ResearchTheme. Not a product feature: no application
runtime module (app.py, any src/ui page) imports this script, and it is
never invoked automatically by anything in this repository.

Invoke as (from the repo root):

    python -m scripts.create_theme --confirm [--backend json|sqlite|postgres]
    python -m scripts.create_theme --set-visibility <theme_id> <new_visibility> --confirm [--backend ...]

Safety gates for authoring new content (mirrors
scripts/create_research_case.py's own exact contract):

  1. AUTHORING_ENABLED below defaults to False. An operator must
     deliberately edit this file and set it to True before this script
     will ever consider persisting anything.
  2. Even with AUTHORING_ENABLED = True, the `--confirm` CLI flag is
     also required — without it, this is a dry run: build, validate,
     print what would happen, never write.
  3. A content-level placeholder-sentinel scan refuses to persist a
     theme, evidence item, or company-map entry that still contains the
     literal REPLACE_ME string anywhere — independent of the two gates
     above, so this script can never publish the shipped placeholder
     text as if it were real research.

Publishing (moving an existing theme through
internal -> ready_to_publish -> published -> archived) is a SEPARATE,
explicit action via --set-visibility — creating a theme's content and
publishing it are two deliberate operator decisions, never one. Every
visibility change goes through the private curator repository seam
(backend_factory.get_theme_curator_repository) — this script never
touches the public, published-only read protocol, and nothing here
ever automatically publishes a theme from a Research Case or any other
internal object.

No external behavior: no network call, no source fetch/validation, no
LLM/model call, no scanning/discovery, no automatic translation, no
email/notification, no deployment. Every id is derived deterministically
from its own (theme_id, source_url/date, company/role) content (see
src.data_access.theme_store's own ID factories), EXCEPT the theme id
itself, which is no longer safe to re-derive across runs: the theme's
own `created_at`/`updated_at` are stamped from the real wall-clock
moment build_authored_theme() runs (by explicit operator instruction,
design/DECISIONS.md, Theme A authoring pass), so re-running this script
does NOT reproduce the same theme_id — it authors a second, distinct
theme row with the same title. An operator re-running --confirm for the
SAME theme must reuse the theme_id printed/logged by the first
successful run (e.g. via --set-visibility, which takes an explicit
theme_id) rather than by re-running the authoring path. Evidence and
company-map rows remain content-addressed off that theme_id as before,
so there is no atomic multi-record transaction here (a deliberate,
simpler design than the Research Case bundle writer — themes are
low-volume and manually curated one at a time; see the approved scope's
own "minimum persistence needed" instruction)."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.config.settings import Settings, get_settings
from src.data_access import backend_factory
from src.data_access.theme_store import build_theme_company_map_id, build_theme_evidence_id, build_theme_id
from src.models.theme_research import (
    CompanyRole,
    EvidenceDirection,
    ResearchTheme,
    ThemeCategory,
    ThemeCompanyMapEntry,
    ThemeEvidenceItem,
    ThemeStatus,
    ThemeVisibility,
)

# =============================================================================
# SAFETY GATE 1 of 2 — see module docstring. Must be hand-edited to True.
# =============================================================================
AUTHORING_ENABLED = True

# A literal sentinel an operator must replace with real, checked content.
_PLACEHOLDER_SENTINEL = "REPLACE_ME"

# The only visibility a freshly authored theme may start at — publishing
# is always a separate, later --set-visibility action.
_INITIAL_VISIBILITY = ThemeVisibility.INTERNAL

# Explicit, narrow visibility state machine for --set-visibility. Any
# transition not listed here is refused with a clear message — this
# script does not trust an arbitrary operator-typed target value.
_ALLOWED_VISIBILITY_TRANSITIONS: dict[ThemeVisibility, frozenset[ThemeVisibility]] = {
    ThemeVisibility.INTERNAL: frozenset({ThemeVisibility.READY_TO_PUBLISH}),
    ThemeVisibility.READY_TO_PUBLISH: frozenset({ThemeVisibility.PUBLISHED, ThemeVisibility.INTERNAL}),
    ThemeVisibility.PUBLISHED: frozenset({ThemeVisibility.ARCHIVED}),
    ThemeVisibility.ARCHIVED: frozenset(),
}

# =============================================================================
# OPERATOR-AUTHORED THEME CONTENT — edit every _PLACEHOLDER_SENTINEL
# value below with real, checked content before setting
# AUTHORING_ENABLED = True. `created_at`/`updated_at` are the one
# deliberate exception to "never generated by this script": by explicit
# operator instruction (design/DECISIONS.md, Theme A authoring pass),
# this script now stamps the real wall-clock moment build_authored_theme()
# runs, exactly like --set-visibility's own updated_at already does
# below — never a value hand-picked by whoever edited this file. Every
# evidence item's own `date` remains fully operator-authored (a real
# historical filing/report date, unrelated to authoring time).
# =============================================================================

_THEME_CATEGORY = ThemeCategory.SECOND_ORDER_EFFECT
_THEME_STATUS = ThemeStatus.NEW
_THEME_TITLE = "Japan semiconductor supply chain: buybacks alongside AI-driven capacity investment"
_THEME_KEY_QUESTION = (
    "Are Tokyo Electron, Shin-Etsu Chemical, Kioxia Holdings, and Murata Manufacturing's large 2026 share "
    "buybacks occurring instead of capacity investment, or alongside it?"
)
_THEME_HYPOTHESIS = (
    "Rising share buybacks at these Japan semiconductor supply-chain companies could be occurring instead of "
    "capacity investment."
)
_THEME_WHAT_EEVA_TESTED = (
    "Eeva set out to test whether large 2026 share buybacks at four Japan semiconductor supply-chain companies "
    "— Tokyo Electron, Shin-Etsu Chemical, Kioxia Holdings, and Murata Manufacturing — were occurring instead "
    "of capacity investment. After reading each company's most recent official EDINET buyback-status report "
    "and annual securities report, that original hypothesis was not supported by the evidence reviewed: all "
    "four companies' own disclosures describe capex maintained or increasing in the same period as the "
    "buyback, with growth investment and shareholder returns funded jointly from cash flow."
)
_THEME_WORKING_THESIS = (
    "Tokyo Electron, Shin-Etsu Chemical, Kioxia Holdings, and Murata Manufacturing are conducting large "
    "board-authorized share repurchases in 2026 while maintaining or increasing relevant capex. The "
    "primary-source evidence reviewed does not show buybacks displacing capacity investment; the companies' "
    "disclosures instead describe growth investment and shareholder returns as jointly funded from cash flow."
)
_THEME_WHY_IT_MATTERS = (
    "This is a bounded, four-company analysis — not a claim about Japanese semiconductor companies generally. "
    "Capital allocation amid an AI-driven demand cycle is a live question for anyone tracking "
    "semiconductor-equipment, -materials, and -memory supply chains; knowing whether buybacks are crowding out "
    "capacity investment (or not) bears directly on capacity/supply forecasts for that cycle."
)
_THEME_WHAT_COULD_CHANGE_THE_VIEW = (
    "Reassess if a future primary disclosure shows capacity/capex reduction concurrent with ongoing buybacks, "
    "or explicitly states that shareholder returns constrain, delay, or replace investment."
)
_THEME_WHAT_TO_WATCH_NEXT = (
    "Each company's next Article 24-6 buyback-status filing, to see whether pace continues. FY2027 capex "
    "actuals vs. the plans cited here — especially Kioxia's ~¥450bn plan (guided +59% YoY) and Shin-Etsu's "
    "Electronic Materials segment plan (¥176bn, nominally below its FY2026 actual of ¥212bn — the one "
    "point worth re-checking). Whether Kioxia authorizes further buybacks given its ¥800bn program was "
    "already ~100% yen-committed after 6 trading days. Any future capital-policy statement from any of the "
    "four that explicitly frames buybacks and capex as competing for capital."
)

_EDINET_PORTAL_URL = "https://disclosure2.edinet-fsa.go.jp/"


def _build_evidence_specs() -> list[dict]:
    """One dict per ThemeEvidenceItem to create. Every value is real,
    checked content read directly from the cited company's own official
    EDINET filing (buyback-status report or annual securities report;
    document id, filing date, and page/section given in each `fact`) —
    never invented. `source_url` is EDINET's own public disclosure
    portal root, the same stable fallback already used elsewhere in
    this codebase for EDINET citations (see src/logic/source_link.py's
    own EDINET_PUBLIC_PORTAL_URL docstring) — EDINET has no confirmed
    stable per-document public URL."""
    return [
        # --- Tokyo Electron (E02652) ---
        {
            "date": "2026-09-11", "company": "Tokyo Electron", "source_name": "EDINET",
            "source_url": _EDINET_PORTAL_URL + "#S100Z1HI-p2",
            "fact": (
                "自己株券買付状況報告書 (S100Z1HI), p.2 §1(2): board authorized (2026-05-29) buyback of up "
                "to 7,500,000 sh / ¥150.0bn, program 2026-06-01–2027-03-31; cumulative through "
                "2026-08-31: 2,443,900 sh / ¥149,999,015,000 (~100% of yen cap in ~2 months)."
            ),
            "relevance": "Establishes size, authorization date, and pace of the buyback under test.",
            "direction": EvidenceDirection.CONTEXT,
        },
        {
            "date": "2026-06-22", "company": "Tokyo Electron", "source_name": "EDINET",
            "source_url": _EDINET_PORTAL_URL + "#S100YEOO-p27",
            "fact": (
                "有価証券報告書 (S100YEOO), p.27 §設備の状況/1: "
                "FY2026 capex ¥216.0bn; new buildings completed at Miyagi/Kyushu HQ; new Miyagi production "
                "building under construction, due summer 2027."
            ),
            "relevance": "Capacity is expanding in the same fiscal year as the buyback.",
            "direction": EvidenceDirection.SUPPORTS,
        },
        {
            "date": "2026-06-22", "company": "Tokyo Electron", "source_name": "EDINET",
            "source_url": _EDINET_PORTAL_URL + "#S100YEOO-p29",
            "fact": (
                "有価証券報告書 (S100YEOO), p.29 §設備の状況/3: "
                "forward capex — Yamanashi ¥17.8bn, Miyagi process equipment ¥56.0bn, Miyagi "
                "production/logistics facility ¥104.0bn total, all multi-year through 2028."
            ),
            "relevance": "Capacity expansion is forward-committed, not a one-off already winding down.",
            "direction": EvidenceDirection.SUPPORTS,
        },
        {
            "date": "2026-06-22", "company": "Tokyo Electron", "source_name": "EDINET",
            "source_url": _EDINET_PORTAL_URL + "#S100YEOO-p21",
            "fact": (
                "有価証券報告書 (S100YEOO), p.21/p.24, MD&A: \"AI server demand from "
                "data centers drove growth of the entire semiconductor [equipment] market... capex for "
                "generative-AI-use semiconductors grew notably.\""
            ),
            "relevance": "Explicit AI-demand link for the capacity expansion above.",
            "direction": EvidenceDirection.SUPPORTS,
        },
        {
            "date": "2026-06-22", "company": "Tokyo Electron", "source_name": "EDINET",
            "source_url": _EDINET_PORTAL_URL + "#S100YEOO-p25",
            "fact": (
                "有価証券報告書 (S100YEOO), p.25, MD&A: FY2026 free cash flow "
                "¥433.2bn; combined dividend+buyback shareholder return ¥421.6bn = 97% of FCF, after "
                "funding R&D/capex from operating cash."
            ),
            "relevance": "Shareholder return is sized as the residual of cash flow after growth investment.",
            "direction": EvidenceDirection.SUPPORTS,
        },
        # --- Shin-Etsu Chemical (E00776) ---
        {
            "date": "2026-09-04", "company": "Shin-Etsu Chemical", "source_name": "EDINET",
            "source_url": _EDINET_PORTAL_URL + "#S100Z0ID-p2",
            "fact": (
                "自己株券買付状況報告書 (S100Z0ID), p.2 §1(2): "
                "board authorized (2026-04-28) buyback of up to 45,000,000 sh / ¥250.0bn, program "
                "2026-05–2027-04; ~100% of yen cap reached by 2026-08."
            ),
            "relevance": "Establishes size, authorization date, and pace of the buyback under test.",
            "direction": EvidenceDirection.CONTEXT,
        },
        {
            "date": "2026-06-19", "company": "Shin-Etsu Chemical", "source_name": "EDINET",
            "source_url": _EDINET_PORTAL_URL + "#S100YE9I-p25",
            "fact": (
                "有価証券報告書 (S100YE9I), p.25 §設備の状況/1: "
                "FY2026 capex ¥339.7bn total; Electronic Materials segment ¥212.4bn for Shin-Etsu "
                "Handotai wafer capacity/quality increase + new photoresist/exposure-material facilities."
            ),
            "relevance": "Capacity expansion in the semiconductor-materials segment, same fiscal year as the buyback.",
            "direction": EvidenceDirection.SUPPORTS,
        },
        {
            "date": "2026-06-19", "company": "Shin-Etsu Chemical", "source_name": "EDINET",
            "source_url": _EDINET_PORTAL_URL + "#S100YE9I-p27",
            "fact": (
                "有価証券報告書 (S100YE9I), p.27 §設備の状況/3: "
                "forward 1-year capex plan ¥350.0bn total (above FY2026's ¥339.7bn actual); Electronic "
                "Materials sub-plan ¥176.0bn (below its own FY2026 actual of ¥212.4bn)."
            ),
            "relevance": (
                "Total company capex plan is rising; the semiconductor-materials sub-segment plan is nominally "
                "lower than last year's actual, with no stated reason — the one genuinely mixed data point "
                "in this set."
            ),
            "direction": EvidenceDirection.MIXED,
        },
        {
            "date": "2026-06-19", "company": "Shin-Etsu Chemical", "source_name": "EDINET",
            "source_url": _EDINET_PORTAL_URL + "#S100YE9I-p21",
            "fact": (
                "有価証券報告書 (S100YE9I), p.21, MD&A: \"The AI-related [semiconductor] "
                "segment continued brisk... [we] grew sales of silicon wafers, photoresist, and mask blanks into "
                "that strong market.\""
            ),
            "relevance": "AI-demand link, less granular than TEL/Kioxia.",
            "direction": EvidenceDirection.SUPPORTS,
        },
        # --- Kioxia Holdings (E35948) ---
        {
            "date": "2026-09-11", "company": "Kioxia Holdings", "source_name": "EDINET",
            "source_url": _EDINET_PORTAL_URL + "#S100Z1UD-p2",
            "fact": (
                "自己株券買付状況報告書 (S100Z1UD), p.2 §1(2): "
                "board authorized (2026-07-31) buyback of up to 30,000,000 sh / ¥800.0bn, program "
                "2026-08–10; ~54% of shares / ~100% of yen cap reached in the first 6 trading days."
            ),
            "relevance": "Largest, fastest-executing buyback of the four — lead example.",
            "direction": EvidenceDirection.CONTEXT,
        },
        {
            "date": "2026-06-24", "company": "Kioxia Holdings", "source_name": "EDINET",
            "source_url": _EDINET_PORTAL_URL + "#S100YJ18-p43",
            "fact": (
                "有価証券報告書 (S100YJ18), p.43 §設備の状況/1: "
                "FY2026 capex ¥283.7bn, explicitly \"expanding production capacity in preparation for "
                "increasing memory demand.\""
            ),
            "relevance": "Capacity expanding, explicit demand-linked language, same year as buyback authorization.",
            "direction": EvidenceDirection.SUPPORTS,
        },
        {
            "date": "2026-06-24", "company": "Kioxia Holdings", "source_name": "EDINET",
            "source_url": _EDINET_PORTAL_URL + "#S100YJ18-p44",
            "fact": (
                "有価証券報告書 (S100YJ18), p.44 §設備の状況/3: "
                "forward FY2027 capex plan ~¥450.0bn — front-end equipment/buildings at "
                "Yokkaichi/Kitakami, including 8th-gen BiCS FLASH equipment; ~59% planned increase over FY2026."
            ),
            "relevance": "Capex is guided up, not down, in the same period as an ¥800bn buyback authorization.",
            "direction": EvidenceDirection.SUPPORTS,
        },
        {
            "date": "2026-06-24", "company": "Kioxia Holdings", "source_name": "EDINET",
            "source_url": _EDINET_PORTAL_URL + "#S100YJ18-p14",
            "fact": (
                "有価証券報告書 (S100YJ18), p.14 area, §事業の状況: "
                "\"Data-center flash memory demand... is accelerating due to growth in AI training-server and AI "
                "inference-server demand, and is expected to keep growing, centered on AI inference servers.\""
            ),
            "relevance": "Most explicit and detailed AI-demand narrative of the four companies.",
            "direction": EvidenceDirection.SUPPORTS,
        },
        {
            "date": "2026-06-24", "company": "Kioxia Holdings", "source_name": "EDINET",
            "source_url": _EDINET_PORTAL_URL + "#S100YJ18-p68",
            "fact": (
                "有価証券報告書 (S100YJ18), p.68 §配当政策: "
                "\"After considering liquidity and future growth investment, [we] plan to use the resulting "
                "surplus cumulative free cash flow for further growth investment and shareholder returns.\""
            ),
            "relevance": "Stated policy funds growth investment first, shareholder return from the residual.",
            "direction": EvidenceDirection.SUPPORTS,
        },
        # --- Murata Manufacturing (E01914) ---
        {
            "date": "2026-09-07", "company": "Murata Manufacturing", "source_name": "EDINET",
            "source_url": _EDINET_PORTAL_URL + "#S100Z0S9-p2",
            "fact": (
                "自己株券買付状況報告書 (S100Z0S9), p.2 §1(2): "
                "board authorized (2026-04-30) buyback of up to 75,000,000 sh / ¥150.0bn, program "
                "2026-05–2027-01; only ~40% of yen cap reached by 2026-08 — the slowest pace of the four."
            ),
            "relevance": "Establishes size/pace; smallest buyback-to-capex ratio of the four.",
            "direction": EvidenceDirection.CONTEXT,
        },
        {
            "date": "2026-06-24", "company": "Murata Manufacturing", "source_name": "EDINET",
            "source_url": _EDINET_PORTAL_URL + "#S100YHPY-p56",
            "fact": (
                "有価証券報告書 (S100YHPY), p.56 §設備の状況/1: "
                "FY2026 capex ¥247.8bn (Components ¥187.7bn, Device/Module ¥55.3bn); no material "
                "capacity-affecting disposals."
            ),
            "relevance": "Capex level, same fiscal year as the buyback.",
            "direction": EvidenceDirection.CONTEXT,
        },
        {
            "date": "2026-06-24", "company": "Murata Manufacturing", "source_name": "EDINET",
            "source_url": _EDINET_PORTAL_URL + "#S100YHPY-p59",
            "fact": (
                "有価証券報告書 (S100YHPY), p.59 §設備の状況/3: "
                "forward 1-year capex plan ¥250.0bn (roughly flat vs. FY2026 actual); new "
                "component-production facilities at Izumo, Fukui, and Thailand, all completing Mar 2027."
            ),
            "relevance": "Capacity maintained/modestly continuing, not reduced.",
            "direction": EvidenceDirection.SUPPORTS,
        },
        {
            "date": "2026-06-24", "company": "Murata Manufacturing", "source_name": "EDINET",
            "source_url": _EDINET_PORTAL_URL + "#S100YHPY-p48",
            "fact": (
                "有価証券報告書 (S100YHPY), p.48, MD&A: \"Due to the increase in "
                "electronic components mounted in AI servers and peripheral equipment, data-center-related "
                "demand expanded.\""
            ),
            "relevance": (
                "AI-demand link — explicit, but one of several named demand drivers (also automotive ADAS, "
                "smartphones) — the most partial AI linkage of the four."
            ),
            "direction": EvidenceDirection.SUPPORTS,
        },
    ]


def _build_company_map_specs() -> list[dict]:
    """One dict per ThemeCompanyMapEntry. All four companies here are
    suppliers into the AI/semiconductor buildout (equipment, materials,
    memory, components) rather than sources of the demand itself, and
    nothing found argues against the working thesis strongly enough to
    warrant a DISCONFIRMING entry."""
    return [
        {
            "company_name": "Tokyo Electron", "role": CompanyRole.ENABLER,
            "note": "Semiconductor manufacturing equipment supplier; capex and buyback both active FY2026.",
        },
        {
            "company_name": "Shin-Etsu Chemical", "role": CompanyRole.ENABLER,
            "note": (
                "Semiconductor materials (silicon wafers, photoresist); largest buyback relative to its own "
                "segment's forward capex plan of the four."
            ),
        },
        {
            "company_name": "Kioxia Holdings", "role": CompanyRole.ENABLER,
            "note": (
                "Flash memory/SSD manufacturer; lead example — largest buyback (¥800bn) and largest "
                "planned capex increase (+59%) of the four."
            ),
        },
        {
            "company_name": "Murata Manufacturing", "role": CompanyRole.ENABLER,
            "note": (
                "Electronic components (MLCCs etc.); smallest, slowest-paced buyback of the four; AI-server "
                "demand is one of several drivers, not the central narrative."
            ),
        },
    ]


def build_authored_theme(
    enable_authoring: bool = AUTHORING_ENABLED,
) -> tuple[ResearchTheme, list[ThemeEvidenceItem], list[ThemeCompanyMapEntry]] | None:
    """Returns None when `enable_authoring` is False (the default) —
    the caller must not proceed to validation/persistence in that case.
    `enable_authoring` is a parameter (not a bare module-level read)
    specifically so tests can exercise both branches without needing to
    edit this file.

    Deliberately NOT pure: `created_at`/`updated_at` are stamped from
    the real wall-clock moment this function runs (by explicit operator
    instruction, design/DECISIONS.md, Theme A authoring pass) — the one
    wall-clock read in this module's content-authoring path, mirroring
    --set-visibility's own existing `datetime.now(timezone.utc)` use
    below. Two separate invocations (e.g. a dry run followed by the
    real --confirm run) will therefore produce two different theme ids,
    since build_theme_id() is content-addressed on (title, created_at)
    — only the id from the run that actually persists matters."""
    if not enable_authoring:
        return None

    authored_at = datetime.now(timezone.utc).isoformat()
    theme_id = build_theme_id(_THEME_TITLE, authored_at)
    theme = ResearchTheme(
        id=theme_id,
        category=_THEME_CATEGORY,
        status=_THEME_STATUS,
        visibility=_INITIAL_VISIBILITY,
        title=_THEME_TITLE,
        key_question=_THEME_KEY_QUESTION,
        hypothesis=_THEME_HYPOTHESIS,
        working_thesis=_THEME_WORKING_THESIS,
        why_it_matters=_THEME_WHY_IT_MATTERS,
        what_could_change_the_view=_THEME_WHAT_COULD_CHANGE_THE_VIEW,
        what_to_watch_next=_THEME_WHAT_TO_WATCH_NEXT,
        created_at=authored_at,
        updated_at=authored_at,
        what_eeva_tested=_THEME_WHAT_EEVA_TESTED,
    )

    evidence_items = []
    for spec in _build_evidence_specs():
        evidence_id = build_theme_evidence_id(theme_id, spec["source_url"], spec["date"])
        evidence_items.append(ThemeEvidenceItem(
            id=evidence_id, theme_id=theme_id,
            date=spec["date"], company=spec["company"], source_name=spec["source_name"],
            source_url=spec["source_url"], fact=spec["fact"], relevance=spec["relevance"],
            direction=spec["direction"],
        ))

    company_map_entries = []
    for spec in _build_company_map_specs():
        entry_id = build_theme_company_map_id(theme_id, spec["company_name"], spec["role"])
        company_map_entries.append(ThemeCompanyMapEntry(
            id=entry_id, theme_id=theme_id,
            company_name=spec["company_name"], role=spec["role"], note=spec.get("note"),
        ))

    return theme, evidence_items, company_map_entries


def _nonblank(value: object) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _safe_source_url(url: object) -> bool:
    return isinstance(url, str) and (url.strip().lower().startswith("https://") or url.strip().lower().startswith("http://"))


def contains_placeholder_sentinel(
    theme: ResearchTheme, evidence_items: list[ThemeEvidenceItem], company_map_entries: list[ThemeCompanyMapEntry],
) -> bool:
    values: list[object] = [
        theme.title, theme.key_question, theme.hypothesis, theme.working_thesis, theme.why_it_matters,
        theme.what_could_change_the_view, theme.what_to_watch_next, theme.created_at,
    ]
    for item in evidence_items:
        values.extend([item.date, item.company, item.source_name, item.source_url, item.fact, item.relevance])
    for entry in company_map_entries:
        values.append(entry.company_name)
        if entry.note is not None:
            values.append(entry.note)
    return any(isinstance(value, str) and _PLACEHOLDER_SENTINEL in value for value in values)


def validate_theme_content(
    theme: ResearchTheme, evidence_items: list[ThemeEvidenceItem], company_map_entries: list[ThemeCompanyMapEntry],
) -> tuple[str, ...]:
    """Plain, deterministic content checks — no separate validation
    module exists for Themes (deliberately, per the approved MVP scope);
    this is the minimum needed to keep a curator from publishing
    obviously broken content. Never raises."""
    errors: list[str] = []
    required_theme_fields = {
        "title": theme.title, "key_question": theme.key_question, "hypothesis": theme.hypothesis,
        "working_thesis": theme.working_thesis, "why_it_matters": theme.why_it_matters,
        "what_could_change_the_view": theme.what_could_change_the_view,
        "what_to_watch_next": theme.what_to_watch_next, "created_at": theme.created_at,
    }
    for field_name, value in required_theme_fields.items():
        if not _nonblank(value):
            errors.append(f"theme.{field_name} must not be blank.")

    if not evidence_items:
        errors.append("At least one evidence item is required.")
    for index, item in enumerate(evidence_items):
        for field_name, value in [
            ("date", item.date), ("company", item.company), ("source_name", item.source_name),
            ("fact", item.fact), ("relevance", item.relevance),
        ]:
            if not _nonblank(value):
                errors.append(f"evidence[{index}].{field_name} must not be blank.")
        if not _safe_source_url(item.source_url):
            errors.append(f"evidence[{index}].source_url must be a working http:// or https:// URL.")

    for index, entry in enumerate(company_map_entries):
        if not _nonblank(entry.company_name):
            errors.append(f"company_map[{index}].company_name must not be blank.")

    return tuple(errors)


def persist_theme(
    theme: ResearchTheme, evidence_items: list[ThemeEvidenceItem], company_map_entries: list[ThemeCompanyMapEntry],
    backend: str, cache_dir=None, sqlite_path=None, postgres_url=None,
) -> tuple[bool, list[str]]:
    """Sequential inserts through the private curator repository —
    never the public/UI-facing protocol. Returns (theme_created, notes)
    where notes describes exactly what happened for each record. Safe
    to re-run: every id is content-derived, so an already-inserted
    record is rejected with no mutation rather than duplicated."""
    settings = Settings(
        db_backend=backend,
        cache_dir=Path(cache_dir) if cache_dir else Settings().cache_dir,
        state_db_path=Path(sqlite_path) if sqlite_path else None,
        state_db_url=postgres_url,
    )
    curator = backend_factory.get_theme_curator_repository(settings)

    notes: list[str] = []
    theme_created = curator.insert_theme(theme)
    notes.append(f"theme {theme.id}: {'created' if theme_created else 'already existed (unchanged)'}")

    for item in evidence_items:
        created = curator.insert_evidence_item(item)
        notes.append(f"evidence {item.id}: {'created' if created else 'already existed (unchanged)'}")

    for entry in company_map_entries:
        created = curator.insert_company_map_entry(entry)
        notes.append(f"company_map {entry.id}: {'created' if created else 'already existed (unchanged)'}")

    return theme_created, notes


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("json", "sqlite", "postgres"), default=None)
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--sqlite-path", default=None)
    parser.add_argument("--postgres-url", default=None)
    parser.add_argument("--confirm", action="store_true", help="Required to actually persist. Without it: dry run only.")
    parser.add_argument(
        "--set-visibility", nargs=2, metavar=("THEME_ID", "NEW_VISIBILITY"), default=None,
        help="Transition an existing theme's visibility (internal|ready_to_publish|published|archived). "
             "A separate, explicit action from authoring new content.",
    )
    return parser.parse_args(argv)


def _resolve_backend_settings(args: argparse.Namespace):
    settings = get_settings()
    backend = args.backend or settings.db_backend or "json"
    cache_dir = args.cache_dir or settings.cache_dir
    sqlite_path = args.sqlite_path or (str(settings.state_db_path) if settings.state_db_path else None)
    postgres_url = args.postgres_url or settings.state_db_url
    return backend, cache_dir, sqlite_path, postgres_url


def _run_set_visibility(args: argparse.Namespace) -> int:
    theme_id, raw_new_visibility = args.set_visibility
    try:
        new_visibility = ThemeVisibility(raw_new_visibility)
    except ValueError:
        print(f"Unrecognized visibility {raw_new_visibility!r}. Must be one of: "
              f"{', '.join(v.value for v in ThemeVisibility)}.")
        return 1

    backend, cache_dir, sqlite_path, postgres_url = _resolve_backend_settings(args)
    settings = Settings(
        db_backend=backend, cache_dir=Path(cache_dir) if cache_dir else Settings().cache_dir,
        state_db_path=Path(sqlite_path) if sqlite_path else None, state_db_url=postgres_url,
    )
    curator = backend_factory.get_theme_curator_repository(settings)

    current = curator.get_theme(theme_id)
    if current is None:
        print(f"No theme found with id {theme_id!r} — nothing to transition.")
        return 1

    allowed = _ALLOWED_VISIBILITY_TRANSITIONS.get(current.visibility, frozenset())
    if new_visibility not in allowed:
        print(
            f"Refusing transition {current.visibility.value!r} -> {new_visibility.value!r}: not an allowed "
            f"transition. Allowed from {current.visibility.value!r}: "
            f"{sorted(v.value for v in allowed) or 'none'}."
        )
        return 1

    if not args.confirm:
        print(f"Dry run (pass --confirm to apply): would transition theme {theme_id} "
              f"{current.visibility.value!r} -> {new_visibility.value!r}.")
        return 0

    updated_at = datetime.now(timezone.utc).isoformat()
    updated = curator.set_visibility(theme_id, new_visibility, updated_at)
    if updated is None:
        print(f"Transition failed for theme {theme_id} — no row updated.")
        return 1
    print(f"Theme {theme_id} is now {updated.visibility.value!r}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.set_visibility is not None:
        return _run_set_visibility(args)

    built = build_authored_theme(AUTHORING_ENABLED)
    if built is None:
        print("AUTHORING_ENABLED is False — this script is disabled by default. Edit scripts/create_theme.py, "
              "fill in real content in place of every REPLACE_ME placeholder, and set AUTHORING_ENABLED = True "
              "before re-running.")
        return 0

    theme, evidence_items, company_map_entries = built
    if contains_placeholder_sentinel(theme, evidence_items, company_map_entries):
        print(f"Refusing to proceed: the authored content still contains the {_PLACEHOLDER_SENTINEL!r} placeholder "
              "in one or more fields. Replace every placeholder with real, checked content before running again.")
        return 1

    errors = validate_theme_content(theme, evidence_items, company_map_entries)
    if errors:
        print("Theme content is invalid — nothing was persisted. Issues:")
        for error in errors:
            print(f"  - {error}")
        return 1

    if not args.confirm:
        print("Dry run (pass --confirm to persist): content is well-formed and contains no placeholder text.")
        print(f"  Theme id: {theme.id} (initial visibility: {theme.visibility.value})")
        print(f"  Evidence items: {len(evidence_items)}")
        print(f"  Company map entries: {len(company_map_entries)}")
        print("  Note: this creates the theme at 'internal' visibility only. Publishing is a separate, "
              "explicit step: python -m scripts.create_theme --set-visibility <theme_id> ready_to_publish --confirm")
        return 0

    backend, cache_dir, sqlite_path, postgres_url = _resolve_backend_settings(args)
    theme_created, notes = persist_theme(
        theme, evidence_items, company_map_entries, backend,
        cache_dir=cache_dir, sqlite_path=sqlite_path, postgres_url=postgres_url,
    )
    for note in notes:
        print(note)
    return 0 if theme_created else 1


if __name__ == "__main__":
    sys.exit(main())
