"""Visual-theme redesign (design/DECISIONS.md) — light editorial palette.
This is a full replacement of the token system (not an additive layer),
so this file's own history is: originally written against the midnight-
navy pass (commit 6e34c76), now revised in place for the light palette
that replaces it — same test names/shape, new expected values.

Pure static-content and contrast-math checks against assets/styles.css,
.streamlit/config.toml, and the two component files with hardcoded (non
CSS-variable) colors. No AppTest/rendering here — this guards the design
tokens themselves, not page behavior (already covered by every other
AppTest suite, none of which this redesign is meant to change)."""
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
# (--hairline-2 is the one deliberate exception — see its own test below.)

_EXPECTED_TOKENS = {
    "bg": "#F8FAFC",
    "rail": "#F1F5F9",
    "surface": "#FFFFFF",
    "surface-2": "#F8FBFF",
    "surface-input": "#FFFFFF",
    "hairline": "#D9E2EC",
    "text": "#0F172A",
    "text-2": "#334155",
    "text-3": "#64748B",
    "invert-bg": "#102A43",
    "invert-fg": "#FFFFFF",
    "accent": "#102A43",
    "accent-hover": "#163E68",
    "focus": "#2563EB",
    "link": "#1D4ED8",
    "pos": "#087F5B",
    "neg": "#B4233C",
    "mix": "#9A6700",
}


def test_root_tokens_match_the_specified_light_editorial_palette():
    for name, expected in _EXPECTED_TOKENS.items():
        assert _token(name) == expected, f"--{name} is {_token(name)!r}, expected {expected!r}"


def test_glow_token_matches_specified_primary_cta_glow():
    assert _token("glow") == "rgba(22, 62, 104, .18)"


def test_functional_border_is_deliberately_darker_than_the_literal_spec_value():
    """#8FA6BF (as literally specified) measures only 2.29:1-2.51:1 against
    this palette's four backgrounds — short of the 3:1 AA non-text-contrast
    minimum a functional widget/chip/input boundary needs (and, since
    --surface-input == --surface == #FFFFFF here, the border is often the
    *only* way an input's boundary is perceivable at all). #7A8DA2 is the
    same color ~15% darker, the lightest step that still clears 3:1 in the
    worst case (the sidebar background). The literal #8FA6BF value
    legitimately still appears in this file's own explanatory comment
    about the adjustment — this only checks the *token itself* is the
    darker value, not that the spec hex goes unmentioned."""
    assert _token("hairline-2") == "#7A8DA2"


# --- No legacy dark/navy-theme color survives anywhere in the stylesheet ---

_LEGACY_HEXES = [
    # midnight-navy pass (commit 6e34c76) — #F1F5F9 deliberately excluded:
    # it was that pass's --text value, but is *also* this pass's correct
    # --rail value (a coincidental hex collision between two unrelated
    # palettes), so it's expected to still be present, just in a different
    # role (checked directly by test_root_tokens_match_the_specified_
    # light_editorial_palette's --rail assertion above).
    "#07111F", "#0A1628", "#101F35", "#152944", "#1B3352", "#0D1A2D",
    "#243A57", "#5578A0", "#B8C5D6", "#8091A8",
    "#60A5FA", "#93C5FD",
    "#34D399", "#FB7185", "#F6C65B",
    # original neutral-gray pass
    "#181818", "#212121", "#2A2A2A", "#303030", "#3A3A3A",
    "#ECECEC", "#B4B4B4", "#A8A8A8", "#8A8A8A",
    "#7CAE8C", "#C98A93", "#C7A968",
]


def test_no_legacy_dark_theme_hex_remains_in_stylesheet():
    # Case-insensitive: CSS hex is case-insensitive and a prior pass might
    # have written lowercase in a comment.
    upper_css = _CSS.upper()
    offenders = [h for h in _LEGACY_HEXES if h.upper() in upper_css]
    assert offenders == [], f"legacy colors still present in styles.css: {offenders}"


def test_no_legacy_white_alpha_or_dark_shadow_glow_remains():
    # The midnight-navy pass's blue-tinted glow/ring/shadow rgba() triples
    # should all be gone, replaced by the navy-tinted ones below.
    assert "rgba(96,165,250" not in _CSS
    assert "rgba(0,0,0,.4)" not in _CSS
    assert "rgba(0,0,0,.45)" not in _CSS
    assert "rgba(255, 255, 255, .16)" not in _CSS  # old .er-chip-interpretation


def test_theme_is_light_only_no_dark_mode_media_query():
    assert "prefers-color-scheme" not in _CSS


# --- Retained mechanics: pill shape + glow effect + fonts unchanged ---

def test_primary_cta_keeps_pill_shape_and_glow_effect():
    assert "border-radius: 999px !important;" in _CSS
    assert "0 0 22px var(--glow)" in _CSS


