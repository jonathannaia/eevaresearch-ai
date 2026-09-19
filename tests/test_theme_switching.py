"""Theme switching (src/ui/theme.py): preference resolution and
persistence, the sidebar account-menu control, the ⌘K "Switch theme"
commands, and the client bridge script.

Browser behavior verified live against the local harness (see the
release report): System by default with no reload; a switch to Light
with the OS in Dark reloads exactly once and lands with native widgets,
tokens, data-theme, cookie and storage all Light; ⌘K "Switch theme: Dark"
likewise; and after a top-level redirect from a different site back to
the app (the shape of the Google sign-in return) the SameSite=Lax cookie
arrives and the server renders the saved theme on the first paint."""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.data_access import backend_factory
from src.ui import theme

REPO_ROOT = Path(__file__).parent.parent
HARNESS_DIR = REPO_ROOT / "tests" / "apptest_pages"


# --- bridge script ------------------------------------------------------------

def _body(preference: str) -> str:
    script = theme.bridge_script(preference)
    assert script.startswith("<script>") and script.endswith("</script>")
    return script[len("<script>"):-len("</script>")]


@pytest.mark.parametrize("preference", ["system", "dark", "light"])
def test_bridge_body_never_contains_a_less_than_sign(preference):
    """Streamlit sanitizes st.html with DOMPurify, which silently drops a
    script whose text has "<" followed by a word character — found live:
    a "t<10000" comparison made the whole bridge vanish."""
    assert "<" not in _body(preference)


@pytest.mark.parametrize("preference,native", [("system", "System"), ("dark", "Dark"), ("light", "Light")])
def test_bridge_carries_the_choice_and_streamlits_own_label(preference, native):
    body = _body(preference)
    assert f'var p="{preference}",n="{native}"' in body


def test_bridge_stamps_data_theme_only_for_an_explicit_choice():
    body = _body("light")
    assert "if(p==='system'){d.removeAttribute('data-theme');}else{d.setAttribute('data-theme',p);}" in body


def test_bridge_cookie_is_lax_site_wide_for_a_year_and_secure_on_https():
    body = _body("dark")
    assert "document.cookie='eeva_theme='+p+'; Path=/; Max-Age=31536000; SameSite=Lax'" in body
    assert "+(location.protocol==='https:'?'; Secure':'')" in body
    assert "SameSite=Strict" not in body  # Strict would be dropped on the sign-in redirect back


def test_bridge_writes_streamlits_theme_key_for_every_route_and_the_current_path():
    body = _body("light")
    paths = json.loads(re.search(r"paths=(\[[^\]]*\])", body).group(1))
    assert paths == list(theme.ROUTE_PATHS)
    assert "'stActiveTheme-'+x+'-v2'" in body
    assert "paths.concat([location.pathname])" in body
    assert "ls.setItem('eeva.theme',p)" in body


def test_bridge_reloads_at_most_once_per_choice_and_never_without_storage():
    body = _body("light")
    # Storage unavailable (private mode, blocked): stop before any reload.
    assert "try{ls=window.localStorage;ss=window.sessionStorage;ls.getItem('x');ss.getItem('x');}catch(e){return;}" in body
    # Already in agreement, or nothing stored and System wanted: no reload.
    assert "if(before===want||(before===null&&n==='System')){return;}" in body
    # One reload per choice inside the guard window.
    assert "if(last&&last.n===n&&last.t+10000>Date.now()){return;}" in body
    assert body.count("location.reload()") == 1
    assert body.index("ss.setItem('eeva.theme.reload'") < body.index("location.reload()")


def test_route_paths_cover_every_page_app_py_registers():
    source = (REPO_ROOT / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    registered = set(re.findall(r'url_path="([^"]+)"', source))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "_URL_PATHS" for t in node.targets):
            registered |= set(ast.literal_eval(node.value).values())
    assert registered, "no url paths found in app.py"
    assert {f"/{p}" for p in registered} | {"/"} == set(theme.ROUTE_PATHS)


