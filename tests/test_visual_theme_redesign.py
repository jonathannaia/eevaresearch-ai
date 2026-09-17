"""Visual-theme redesign (design/DECISIONS.md) — Midnight Teal
institutional-research palette (visual-hierarchy-and-token pass only —
no navigation/data/feature/architecture change). This is a full
replacement of the token system (not an additive layer), so this file's
own history is: originally written against the midnight-navy pass
(commit 6e34c76), then a light editorial (slate/navy-accent) palette,
then an indigo/editorial-white palette, then a near-black/indigo
application-shell dark pass, and now revised in place again for this
teal palette — same test names/shape (where the underlying token still
exists under the same or a renamed identity), new expected values each
time.

This is also the first pass to rename several tokens, not just revalue
them: --hairline/--hairline-2 -> --border-soft/--border, --text-2 ->
--text-secondary, --text-3/--text-4 (always-equal duplicates) collapse
into --text-muted, --surface-2/--surface-3 -> --surface-hover/
--surface-active, --pos/--neg/--mix -> --positive/--negative/--warning.
One genuinely new token is added: --info/--info-dim.

Pure static-content and contrast-math checks against assets/styles.css,
.streamlit/config.toml, and the component files with hardcoded (non
CSS-variable) colors. No AppTest/rendering here — this guards the design
tokens themselves, not page behavior (already covered by every other
AppTest suite, none of which this pass is meant to change)."""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
_CSS_PATH = REPO_ROOT / "assets" / "styles.css"
_CONFIG_PATH = REPO_ROOT / ".streamlit" / "config.toml"

_CSS = _CSS_PATH.read_text(encoding="utf-8")
_CONFIG = _CONFIG_PATH.read_text(encoding="utf-8")


def _token(name: str) -> str:
    match = re.search(rf"--{re.escape(name)}:\s*([^;]+);", _CSS)
    assert match, f"--{name} not found in styles.css"
    return match.group(1).strip()


# --- New palette tokens are present with the exact specified values ---
# (--text-muted is the one deliberate exception — see its own test below.)

_EXPECTED_TOKENS = {
    "bg": "#101417",
    "rail": "#0B0F11",
    "surface": "#171D20",
    "surface-hover": "#1D2529",
    "surface-active": "#202A2E",
    "surface-input": "#171D20",
    "border": "#263137",
    "border-soft": "#1E282C",
    "text": "#F1F4F2",
    "text-secondary": "#B1BCBB",
    "invert-fg": "#FFFFFF",
    "accent": "#21B7A8",
    "accent-hover": "#29CBBB",
    "accent-soft": "rgba(33, 183, 168, 0.14)",
    "focus": "#21B7A8",
    "link": "#21B7A8",
    "positive": "#3BCB8A",
    "negative": "#F07178",
    "warning": "#E4B65A",
    "info": "#62A8E5",
}


def test_root_tokens_match_the_specified_midnight_teal_palette():
    for name, expected in _EXPECTED_TOKENS.items():
        assert _token(name) == expected, f"--{name} is {_token(name)!r}, expected {expected!r}"


def test_text_muted_is_nudged_lighter_than_the_literal_spec_value():
    """#788583 (as given) measures only 4.06:1-4.45:1 against --surface/
    --surface-hover — under the 4.5:1 AA normal-text floor. Nudged
    lighter to #889593 (5.02:1 in the worst case) — the same "nudge the
    minimum amount needed, disclose it" precedent every earlier pass in
    this file has used at least once (e.g. the prior pass's own
    --hairline-2 nudge)."""
    assert _token("text-muted") == "#889593"


def test_glow_token_matches_the_new_teal_accent():
    assert _token("glow") == "rgba(33, 183, 168, .35)"


