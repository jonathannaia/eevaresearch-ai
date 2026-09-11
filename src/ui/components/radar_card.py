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
  4) `View filing excerpt` (English/EDGAR) or `View translated filing
     excerpt` / `View original filing excerpt` (Korean/Japanese) —
     compact, display-only toggles, shown only when the corresponding
     stored text exists and (for any original-language/extracted text)
     passes the same quality gate Summary uses. Filing-card machine-
     artifact / excerpt-honesty fix: labels now say "excerpt", not
     "text"/"translation", so a reader understands this is a bounded
     fragment, never the full document; an expanded toggle also shows an
     "Excerpt may be incomplete..." notice whenever
     filing_display.excerpt_may_be_incomplete(candidate.excerpt_original)
     is true (see `_render_expandable_text`'s own docstring);
  5) `Open original filing ↗` — the card's sole action. EDINET is the
     one exception: it has no working direct document link (verified
     live — disclosure2.edinet-fsa.go.jp's per-row PDF action is a
     session-bound JS postback, not a derivable URL), so EDINET cards
     instead render `Search original EDINET filing ↗` linking to the
     official search portal root, plus (unchanged call site, changed
     body — see `_edinet_locator_line`'s own docstring) a fixed, concise
     EDINET lookup-guidance sentence;
  6) a compact "Official filing reference" block, for all three
     providers, built by filing_display.official_filing_reference from
     FilingEvent's own already-stored fields with provider-accurate
     labels (EDINET issuer code / DART issuer code / CIK, Securities
     code, Document ID / Receipt number / Accession number, Filed date)
     — never an EDINET-only label applied to a DART/EDGAR filing.

EDINET's title/Summary/toggle mechanism (items 2-4 above) is entirely
shared with DART — no EDINET-specific branch exists in `display_title`,
the Summary logic, or the toggles below; both sources already go through
the exact same `is_english_native(filing)`-gated code path in
`candidate_row`. The only EDINET-specific rendering is item 5's
original-source-link fallback.

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
quality pass to a second toggle, and further extended by the filing-card
summary/translation presentation fix: every text-reveal toggle on this
card is purely a client-side visibility switch, keyed off
`st.session_state` only — none of them ever calls a translation
provider, writes to CandidateSignal/the database, or queues/retries
anything. `Show English translation`, `View filing text`, and `View
original filing text` are now ALL only ever rendered when the
corresponding stored text exists AND passes the same quality gate
(`is_readable_extracted_text`) — a raw/document-like stored translation
no longer bypasses this gate the way it previously did. When no such
text/translation is stored, or it fails the gate, this card renders no
toggle and no messaging beyond the Summary and the source link — no
"Translation unavailable", no "being prepared", no retry/status/error
wording of any kind.
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


_EDINET_LOOKUP_GUIDANCE = (
    "To find this filing on EDINET, search by EDINET issuer code or "
    "securities code, then filter by filing date and type."
)


def _edinet_locator_line(filing: FilingEvent, filed_label: str | None) -> str | None:
    """EDINET has no working direct document link (disclosure2.edinet-
    fsa.go.jp's per-row "PDF表示" action is a session-bound JS postback
    keyed to an opaque per-render token, not a derivable URL — verified
    live, see the EDINET original-source-link investigation).

    Filing-card machine-artifact / excerpt-honesty fix: this used to
    build a field-listing locator line (filer name, native title, EDINET
    code, securities code, filed date) from `filing` itself. Those code/
    securities-code/filed-date fields now live in the shared, provider-
    neutral `official_filing_reference` block instead (rendered
    separately, for every provider, not just EDINET) — duplicating them
    here too would be redundant. This function now returns the fixed,
    concise EDINET lookup-guidance sentence instead: how to actually use
    those fields on the official search portal. Kept as this same
    function (name, signature, and `_render_quiet_links`'s one call site
    below all unchanged) specifically so `_render_quiet_links` itself
    needed no edit — see that function's own docstring. `filing`/
    `filed_label` are accepted for signature stability but no longer
    read; the guidance sentence is a fixed string true for every EDINET
    filing, not built from this specific filing's own fields."""
    del filing, filed_label
    return _EDINET_LOOKUP_GUIDANCE


def _render_quiet_links(filing: FilingEvent, filed_label: str | None = None) -> None:
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
        locator = _edinet_locator_line(filing, filed_label)
        if locator:
            st.markdown(f'<div class="er-muted" style="margin-top:0.4rem;">{locator}</div>', unsafe_allow_html=True)
        return

    link_cols = st.columns([2, 7])
    with link_cols[0]:
        with st.container(key=f"cta-tertiary-radar-original-{filing.rcept_no}"):
            st.link_button("Open original filing ↗", _public_source_url(filing), use_container_width=True)


_EXCERPT_MAY_BE_INCOMPLETE_NOTICE = "Excerpt may be incomplete. Open the official filing for the full document."


