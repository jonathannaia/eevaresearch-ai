"""Card-style components, built on Streamlit's native bordered container
(st.container(border=True, key="card-...")) rather than custom HTML — keeps
borders/spacing consistent with the theme and gives every card the shared
hover-lift rule in theme.py (which targets the `st-key-card-*` prefix).
Direction rails use a small per-instance <style> block injected right
before the container, since the rail color varies per signal and Streamlit
containers don't accept an arbitrary CSS class directly — only a `key`,
which becomes a `st-key-{key}` class (confirmed against the running app).
"""
from __future__ import annotations

import html

import streamlit as st

from src.logic.evidence import source_label
from src.logic.formatting import fmt_date, fmt_pct
from src.logic.market_map import jurisdiction_for_source
from src.models.models import CapitalRotationMetric, Catalyst, EvidenceItem, Signal, Theme
from src.ui.components.badges import demo_badge, direction_dot_html, direction_rail_class, freshness_badge
from src.ui.components.evidence_chips import evidence_chip
from src.ui.components.excerpts import render_excerpt


def _esc(value: object) -> str:
    if value is None:
        return ""
    return html.escape(str(value))


def _safe_url(url: object) -> str | None:
    """Only an http://​/https:// URL is ever rendered as a clickable link
    — same discipline as src/ui/pages/themes_research.py's own
    _safe_source_url. Anything else (empty, malformed, or an unsafe
    scheme) returns None, so the caller renders no link at all rather
    than an unsafe one."""
    if not isinstance(url, str):
        return None
    stripped = url.strip()
    lowered = stripped.lower()
    if lowered.startswith("https://") or lowered.startswith("http://"):
        return stripped
    return None


def _display_theme_name(signal: Signal, theme_name: str | None) -> str:
    if theme_name:
        return theme_name
    return signal.theme_slug.replace("-", " ").title()

_THEME_ICONS: dict[str, str] = {
    "ai-buildout": (
        '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.4">'
        '<rect x="3" y="3" width="14" height="4" rx="1"/><rect x="3" y="8" width="14" height="4" rx="1"/>'
        '<rect x="3" y="13" width="14" height="4" rx="1"/>'
        '<circle cx="6" cy="5" r="0.6" fill="currentColor" stroke="none"/>'
        '<circle cx="6" cy="10" r="0.6" fill="currentColor" stroke="none"/>'
        '<circle cx="6" cy="15" r="0.6" fill="currentColor" stroke="none"/></svg>'
    ),
    "humanoids": (
        '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.4">'
        '<circle cx="10" cy="4.5" r="2.3"/><line x1="10" y1="6.8" x2="10" y2="12"/>'
        '<line x1="10" y1="9" x2="5" y2="13.5"/><line x1="10" y1="9" x2="15" y2="13.5"/>'
        '<line x1="10" y1="12" x2="6.5" y2="17.5"/><line x1="10" y1="12" x2="13.5" y2="17.5"/>'
        '<circle cx="10" cy="9" r="0.7" fill="currentColor" stroke="none"/></svg>'
    ),
    "space": (
        '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.4">'
        '<ellipse cx="10" cy="10" rx="8" ry="3.2" transform="rotate(-20 10 10)"/>'
        '<circle cx="10" cy="10" r="1.5" fill="currentColor" stroke="none"/>'
        '<circle cx="16" cy="6.6" r="1" fill="currentColor" stroke="none"/></svg>'
    ),
    "memory": (
        '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.4">'
        '<rect x="5" y="5" width="10" height="10" rx="1"/>'
        '<line x1="7" y1="2" x2="7" y2="5"/><line x1="10" y1="2" x2="10" y2="5"/><line x1="13" y1="2" x2="13" y2="5"/>'
        '<line x1="7" y1="15" x2="7" y2="18"/><line x1="10" y1="15" x2="10" y2="18"/><line x1="13" y1="15" x2="13" y2="18"/>'
        '<line x1="2" y1="7" x2="5" y2="7"/><line x1="2" y1="10" x2="5" y2="10"/><line x1="2" y1="13" x2="5" y2="13"/>'
        '<line x1="15" y1="7" x2="18" y2="7"/><line x1="15" y1="10" x2="18" y2="10"/><line x1="15" y1="13" x2="18" y2="13"/></svg>'
    ),
    "photonics": (
        '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.4">'
        '<path d="M2 10 Q5 4 8 10 T14 10 T20 10" stroke-linecap="round"/>'
        '<circle cx="2" cy="10" r="1" fill="currentColor" stroke="none"/></svg>'
    ),
}