def test_invert_bg_is_a_new_derived_token_not_the_accent_itself():
    """A CTA button needs a solid fill dark/saturated enough to host
    white label text at >=4.5:1 — white on --accent measures only 2.5:1,
    and white on --accent-hover only 2.03:1, so neither can serve as a
    button fill directly (same reasoning the prior two palette passes
    already established for keeping --invert-bg distinct from --accent).
    --invert-bg/--invert-bg-hover are new, derived, darker teals: white
    label text measures 5.58:1 / 4.93:1 respectively, both clearing
    4.5:1 (see test_primary_cta_label_meets_aa_contrast_on_default_and_
    hover_fill below for the exact numbers pinned)."""
    assert _token("invert-bg") == "#0F7568"
    assert _token("invert-bg-hover") == "#157E72"


def test_accent_hover_is_now_a_text_hover_brighten_not_a_button_fill():
    """Reversed role from the prior pass: --accent-hover there was a
    dark, white-text-safe button-fill color; here it is deliberately
    LIGHTER than --accent (a hover-brighten shade for text/icon/link
    contexts — 7.68:1 as text against every surface). The primary CTA's
    own hover/active fill now reads --invert-bg-hover instead (see
    test_invert_bg_is_a_new_derived_token_not_the_accent_itself), and the
    plain a:hover rule reads --accent-hover again (the prior pass had
    redirected it to plain --accent for exactly the opposite reason —
    its own --accent-hover was too dark to use as text against a
    near-black page)."""
    assert "a:hover { color: var(--accent-hover); }" in _CSS
    assert "a:hover { color: var(--accent); }" not in _CSS
    assert "background: var(--invert-bg-hover) !important;" in _CSS
    assert "background: var(--accent-hover) !important;" not in _CSS


def test_border_and_border_soft_are_deliberately_not_held_to_3to1():
    """Unlike the prior pass's --hairline-2 (a functional-boundary token
    explicitly nudged to clear 3:1), this pass's --border and
    --border-soft are BOTH used exactly as specified, un-nudged — both
    measure well under 3:1 against every real surface (see
    test_border_tokens_do_not_meet_the_old_functional_boundary_floor
    below for the exact numbers). This is intentional, not a regression:
    "reduce excessive card-border visibility" needs a genuinely quiet
    border, and WCAG's 1.4.11 non-text-contrast rule was never meant to
    hold a purely decorative card/row divider to 3:1 in the first place
    (this file already carried that exact exemption for the prior
    pass's own --hairline; it now applies to both border tokens here).
    The one place a boundary must still be reliably perceivable —
    interactive-control focus — is carried entirely by --focus (an
    --accent copy, checked separately below), never by --border's own
    contrast."""
    assert _token("border") == "#263137"
    assert _token("border-soft") == "#1E282C"


# --- No legacy palette color (including the retired indigo/purple accent
# family) survives anywhere in the stylesheet ---

_LEGACY_HEXES = [
    # midnight-navy pass (commit 6e34c76)
    "#07111F", "#0A1628", "#101F35", "#152944", "#1B3352", "#0D1A2D",
    "#243A57", "#5578A0", "#B8C5D6", "#8091A8",
    "#60A5FA", "#93C5FD",
    "#34D399", "#FB7185", "#F6C65B",
    # original neutral-gray pass
    "#181818", "#212121", "#2A2A2A", "#303030", "#3A3A3A",
    "#ECECEC", "#B4B4B4", "#A8A8A8", "#8A8A8A",
    "#7CAE8C", "#C98A93", "#C7A968",
    # light-editorial pass (slate/navy-accent)
    "#F8FAFC", "#F1F5F9", "#F8FBFF", "#D9E2EC", "#0F172A", "#334155",
    "#64748B", "#102A43", "#163E68", "#2563EB", "#1D4ED8", "#E2E8F0",
    # indigo/editorial-white pass
    "#FAFAFA", "#F3F4F6", "#E5E7EB", "#7A8DA2", "#D1D5DB", "#111827", "#374151",
    "#EEF2FF", "#087F5B", "#B4233C", "#9A6700",
    # application-shell dark/indigo pass — the entire indigo/purple accent
    # family this Midnight Teal pass explicitly retires, plus that pass's
    # own surfaces/text/status colors. #6B7280 was that pass's --border
    # (functional-boundary) value; no coincidental reuse this time, so it
    # is listed here without a carve-out.
    "#0B0D10", "#0E1013", "#16181D", "#1D2026", "#262A31",
    "#23262D", "#6B7280", "#838B99", "#F5F6F7", "#B7BCC4", "#8B909A",
    "#4F46E5", "#818CF8", "#4338CA", "#23223F",
    "#2ED99C", "#F87171", "#FBBF24",
]


