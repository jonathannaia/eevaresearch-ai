"""Small reusable UI primitives for the v2 redesign (design/redesign-v2).

Pure HTML-string builders plus two thin Streamlit renderers, all reading
only assets/styles.css tokens through class names — never an inline
color. Every helper escapes the text it is given; callers pass raw
strings. Nothing here fetches, invents, or reformats research data: a
chip shows the label it is handed, a venue badge maps the three real
source_name values and nothing else, an evidence bar renders the counts
it is given (all four directions, so Contradicts is never collapsed).
"""
from __future__ import annotations

import html
from collections.abc import Mapping

import streamlit as st

_VENUE_BY_SOURCE: dict[str, tuple[str, str]] = {
    "SEC EDGAR": ("EDGAR", "edgar"),
    "OpenDART / DART": ("DART", "dart"),
    "EDINET": ("EDINET", "edinet"),
}

# EvidenceDirection.value -> (label, css suffix); order is the display order.
EVIDENCE_DIRECTIONS: tuple[tuple[str, str], ...] = (
    ("Supports", "supports"),
    ("Mixed", "mixed"),
    ("Contradicts", "contradicts"),
    ("Context", "context"),
)

_THEME_CLASS: dict[str, str] = {
    "ai-buildout": "ai-buildout",
    "memory": "memory",
    "space": "space",
    "photonics": "photonics",
}

_LANG_ATTR: dict[str, str] = {"Korean": "ko", "Japanese": "ja"}


def esc(value: object) -> str:
    return "" if value is None else html.escape(str(value))


def lang_attr(original_language: str | None) -> str:
    """` lang="ko"` / ` lang="ja"` for original-language text, else ""."""
    code = _LANG_ATTR.get((original_language or "").strip())
    return f' lang="{code}"' if code else ""


def chip_html(label: str, variant: str = "neutral", *, dot: bool = False, mono: bool = False) -> str:
    dot_html = '<span class="er-chip-dot"></span>' if dot else ""
    mono_cls = " er-tag-mono" if mono else ""
    return f'<span class="er-status-tag er-tag-{variant}{mono_cls}">{dot_html}{esc(label)}</span>'


def venue_badge_html(source_name: str) -> str:
    """Badge for one of the three real filing venues; an unknown source
    name renders as its own text in the neutral style, never guessed."""
    label, slug = _VENUE_BY_SOURCE.get(source_name, (source_name, "other"))
    return f'<span class="er-venue er-venue-{slug}">{esc(label)}</span>'


def eyebrow_html(text: str) -> str:
    return f'<div class="er-eyebrow">{esc(text)}</div>'


def mono_html(text: str, muted: bool = False) -> str:
    cls = "er-mono er-mono-muted" if muted else "er-mono"
    return f'<span class="{cls}">{esc(text)}</span>'


def theme_dot_html(theme_slug: str) -> str:
    cls = _THEME_CLASS.get(theme_slug, "other")
    return f'<span class="er-theme-dot er-theme-{cls}"></span>'


def evidence_bar_html(counts: Mapping[str, int], *, show_labels: bool = True) -> str:
    """Segmented evidence bar + labelled counts for Supports / Mixed /
    Contradicts / Context. All four labels always render (a zero is
    shown as 0), so a contradicting count can never disappear."""
    total = sum(max(0, int(counts.get(label, 0))) for label, _ in EVIDENCE_DIRECTIONS)
    segments = []
    for label, slug in EVIDENCE_DIRECTIONS:
        n = max(0, int(counts.get(label, 0)))
        if total and n:
            segments.append(f'<span class="er-evbar-seg er-ev-{slug}" style="flex:{n} 0 0;"></span>')
    bar = f'<div class="er-evbar">{"".join(segments) or ""}</div>'
    if not show_labels:
        return bar
    legend = "".join(
        f'<span class="er-evlabel"><span class="er-evdot er-ev-{slug}"></span>{label} '
        f'<span class="er-mono">{max(0, int(counts.get(label, 0)))}</span></span>'
        for label, slug in EVIDENCE_DIRECTIONS
    )
    return f'{bar}<div class="er-evlegend">{legend}</div>'


def kv_grid_html(pairs: list[tuple[str, str]], *, mono_values: bool = False) -> str:
    """Labelled key/value cells (uppercase mono label over a value). Empty
    values are skipped by the caller, never rendered as a placeholder."""
    value_cls = "er-kv-value er-mono" if mono_values else "er-kv-value"
    cells = "".join(
        f'<div class="er-kv"><div class="er-kv-label">{esc(k)}</div><div class="{value_cls}">{esc(v)}</div></div>'
        for k, v in pairs
    )
    return f'<div class="er-kv-grid">{cells}</div>'


def page_header(title: str, subtitle: str | None = None) -> None:
    st.markdown(f'<div class="er-page-title">{esc(title)}</div>', unsafe_allow_html=True)
    if subtitle:
        st.markdown(f'<div class="er-page-subtitle">{esc(subtitle)}</div>', unsafe_allow_html=True)


def summary_tile(key: str, label: str, value: str, detail: str | None = None, *, live_dot: bool = False) -> None:
    """One dashboard summary tile: mono uppercase label, large mono value,
    muted detail line. Renders exactly the strings it is given."""
    dot = ' <span class="dot live"></span>' if live_dot else ""
    detail_html = f'<div class="er-tile-detail">{esc(detail)}</div>' if detail else ""
    with st.container(key=f"card-summary-{key}"):
        st.markdown(
            f'<div class="er-metric-label">{esc(label)}</div>'
            f'<div class="er-metric-value">{esc(value)}{dot}</div>{detail_html}',
            unsafe_allow_html=True,
        )
