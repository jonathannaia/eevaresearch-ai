"""Visual-theme redesign (design/DECISIONS.md) — midnight-navy palette.
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

_EXPECTED_TOKENS = {
    "bg": "#07111F",
    "rail": "#0A1628",
    "surface": "#101F35",
    "surface-2": "#152944",
    "surface-input": "#0D1A2D",
    "hairline": "#243A57",
    "text": "#F1F5F9",
    "text-2": "#B8C5D6",
    "text-3": "#8091A8",
    "invert-bg": "#60A5FA",
    "accent": "#60A5FA",
    "accent-hover": "#93C5FD",
    "focus": "#93C5FD",
    "pos": "#34D399",
    "neg": "#FB7185",
    "mix": "#F6C65B",
}


def test_root_tokens_match_the_specified_midnight_navy_palette():
    for name, expected in _EXPECTED_TOKENS.items():
        assert _token(name) == expected, f"--{name} is {_token(name)!r}, expected {expected!r}"


def test_glow_token_matches_specified_primary_cta_glow():
    assert _token("glow") == "rgba(96, 165, 250, .24)"


# --- No legacy gray/charcoal hex survives anywhere in the stylesheet ---

_LEGACY_HEXES = [
    "#181818", "#212121", "#2A2A2A", "#303030", "#3A3A3A",
    "#ECECEC", "#B4B4B4", "#A8A8A8", "#8A8A8A",
    "#7CAE8C", "#C98A93", "#C7A968",
]


def test_no_legacy_gray_or_charcoal_hex_remains_in_stylesheet():
    offenders = [h for h in _LEGACY_HEXES if h in _CSS]
    assert offenders == [], f"legacy colors still present in styles.css: {offenders}"


def test_no_legacy_white_alpha_glow_or_focus_rings_remain():
    # The pre-redesign button glow/focus used plain white — every such
    # usage should now be an accent-tinted or token-based value instead.
    assert "rgba(255,255,255,.10)" not in _CSS
    assert "rgba(255,255,255,.22)" not in _CSS
    assert "rgba(255,255,255,.34)" not in _CSS
    assert "outline: 2px solid #fff" not in _CSS


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
    assert 'backgroundColor = "#07111F"' in _CONFIG
    assert 'secondaryBackgroundColor = "#0D1A2D"' in _CONFIG
    assert 'textColor = "#F1F5F9"' in _CONFIG
    assert 'primaryColor = "#60A5FA"' in _CONFIG
    assert 'base = "dark"' in _CONFIG  # still dark-only, no light mode


def test_config_toml_has_no_legacy_gray_values():
    for legacy in ('"#212121"', '"#2A2A2A"', '"#ECECEC"', '"#B4B4B4"', 'rgba(255, 255, 255, .08)'):
        assert legacy not in _CONFIG, f"legacy value {legacy!r} still present in config.toml"


# --- Component files with hardcoded (non-CSS-variable) colors ---

def test_charts_component_uses_new_palette_not_legacy_grays():
    source = (REPO_ROOT / "src" / "ui" / "components" / "charts.py").read_text(encoding="utf-8")
    for legacy in ("#ECECEC", "#B4B4B4", "#6E6E6E", "#303030", "#8F8F8F", "rgba(255,255,255,.14)"):
        assert legacy not in source
    assert "#F1F5F9" in source
    assert "#5578A0" in source


def test_watchlists_thesis_warning_uses_negative_semantic_token_not_raw_white():
    source = (REPO_ROOT / "src" / "ui" / "pages" / "watchlists.py").read_text(encoding="utf-8")
    assert "rgba(255,255,255,.025)" not in source
    assert "var(--neg-dim)" in source


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


_BACKGROUNDS = ["#07111F", "#0A1628", "#101F35", "#152944", "#0D1A2D"]


def test_text_tokens_meet_aa_normal_text_contrast_on_every_surface():
    for name in ("text", "text-2", "text-3"):
        color = _token(name)
        for bg in _BACKGROUNDS:
            ratio = _contrast_ratio(color, bg)
            assert ratio >= 4.5, f"--{name} ({color}) on {bg} is only {ratio:.2f}:1, below AA 4.5:1"


def test_accent_and_status_colors_meet_aa_ui_control_contrast_on_every_surface():
    for name in ("accent", "accent-hover", "focus", "pos", "neg", "mix"):
        color = _token(name)
        for bg in _BACKGROUNDS:
            ratio = _contrast_ratio(color, bg)
            assert ratio >= 3.0, f"--{name} ({color}) on {bg} is only {ratio:.2f}:1, below AA 3:1"


def test_functional_border_token_meets_aa_ui_control_contrast_on_every_surface():
    # --hairline-2 (not --hairline, which is decorative-only) is the one
    # used for secondary-button/input/chip boundaries.
    color = _token("hairline-2")
    for bg in _BACKGROUNDS:
        ratio = _contrast_ratio(color, bg)
        assert ratio >= 3.0, f"--hairline-2 ({color}) on {bg} is only {ratio:.2f}:1, below AA 3:1"


def test_primary_cta_label_meets_aa_contrast_on_its_own_button_fill():
    label, fill = _token("invert-fg"), _token("invert-bg")
    ratio = _contrast_ratio(label, fill)
    assert ratio >= 4.5, f"CTA label {label} on fill {fill} is only {ratio:.2f}:1, below AA 4.5:1"
