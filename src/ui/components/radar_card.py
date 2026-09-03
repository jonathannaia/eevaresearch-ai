"""Radar Inbox's per-item card (Radar simplicity + translation reliability
workstream; layout correction pass — design/DECISIONS.md) — a minimal,
read-only research-feed card showing exactly:

  a) company name and ticker/security code (when available), with the
     filing's own official filed date at the top-right, when known;
  b) English filing title;
  c) `Original` — the original-language filing title and/or a concise
     extracted source excerpt;
  d) a compact `Show English translation` / `Hide English translation`
     toggle, shown only when a translation is already stored — expanding
     it reveals `English translation` and the stored translated text;
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
(DART/EDINET), the "Original" line always shows the extracted excerpt
when one exists, otherwise the filing's own native title.

Layout correction pass (design/DECISIONS.md): the translation toggle is
purely a client-side visibility switch, keyed off `st.session_state`
only — it never calls a translation provider, never writes to
CandidateSignal/the database, and never queues or retries a translation.
It is only ever rendered when a translation is already stored
(`candidate.excerpt_translation`/`candidate.title_translation` is not
None); when no translation is stored, this card renders no toggle and no
messaging beyond the original text and the source link — no "Translation
unavailable", no "being prepared", no retry/status/error wording of any
kind, superseding this card's own earlier "being prepared" messaging
(translation_retry_count/translation_failure_*/translation_next_retry_at
remain read/written elsewhere — src/data_access/translation/
translation_service.py — this card simply no longer surfaces any of that
state to the reader).
"""
from __future__ import annotations

import html
from datetime import datetime

import streamlit as st

from src.models.models import CandidateSignal, FilingEvent
from src.ui.components.radar_status import RadarItem


def _is_english_native(filing: FilingEvent) -> bool:
    return filing.original_language == "English"


def _parse_source_filed_date(raw: str):
    """FilingEvent.rcept_dt is each source's own raw *official filing*
    date (DART's unconverted "YYYYMMDD", EDGAR/EDINET's dashed
    "YYYY-MM-DD") — the same field radar_inbox.py's own "Filed between"
    filter and sort order already treat as the filing's official date.
    Never FilingEvent.retrieved_at, a wholly separate capture/retrieval
    timestamp this function never reads. None on any parse failure or
    empty input — never guessed or substituted."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw.replace("-", ""), "%Y%m%d").date()
    except ValueError:
        return None


def _filed_label(filing: FilingEvent) -> str | None:
    parsed = _parse_source_filed_date(filing.rcept_dt)
    if parsed is None:
        return None
    return f"Filed {parsed.strftime('%b')} {parsed.day}, {parsed.year}"


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


_EDGAR_SOURCE_NAME = "SEC EDGAR"


def _public_source_url(filing: FilingEvent) -> str:
    """EDGAR-only correction: `filing.source_url` is EdgarClient.
    filing_index_url()'s bare accession-directory URL (e.g.
    ".../000104581026000078/") — a raw directory listing, not a useful
    link for a reader. Storage/ingestion (scan_service.py) is untouched;
    this only changes what URL the public card links to.

    Prefers a direct link to the primary filing document, built from
    official EDGAR primary-document metadata (`filing.primary_document`,
    sourced from the submissions API's own `primaryDocument` field — see
    edgar_scan_service.py) — the exact same concatenation
    EdgarClient.fetch_document() itself performs, never a filename
    guessed from the ticker or company name.

    When that metadata is absent, falls back to the official EDGAR
    filing index page — `{accession-no-dashes}-index.htm`, SEC's own
    uniform naming convention for the index page inside every accession
    folder — built from `filing.rcept_no` (the canonical dashed
    accession number every EDGAR FilingEvent already stores, never
    guessed). Still never the bare directory listing.

    DART/EDINET are untouched: neither source's `source_url` ends with
    "/", so both fall through to the unmodified `filing.source_url`."""
    if filing.source_name != _EDGAR_SOURCE_NAME or not filing.source_url.endswith("/"):
        return filing.source_url
    if filing.primary_document:
        return filing.source_url + filing.primary_document
    return f"{filing.source_url}{filing.rcept_no}-index.htm"


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
            st.link_button("Open original filing ↗", _public_source_url(filing), use_container_width=True)


def candidate_row(item: RadarItem, comparison_record=None) -> None:
    """`comparison_record` is accepted for call-site compatibility with
    radar_inbox.py (which still computes a per-page comparison-record bulk
    read) but is no longer used by this card — the public card no longer
    renders a status pill or a Comparison row."""
    filing = item.filing
    candidate = item.candidate

    with st.container(border=True, key=f"radar-item-{filing.rcept_no}"):
        filed_label = _filed_label(filing)
        if filed_label:
            st.markdown(
                '<div style="display:flex; justify-content:space-between; align-items:baseline;">'
                f'<div class="er-muted">{_identity_line(filing)}</div>'
                f'<div class="er-muted">{html.escape(filed_label)}</div>'
                "</div>",
                unsafe_allow_html=True,
            )
        else:
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
                # Display-only visibility switch: st.session_state here is
                # purely ephemeral client-side UI state (which section of
                # this one already-rendered card is visible), never a
                # write to CandidateSignal or any repository, and never a
                # trigger for translation_service — no provider call, no
                # queue, no retry. See on_click below for why the toggle
                # is flipped via a callback rather than an inline check.
                toggle_key = f"radar-translation-expanded-{filing.rcept_no}"
                if toggle_key not in st.session_state:
                    st.session_state[toggle_key] = False

                def _toggle_translation_visibility() -> None:
                    st.session_state[toggle_key] = not st.session_state[toggle_key]

                expanded = st.session_state[toggle_key]
                toggle_label = "Hide English translation" if expanded else "Show English translation"
                with st.container(key=f"cta-tertiary-radar-translation-toggle-{filing.rcept_no}"):
                    st.button(toggle_label, key=f"{toggle_key}-btn", on_click=_toggle_translation_visibility)

                if expanded:
                    st.markdown('<div class="er-muted" style="margin-top:0.4rem;"><strong>English translation</strong></div>', unsafe_allow_html=True)
                    st.markdown(f'<div>{html.escape(translation.translated_text)}</div>', unsafe_allow_html=True)
            # No stored translation: render nothing further beyond the
            # original text above — no toggle, no "Translation
            # unavailable", no "being prepared", no retry/status/error
            # wording of any kind.

        _render_quiet_links(filing)
