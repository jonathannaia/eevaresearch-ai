"""Radar Inbox's per-item row + detail — a research-feed card (Phase R1,
design/DECISIONS.md), not a review-task form. Default-visible anatomy is
issuer/ticker, source/date, filing type, "Why this matters," theme, and
detection confidence, plus one primary action ("Investigate →") and quiet
secondary links (original filing, company). Everything operational —
document-preparation ("Prepare analyst view"/retry), the Publish/Monitor/
Exclude review decision, translation/materiality/technical detail, and
state history — moved inside the Investigate expander (the same
`st.expander` this file already used as "Details," just relabeled and
promoted to the one primary control, per the approved plan's "least
complex existing stable Streamlit pattern" instruction — no new drawer,
no DOM hack). None of those controls' wording, status transitions,
storage, eligibility effects, or audit trail changed — only where they
render.

Deliberately reuses the app's existing tokens/chip conventions
(evidence_chips.py's dashed/outline treatment, freshness.py's live/demo
chip) rather than inventing a new visual system, while keeping every
label Radar-specific (radar_status.py) so a Radar item is never mistaken
for a curated, published Signal.
"""
from __future__ import annotations

import html
from typing import Callable

import streamlit as st

from src.data_access.comparison_store import ComparisonRecord
from src.data_access.dart import dart_rules, retry_policy
from src.models.models import CandidateSignal, CandidateStatus, EvidenceLocation, FilingEvent, LocationKind
from src.ui.components.analyst_view import render_analyst_view
from src.ui.components.radar_status import (
    RadarItem,
    comparison_status_label,
    default_card_status_html,
    evidence_document_id_label,
    evidence_native_text_label,
    evidence_review_label,
    evidence_source_link_label,
    evidence_translation_label,
    status_pill_html,
    translation_unavailable_tag_html,
)
from src.ui.ui import get_page

_TRANSLATION_LABEL = "Machine translation · For convenience · Verify against the original-language source"
_INVESTIGATE_LABEL = "Investigate →"
_COMPARISON_CAVEAT = "Deterministic rule-category comparison — not a filing-text, financial, or materiality determination."

_PREPARE_ANALYST_VIEW_LABEL = "Prepare analyst view"
_RETRY_ANALYST_VIEW_LABEL = "Retry analyst view preparation"
_PREPARING_SPINNER_TEXT = "Preparing analyst view — retrieving and interpreting the filing…"
_ANALYST_VIEW_CAPTION = "When ready, the analyst-ready summary appears above."
_UNAVAILABLE_REASON = "Preparation is unavailable until this source is configured."

