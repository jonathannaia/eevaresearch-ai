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
_PAIRS: dict[str, tuple[str, str]] = {
    # Surfaces
    "bg": ("#111110", "#F6F4EF"),
    "bg-sidebar": ("#141412", "#EFECE5"),
    "surface": ("#1A1917", "#FCFBF8"),
    "surface-input": ("#161513", "#FFFFFF"),
    "surface-chip": ("#22211E", "#F0EDE6"),
    "nav-active-bg": ("#1D2230", "#E4E8F2"),
    "selected-bg": ("#1C2130", "#EEF1F8"),
    "selected-ring": ("#34466B", "#C3CDE6"),
    # Borders
    "border-subtle": ("#23221F", "#E9E5DD"),
    "border": ("#2A2825", "#E2DED5"),
    "border-strong": ("#302E2A", "#D6D1C7"),
    # Text
    "text": ("#ECE9E4", "#1C1B19"),
    "text-secondary": ("#C2BDB5", "#45423D"),
    "text-muted": ("#9C978F", "#625E57"),
    "text-label": ("#88837B", "#6E6961"),
    # Accent — interactive only, never evidence
    "accent": ("#8AA8E0", "#23386E"),
    "accent-contrast": ("#0C1424", "#FFFFFF"),
    "link": ("#9DB6EA", "#2F4F9E"),
    "link-hover": ("#BFD0F2", "#1F3A7A"),
    "brand": ("#6FD3BC", "#6FD3BC"),
    # Evidence — reserved for evidence meaning
    "ev-supports": ("#5FC88A", "#3A9A62"),
    "ev-mixed": ("#E0B060", "#C08A2A"),
    "ev-contradicts": ("#E07A6F", "#B5483C"),
    "ev-context": ("#3E434A", "#C9C4BA"),
    # Status
    "live": ("#5FD08F", "#2E9E5B"),
    "high-signal-bg": ("#152A22", "#E4F2E8"),
    "high-signal-text": ("#7FE0A6", "#256B41"),
    # Filing venues (tint / text)
    "venue-edgar-bg": ("#18233A", "#E3EAF7"),
    "venue-edgar-text": ("#9CBCF0", "#2D4A85"),
    "venue-dart-bg": ("#2A1D33", "#F0E6F5"),
    "venue-dart-text": ("#D4AEF0", "#6B3F87"),
    "venue-edinet-bg": ("#172A2C", "#E2F2F3"),
    "venue-edinet-text": ("#93D2D8", "#256A70"),
    "venue-tdnet-bg": ("#2B1F24", "#F6E8ED"),
    "venue-tdnet-text": ("#E8B4C4", "#8A3F58"),
    "venue-cninfo-bg": ("#2A201C", "#F6EBE4"),
    "venue-cninfo-text": ("#E8B79C", "#8A4B2E"),
    "venue-hkex-bg": ("#25271B", "#F1F2E2"),
    "venue-hkex-text": ("#CFD39A", "#5E6328"),
    # Taxonomy categories — never evidence
    "cat-ai-buildout": ("#D39A68", "#B87A45"),
    "cat-memory": ("#8FB4E8", "#4F74B8"),
    "cat-space": ("#C9A0E8", "#8E62B0"),
    "cat-photonics": ("#E0B060", "#C08A2A"),
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
