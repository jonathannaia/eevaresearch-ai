"""Filing overview — Phase 1, "evidence-first filing overview": the first
structured, human-readable section inside a processed Radar candidate's
"Details," built on the real DART/SK Hynix processed filing that proved
the underlying pipeline works (see design/DECISIONS.md). Every sentence
here is either copied verbatim from a structured FilingEvent field or
selected from a small, hand-reviewed template table keyed by the
candidate's own matched category — never generated, never a guess, never
a market judgment, price target, rating, or action recommendation.
Reuses this app's existing Fact/Interpretation/Inference/Uncertainty
vocabulary (evidence_chips.py) rather than inventing a new one.

Phase 1 explicitly does NOT read or summarize the excerpt's own content —
it states filing metadata (issuer, title, source, date) and the matched
category/keyword only. It must never be presented as, or mistaken for, a
substantive summary of what the filing text actually says — that is
Phase 2 (a separate, not-yet-approved task: an evidence-grounded
substantive summary from the extracted filing text, with strict source
citations and no invented claims). The "Filing overview" heading itself
carries this caveat so the distinction is visible in the UI, not just in
this docstring.

Deliberately renders only when there is real extracted text to reason
about (`should_render_analyst_view`) — a deferred, failed, or
not-yet-processed candidate gets none of this, same "never fabricate
what wasn't actually retrieved" discipline as the rest of Radar Inbox.
"""
from __future__ import annotations

import streamlit as st

from src.models.models import CandidateSignal, ClaimType, ExtractionState, FilingEvent, TranslationState
from src.ui.components.evidence_chips import evidence_chip_html

_DART_SOURCE = "OpenDART / DART"
_EDINET_SOURCE = "EDINET"

# --- "What is unconfirmed" — deterministic, per-category, hand-reviewed.
# Only one real category has a specific template so far (the one this was
# built against — a real DART market_rumor_response filing). Every other
# category, from any source, gets the same honest fallback rather than a
# guessed template.
_UNCONFIRMED_TEMPLATES: dict[str, str] = {
    "market_rumor_response": (
        "This filing is a disclosure inquiry or response about reported information. "
        "It does not confirm that a transaction has occurred."
    ),
}
_UNCONFIRMED_FALLBACK = (
    "This filing type has no specific uncertainty template yet. "
    "Read the source excerpt directly before drawing conclusions."
)

# --- "Follow-up evidence to watch" — deterministic checklist only, no
# AI-generated synthesis or predictions. Every item names a piece of
# evidence that could later confirm/clarify the filing, never an action.
_FOLLOWUP_TEMPLATES: dict[str, list[str]] = {
    "market_rumor_response": [
        "A formal company response or clarification",
        "A subsequent filing that confirms or denies the reported matter",
        "An amendment or related disclosure",
    ],
}
_FOLLOWUP_FALLBACK: list[str] = [
    "Review subsequent company filings or official statements related to this disclosure.",
]

# --- "What happened" — gated on a simple, source-neutral excerpt-length
# check, not ExcerptQuality. ExcerptQuality is DART-only (never computed
# for EDGAR/EDINET — confirmed by grep, not assumed) and is a coarse,
# single-marker-anywhere-fails-the-whole-excerpt heuristic even within
# DART (its own docstring: "never a materiality score... not an attempt
# at real text-quality classification") — verified concretely against a
# real Samsung candidate, where one incidental table-of-contents match
# ("대표이사" inside "【대표이사 등의 확인】") flagged an otherwise
# substantive 600-char excerpt as LIKELY_BOILERPLATE. The structured-
# facts sentence below never reads the excerpt's content at all — only
# FilingEvent fields and matched_rules — so its accuracy never actually
# depended on excerpt quality; gating on ExcerptQuality was solving a
# problem that didn't apply to it. ExcerptQuality remains visible as
# informational metadata in radar_card.py's "Technical details" — it
# just no longer decides whether this section renders.
_MIN_SUBSTANTIVE_EXCERPT_CHARS = 40

# Verbatim fallback whenever the excerpt is shorter than the threshold
# above. Never invented per filing — this exact sentence, unchanged,
# every time.
_INSUFFICIENT_EXCERPT_TEXT = (
    "The filing was detected, but the available excerpt is not sufficient "
    "to summarize the disclosure reliably. Read the original filing."
)

