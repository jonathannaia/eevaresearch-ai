"""The root path — a redirect, not a page.

Something must own "/". Streamlit picks the page marked ``default=True``
and, if no page is marked, silently promotes the first one in the list
and mutates its ``_default`` (streamlit/commands/navigation.py). There is
no such thing as "no default page".

That matters because ``Page.url_path`` is ``"" if self._default else
self._url_path`` (streamlit/navigation/page.py). The default page has no
public URL of its own: it drops out of the routing table, and every
``st.page_link`` pointing at it renders ``href=""``, which resolves to
whatever page the reader is already on. Dashboard briefly held that role
and paid all three prices — ``/dashboard`` answered with a "Page not
found" toast, and the sidebar's own Dashboard link and the wordmark
became inert.

So the default page is this one: a route nothing links to, whose only
job is to hand the reader to Dashboard. Dashboard goes back to being an
ordinary page with a real ``url_path`` of "dashboard", reachable by URL
and linkable from the sidebar.

``url_path=""`` is deliberate and is accepted precisely because this page
is the default (page.py rejects an empty path on any other page). The
cost is one extra hop: "/" lands on "/dashboard", and the address bar
says so. That is the price of Dashboard keeping its own URL, and it is
the right trade.

render() emits nothing at all, for the same reason src/ui/pages/home.py
does: anything drawn here would flash before the redirect lands.
"""
from __future__ import annotations

import streamlit as st

from src.ui.ui import get_page

_REDIRECT_TARGET = "dashboard"


def render() -> None:
    """Redirect to Dashboard. Renders nothing on any path.

    If Dashboard were somehow unregistered, falling through silently is
    better than raising: the visitor lands on an empty page rather than
    an error, and every other route is unaffected."""
    target = get_page(_REDIRECT_TARGET)
    if target is not None:
        st.switch_page(target)