# --- resolution and persistence ----------------------------------------------------

_RESOLVE_SCRIPT = """
import streamlit as st
from src.ui import theme
st.session_state["resolved"] = theme.current_preference()
"""


@pytest.fixture
def prefs_repo(tmp_path, monkeypatch):
    repo = backend_factory.JsonUserPreferencesRepository(cache_dir=tmp_path)
    calls = {"opened": 0}

    def _open():
        calls["opened"] += 1
        return repo

    monkeypatch.setattr(theme, "_preferences_repository", _open)
    return repo, calls


def _run(script: str = _RESOLVE_SCRIPT) -> AppTest:
    at = AppTest.from_string(script, default_timeout=10)
    at.run()
    assert not at.exception
    return at


def test_no_session_account_or_cookie_resolves_to_system(monkeypatch):
    monkeypatch.setattr(theme, "_cookie_preference", lambda: None)
    monkeypatch.setattr(theme, "_signed_in_email", lambda: None)
    assert _run().session_state["resolved"] == "system"


def test_cookie_applies_when_signed_out(monkeypatch):
    monkeypatch.setattr(theme, "_cookie_preference", lambda: "light")
    monkeypatch.setattr(theme, "_signed_in_email", lambda: None)
    assert _run().session_state["resolved"] == "light"


def test_account_preference_beats_the_cookie_and_is_read_once_per_session(monkeypatch, prefs_repo):
    repo, calls = prefs_repo
    repo.set_theme_preference("reader@example.test", "dark", "t0")
    monkeypatch.setattr(theme, "_cookie_preference", lambda: "light")
    monkeypatch.setattr(theme, "_signed_in_email", lambda: "reader@example.test")
    at = _run()
    assert at.session_state["resolved"] == "dark"
    at.run()
    at.run()
    assert calls["opened"] == 1


def test_signed_in_account_with_nothing_saved_falls_back_to_the_cookie(monkeypatch, prefs_repo):
    monkeypatch.setattr(theme, "_cookie_preference", lambda: "light")
    monkeypatch.setattr(theme, "_signed_in_email", lambda: "new@example.test")
    assert _run().session_state["resolved"] == "light"


def test_a_choice_made_this_session_wins_and_is_saved_to_the_account(monkeypatch, prefs_repo):
    repo, _calls = prefs_repo
    repo.set_theme_preference("reader@example.test", "dark", "t0")
    monkeypatch.setattr(theme, "_cookie_preference", lambda: None)
    monkeypatch.setattr(theme, "_signed_in_email", lambda: "reader@example.test")
    at = _run("""
import streamlit as st
from src.ui import theme
if "chose" not in st.session_state:
    st.session_state["chose"] = theme.set_preference("light")
st.session_state["resolved"] = theme.current_preference()
""")
    assert at.session_state["chose"] is True
    assert at.session_state["resolved"] == "light"
    assert repo.get_preferences("reader@example.test").theme_preference == "light"


def test_signed_out_choice_is_session_only_and_touches_no_account(monkeypatch, prefs_repo):
    _repo, calls = prefs_repo
    monkeypatch.setattr(theme, "_cookie_preference", lambda: None)
    monkeypatch.setattr(theme, "_signed_in_email", lambda: None)
    at = _run("""
import streamlit as st
from src.ui import theme
theme.set_preference("dark")
st.session_state["resolved"] = theme.current_preference()
""")
    assert at.session_state["resolved"] == "dark"
    assert calls["opened"] == 0


def test_an_unknown_choice_changes_nothing(monkeypatch):
    monkeypatch.setattr(theme, "_cookie_preference", lambda: None)
    monkeypatch.setattr(theme, "_signed_in_email", lambda: None)
    at = _run("""
import streamlit as st
from src.ui import theme
st.session_state["accepted"] = theme.set_preference("sepia")
st.session_state["resolved"] = theme.current_preference()
""")
    assert at.session_state["accepted"] is False
    assert at.session_state["resolved"] == "system"


