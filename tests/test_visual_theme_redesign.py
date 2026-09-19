"""Visual theme — stylesheet structure (assets/styles.css).

History: this file has been rewritten in place for every palette pass
(midnight-navy, light editorial, indigo/editorial-white, application-
shell dark, Midnight Teal, redesign v2). The Theming + Typography release
moves every color VALUE out of this stylesheet into
src/ui/theme_tokens.py (exact values, config parity and contrast math
now live in tests/test_theme_tokens.py; the no-literal rule in
tests/test_no_hardcoded_colors.py). What stays here is the structural
contract each rule must keep: which semantic token a rule reads, the
active/inactive nav treatment, focus rings, radii, density, and the
evidence-vs-status class split.

Token renames in this release: --rail -> --bg-sidebar; --border-soft ->
--border-subtle; --border-shell/--card-border-hover -> --border-strong;
--surface-active -> --surface-pressed (state) or --surface-chip (chip/
track); --invert-bg/--invert-fg -> --accent/--accent-contrast;
--accent-soft -> --nav-active-bg / --selected-bg; --focus ->
--focus-ring; --positive/--negative/--warning/--context -> --ev-*;
--theme-* -> --cat-*; --info/--glow retired."""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
_CSS = (REPO_ROOT / "assets" / "styles.css").read_text(encoding="utf-8")

_RETIRED_TOKENS = (
    "rail", "border-soft", "border-shell", "card-border-hover", "surface-active", "invert-bg", "invert-bg-hover",
    "invert-fg", "glow", "accent-soft", "focus", "positive", "positive-dim", "negative", "negative-dim",
    "warning", "warning-dim", "info", "info-dim", "context", "theme-ai-buildout", "theme-memory",
    "theme-space", "theme-photonics",
    # earlier passes
    "hairline", "text-2", "text-3", "text-4", "surface-2", "surface-3", "pos", "neg", "mix",
)


def _rule(selector_regex: str) -> str:
    match = re.search(selector_regex + r"\s*\{([^}]*)\}", _CSS, re.DOTALL)
    assert match, f"rule not found: {selector_regex}"
    return match.group(1)


def test_stylesheet_defines_no_color_tokens_of_its_own():
    """Color tokens are rendered from src/ui/theme_tokens.py; the
    stylesheet's own :root carries radii, fonts and spacing only."""
    roots = re.findall(r"(?m)^:root \{([^}]*)\}", _CSS)
    assert len(roots) == 1
    declared = set(re.findall(r"--([a-z0-9-]+):", roots[0]))
    assert declared == {"r-sm", "r-md", "r-lg", "font-ui", "font-ui-ja", "font-mono", "font-serif", "font-serif-ja",
                        "fs-page-title", "fs-card-title", "fs-label", *(f"space-{i}" for i in range(1, 9))}


def test_no_retired_token_name_is_consumed_anywhere():
    offenders = []
    sources = [("assets/styles.css", _CSS)] + [
        (str(p.relative_to(REPO_ROOT)), p.read_text(encoding="utf-8")) for p in (REPO_ROOT / "src").rglob("*.py")
    ]
    for name, text in sources:
        for token_name in _RETIRED_TOKENS:
            if re.search(rf"var\(--{re.escape(token_name)}\)", text):
                offenders.append((name, token_name))
    assert offenders == []


def test_theme_media_query_lives_only_in_the_rendered_token_css():
    assert "@media (prefers-color-scheme" not in _CSS


def test_no_gradients_glows_or_theme_transition_animations():
    assert "gradient(" not in _CSS
    # No glow: every box-shadow is a hard ring (zero blur) or none.
    for value in re.findall(r"box-shadow:\s*([^;!]+)", _CSS):
        if value.strip() == "none":
            continue
        for shadow in value.split(","):
            lengths = re.findall(r"-?\d*\.?\d+(?:px)?", shadow.replace("inset", ""))
            assert len(lengths) < 3 or float(lengths[2].removesuffix("px")) == 0, value
    # Transitions name the properties they animate; none may animate the
    # whole element ("all") or a theme-wide custom property.
    for body in re.findall(r"transition:\s*([^;]+);", _CSS):
        assert "all" not in body.split() and "--" not in body, body
    # Only the three sanctioned motions exist: live-dot pulse, bar fill, skeleton pulse.
    assert set(re.findall(r"@keyframes ([\w-]+)", _CSS)) == {"dot-pulse", "fill", "skel-pulse"}


