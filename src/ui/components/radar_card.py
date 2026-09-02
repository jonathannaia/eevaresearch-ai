"""Radar Inbox's per-item card (reader-facing data-integrity pass,
design/DECISIONS.md) — a read-only research-feed card, not a review-task
form. Every field renders directly on the card, always visible; the
card's only action is "Open original {source} filing", opening the
official source URL in a new tab (st.link_button's native behavior).

Removed entirely this pass:
  - The Publish/Monitor/Exclude review decision and its optional note
    field. src/logic/review_actions.record_review_decision is no longer
    called from this public interface.
  - The "Prepare analyst view"/"Retry analyst view preparation"
    trigger, which called {edgar,dart,radar}_service.
    process_candidate_now — a live, on-demand extraction call. Removing
    the only public caller means a PROCESSING_DEFERRED candidate is not
    processed further until/unless a separate, autonomous caller is
    added — flagged as a proposed worker-side change, not made here
    (see design/DECISIONS.md's Section 5 report).
  - The "Technical details" expander (raw enum values, filer name,
    state history) — not research information a public reader needs.
    The underlying CandidateStatus/reviewed_at/state_history remain on
    the stored record for worker-internal use; they are simply never
    rendered here.
  - The "Investigate" expander wrapper itself — evidence status, the
    filing-overview interpretation, the original-language excerpt and
    its translation, and materiality now render directly on the card,
    unconditionally, rather than behind a click.

Deliberately reuses the app's existing tokens/chip conventions
(evidence_chips.py's dashed/outline treatment, freshness.py's live/demo
chip) rather than inventing a new visual system, while keeping every
label Radar-specific (radar_status.py) so a Radar item is never mistaken
for a curated, published Signal.
"""
from __future__ import annotations

import html

import streamlit as st

from src.data_access.comparison_store import ComparisonRecord
from src.data_access.dart import dart_rules
from src.logic.market_map import jurisdiction_for_source
from src.models.models import CandidateSignal, EvidenceLocation, FilingEvent, LocationKind
from src.ui.components.analyst_view import render_analyst_view
from src.ui.components.radar_status import (
    RadarItem,
    comparison_status_label,
    default_card_status_html,
    evidence_document_id_label,
    evidence_native_text_label,
    evidence_source_link_label,
    evidence_translation_label,
    status_pill_html,
    translation_unavailable_tag_html,
)

_TRANSLATION_LABEL = "Machine translation · For convenience · Verify against the original-language source"
_COMPARISON_CAVEAT = "Deterministic rule-category comparison — not a filing-text, financial, or materiality determination."


def _why_this_matters_phrases(matched_rules: list[str]) -> list[str]:
    """Parses matched-rule strings into short, human-readable phrases —
    presentation-only, no rule-engine logic here. Handles both real rule
    shapes in this app: DART's dart_rules.evaluate_report_name
    `category:rule_name:keyword` (3 parts, plus the bare
    "amendment_or_correction" marker) and EDGAR's edgar_rules
    `category:8-K item X.XX` / `category:FORM-TYPE` (2 parts)."""
    phrases: list[str] = []
    for rule in matched_rules:
        if rule == "amendment_or_correction":
            phrases.append("Amends or corrects an earlier filing")
            continue
        parts = rule.split(":", 2)
        if len(parts) == 3:
            category, _rule_name, keyword = parts
            label = category.replace("_", " ").capitalize()
            phrases.append(f"{label} keyword match ({keyword})")
        elif len(parts) == 2:
            category, detail = parts
            label = category.replace("_", " ").capitalize()
            phrases.append(f"{label} ({detail})")
        else:
            phrases.append(rule)
    return phrases


def _filing_type_label(filing: FilingEvent) -> str | None:
    """`pblntf_ty` holds a real per-filing type code for EDGAR (e.g.
    "8-K", "10-Q") and EDINET (its own formCode) — see FilingEvent's own
    docstring. DART's disclosure-list response never echoes a type code
    per row, so this is always empty for DART; omitted rather than shown
    as a blank or guessed value, matching this app's existing "never
    fabricate" discipline."""
    return filing.pblntf_ty or None