_REVIEW_NOTE_LABEL = "Review note"
_REVIEW_NOTE_PLACEHOLDER = "Optional note (required for Exclude)…"
_PUBLISH_LABEL = "Publish"
_MONITOR_LABEL = "Monitor"
_EXCLUDE_LABEL = "Exclude"
_CONFIRM_EXCLUDE_LABEL = "Confirm exclude"
_CANCEL_LABEL = "Cancel"
_EXCLUDE_NOTE_REQUIRED_REASON = "A note is required before excluding — explain why this filing doesn't need review."
_REVIEW_DECISION_ERROR = "Could not record this decision — the candidate may no longer exist. Refresh and try again."


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
    yet; the existing raw technical rows further below are untouched and
    still show `.value` strings for anyone who wants them.

    `comparison_record` (Radar evidence-packet foundation, Phase 3, Step
    3B) is additive and optional — every existing caller that omits it is
    unaffected. Never computed here, never fetched here: the caller
    (radar_inbox.py) already performed the one bulk repository read for
    the whole page and is only handing this function an already-resolved
    record (or None)."""
    st.markdown('<div class="er-muted" style="margin-top:0.2rem;"><strong>Evidence status</strong></div>', unsafe_allow_html=True)
    _detail_row("Original document", evidence_source_link_label(filing))
    if filing.filed_at:
        # Evidence-packet foundation, Phase 1 — the full filed/published
        # timestamp, shown only when the source actually supplied one
        # (EDINET today); never fabricated for EDGAR/DART.
        _detail_row("Filed (full timestamp)", filing.filed_at)
    if candidate is not None:
        _detail_row("Native text", evidence_native_text_label(candidate.extraction_state))
        _detail_row("Translation", evidence_translation_label(candidate.translation_state))
        _detail_row("Review", evidence_review_label(candidate))
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
        # expander; Phase R1 folds it in here instead, since Investigate
        # already is the one collapsed detail area for this card) — same
        # three rows, same exact labels, unconditioned by candidate
        # presence. "—" means the code was genuinely absent on the source
        # record; any other value is shown verbatim.
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


def _render_process_action(candidate_id: str, label: str, key: str, ready: bool, on_process: Callable[[str], None]) -> None:
    """Renders the one "Prepare analyst view" / "Retry analyst view
    preparation" button — the seam that used to click and appear to do
    nothing (no visible spinner during the synchronous retrieval/
    extraction/translation call, which can legitimately take a long
    time). Now: a visible spinner covers the call, a disabled state with
    an honest, non-secret reason covers the "this source isn't
    configured" case (readiness is checked here, not inferred), and a
    narrow except-Exception guards only against a truly unexpected
    failure outside the pipeline's own typed retrieval/parse/translation
    failure states — those are already caught and persisted as a
    CandidateStatus inside the pipeline itself and must reach the UI
    unchanged; this is defense-in-depth only, same pattern as the Scan
    buttons' own try/except in radar_inbox.py."""
    if not ready:
        st.button(label, key=key, use_container_width=True, disabled=True)
        st.markdown(f'<div class="er-muted" style="margin-top:0.2rem; font-size:0.78rem;">{_UNAVAILABLE_REASON}</div>', unsafe_allow_html=True)
        return

    error_key = f"radar-process-error-{candidate_id}"
    if st.button(label, key=key, use_container_width=True):
        st.session_state.pop(error_key, None)
        with st.spinner(_PREPARING_SPINNER_TEXT):
            try:
                on_process(candidate_id)
            except Exception:  # noqa: BLE001 — defense-in-depth only; see docstring above
                st.session_state[error_key] = "Preparing the analyst view failed unexpectedly — see server logs for detail."
    if st.session_state.get(error_key):
        st.markdown(f'<div class="er-muted" style="margin-top:0.2rem; font-size:0.78rem;">{st.session_state[error_key]}</div>', unsafe_allow_html=True)