def test_fonts_are_unchanged():
    assert '--font-ui: "Inter", sans-serif;' in _CSS
    assert '--font-mono: "JetBrains Mono", monospace;' in _CSS
    assert '--font-serif: "Source Serif 4"' in _CSS


# --- .streamlit/config.toml native-widget theme matches the new palette ---

def test_config_toml_theme_matches_new_palette():
    assert 'backgroundColor = "#F8FAFC"' in _CONFIG
    assert 'secondaryBackgroundColor = "#FFFFFF"' in _CONFIG
    assert 'textColor = "#0F172A"' in _CONFIG
    assert 'primaryColor = "#102A43"' in _CONFIG
    assert 'linkColor = "#1D4ED8"' in _CONFIG
    assert 'base = "light"' in _CONFIG


def test_config_toml_has_no_legacy_dark_theme_values():
    for legacy in (
        '"#07111F"', '"#0D1A2D"', '"#5578A0"', '"#60A5FA"', '"#F1F5F9"',
        '"#212121"', '"#2A2A2A"', '"#ECECEC"', '"#B4B4B4"', 'base = "dark"',
    ):
        assert legacy not in _CONFIG, f"legacy value {legacy!r} still present in config.toml"


# --- Component files with hardcoded (non-CSS-variable) colors ---

def test_charts_component_uses_new_palette_not_legacy_colors():
    source = (REPO_ROOT / "src" / "ui" / "components" / "charts.py").read_text(encoding="utf-8")
    for legacy in (
        "#F1F5F9", "#B8C5D6", "#5578A0", "#152944", "#8091A8",  # navy pass
        "#ECECEC", "#B4B4B4", "#6E6E6E", "#303030", "#8F8F8F",  # original
        "rgba(85,120,160", "rgba(255,255,255,.14)",
    ):
        assert legacy not in source
    assert "#0F172A" in source
    assert "#64748B" in source


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


# The four real app surfaces text/controls actually render on. --rail is
# the one background text-3/text-4 (muted) is *not* verified against here
# — see test_muted_text_is_not_used_directly_on_the_sidebar_background.
_MAIN_BACKGROUNDS = ["#F8FAFC", "#FFFFFF", "#F8FBFF"]
_ALL_BACKGROUNDS = ["#F8FAFC", "#F1F5F9", "#FFFFFF", "#F8FBFF"]


def test_primary_and_secondary_text_meet_aa_normal_text_contrast_on_every_surface():
    for name in ("text", "text-2"):
        color = _token(name)
        for bg in _ALL_BACKGROUNDS:
            ratio = _contrast_ratio(color, bg)
            assert ratio >= 4.5, f"--{name} ({color}) on {bg} is only {ratio:.2f}:1, below AA 4.5:1"


def test_muted_text_meets_aa_normal_text_contrast_on_page_card_and_elevated_surfaces():
    # Muted (#64748B) clears 4.5:1 on --bg/--surface/--surface-2 (4.55-
    # 4.76:1) but is 4.34:1 on --rail (--rail is intentionally excluded
    # here; see the next test for why that pairing never actually occurs).
    color = _token("text-3")
    for bg in _MAIN_BACKGROUNDS:
        ratio = _contrast_ratio(color, bg)
        assert ratio >= 4.5, f"--text-3 ({color}) on {bg} is only {ratio:.2f}:1, below AA 4.5:1"


def test_muted_text_is_not_used_directly_on_the_sidebar_background():
    """--text-3/--text-4 (muted, #64748B) measures 4.34:1 on --rail
    (#F1F5F9) — just under the 4.5:1 AA minimum. Every sidebar-only
    selector that would otherwise use muted text is bumped to --text-2
    (secondary, 9.4:1) instead (see the "sidebar contrast fix" comments in
    styles.css) — this checks that fix is actually in place for each of
    them, rather than re-deriving the sidebar DOM structure here."""
    # The two per-list-watchlist-shortcut selectors that used to be checked
    # here were removed along with that sidebar section itself (navigation-
    # cleanup pass, design/DECISIONS.md — Watchlists is now a primary
    # Workspace destination; the shortcuts pointed into Signals, no longer
    # a visible destination for them to target).
    sidebar_muted_selectors = [
        r"\.er-rail-group-label\s*\{[^}]*color:\s*var\(--text-2\)",
        r"\.er-rail-status\s*\{[^}]*color:\s*var\(--text-2\)",
        r'\.er-rail-footlinks \[data-testid="stPageLink"\] a p\s*\{[^}]*color:\s*var\(--text-2\)',
    ]
    for pattern in sidebar_muted_selectors:
        assert re.search(pattern, _CSS, re.DOTALL), f"expected sidebar text-2 fix not found for pattern: {pattern}"


