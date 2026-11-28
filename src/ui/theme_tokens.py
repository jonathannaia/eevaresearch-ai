"""The one source of truth for every color the dashboard paints.

Dark and Light are two complete sets of the same semantic tokens.
Components, page CSS (assets/styles.css), and charts consume tokens by
name only; no other file in src/, app.py, scripts/, or assets/*.css may
contain a color literal (tests/test_no_hardcoded_colors.py). The native
Streamlit widget theme (.streamlit/config.toml [theme.dark]/[theme.light])
is a mirror of STREAMLIT_THEME below and is parity-checked by
tests/test_theme_tokens.py.

Roles are strict:
  * accent / link: interactive elements only, never evidence;
  * ev-*: evidence meaning only (Supports / Mixed / Contradicts / Context);
  * cat-*: the four taxonomy categories, never evidence;
  * live / high-signal-*: status only;
  * brand, ev-*, live, cat-*: fills, dots, bars, rails and borders only,
    never text (several are below 4.5:1 as text in Light).

Hover, pressed, focus and tint states are derived from these tokens with
color-mix() (DERIVED_TOKENS) rather than given colors of their own.
"""
from __future__ import annotations

from typing import Literal

ThemeName = Literal["dark", "light"]
ThemePreference = Literal["system", "dark", "light"]

THEMES: tuple[ThemeName, ...] = ("dark", "light")
PREFERENCES: tuple[ThemePreference, ...] = ("system", "dark", "light")

# token name (without the leading --) -> (dark, light)
#
# DARK is the designed theme. Every surface, border, text, accent and
# status value below is taken verbatim from the three mockups in
# design/ui-redesign/ — see that directory's README for which hex sits
# where. The families the mockups never show (evidence, filing venues,
# taxonomy categories) keep the HUES they already had, because those
# hues carry meaning a reader has learned, and only their lightness and
# chroma are re-seated onto the new, cooler ground.
#
# LIGHT is DERIVED from Dark, not designed separately — the mockups
# define no light palette and inventing one would be guessing. The rule,
# applied per role rather than per colour:
#   * surfaces and borders keep the dark hue and take a lightness from
#     the light ladder that preserves each role's RELATIONSHIP to the
#     page ground (a card sits above the ground in both themes, the rail
#     below it, borders step away from it). A literal lightness mirror
#     does not work: --bg at L=0.17 mirrors to a mid grey, not a page.
#   * text keeps its hue and steps down a mirrored ladder.
#   * chromatic tokens keep their hue and are darkened until they clear
#     the contrast floor their role needs on the surface they land on —
#     4.5:1 for text, 3:1 for dots, bars and rails. The floor is measured
#     against the DARKEST surface each token actually renders on (see
#     _TEXT_PAIRS in tests/test_theme_tokens.py), not against the
#     lightest card: deriving --accent against --surface alone left it at
#     3.79:1 on --nav-active-bg, which is exactly where the active nav
#     item paints it.
# The derivation ran in OKLCH so hue survives the lightness change; the
# contrast floors are enforced for real by this module's own tests
# (tests/test_theme_tokens.py), not by the script that produced them.
_PAIRS: dict[str, tuple[str, str]] = {
    # Surfaces
    "bg": ("#0E1013", "#F5F7FB"),
    "bg-sidebar": ("#0B0D10", "#EEF0F4"),
    "surface": ("#121418", "#FBFDFF"),
    "surface-input": ("#0F1215", "#FCFFFF"),
    "surface-chip": ("#1A1E25", "#E6EBF3"),
    "nav-active-bg": ("#1A1E25", "#E2E7EF"),
    "selected-bg": ("#161A20", "#EBEFF6"),
    "selected-ring": ("#1B5C51", "#6EB5A1"),
    # Borders
    "border-subtle": ("#1D2127", "#E0E4EB"),
    "border": ("#23272E", "#D3D7DF"),
    "border-strong": ("#2A2F37", "#C0C5CD"),
    # Text
    "text": ("#E7E9EC", "#222427"),
    "text-secondary": ("#B8BEC7", "#4A5059"),
    "text-muted": ("#9AA1AC", "#5E6571"),
    "text-label": ("#848B97", "#646B77"),
    # Accent — interactive only, never evidence
    "accent": ("#2DBFA8", "#00715B"),
    "accent-contrast": ("#0B0D10", "#FFFFFF"),
    # The mockups make links plain body text (`a{color:#E7E9EC}`), which
    # leaves a link indistinguishable from the prose around it. #6AA8F0
    # is the mockups' own second accent (it is in the accent picker's
    # option list and draws the company-page timeline dots), so links
    # take that rather than a colour from nowhere or no colour at all.
    "link": ("#6AA8F0", "#1C68B6"),
    "link-hover": ("#83C2FF", "#0058A4"),
    # The logo tile behind the "e" mark in every mockup header.
    "brand": ("#13233A", "#13233A"),
    # Evidence — reserved for evidence meaning
    "ev-supports": ("#5FC88A", "#14A863"),
    "ev-mixed": ("#E0B060", "#BE881E"),
    "ev-contradicts": ("#E07A6F", "#E57065"),
    # The one evidence value the mockups do pin down, by role if not by
    # name: the inert bar on the company page's thesis list.
    "ev-context": ("#4A505A", "#4A505A"),
    # Status
    "live": ("#2DBFA8", "#00715B"),
    "high-signal-bg": ("#4A2E07", "#FCEEDE"),
    "high-signal-text": ("#F5B66A", "#A55900"),
    # New: the Early tier had no colour of its own — its badge rendered
    # in the neutral chip style, so High was the only tier a reader could
    # pick out at a glance. The mockups give it #8DBDF5.
    "early-bg": ("#1F354D", "#E7F1FE"),
    "early-text": ("#8DBDF5", "#316FB3"),
    # Filing venues (tint / text)
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
    # Taxonomy categories — never evidence. One distinct hue per theme in
    # src/logic/theme_taxonomy.py; all five must stay mutually
    # distinguishable in both themes (tests/test_theme_tokens.py).
    "cat-ai-buildout": ("#D39A68", "#C5854A"),
    "cat-memory": ("#8FB4E8", "#6B95CF"),
    "cat-space": ("#C9A0E8", "#AC7ECE"),
    "cat-photonics": ("#5FC2C8", "#14A1A9"),
    "cat-humanoids": ("#D98FA8", "#CB7896"),
}