def test_no_legacy_theme_hex_remains_in_stylesheet():
    # Case-insensitive: CSS hex is case-insensitive and a prior pass might
    # have written lowercase in a comment.
    upper_css = _CSS.upper()
    offenders = [h for h in _LEGACY_HEXES if h.upper() in upper_css]
    assert offenders == [], f"legacy colors still present in styles.css: {offenders}"


def test_no_stale_indigo_or_purple_brand_tokens_remain_in_the_public_shell():
    """Direct, literal proof of the task's own explicit requirement: the
    entire indigo/purple accent family (#818CF8 the old --accent,
    #4F46E5 the old dark-pass --invert-bg/glow base, #4338CA the old
    --accent-hover, #23223F the old --accent-soft) is gone — checked
    together, by name, rather than only relying on the broader
    _LEGACY_HEXES sweep above."""
    upper_css = _CSS.upper()
    for legacy in ("#818CF8", "#4F46E5", "#4338CA", "#23223F"):
        assert legacy.upper() not in upper_css, f"stale indigo/purple hex {legacy} still present"
    # The one place any of these four survive is this file's own dated
    # changelog-style header comment, naming the retired pass by name —
    # documented history, never a live rule. Confirmed separately: every
    # test above/below this one already proves the *live* --accent/
    # --invert-bg/--accent-hover/--accent-soft tokens hold the new teal
    # values, so a literal-hex sweep finding zero matches (checked here)
    # really does mean zero live usage, not a false negative from a
    # comment mention masking a real one.


def test_no_legacy_glow_or_alpha_rgba_remains():
    # Both the light-editorial-indigo pass's own glow rgba triple
    # (79, 70, 229 == #4F46E5) and the application-shell dark pass's own
    # (129, 140, 248 == #818CF8) must be gone from --glow specifically.
    assert _token("glow") not in ("rgba(79, 70, 229, .18)", "rgba(129, 140, 248, .35)")
    assert "rgba(79,70,229" not in _CSS
    assert "rgba(67,56,202" not in _CSS
    assert "rgba(96,165,250" not in _CSS
    assert "rgba(0,0,0,.4)" not in _CSS
    assert "rgba(0,0,0,.45)" not in _CSS
    assert "rgba(255, 255, 255, .16)" not in _CSS  # old .er-chip-interpretation
    assert "rgba(255, 255, 255, .92)" not in _CSS  # retired .er-topbar-anchor background
    assert "rgba(16, 42, 67" not in _CSS  # stray pre-token-era .er-chip-interpretation literal


def test_theme_is_dark_only_no_prefers_color_scheme_media_query():
    # This is a hard, unconditional dark theme (base = "dark" in
    # config.toml + matching CSS tokens) — never a light/dark toggle keyed
    # off the OS/browser's own prefers-color-scheme.
    assert "prefers-color-scheme" not in _CSS


def test_topbar_anchor_class_and_retired_topbar_only_selectors_are_gone():
    """Application-shell dark/dim pass: the sticky top bar (search +
    account avatar, above the main content) was retired — both controls
    moved into the sidebar (search beside the brand mark at top, account
    anchored to the bottom) — so none of its own CSS should remain.
    Unaffected by this Midnight Teal pass; still checked here so this
    file stays the one place that guards it."""
    assert ".er-topbar-anchor" not in _CSS
    assert "st-key-topbar-avatar-" not in _CSS
    assert ".er-topbar-avatar-email" not in _CSS


