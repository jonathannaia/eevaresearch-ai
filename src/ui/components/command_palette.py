"""Command palette (brief §12) — ⌘K/Ctrl+K fuzzy search over companies,
themes, Radar Signals and actions.

The Companies group is back (UI redesign item 1: "⌘K/search by ticker or
company"). It was removed once before, in the reader-facing data-
integrity pass, together with the Company/Watchlists/Research pages it
pointed at — none of which had live real data. What makes it honest this
time is that it searches the real issuer registry and routes at the real
Companies index (src/ui/pages/companies.py), not at a page of invented
rows. "Watchlists"/"Recent research"/"New research thread" stay gone.

Streamlit has no native palette. Two things had to be tested for real
feasibility (per the brief's own instruction) rather than assumed:

1. **A global ⌘K listener.** `st.iframe` (with a raw HTML/script string —
   the "st.components.v1.html" pattern, since renamed) renders in a
   sandboxed same-origin iframe — a `keydown` listener attached only inside that
   iframe would never fire while focus is anywhere else on the page. It
   DOES work to reach into `window.parent.document` from inside the iframe
   (same-origin), attach the listener there, and `.click()` a real
   Streamlit button found via its stable `st-key-{key}` class. That's what
   `render_palette_trigger()` does — the shortcut drives an actual
   Streamlit button/rerun, not a fake overlay. Verified working in-browser.
2. **Arrow-key result navigation, a literal ⌘↵/↵, and ⌘↵-new-thread.**
   `st.text_input` commits its value on Enter *or* blur with no public way
   to tell those two apart — so "Enter opens the top result" can't be
   built as a real global keyboard behavior without a custom component
   (ruled out: "stays Streamlit, no React"). Search still works — typing
   then pressing Enter (or clicking away) reruns and filters the grouped
   list live, first result visibly bolded — but opening the top result is
   an explicit, honestly-labeled **button** rather than an intercepted
   keypress. ↑/↓ and ⌘↵ are click/tap only. Logged in design/DECISIONS.md.

`Esc` closing and focus restoring on close are `st.dialog`'s own native
behavior — free, not reimplemented here.
"""
from __future__ import annotations

import streamlit as st

from src.config.issuer_registry import SEED_ISSUERS
from src.logic.theme_taxonomy import company_counts_by_theme, theme_display_name
from src.ui import theme
from src.ui.ui import get_page

PALETTE_TRIGGER_KEY = "cmdk-trigger"


def render_palette_trigger(compact: bool = False) -> None:
    """Search trigger — the same button/⌘K-dialog/keyboard-shortcut
    mechanism either way; `compact` only changes its own rendering, never
    what it does. Default (compact=False): a full-width "Search… ⌘K"
    button, used wherever the trigger has room to spell itself out.
    compact=True (sidebar top-right, beside the brand mark — application-
    shell dark/dim pass): a small icon-only button using Streamlit's
    built-in Material Symbols shorthand (`:material/search:`, no custom
    SVG/asset needed), with `help` supplying the accessible tooltip/label
    an icon-only control needs (the plus 'Search (⌘K)' hint an icon alone
    can't convey) — assets/styles.css's own `.er-rail-brand [class*=...]`
    rule then constrains it to a small square via the same
    `st-key-cta-secondary-{PALETTE_TRIGGER_KEY}` container class either
    rendering already carries."""
    label = ":material/search:" if compact else "Ticker or company    ⌘K"
    help_text = "Search (⌘K)" if compact else None
    with st.container(key=f"cta-secondary-{PALETTE_TRIGGER_KEY}"):
        if st.button(label, key=PALETTE_TRIGGER_KEY, width="stretch", help=help_text):
            _open_palette()

    st.iframe(
        f"""
        <script>
        (function() {{
            var doc = window.parent.document;
            if (doc.__eevaCmdKBound) return;
            doc.__eevaCmdKBound = true;
            doc.addEventListener('keydown', function(e) {{
                if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {{
                    var btn = doc.querySelector('.st-key-{PALETTE_TRIGGER_KEY} button');
                    if (btn) {{ e.preventDefault(); btn.click(); }}
                }}
            }});
        }})();
        </script>
        """,
        height=1,
    )


