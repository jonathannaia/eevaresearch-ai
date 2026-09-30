"""Coverage — Phase A read-only observability page over the Issuer
Registry (design/ISSUER_REGISTRY_FOUNDATION.md). Static/local data only:
no scan, no source request, no candidate/Signal action, and no registry
mutation happens anywhere on this page. Every row/metric reads straight
from `src.config.issuer_registry`'s `SEED_ISSUERS`/`DISCOVERY_STUBS` via
the pure query layer in `src.logic.issuer_coverage` — this page owns no
issuer data of its own, and never duplicates the issuer universe into a
UI-specific list.

This is an observability/coverage map, not a review-action page — there is
deliberately no button here that writes state or invokes an external
system, unlike Radar Inbox's Scan/Process/Publish/Monitor/Exclude
controls."""
from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from src.config.issuer_registry import DISCOVERY_STUBS, SEED_ISSUERS, source_name_for_seed_issuer
from src.config.ontology import KNOWN_CATEGORY_CONFLICTS
from src.config.issuer_registry import SEED_ISSUERS as _SEED_FOR_NAMES
from src.logic.issuer_coverage import (
    COVERAGE_STATES,
    coverage_counts_by_lane,
    coverage_gaps,
    last_verified_observation_by_lane,
    stale_after_hours,
    filter_seed_issuers,
    get_ambiguous_stub_labels,
    get_coverage_summary,
    get_jurisdiction_gaps,
)
from src.models.issuer import Issuer

# issuer_id -> legal name. The gaps table shows the instrument, never a
# source, URL or document id.
_ISSUER_NAMES = {issuer.issuer_id: issuer.legal_name for issuer in _SEED_FOR_NAMES}
from src.ui import render_timing
from src.ui.components.cards import metric_tile
from src.ui.components.empty_state import empty_state
from src.ui.components.section import section_header


def _seed_row(issuer: Issuer, include_layer_column: bool) -> dict:
    # Phase D (design/DECISIONS.md): the Layer(s) column is only included
    # when at least one seed issuer anywhere has a populated
    # supply_chain_layers value — same underlying field/data-model
    # support either way, just not a column of all-"—" dashes when
    # nothing has been assigned yet. Dict insertion order keeps it in the
    # same relative position (after Theme(s), before Coverage) whenever
    # it is included.
    row = {
        "Company": issuer.legal_name,
        "Ticker": issuer.primary_ticker or "—",
        "Exchange": issuer.primary_exchange or "—",
        "Jurisdiction": issuer.country_or_jurisdiction or "—",
        "Source": source_name_for_seed_issuer(issuer),
        "Theme(s)": ", ".join(issuer.themes) if issuer.themes else "—",
    }
    if include_layer_column:
        row["Layer(s)"] = ", ".join(issuer.supply_chain_layers) if issuer.supply_chain_layers else "—"
    row["Coverage"] = issuer.coverage_state.value
    row["Lifecycle"] = issuer.lifecycle_state.value
    row["Scan eligibility"] = "Eligible"
    return row


def _discovery_label(issuer: Issuer) -> str:
    # A stub whose name is a "not given in seed list" placeholder shows its
    # bare ticker instead — never presents the placeholder text as if it
    # were a real company name.
    name = issuer.legal_name or ""
    if "not given in seed list" in name.lower():
        return issuer.primary_ticker or name
    return name


def _discovery_row(issuer: Issuer) -> dict:
    return {
        "Company / ticker": _discovery_label(issuer),
        "Portfolio-map category / layer": ", ".join(issuer.supply_chain_layers) if issuer.supply_chain_layers else "—",
        "Best-fit theme": ", ".join(issuer.themes) if issuer.themes else "Not assigned",
        "Coverage state": issuer.coverage_state.value,
        "Scan eligibility": "Not eligible",
        "Why": issuer.normalization_status or "Not independently verified.",
    }


def _render_summary() -> None:
    with render_timing.data_load():
        summary = get_coverage_summary()
    cols = st.columns(4)
    with cols[0]:
        metric_tile("Active seed issuers", str(summary.active_seed_count))
    with cols[1]:
        metric_tile("Discovery proposals", str(summary.discovery_count))
    with cols[2]:
        metric_tile("Scan-eligible", str(summary.scan_eligible_count))
    with cols[3]:
        metric_tile("Unverified / excluded", str(summary.unverified_excluded_count))

    if summary.seed_count_by_source:
        breakdown = " · ".join(f"{source}: {count}" for source, count in sorted(summary.seed_count_by_source.items()))
        st.markdown(f'<div class="er-muted" style="margin-top:0.3rem;">Seed issuers by source — {breakdown}</div>', unsafe_allow_html=True)