TOKENS: dict[ThemeName, dict[str, str]] = {
    "dark": {name: pair[0] for name, pair in _PAIRS.items()},
    "light": {name: pair[1] for name, pair in _PAIRS.items()},
}

# State colors, derived from the semantic tokens above. Declared once on
# :root; because every input is a custom property on the same element,
# each one re-resolves automatically when the theme's tokens change.
DERIVED_TOKENS: dict[str, str] = {
    "surface-hover": "color-mix(in srgb, var(--surface) 95%, var(--text))",
    "surface-pressed": "color-mix(in srgb, var(--surface) 90%, var(--text))",
    "accent-hover": "color-mix(in srgb, var(--accent) 85%, var(--text))",
    "accent-pressed": "color-mix(in srgb, var(--accent) 72%, var(--text))",
    "accent-tint": "color-mix(in srgb, var(--accent) 14%, var(--surface))",
    "focus-ring": "color-mix(in srgb, var(--accent) 80%, var(--text))",
    "live-ring": "color-mix(in srgb, var(--live) 45%, transparent)",
    "ev-supports-tint": "color-mix(in srgb, var(--ev-supports) 14%, var(--surface))",
    "ev-mixed-tint": "color-mix(in srgb, var(--ev-mixed) 14%, var(--surface))",
    "ev-contradicts-tint": "color-mix(in srgb, var(--ev-contradicts) 12%, var(--surface))",
}

# .streamlit/config.toml mirror: native widget colors per theme section.
# The sidebar key lives under [theme.<name>.sidebar].
_STREAMLIT_KEYS: dict[str, str] = {
    "primaryColor": "accent",
    "backgroundColor": "bg",
    "secondaryBackgroundColor": "surface-chip",
    "textColor": "text",
    "linkColor": "link",
    "borderColor": "border",
    "dataframeBorderColor": "border",
    "dataframeHeaderBackgroundColor": "surface",
    "codeTextColor": "text",
    "codeBackgroundColor": "surface-input",
}
STREAMLIT_THEME: dict[ThemeName, dict[str, str]] = {
    theme: {key: TOKENS[theme][token] for key, token in _STREAMLIT_KEYS.items()} for theme in THEMES
}
STREAMLIT_SIDEBAR_THEME: dict[ThemeName, dict[str, str]] = {
    theme: {"backgroundColor": TOKENS[theme]["bg-sidebar"]} for theme in THEMES
}


def token(theme: ThemeName, name: str) -> str:
    """One token's literal value — for the few consumers that cannot read
    a CSS custom property (server-rendered chart specs)."""
    return TOKENS[theme][name]


def _block(selector: str, theme: ThemeName) -> str:
    body = "".join(f"--{name}:{value};" for name, value in TOKENS[theme].items())
    return f"{selector}{{color-scheme:{theme};{body}}}"


def render_token_css(preference: ThemePreference = "system") -> str:
    """Token CSS for one page render.

    The bare :root carries the server-resolved theme (Dark for "system"
    and "dark", Light for "light") so the first paint after the style
    block lands is already correct. For "system" an un-stamped document
    follows prefers-color-scheme. An explicit data-theme stamp on <html>
    (set by the client bridge) always wins, in both directions, so a
    switch never waits on a server round trip."""
    base: ThemeName = "light" if preference == "light" else "dark"
    parts = [_block(":root", base)]
    if preference == "system":
        parts.append(f"@media (prefers-color-scheme: light){{{_block(':root:not([data-theme])', 'light')}}}")
    parts.append(_block(':root[data-theme="light"]', "light"))
    parts.append(_block(':root[data-theme="dark"]', "dark"))
    parts.append(":root{" + "".join(f"--{name}:{value};" for name, value in DERIVED_TOKENS.items()) + "}")
    return "\n".join(parts)