def _detail_row(label: str, value: str) -> None:
    st.markdown(
        f'<div class="er-muted" style="margin-top:0.15rem;"><strong>{label}:</strong> {value}</div>',
        unsafe_allow_html=True,
    )


def _location_display(location: EvidenceLocation) -> str:
    """Source-aware display string for an EvidenceLocation — never
    fabricates a page for an HTML/XML/XBRL source (see the field's own
    docstring, src/models/models.py); only ever shows a kind Phase 1
    actually populated from data the pipeline already produced."""
    if location.kind == LocationKind.PAGE and location.page is not None:
        return f"Page {location.page}"
    if location.kind == LocationKind.SECTION and location.section:
        return location.section
    if location.kind == LocationKind.TABLE and location.table:
        return f"Table {location.table}"
    if location.kind == LocationKind.PARAGRAPH and location.paragraph_index is not None:
        return f"Paragraph {location.paragraph_index}"
    return location.kind.value


def _evidence_status_panel(
    filing: FilingEvent, candidate: CandidateSignal | None, comparison_record: ComparisonRecord | None = None,
) -> None:
    """Compact, honest, source-neutral evidence summary — plain-language
    labels only (see radar_status.py's evidence_* helpers), never a raw
    enum `.value`. Shown for every item, with or without a CandidateSignal
    yet, always directly on the card (reader-facing data-integrity pass —
    no longer behind a click).

    `comparison_record` (Radar evidence-packet foundation, Phase 3, Step
    3B) is additive and optional — every existing caller that omits it is
    unaffected. Never computed here, never fetched here: the caller
    (radar_inbox.py) already performed the one bulk repository read for
    the whole page and is only handing this function an already-resolved
    record (or None).

    Deliberately excludes the candidate's own review/status bookkeeping
    (CandidateStatus, reviewed_at) — that is worker-internal state, not
    research information a public reader needs (reader-facing data-
    integrity pass, design/DECISIONS.md)."""
    st.markdown('<div class="er-muted" style="margin-top:0.2rem;"><strong>Evidence status</strong></div>', unsafe_allow_html=True)
    _detail_row("Original document", evidence_source_link_label(filing))
    _detail_row("Captured", filing.retrieved_at)
    if filing.filed_at:
        # Evidence-packet foundation, Phase 1 — the full filed/published
        # timestamp, shown only when the source actually supplied one
        # (EDINET today); never fabricated for EDGAR/DART.
        _detail_row("Filed (full timestamp)", filing.filed_at)
    if candidate is not None:
        _detail_row("Native text", evidence_native_text_label(candidate.extraction_state))
        _detail_row("Translation", evidence_translation_label(candidate.translation_state))
        if candidate.flag_reason is not None:
            # Evidence-packet foundation, Phase 1 — the normalized,
            # source-neutral "why flagged" reason. Additive alongside the
            # existing "Why this matters:" bullets on the card itself;
            # never a substitute for them.
            _detail_row("Why flagged", candidate.flag_reason.human_readable_reason)
        if candidate.evidence_location is not None and candidate.evidence_location.kind != LocationKind.UNAVAILABLE:
            _detail_row("Evidence location", _location_display(candidate.evidence_location))
    else:
        # A bare FilingEvent has no extraction/translation/candidate-status
        # data to show — never fabricate one. The exact copy below is the
        # honest, source-neutral answer to "what about translation?" for a
        # filing no candidate pipeline has touched yet.
        _detail_row("Document processing", "Not started")
        _detail_row("Translation", "Available after document processing")
    _detail_row(evidence_document_id_label(filing.source_name), filing.rcept_no)
    if filing.source_name == "EDINET":
        # Gate 8.1 detail (previously its own sibling "EDINET form codes"
        # expander) — same three rows, same exact labels, unconditioned
        # by candidate presence. "—" means the code was genuinely absent
        # on the source record; any other value is shown verbatim.
        _detail_row("Ordinance code", filing.ordinance_code or "—")
        _detail_row("Form code", filing.pblntf_ty or "—")
        _detail_row("Document type code", filing.pblntf_detail_ty or "—")
    if candidate is not None and candidate.evidence_source_member:
        # Evidence-packet foundation, Phase 2, Step 2 — the safe archive-
        # relative ZIP-member path/name an EDINET excerpt was extracted
        # from (see CandidateSignal.evidence_source_member's own
        # docstring). Container provenance only, never a clickable/
        # fetchable reference — plain, HTML-escaped text, since this
        # value originates from a ZIP member's own filename rather than
        # an app-generated string. None/empty for EDGAR, DART, and
        # EDINET's own bare-PDF/HTML/text path, so this row is simply
        # absent for every card but a ZIP-backed EDINET one.
        _detail_row("Evidence file", html.escape(candidate.evidence_source_member))
    if (
        candidate is not None and candidate.id
        and comparison_record is not None
        and comparison_record.current_candidate_id == candidate.id
    ):
        # Radar evidence-packet foundation, Phase 3, Step 3B — a
        # deterministic detection-category comparison only, never a raw
        # stored category/limitation/excerpt/document-id/date (those
        # never render here at all, in this step or any future one this
        # step defines) and never shown unless the supplied record's own
        # identity provably matches the candidate on this card — a
        # mismatch (or no record, or no candidate) renders nothing.
        _detail_row("Comparison", comparison_status_label(comparison_record.comparison_status))
        st.markdown(
            f'<div class="er-muted" style="margin-top:0.1rem; font-size:0.78rem;">{_COMPARISON_CAVEAT}</div>',
            unsafe_allow_html=True,
        )