def theme_icon_html(theme_slug: str) -> str:
    icon = _THEME_ICONS.get(theme_slug, "")
    return f'<div class="er-theme-icon">{icon}</div>'


def metric_pair_html(label1: str, value1: str, label2: str, value2: str) -> str:
    return (
        '<div style="display:flex; gap:1.5rem; margin: 0.4rem 0 0.8rem 0;">'
        f'<div><div class="er-metric-label">{label1}</div><div class="er-metric-value">{value1}</div></div>'
        f'<div><div class="er-metric-label">{label2}</div><div class="er-metric-value">{value2}</div></div>'
        "</div>"
    )


def metric_tile(label: str, value: str, help_text: str | None = None) -> None:
    st.metric(label, value, help=help_text)


def theme_card(theme: Theme, metric: CapitalRotationMetric | None, page=None) -> None:
    with st.container(border=True, key=f"card-theme-{theme.slug}"):
        icon = _THEME_ICONS.get(theme.slug, "")
        st.markdown(f'<div class="er-theme-icon">{icon}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="er-card-title">{theme.name}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="er-muted">{theme.description}</div>', unsafe_allow_html=True)
        if metric:
            dot = direction_dot_html(_infer_direction(metric.relative_performance_pct))
            st.markdown(
                f"""
                <div style="display:flex; gap:1.5rem; margin-top:0.6rem;">
                    <div><div class="er-metric-label">Rel. perf.</div>
                    <div class="er-metric-value">{fmt_pct(metric.relative_performance_pct)}</div></div>
                    <div><div class="er-metric-label">Breadth</div>
                    <div class="er-metric-value">{metric.breadth_pct:.0f}%</div></div>
                </div>
                <div class="er-muted" style="margin-top:0.4rem;">{dot}</div>
                """,
                unsafe_allow_html=True,
            )
        if page is not None:
            with st.container(key=f"cta-tertiary-theme-{theme.slug}"):
                st.page_link(page, label=f"Explore {theme.name} →")


def _infer_direction(relative_performance_pct: float):
    from src.models.models import Direction

    if relative_performance_pct > 2:
        return Direction.IMPROVING
    if relative_performance_pct < -2:
        return Direction.WEAKENING
    return Direction.MIXED


