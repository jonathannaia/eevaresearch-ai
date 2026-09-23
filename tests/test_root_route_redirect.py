"""The root is the research app, and /home is a redirect that survives.

The in-app landing page used to take "/" on a session's first visit and
show a hero, an "Explore the research" CTA and a capability list that
duplicated About. Dashboard now owns the root unconditionally. The
/home route stays registered so an existing bookmark redirects rather
than 404s, which makes two things worth proving rather than assuming:
that nothing renders on the way through, and that nothing points back at
it — a brand link aimed at Home is exactly the shape a redirect loop
would take.

No data access, auth, worker or deployment behaviour is exercised here;
these are route-shape assertions."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from src.ui import ui as ui_module
from src.ui.pages import dashboard, home
from src.ui.theme import ROUTE_PATHS

APP_PATH = Path(__file__).parent.parent / "app.py"

# Every deep route whose url_path must not move.
DEEP_ROUTES = {
    "dashboard": "dashboard",
    "radar_inbox": "radar-inbox",
    "daily_news": "daily-news",
    "coverage": "coverage",
    "themes": "themes",
    "signals": "signals",
    "methodology": "methodology",
    "about": "about",
    "disclaimer": "disclaimer",
}

# Visible copy from the retired page. Checked against rendered output
# AND against the module source.
RETIRED_HERO_COPY = (
    "Research grounded in what was actually filed and said.",
    "Explore the research",
    "What Eeva does today",
    "Cross-market primary sources",
)

# Class names the retired page used. Checked against the module source
# ONLY: the .er-hero-* rules stay in assets/styles.css on purpose (Daily
# News uses them), so they legitimately appear in every page's injected
# <style> block and prove nothing about rendered content.
RETIRED_HERO_CLASSES = ("er-hero-title", "er-hero-sub", "er-eyebrow", "er-home-column")


def _sign_in(monkeypatch, email: str = "reader@example.test") -> None:
    from streamlit.runtime import context as ctx_module
    from streamlit import user_info as user_info_module

    monkeypatch.setattr(ctx_module, "_get_user_info", lambda: {"is_logged_in": True, "email": email}, raising=False)
    monkeypatch.setattr(user_info_module, "_get_user_info", lambda: {"is_logged_in": True, "email": email})
    monkeypatch.delenv("EDGE_PRIVATE_BETA_ALLOWED_EMAILS", raising=False)


def _run_app(monkeypatch, runs: int = 1) -> AppTest:
    _sign_in(monkeypatch)
    at = AppTest.from_file(str(APP_PATH), default_timeout=15)
    for _ in range(runs):
        at.run()
    return at


def _text(at: AppTest) -> str:
    """Rendered content only. The injected <style> block carries the whole
    stylesheet — including the retained .er-hero-* rules — and would make
    any copy assertion below meaningless."""
    markdown = " ".join(m.value for m in at.get("markdown") if not m.value.startswith("<style>"))
    return markdown + " ".join(str(w.value) for w in at.get("write"))


# --- 1. the root opens the research app -------------------------------------------

@pytest.mark.parametrize("runs", [1, 2, 3])
def test_root_opens_dashboard_on_the_first_visit_and_on_later_visits(monkeypatch, runs):
    at = _run_app(monkeypatch, runs=runs)
    assert not at.exception
    pages = at.session_state["_pages"]
    assert pages["dashboard"]._default is True
    assert pages["home"]._default is False


def test_the_root_no_longer_renders_any_landing_markup(monkeypatch):
    at = _run_app(monkeypatch)
    rendered = _text(at)
    for fragment in RETIRED_HERO_COPY:
        assert fragment not in rendered, fragment


def test_exactly_one_page_owns_the_root(monkeypatch):
    """Two defaults would make which page answers "/" undefined."""
    at = _run_app(monkeypatch)
    pages = at.session_state["_pages"]
    defaults = sorted(key for key, page in pages.items() if page._default)
    assert defaults == ["dashboard"]


# --- 2. /home redirects safely and never 404s ---------------------------------------

def test_home_stays_registered_at_an_explicit_path_so_it_cannot_404(monkeypatch):
    at = _run_app(monkeypatch)
    pages = at.session_state["_pages"]
    assert "home" in pages
    assert pages["home"].url_path == "home"


def test_home_is_hidden_from_navigation(monkeypatch):
    at = _run_app(monkeypatch)
    assert at.session_state["_pages"]["home"]._visibility == "hidden"


def test_home_redirects_to_dashboard():
    target = object()
    with patch.object(home, "get_page", return_value=target) as get_page, \
         patch.object(home.st, "switch_page") as switch_page:
        home.render()
    get_page.assert_called_once_with("dashboard")
    switch_page.assert_called_once_with(target)


def test_home_falls_through_quietly_if_dashboard_is_unregistered():
    """An empty page beats an exception: every other route keeps working."""
    with patch.object(home, "get_page", return_value=None), \
         patch.object(home.st, "switch_page") as switch_page:
        home.render()  # must not raise
    switch_page.assert_not_called()


# --- 3. /home emits no hero, CTA or capability markup ---------------------------------

def test_home_renders_nothing_on_the_way_through():
    """Anything drawn here would flash before the redirect lands."""
    emitted: list[object] = []

    def _capture(*args, **kwargs):
        emitted.append(args)

    with patch.object(home, "get_page", return_value=object()), \
         patch.object(home.st, "switch_page"), \
         patch.object(home.st, "markdown", _capture), \
         patch.object(home.st, "write", _capture), \
         patch.object(home.st, "columns", _capture), \
         patch.object(home.st, "divider", _capture), \
         patch.object(home.st, "page_link", _capture):
        home.render()
    assert emitted == []


@pytest.mark.parametrize("fragment", RETIRED_HERO_COPY + RETIRED_HERO_CLASSES)
def test_the_retired_hero_copy_is_gone_from_the_module(fragment):
    source = (Path(__file__).parent.parent / "src" / "ui" / "pages" / "home.py").read_text(encoding="utf-8")
    code = source.split('"""', 2)[2] if source.count('"""') >= 2 else source
    assert fragment not in code


def test_home_no_longer_builds_a_capability_list():
    assert not hasattr(home, "_CAPABILITIES")


# --- 4. exactly one switch_page from Home, none from Dashboard ---------------------------

def test_home_produces_exactly_one_switch_page_call():
    with patch.object(home, "get_page", return_value=object()), \
         patch.object(home.st, "switch_page") as switch_page:
        home.render()
    assert switch_page.call_count == 1


def test_dashboard_produces_no_switch_page_call():
    source = (Path(__file__).parent.parent / "src" / "ui" / "pages" / "dashboard.py").read_text(encoding="utf-8")
    assert "switch_page" not in source
    assert not hasattr(dashboard, "_REDIRECT_TARGET")


# --- 5. nothing points back at Home ---------------------------------------------------------

def test_the_sidebar_brand_link_points_at_dashboard_not_home():
    """A brand link aimed at Home is the shape a redirect loop takes."""
    source = (Path(__file__).parent.parent / "src" / "ui" / "ui.py").read_text(encoding="utf-8")
    body = source[source.index("def render_sidebar("):source.index("def render_footer(")]
    assert 'brand_page = pages.get("dashboard")' in body
    assert 'pages.get("home")' not in body


def test_no_navigation_group_lists_home():
    from src.ui.ui import HIDDEN_FROM_NAV, PRIMARY_NAV, SYSTEM_NAV

    keys = {key for key, _ in PRIMARY_NAV + SYSTEM_NAV + HIDDEN_FROM_NAV}
    assert "home" not in keys


def test_no_page_module_links_to_home(monkeypatch):
    """Any surviving CTA into Home would bounce the reader through an
    extra rerun for nothing."""
    pages_dir = Path(__file__).parent.parent / "src" / "ui" / "pages"
    offenders = []
    for path in sorted(pages_dir.glob("*.py")):
        if path.name == "home.py":
            continue
        source = path.read_text(encoding="utf-8")
        if 'get_page("home")' in source or "get_page('home')" in source:
            offenders.append(path.name)
    assert offenders == []


def test_no_registered_page_redirects_into_home(monkeypatch):
    at = _run_app(monkeypatch)
    pages = at.session_state["_pages"]
    home_page = pages["home"]
    # Home must not be anything else's default, and must not own the root.
    assert home_page._default is False
    assert home_page.url_path != ""


# --- 6. deep links keep their paths ------------------------------------------------------------

@pytest.mark.parametrize("key,url_path", sorted(DEEP_ROUTES.items()))
def test_every_deep_route_keeps_its_url_path(monkeypatch, key, url_path):
    """Read via the private `_url_path`: Streamlit's public `url_path`
    property reports "" for whichever page is default, so the root owner
    would otherwise look as though its own path had disappeared. Routing
    matches on the internal value, which is why a default page stays
    reachable at both "/" and its own path."""
    at = _run_app(monkeypatch)
    pages = at.session_state["_pages"]
    assert key in pages, key
    assert pages[key]._url_path == url_path


def test_dashboard_answers_both_the_root_and_its_own_path(monkeypatch):
    at = _run_app(monkeypatch)
    dashboard_page = at.session_state["_pages"]["dashboard"]
    assert dashboard_page._default is True           # answers "/"
    assert dashboard_page._url_path == "dashboard"   # and "/dashboard"
    assert dashboard_page.url_path == ""             # the property masks it, by design


def test_the_theme_bootstrap_knows_every_route_including_home():
    for url_path in DEEP_ROUTES.values():
        assert f"/{url_path}" in ROUTE_PATHS, url_path
    assert "/" in ROUTE_PATHS
    assert "/home" in ROUTE_PATHS


# --- 7. the public static marketing site is out of scope -------------------------------------------

def test_the_public_static_site_source_exists_and_is_untouched():
    """landing/ IS the eevaresearch.com Render Static Site, tracked in
    this repo. It is explicitly out of scope, so the assertion is that it
    is unchanged — not that it is absent."""
    import subprocess

    repo_root = Path(__file__).parent.parent
    assert (repo_root / "landing" / "index.html").exists()

    changed = subprocess.run(
        ["git", "status", "--porcelain", "--", "landing"],
        cwd=repo_root, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert changed == "", f"the public static site was modified: {changed}"


def test_the_streamlit_app_does_not_serve_the_static_landing_page():
    """The two are separate deployments; the app must not start pulling
    the marketing page in."""
    repo_root = Path(__file__).parent.parent
    # Comments may name the static site to document the separation
    # (app.py already does). What must not appear is executable code that
    # reads or serves it.
    for name in ("app.py", "src/ui/pages/home.py", "src/ui/ui.py"):
        source = (repo_root / name).read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        for reference in ("landing/", "index.html"):
            assert reference not in code, f"{name} has executable code referencing {reference}"


def test_the_retained_copy_lives_in_about_and_not_in_the_footer():
    about = (Path(__file__).parent.parent / "src" / "ui" / "pages" / "about.py").read_text(encoding="utf-8")
    for fragment in (
        "Evidence-first research across AI infrastructure",
        "what was actually filed and said",
        "SEC EDGAR, DART, and EDINET",
    ):
        assert fragment in about, fragment

    ui_source = (Path(__file__).parent.parent / "src" / "ui" / "ui.py").read_text(encoding="utf-8")
    footer = ui_source[ui_source.index("def render_footer("):]
    assert "Evidence-first research across AI infrastructure" not in footer


def test_the_shared_hero_styles_are_retained_for_other_pages():
    css = (Path(__file__).parent.parent / "assets" / "styles.css").read_text(encoding="utf-8")
    assert ".er-hero" in css