def _render_seed_coverage() -> None:
    section_header("Seed coverage", "Known, currently eligible issuers from the active tracked-company universe.")
    if not SEED_ISSUERS:
        empty_state("No seed issuers configured yet.")
        return

    theme_options = sorted({t for i in SEED_ISSUERS for t in i.themes})
    layer_options = sorted({layer for i in SEED_ISSUERS for layer in i.supply_chain_layers})
    source_options = sorted({source_name_for_seed_issuer(i) for i in SEED_ISSUERS})
    country_options = sorted({i.country_or_jurisdiction for i in SEED_ISSUERS if i.country_or_jurisdiction})

    # Phase B (UI audit): only render the Layer multiselect when it has
    # options — today's seed data has none, so it rendered as a dead
    # control beside three working ones. Reappears automatically once
    # any seed issuer has a populated supply_chain_layers value.
    dimensions = [
        ("theme", "Theme", theme_options, "coverage-seed-theme"),
        ("layer", "Layer", layer_options, "coverage-seed-layer"),
        ("source", "Source", source_options, "coverage-seed-source"),
        ("country", "Jurisdiction", country_options, "coverage-seed-country"),
    ]
    active_dimensions = [d for d in dimensions if d[2]]
    cols = st.columns(1 + len(active_dimensions))
    search = cols[0].text_input("Search", key="coverage-seed-search", placeholder="Company or ticker…")
    selected: dict[str, list[str]] = {name: [] for name, _, _, _ in dimensions}
    for col, (name, label, options, key) in zip(cols[1:], active_dimensions):
        selected[name] = col.multiselect(label, options, key=key)
    theme_filter = selected["theme"]
    layer_filter = selected["layer"]
    source_filter = selected["source"]
    country_filter = selected["country"]

    filtered = filter_seed_issuers(
        SEED_ISSUERS,
        search=search,
        themes=tuple(theme_filter),
        layers=tuple(layer_filter),
        sources=tuple(source_filter),
        countries=tuple(country_filter),
    )
    if not filtered:
        empty_state("No seed issuers match these filters.", "Clear a filter to see more results.")
        return

    include_layer_column = bool(layer_options)
    if not include_layer_column:
        # Quiet, contextual note — only shown when the column is actually
        # absent, not a permanent caveat sitting above the table either way.
        st.caption("Layer assignments aren't available for the current issuer set yet.")
    st.dataframe([_seed_row(i, include_layer_column) for i in filtered], hide_index=True, width="stretch")
    st.markdown(f'<div class="er-muted">{len(filtered)} of {len(SEED_ISSUERS)} seed issuers</div>', unsafe_allow_html=True)


def _render_discovery_queue() -> None:
    section_header("Discovery queue", "Unverified portfolio-map candidates — not tracked, not scan-eligible.")
    with st.expander(f"{len(DISCOVERY_STUBS)} discovery proposals — not active coverage", expanded=False):
        if not DISCOVERY_STUBS:
            empty_state("No discovery proposals yet.")
            return
        st.markdown(
            '<div class="er-muted" style="margin-bottom:0.5rem;">These names have not been externally verified '
            "and are not part of the active tracked universe. No scan, add-to-coverage, or review action is "
            "available here.</div>",
            unsafe_allow_html=True,
        )
        st.dataframe([_discovery_row(i) for i in DISCOVERY_STUBS], hide_index=True, width="stretch")


_LANE_LABELS = {"edgar": "SEC EDGAR (U.S.)", "edinet": "EDINET (Japan)", "dart": "DART (Korea)"}

# Closed vocabulary. A reason that is not in here is never rendered —
# this is what keeps a raw exception message, a URL or a source name
# from reaching a reader through the blocking-reason column.
_BLOCKING_REASON_LABELS = {
    "identity_unresolved": "Identity not resolved",
    "untrusted_outcome": "Outcome not trustworthy",
    "provider_error": "Source unavailable",
    "lane_untrusted": "Lane result not trustworthy",
    "unresolvable_scan_name": "Unrecognized instrument in scan",
    "": "—",
}


def _static_coverage_freshness_panel() -> None:
    """The pre-measurement panel. Still the fallback whenever measured
    coverage cannot be read, so a backend problem degrades this page to
    what it said yesterday rather than taking it down."""
    with st.container(border=True, key="card-coverage-freshness"):
        st.markdown(
            "**Filing Radar** — selected issuers across SEC EDGAR (U.S.), EDINET (Japan), "
            "and DART (Korea); scheduled hourly."
        )
        st.markdown(
            "**Signals** — material disclosures and developments from official company and "
            "editorial sources; scheduled every 30 minutes."
        )
        st.markdown(
            "**Translations** — machine-generated English translations for supported short "
            "Japanese and Korean content; original sources remain authoritative."
        )