def signal_card(
    signal: Signal, theme_page=None, evidence_repository=None, unread: bool = False, theme_name: str | None = None,
) -> None:
    """Compact signal card — one-line interpretation leads (the thing a
    scanning reader needs first), meta strip follows, contrary evidence /
    validation / invalidation live only in the drawer (Review evidence),
    never inline. A thin left rail carries a restrained direction-color
    accent (UX-refinement pass)."""
    key = f"card-signal-{signal.id}"
    rail_var = {"er-rail-pos": "var(--pos)", "er-rail-neg": "var(--neg)", "er-rail-mix": "var(--mix)"}[direction_rail_class(signal.direction)]
    st.markdown(
        f'<style>.st-key-{key} {{ border-left: 2px solid {rail_var} !important; }}</style>',
        unsafe_allow_html=True,
    )
    with st.container(border=True, key=key):
        top = st.columns([3, 1])
        with top[0]:
            dot = '<span class="er-unread-dot"></span>' if unread else ""
            st.markdown(f'<div class="er-card-title">{dot}{signal.title}</div>', unsafe_allow_html=True)
            if signal.title_translated:
                # signal.title above is already the translation (see
                # signal_promotion._title) — this retains the original
                # beneath it, explicitly labeled, never overwritten.
                st.markdown(
                    f'<div class="er-muted" style="font-size:0.78rem; margin-top:0.1rem;">'
                    f'English — machine translation. Original ({signal.original_language}): {signal.title_native}</div>',
                    unsafe_allow_html=True,
                )
            tag_line = signal.theme_slug + (f" / {signal.subtheme_slug}" if signal.subtheme_slug else "")
            st.markdown(f'<div class="er-muted">{tag_line}</div>', unsafe_allow_html=True)
            if signal.issuer:
                # Real Radar promotion only (signal_promotion.py) — issuer/
                # source name/date copied verbatim from the filing, never
                # inferred. Empty for every demo signal. exchange_symbol
                # only appears on an exact tracked-company registry match.
                identity = f"{signal.issuer} · {signal.exchange_symbol}" if signal.exchange_symbol else signal.issuer
                jurisdiction = jurisdiction_for_source(signal.source_name)
                source_label_text = f"{signal.source_name} · {jurisdiction}" if jurisdiction else signal.source_name
                st.markdown(
                    f'<div class="er-muted" style="font-size:0.85rem; margin-top:0.15rem;">'
                    f'{identity} · {source_label_text} · {fmt_date(signal.last_updated)}</div>',
                    unsafe_allow_html=True,
                )
        with top[1]:
            if signal.is_demo:
                demo_badge("Sample")

        st.markdown(
            f'<div style="margin:0.4rem 0; max-height:3.2em; overflow:hidden;">{signal.interpretation}</div>',
            unsafe_allow_html=True,
        )
        if signal.excerpt_translated:
            # English first, native retained below, never overwritten —
            # the original stays the evidence of record.
            st.markdown(
                f'<div class="er-excerpt" style="font-size:0.85rem; margin:0.3rem 0;">{signal.excerpt_translated}</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                '<div class="er-muted" style="font-size:0.72rem; margin-top:-0.15rem;">'
                'English — machine translation. Original remains the evidence.</div>',
                unsafe_allow_html=True,
            )
            if signal.excerpt:
                # Phase B (UI audit): the original-language excerpt (often
                # long, e.g. a full Korean filing) collapses behind an
                # opt-in expander — the English translation above remains
                # the default reading path. Nothing here is removed: the
                # original stays the evidence of record, one click away.
                with st.expander("Show original filing", expanded=False):
                    st.markdown(
                        f'<div class="er-muted" style="font-size:0.72rem;">Original ({signal.original_language}):</div>',
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        f'<div class="er-excerpt" style="font-size:0.85rem; margin:0.15rem 0 0.3rem;">{signal.excerpt}</div>',
                        unsafe_allow_html=True,
                    )
        elif signal.excerpt:
            st.markdown(
                f'<div class="er-excerpt" style="font-size:0.85rem; margin:0.3rem 0;">{signal.excerpt}</div>',
                unsafe_allow_html=True,
            )
            if signal.translation_state and signal.translation_state != "Not requested":
                # Honest status only — DART/EDINET pending/unavailable
                # cases. EDGAR's "Not requested" never renders any
                # translation-status line at all (English-original, per
                # the approved plan).
                st.markdown(
                    f'<div class="er-muted" style="font-size:0.72rem;">{signal.translation_state}.</div>',
                    unsafe_allow_html=True,
                )
        st.markdown(
            f"""
            <div style="display:flex; gap:1.25rem; margin: 0.4rem 0;">
                <div><div class="er-metric-label">Direction</div><div class="er-muted">{direction_dot_html(signal.direction)}</div></div>
                <div><div class="er-metric-label">Strength</div><div class="er-mono">{signal.strength.value}</div></div>
                <div><div class="er-metric-label">Horizon</div><div class="er-mono">{signal.horizon.value}</div></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="er-muted" style="font-size:0.78rem;">{signal.evidence_count} evidence item(s) '
            f'· updated {fmt_date(signal.last_updated)}</div>',
            unsafe_allow_html=True,
        )
        if signal.related_tickers:
            # Plain text, not a link (reader-facing data-integrity pass,
            # design/DECISIONS.md) — the Company page these used to link
            # to had no live real data and was removed; there is no
            # per-ticker detail route to link to today.
            tickers = ", ".join(signal.related_tickers)
            st.markdown(f'<div class="er-muted" style="font-size:0.78rem;">Related: {tickers}</div>', unsafe_allow_html=True)
        if signal.source_url:
            st.markdown(
                f'<div class="er-muted" style="font-size:0.78rem;">'
                f'<a href="{signal.source_url}" target="_blank" rel="noopener noreferrer" '
                f'style="color:var(--text-2); text-decoration:underline;">View source document ↗</a></div>',
                unsafe_allow_html=True,
            )

        action_cols = st.columns([1, 1, 2])
        with action_cols[0]:
            with st.container(key=f"cta-secondary-{signal.id}"):
                if st.button("Review evidence", key=f"open-drawer-{signal.id}", width="stretch"):
                    from src.ui.components.signal_drawer import open_signal_drawer

                    open_signal_drawer(signal, evidence_repository=evidence_repository)
        if theme_page is not None:
            with action_cols[1]:
                with st.container(key=f"cta-tertiary-{signal.id}"):
                    st.page_link(theme_page, label=f"Explore {_display_theme_name(signal, theme_name)} →")


def compact_signal_row(signal: Signal) -> None:
    """A slimmer signal presentation for Overview's top-3 — direction,
    strength, horizon, and a one-line interpretation only. No expander, no
    evidence-count/related-tickers/CTA — that detail lives on the full
    signal_card, which is Signal Board's job now, not Overview's."""
    key = f"card-compact-signal-{signal.id}"
    with st.container(border=True, key=key):
        st.markdown(f'<div class="er-card-title" style="font-size:0.95rem;">{signal.title}</div>', unsafe_allow_html=True)
        st.markdown(
            f"""
            <div class="er-muted" style="margin: 0.2rem 0;">
                {direction_dot_html(signal.direction)} · <span class="er-mono">{signal.strength.value}</span>
                · <span class="er-mono">{signal.horizon.value}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="er-muted" style="white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">{signal.interpretation}</div>',
            unsafe_allow_html=True,
        )