def test_a_failing_preferences_store_never_breaks_the_page(monkeypatch):
    def _broken():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(theme, "_preferences_repository", _broken)
    monkeypatch.setattr(theme, "_cookie_preference", lambda: "dark")
    monkeypatch.setattr(theme, "_signed_in_email", lambda: "reader@example.test")
    at = _run("""
import streamlit as st
from src.ui import theme
st.session_state["resolved"] = theme.current_preference()
theme.set_preference("light")
st.session_state["after"] = theme.current_preference()
""")
    assert at.session_state["resolved"] == "dark"
    assert at.session_state["after"] == "light"


# --- rendered shell: sidebar control, token CSS, bridge -------------------------------

def _style_block(at: AppTest) -> str:
    return next(m.value for m in at.markdown if m.value.startswith("<style>"))


def test_sidebar_theme_control_switches_the_rendered_theme(monkeypatch):
    monkeypatch.setattr(theme, "_cookie_preference", lambda: None)
    monkeypatch.setattr(theme, "_signed_in_email", lambda: None)
    at = AppTest.from_file(str(HARNESS_DIR / "dashboard_page.py"), default_timeout=30)
    at.run()
    assert not at.exception
    control = at.get("button_group")
    control = next(c for c in control if getattr(c, "key", None) == "sidebar-theme-control")
    assert control.value == "system"
    assert "@media (prefers-color-scheme: light)" in _style_block(at)

    control.set_value("light").run()
    assert not at.exception
    css = _style_block(at)
    assert css.split(":root{", 1)[1].startswith("color-scheme:light;--bg:#F6F4EF;")
    assert "@media (prefers-color-scheme" not in css
    assert at.session_state["sidebar-theme-control"] == "light"


def test_every_chrome_page_emits_the_bridge_inside_its_hidden_container(monkeypatch):
    monkeypatch.setattr(theme, "_cookie_preference", lambda: "dark")
    monkeypatch.setattr(theme, "_signed_in_email", lambda: None)
    at = AppTest.from_file(str(HARNESS_DIR / "dashboard_page.py"), default_timeout=30)
    at.run()
    # AppTest has no typed st.html element; read the raw protos.
    bodies = [h.proto.body for h in at.get("html") if h.proto.unsafe_allow_javascript]
    bridges = [b for b in bodies if "stActiveTheme-" in b]
    assert len(bridges) == 1
    assert 'var p="dark",n="Dark"' in bridges[0]
    css = (REPO_ROOT / "assets" / "styles.css").read_text(encoding="utf-8")
    assert '[data-testid="stLayoutWrapper"]:has(> .st-key-theme-bridge)' in css


# --- ⌘K ---------------------------------------------------------------------------------

def test_palette_theme_command_sets_the_preference_and_reruns(monkeypatch):
    from src.ui.components import command_palette

    chosen, reruns = [], []
    monkeypatch.setattr(theme, "set_preference", lambda p: chosen.append(p) or True)
    monkeypatch.setattr(command_palette.st, "rerun", lambda: reruns.append(True))
    command_palette._navigate({"group": "Switch theme", "label": "Switch theme: Light", "sub": "", "go": "theme:light"})
    assert chosen == ["light"] and reruns == [True]


def test_palette_marks_the_current_theme(monkeypatch):
    from src.ui.components import command_palette

    monkeypatch.setattr(theme, "current_preference", lambda: "dark")

    class _Empty:
        def get_all_themes(self):
            return []

        def get_all_signals(self):
            return []

    ctx = type("Ctx", (), {"theme_repository": _Empty(), "signal_repository": _Empty()})()
    items = [i for i in command_palette._index(ctx) if i["group"] == "Switch theme"]
    assert [(i["label"], i["sub"]) for i in items] == [
        ("Switch theme: System", ""), ("Switch theme: Dark", "Current"), ("Switch theme: Light", ""),
    ]