def test_link_color_meets_aa_normal_text_contrast_on_every_surface():
    color = _token("link")
    for bg in _ALL_BACKGROUNDS:
        ratio = _contrast_ratio(color, bg)
        assert ratio >= 4.5, f"--link ({color}) on {bg} is only {ratio:.2f}:1, below AA 4.5:1"


def test_status_colors_meet_aa_normal_text_contrast_as_glyphs_on_main_surfaces():
    # pos/neg/mix render as text-weight glyphs/labels (direction glyphs,
    # status-tag/chip labels, alert labels) only within main-content-area
    # components (Dashboard/Themes/Signals/Company cards) — never in the
    # sidebar, which has no direction/status/alert component of its own —
    # so this is checked against --bg/--surface/--surface-2, not --rail.
    # --mix (#9A6700) is in fact only 4.44:1 on --rail specifically, which
    # is exactly why this check is scoped to where these colors are
    # actually used rather than every surface in the app.
    for name in ("pos", "neg", "mix"):
        color = _token(name)
        for bg in _MAIN_BACKGROUNDS:
            ratio = _contrast_ratio(color, bg)
            assert ratio >= 4.5, f"--{name} ({color}) on {bg} is only {ratio:.2f}:1, below AA 4.5:1"


def test_status_colors_meet_aa_text_contrast_on_their_own_dim_tint_pill():
    """Status-tag pills render the status color as text on a low-alpha
    tint of itself (--pos-dim/--neg-dim/--mix-dim) — this computes that
    exact tint (mixing the status color into #FFFFFF at the token's own
    alpha) and checks the resulting pairing, the same math used to choose
    .06 as the alpha in the first place (see the --pos-dim/etc. comment in
    styles.css: a higher alpha darkens the tint enough to fail 4.5:1)."""
    for name, dim_name in [("pos", "pos-dim"), ("neg", "neg-dim"), ("mix", "mix-dim")]:
        color = _token(name)
        alpha_match = re.search(r",\s*\.(\d+)\)", _token(dim_name))
        assert alpha_match, f"could not parse alpha out of --{dim_name}: {_token(dim_name)!r}"
        alpha = float(f"0.{alpha_match.group(1)}")
        r, g, b = (int(color.lstrip('#')[i : i + 2], 16) for i in (0, 2, 4))
        tint = "#{:02X}{:02X}{:02X}".format(
            round(r * alpha + 255 * (1 - alpha)),
            round(g * alpha + 255 * (1 - alpha)),
            round(b * alpha + 255 * (1 - alpha)),
        )
        ratio = _contrast_ratio(color, tint)
        assert ratio >= 4.5, f"--{name} ({color}) on its own {dim_name} tint ({tint}) is only {ratio:.2f}:1"


def test_focus_ring_meets_aa_ui_control_contrast_on_every_surface():
    color = _token("focus")
    for bg in _ALL_BACKGROUNDS:
        ratio = _contrast_ratio(color, bg)
        assert ratio >= 3.0, f"--focus ({color}) on {bg} is only {ratio:.2f}:1, below AA 3:1"


def test_functional_border_meets_aa_ui_control_contrast_on_every_surface():
    color = _token("hairline-2")
    for bg in _ALL_BACKGROUNDS:
        ratio = _contrast_ratio(color, bg)
        assert ratio >= 3.0, f"--hairline-2 ({color}) on {bg} is only {ratio:.2f}:1, below AA 3:1"


def test_primary_cta_label_meets_aa_contrast_on_default_and_hover_fill():
    """Explicitly required by the redesign brief: white button text
    against both the default (#102A43) and hover (#163E68) fills."""
    label = _token("invert-fg")
    for fill_name in ("invert-bg", "accent-hover"):
        fill = _token(fill_name)
        ratio = _contrast_ratio(label, fill)
        assert ratio >= 4.5, f"CTA label {label} on {fill_name} ({fill}) is only {ratio:.2f}:1, below AA 4.5:1"


def test_decorative_border_is_documented_as_intentionally_below_aa():
    """--hairline (#D9E2EC, ~1.2-1.3:1 against every surface) is used only
    for decorative dividers/footer rules/chart gridlines, never a
    functional control boundary (that role is --hairline-2, checked
    above) — WCAG's non-text-contrast rule doesn't apply to purely
    decorative separators, so this is intentionally not held to 3:1. This
    test just pins the value so a future change notices if --hairline
    silently becomes something a control boundary starts depending on."""
    color = _token("hairline")
    assert color == "#D9E2EC"
    for bg in _ALL_BACKGROUNDS:
        ratio = _contrast_ratio(color, bg)
        assert ratio < 3.0  # documents the (accepted) shortfall, not a bug