def _render_card_detail(
    filing: FilingEvent, candidate: CandidateSignal | None, comparison_record: ComparisonRecord | None = None,
) -> None:
    """Everything previously gathered behind the removed "Investigate"
    expander — source/provenance, evidence, the original-language
    excerpt and its translation, and materiality — now always visible,
    directly on the card (reader-facing data-integrity pass, design/
    DECISIONS.md). Read-only: no button, no write call, anywhere in this
    function.

    `comparison_record` (Phase 3, Step 3B) is additive and optional — see
    _evidence_status_panel's own docstring."""
    _evidence_status_panel(filing, candidate, comparison_record)
    if candidate is None:
        return
    render_analyst_view(filing, candidate)
    if candidate.excerpt_original:
        st.markdown('<div class="er-muted" style="margin-top:0.4rem;"><strong>Original-language excerpt</strong></div>', unsafe_allow_html=True)
        st.markdown(f'<div>{candidate.excerpt_original}</div>', unsafe_allow_html=True)
    if candidate.excerpt_translation is not None:
        # Evidence-packet foundation, Phase 1: labeled "working translation"
        # — a machine-translated convenience string, never a substitute
        # for the original-language excerpt above it.
        st.markdown('<div class="er-muted" style="margin-top:0.4rem;"><strong>English working translation</strong></div>', unsafe_allow_html=True)
        st.markdown(f'<div>{candidate.excerpt_translation.translated_text}</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="er-muted" style="margin-top:0.2rem;">'
            f'{candidate.excerpt_translation.provider} · English working translation · translated at {candidate.excerpt_translation.translated_at}</div>',
            unsafe_allow_html=True,
        )
    unavailable_tag = translation_unavailable_tag_html(RadarItem(filing=filing, candidate=candidate))
    if unavailable_tag:
        st.markdown(f'<div style="margin-top:0.3rem;">{unavailable_tag}</div>', unsafe_allow_html=True)
    if candidate.materiality_assessment != "Not assessed":
        st.markdown(
            f'<div class="er-muted" style="margin-top:0.3rem;">Potential materiality: {candidate.materiality_assessment}</div>',
            unsafe_allow_html=True,
        )


def _render_quiet_links(filing: FilingEvent) -> None:
    """The card's one action: opening the official source URL in a new
    tab (st.link_button's native behavior) — de-emphasized (the same
    cta-tertiary-* ghost treatment used everywhere else in the app for a
    low-emphasis action), never a form control."""
    link_cols = st.columns([2, 7])
    with link_cols[0]:
        with st.container(key=f"cta-tertiary-radar-original-{filing.rcept_no}"):
            st.link_button(f"Open original {filing.source_name} filing ↗", filing.source_url, use_container_width=True)


