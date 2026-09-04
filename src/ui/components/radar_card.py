"""Radar Inbox's per-item card (Radar simplicity + translation reliability
workstream; layout correction pass; filing-quality pass — design/
DECISIONS.md) — a minimal, read-only research-feed card showing exactly:

  1) company name and ticker/security code (when available), with the
     filing's own official filed date at the top-right, when known;
  2) a clean, deterministic, source-safe display title (see
     src/logic/filing_display.display_title — for EDGAR, a readable
     mapping from the official SEC form type, e.g. "Quarterly Report —
     Form 10-Q"; for DART/EDINET, unchanged: the stored title
     translation when one exists, otherwise the native official title);
  3) `Summary` — a concise extractive summary grounded only in stored,
     quality-gated readable text, or (whenever no such text exists) a
     neutral, factual "{Company} filed {title} on {date}." sentence —
     see src/logic/filing_display for the exact quality gate and
     fallback wording;
  4) `View filing text` (English/EDGAR) or `Show English translation` /
     `View original filing text` (Korean/Japanese) — compact,
     display-only toggles, shown only when the corresponding stored text
     exists and (for any original-language/extracted text) passes the
     same quality gate Summary uses;
  5) `Open original filing ↗` — the card's sole action. EDINET is the
     one exception: it has no working direct document link (verified
     live — disclosure2.edinet-fsa.go.jp's per-row PDF action is a
     session-bound JS postback, not a derivable URL), so EDINET cards
     instead render `Search original EDINET filing ↗` linking to the
     official search portal root, plus a non-clickable locator line
     naming the fields (EDINET code, securities code, filed date,
     title) needed to re-find the filing there. See
     `_render_quiet_links`.

Filing-quality pass (design/DECISIONS.md): some EDGAR filings store an
extraction dominated by raw XML/XBRL markup, taxonomy namespace prefixes,
and machine identifiers instead of readable prose (e.g. a Marvell-style
Form 10-Q) — that text must never reach a reader. Every place this card
would have shown stored extracted text verbatim now goes through
src.logic.filing_display.is_readable_extracted_text first; text that
fails degrades to the neutral metadata-only Summary and no filing-text
toggle, never a raw dump.

Removed entirely (Radar simplicity workstream): Why this matters,
Memory/theme labels, detection confidence, Evidence status, Fact/
Interpretation/Uncertainty analyst-view content (Filing overview),
Potential materiality, status pills of any kind (Needs review,
Processing deferred, etc.), the Comparison row, and every other
technical/process/review label. None of the underlying CandidateSignal/
FilingEvent fields these used to read are removed — src/logic/
review_actions and src/ui/components/analyst_view still exist and are
still independently unit-tested; they are simply never called from this
public card any more.

Layout correction pass (design/DECISIONS.md), extended by the filing-
quality pass to a second toggle: every text-reveal toggle on this card
is purely a client-side visibility switch, keyed off `st.session_state`
only — none of them ever calls a translation provider, writes to
CandidateSignal/the database, or queues/retries anything. `Show English
translation` is only ever rendered when a translation is already stored
(`candidate.excerpt_translation` is not None); `View filing text`/`View
original filing text` are only ever rendered when the corresponding
stored text exists AND passes the quality gate. When no such text/
translation is stored, this card renders no toggle and no messaging
beyond the Summary and the source link — no "Translation unavailable",
no "being prepared", no retry/status/error wording of any kind.
"""
from __future__ import annotations

import html
from datetime import datetime

import streamlit as st

from src.logic import filing_display
from src.models.models import FilingEvent
from src.ui.components.radar_status import RadarItem


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
    return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year}"


def _identity_line(filing: FilingEvent) -> str:
    identity = html.escape(filing.corp_name)
    if filing.stock_code:
        identity += f" · {html.escape(filing.stock_code)}"
    return identity


_EDGAR_SOURCE_NAME = "SEC EDGAR"
_EDINET_SOURCE_NAME = "EDINET"
_EDINET_SEARCH_URL = "https://disclosure2.edinet-fsa.go.jp/"


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


def _edinet_locator_line(filing: FilingEvent, title: str, filed_label: str | None) -> str | None:
    """EDINET has no working direct document link (disclosure2.edinet-
    fsa.go.jp's per-row "PDF表示" action is a session-bound JS postback
    keyed to an opaque per-render token, not a derivable URL — verified
    live, see the EDINET original-source-link investigation). This line
    gives a reader the exact fields to re-find the filing themselves on
    the official search portal. Built only from fields already present
    on `filing`/the already-computed display `title` — never queries an
    API, never infers a code, and omits any item that's simply absent
    rather than showing a placeholder or the private API URL."""
    parts = []
    if filing.corp_code:
        parts.append(f"EDINET code {html.escape(filing.corp_code)}")
    if filing.stock_code:
        parts.append(f"Securities code {html.escape(filing.stock_code)}")
    if filed_label:
        parts.append(f"Filed {html.escape(filed_label)}")
    if title:
        parts.append(html.escape(title))
    if not parts:
        return None
    return "Official EDINET search: " + " · ".join(parts)