# --- "Why it matters" — deliberately sparse and conditional, per the
# approved plan: only rendered when a real, hand-written per-category
# template exists. Every other category gets nothing here, never a
# generic hedge invented to fill the section.
_WHY_IT_MATTERS_TEMPLATES: dict[str, str] = {
    "market_rumor_response": (
        "This may matter because it is a company's formal response to reported "
        "information — not yet a confirmed transaction."
    ),
}


def should_render_analyst_view(candidate: CandidateSignal) -> bool:
    """The only gate: real extracted text must exist. Deliberately not
    gated on translation, review, or materiality — a Korean-only excerpt
    with no translation yet is still real source text worth structuring."""
    return candidate.extraction_state == ExtractionState.EXTRACTED and bool(candidate.excerpt_original)


def _matched_category(matched_rules: list[str]) -> str | None:
    """The first real (non-amendment-marker) matched category — matching
    the same category a candidate's confidence score was actually
    computed from. `amendment_or_correction` is a modifier, not a
    category of its own (see dart_rules.py), so it's skipped here."""
    for rule in matched_rules:
        if rule == "amendment_or_correction":
            continue
        return rule.split(":", 1)[0]
    return None


def _category_label(category: str) -> str:
    return category.replace("_", " ").capitalize()


def _why_entered_radar_phrases(source_name: str, matched_rules: list[str]) -> list[str]:
    """Source-aware on purpose: DART/EDGAR's matched_rules entries name a
    real native/documented *keyword or item number* (see
    dart_rules.py/edgar_rules.py) — "keyword match" is an accurate
    description there. EDINET's entries name a *routing code triplet*
    (ordinanceCode:formCode:docTypeCode — see edinet_rules.py), never a
    keyword — calling that a "keyword match" would misrepresent what was
    actually matched, so EDINET gets its own, honestly-worded phrasing."""
    if source_name == _EDINET_SOURCE:
        phrases: list[str] = []
        for rule in matched_rules:
            parts = rule.split(":", 1)
            if len(parts) == 2:
                category, code = parts
                phrases.append(f"{_category_label(category)} — matched by filing type/form code ({code})")
            else:
                phrases.append(rule)
        return phrases

    phrases = []
    for rule in matched_rules:
        if rule == "amendment_or_correction":
            phrases.append("Amends or corrects an earlier filing")
            continue
        parts = rule.split(":", 2)
        if len(parts) == 3:
            category, _rule_name, keyword = parts
            phrases.append(f"{_category_label(category)} — matched keyword “{keyword}”")
        elif len(parts) == 2:
            category, detail = parts
            phrases.append(f"{_category_label(category)} ({detail})")
        else:
            phrases.append(rule)
    return phrases


def _source_facts_html(filing: FilingEvent) -> str:
    """Deterministic and source-grounded only: company name, the filing's
    own title (quoted verbatim, never paraphrased), source, and filed
    date — every one a structured FilingEvent field, never a value parsed
    out of unstructured excerpt text. No amount, counterparty, or date is
    ever inferred from free text here."""
    return f"{filing.corp_name} filed “{filing.report_nm}” with {filing.source_name} on {filing.rcept_dt}."


