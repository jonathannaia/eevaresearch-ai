# Research Theses — Active/Dormant Indicator (Design Note)

Design artifact only — no code, no production data, no
`theme_workspace.py` changes. Documents how tags and "Live evidence"
work today, then proposes a small, code-only active/dormant indicator
and a few copy/UX refinements.

## How tags and "Live evidence" work today

Both features live in `src/logic/theme_evidence.py` (pure filtering/
sorting, no I/O) plus rendering in `src/ui/pages/themes_research.py`
(the one page that reads via `backend_factory`/`daily_news_backend`).

**Theme tags** (`theme_evidence.theme_tags_for_companies()`): a
`ResearchTheme` has no sector-tag field of its own — `ThemeCategory` is
a research-framing label ("Bottleneck"/"Demand shift"/"Second-order
effect"), not a sector. Tags are derived instead from the Thesis's own
curated company map: for every `ThemeCompanyMapEntry.company_name` that
exactly matches a real `TrackedCompany.name`, that company's own
`TrackedCompany.themes` tuple (e.g. `"memory"`, `"ai-buildout"`) is
unioned in, first-seen order, and rendered on the list card
(`_theme_tags_html()` in `themes_research.py`).

**Live evidence** (`theme_evidence.recent_filing_evidence()` /
`recent_daily_news_evidence()`, called from `_render_live_evidence()`):
on the detail view only, reads all `FilingEvent`s across DART/EDGAR/
EDINET (`backend_factory.get_filing_event_repository()`, one call per
source) and all Daily News items (`daily_news_backend.
get_daily_news_repository()` / `get_editorial_story_repository()`),
filters to an **exact** company-name match against the Thesis's own
`company_map`, sorts newest-first by each item's own stored date, and
shows the 5 most recent filings and 5 most recent news items — titles
and links only. `NewsStory` items are further gated to
`status == PUBLISHED`. Falls back to "No recent evidence yet." when a
Thesis has no mapped companies, or none have recent activity.

Both mechanisms already give us everything a dormancy signal needs: a
count of live evidence items and, implicitly, how recent the newest one
is — no new data source or matching logic required.

## Proposal: Active vs. Dormant indicator

**Definition** (code-only, computed at render time, never stored):

- **Active** — at least one live-evidence item (filing or news, either
  kind) with a date within the last **30 days**.
- **Dormant** — the Thesis has mapped companies, but no live-evidence
  item in the last 30 days (evidence may exist, just older).
- **No coverage yet** — the Thesis has no mapped companies at all, or
  none are real tracked companies (today's existing "No recent evidence
  yet." case) — kept distinct from "Dormant," since it's a different
  situation (nothing to match against, vs. matched but quiet).

30 days is a starting point, not a hard recommendation — it should
track whatever cadence the curator team considers "this Thesis is
still being actively corroborated." A shorter window (e.g. 14 days)
would flag more Theses as dormant sooner; a longer one (60–90 days)
suits a Thesis about a genuinely slow-moving structural bottleneck. The
right choice depends on how often DART/EDINET/Daily News actually
produce matching items for a typical Thesis's company set, which is
worth checking against a few real Theses before picking a number.

**Where it renders**: as a small `er-status-tag` chip next to the
existing category/status badges on the list card (`_render_card()`),
and again at the top of the detail view next to the
category/status/updated-at line. Reuses the existing badge system —
`er-tag-pos` (Active) / `er-tag-neutral` (Dormant) / `er-tag-neutral`
with muted styling (No coverage yet) — no new visual language.

**Implementation shape** (for a future code change, not this note): a
small pure function in `theme_evidence.py`, e.g.
`thesis_activity_status(filing_links, news_links, now, window_days=30)
-> Literal["active", "dormant", "no_coverage"]`, taking the same
`EvidenceLink` tuples `_render_live_evidence()` already computes —
no new query, no new field on `ResearchTheme`, no schema change. The
index view would need to compute this per card the same way it already
computes `evidence`/`company_map` counts today (an explicitly-accepted
tradeoff for this page, per its own docstring: "Themes are an
intentionally small, curated, low-volume set").

## Suggested copy/UX refinements (code-only)

- **List card**: alongside the Active/Dormant chip, a one-line
  timestamp — "Last live activity: 3 days ago" (Active) or "Last live
  activity: 47 days ago" (Dormant) — computed from the same evidence
  the indicator itself uses, via the existing `fmt_datetime_local`-style
  relative formatting already used elsewhere on this page.
- **Detail view**: when Dormant, a short, factual note directly above
  the "Live evidence" section — "No new filings or news matched to this
  thesis's companies in the last 30 days." — distinct from the existing
  "No recent evidence yet." (which covers zero evidence ever, not
  "went quiet"). Never a value judgment about whether the thesis itself
  is still correct — this is purely a coverage-recency signal, exactly
  like the existing "shown for context, not analyzed or scored"
  framing the Live evidence section already carries.
- **Sort order on the index page**: today `list_published_themes()`
  sorts by `(updated_at, id)` — the curator's own last-edited timestamp,
  unrelated to live activity. Worth considering a secondary sort
  (Active theses first, then Dormant, then No coverage yet, each
  group keeping today's `updated_at` order) so a reader scanning the
  list sees what's currently corroborated before what's gone quiet —
  though this changes today's ordering contract and would need its own
  explicit sign-off, not something to bundle silently into the
  indicator itself.
- **Keep it uncluttered**: exactly one new chip plus one new line of
  text per card/detail — no new section, no new page, no change to the
  existing curated Evidence ledger, company map, or any of the
  narrative sections. The whole point of "Active/Dormant" is a fast,
  low-ink signal, not a new dashboard.

## Explicitly out of scope for this note

- No change to `ResearchTheme`, `ThemeEvidenceItem`, or
  `ThemeCompanyMapEntry` — no new stored field.
- No change to `theme_workspace.py` or any curator/authoring path.
- No change to which items count as "recent" for the existing Live
  evidence section itself (still the 5-most-recent, unbounded by date).
- No proposal to hide or delist a Dormant thesis — dormancy is a
  display signal only, never an implicit archival mechanism (that
  remains `ThemeVisibility.ARCHIVED`, a deliberate curator action).