def _render_quiet_links(filing: FilingEvent, title: str = "", filed_label: str | None = None) -> None:
    """The card's one action: opening the official source URL in a new
    tab (st.link_button's native behavior) — de-emphasized (the same
    cta-tertiary-* ghost treatment used everywhere else in the app for a
    low-emphasis action), never a form control. The label is the exact
    fixed string the approved spec calls for — no source name, jurisdiction,
    or other technical detail interpolated into it.

    EDINET carries no working direct document link (see
    `_edinet_locator_line`'s docstring), so it renders a different,
    honest action instead: a link to the official public search portal
    root, plus a locator line naming the fields a reader needs to find
    this exact filing there. `filing.source_url`/api.edinet-fsa.go.jp is
    never rendered as a clickable EDINET link, and no credential, query
    parameter, or document token is ever attached to the portal link."""
    if filing.source_name == _EDINET_SOURCE_NAME:
        link_cols = st.columns([2, 7])
        with link_cols[0]:
            with st.container(key=f"cta-tertiary-radar-original-{filing.rcept_no}"):
                st.link_button("Search original EDINET filing ↗", _EDINET_SEARCH_URL, use_container_width=True)
        locator = _edinet_locator_line(filing, title, filed_label)
        if locator:
            st.markdown(f'<div class="er-muted" style="margin-top:0.4rem;">{locator}</div>', unsafe_allow_html=True)
        return

    link_cols = st.columns([2, 7])
    with link_cols[0]:
        with st.container(key=f"cta-tertiary-radar-original-{filing.rcept_no}"):
            st.link_button("Open original filing ↗", _public_source_url(filing), use_container_width=True)


def _render_expandable_text(*, toggle_key: str, show_label: str, hide_label: str, section_label: str, text: str) -> None:
    """One compact, display-only show/hide toggle revealing `text` under
    `section_label` when expanded. `st.session_state` here is purely
    ephemeral client-side UI visibility state — never a write to
    CandidateSignal/the database, never a trigger for a translation
    provider or any other service. The toggle is flipped via an
    on_click callback (not an inline check) so the button's own label
    updates on the same rerun it's clicked, matching every other toggle
    in this app."""
    if toggle_key not in st.session_state:
        st.session_state[toggle_key] = False

    def _toggle() -> None:
        st.session_state[toggle_key] = not st.session_state[toggle_key]

    expanded = st.session_state[toggle_key]
    label = hide_label if expanded else show_label
    with st.container(key=f"cta-tertiary-{toggle_key}"):
        st.button(label, key=f"{toggle_key}-btn", on_click=_toggle)

    if expanded:
        st.markdown(f'<div class="er-muted" style="margin-top:0.4rem;"><strong>{html.escape(section_label)}</strong></div>', unsafe_allow_html=True)
        st.markdown(f'<div>{html.escape(text)}</div>', unsafe_allow_html=True)


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
                f'<div class="er-muted">Filed {html.escape(filed_label)}</div>'
                "</div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(f'<div class="er-muted">{_identity_line(filing)}</div>', unsafe_allow_html=True)

        title = filing_display.display_title(filing, candidate)
        st.markdown(f'<div class="er-card-title" style="margin-top:0.3rem;">{html.escape(title)}</div>', unsafe_allow_html=True)

        if filing_display.is_english_native(filing):
            readable_text = candidate.excerpt_original if candidate is not None else None
            passes_gate = bool(readable_text) and filing_display.is_readable_extracted_text(readable_text)
            summary = (
                filing_display.extractive_summary(readable_text) if passes_gate
                else filing_display.metadata_only_summary(filing, title, filed_label)
            )
        else:
            translation_text = (
                candidate.excerpt_translation.translated_text
                if candidate is not None and candidate.excerpt_translation is not None else None
            )
            summary_source = translation_text if translation_text and filing_display.is_readable_extracted_text(translation_text) else None
            summary = (
                filing_display.extractive_summary(summary_source) if summary_source
                else filing_display.metadata_only_summary(filing, title, filed_label)
            )

        st.markdown('<div class="er-muted" style="margin-top:0.5rem;"><strong>Summary</strong></div>', unsafe_allow_html=True)
        st.markdown(f'<div>{html.escape(summary)}</div>', unsafe_allow_html=True)

        if filing_display.is_english_native(filing):
            if passes_gate:
                _render_expandable_text(
                    toggle_key=f"radar-filingtext-{filing.rcept_no}",
                    show_label="View filing text", hide_label="Hide filing text",
                    section_label="Filing text", text=readable_text,
                )
        else:
            if translation_text:
                _render_expandable_text(
                    toggle_key=f"radar-translation-expanded-{filing.rcept_no}",
                    show_label="Show English translation", hide_label="Hide English translation",
                    section_label="English translation", text=translation_text,
                )

            native_text = candidate.excerpt_original if candidate is not None else None
            if native_text and filing_display.is_readable_extracted_text(native_text):
                _render_expandable_text(
                    toggle_key=f"radar-originaltext-{filing.rcept_no}",
                    show_label="View original filing text", hide_label="Hide original filing text",
                    section_label="Original filing text", text=native_text,
                )

        _render_quiet_links(filing, title, filed_label)