def candidate_row(item: RadarItem, show_full_status: bool = False, comparison_record: ComparisonRecord | None = None) -> None:
    """`show_full_status` (Phase T1, design/DECISIONS.md) — False (the
    default, used by the "Latest" view) suppresses internal/non-
    actionable status pills and shows a quiet note for a genuine
    retrieval/parse failure instead of the loud raw pill; True (used by
    "Captured filings," the fuller/opt-in inventory view) always shows
    the complete, real status_pill_html() unchanged, since that view's
    whole purpose is truthful completeness. Neither path changes
    `status_bucket()`/`status_label()`/the stored status itself — only
    which HTML this one component renders.

    `comparison_record` (Phase 3, Step 3B, design/DECISIONS.md) is
    additive and optional, defaulting to None — every existing caller
    that omits it renders exactly as before.

    Reader-facing data-integrity pass (design/DECISIONS.md): no
    `on_process`/`on_review_decision`/`process_ready` parameters exist
    any more — this card is read-only end to end, with exactly one
    action (_render_quiet_links, below)."""
    filing = item.filing
    candidate = item.candidate

    with st.container(border=True, key=f"radar-item-{filing.rcept_no}"):
        top_cols = st.columns([3, 3, 2, 2], vertical_alignment="center")
        with top_cols[0]:
            # Phase T1 (design/DECISIONS.md): internal, non-actionable
            # workflow states (Needs review, Candidate detected, ...)
            # render nothing here in the default "Latest" view — they
            # convey no real signal to a researcher; a genuine retrieval/
            # parse failure renders a quiet, honest note instead of the
            # loud red status pill; everything else (Published, a bare
            # new filing) is unchanged. "Captured filings" always shows
            # the complete, real status.
            indicator = status_pill_html(item) if show_full_status else default_card_status_html(item)
            if indicator:
                st.markdown(indicator, unsafe_allow_html=True)
        with top_cols[1]:
            st.markdown(f'<div class="er-muted">{filing.corp_name} · {filing.stock_code}</div>', unsafe_allow_html=True)
        with top_cols[2]:
            jurisdiction = jurisdiction_for_source(filing.source_name)
            source_label = f"{filing.source_name} · {jurisdiction}" if jurisdiction else filing.source_name
            st.markdown(f'<div class="er-muted">{source_label}</div>', unsafe_allow_html=True)
        with top_cols[3]:
            st.markdown(f'<div class="er-muted" style="text-align:right;">{filing.rcept_dt} · {filing.original_language}</div>', unsafe_allow_html=True)

        st.markdown(f'<div class="er-card-title" style="margin-top:0.4rem;">{filing.report_nm}</div>', unsafe_allow_html=True)

        if candidate is not None and candidate.title_translation is not None:
            st.markdown(f'<div style="margin-top:0.2rem;">{candidate.title_translation.translated_text}</div>', unsafe_allow_html=True)
            st.markdown(f'<span class="er-chip er-chip-inference">{_TRANSLATION_LABEL}</span>', unsafe_allow_html=True)

        filing_type = _filing_type_label(filing)
        if filing_type:
            st.markdown(f'<div class="er-muted" style="margin-top:0.3rem;">Filing type: {filing_type}</div>', unsafe_allow_html=True)

        if candidate is not None:
            phrases = _why_this_matters_phrases(candidate.matched_rules)
            if phrases:
                st.markdown('<div class="er-muted" style="margin-top:0.3rem;"><strong>Why this matters:</strong></div>', unsafe_allow_html=True)
                for phrase in phrases:
                    st.markdown(f'<div class="er-muted" style="margin-left:0.8rem;">• {phrase}</div>', unsafe_allow_html=True)

        theme_label = filing.theme_slug.replace("-", " ").title() if filing.theme_slug else ""
        if theme_label:
            st.markdown(f'<div class="er-muted" style="margin-top:0.3rem;">{theme_label}</div>', unsafe_allow_html=True)

        if candidate is not None:
            st.markdown(
                f'<div class="er-muted" style="margin-top:0.2rem;">{dart_rules.format_confidence_label(candidate.confidence)}</div>',
                unsafe_allow_html=True,
            )

        _render_card_detail(filing, candidate, comparison_record)
        _render_quiet_links(filing)
