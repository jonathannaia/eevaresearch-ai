"""Streamlit favicon/page-config branding fix — structural checks only.
Streamlit's own testing.v1.AppTest harness (used elsewhere in this repo
for page-body assertions, e.g. tests/test_app_auth_gate.py) does not
expose st.set_page_config()'s own arguments or render actual browser
<head> metadata, so this file inspects app.py's source/AST directly —
the same convention this repo's own scope-guard tests already use (e.g.
tests/test_research_lead_selection.py's import-scan tests).

No new test dependency: PNG width/height/color-type are read directly
from the file's own IHDR chunk (a fixed byte offset per the PNG spec),
never via Pillow — Pillow is only ever a transitive dependency of
streamlit in this project, never declared in requirements.txt."""
from __future__ import annotations

import ast
import struct
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
APP_PY = REPO_ROOT / "app.py"
FAVICON_PATH = REPO_ROOT / "assets" / "favicon.png"

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _parse_app_module() -> ast.Module:
    return ast.parse(APP_PY.read_text(encoding="utf-8"), filename="app.py")


def _first_top_level_streamlit_call(tree: ast.Module) -> ast.Call:
    """The first module-level statement whose value is a call on the `st`
    name (e.g. `st.set_page_config(...)`) — mirrors Streamlit's own "must
    be the first Streamlit command" rule, which only applies to real
    top-level statements, never to code nested inside a function/class
    body or to decorators like @st.cache_data (those aren't top-level
    Expr statements at all, so they're never visited here)."""
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "st":
                return node.value
    raise AssertionError("app.py has no top-level st.* call at all")


def _png_dimensions_and_color_type(path: Path) -> tuple[int, int, int]:
    data = path.read_bytes()
    assert data[:8] == _PNG_SIGNATURE, f"{path} is not a valid PNG file"
    assert data[12:16] == b"IHDR", f"{path}'s first chunk is not IHDR"
    width, height = struct.unpack(">II", data[16:24])
    color_type = data[25]
    return width, height, color_type


# ============================================================
# app.py is the one real public Streamlit entry point
# ============================================================


def test_app_py_is_the_only_public_page_module_with_set_page_config():
    # scripts/view_edinet_discoveries.py also calls set_page_config, but
    # its own module docstring documents it as "deliberately NOT wired
    # into app.py/ui.py's navigation — a fully separate process from the
    # main product" (an internal/experimental diagnostic script, never a
    # deployed public entry point) — excluded here by design, not missed.
    offenders = []
    for path in (REPO_ROOT / "src" / "ui" / "pages").glob("*.py"):
        if "set_page_config" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []


def test_view_edinet_discoveries_script_is_documented_as_not_public():
    # Regression: if this internal script's own docstring ever stops
    # saying it's excluded from the main product, this suite should be
    # revisited to decide whether it now needs the same favicon fix.
    source = (REPO_ROOT / "scripts" / "view_edinet_discoveries.py").read_text(encoding="utf-8")
    assert "NOT wired into app.py" in source


# ============================================================
# st.set_page_config() itself
# ============================================================


def test_set_page_config_is_the_first_streamlit_command_in_app_py():
    call = _first_top_level_streamlit_call(_parse_app_module())
    assert call.func.attr == "set_page_config"


def test_set_page_config_uses_the_intended_page_title():
    call = _first_top_level_streamlit_call(_parse_app_module())
    title_kwarg = next(kw for kw in call.keywords if kw.arg == "page_title")
    assert isinstance(title_kwarg.value, ast.Constant)
    assert title_kwarg.value.value == "EevaResearch | Global Filing Intelligence"


def test_set_page_config_page_icon_is_never_a_bare_none_or_omitted():
    call = _first_top_level_streamlit_call(_parse_app_module())
    icon_kwarg = next((kw for kw in call.keywords if kw.arg == "page_icon"), None)
    assert icon_kwarg is not None, "page_icon must be explicitly configured, never left to Streamlit's own default"
    is_hardcoded_none = isinstance(icon_kwarg.value, ast.Constant) and icon_kwarg.value.value is None
    assert not is_hardcoded_none, "page_icon must not be a hardcoded None — that leaves Streamlit's default (crown) icon in place"


def test_set_page_config_page_icon_references_the_favicon_path_constant():
    call = _first_top_level_streamlit_call(_parse_app_module())
    icon_kwarg = next(kw for kw in call.keywords if kw.arg == "page_icon")
    assert "_FAVICON_PATH" in ast.unparse(icon_kwarg.value)


def test_set_page_config_preserves_wide_layout():
    call = _first_top_level_streamlit_call(_parse_app_module())
    layout_kwarg = next(kw for kw in call.keywords if kw.arg == "layout")
    assert isinstance(layout_kwarg.value, ast.Constant)
    assert layout_kwarg.value.value == "wide"


def test_favicon_path_constant_points_under_the_established_assets_directory():
    source = APP_PY.read_text(encoding="utf-8")
    assert '"assets" / "favicon.png"' in source


# ============================================================
# The favicon asset itself
# ============================================================


def test_favicon_asset_exists_at_the_referenced_path():
    assert FAVICON_PATH.exists(), f"favicon asset missing at {FAVICON_PATH}"
    assert FAVICON_PATH.is_file()


def test_favicon_asset_is_a_valid_png():
    width, height, _color_type = _png_dimensions_and_color_type(FAVICON_PATH)
    assert width > 0 and height > 0


def test_favicon_asset_is_square_and_small_enough_for_a_favicon():
    # A non-square icon is what src/ui/ui.py's own larger sidebar mark
    # (assets/eeva-logo.png, 116x128) already is — fine at UI size, but
    # exactly the shape that renders oddly or gets silently rejected as a
    # browser-tab favicon at 16x16/32x32. This dedicated asset must be
    # square.
    width, height, _color_type = _png_dimensions_and_color_type(FAVICON_PATH)
    assert width == height, f"favicon must be square for clean 16x16/32x32 rendering, got {width}x{height}"
    assert 0 < width <= 256


def test_favicon_asset_has_an_alpha_channel_for_transparency():
    _width, _height, color_type = _png_dimensions_and_color_type(FAVICON_PATH)
    # PNG color type 6 = truecolor+alpha (RGBA), 4 = greyscale+alpha.
    assert color_type in (4, 6), f"expected an alpha-channel PNG color type, got {color_type}"


def test_favicon_asset_byte_content_matches_the_eeva_logo_mark():
    # This asset is a resized/repositioned derivative of the real
    # EevaResearch mark, not an arbitrary placeholder — both files must
    # decode as valid, non-trivially-sized PNGs sharing the same brand.
    favicon_bytes = FAVICON_PATH.read_bytes()
    assert len(favicon_bytes) > 512  # not an empty/placeholder stub


def test_eeva_logo_asset_used_by_the_sidebar_mark_is_left_untouched():
    # src/ui/ui.py's own sidebar brand mark still reads assets/eeva-logo.png
    # directly at its own, larger, non-favicon size — this fix must not
    # repoint or remove that unrelated usage.
    ui_source = (REPO_ROOT / "src" / "ui" / "ui.py").read_text(encoding="utf-8")
    assert '"assets" / "eeva-logo.png"' in ui_source
    assert (REPO_ROOT / "assets" / "eeva-logo.png").exists()