def test_topbar_anchor_class_and_retired_topbar_only_selectors_are_gone():
    assert ".er-topbar-anchor" not in _CSS
    assert "st-key-topbar-avatar-" not in _CSS
    assert ".er-topbar-avatar-email" not in _CSS


# --- navigation -----------------------------------------------------------

def test_active_nav_item_gets_the_nav_active_surface_and_a_left_accent_bar():
    body = _rule(r'\[class\*="st-key-navitem-"\]\[class\*="-active"\] \[data-testid="stPageLink"\] a')
    assert ".er-rail-navactive" not in _CSS
    assert "background: var(--nav-active-bg)" in body
    assert "color: var(--accent)" in body
    assert re.search(r"border-left:\s*2px solid var\(--accent\)", body)


def test_inactive_nav_item_is_quiet_accent_only_on_hover_or_active():
    resting = _rule(r'\[data-testid="stSidebar"\] \[data-testid="stPageLink"\] a')
    assert "var(--accent" not in resting
    assert "var(--text-secondary)" in resting
    assert re.search(r"border-left:\s*2px solid transparent", resting)
    hover = _rule(r'\[data-testid="stSidebar"\] \[data-testid="stPageLink"\] a:hover')
    assert "var(--accent-hover)" in hover and "var(--surface-hover)" in hover


def test_nav_icons_are_token_painted_masks_with_no_color_of_their_own():
    base = _rule(r'\[class\*="st-key-navitem-"\] \[data-testid="stPageLink"\] a::before')
    assert "background-color: var(--text-muted)" in base
    active = _rule(r'\[class\*="st-key-navitem-"\]\[class\*="-active"\] \[data-testid="stPageLink"\] a::before')
    assert "background-color: var(--accent)" in active
    for item in ("dashboard", "radar_inbox", "daily_news", "themes", "coverage", "admin_users"):
        body = _rule(rf'\.st-key-navitem-{item} \[data-testid="stPageLink"\] a::before')
        assert "mask-image: url(" in body and "stroke%3D%22currentColor%22" in body
        assert "background-image" not in body
    assert "-active [data-testid=\"stPageLink\"] a::before {\n    background-image" not in _CSS


def test_sidebar_uses_its_own_surface_and_strong_edge():
    body = _rule(r'\[data-testid="stSidebar"\]')
    assert "background: var(--bg-sidebar) !important;" in body
    assert "border-right: 1px solid var(--border-strong);" in body


def test_sidebar_text_uses_secondary_not_label_grey():
    for pattern in (r"\.er-rail-group-label", r"\.er-rail-status", r'\.er-rail-footlinks \[data-testid="stPageLink"\] a p'):
        assert "color: var(--text-secondary)" in _rule(pattern)


def test_active_nav_count_badge_uses_the_selected_surface():
    body = _rule(r'\[class\*="st-key-navitem-"\]\[class\*="-active"\] \.er-rail-count')
    assert "var(--selected-bg)" in body and "color: var(--accent)" in body


# --- controls ---------------------------------------------------------------

def test_sidebar_account_avatar_is_accent_with_contrast_label():
    body = _rule(
        r'\[class\*="st-key-sidebar-account-"\] \[data-testid="stPopoverButton"\],\s*'
        r'\[class\*="st-key-sidebar-account-"\] \[data-testid="stBaseButton-secondary"\]'
    )
    assert "background: var(--accent)" in body and "color: var(--accent-contrast)" in body