def _render_expandable_text(
    *, toggle_key: str, show_label: str, hide_label: str, section_label: str, text: str, may_be_incomplete: bool = False
) -> None:
    """One compact, display-only show/hide toggle revealing `text` under
    `section_label` when expanded. `st.session_state` here is purely
    ephemeral client-side UI visibility state — never a write to
    CandidateSignal/the database, never a trigger for a translation
    provider or any other service. The toggle is flipped via an
    on_click callback (not an inline check) so the button's own label
    updates on the same rerun it's clicked, matching every other toggle
    in this app.

    `may_be_incomplete` (filing-card excerpt-honesty fix): when True,
    renders `_EXCERPT_MAY_BE_INCOMPLETE_NOTICE` directly below `text`,
    only while this toggle is expanded — never as a standalone message,
    never when collapsed. Driven by the caller's own
    filing_display.excerpt_may_be_incomplete(candidate.excerpt_original)
    check, shared across every excerpt toggle on one card since it is
    always about the same underlying original-language extraction cap,
    regardless of which excerpt (original or translated) is shown."""
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
        if may_be_incomplete:
            st.markdown(
                f'<div class="er-muted" style="margin-top:0.3rem;">{html.escape(_EXCERPT_MAY_BE_INCOMPLETE_NOTICE)}</div>',
                unsafe_allow_html=True,
            )


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

        # Filing-card machine-artifact / excerpt-honesty fix: always
        # driven by the ORIGINAL-language excerpt_original's own length —
        # never a translation's — and shared across every excerpt toggle
        # rendered on this one card below, since it is always the same
        # underlying extraction cap regardless of which excerpt is shown.
        excerpt_original = candidate.excerpt_original if candidate is not None else None
        may_be_incomplete = filing_display.excerpt_may_be_incomplete(excerpt_original)

        if filing_display.is_english_native(filing):
            readable_text = excerpt_original
            passes_gate = bool(readable_text) and filing_display.is_readable_extracted_text(readable_text)
            summary = filing_display.extractive_summary(readable_text) if passes_gate else ""
            if not summary:
                summary = filing_display.metadata_only_summary(filing, title, filed_label)
        else:
            translation_text = (
                candidate.excerpt_translation.translated_text
                if candidate is not None and candidate.excerpt_translation is not None else None
            )
            native_text = excerpt_original
            if filing.source_name == _EDINET_SOURCE_NAME:
                # Filing-card machine-artifact / excerpt-honesty fix:
                # EDINET's inline-XBRL cover page can leak a machine-
                # generated document-title-timestamp label and/or a
                # leading numbered item heading into excerpt_original/
                # excerpt_translation — see filing_display.
                # strip_edinet_machine_artifacts's own comment for the
                # exact evidenced shapes. Applied BEFORE the readability
                # gate and BEFORE extractive_summary(), to both the
                # native and translated text, and EDINET only — DART's
                # own extraction already keeps this class of artifact
                # out (its SECTION-1 cover-page skip), so this cleanup is
                # never invoked for DART, and never touches the stored
                # CandidateSignal — only these two local variables.
                if translation_text:
                    translation_text = filing_display.strip_edinet_machine_artifacts(translation_text)
                if native_text:
                    native_text = filing_display.strip_edinet_machine_artifacts(native_text)

            # Filing-card summary/translation presentation fix: the same
            # readability gate now decides both the Summary source below
            # AND the translated-excerpt toggle further down — previously
            # the toggle rendered whenever translation_text existed at
            # all, regardless of readability, exposing a raw/document-
            # like block. extractive_summary() itself may still return ""
            # (no clean sentence found even within its own bounded search
            # window); that empty-return case, not just an unreadable-
            # source case, also falls back to metadata_only_summary()
            # here — this is the "existing caller" extractive_summary()'s
            # own docstring refers to.
            translation_is_readable = bool(translation_text) and filing_display.is_readable_extracted_text(translation_text)
            summary_source = translation_text if translation_is_readable else None
            summary = filing_display.extractive_summary(summary_source) if summary_source else ""
            if not summary:
                summary = filing_display.metadata_only_summary(filing, title, filed_label)

        st.markdown('<div class="er-muted" style="margin-top:0.5rem;"><strong>Summary</strong></div>', unsafe_allow_html=True)
        st.markdown(f'<div>{html.escape(summary)}</div>', unsafe_allow_html=True)

        if filing_display.is_english_native(filing):
            if passes_gate:
                _render_expandable_text(
                    toggle_key=f"radar-filingtext-{filing.rcept_no}",
                    show_label="View filing excerpt", hide_label="Hide filing excerpt",
                    section_label="Filing excerpt", text=readable_text,
                    may_be_incomplete=may_be_incomplete,
                )
        else:
            if translation_text and translation_is_readable:
                _render_expandable_text(
                    toggle_key=f"radar-translation-expanded-{filing.rcept_no}",
                    show_label="View translated filing excerpt", hide_label="Hide translated filing excerpt",
                    section_label="Translated filing excerpt", text=translation_text,
                    may_be_incomplete=may_be_incomplete,
                )

            if native_text and filing_display.is_readable_extracted_text(native_text):
                _render_expandable_text(
                    toggle_key=f"radar-originaltext-{filing.rcept_no}",
                    show_label="View original filing excerpt", hide_label="Hide original filing excerpt",
                    section_label="Original filing excerpt", text=native_text,
                    may_be_incomplete=may_be_incomplete,
                )

        _render_quiet_links(filing, filed_label)

        # Filing-card machine-artifact / excerpt-honesty fix: a compact,
        # provider-neutral reference block for all three providers.
        # EDINET's own lookup-guidance sentence is NOT re-rendered here —
        # _render_quiet_links above already renders it once, via its own
        # existing, unedited call to _edinet_locator_line (whose body
        # this fix changed, but whose call site inside
        # _render_quiet_links stays untouched — see that function's own
        # docstring). Rendering it again here would duplicate it.
        reference = filing_display.official_filing_reference(filing, filed_label)
        st.markdown(
            '<div class="er-muted" style="margin-top:0.5rem;"><strong>Official filing reference</strong></div>'
            f'<div class="er-muted">{html.escape(reference)}</div>',
            unsafe_allow_html=True,
        )
