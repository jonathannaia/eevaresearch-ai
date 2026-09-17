"""Visual-theme redesign (design/DECISIONS.md) — near-black/slate palette
(application-shell dark/dim pass, Perplexity-inspired sidebar layout).
This is a full replacement of the token system (not an additive layer),
so this file's own history is: originally written against the
midnight-navy pass (commit 6e34c76), then a light editorial (slate/
navy-accent) palette, then an indigo/editorial-white palette
(feat/ui-redesign-live), and now revised in place again for this dark
palette — same test names/shape, new expected values each time.

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
    "bg": "#0B0D10",
    "rail": "#0E1013",
    "surface": "#16181D",
    "surface-2": "#1D2026",
    "surface-input": "#16181D",
    "hairline": "#23262D",
    "text": "#F5F6F7",
    "text-2": "#B7BCC4",
    "text-3": "#8B909A",
    "invert-bg": "#4F46E5",
    "invert-fg": "#FFFFFF",
    "accent": "#818CF8",
    "accent-hover": "#4338CA",
    "focus": "#818CF8",
    "link": "#818CF8",
    "pos": "#2ED99C",
    "neg": "#F87171",
    "mix": "#FBBF24",
}


def test_root_tokens_match_the_specified_dark_palette():
    for name, expected in _EXPECTED_TOKENS.items():
        assert _token(name) == expected, f"--{name} is {_token(name)!r}, expected {expected!r}"


def test_glow_token_matches_specified_primary_cta_glow():
    assert _token("glow") == "rgba(129, 140, 248, .35)"


def test_accent_hover_is_deliberately_unchanged_from_the_prior_light_pass():
    """--accent-hover is a solid button-fill hover background (always
    under white label text) rather than a text/link color, so — unlike
    --accent, --link, and --focus, all re-tuned lighter for this pass —
    it keeps the prior light-editorial pass's own #4338CA: a fill's own
    darkness only has to work against the white text sitting on it
    (7.9:1, see the contrast tests below), never against the page's own
    ambient background. See this token's own :root comment, and the
    a:hover rule (which stopped reading this token for exactly the
    opposite reason — a dark fill color is low-contrast as plain text
    against this pass's near-black page)."""
    assert _token("accent-hover") == "#4338CA"
    assert "a:hover { color: var(--accent); }" in _CSS
    assert "a:hover { color: var(--accent-hover); }" not in _CSS


def test_functional_border_clears_aa_ui_control_contrast_against_every_surface():
    """--hairline-2 (#6B7280) is the lightest neutral slate that still
    clears the 3:1 AA non-text-contrast minimum against every one of this
    palette's four real surfaces — 3.38:1 against --surface-2, the
    lightest of the four and the binding constraint (see the exact math
    in test_functional_border_meets_aa_ui_control_contrast_on_every_
    surface below); a neighboring, slightly darker slate (e.g. #616873)
    was tried first and fell short of 3:1 against --surface-2 alone."""
    assert _token("hairline-2") == "#6B7280"


# --- No legacy palette color survives anywhere in the stylesheet ---

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
    # light-editorial pass (slate/navy-accent) — retired by the indigo pass
    "#F8FAFC", "#F1F5F9", "#F8FBFF", "#D9E2EC", "#0F172A", "#334155",
    "#64748B", "#102A43", "#163E68", "#2563EB", "#1D4ED8", "#E2E8F0",
    # indigo/editorial-white pass (feat/ui-redesign-live) — retired by this
    # dark pass. #6B7280 is deliberately NOT listed here: it was that
    # pass's own --text-3/--text-4 (muted text) value, and is *also* this
    # dark pass's --hairline-2 value (a coincidental hex collision between
    # two unrelated palettes, same as #F1F5F9's own carve-out two passes
    # ago) — checked directly by test_root_tokens_match_the_specified_
    # dark_palette's --hairline-2 assertion above, not "missing" from this
    # list. #4F46E5 is also NOT listed: it is still --invert-bg's real,
    # current, unchanged value (see that token's own comment).
    "#FAFAFA", "#F3F4F6", "#E5E7EB", "#7A8DA2", "#D1D5DB", "#111827", "#374151",
    "#EEF2FF", "#087F5B", "#B4233C", "#9A6700",
]