def _index(ctx) -> list[dict]:
    items: list[dict] = []
    # Companies first (UI redesign item 1: "⌘K/search by ticker or
    # company"). Static registry read — no repository, no connection, so
    # this group costs the dialog nothing. The destination is the
    # Companies index, not a per-company page: those are item 3, and
    # routing a result at a page that does not exist yet would be a dead
    # end. `sub` carries the ticker so a reader typing "NVDA" sees why the
    # row matched; _matches() searches it.
    for issuer in SEED_ISSUERS:
        ticker = issuer.primary_ticker or ""
        exchange = issuer.primary_exchange or ""
        items.append({
            "group": "Companies",
            "label": issuer.legal_name,
            # The native name joins `sub` so a search in Japanese finds
            # the company too, for the 13 seed issuers that have one. It
            # is never synthesised — see src/ui/pages/companies.py.
            "sub": " · ".join(p for p in (ticker, exchange, issuer.native_name) if p),
            "go": "companies",
        })
    # Themes — the content-tag taxonomy, from the issuer registry.
    #
    # This group used to read ctx.theme_repository, which is
    # DemoThemeRepository (src/data_access/container.py): DEMO DATA,
    # routed at the Research Theses page, which has nothing to do with
    # it. A reader searching "memory" was being offered a demo row
    # dressed as a research thesis. It now reads the real taxonomy and
    # routes at the real Themes index (src/ui/pages/theme_index.py), so
    # the label, the data and the destination finally agree — and the
    # palette no longer depends on demo data at all.
    #
    # Published Research Theses are deliberately NOT indexed here yet:
    # that needs backend_factory.get_theme_repository(), a repository
    # read inside the dialog, and it is a new capability rather than
    # part of "search by ticker or company".
    for slug, count in company_counts_by_theme().items():
        items.append({
            "group": "Themes", "label": theme_display_name(slug),
            "sub": f"{count} companies", "go": "theme_index",
        })
    for s in ctx.signal_repository.get_all_signals():
        # Product-naming separation (design/DECISIONS.md): "Signals" is
        # now the Daily News rework's own group; these are Radar-derived
        # filing signals, still routed to the unchanged "signals" page/
        # route key — only the group label moved.
        items.append({"group": "Radar Signals", "label": s.title, "sub": s.theme_slug, "go": "signals"})
    # Filings (radar_inbox) left the sidebar in UI redesign item 1 and is
    # not merged into the Feed until item 2, so this action is the one
    # discoverable path to it in the meantime. Remove it once the Feed
    # actually carries filings; until then it must stay.
    items.append({"group": "Actions", "label": "Open Filings", "sub": "Radar filing list", "go": "radar_inbox"})
    items.append({"group": "Actions", "label": "Open Methodology", "sub": "", "go": "methodology"})
    current = theme.current_preference()
    for preference, label in theme.LABELS.items():
        items.append({
            "group": "Switch theme", "label": f"Switch theme: {label}",
            "sub": "Current" if preference == current else "", "go": f"theme:{preference}",
        })
    return items


def _matches(item: dict, query: str) -> bool:
    q = query.lower()
    return q in item["label"].lower() or q in item["sub"].lower() or q in item["group"].lower()


_PER_GROUP_LIMIT = 8


def _capped_by_group(items: list[dict]) -> list[dict]:
    """`items` truncated to _PER_GROUP_LIMIT rows per group, preserving
    the caller's order, with a non-clickable "+N more" marker appended to
    any group that was cut. Theme-switch commands (`go` starting
    "theme:") are never capped — they are commands, not search results,
    and must stay reachable however many companies match."""
    out: list[dict] = []
    counts: dict[str, int] = {}
    held: dict[str, int] = {}
    for item in items:
        if item["go"].startswith("theme:"):
            out.append(item)
            continue
        group = item["group"]
        counts[group] = counts.get(group, 0) + 1
        if counts[group] <= _PER_GROUP_LIMIT:
            out.append(item)
        else:
            held[group] = held.get(group, 0) + 1
    if not held:
        return out
    final: list[dict] = []
    for n, item in enumerate(out):
        final.append(item)
        group = item["group"]
        is_last_of_group = n + 1 == len(out) or out[n + 1]["group"] != group
        if is_last_of_group and held.get(group):
            final.append({"group": group, "label": "", "sub": f"+{held[group]} more — narrow the search", "go": ""})
    return final


