"""Static landing page (landing/index.html, served at eevaresearch.com) —
Midnight Teal token pass, applied separately from assets/styles.css
because the static site and the Streamlit app are two independent
deployments with no shared build step or asset pipeline (landing/ is a
standalone static host, confirmed via landing/_redirects; the Streamlit
app is deployed separately at app.eevaresearch.com). Token values are
intentionally duplicated in landing/index.html's own <style> block
rather than shared — see the :root comment there for why.

No existing test in this suite touched landing/ before this pass
(confirmed by grep), so this is a deterministic structural check rather
than an extension of an existing pattern: pure static-content assertions
against the HTML source, no browser/rendering involved. Mirrors the
token-presence-checking shape of test_visual_theme_redesign.py, scoped
to this one file."""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
_HTML_PATH = REPO_ROOT / "landing" / "index.html"

_HTML = _HTML_PATH.read_text(encoding="utf-8")

_EXPECTED_TOKENS = {
    "bg": "#101417",
    "rail": "#0B0F11",
    "surface": "#171D20",
    "surface-hover": "#1D2529",
    "border": "#263137",
    "text": "#F1F4F2",
    "text-secondary": "#B1BCBB",
    "text-muted": "#889593",
    "accent": "#21B7A8",
    "accent-hover": "#29CBBB",
}

_LEGACY_COLORS = [
    "#F8FAFC", "#102A43", "#0F172A", "#475569", "#1D4ED8",
    "#0B1E33", "#E2E8F0", "#818CF8", "#4F46E5", "#6366F1", "#7C3AED",
]


def _token(name: str) -> str:
    match = re.search(rf"--{re.escape(name)}:\s*([^;]+);", _HTML)
    assert match, f"--{name} not found in landing/index.html"
    return match.group(1).strip()


def test_midnight_teal_tokens_are_present():
    for name, expected in _EXPECTED_TOKENS.items():
        assert _token(name) == expected, f"--{name} expected {expected}, got {_token(name)}"


def test_no_legacy_light_theme_or_indigo_colors_remain():
    html_upper = _HTML.upper()
    for hex_color in _LEGACY_COLORS:
        assert hex_color.upper() not in html_upper, (
            f"legacy color {hex_color} still present in landing/index.html"
        )


def test_no_gradients_or_indigo_purple_keywords_in_rendered_css():
    # Excludes HTML comments, which may reference these terms explanatorily
    # (e.g. "no shadow/glow/gradient" as a design-rationale note).
    css_only = re.sub(r"<!--.*?-->", "", _HTML, flags=re.DOTALL)
    style_block = re.search(r"<style>(.*?)</style>", css_only, re.DOTALL)
    assert style_block, "no <style> block found in landing/index.html"
    css_text = re.sub(r"/\*.*?\*/", "", style_block.group(1), flags=re.DOTALL)
    assert "gradient" not in css_text.lower()
    assert "indigo" not in css_text.lower()
    assert "purple" not in css_text.lower()


def test_information_architecture_is_unchanged():
    assert "<header>" in _HTML and "</header>" in _HTML
    assert "<footer>" in _HTML and "</footer>" in _HTML
    assert "Open Research Platform" in _HTML
    assert "EevaResearch" in _HTML
    assert "© EevaResearch" in _HTML or "&copy; EevaResearch" in _HTML


def test_cta_destination_is_unchanged():
    match = re.search(r'class="cta"[^>]*href="([^"]+)"', _HTML)
    if not match:
        match = re.search(r'href="([^"]+)"[^>]*class="cta"', _HTML)
    assert match, "could not locate the CTA link's href"
    assert match.group(1) == "https://app.eevaresearch.com/"


def test_metadata_is_unchanged():
    assert re.search(r'<link rel="icon"[^>]*href="/favicon-v2\.png">', _HTML)
    assert re.search(r"<title>[^<]*EevaResearch[^<]*</title>", _HTML)
    assert '<link rel="canonical"' in _HTML
    assert 'property="og:' in _HTML


def test_logo_images_use_invert_and_screen_blend_not_invert_alone():
    # invert(1) alone on this opaque (non-transparent) source asset would
    # produce a hard black tile behind the glyph; mix-blend-mode: screen
    # is required to make that background disappear into the backdrop.
    style_block = re.search(r"<style>(.*?)</style>", _HTML, re.DOTALL).group(1)
    header_rule = re.search(r"header img\s*\{([^}]*)\}", style_block)
    mark_rule = re.search(r"\.card img\.mark\s*\{([^}]*)\}", style_block)
    assert header_rule and "mix-blend-mode: screen" in header_rule.group(1)
    assert header_rule and "filter: invert(1)" in header_rule.group(1)
    assert mark_rule and "mix-blend-mode: screen" in mark_rule.group(1)
    assert mark_rule and "filter: invert(1)" in mark_rule.group(1)