def test_no_legacy_theme_hex_remains_in_stylesheet():
    # Case-insensitive: CSS hex is case-insensitive and a prior pass might
    # have written lowercase in a comment.
    upper_css = _CSS.upper()
    offenders = [h for h in _LEGACY_HEXES if h.upper() in upper_css]
    assert offenders == [], f"legacy colors still present in styles.css: {offenders}"


def test_no_legacy_light_pass_alpha_or_glow_remains():
    # The light-editorial-indigo pass's own indigo-600 glow rgba triple
    # (79, 70, 229 == #4F46E5) should be gone from --glow specifically —
    # #4F46E5 itself legitimately still appears elsewhere (--invert-bg,
    # decorative box-shadow rgba(79,70,229,...) accents unrelated to
    # --glow, unchanged from the prior pass and still correct), so this
    # checks the --glow token's own value directly rather than searching
    # for the base hex/rgb triple anywhere in the file.
    assert _token("glow") != "rgba(79, 70, 229, .18)"
    assert "rgba(96,165,250" not in _CSS
    assert "rgba(0,0,0,.4)" not in _CSS
    assert "rgba(0,0,0,.45)" not in _CSS
    assert "rgba(255, 255, 255, .16)" not in _CSS  # old .er-chip-interpretation
    assert "rgba(255, 255, 255, .92)" not in _CSS  # retired .er-topbar-anchor background


def test_theme_is_dark_only_no_prefers_color_scheme_media_query():
    # This is a hard, unconditional dark theme (base = "dark" in
    # config.toml + matching CSS tokens) — never a light/dark toggle keyed
    # off the OS/browser's own prefers-color-scheme.
    assert "prefers-color-scheme" not in _CSS


def test_topbar_anchor_class_and_retired_topbar_only_selectors_are_gone():
    """Application-shell dark/dim pass: the sticky top bar (search +
    account avatar, above the main content) was retired — both controls
    moved into the sidebar (search beside the brand mark at top, account
    anchored to the bottom) — so none of its own CSS should remain."""
    assert ".er-topbar-anchor" not in _CSS
    assert "st-key-topbar-avatar-" not in _CSS
    assert ".er-topbar-avatar-email" not in _CSS


# --- Retained/changed mechanics: button radius, glow effect, fonts ---

def test_primary_cta_uses_the_8px_radius_not_a_pill_and_keeps_the_glow():
    """Unchanged mechanic across every palette pass so far — only the
    color values feeding it have ever moved."""
    assert "border-radius: var(--r-sm) !important;" in _CSS
    assert "0 0 22px var(--glow)" in _CSS


def test_status_tags_and_chips_still_use_pill_shape():
    assert "border-radius: 999px;" in _CSS


def test_fonts_are_unchanged():
    assert '--font-ui: "Inter", sans-serif;' in _CSS
    assert '--font-mono: "JetBrains Mono", monospace;' in _CSS
    assert '--font-serif: "Source Serif 4"' in _CSS


# --- Keyboard focus: every focusable control gets a visible ring ---

