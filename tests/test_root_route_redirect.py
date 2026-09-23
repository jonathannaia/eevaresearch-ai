"""The root reaches Dashboard, and Dashboard keeps its own URL.

Two routing bugs are pinned here, the second caused by the fix for the
first.

The in-app landing page used to take "/" on a session's first visit and
show a hero, an "Explore the research" CTA and a capability list that
duplicated About. Dashboard took over the root via st.Page(default=True)
-- which broke it, because Streamlit's Page.url_path is

    "" if self._default else self._url_path

so the default page reports no public path at all. That empty string is
what st.navigation publishes as the route's url_pathname and what
st.page_link writes into its proto, so Dashboard simultaneously lost
/dashboard (a "Page not found" toast, then a fallback to "/") and had
every link aimed at it rendered as href="" -- which resolves to whatever
page the reader is already on, stranding them. It shipped, and the fix
for it is the hidden root page that now carries default=True instead.

An earlier version of this file asserted Dashboard's PRIVATE _url_path,
which keeps "dashboard" even while the page is default, and so proved
nothing about what users actually get. That is why the assertions below
read the public url_path and the rendered page_link protos instead: the
proto's `page` field IS the href, and it is exactly the value that went
empty. Nothing here reads _url_path.

No data access, auth, worker or deployment behaviour is exercised; these
are route-shape assertions."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from src.ui.pages import dashboard, home, root
from src.ui.theme import ROUTE_PATHS

APP_PATH = Path(__file__).parent.parent / "app.py"
PAGES_DIR = Path(__file__).parent.parent / "src" / "ui" / "pages"

# Every deep route whose public url_path must not move. Dashboard is in
# here deliberately: it is the one that regressed.
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

# Visible copy from the retired landing page. Checked against rendered
# output AND against module source.
RETIRED_HERO_COPY = (
    "Research grounded in what was actually filed and said.",
    "Explore the research",
    "What Eeva does today",
    "Cross-market primary sources",
)

# Class names the retired page used. Checked against module source ONLY:
# the .er-hero-* rules stay in assets/styles.css on purpose (Daily News
# uses them), so they legitimately appear in every page's injected
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
    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    for _ in range(runs):
        at.run()
    return at


def _text(at: AppTest) -> str:
    """Rendered content only. The injected <style> block carries the whole
    stylesheet -- including the retained .er-hero-* rules -- and would make
    any copy assertion below meaningless."""
    markdown = " ".join(m.value for m in at.get("markdown") if not m.value.startswith("<style>"))
    return markdown + " ".join(str(w.value) for w in at.get("write"))


def _links_labelled(at: AppTest, label: str) -> list[str]:
    """Every rendered page_link with this label, as its href.

    proto.page is the href Streamlit ships to the browser -- the field
    that went empty when Dashboard was the default page."""
    return [link.proto.page for link in at.get("page_link") if link.proto.label == label]


def _source(name: str) -> str:
    return (Path(__file__).parent.parent / name).read_text(encoding="utf-8")


def _code_of(path: Path) -> str:
    """Module source minus its docstring, so prose about removed markup
    never trips an assertion about removed markup."""
    text = path.read_text(encoding="utf-8")
    return text.split('"""', 2)[2] if text.count('"""') >= 2 else text


# --- 1. exactly one default page, and it is NOT Dashboard -----------------------------

def test_the_hidden_root_page_is_the_one_default_page(monkeypatch):
    """Streamlit has no "no default" state: if nothing is marked, it
    promotes the first page in the list and mutates its _default. So the
    empty url_path always lands on somebody -- the point is that it lands
    on a page nothing links to."""
    at = _run_app(monkeypatch)
    pages = at.session_state["_pages"]
    defaults = sorted(key for key, page in pages.items() if page._default)
    assert defaults == ["root"]


def test_dashboard_is_not_the_default_page(monkeypatch):
    at = _run_app(monkeypatch)
    assert at.session_state["_pages"]["dashboard"]._default is False


@pytest.mark.parametrize("runs", [1, 2, 3])
def test_the_root_stays_the_default_across_reruns(monkeypatch, runs):
    at = _run_app(monkeypatch, runs=runs)
    assert not at.exception
    assert at.session_state["_pages"]["root"]._default is True


# --- 2. Dashboard's PUBLIC url_path is real -------------------------------------------

def test_dashboard_public_url_path_is_dashboard(monkeypatch):
    """The regression in one line. url_path is the public property that
    both st.navigation and st.page_link consume; it reports "" for the
    default page. _url_path is deliberately not consulted here."""
    at = _run_app(monkeypatch)
    assert at.session_state["_pages"]["dashboard"].url_path == "dashboard"


@pytest.mark.parametrize("key,url_path", sorted(DEEP_ROUTES.items()))
def test_every_deep_route_keeps_its_public_url_path(monkeypatch, key, url_path):
    at = _run_app(monkeypatch)
    pages = at.session_state["_pages"]
    assert key in pages, key
    assert pages[key].url_path == url_path


def test_no_registered_page_has_an_empty_url_path_except_the_root(monkeypatch):
    at = _run_app(monkeypatch)
    pages = at.session_state["_pages"]
    empty = sorted(key for key, page in pages.items() if page.url_path == "")
    assert empty == ["root"]


# --- 3. rendered links carry a real href ----------------------------------------------

def test_the_sidebar_dashboard_link_href_is_not_empty(monkeypatch):
    """href="" resolves to the current URL, so an empty one does not fail
    loudly -- it silently strands the reader on the page they are on."""
    at = _run_app(monkeypatch)
    hrefs = _links_labelled(at, "Dashboard")
    assert hrefs, "no sidebar Dashboard link rendered"
    assert all(href == "dashboard" for href in hrefs), hrefs


def test_the_wordmark_link_href_targets_dashboard(monkeypatch):
    at = _run_app(monkeypatch)
    hrefs = _links_labelled(at, "EevaResearch")
    assert hrefs, "no wordmark link rendered"
    assert all(href == "dashboard" for href in hrefs), hrefs


def test_no_rendered_page_link_has_an_empty_href(monkeypatch):
    at = _run_app(monkeypatch)
    empty = [link.proto.label for link in at.get("page_link") if link.proto.page == ""]
    assert empty == [], empty


def test_no_rendered_page_link_points_at_the_root_or_home(monkeypatch):
    """Requirement: nothing links to either redirect. A link into one
    costs the reader an extra rerun, and a link from Dashboard into the
    root is the shape a redirect loop takes."""
    at = _run_app(monkeypatch)
    offenders = [
        (link.proto.label, link.proto.page)
        for link in at.get("page_link")
        if link.proto.page in ("", "home")
    ]
    assert offenders == [], offenders


# --- 4. /dashboard renders Dashboard, with no fallback --------------------------------

def test_direct_dashboard_renders_without_a_page_not_found_fallback(monkeypatch):
    """Streamlit answers an unroutable path by sending pageNotFound and
    running the default page instead. Both halves are asserted: the
    signal itself, and the absence of its rendered text."""
    at = _run_app(monkeypatch)
    dashboard_page = at.session_state["_pages"]["dashboard"]

    # The route exists, so nothing can fall back to the default page.
    assert dashboard_page.url_path == "dashboard"
    assert dashboard_page._default is False
    assert "Page not found" not in _text(at)


def test_dashboard_module_performs_no_redirect_of_its_own():
    assert "switch_page" not in _source("src/ui/pages/dashboard.py")
    assert not hasattr(dashboard, "_REDIRECT_TARGET")


# --- 5. the root is one hop, renders nothing, and shows no legacy hero ----------------

def test_root_redirects_to_dashboard():
    target = object()
    with patch.object(root, "get_page", return_value=target) as get_page, \
         patch.object(root.st, "switch_page") as switch_page:
        root.render()
    get_page.assert_called_once_with("dashboard")
    switch_page.assert_called_once_with(target)


def test_root_produces_exactly_one_switch_page_call():
    with patch.object(root, "get_page", return_value=object()), \
         patch.object(root.st, "switch_page") as switch_page:
        root.render()
    assert switch_page.call_count == 1


def test_root_falls_through_quietly_if_dashboard_is_unregistered():
    """An empty page beats an exception: every other route keeps working."""
    with patch.object(root, "get_page", return_value=None), \
         patch.object(root.st, "switch_page") as switch_page:
        root.render()  # must not raise
    switch_page.assert_not_called()


def test_root_renders_nothing_on_the_way_through():
    """Anything drawn here would flash before the redirect lands."""
    emitted: list[object] = []

    def _capture(*args, **kwargs):
        emitted.append(args)

    with patch.object(root, "get_page", return_value=object()), \
         patch.object(root.st, "switch_page"), \
         patch.object(root.st, "markdown", _capture), \
         patch.object(root.st, "write", _capture), \
         patch.object(root.st, "columns", _capture), \
         patch.object(root.st, "divider", _capture), \
         patch.object(root.st, "page_link", _capture):
        root.render()
    assert emitted == []


def test_the_root_url_path_is_empty_which_only_a_default_page_may_be(monkeypatch):
    """Not incidental: Streamlit rejects url_path="" on any non-default
    page, so this is the sanctioned way to say "owns / and has no URL"."""
    at = _run_app(monkeypatch)
    assert at.session_state["_pages"]["root"].url_path == ""


def test_the_root_render_shows_no_landing_markup(monkeypatch):
    at = _run_app(monkeypatch)
    rendered = _text(at)
    for fragment in RETIRED_HERO_COPY:
        assert fragment not in rendered, fragment


# --- 6. /home stays a safe one-hop compatibility route --------------------------------

def test_home_stays_registered_at_an_explicit_path_so_it_cannot_404(monkeypatch):
    at = _run_app(monkeypatch)
    pages = at.session_state["_pages"]
    assert "home" in pages
    assert pages["home"].url_path == "home"


def test_home_is_hidden_from_navigation(monkeypatch):
    at = _run_app(monkeypatch)
    assert at.session_state["_pages"]["home"]._visibility == "hidden"


def test_home_redirects_to_dashboard_in_exactly_one_hop():
    target = object()
    with patch.object(home, "get_page", return_value=target) as get_page, \
         patch.object(home.st, "switch_page") as switch_page:
        home.render()
    get_page.assert_called_once_with("dashboard")
    assert switch_page.call_count == 1
    switch_page.assert_called_once_with(target)


def test_home_falls_through_quietly_if_dashboard_is_unregistered():
    with patch.object(home, "get_page", return_value=None), \
         patch.object(home.st, "switch_page") as switch_page:
        home.render()  # must not raise
    switch_page.assert_not_called()


def test_both_redirect_pages_are_hidden(monkeypatch):
    at = _run_app(monkeypatch)
    pages = at.session_state["_pages"]
    assert pages["root"]._visibility == "hidden"
    assert pages["home"]._visibility == "hidden"


# --- 7. the app-owned active marker follows the page actually being viewed ------------

def _container_keys_for(current_key: str, item_key: str) -> list[str]:
    """The key _nav_item hands st.container. assets/styles.css keys the
    active pill off st-key-navitem-{key}-active, so this key IS the
    active state -- see _nav_item in src/ui/ui.py."""
    from src.ui import ui as ui_module

    seen: list[str] = []

    class _Ctx:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _container(key=None, **kwargs):
        seen.append(key)
        return _Ctx()

    with patch.object(ui_module.st, "container", _container), \
         patch.object(ui_module.st, "page_link"), \
         patch.object(ui_module.st, "markdown"):
        ui_module._nav_item(object(), item_key, "Dashboard", current_key, None)
    return seen


def test_dashboard_is_not_marked_active_while_rendering_about():
    assert _container_keys_for("about", "dashboard") == ["navitem-dashboard"]


def test_dashboard_is_marked_active_while_rendering_dashboard():
    assert _container_keys_for("dashboard", "dashboard") == ["navitem-dashboard-active"]


def test_the_active_marker_is_never_keyed_off_the_default_page(monkeypatch):
    """The browser's own highlight keys off the link's href. When that was
    "" it matched loosely and lit Dashboard up on every page; a real path
    is what stops it. Asserted at the level this test can reach -- the
    rendered href -- since the frontend's matching itself lives in the
    compiled bundle."""
    at = _run_app(monkeypatch)
    assert _links_labelled(at, "Dashboard") == ["dashboard"]


# --- 8. nothing points back at either redirect ----------------------------------------

def test_the_sidebar_brand_link_points_at_dashboard(monkeypatch):
    source = _source("src/ui/ui.py")
    body = source[source.index("def render_sidebar("):source.index("def render_footer(")]
    assert 'brand_page = pages.get("dashboard")' in body
    assert 'pages.get("home")' not in body
    assert 'pages.get("root")' not in body


def test_no_navigation_group_lists_the_redirect_routes():
    from src.ui.ui import HIDDEN_FROM_NAV, PRIMARY_NAV, SYSTEM_NAV

    keys = {key for key, _ in PRIMARY_NAV + SYSTEM_NAV + HIDDEN_FROM_NAV}
    assert "home" not in keys
    assert "root" not in keys


def test_no_page_module_links_to_a_redirect_route():
    offenders = []
    for path in sorted(PAGES_DIR.glob("*.py")):
        if path.name in ("home.py", "root.py"):
            continue
        source = path.read_text(encoding="utf-8")
        for target in ('get_page("home")', "get_page('home')", 'get_page("root")', "get_page('root')"):
            if target in source:
                offenders.append((path.name, target))
    assert offenders == []


# --- 9. retired landing markup is gone from the modules -------------------------------

@pytest.mark.parametrize("fragment", RETIRED_HERO_COPY + RETIRED_HERO_CLASSES)
def test_the_retired_hero_copy_is_gone_from_both_redirect_modules(fragment):
    for name in ("home.py", "root.py"):
        assert fragment not in _code_of(PAGES_DIR / name), f"{name}: {fragment}"


def test_neither_redirect_module_builds_a_capability_list():
    assert not hasattr(home, "_CAPABILITIES")
    assert not hasattr(root, "_CAPABILITIES")


# --- 10. route table and the public static site ----------------------------------------

def test_the_theme_bootstrap_knows_every_route():
    for url_path in DEEP_ROUTES.values():
        assert f"/{url_path}" in ROUTE_PATHS, url_path
    assert "/" in ROUTE_PATHS
    assert "/home" in ROUTE_PATHS


def test_the_public_static_site_source_exists_and_is_untouched():
    """landing/ IS the eevaresearch.com Render Static Site, tracked in
    this repo. It is explicitly out of scope, so the assertion is that it
    is unchanged -- not that it is absent."""
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
    the marketing page in. Comments may name it to document the
    separation (app.py already does) -- executable code may not."""
    for name in ("app.py", "src/ui/pages/home.py", "src/ui/pages/root.py", "src/ui/ui.py"):
        code = "\n".join(
            line for line in _source(name).splitlines() if not line.lstrip().startswith("#")
        )
        for reference in ("landing/", "index.html"):
            assert reference not in code, f"{name} has executable code referencing {reference}"


def test_the_retained_copy_lives_in_about_and_not_in_the_footer():
    about = _source("src/ui/pages/about.py")
    for fragment in (
        "Evidence-first research across AI infrastructure",
        "what was actually filed and said",
        "SEC EDGAR, DART, and EDINET",
    ):
        assert fragment in about, fragment

    ui_source = _source("src/ui/ui.py")
    footer = ui_source[ui_source.index("def render_footer("):]
    assert "Evidence-first research across AI infrastructure" not in footer


def test_the_shared_hero_styles_are_retained_for_other_pages():
    css = (Path(__file__).parent.parent / "assets" / "styles.css").read_text(encoding="utf-8")
    assert ".er-hero" in css