def _relative_age(timestamp: str | None, now: datetime) -> str:
    if not timestamp:
        return "never"
    try:
        moment = datetime.fromisoformat(timestamp)
    except ValueError:
        return "unknown"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    minutes = int((now - moment).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    return f"{hours}h ago" if hours < 48 else f"{hours // 24}d ago"


def _measured_coverage_rows():
    """The one repository read this page performs. Timed as its own
    data-load step so it can be seen in production timing alongside the
    Dashboard's."""
    from src.config.settings import get_settings
    from src.data_access import backend_factory

    with render_timing.step("data_load.coverage_status"):
        settings = get_settings()
        repository = backend_factory.get_coverage_status_repository(settings)
        return tuple(repository.get_all_coverage_statuses().values()), settings


def _render_coverage_freshness_panel() -> None:
    """Measured coverage per lane, replacing the three static sentences
    this panel used to carry.

    Fails closed: any problem reading measured coverage — an
    unsupported backend, an unreachable database, a schema not yet
    migrated — falls back to the static panel. A reader never sees an
    error, a source name, a document id or a database detail."""
    section_header("Coverage & Freshness")
    try:
        rows, settings = _measured_coverage_rows()
    except Exception:  # noqa: BLE001 — observability must never take down the page
        _static_coverage_freshness_panel()
        return
    if not rows:
        _static_coverage_freshness_panel()
        return

    now = datetime.now(timezone.utc)
    stale_hours = stale_after_hours(getattr(settings, "radar_scan_interval_minutes", 60))
    counts = coverage_counts_by_lane(rows, now, stale_hours)
    latest = last_verified_observation_by_lane(rows)
    gaps = coverage_gaps(rows, now, stale_hours)

    with st.container(border=True, key="card-coverage-freshness"):
        st.markdown(
            f'<div class="er-muted">Measured from completed scans. A lane is considered stale '
            f"after {stale_hours}h without a trustworthy observation.</div>",
            unsafe_allow_html=True,
        )
        st.dataframe(
            [
                {
                    "Lane": _LANE_LABELS.get(lane, lane),
                    **{state: counts[lane][state] for state in COVERAGE_STATES},
                    "Last verified": _relative_age(latest.get(lane), now),
                }
                for lane in sorted(counts)
            ],
            hide_index=True,
            width="stretch",
        )

    if gaps:
        with st.expander(f"{len(gaps)} instrument coverage gaps", expanded=False):
            st.dataframe(
                [
                    {
                        "Instrument": _ISSUER_NAMES.get(gap["issuer_id"], gap["issuer_id"]),
                        "Lane": _LANE_LABELS.get(gap["lane"], gap["lane"]),
                        "State": gap["state"],
                        "Reason": _BLOCKING_REASON_LABELS.get(gap["blocking_reason"], "—"),
                        "Last verified": _relative_age(gap["last_success_at"], now),
                    }
                    for gap in gaps
                ],
                hide_index=True,
                width="stretch",
            )


def _render_coverage_notes() -> None:
    section_header(
        "Coverage notes",
        "Open normalization items — documented, not silently resolved. None of these affect current scan eligibility.",
    )
    with st.expander(f"{len(KNOWN_CATEGORY_CONFLICTS)} known category conflicts", expanded=False):
        if not KNOWN_CATEGORY_CONFLICTS:
            empty_state("No known conflicts recorded.")
        for conflict in KNOWN_CATEGORY_CONFLICTS:
            st.markdown(f'<div class="er-card-title" style="font-size:0.95rem;">{conflict.subject}</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="er-muted" style="margin-bottom:0.7rem;">{conflict.description}</div>', unsafe_allow_html=True)

    jurisdiction_gaps = get_jurisdiction_gaps()
    if jurisdiction_gaps:
        st.markdown(
            f'<div class="er-muted" style="margin-top:0.4rem;">New-jurisdiction gaps — no configured '
            f'regulatory-source adapter yet: {", ".join(jurisdiction_gaps)}.</div>',
            unsafe_allow_html=True,
        )

    ambiguous = get_ambiguous_stub_labels()
    if ambiguous:
        st.markdown(
            f'<div class="er-muted" style="margin-top:0.3rem;">Ambiguous identity/listing items — flagged, '
            f'not guessed: {", ".join(ambiguous)}.</div>',
            unsafe_allow_html=True,
        )


def render() -> None:
    st.markdown('<div class="er-page-title">Coverage</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="er-muted">Global issuer coverage by theme, supply-chain layer, source readiness, and '
        "discovery status.</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="er-muted" style="margin-top:0.3rem;">Registry view — static local configuration; not a '
        "live scan-status feed.</div>",
        unsafe_allow_html=True,
    )

    _render_summary()
    _render_coverage_freshness_panel()
    _render_seed_coverage()
    _render_discovery_queue()
    _render_coverage_notes()
