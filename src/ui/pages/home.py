"""Home — a compatibility redirect, not a page.

This used to be the landing page: it took the root path on a session's
first visit and rendered a hero, an "Explore the research" CTA into
Dashboard, and a capability list that duplicated About. The root now
belongs to Dashboard unconditionally, so a visitor reaches the research
app immediately rather than through an interstitial.

The route itself is kept, registered at an explicit url_path="home"
(app.py) and hidden from every navigation surface, purely so an existing
/home bookmark or inbound link redirects instead of 404ing. render()
therefore emits no markup at all — no hero, no CTA, no capability list —
and performs exactly one st.switch_page() into Dashboard. Emitting
nothing is deliberate: anything drawn here would flash before the
redirect lands.

Nothing points at this route any more. The sidebar wordmark goes
straight to Dashboard (src/ui/ui.py), and the three lines of copy worth
keeping moved to About's "What the tool does" section. The old hero's
.er-hero-* styles stay in assets/styles.css because Daily News uses
them.
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