def test_sidebar_account_popover_gets_a_visible_focus_ring():
    """stPopoverButton (the sidebar's circular account-avatar trigger,
    application-shell dark/dim pass — previously the top bar's own
    avatar trigger before that bar was retired) carries only its own
    testid, never stBaseButton-secondary — unlike every other button in
    the app, so it was silently missing from the shared focus-visible
    selector list until this was caught in a keyboard/focus audit and
    fixed. This pins two things so the gap can't reappear silently: (1)
    stPopoverButton is part of the same shared --focus outline rule as
    every other interactive control, and (2) it gets its own circular
    outline-radius override so the ring matches the button's circular
    shape instead of the app's default squared outline."""
    shared_rule_match = re.search(r'\[data-testid="stPageLink"\] a:focus-visible,.*?\{[^}]*outline:\s*2px solid var\(--focus\)', _CSS, re.DOTALL)
    assert shared_rule_match, "shared focus-visible rule block not found"
    assert '[data-testid="stPopoverButton"]:focus-visible' in shared_rule_match.group(0)

    circular_override_match = re.search(r'\[data-testid="stPopoverButton"\]:focus-visible\s*\{([^}]*)\}', _CSS)
    assert circular_override_match, "stPopoverButton circular focus-ring override not found"
    assert "border-radius: 50%" in circular_override_match.group(1)


# --- .streamlit/config.toml native-widget theme matches the new palette ---

def test_config_toml_theme_matches_new_palette():
    assert 'backgroundColor = "#0B0D10"' in _CONFIG
    assert 'secondaryBackgroundColor = "#1D2026"' in _CONFIG
    assert 'textColor = "#F5F6F7"' in _CONFIG
    assert 'primaryColor = "#818CF8"' in _CONFIG
    assert 'linkColor = "#818CF8"' in _CONFIG
    assert 'base = "dark"' in _CONFIG


def test_config_toml_has_no_legacy_light_pass_values():
    for legacy in (
        '"#102A43"', '"#F8FAFC"', '"#0F172A"', '"#1D4ED8"', '"#7A8DA2"',
        'base = "light"',
        # every prior dark (midnight-navy) pass's own values — this is a
        # genuinely new dark palette, not a reversion to that one.
        '"#07111F"', '"#0D1A2D"', '"#5578A0"', '"#60A5FA"', '"#F1F5F9"',
        '"#212121"', '"#2A2A2A"', '"#ECECEC"', '"#B4B4B4"',
    ):
        assert legacy not in _CONFIG, f"legacy value {legacy!r} still present in config.toml"


# --- Component files with hardcoded (non-CSS-variable) colors ---

def test_charts_component_uses_new_palette_not_legacy_colors():
    source = (REPO_ROOT / "src" / "ui" / "components" / "charts.py").read_text(encoding="utf-8")
    for legacy in (
        "#F1F5F9", "#B8C5D6", "#5578A0", "#152944", "#8091A8",  # navy pass
        "#ECECEC", "#B4B4B4", "#6E6E6E", "#303030", "#8F8F8F",  # original
        "#0F172A", "#64748B", "#D9E2EC",  # indigo/editorial-white pass
        "rgba(85,120,160", "rgba(255,255,255,.14)", "rgba(122,141,162",
    ):
        assert legacy not in source
    assert "#F5F6F7" in source
    assert "#8B909A" in source


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
_MAIN_BACKGROUNDS = ["#0B0D10", "#16181D", "#1D2026"]
_ALL_BACKGROUNDS = ["#0B0D10", "#0E1013", "#16181D", "#1D2026"]


def test_primary_and_secondary_text_meet_aa_normal_text_contrast_on_every_surface():
    for name in ("text", "text-2"):
        color = _token(name)
        for bg in _ALL_BACKGROUNDS:
            ratio = _contrast_ratio(color, bg)
            assert ratio >= 4.5, f"--{name} ({color}) on {bg} is only {ratio:.2f}:1, below AA 4.5:1"


def test_muted_text_meets_aa_normal_text_contrast_on_page_card_and_elevated_surfaces():
    color = _token("text-3")
    for bg in _MAIN_BACKGROUNDS:
        ratio = _contrast_ratio(color, bg)
        assert ratio >= 4.5, f"--text-3 ({color}) on {bg} is only {ratio:.2f}:1, below AA 4.5:1"


