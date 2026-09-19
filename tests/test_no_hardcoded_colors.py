"""Hex/color-literal guard: every color the dashboard paints comes from
src/ui/theme_tokens.py. No component, page, stylesheet, chart or script
may carry its own color literal — hex (including the url-encoded %23
form used inside data: SVGs), rgb()/rgba()/hsl()/hsla(), or a named
color in a CSS color position.

Allowlisted: src/ui/theme_tokens.py (the source of truth) and
.streamlit/config.toml (its generated mirror, parity-checked in
tests/test_theme_tokens.py). Binary assets (the logo PNG) are not text.

Python files are scanned by string literal only (via tokenize), so a
comment or docstring reference like "PR #63" is never mistaken for a
color, while any color that could reach rendered HTML/CSS is caught."""
from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
_ALLOWLIST = {REPO_ROOT / "src" / "ui" / "theme_tokens.py"}

_HEX = re.compile(r"(?:(?<![\w&/])#|%23)(?:[0-9A-Fa-f]{8}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{3,4})(?![0-9A-Za-z_])")
_FUNC = re.compile(r"(?<![\w-])(?:rgba?|hsla?)\s*\(", re.IGNORECASE)
_ALLOWED_WORDS = {"transparent", "currentcolor", "inherit", "initial", "unset", "none", "var", "url", "revert"}
_COLOR_PROP = re.compile(
    r"(?<![\w-])(?:color|background|background-color|border(?:-(?:top|right|bottom|left))?-color|fill|stroke|outline-color)"
    r"\s*:\s*([A-Za-z]+)\b",
)
_BORDER_WORD = re.compile(r"\d(?:px|rem|em)?\s+(?:solid|dashed|dotted|double)\s+([A-Za-z]+)\b")


def _files() -> list[Path]:
    files = [*sorted((REPO_ROOT / "src").rglob("*.py")), REPO_ROOT / "app.py",
             *sorted((REPO_ROOT / "scripts").glob("*.py")), *sorted((REPO_ROOT / "assets").glob("*.css"))]
    return [f for f in files if f.exists() and f not in _ALLOWLIST]


def _scannable_text(path: Path) -> list[tuple[int, str]]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".css":
        return list(enumerate(text.splitlines(), start=1))
    chunks = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.STRING or tok.type == getattr(tokenize, "FSTRING_MIDDLE", -1):
            chunks.append((tok.start[0], tok.string))
    return chunks


def _violations(text: str) -> list[str]:
    found = [m.group(0) for m in _HEX.finditer(text)]
    found += [m.group(0) for m in _FUNC.finditer(text)]
    for pattern in (_COLOR_PROP, _BORDER_WORD):
        found += [m.group(0) for m in pattern.finditer(text) if m.group(1).lower() not in _ALLOWED_WORDS]
    return found


def test_no_color_literal_outside_the_theme_token_source():
    offenders = []
    for path in _files():
        for line, chunk in _scannable_text(path):
            for hit in _violations(chunk):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line}: {hit}")
    assert offenders == [], "color literals outside src/ui/theme_tokens.py:\n" + "\n".join(offenders)


def test_guard_catches_every_literal_form_it_claims_to():
    for sample in ('style="color:#9197A0"', "background: #fff;", "stroke%3D%22%236FD3BC%22",
                   "background:rgba(255,255,255,.025)", "color: hsl(10 20% 30%)", "color: white;",
                   "border: 1px solid navy", "fill: red"):
        assert _violations(sample), sample


def test_guard_ignores_tokens_keywords_and_non_color_hashes():
    for sample in ("color: var(--text);", "background: transparent !important;", "fill: currentColor",
                   "border: 1px solid var(--border)", "&#39;", 'href="#section-2"', "border: 1px solid transparent"):
        assert not _violations(sample), sample


def test_theme_token_source_is_the_only_allowlisted_file():
    assert _ALLOWLIST == {REPO_ROOT / "src" / "ui" / "theme_tokens.py"}