# --- Surface hierarchy: canvas, sidebar, card, hover/selected are each
# individually distinguishable, not just "dark vs. less dark" ---

def test_surface_hierarchy_is_a_strictly_increasing_luminance_ladder():
    """"Increase surface hierarchy between the canvas, sidebar, cards/
    panels, and hover/selected states" — checked directly against the
    real relative-luminance math (same formula the contrast-ratio tests
    below use), not just "the hex strings differ." --rail sits below
    --bg (the sidebar recedes slightly behind the main canvas); the
    elevation ladder proper (--surface < --surface-hover <
    --surface-active) is strictly increasing as each rule intends: a
    resting card, its hover state, and a selected/active state are each
    a visibly distinct step up."""
    ladder = ["rail", "bg", "surface", "surface-hover", "surface-active"]
    luminances = [_relative_luminance(_token(name)) for name in ladder]
    assert luminances == sorted(luminances), f"surface ladder not strictly increasing: {list(zip(ladder, luminances))}"
    assert len(set(luminances)) == len(luminances), "two surface tiers share the same luminance"


# --- Nav active-state: subtle teal-tinted background + narrow left accent ---

def test_active_nav_item_gets_a_teal_tinted_background_and_left_accent_bar():
    """"Replace broad gray active-navigation blocks with a subtle
    teal-tinted active background plus a narrow left accent indicator" —
    checked directly against the real active-nav-item rule. Keyed off
    the item's own container key (real DOM ancestor of the page_link —
    see src/ui/ui.py::render_sidebar()), not the prior, broken
    ".er-rail-navactive" nested-div wrapper (two separate st.markdown()
    calls never actually nest around another Streamlit element in the
    real browser DOM — confirmed live; this rule never matched anything
    in production before this pass's fix)."""
    match = re.search(r'\[class\*="st-key-navitem-"\]\[class\*="-active"\] \[data-testid="stPageLink"\] a\s*\{([^}]*)\}', _CSS, re.DOTALL)
    assert match, "active nav item rule not found"
    assert ".er-rail-navactive" not in _CSS, "the broken nested-div active-state wrapper should no longer be referenced anywhere"
    body = match.group(1)
    assert "background: var(--accent-soft)" in body
    assert "color: var(--accent)" in body
    assert re.search(r"border-left:\s*2px solid var\(--accent\)", body), "no left accent indicator on the active nav item"


def test_inactive_nav_item_is_quiet_teal_only_appears_on_hover_or_active():
    """"Make inactive navigation quiet; apply teal only on hover/active
    interaction" — the resting nav-link rule must carry no accent color
    anywhere (only --text-secondary, plus a transparent placeholder left
    border so the active state's real border doesn't shift row layout);
    hover gets a light text-only teal touch; only the active rule
    (checked above) gets the full tinted-background + left-bar
    treatment."""
    resting_match = re.search(
        r'\[data-testid="stSidebar"\] \[data-testid="stPageLink"\] a \{([^}]*)\}', _CSS, re.DOTALL,
    )
    assert resting_match, "resting sidebar nav-link rule not found"
    resting_body = resting_match.group(1)
    assert "var(--accent)" not in resting_body and "var(--accent-hover)" not in resting_body
    assert "var(--text-secondary)" in resting_body
    assert re.search(r"border-left:\s*2px solid transparent", resting_body)

    hover_match = re.search(
        r'\[data-testid="stSidebar"\] \[data-testid="stPageLink"\] a:hover\s*\{([^}]*)\}', _CSS, re.DOTALL,
    )
    assert hover_match, "sidebar nav-link hover rule not found"
    assert "var(--accent-hover)" in hover_match.group(1)