def catalyst_timeline_row(catalyst: Catalyst) -> None:
    st.markdown(
        f"""
        <div class="er-row" style="gap:var(--space-4);">
            <span class="er-mono er-date-badge">{fmt_date(catalyst.date)}</span>
            <span style="flex:1;">{catalyst.title}</span>
            <span class="er-muted" style="white-space:nowrap;">{catalyst.catalyst_type}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def priority_signal_row(signal: Signal, order: int | None = None) -> None:
    """The compact single-line-plus-meta row used by Dashboard's Priority
    Signals (top 2-3 only) — direction, theme/subtheme, title, strength,
    horizon, one-line interpretation, and a real 'Review evidence →'
    action. Distinct from both the full `signal_card` (too tall for a
    dashboard summary) and `compact_signal_row` (no evidence action).
    `order` (1-based) renders a small "01"/"02" marker so the section reads
    as ranked rather than an arbitrary list (final polish pass).

    Reader-facing data-integrity pass (design/DECISIONS.md): this
    component is only ever called with a real Radar-promoted signal
    (is_demo=False unconditionally, see src/logic/signal_promotion.py) —
    no `evidence_repository`/`theme_name` parameters, since both only
    ever fed the now-removed demo path. Every row shows the full real
    provenance (company, jurisdiction, source, date, original-source
    link) directly, not just behind an extra click."""
    key = f"card-priority-signal-{signal.id}"
    rail_var = {"er-rail-pos": "var(--pos)", "er-rail-neg": "var(--neg)", "er-rail-mix": "var(--mix)"}[direction_rail_class(signal.direction)]
    st.markdown(f'<style>.st-key-{key} {{ border-left: 2px solid {rail_var} !important; }}</style>', unsafe_allow_html=True)
    with st.container(border=True, key=key):
        row = st.columns([0.4, 4.6, 1, 1, 1.6])
        with row[0]:
            marker = f'<span class="er-order-marker">{order:02d}</span>' if order else ""
            st.markdown(f'<div style="margin-top:0.15rem;">{marker}</div>', unsafe_allow_html=True)
        with row[1]:
            tag_line = signal.theme_slug + (f" / {signal.subtheme_slug}" if signal.subtheme_slug else "")
            st.markdown(
                f'<div class="er-card-title" style="font-size:0.88rem;">{direction_dot_html(signal.direction)} · {_esc(signal.title)}</div>'
                f'<div class="er-muted" style="font-size:0.76rem; margin-top:var(--space-1);">{_esc(tag_line)}</div>',
                unsafe_allow_html=True,
            )
            jurisdiction = jurisdiction_for_source(signal.source_name)
            provenance = [p for p in (signal.issuer, jurisdiction, signal.source_name) if p]
            if signal.last_updated:
                provenance.append(fmt_date(signal.last_updated))
            if provenance:
                st.markdown(
                    f'<div class="er-muted" style="font-size:0.74rem; margin-top:0.1rem;">{_esc(" · ".join(provenance))}</div>',
                    unsafe_allow_html=True,
                )
            safe_url = _safe_url(signal.source_url)
            if safe_url:
                st.markdown(
                    f'<div style="margin-top:0.1rem;"><a href="{html.escape(safe_url, quote=True)}" '
                    f'target="_blank" rel="noopener noreferrer" style="color:var(--text-2); font-size:0.74rem; '
                    f'text-decoration:underline;">Original source ↗</a></div>',
                    unsafe_allow_html=True,
                )
        with row[2]:
            st.markdown(f'<div class="er-metric-label">Strength</div><div class="er-mono" style="font-size:0.8rem;">{signal.strength.value}</div>', unsafe_allow_html=True)
        with row[3]:
            st.markdown(f'<div class="er-metric-label">Horizon</div><div class="er-mono" style="font-size:0.8rem;">{signal.horizon.value}</div>', unsafe_allow_html=True)
        with row[4]:
            with st.container(key=f"cta-secondary-priority-{signal.id}"):
                if st.button("Review evidence", key=f"priority-review-{signal.id}", width="stretch"):
                    from src.ui.components.signal_drawer import open_signal_drawer

                    open_signal_drawer(signal)
        st.markdown(
            f'<div class="er-muted" style="font-size:0.82rem; margin-top:var(--space-2); white-space:nowrap; '
            f'overflow:hidden; text-overflow:ellipsis;">{_esc(signal.interpretation)}</div>',
            unsafe_allow_html=True,
        )


# Backwards-compatible alias — catalyst_row was the pre-redesign name.
catalyst_row = catalyst_timeline_row


def evidence_row(evidence: EvidenceItem) -> None:
    with st.container(border=True, key=f"card-evidence-{evidence.id}"):
        top = st.columns([3, 1, 1])
        top[0].markdown(f'<div class="er-card-title">{evidence.title}</div>', unsafe_allow_html=True)
        with top[1]:
            evidence_chip(evidence.claim_type, has_source=bool(evidence.source_name))
        with top[2]:
            freshness_badge(evidence)
        render_excerpt(evidence)
        st.markdown(
            f'<div class="er-muted" style="margin-top:0.3rem;">{source_label(evidence)}</div>',
            unsafe_allow_html=True,
        )
