"""Radar Inbox's per-item card (Radar simplicity + translation reliability
workstream; layout correction pass; filing-quality pass; Dashboard/
Filings usability pass — design/DECISIONS.md) — a minimal, read-only
research-feed card showing exactly:

  1) company name and ticker/security code (when available), with the
     filing's own official filed date at the top-right, when known;
  2) a clean, deterministic, source-safe display title (see
     src/logic/filing_display.display_title — for EDGAR, a readable
     mapping from the official SEC form type, e.g. "Quarterly Report —
     Form 10-Q"; for DART/EDINET, the stored title translation or the
     native official title, selected by each card's own Original/English
     toggle state — see below);
  3) `Filing summary` — a concise extractive summary grounded only in
     stored, quality-gated readable text (original or translated,
     following the same Original/English toggle state as the title), or
     (whenever no such text exists) a neutral, factual "{Company} filed
     {title} on {date}." sentence — see src/logic/filing_display for the
     exact quality gate and fallback wording. A compact `Item X.XX` label
     renders beside the heading only when candidate.evidence_location
     already, reliably records a genuine Item-header anchor for this
     EDGAR excerpt (filing_display.item_anchor_label) — read from
     already-persisted evidence metadata only, never derived from raw
     text here;
  4) for DART/EDINET only, an `Original`/`English` toggle for the title +
     Filing summary pair — rendered only when a title translation is
     actually stored. DART defaults to English (unchanged from before
     this pass); EDINET defaults to Original (changed by this pass — see
     module-level default_show_translated below). SEC/EDGAR cards never
     request a translation and never show this toggle;
  5) `View filing excerpt` (English/EDGAR) or `View translated filing
     excerpt` / `View original filing excerpt` (Korean/Japanese) —
     compact, display-only toggles, shown only when the corresponding
     stored text exists and (for any original-language/extracted text)
     passes the same quality gate Filing summary uses. These are
     independent of the item-4 title/summary language toggle — both the
     translated and original full excerpts stay separately reachable
     regardless of which language the title/summary currently show.
     Filing-card machine-artifact / excerpt-honesty fix: labels say
     "excerpt", not "text"/"translation", so a reader understands this is
     a bounded fragment, never the full document; an expanded toggle also
     shows an "Excerpt may be incomplete..." notice whenever
     filing_display.excerpt_may_be_incomplete(candidate.excerpt_original)
     is true (see `_render_expandable_text`'s own docstring);
  6) `Open original filing ↗` — the card's sole action. EDINET is the
     one exception: it has no working direct document link (verified
     live — disclosure2.edinet-fsa.go.jp's per-row PDF action is a
     session-bound JS postback, not a derivable URL), so EDINET cards
     instead render `Search original EDINET filing ↗` linking to the
     official search portal root, plus (unchanged call site, changed
     body — see `_edinet_locator_line`'s own docstring) a fixed, concise
     EDINET lookup-guidance sentence;
  7) a compact "Official filing reference" block, for all three
     providers, built by filing_display.official_filing_reference from
     FilingEvent's own already-stored fields with provider-accurate
     labels (EDINET issuer code / DART issuer code / CIK, Securities
     code, Document ID / Receipt number / Accession number, Filed date)
     — never an EDINET-only label applied to a DART/EDGAR filing.

Dashboard/Filings usability pass (design/DECISIONS.md): DART and EDINET
share the exact same title/Filing-summary/toggle mechanism (items 2-4
above) — no EDINET-specific branch beyond which language is the default
(`default_show_translated`); both sources already go through the exact
same `is_english_native(filing)`-gated code path in `candidate_row`. This
toggle is purely a client-side, `st.session_state`-keyed display switch
over ALREADY-STORED title_translation/excerpt_translation — it never
calls the translation provider, never writes to CandidateSignal/the
database, and never triggers a new translation attempt; DART/EDINET's
own pipelines still translate automatically at document-processing time,
completely unchanged. The only EDINET-specific rendering beyond the
default is item 6's original-source-link fallback.

Filing-quality pass (design/DECISIONS.md), extended by the Dashboard/
Filings usability pass: some EDGAR filings store an extraction dominated
by raw XML/XBRL markup, taxonomy namespace prefixes, and machine
identifiers instead of readable prose (e.g. a Marvell-style Form 10-Q) —
that text must never reach a reader. Every place this card would have
shown stored extracted text verbatim now goes through
src.logic.filing_display.is_readable_extracted_text first; text that
fails degrades to the neutral metadata-only Filing summary and no
filing-text toggle, never a raw dump. A second, narrower case — a
gate-passing but non-substantive 8-K cover-page prefix — is handled by
filing_display.prefer_metadata_only_summary(candidate.evidence_location),
gated strictly on a positive, already-persisted "confirmed unanchored"
signal (never on the mere absence of one — see that function's own
docstring for why).

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
quality pass to a second toggle, further extended by the filing-card
summary/translation presentation fix, and further extended by the
Dashboard/Filings usability pass's own title/summary language toggle:
every text-reveal/language toggle on this card is purely a client-side
visibility switch, keyed off `st.session_state` only — none of them ever
calls a translation provider, writes to CandidateSignal/the database, or
queues/retries anything. `View filing excerpt`, `View translated filing
excerpt`/`View original filing excerpt`, and the title/summary
`Original`/`English` toggle are ALL only ever rendered when the
corresponding stored text/translation exists AND (for the excerpt
toggles) passes the same quality gate (`is_readable_extracted_text`) — a
raw/document-like stored translation no longer bypasses this gate the
way it previously did. When no such text/translation is stored, or it
fails the gate, this card renders no toggle and no messaging beyond the
Filing summary and the source link — no "Translation unavailable", no
"being prepared", no retry/status/error wording of any kind.
"""
from __future__ import annotations