def _render_review_actions(
    candidate: CandidateSignal,
    on_review_decision: Callable[[str, CandidateStatus, str], CandidateSignal | None],
) -> None:
    """Publish / Monitor / Exclude — always offered regardless of the
    candidate's current status, so an earlier reviewer decision can be
    revised; every decision is recorded via the shared, source-agnostic
    record_review_decision() seam (src/logic/review_actions.py), routed
    to the right on-disk store by the render()-level closure passed in
    as `on_review_decision` — this component has no source awareness of
    its own, same separation the existing on_process callback already
    uses. Exclude requires a non-whitespace note and a second, explicit
    "Confirm exclude" click before anything is written; Publish/Monitor
    are single-click with an optional note.

    Phase R1: rendered inside the Investigate expander now, not in the
    card's default body — same function, same wording, same status
    transitions/storage/eligibility effects, only relocated."""
    note_key = f"radar-review-note-{candidate.id}"
    pending_exclude_key = f"radar-exclude-pending-{candidate.id}"
    error_key = f"radar-review-error-{candidate.id}"

    st.markdown('<div class="er-muted" style="margin-top:0.4rem;"><strong>Review decision</strong></div>', unsafe_allow_html=True)
    note = st.text_input(_REVIEW_NOTE_LABEL, key=note_key, label_visibility="collapsed", placeholder=_REVIEW_NOTE_PLACEHOLDER)
    note_stripped = note.strip()

    def _apply(status: CandidateStatus) -> None:
        st.session_state.pop(error_key, None)
        result = on_review_decision(candidate.id, status, note_stripped)
        if result is None:
            st.session_state[error_key] = _REVIEW_DECISION_ERROR
            return
        st.session_state.pop(note_key, None)
        st.session_state.pop(pending_exclude_key, None)
        st.rerun()

    is_pending_exclude = st.session_state.get(pending_exclude_key, False)
    review_cols = st.columns(4) if is_pending_exclude else st.columns(3)

    with review_cols[0]:
        if st.button(_PUBLISH_LABEL, key=f"publish-{candidate.id}", use_container_width=True):
            _apply(CandidateStatus.PUBLISHED)
    with review_cols[1]:
        if st.button(_MONITOR_LABEL, key=f"monitor-{candidate.id}", use_container_width=True):
            _apply(CandidateStatus.MONITORING)

    if not is_pending_exclude:
        with review_cols[2]:
            if st.button(_EXCLUDE_LABEL, key=f"exclude-{candidate.id}", use_container_width=True):
                st.session_state[pending_exclude_key] = True
                st.rerun()
    else:
        with review_cols[2]:
            if st.button(
                _CONFIRM_EXCLUDE_LABEL, key=f"exclude-confirm-{candidate.id}",
                use_container_width=True, disabled=not note_stripped,
            ):
                _apply(CandidateStatus.DISMISSED)
        with review_cols[3]:
            if st.button(_CANCEL_LABEL, key=f"exclude-cancel-{candidate.id}", use_container_width=True):
                st.session_state.pop(pending_exclude_key, None)
                st.rerun()
        if not note_stripped:
            st.markdown(f'<div class="er-muted" style="margin-top:0.15rem; font-size:0.78rem;">{_EXCLUDE_NOTE_REQUIRED_REASON}</div>', unsafe_allow_html=True)

    if st.session_state.get(error_key):
        st.markdown(f'<div class="er-muted" style="margin-top:0.15rem; font-size:0.78rem;">{st.session_state[error_key]}</div>', unsafe_allow_html=True)


def _render_investigate_body(
    filing: FilingEvent,
    candidate: CandidateSignal,
    on_process: Callable[[str], None] | None,
    process_ready: bool,
    on_review_decision: Callable[[str, CandidateStatus, str], CandidateSignal | None] | None,
    comparison_record: ComparisonRecord | None = None,
) -> None:
    """Everything that used to sit directly in the card body or in a
    separate "Details" expander, now gathered behind the one Investigate
    control (Phase R1) — source/provenance, evidence, the original-
    language excerpt and its translation, materiality, the document-
    preparation action, the human review decision, and the technical/
    audit-trail detail. Nothing here changed except position: no
    wording, status transition, storage call, or eligibility effect was
    touched.

    `comparison_record` (Phase 3, Step 3B) is additive and optional — see
    _evidence_status_panel's own docstring."""
    _evidence_status_panel(filing, candidate, comparison_record)
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

    if on_process is not None:
        if candidate.status == CandidateStatus.PROCESSING_DEFERRED:
            st.markdown('<div style="margin-top:0.5rem;"></div>', unsafe_allow_html=True)
            _render_process_action(
                candidate.id, _PREPARE_ANALYST_VIEW_LABEL, f"process-{candidate.id}", process_ready, on_process,
            )
            st.markdown(f'<div class="er-muted" style="margin-top:0.2rem; font-size:0.78rem;">{_ANALYST_VIEW_CAPTION}</div>', unsafe_allow_html=True)
        elif retry_policy.is_retryable(candidate):
            eligibility = retry_policy.retry_eligibility(candidate)
            st.markdown('<div style="margin-top:0.5rem;"></div>', unsafe_allow_html=True)
            if eligibility.eligible:
                _render_process_action(
                    candidate.id, _RETRY_ANALYST_VIEW_LABEL, f"retry-{candidate.id}", process_ready, on_process,
                )
                st.markdown(f'<div class="er-muted" style="margin-top:0.2rem; font-size:0.78rem;">{_ANALYST_VIEW_CAPTION}</div>', unsafe_allow_html=True)
            elif eligibility.reason == "Retry limit reached":
                st.button(
                    "Retry limit reached · Review original filing", key=f"retry-{candidate.id}",
                    disabled=True, use_container_width=True,
                )
            else:
                st.button(
                    f"Retry available in {eligibility.cooldown_remaining_seconds}s",
                    key=f"retry-{candidate.id}", disabled=True, use_container_width=True,
                )

    if on_review_decision is not None:
        _render_review_actions(candidate, on_review_decision)

    with st.expander("Technical details"):
        _detail_row(evidence_document_id_label(filing.source_name), filing.rcept_no)
        _detail_row("Filer", filing.flr_nm)
        _detail_row("Filed", filing.rcept_dt)
        _detail_row("Retrieved", filing.retrieved_at)
        _detail_row("Extraction state", candidate.extraction_state.value)
        _detail_row("Translation state", candidate.translation_state.value)
        _detail_row("Excerpt quality", candidate.excerpt_quality.value)
        if candidate.state_history:
            st.markdown('<div class="er-muted" style="margin-top:0.4rem;"><strong>State history</strong></div>', unsafe_allow_html=True)
            for transition in candidate.state_history:
                detail = f" — {transition.detail}" if transition.detail else ""
                st.markdown(
                    f'<div class="er-muted" style="margin-left:0.8rem;">{transition.at} · {transition.status.value}{detail}</div>',
                    unsafe_allow_html=True,
                )