# --- Account avatar no longer uses purple ---

def test_sidebar_account_avatar_uses_the_new_accent_not_the_retired_indigo():
    match = re.search(
        r'\[class\*="st-key-sidebar-account-"\] \[data-testid="stPopoverButton"\],\s*'
        r'\[class\*="st-key-sidebar-account-"\] \[data-testid="stBaseButton-secondary"\]\s*\{([^}]*)\}',
        _CSS, re.DOTALL,
    )
    assert match, "sidebar account avatar rule not found"
    assert "background: var(--accent)" in match.group(1)


# --- Search/icon controls: lighter at rest, consistent hover/focus ---

def test_compact_search_trigger_is_borderless_at_rest_with_a_teal_hover():
    assert "border-color: transparent !important; color: var(--text-secondary) !important;" in _CSS
    hover_match = re.search(
        r'\.er-rail-brand \[class\*="st-key-cta-secondary-cmdk-trigger"\] \[data-testid="stBaseButton-secondary"\]:hover\s*\{([^}]*)\}',
        _CSS, re.DOTALL,
    )
    assert hover_match, "compact search trigger hover rule not found"
    assert "var(--accent)" in hover_match.group(1)


def test_sidebar_collapse_expand_buttons_are_borderless_at_rest_with_a_teal_hover():
    assert '[data-testid="stSidebarCollapseButton"] button:hover,\n[data-testid="stExpandSidebarButton"]:hover {\n    background: var(--surface-active) !important;\n    border-color: var(--accent) !important;' in _CSS


# --- Reduced pill-like metadata styling ---

def test_status_tags_and_fresh_badges_no_longer_use_a_full_pill_radius():
    """"Reduce excessive card-border visibility and pill-like metadata
    styling" — .er-status-tag and .er-fresh both move from a full pill
    (999px) to --r-sm. The evidence-chip system (.er-chip — Fact/
    Interpretation/Inference/Uncertainty, the product's own signature
    element) is deliberately untouched: reshaping it is a product-
    identity decision, out of scope for a visual-hierarchy-and-token
    pass."""
    status_tag_match = re.search(r"\.er-status-tag \{([^}]*)\}", _CSS, re.DOTALL)
    assert status_tag_match and "border-radius: var(--r-sm)" in status_tag_match.group(1)
    fresh_match = re.search(r"\.er-fresh \{([^}]*)\}", _CSS, re.DOTALL)
    assert fresh_match and "border-radius: var(--r-sm)" in fresh_match.group(1)
    # .er-chip itself (the signature element) still uses 999px — proves
    # the exclusion above is real, not just "no pills left anywhere".
    chip_match = re.search(r"\.er-chip \{([^}]*)\}", _CSS, re.DOTALL)
    assert chip_match and "border-radius: 999px" in chip_match.group(1)


def test_info_status_tag_uses_the_new_info_semantic_token():
    match = re.search(r"\.er-status-tag\.er-tag-info \{([^}]*)\}", _CSS)
    assert match, "er-tag-info rule not found"
    assert "var(--info" in match.group(1)


# --- Dashboard update rows: denser, lower border prominence, smaller radius ---

def test_dashboard_recently_updated_rows_are_denser_with_a_smaller_radius():
    assert '[class*="st-key-card-recently-updated-"] .er-row { padding: 4px 0; }' in _CSS
    assert '[class*="st-key-card-recently-updated-"] { border-radius: var(--r-sm) !important; }' in _CSS


# --- Retained/changed mechanics: button radius, glow effect, fonts ---

def test_primary_cta_uses_the_8px_radius_not_a_pill_and_keeps_the_glow():
    """Unchanged mechanic across every palette pass so far — only the
    color values feeding it have ever moved. The glow itself stays a
    box-shadow blur, never a background-image gradient — no gradients
    were introduced anywhere in this pass."""
    assert "border-radius: var(--r-sm) !important;" in _CSS
    assert "0 0 22px var(--glow)" in _CSS
    assert "linear-gradient" not in _CSS
    assert "radial-gradient" not in _CSS