def _open_palette() -> None:
    from src.data_access.container import get_repositories

    @st.dialog("Search", width="large")
    def _dialog() -> None:
        ctx = get_repositories()
        # Plain (non-form) text_input — Streamlit commits its value on
        # Enter *or* blur, with no public way to tell those apart, so a
        # literal "Enter opens the top result" can't be built without a
        # custom component (see module docstring). The button below is an
        # explicit, honestly-labeled action instead of overloading Enter.
        query = st.text_input(
            "Search", placeholder="Ticker, company, thesis or signal…",
            label_visibility="collapsed", key="cmdk-query",
        )
        items = _index(ctx)
        shown = [i for i in items if _matches(i, query)] if query else items

        with st.container(key="cta-secondary-cmdk-open-top"):
            open_top = st.button("Open top result ↵", key="cmdk-open-top", width="stretch", disabled=not shown)
        if open_top and shown:
            _navigate(shown[0])
            return

        if not shown:
            st.markdown('<div class="er-muted">No matches. Try a ticker, a company or a thesis.</div>', unsafe_allow_html=True)
            return

        last_group = None
        # Capped PER GROUP, not as one flat slice. The Companies group
        # added in UI redesign item 1 is 129 rows on its own — a flat
        # shown[:25] would have filled the whole dialog with companies
        # and pushed Research Theses, Radar Signals and every Action out
        # of sight whenever the query was empty or broad. Each group now
        # shows its first _PER_GROUP_LIMIT matches with a count of what
        # it is holding back, so every group stays visible and a reader
        # who wants more narrows the query. The theme commands are
        # exempt and always render.
        visible = _capped_by_group(shown)
        for n, item in enumerate(visible):
            if item["group"] != last_group:
                st.markdown(f'<div class="er-section-label" style="margin-top:0.75rem;">{item["group"]}</div>', unsafe_allow_html=True)
                last_group = item["group"]
            # The "+N more" marker _capped_by_group() appends carries no
            # label and no destination: it renders as muted text, never
            # as a button that would look clickable and go nowhere.
            if not item["go"]:
                st.markdown(f'<div class="er-muted" style="font-size:11px;">{item["sub"]}</div>', unsafe_allow_html=True)
                continue
            row_cols = st.columns([5, 2])
            with row_cols[0]:
                label = f"**{item['label']}**" if n == 0 else item["label"]
                if st.button(label, key=f"cmdk-result-{n}", width="stretch"):
                    _navigate(item)
            with row_cols[1]:
                # er-cmdk-sub, not an inline padding: the row is compact
                # now (assets/styles.css), and the 0.4rem top padding
                # this used to carry was sized for the old full-height
                # button, leaving the ticker floating below its name.
                st.markdown(f'<div class="er-cmdk-sub">{item["sub"]}</div>', unsafe_allow_html=True)

        st.markdown(
            '<div class="er-muted" style="margin-top:0.75rem; font-size:11px;">'
            "Search commits on Enter or click-away · \"Open top result\" opens the first match · "
            "click any row to open it directly · Esc closes</div>",
            unsafe_allow_html=True,
        )

    _dialog()


def _navigate(item: dict) -> None:
    if item["go"].startswith("theme:"):
        # Applies on the rerun (which also closes the dialog): the new
        # token CSS, data-theme stamp and native-widget theme.
        if theme.set_preference(item["go"].removeprefix("theme:")):
            st.rerun()
        return
    page = get_page(item["go"])
    if page is None:
        return
    st.switch_page(page)