def render_analyst_view(filing: FilingEvent, candidate: CandidateSignal) -> None:
    if not should_render_analyst_view(candidate):
        return

    st.markdown(
        '<div class="er-muted" style="margin-top:0.6rem;"><strong>Filing overview</strong> '
        '<span style="font-size:0.72rem;">— Phase 1: built from filing metadata and category '
        'labels only. Not a substantive summary of the filing text.</span></div>',
        unsafe_allow_html=True,
    )

    category = _matched_category(candidate.matched_rules)

    # 1. What happened — Phase 1: structured metadata only, gated on
    # excerpt length (source-neutral, applies identically to DART/EDGAR/
    # EDINET). Never reads/paraphrases the excerpt itself (that's Phase 2,
    # not yet approved).
    st.markdown('<div class="er-muted" style="margin-top:0.4rem;"><strong>What happened</strong></div>', unsafe_allow_html=True)
    excerpt_len = len((candidate.excerpt_original or "").strip())
    if excerpt_len >= _MIN_SUBSTANTIVE_EXCERPT_CHARS:
        if filing.source_url:
            st.markdown(evidence_chip_html(ClaimType.FACT, has_source=True), unsafe_allow_html=True)
        st.markdown(f'<div style="margin-top:0.15rem;">{_source_facts_html(filing)}</div>', unsafe_allow_html=True)
        why_phrases = _why_entered_radar_phrases(filing.source_name, candidate.matched_rules)
        if why_phrases:
            st.markdown(evidence_chip_html(ClaimType.INTERPRETATION), unsafe_allow_html=True)
            st.markdown('<div style="margin-top:0.1rem;">Radar flagged this filing because:</div>', unsafe_allow_html=True)
            for phrase in why_phrases:
                st.markdown(f'<div style="margin-top:0.1rem; margin-left:0.8rem;">• {phrase}</div>', unsafe_allow_html=True)
    else:
        st.markdown(f'<div style="margin-top:0.15rem;">{_INSUFFICIENT_EXCERPT_TEXT}</div>', unsafe_allow_html=True)
    if filing.source_url:
        st.markdown(
            f'<div style="margin-top:0.15rem;"><a href="{filing.source_url}" target="_blank">Open original filing ↗</a></div>',
            unsafe_allow_html=True,
        )

    # 2. Why it matters — only when a real, hand-written per-category
    # template exists. No generic hedge invented for other categories.
    why_it_matters = _WHY_IT_MATTERS_TEMPLATES.get(category) if filing.source_name == _DART_SOURCE else None
    if why_it_matters:
        st.markdown('<div class="er-muted" style="margin-top:0.4rem;"><strong>Why it matters</strong></div>', unsafe_allow_html=True)
        st.markdown(evidence_chip_html(ClaimType.INTERPRETATION), unsafe_allow_html=True)
        st.markdown(f'<div style="margin-top:0.15rem;">{why_it_matters}</div>', unsafe_allow_html=True)

    # 3. What remains uncertain — merges the prior "What is unconfirmed"
    # and "Follow-up evidence to watch" sections; template selection
    # logic is unchanged from before.
    if filing.source_name == _DART_SOURCE and category == "market_rumor_response":
        unconfirmed_text = _UNCONFIRMED_TEMPLATES["market_rumor_response"]
        followups = _FOLLOWUP_TEMPLATES["market_rumor_response"]
    else:
        unconfirmed_text = _UNCONFIRMED_FALLBACK
        followups = _FOLLOWUP_FALLBACK
    st.markdown('<div class="er-muted" style="margin-top:0.4rem;"><strong>What remains uncertain</strong></div>', unsafe_allow_html=True)
    st.markdown(evidence_chip_html(ClaimType.UNCERTAINTY), unsafe_allow_html=True)
    st.markdown(f'<div style="margin-top:0.15rem;">{unconfirmed_text}</div>', unsafe_allow_html=True)
    st.markdown('<div class="er-muted" style="margin-top:0.3rem; font-size:0.85rem;">Watch for:</div>', unsafe_allow_html=True)
    for item in followups:
        st.markdown(f'<div style="margin-top:0.1rem; margin-left:0.8rem;">• {item}</div>', unsafe_allow_html=True)

    # 4. Evidence and provenance — a pointer to what's already rendered
    # below (the raw excerpt/translation blocks stay exactly where they
    # are, unmodified), not a duplicate of it.
    st.markdown('<div class="er-muted" style="margin-top:0.4rem;"><strong>Evidence and provenance</strong></div>', unsafe_allow_html=True)
    st.markdown('<div style="margin-top:0.15rem;">Original-language excerpt: see below.</div>', unsafe_allow_html=True)
    if candidate.translation_state != TranslationState.NOT_REQUESTED:
        if candidate.excerpt_translation is not None:
            st.markdown(
                '<div style="margin-top:0.1rem;">Machine translation: see below — for convenience, verify against the original-language source.</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown('<div style="margin-top:0.1rem;">Machine translation: not currently available for this excerpt.</div>', unsafe_allow_html=True)