def test_fonts_are_unchanged():
    assert '--font-ui: "Inter", sans-serif;' in _CSS
    assert '--font-mono: "JetBrains Mono", monospace;' in _CSS
    assert '--font-serif: "Source Serif 4"' in _CSS


# --- Keyboard focus: every focusable control gets a visible ring ---

def test_sidebar_account_popover_gets_a_visible_focus_ring():
    """stPopoverButton (the sidebar's circular account-avatar trigger)
    carries only its own testid, never stBaseButton-secondary — unlike
    every other button in the app, so it was silently missing from the
    shared focus-visible selector list until a keyboard/focus audit
    caught it two passes ago. Unaffected by this Midnight Teal pass
    (still checked here so it can't silently regress): (1) stPopoverButton
    is part of the same shared --focus outline rule as every other
    interactive control, and (2) it gets its own circular outline-radius
    override so the ring matches the button's circular shape."""
    shared_rule_match = re.search(r'\[data-testid="stPageLink"\] a:focus-visible,.*?\{[^}]*outline:\s*2px solid var\(--focus\)', _CSS, re.DOTALL)
    assert shared_rule_match, "shared focus-visible rule block not found"
    assert '[data-testid="stPopoverButton"]:focus-visible' in shared_rule_match.group(0)

    circular_override_match = re.search(r'\[data-testid="stPopoverButton"\]:focus-visible\s*\{([^}]*)\}', _CSS)
    assert circular_override_match, "stPopoverButton circular focus-ring override not found"
    assert "border-radius: 50%" in circular_override_match.group(1)


# --- .streamlit/config.toml native-widget theme matches the new palette ---

def test_config_toml_theme_matches_new_palette():
    assert 'backgroundColor = "#101417"' in _CONFIG
    assert 'secondaryBackgroundColor = "#1D2529"' in _CONFIG
    assert 'textColor = "#F1F4F2"' in _CONFIG
    assert 'primaryColor = "#21B7A8"' in _CONFIG
    assert 'linkColor = "#21B7A8"' in _CONFIG
    assert 'base = "dark"' in _CONFIG


def test_config_toml_has_no_legacy_values():
    for legacy in (
        '"#102A43"', '"#F8FAFC"', '"#0F172A"', '"#1D4ED8"', '"#7A8DA2"',
        'base = "light"',
        '"#07111F"', '"#0D1A2D"', '"#5578A0"', '"#60A5FA"', '"#F1F5F9"',
        '"#212121"', '"#2A2A2A"', '"#ECECEC"', '"#B4B4B4"',
        # application-shell dark/indigo pass, retired by this pass
        '"#0B0D10"', '"#1D2026"', '"#F5F6F7"', '"#818CF8"', '"#6B7280"',
    ):
        assert legacy not in _CONFIG, f"legacy value {legacy!r} still present in config.toml"


# --- Component files with hardcoded (non-CSS-variable) colors ---

def test_charts_component_uses_new_palette_not_legacy_colors():
    source = (REPO_ROOT / "src" / "ui" / "components" / "charts.py").read_text(encoding="utf-8")
    for legacy in (
        "#F1F5F9", "#B8C5D6", "#5578A0", "#152944", "#8091A8",  # navy pass
        "#ECECEC", "#B4B4B4", "#6E6E6E", "#303030", "#8F8F8F",  # original
        "#0F172A", "#64748B", "#D9E2EC",  # indigo/editorial-white pass
        "#F5F6F7", "#B7BCC4", "#8B909A", "#23262D",  # application-shell dark/indigo pass
        "rgba(85,120,160", "rgba(255,255,255,.14)", "rgba(122,141,162", "rgba(139,144,154",
    ):
        assert legacy not in source
    assert "#F1F4F2" in source
    assert "#889593" in source