def _render_quiet_links(filing: FilingEvent) -> None:
    """Secondary access — de-emphasized (the same cta-tertiary-* ghost
    treatment used everywhere else in the app for a low-emphasis action),
    never the card's primary interaction. Company links only when a
    stock/ticker code exists on the filing, reusing the exact existing
    Company-page route/query-param pattern Market Map already established
    (get_page("company"), ?symbol=) — no new route. A "related evidence/
    Radar" link is deliberately omitted: no existing route lets this page
    link into itself pre-filtered by company, and inventing one is out of
    scope for this phase."""
    link_cols = st.columns([2, 2, 5])
    with link_cols[0]:
        with st.container(key=f"cta-tertiary-radar-original-{filing.rcept_no}"):
            st.link_button(f"Open original {filing.source_name} filing ↗", filing.source_url, use_container_width=True)
    if filing.stock_code:
        company_page = get_page("company")
        if company_page is not None:
            with link_cols[1]:
                with st.container(key=f"cta-tertiary-radar-company-{filing.rcept_no}"):
                    st.page_link(company_page, label="Company", query_params={"symbol": filing.stock_code})


def candidate_row(
    item: RadarItem, on_process: Callable[[str], None] | None = None, process_ready: bool = True,
    on_review_decision: Callable[[str, CandidateStatus, str], CandidateSignal | None] | None = None,
    show_full_status: bool = False,
    comparison_record: ComparisonRecord | None = None,
) -> None:
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
    that omits it renders exactly as before. The caller (radar_inbox.py)
    is expected to have already resolved this via one bulk repository
    read for the whole page; this component never fetches, computes, or
    caches a comparison result itself. Passed straight through to
    _render_investigate_body/_evidence_status_panel, which are the only
    places that decide whether it's actually displayable (candidate
    present with a non-empty id, and the record's own
    current_candidate_id matching that id)."""
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
            # the complete, real status. The full, real status remains
            # available inside Investigate's own evidence-status panel
            # either way.
            indicator = status_pill_html(item) if show_full_status else default_card_status_html(item)
            if indicator:
                st.markdown(indicator, unsafe_allow_html=True)
        with top_cols[1]:
            st.markdown(f'<div class="er-muted">{filing.corp_name} · {filing.stock_code}</div>', unsafe_allow_html=True)
        with top_cols[2]:
            st.markdown(f'<div class="er-muted">{filing.source_name}</div>', unsafe_allow_html=True)
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

        _render_quiet_links(filing)

        if candidate is not None:
            with st.expander(_INVESTIGATE_LABEL):
                _render_investigate_body(filing, candidate, on_process, process_ready, on_review_decision, comparison_record)
        else:
            with st.expander(_INVESTIGATE_LABEL):
                _evidence_status_panel(filing, None)
