"""Theme tokens (src/ui/theme_tokens.py) — exact approved values, native
widget parity (.streamlit/config.toml), derived color-mix() states, and
WCAG contrast for every pair the stylesheet actually renders, in both
Dark and Light.

Contrast rule: text is held to 4.5:1 (AA, normal text); the focus ring
to 3:1 (AA, non-text UI). The exceptions are listed explicitly below
(_TEXT_EXCEPTIONS / _NON_TEXT_ONLY) with the reason each one is safe, and
the non-text-only tokens are separately proven never to be used as a
text color in assets/styles.css."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from src.ui import theme_tokens
from src.ui.theme_tokens import DERIVED_TOKENS, STREAMLIT_SIDEBAR_THEME, STREAMLIT_THEME, THEMES, TOKENS, render_token_css

REPO_ROOT = Path(__file__).parent.parent
_CSS = (REPO_ROOT / "assets" / "styles.css").read_text(encoding="utf-8")
_CONFIG = tomllib.loads((REPO_ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))

# The approved palette, pinned literally: (dark, light).
_APPROVED: dict[str, tuple[str, str]] = {
    # The mockup palette (UI redesign: Dark designed, Light derived).
    # This is an independent second copy of the approved values on
    # purpose: changing a colour has to be done in two places, so it
    # cannot happen by accident.
    "bg": ("#0E1013", "#F5F7FB"),
    "bg-sidebar": ("#0B0D10", "#EEF0F4"),
    "surface": ("#121418", "#FBFDFF"),
    "surface-input": ("#0F1215", "#FCFFFF"),
    "surface-chip": ("#1A1E25", "#E6EBF3"),
    "nav-active-bg": ("#1A1E25", "#E2E7EF"),
    "selected-bg": ("#161A20", "#EBEFF6"),
    "selected-ring": ("#1B5C51", "#6EB5A1"),
    "border-subtle": ("#1D2127", "#E0E4EB"),
    "border": ("#23272E", "#D3D7DF"),
    "border-strong": ("#2A2F37", "#C0C5CD"),
    "text": ("#E7E9EC", "#222427"),
    "text-secondary": ("#B8BEC7", "#4A5059"),
    "text-muted": ("#9AA1AC", "#5E6571"),
    "text-label": ("#848B97", "#646B77"),
    "accent": ("#2DBFA8", "#00715B"),
    "accent-contrast": ("#0B0D10", "#FFFFFF"),
    "link": ("#6AA8F0", "#1C68B6"),
    "link-hover": ("#83C2FF", "#0058A4"),
    "brand": ("#13233A", "#13233A"),
    "ev-supports": ("#5FC88A", "#14A863"),
    "ev-mixed": ("#E0B060", "#BE881E"),
    "ev-contradicts": ("#E07A6F", "#E57065"),
    "ev-context": ("#4A505A", "#4A505A"),
    "live": ("#2DBFA8", "#00715B"),
    "high-signal-bg": ("#4A2E07", "#FCEEDE"),
    "high-signal-text": ("#F5B66A", "#A55900"),
    "early-bg": ("#1F354D", "#E7F1FE"),
    "early-text": ("#8DBDF5", "#316FB3"),
    "venue-edgar-bg": ("#233147", "#E9F1FD"),
    "venue-edgar-text": ("#9CBCF0", "#466BA9"),
    "venue-dart-bg": ("#3A2946", "#F5ECFB"),
    "venue-dart-text": ("#D4AEF0", "#8858A8"),
    "venue-edinet-bg": ("#1A383A", "#E6F3F4"),
    "venue-edinet-text": ("#93D2D8", "#167880"),
    "venue-tdnet-bg": ("#412B32", "#F9ECF0"),
    "venue-tdnet-text": ("#E8B4C4", "#965A6F"),
    "venue-cninfo-bg": ("#412C20", "#F9EEE7"),
    "venue-cninfo-text": ("#E8B79C", "#975E3D"),
    "venue-hkex-bg": ("#34361C", "#F0F1E5"),
    "venue-hkex-text": ("#CFD39A", "#6E7125"),
    "cat-ai-buildout": ("#D39A68", "#C5854A"),
    "cat-memory": ("#8FB4E8", "#6B95CF"),
    "cat-space": ("#C9A0E8", "#AC7ECE"),
    "cat-photonics": ("#5FC2C8", "#14A1A9"),
    "cat-humanoids": ("#D98FA8", "#CB7896"),
}


def test_tokens_are_exactly_the_approved_palette_in_both_themes():
    assert set(TOKENS["dark"]) == set(_APPROVED) == set(TOKENS["light"])
    for name, (dark, light) in _APPROVED.items():
        assert TOKENS["dark"][name] == dark, name
        assert TOKENS["light"][name] == light, name


def test_every_theme_defines_the_same_token_names():
    assert list(TOKENS["dark"]) == list(TOKENS["light"])


# --- color math ------------------------------------------------------------

def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _linearize(channel: float) -> float:
    c = channel / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = rgb
    return 0.2126 * _linearize(r) + 0.7152 * _linearize(g) + 0.0722 * _linearize(b)


def _contrast_ratio(fg: tuple[int, int, int], bg: tuple[int, int, int]) -> float:
    l1, l2 = _relative_luminance(fg), _relative_luminance(bg)
    return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)


_MIX = re.compile(r"color-mix\(in srgb, var\(--([a-z0-9-]+)\) (\d+)%, (?:var\(--([a-z0-9-]+)\)|(transparent))\)")


def _resolved(theme: str) -> dict[str, tuple[int, int, int]]:
    """Every token as opaque sRGB for one theme. color-mix(in srgb, A p%, B)
    interpolates gamma-encoded sRGB channels; a mix with `transparent`
    is only ever a ring/halo and is excluded here (checked separately)."""
    out = {name: _rgb(value) for name, value in TOKENS[theme].items()}
    for name, expr in DERIVED_TOKENS.items():
        match = _MIX.fullmatch(expr)
        assert match, f"--{name} is not a simple srgb color-mix of two tokens: {expr!r}"
        a, pct, b, transparent = match.groups()
        if transparent:
            continue
        p = int(pct) / 100
        out[name] = tuple(round(x * p + y * (1 - p)) for x, y in zip(out[a], out[b]))  # type: ignore[assignment]
    return out


def test_derived_states_only_mix_approved_tokens():
    for name, expr in DERIVED_TOKENS.items():
        a, _pct, b, _t = _MIX.fullmatch(expr).groups()
        assert a in _APPROVED and (b is None or b in _APPROVED), (name, expr)
        assert name not in _APPROVED


def test_hover_and_pressed_states_step_away_from_their_base_in_both_themes():
    for theme in THEMES:
        t = _resolved(theme)
        for base, hover, pressed in (("surface", "surface-hover", "surface-pressed"), ("accent", "accent-hover", "accent-pressed")):
            assert t[base] != t[hover] != t[pressed], (theme, base)
            # Pressed is always the further step from the base.
            assert _contrast_ratio(t[base], t[pressed]) > _contrast_ratio(t[base], t[hover]), (theme, base)


# --- contrast ------------------------------------------------------------------

_TEXT_SURFACES = ("bg", "bg-sidebar", "surface", "surface-input", "surface-chip", "surface-hover", "surface-pressed",
                  "nav-active-bg", "selected-bg", "accent-tint", "ev-supports-tint", "ev-mixed-tint",
                  "ev-contradicts-tint", "high-signal-bg")

# (text token, surfaces it renders on) — every pair must clear 4.5:1 in both themes.
_TEXT_PAIRS: dict[str, tuple[str, ...]] = {
    "text": _TEXT_SURFACES,
    "text-secondary": _TEXT_SURFACES,
    "text-muted": _TEXT_SURFACES,
    # Small uppercase labels: canvas, sidebar, card and inset surfaces only.
    "text-label": ("bg", "bg-sidebar", "surface", "surface-input"),
    "link": ("bg", "surface", "surface-input", "surface-chip"),
    "link-hover": ("bg", "surface", "surface-input", "surface-chip"),
    # Active nav text/icon, compact search hover, expander hover, counts.
    "accent": ("bg", "bg-sidebar", "surface", "surface-hover", "nav-active-bg", "selected-bg"),
    "accent-hover": ("bg-sidebar", "surface-hover"),
}

# Documented shortfalls: pairs the stylesheet never renders.
_TEXT_EXCEPTIONS = {
    ("text-label", "nav-active-bg"): "labels never sit on the active nav item (4.32 light)",
    ("text-label", "surface-chip"): "labels never sit on chips (4.48 light)",
    ("text-label", "surface-pressed"): "labels never sit on a pressed surface (4.25 dark / 4.33 light)",
}

# Fills, dots, bars, rails and borders only — never text. Several fall
# below 4.5:1 (and some below 3:1) as text in Light, which is exactly why.
_NON_TEXT_ONLY = ("brand", "ev-supports", "ev-mixed", "ev-contradicts", "ev-context", "live",
                  "cat-ai-buildout", "cat-memory", "cat-space", "cat-photonics", "cat-humanoids")


@pytest.mark.parametrize("theme", THEMES)
def test_text_tokens_meet_aa_on_every_surface_they_render_on(theme):
    t = _resolved(theme)
    failures = []
    for fg, surfaces in _TEXT_PAIRS.items():
        for bg in surfaces:
            ratio = _contrast_ratio(t[fg], t[bg])
            if ratio < 4.5:
                failures.append(f"--{fg} on --{bg}: {ratio:.2f}:1")
    assert failures == []


@pytest.mark.parametrize("theme", THEMES)
def test_paired_foreground_and_fill_tokens_meet_aa(theme):
    t = _resolved(theme)
    pairs = [
        ("accent-contrast", "accent"), ("accent-contrast", "accent-hover"), ("accent-contrast", "accent-pressed"),
        ("high-signal-text", "high-signal-bg"), ("early-text", "early-bg"),
        ("bg", "text"),  # the inverted Fact chip
        *[(f"venue-{v}-text", f"venue-{v}-bg") for v in ("edgar", "dart", "edinet", "tdnet", "cninfo", "hkex")],
    ]
    for fg, bg in pairs:
        ratio = _contrast_ratio(t[fg], t[bg])
        assert ratio >= 4.5, f"{theme}: --{fg} on --{bg} is {ratio:.2f}:1"


@pytest.mark.parametrize("theme", THEMES)
def test_focus_ring_meets_aa_non_text_contrast_on_every_surface(theme):
    t = _resolved(theme)
    for bg in ("bg", "bg-sidebar", "surface", "surface-input", "surface-chip", "surface-hover", "nav-active-bg"):
        assert _contrast_ratio(t["focus-ring"], t[bg]) >= 3.0, (theme, bg)


def test_documented_text_exceptions_are_real_and_never_rendered():
    """Each exception is a genuine shortfall in at least one theme (so the
    list never goes stale), and styles.css never pairs them."""
    for (fg, bg), _reason in _TEXT_EXCEPTIONS.items():
        assert min(_contrast_ratio(_resolved(th)[fg], _resolved(th)[bg]) for th in THEMES) < 4.5, (fg, bg)
    label_rules = [body for body in re.findall(r"\{([^}]*)\}", _CSS) if "color: var(--text-label)" in body]
    assert label_rules
    for body in label_rules:
        for (_fg, bg) in _TEXT_EXCEPTIONS:
            assert f"var(--{bg})" not in body


def test_non_text_only_tokens_are_never_used_as_a_text_color():
    """U7: evidence, category, live and brand hues are indicators only —
    dots, bars, rails, borders and tints. The one sanctioned glyph is the
    direction marker (▲▼●) beside a direction label that always carries
    the meaning in text."""
    offenders = []
    for selector, body in re.findall(r"([^{}]+)\{([^}]*)\}", _CSS):
        for token_name in _NON_TEXT_ONLY:
            if re.search(rf"(?<![-\w])color:\s*var\(--{token_name}\)", body) and ".er-dir-glyph" not in selector:
                offenders.append((selector.strip()[-80:], token_name))
    assert offenders == []


def test_light_theme_indicator_hues_really_would_fail_as_text():
    """The point of _NON_TEXT_ONLY is that these hues are chosen to be
    SEEN as a dot or bar, not READ as a word, and in Light several of
    them genuinely cannot carry text. Checking that keeps the
    classification honest rather than decorative.

    Two tokens left this list in the mockup-palette pass, for opposite
    reasons, and both are still in _NON_TEXT_ONLY (the stylesheet still
    may not use either as a text colour):

      * --live is no longer a hue of its own. The mockups paint "Live ·
        17:32 EST" in the accent teal, so --live now equals --accent,
        and --accent is required to clear 4.5:1 as text. Asserting the
        same value both fails and passes as text is a contradiction, not
        a guard.
      * --brand is the dark navy logo tile, with near-white text on it.
        It fails as text for the opposite reason — too dark against a
        light card, not too light — so the assertion below would not
        describe it. Its real contract is with the mark's own label,
        asserted separately.
    """
    t = _resolved("light")
    for name in ("ev-supports", "ev-mixed", "ev-contradicts",
                 "cat-ai-buildout", "cat-memory", "cat-space", "cat-photonics", "cat-humanoids"):
        assert _contrast_ratio(t[name], t["surface"]) < 4.5, name


def test_the_brand_tile_carries_its_own_mark_legibly_in_both_themes():
    """--brand is the logo tile behind the "e" in every mockup header,
    the same dark navy in both themes, and the mark on it is near-white.
    It is a fill, never text (see _NON_TEXT_ONLY), so what matters is
    that the mark reads on the tile."""
    for theme in THEMES:
        assert _contrast_ratio(_rgb("#F2F2EE"), _resolved(theme)["brand"]) >= 4.5, theme


# --- native widget parity -------------------------------------------------------

def test_config_toml_theme_sections_mirror_the_tokens_exactly():
    theme = _CONFIG["theme"]
    for name in THEMES:
        section = dict(theme[name])
        sidebar = section.pop("sidebar")
        assert section == STREAMLIT_THEME[name], name
        assert sidebar == STREAMLIT_SIDEBAR_THEME[name], name


def test_config_background_equals_bg_so_the_pre_css_canvas_is_already_right():
    for name in THEMES:
        assert _CONFIG["theme"][name]["backgroundColor"] == TOKENS[name]["bg"]
        assert _CONFIG["theme"][name]["sidebar"]["backgroundColor"] == TOKENS[name]["bg-sidebar"]


def test_config_shared_section_carries_no_color_and_no_forced_base():
    shared = {k: v for k, v in _CONFIG["theme"].items() if k not in THEMES}
    assert "base" not in shared
    assert not any("Color" in key for key in shared)


# --- rendered token CSS --------------------------------------------------------------

def _declarations(css: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\{([^}]*)\}", css)
    assert match, selector
    return match.group(1)


def test_system_preference_follows_the_os_and_an_explicit_stamp_wins_both_ways():
    css = render_token_css("system")
    assert "--bg:#0E1013;" in _declarations(css, ":root")
    assert "@media (prefers-color-scheme: light){:root:not([data-theme]){" in css
    assert "--bg:#F5F7FB;" in _declarations(css, ':root[data-theme="light"]')
    assert "--bg:#0E1013;" in _declarations(css, ':root[data-theme="dark"]')


@pytest.mark.parametrize("preference,bg", [("dark", "#0E1013"), ("light", "#F5F7FB")])
def test_explicit_preference_is_the_bare_root_and_ignores_the_os(preference, bg):
    css = render_token_css(preference)
    assert f"--bg:{bg};" in _declarations(css, ":root")
    assert "prefers-color-scheme" not in css


def test_rendered_css_sets_color_scheme_so_native_scrollbars_follow_the_theme():
    css = render_token_css("system")
    assert "color-scheme:dark;" in _declarations(css, ':root[data-theme="dark"]')
    assert "color-scheme:light;" in _declarations(css, ':root[data-theme="light"]')


def test_derived_tokens_are_declared_once_and_every_consumed_token_exists():
    css = render_token_css("system")
    for name in DERIVED_TOKENS:
        assert css.count(f"--{name}:") == 1
    defined = set(TOKENS["dark"]) | set(DERIVED_TOKENS) | set(re.findall(r"--([a-z0-9-]+):", _CSS))
    used = set(re.findall(r"var\(--([a-z0-9-]+)", _CSS))
    assert used - defined - {"w"} == set()


def test_token_helper_reads_the_single_source():
    assert theme_tokens.token("light", "text-muted") == "#5E6571"


def test_every_theme_slug_has_its_own_distinct_category_colour():
    """The dot beside a theme name is the only thing distinguishing one
    theme from another on the Companies and Themes indexes, so every
    slug the taxonomy carries needs a token, and no two may share a
    value. Photonics used to be an amber indistinguishable from AI
    Buildout's orange, and Humanoids had no token at all — it fell
    through to the grey "other" dot, reading as "no theme" rather than
    as its own.
    """
    from src.logic.theme_taxonomy import known_theme_slugs
    from src.ui.components.primitives import _THEME_CLASS

    for theme in THEMES:
        values = {}
        for slug in known_theme_slugs():
            assert slug in _THEME_CLASS, f"{slug} renders as the grey 'other' dot"
            name = f"cat-{_THEME_CLASS[slug]}"
            assert name in TOKENS[theme], name
            value = TOKENS[theme][name]
            assert value not in values, f"{theme}: {slug} shares {value} with {values[value]}"
            values[value] = slug