def test_no_python_source_file_references_a_retired_css_token_name():
    """The Midnight Teal pass renamed several tokens (--hairline/-2,
    --text-2/-3/-4, --surface-2/-3, --pos/--neg/--mix) — this proves no
    Python file's own inline `style="..."` string still references an
    old name (styles.css itself is checked by the hex/token assertions
    above; this covers the two component files that build inline CSS
    strings directly, e.g. src/ui/components/cards.py's per-signal
    left-rail color)."""
    src_root = REPO_ROOT / "src"
    retired_patterns = [
        r"var\(--hairline\b", r"var\(--text-2\b", r"var\(--text-3\b", r"var\(--text-4\b",
        r"var\(--surface-2\b", r"var\(--surface-3\b", r"var\(--pos\b", r"var\(--neg\b", r"var\(--mix\b",
    ]
    offenders = []
    for path in src_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pattern in retired_patterns:
            if re.search(pattern, text):
                offenders.append((str(path.relative_to(REPO_ROOT)), pattern))
    assert offenders == [], f"retired token references still present: {offenders}"


# --- WCAG AA contrast math on the actual token values ---

def _linearize(channel: float) -> float:
    c = channel / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _relative_luminance(hex_color: str) -> float:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _linearize(r) + 0.7152 * _linearize(g) + 0.0722 * _linearize(b)


def _contrast_ratio(fg: str, bg: str) -> float:
    l1, l2 = _relative_luminance(fg), _relative_luminance(bg)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


# The four real app surfaces text/controls actually render on.
_MAIN_BACKGROUNDS = ["#101417", "#171D20", "#1D2529"]
_ALL_BACKGROUNDS = ["#101417", "#0B0F11", "#171D20", "#1D2529"]


def test_primary_and_secondary_text_meet_aa_normal_text_contrast_on_every_surface():
    for name in ("text", "text-secondary"):
        color = _token(name)
        for bg in _ALL_BACKGROUNDS:
            ratio = _contrast_ratio(color, bg)
            assert ratio >= 4.5, f"--{name} ({color}) on {bg} is only {ratio:.2f}:1, below AA 4.5:1"


def test_muted_text_meets_aa_normal_text_contrast_on_every_surface():
    """Unlike the prior two palettes (where muted text fell just under
    4.5:1 against the sidebar background specifically and needed a
    per-selector --text-secondary override, still present and still
    checked below), this palette's own --text-muted clears 4.5:1 against
    all four real surfaces, including --rail (6.2:1)."""
    color = _token("text-muted")
    for bg in _ALL_BACKGROUNDS:
        ratio = _contrast_ratio(color, bg)
        assert ratio >= 4.5, f"--text-muted ({color}) on {bg} is only {ratio:.2f}:1, below AA 4.5:1"


def test_muted_text_sidebar_override_is_still_present_though_no_longer_load_bearing():
    """Kept from the prior passes (see the previous test's docstring) —
    the --text-secondary sidebar-only overrides are still real and still
    correct, so this still checks they're in place rather than
    re-deriving the sidebar DOM structure here."""
    sidebar_muted_selectors = [
        r"\.er-rail-group-label\s*\{[^}]*color:\s*var\(--text-secondary\)",
        r"\.er-rail-status\s*\{[^}]*color:\s*var\(--text-secondary\)",
        r'\.er-rail-footlinks \[data-testid="stPageLink"\] a p\s*\{[^}]*color:\s*var\(--text-secondary\)',
    ]
    for pattern in sidebar_muted_selectors:
        assert re.search(pattern, _CSS, re.DOTALL), f"expected sidebar text-secondary fix not found for pattern: {pattern}"


def test_link_and_accent_hover_meet_aa_normal_text_contrast_on_every_surface():
    for name in ("link", "accent", "accent-hover"):
        color = _token(name)
        for bg in _ALL_BACKGROUNDS:
            ratio = _contrast_ratio(color, bg)
            assert ratio >= 4.5, f"--{name} ({color}) on {bg} is only {ratio:.2f}:1, below AA 4.5:1"


