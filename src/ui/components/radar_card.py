"""Radar Inbox's per-item card (Radar simplicity + translation reliability
workstream) — a minimal, read-only research-feed card showing exactly:

  a) company name and ticker/security code (when available);
  b) English filing title;
  c) `Original` — the original-language filing title and/or a concise
     extracted source excerpt;
  d) `English translation` — the stored translation of that exact
     displayed original-language content;
  e) `Open original filing ↗` — the card's sole action.

Removed entirely this pass: Why this matters, Memory/theme labels,
detection confidence, Evidence status, Fact/Interpretation/Uncertainty
analyst-view content (Filing overview), Potential materiality, status
pills of any kind (Needs review, Processing deferred, etc.), the
Comparison row, and every other technical/process/review label. None of
the underlying CandidateSignal/FilingEvent fields these used to read are
removed — src/logic/review_actions and src/ui/components/analyst_view
still exist and are still independently unit-tested; they are simply
never called from this public card any more.

For an English-native source (EDGAR), the title *is* the English title,
so no separate Original/English translation pair is ever shown — that
would just duplicate the same English text. For a Korean/Japanese source
(DART/EDINET), the "Original" line and the "English translation" line
always describe the exact same underlying text: the extracted excerpt
when one exists, otherwise the filing's own native title.

Translation-reliability display contract (see
src/data_access/translation/translation_service.py for the retry state
machine this reads): "English translation is being prepared." is shown
if and only if `candidate.translation_next_retry_at` is set — i.e. a
bounded, persisted automatic retry is genuinely still scheduled. A
terminally failed translation (no retry scheduled, whether because the
failure was non-retryable or because the retry cap was reached) shows no
translation section at all — just the original text above it, with no
error jargon or false assurance. The legacy "Translation unavailable"
wording never appears anywhere on this card.
"""
from __future__ import annotations

import html

import streamlit as st

from src.models.models import CandidateSignal, FilingEvent, TranslationState
from src.ui.components.radar_status import RadarItem


def _is_english_native(filing: FilingEvent) -> bool:
    return filing.original_language == "English"


def _identity_line(filing: FilingEvent) -> str:
    identity = html.escape(filing.corp_name)
    if filing.stock_code:
        identity += f" · {html.escape(filing.stock_code)}"
    return identity


def _english_title(filing: FilingEvent, candidate: CandidateSignal | None) -> str:
    if _is_english_native(filing):
        return filing.report_nm
    if candidate is not None and candidate.title_translation is not None:
        return candidate.title_translation.translated_text
    return filing.report_nm


def _native_text(filing: FilingEvent, candidate: CandidateSignal | None) -> str:
    if candidate is not None and candidate.excerpt_original:
        return candidate.excerpt_original
    return filing.report_nm


def _translation_for_native_text(candidate: CandidateSignal | None):
    if candidate is None:
        return None
    if candidate.excerpt_original:
        return candidate.excerpt_translation
    return candidate.title_translation


def _render_quiet_links(filing: FilingEvent) -> None:
    """The card's one action: opening the official source URL in a new
    tab (st.link_button's native behavior) — de-emphasized (the same
    cta-tertiary-* ghost treatment used everywhere else in the app for a
    low-emphasis action), never a form control. The label is the exact
    fixed string the approved spec calls for — no source name, jurisdiction,
    or other technical detail interpolated into it."""
    link_cols = st.columns([2, 7])
    with link_cols[0]:
        with st.container(key=f"cta-tertiary-radar-original-{filing.rcept_no}"):
            st.link_button("Open original filing ↗", filing.source_url, use_container_width=True)


def candidate_row(item: RadarItem, show_full_status: bool = False, comparison_record=None) -> None:
    """`show_full_status`/`comparison_record` are accepted for call-site
    compatibility with radar_inbox.py (which still computes a per-page
    comparison-record bulk read and a view-mode flag for its own filtering
    logic) but are no longer used by this card — the public card no longer
    renders a status pill or a Comparison row in any view."""
    filing = item.filing
    candidate = item.candidate

    with st.container(border=True, key=f"radar-item-{filing.rcept_no}"):
        st.markdown(f'<div class="er-muted">{_identity_line(filing)}</div>', unsafe_allow_html=True)

        english_title = _english_title(filing, candidate)
        st.markdown(f'<div class="er-card-title" style="margin-top:0.3rem;">{html.escape(english_title)}</div>', unsafe_allow_html=True)

        english_native = _is_english_native(filing)
        if english_native:
            excerpt = candidate.excerpt_original if candidate is not None else None
            if excerpt:
                st.markdown(f'<div style="margin-top:0.4rem;">{html.escape(excerpt)}</div>', unsafe_allow_html=True)
        else:
            native_text = _native_text(filing, candidate)
            translation = _translation_for_native_text(candidate)

            st.markdown('<div class="er-muted" style="margin-top:0.5rem;"><strong>Original</strong></div>', unsafe_allow_html=True)
            st.markdown(f'<div>{html.escape(native_text)}</div>', unsafe_allow_html=True)

            if translation is not None:
                st.markdown('<div class="er-muted" style="margin-top:0.4rem;"><strong>English translation</strong></div>', unsafe_allow_html=True)
                st.markdown(f'<div>{html.escape(translation.translated_text)}</div>', unsafe_allow_html=True)
            elif (
                candidate is not None
                and candidate.translation_state == TranslationState.UNAVAILABLE
                and candidate.translation_next_retry_at
            ):
                # A persisted automatic retry is genuinely still scheduled
                # (translation_service.translation_retry_eligible's own
                # data) — never shown for a terminal failure or a candidate
                # that was never attempted at all.
                st.markdown(
                    '<div class="er-muted" style="margin-top:0.4rem;">English translation is being prepared.</div>',
                    unsafe_allow_html=True,
                )
                # Terminal failure (translation_state is UNAVAILABLE and no
                # retry is scheduled): render nothing further — the
                # original text above already stands alone, with no error
                # jargon or false assurance.

        _render_quiet_links(filing)