def test_compact_search_trigger_is_borderless_at_rest_with_an_accent_hover():
    assert "border-color: transparent !important; color: var(--text-secondary) !important;" in _CSS
    hover = _rule(r'\.er-rail-brand \[class\*="st-key-cta-secondary-cmdk-trigger"\] \[data-testid="stBaseButton-secondary"\]:hover')
    assert "var(--accent)" in hover


def test_sidebar_collapse_expand_buttons_step_to_the_pressed_surface_on_hover():
    body = _rule(r'\[data-testid="stSidebarCollapseButton"\] button:hover,\s*\[data-testid="stExpandSidebarButton"\]:hover')
    assert "background: var(--surface-pressed) !important;" in body
    assert "border-color: var(--accent) !important;" in body


def test_primary_cta_is_an_accent_fill_with_derived_hover_and_pressed_states_and_no_shadow():
    rest = _rule(r'\[class\*="st-key-cta-primary-"\] \[data-testid="stPageLink"\] a,\s*\[class\*="st-key-cta-primary-"\] \[data-testid="stBaseButton-secondary"\]')
    assert "background: var(--accent) !important; color: var(--accent-contrast) !important;" in rest
    assert "border-radius: var(--r-md) !important;" in rest
    assert "box-shadow: none !important;" in rest
    hover = _rule(r'\[class\*="st-key-cta-primary-"\] \[data-testid="stPageLink"\] a:hover,\s*\[class\*="st-key-cta-primary-"\] \[data-testid="stBaseButton-secondary"\]:hover')
    assert hover.strip() == "background: var(--accent-hover) !important;"
    pressed = _rule(r'\[class\*="st-key-cta-primary-"\] \[data-testid="stPageLink"\] a:active,\s*\[class\*="st-key-cta-primary-"\] \[data-testid="stBaseButton-secondary"\]:active')
    assert pressed.strip() == "background: var(--accent-pressed) !important;"


def test_native_primary_button_matches_the_custom_primary_cta():
    assert "background: var(--accent) !important; color: var(--accent-contrast) !important;" in _rule(r'\[data-testid="stBaseButton-primary"\]')
    assert "var(--accent-hover)" in _rule(r'\[data-testid="stBaseButton-primary"\]:hover')
    assert "var(--accent-pressed)" in _rule(r'\[data-testid="stBaseButton-primary"\]:active')


def test_secondary_cta_pressed_state_uses_the_derived_pressed_surface():
    body = _rule(r'\[class\*="st-key-cta-secondary-"\] \[data-testid="stPageLink"\] a:active,\s*\[class\*="st-key-cta-secondary-"\] \[data-testid="stBaseButton-secondary"\]:active')
    assert "background: var(--surface-pressed) !important;" in body


def test_every_focusable_control_gets_the_focus_ring_including_the_avatar_popover():
    shared = re.search(r'\[data-testid="stPageLink"\] a:focus-visible,.*?\{[^}]*outline:\s*2px solid var\(--focus-ring\)', _CSS, re.DOTALL)
    assert shared and '[data-testid="stPopoverButton"]:focus-visible' in shared.group(0)
    assert "border-radius: 50%" in _rule(r'\[data-testid="stPopoverButton"\]:focus-visible')


# --- chips, tags and badges ---------------------------------------------------

def test_status_tags_and_fresh_badges_use_the_small_radius_and_chips_stay_pills():
    assert "border-radius: var(--r-sm)" in _rule(r"\.er-status-tag")
    assert "border-radius: var(--r-sm)" in _rule(r"\.er-fresh")
    assert "border-radius: 999px" in _rule(r"\.er-chip")


def test_status_tags_never_use_an_evidence_hue():
    for variant in ("neutral", "info", "high-signal", "live"):
        body = _rule(rf"\.er-status-tag\.er-tag-{variant}")
        assert "var(--ev-" not in body, variant
    assert "var(--high-signal-bg)" in _rule(r"\.er-status-tag\.er-tag-high-signal")
    assert "var(--high-signal-text)" in _rule(r"\.er-status-tag\.er-tag-high-signal")
    assert "var(--selected-bg)" in _rule(r"\.er-status-tag\.er-tag-info")
    assert "var(--live)" in _rule(r"\.er-status-tag\.er-tag-live::before")