def test_status_colors_meet_aa_normal_text_contrast_as_glyphs_on_main_surfaces():
    # positive/negative/warning/info render as text-weight glyphs/labels
    # only within main-content-area components — never in the sidebar,
    # which has no direction/status/alert component of its own.
    for name in ("positive", "negative", "warning", "info"):
        color = _token(name)
        for bg in _MAIN_BACKGROUNDS:
            ratio = _contrast_ratio(color, bg)
            assert ratio >= 4.5, f"--{name} ({color}) on {bg} is only {ratio:.2f}:1, below AA 4.5:1"


def test_status_colors_meet_aa_text_contrast_on_their_own_dim_tint_pill():
    """Status-tag pills render the status color as text on a low-alpha
    tint of itself (--positive-dim/--negative-dim/--warning-dim/
    --info-dim), composited over --surface — the real ambient background
    every status tag/chip/alert actually renders on in this app."""
    surface_rgb = tuple(int(_token("surface").lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
    for name, dim_name in [("positive", "positive-dim"), ("negative", "negative-dim"), ("warning", "warning-dim"), ("info", "info-dim")]:
        color = _token(name)
        alpha_match = re.search(r",\s*\.(\d+)\)", _token(dim_name))
        assert alpha_match, f"could not parse alpha out of --{dim_name}: {_token(dim_name)!r}"
        alpha = float(f"0.{alpha_match.group(1)}")
        r, g, b = (int(color.lstrip('#')[i : i + 2], 16) for i in (0, 2, 4))
        sr, sg, sb = surface_rgb
        tint = "#{:02X}{:02X}{:02X}".format(
            round(r * alpha + sr * (1 - alpha)),
            round(g * alpha + sg * (1 - alpha)),
            round(b * alpha + sb * (1 - alpha)),
        )
        ratio = _contrast_ratio(color, tint)
        assert ratio >= 4.5, f"--{name} ({color}) on its own {dim_name} tint ({tint}) is only {ratio:.2f}:1"


def test_focus_ring_meets_aa_ui_control_contrast_on_every_surface():
    color = _token("focus")
    for bg in _ALL_BACKGROUNDS:
        ratio = _contrast_ratio(color, bg)
        assert ratio >= 3.0, f"--focus ({color}) on {bg} is only {ratio:.2f}:1, below AA 3:1"


def test_border_tokens_do_not_meet_the_old_functional_boundary_floor():
    """Documents the (accepted, requested) shortfall for both border
    tokens — see test_border_and_border_soft_are_deliberately_not_held_
    to_3to1's own docstring for why this is intentional under this
    pass's "reduce excessive card-border visibility" direction, not a
    regression from the prior pass's own 3:1-held --hairline-2."""
    for name in ("border", "border-soft"):
        color = _token(name)
        for bg in _ALL_BACKGROUNDS:
            ratio = _contrast_ratio(color, bg)
            assert ratio < 3.0, f"--{name} ({color}) on {bg} is {ratio:.2f}:1 — expected < 3:1 (documents the accepted shortfall)"


def test_primary_cta_label_meets_aa_contrast_on_default_and_hover_fill():
    """White button label against both the default (--invert-bg,
    #0F7568, 5.58:1) and hover (--invert-bg-hover, #157E72, 4.93:1)
    fills — both new, derived tokens for this pass (see
    test_invert_bg_is_a_new_derived_token_not_the_accent_itself)."""
    label = _token("invert-fg")
    for fill_name in ("invert-bg", "invert-bg-hover"):
        fill = _token(fill_name)
        ratio = _contrast_ratio(label, fill)
        assert ratio >= 4.5, f"CTA label {label} on {fill_name} ({fill}) is only {ratio:.2f}:1, below AA 4.5:1"