def test_muted_text_clears_aa_even_on_the_sidebar_background_this_pass():
    """Unlike the prior (light-editorial and indigo) passes, where muted
    text fell just under 4.5:1 against --rail specifically and needed a
    per-sidebar-selector --text-2 override (see the next test), this
    dark palette's own --text-3 (#8B909A) clears 4.5:1 against --rail too
    (5.54:1) — not by design necessity, just because the near-black
    background gives more headroom. The --text-2 sidebar overrides are
    therefore no longer strictly required, but are kept as-is (still
    correct, just no longer load-bearing) rather than reverted — same
    "harmless leftover" precedent this file already used for a coincidental
    hex collision two passes ago."""
    color = _token("text-3")
    ratio = _contrast_ratio(color, _token("rail"))
    assert ratio >= 4.5, f"--text-3 ({color}) on --rail ({_token('rail')}) is only {ratio:.2f}:1"


def test_muted_text_is_not_used_directly_on_the_sidebar_background():
    """Kept from the prior passes (see the previous test's docstring for
    why it's no longer strictly load-bearing under this palette) — the
    --text-2 sidebar-only overrides are still real and still correct, so
    this still checks they're in place rather than re-deriving the
    sidebar DOM structure here."""
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
    # pos/neg/mix render as text-weight glyphs/labels only within main-
    # content-area components — never in the sidebar, which has no
    # direction/status/alert component of its own.
    for name in ("pos", "neg", "mix"):
        color = _token(name)
        for bg in _MAIN_BACKGROUNDS:
            ratio = _contrast_ratio(color, bg)
            assert ratio >= 4.5, f"--{name} ({color}) on {bg} is only {ratio:.2f}:1, below AA 4.5:1"


def test_status_colors_meet_aa_text_contrast_on_their_own_dim_tint_pill():
    """Status-tag pills render the status color as text on a low-alpha
    tint of itself (--pos-dim/--neg-dim/--mix-dim), composited over
    --surface — the real ambient background every status tag/chip/alert
    actually renders on in this app (unlike the prior, white-ground
    passes, where every real surface was at/near pure white, so mixing
    into a literal 255/255/255 white was an accurate simplification —
    that assumption no longer holds here, so this mixes into --surface's
    own RGB instead)."""
    surface_rgb = tuple(int(_token("surface").lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
    for name, dim_name in [("pos", "pos-dim"), ("neg", "neg-dim"), ("mix", "mix-dim")]:
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


def test_functional_border_meets_aa_ui_control_contrast_on_every_surface():
    color = _token("hairline-2")
    for bg in _ALL_BACKGROUNDS:
        ratio = _contrast_ratio(color, bg)
        assert ratio >= 3.0, f"--hairline-2 ({color}) on {bg} is only {ratio:.2f}:1, below AA 3:1"


def test_primary_cta_label_meets_aa_contrast_on_default_and_hover_fill():
    """White button label against both the default (--invert-bg,
    #4F46E5) and hover (--accent-hover, #4338CA) fills — both kept
    unchanged from the prior pass (see --accent-hover's own :root
    comment for why): a solid fill's own darkness is independent of the
    page's ambient background."""
    label = _token("invert-fg")
    for fill_name in ("invert-bg", "accent-hover"):
        fill = _token(fill_name)
        ratio = _contrast_ratio(label, fill)
        assert ratio >= 4.5, f"CTA label {label} on {fill_name} ({fill}) is only {ratio:.2f}:1, below AA 4.5:1"


def test_decorative_border_is_documented_as_intentionally_below_aa():
    """--hairline (#23262D) is used only for decorative dividers/footer
    rules/chart gridlines, never a functional control boundary (that
    role is --hairline-2, checked above) — WCAG's non-text-contrast rule
    doesn't apply to purely decorative separators, so this is
    intentionally not held to 3:1. This test just pins the value so a
    future change notices if --hairline silently becomes something a
    control boundary starts depending on."""
    color = _token("hairline")
    assert color == "#23262D"
    for bg in _ALL_BACKGROUNDS:
        ratio = _contrast_ratio(color, bg)
        assert ratio < 3.0  # documents the (accepted) shortfall, not a bug