def test_evidence_tags_carry_the_hue_as_a_dot_and_tint_with_neutral_text():
    for direction in ("supports", "mixed", "contradicts"):
        body = _rule(rf"\.er-status-tag\.er-tag-ev-{direction}")
        assert f"background: var(--ev-{direction}-tint)" in body and "color: var(--text)" in body
        assert f"background: var(--ev-{direction})" in _rule(rf"\.er-status-tag\.er-tag-ev-{direction}::before")


def test_the_old_ambiguous_pos_neg_mix_tag_classes_are_gone():
    for retired in (".er-tag-pos", ".er-tag-neg", ".er-tag-mix", ".er-tag-accent"):
        assert retired not in _CSS


def test_thesis_invalidation_callout_keeps_its_label_in_text_color():
    assert "var(--ev-contradicts)" in _rule(r"\.er-alert-neg")
    assert "color: var(--text)" in _rule(r"\.er-alert-neg-label")
    assert "var(--ev-contradicts)" in _rule(r"\.er-alert-neg-label::before")


def test_every_filing_venue_has_its_own_tint_and_text_tokens():
    for venue in ("edgar", "dart", "edinet", "tdnet", "cninfo", "hkex"):
        body = _rule(rf"\.er-venue-{venue}")
        assert f"background: var(--venue-{venue}-bg)" in body and f"color: var(--venue-{venue}-text)" in body


def test_category_dots_and_evidence_segments_read_their_own_token_families():
    for cat in ("ai-buildout", "memory", "space", "photonics"):
        assert _rule(rf"\.er-theme-{cat}").strip() == f"background: var(--cat-{cat});"
    for direction in ("supports", "mixed", "contradicts", "context"):
        assert _rule(rf"\.er-ev-{direction}").strip() == f"background: var(--ev-{direction});"


def test_fact_chip_is_the_inverted_neutral_never_the_accent():
    body = _rule(r"\.er-chip-fact")
    assert "background: var(--text); color: var(--bg);" in body


def test_live_indicators_use_the_live_status_token():
    assert "background: var(--live)" in _rule(r"\.dot\.live")
    assert "background: var(--live)" in _rule(r"\.er-fresh-live \.er-fresh-dot")
    assert _CSS.count("border: 1px solid var(--live-ring)") == 2


# --- density ---------------------------------------------------------------------

def test_dashboard_recently_updated_rows_are_denser_with_a_smaller_radius():
    assert '[class*="st-key-card-recently-updated-"] .er-row { padding: 4px 0; }' in _CSS
    assert '[class*="st-key-card-recently-updated-"] { border-radius: var(--r-sm) !important; }' in _CSS


def test_cards_step_their_border_up_on_hover_without_a_shadow():
    body = _rule(r'\[class\*="st-key-card-"\]:hover')
    assert "border-color: var(--border-strong) !important;" in body and "box-shadow: none !important;" in body


# --- typography -------------------------------------------------------------------