import html
from datetime import datetime

import streamlit as st

from src.logic import filing_display
from src.logic.source_link import public_source_url
from src.models.models import FilingEvent
from src.ui.components import radar_status
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

    DART is untouched: its own `source_url` never ends with "/", so it
    falls through to the unmodified `filing.source_url`. EDINET-safety
    fix (design/DECISIONS.md): every branch's result — including DART's
    and EDGAR's own unmodified `filing.source_url` fall-throughs — is
    finally passed through src.logic.source_link.public_source_url()
    below, consolidating what used to be this file's own separate,
    bespoke `_EDINET_SEARCH_URL`-based branch in `_render_quiet_links`
    into the one shared helper every other source-link rendering surface
    in this app now also uses. For EDINET, `filing.source_url` is always
    the raw, key-required api.edinet-fsa.go.jp document endpoint (see
    EdinetClient.document_index_url()'s own docstring), so this line is
    what actually rewrites it to the public portal root today — the
    EDGAR-specific rewriting above never applies to an EDINET URL, since
    api.edinet-fsa.go.jp URLs never end in "/"."""
    if filing.source_name == _EDGAR_SOURCE_NAME and filing.source_url.endswith("/"):
        if filing.primary_document:
            resolved = filing.source_url + filing.primary_document
        else:
            resolved = f"{filing.source_url}{filing.rcept_no}-index.htm"
    else:
        resolved = filing.source_url
    return public_source_url(resolved) or resolved


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
    root (via `_public_source_url`, which now consolidates the EDINET
    URL-safety rewrite — see that function's own docstring), plus a
    locator line naming the fields a reader needs to find this exact
    filing there. `filing.source_url`/api.edinet-fsa.go.jp is never
    rendered as a clickable EDINET link, and no credential, query
    parameter, or document token is ever attached to the portal link."""
    if filing.source_name == _EDINET_SOURCE_NAME:
        link_cols = st.columns([2, 7])
        with link_cols[0]:
            with st.container(key=f"cta-tertiary-radar-original-{filing.rcept_no}"):
                st.link_button("Search original EDINET filing ↗", _public_source_url(filing), use_container_width=True)
        locator = _edinet_locator_line(filing, filed_label)
        if locator:
            st.markdown(f'<div class="er-muted" style="margin-top:0.4rem;">{locator}</div>', unsafe_allow_html=True)
        return

    link_cols = st.columns([2, 7])
    with link_cols[0]:
        with st.container(key=f"cta-tertiary-radar-original-{filing.rcept_no}"):
            st.link_button("Open original filing ↗", _public_source_url(filing), use_container_width=True)


_EXCERPT_MAY_BE_INCOMPLETE_NOTICE = "Excerpt may be incomplete. Open the official filing for the full document."
# Excerpt-display-trim fix (design/DECISIONS.md): shown in place of the
# raw excerpt only when may_be_incomplete is True AND
# filing_display.trim_excerpt_for_display() found no safe complete-
# sentence boundary anywhere in the text — never a fabricated claim
# about the filing's content, just an honest statement that no safe
# excerpt could be shown. The existing "Excerpt may be incomplete..."
# notice still renders directly below this, unchanged.
_NO_SAFE_EXCERPT_BOUNDARY_FALLBACK = "A complete excerpt could not be safely determined for this filing."


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

    `may_be_incomplete` (filing-card excerpt-honesty fix, extended by the
    excerpt-display-trim fix): when True, the displayed `text` is first
    passed through filing_display.trim_excerpt_for_display(), which cuts
    it back to its own last complete sentence boundary — discarding only
    a trailing partial-sentence fragment, never a complete sentence,
    never `text` itself (the caller's own stored CandidateSignal is
    never touched, only this local render). When no safe boundary exists
    at all, `_NO_SAFE_EXCERPT_BOUNDARY_FALLBACK` is shown instead of the
    raw, possibly-fragmentary text. `_EXCERPT_MAY_BE_INCOMPLETE_NOTICE`
    still renders directly below either outcome, only while this toggle
    is expanded — never as a standalone message, never when collapsed.
    When `may_be_incomplete` is False, `text` is rendered completely
    unchanged, even if it happens to lack terminal punctuation — trimming
    only ever applies to an excerpt already believed possibly cut by the
    shared extraction cap (see
    filing_display.excerpt_may_be_incomplete(candidate.excerpt_original)),
    shared across every excerpt toggle on one card since it is always
    about the same underlying cap, regardless of which excerpt (original
    or translated) is shown."""
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
        display_text = text
        if may_be_incomplete:
            display_text = filing_display.trim_excerpt_for_display(text) or _NO_SAFE_EXCERPT_BOUNDARY_FALLBACK
        st.markdown(f'<div>{html.escape(display_text)}</div>', unsafe_allow_html=True)
        if may_be_incomplete:
            st.markdown(
                f'<div class="er-muted" style="margin-top:0.3rem;">{html.escape(_EXCERPT_MAY_BE_INCOMPLETE_NOTICE)}</div>',
                unsafe_allow_html=True,
            )


def _toggle_title_language(rcept_no: str) -> None:
    key = f"radar-titlelang-{rcept_no}"
    st.session_state[key] = not st.session_state.get(key, True)


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

        # Dashboard/Filings usability pass (design/DECISIONS.md): DART/
        # EDINET's Original/English title+Filing-summary toggle state.
        # SEC/EDGAR never requests a translation, so has_translation is
        # always False there and this control never renders for it.
        is_english = filing_display.is_english_native(filing)
        # Title and excerpt translations are tracked, and can succeed/fail,
        # completely independently (CandidateSignal's own docstring) —
        # the shared toggle below renders whenever EITHER exists, but
        # display_title()/the Summary-source selection each still check
        # their own specific translation field, so a title-only or
        # excerpt-only translation degrades correctly on its own half.
        has_title_translation = candidate is not None and candidate.title_translation is not None
        has_excerpt_translation = candidate is not None and candidate.excerpt_translation is not None
        has_any_translation = has_title_translation or has_excerpt_translation
        title_toggle_key = f"radar-titlelang-{filing.rcept_no}"
        if not is_english:
            # DART preserves its existing English-first default; EDINET's
            # default flips to original-language-first (approved product
            # decision) — the one deliberate difference between the two
            # sources in this whole card.
            default_show_translated = filing.source_name != _EDINET_SOURCE_NAME
            if title_toggle_key not in st.session_state:
                st.session_state[title_toggle_key] = default_show_translated
            show_translated = st.session_state[title_toggle_key] if has_any_translation else False
        else:
            show_translated = True  # irrelevant: display_title() ignores this for an English-native filing

        title = filing_display.display_title(filing, candidate, prefer_translated=show_translated)
        st.markdown(f'<div class="er-card-title" style="margin-top:0.3rem;">{html.escape(title)}</div>', unsafe_allow_html=True)

        # "Review needed" badge (design/DECISIONS.md) — advisory only,
        # never blocks or delays publication; see filing_display.
        # review_needed()'s own docstring for exactly what triggers it.
        review_needed_tag = radar_status.review_needed_tag_html(item)
        if review_needed_tag:
            st.markdown(f'<div style="margin-top:0.3rem;">{review_needed_tag}</div>', unsafe_allow_html=True)

        # Filing-card machine-artifact / excerpt-honesty fix: always
        # driven by the ORIGINAL-language excerpt_original's own length —
        # never a translation's — and shared across every excerpt toggle
        # rendered on this one card below, since it is always the same
        # underlying extraction cap regardless of which excerpt is shown.
        excerpt_original = candidate.excerpt_original if candidate is not None else None
        may_be_incomplete = filing_display.excerpt_may_be_incomplete(excerpt_original)

        item_label = None
        if is_english:
            readable_text = excerpt_original
            passes_gate = bool(readable_text) and filing_display.is_readable_extracted_text(readable_text)
            # Filing-card boilerplate fix: a gate-passing but reliably-
            # confirmed-unanchored 8-K excerpt (see filing_display.
            # prefer_metadata_only_summary's own docstring) prefers the
            # neutral fallback even though it would otherwise pass the
            # readability gate — never based on the mere absence of an
            # anchor, only a positive, already-persisted signal.
            evidence_location = candidate.evidence_location if candidate is not None else None
            prefer_fallback = filing_display.prefer_metadata_only_summary(evidence_location)
            summary = filing_display.extractive_summary(readable_text) if (passes_gate and not prefer_fallback) else ""
            if not summary:
                summary = filing_display.metadata_only_summary(filing, title, filed_label)
            item_label = filing_display.item_anchor_label(evidence_location)
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

            # Filing-card summary/translation presentation fix, extended
            # by the Dashboard/Filings usability pass: the Filing summary
            # source now follows the same Original/English toggle state
            # as the title — `show_translated` True (DART's default, or
            # EDINET after an explicit toggle) prefers the translated
            # excerpt exactly as before this pass; False (EDINET's new
            # default, or DART after an explicit toggle) prefers the
            # native excerpt instead, its own mirror-image behavior.
            # extractive_summary() itself may still return "" (no clean
            # sentence found even within its own bounded search window);
            # that empty-return case, not just an unreadable-source case,
            # also falls back to metadata_only_summary() here.
            translation_is_readable = bool(translation_text) and filing_display.is_readable_extracted_text(translation_text)
            native_is_readable = bool(native_text) and filing_display.is_readable_extracted_text(native_text)
            if not has_any_translation:
                # No stored translation at all (neither title nor
                # excerpt) — unchanged pre-existing behavior: the Filing
                # summary never shows the raw native excerpt directly,
                # only a translated summary or the neutral fallback (the
                # native excerpt stays reachable only via its own
                # separate, explicit "View original filing excerpt"
                # toggle below). The new native-as-summary preference
                # only applies once some translation exists and the
                # toggle is explicitly set to Original.
                summary_source = None
            elif show_translated:
                summary_source = translation_text if translation_is_readable else None
            else:
                summary_source = native_text if native_is_readable else None
            summary = filing_display.extractive_summary(summary_source) if summary_source else ""
            if not summary:
                summary = filing_display.metadata_only_summary(filing, title, filed_label)

        summary_label_html = '<div class="er-muted" style="margin-top:0.5rem; display:flex; align-items:center; gap:0.4rem;"><strong>Filing summary</strong>'
        if item_label:
            summary_label_html += f'<span class="er-status-tag er-tag-neutral">{html.escape(item_label)}</span>'
        summary_label_html += "</div>"
        st.markdown(summary_label_html, unsafe_allow_html=True)
        st.markdown(f'<div>{html.escape(summary)}</div>', unsafe_allow_html=True)

        if not is_english and has_any_translation:
            toggle_label = "Original" if show_translated else "English"
            with st.container(key=f"cta-tertiary-titlelang-{filing.rcept_no}"):
                st.button(
                    toggle_label, key=f"radar-titlelang-{filing.rcept_no}-btn",
                    on_click=_toggle_title_language, args=(filing.rcept_no,),
                )

        if is_english:
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

            if native_text and native_is_readable:
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