_CONFIG_TEXT = (REPO_ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")


def test_font_families_are_ibm_plex_with_source_serif_and_the_noto_serif_kr_fallback():
    imports = re.findall(r"@import url\('([^']+)'\)", _CSS)
    assert len(imports) == 1
    url = imports[0]
    for family in ("family=IBM+Plex+Sans:wght@400;500;600", "family=IBM+Plex+Mono:wght@400;500",
                   "family=IBM+Plex+Sans+KR:wght@400;500;600", "family=IBM+Plex+Sans+JP:wght@400;500;600",
                   "family=Source+Serif+4:opsz,wght@8..60,400;8..60,600", "family=Noto+Serif+KR:wght@400;600"):
        assert family in url, family
    assert "display=swap" in url
    assert "Geist" not in _CSS and "Geist" not in _CONFIG_TEXT


def test_role_stacks_put_the_right_face_first_and_keep_cjk_fallbacks():
    assert '--font-ui: "IBM Plex Sans", "IBM Plex Sans KR", "IBM Plex Sans JP",' in _CSS
    assert '--font-ui-ja: "IBM Plex Sans", "IBM Plex Sans JP", "IBM Plex Sans KR",' in _CSS
    assert '--font-mono: "IBM Plex Mono",' in _CSS
    assert '--font-serif: "Source Serif 4", "Noto Serif KR",' in _CSS
    assert '--font-serif-ja: "Source Serif 4",' in _CSS


def test_japanese_text_takes_japanese_glyph_forms():
    assert ":lang(ja) { font-family: var(--font-ui-ja); }" in _CSS
    body = _rule(r"\.er-excerpt:lang\(ja\), \.er-filing-native:lang\(ja\), \.er-serif-title:lang\(ja\), \.er-serif-question:lang\(ja\)")
    assert "var(--font-serif-ja)" in body
    # The generic rule precedes every component rule, so mono and serif roles still win.
    assert _CSS.index(":lang(ja) {") < _CSS.index(".er-mono {") < _CSS.index(".er-excerpt:lang(ja)")


def test_numerals_are_tabular_everywhere():
    assert "font-variant-numeric: tabular-nums" in _rule(r'html, body, \[class\*="css"\]')


def test_page_and_card_title_scale():
    assert "--fs-page-title: 30px;" in _CSS and "--fs-card-title: 15px;" in _CSS and "--fs-label: 11px;" in _CSS
    page = _rule(r"\.er-page-title")
    assert "font-size: var(--fs-page-title)" in page and "font-weight: 600" in page and "letter-spacing: -0.02em" in page
    assert "font-size: var(--fs-page-title)" in _rule(r"\.er-greeting")
    for selector in (r"\.er-card-title", r'\[class\*="st-key-radar-item-"\] \.er-card-title', r"\.er-signal-headline", r"\.er-feed-title"):
        body = _rule(selector)
        assert "font-size: var(--fs-card-title)" in body and "font-weight: 600" in body, selector


def test_uppercase_labels_are_11px_mono_with_wide_tracking():
    for selector in (r"\.er-eyebrow", r"\.er-metric-label", r"\.er-rail-group-label", r"\.er-kv-label",
                     r"\.er-inset-label", r"\.er-date-group", r"table\.er-table th"):
        body = _rule(selector)
        assert "font-family: var(--font-mono)" in body, selector
        assert "font-size: var(--fs-label)" in body, selector
        assert "letter-spacing: 0.08em" in body and "text-transform: uppercase" in body, selector


def test_mono_carries_timestamps_tickers_codes_and_counts():
    for selector in (r"\.er-mono", r"\.er-metric-value", r"\.er-time-cell", r"\.er-topbar-date", r"\.er-filing-filed",
                     r"\.er-date-badge", r"\.er-rail-count", r"\.er-reference-line", r"\.er-order-marker",
                     r"\.er-divbar-value", r"\.er-spine-source", r"\.er-footer \.er-footer-version"):
        assert "font-family: var(--font-mono)" in _rule(selector), selector


def test_serif_is_reserved_for_verbatim_excerpts_and_thesis_type():
    for selector in (r"\.er-excerpt", r"\.er-serif-title", r"\.er-serif-question", r"\.er-filing-native"):
        assert "font-family: var(--font-serif)" in _rule(selector), selector


def test_native_theme_fonts_match_the_stylesheet_stacks():
    assert 'font = "IBM Plex Sans, IBM Plex Sans KR, IBM Plex Sans JP:https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:' in _CONFIG_TEXT
    assert 'headingFont = "IBM Plex Sans, IBM Plex Sans KR, IBM Plex Sans JP:' in _CONFIG_TEXT
    assert 'codeFont = "IBM Plex Mono:https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:' in _CONFIG_TEXT
