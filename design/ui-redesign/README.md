# UI redesign mockups

Three reference mockups, as supplied 2026-11-27:

| file | screen |
|---|---|
| `main-dashboard.html` | **Today** — the dashboard |
| `feed.html` | **Feed** — Filings and Signals merged into one list |
| `company-nvidia.html` | **Company** — the NVIDIA page |

Static HTML with inline styles, exported from a design tool. Two
things to know before opening them:

- each references `./support.js`, which was not supplied, and the
  `<x-dc>` wrapper plus the `data-dc-script` block at the foot are
  that tool's own scaffolding. The pages render without it; only the
  accent-colour picker does not work.
- `main-dashboard.html` carries one unresolved template placeholder,
  `{{accent}}`, on the "Open full feed" button. Its default is the
  accent teal below.

## Design tokens

From the brief, confirmed against the mockups' own inline styles.

| token | value | used for |
|---|---|---|
| bg | `#0E1013` | page background |
| surface | `#121418` | cards, panels, rows |
| border | `#23272E` | card and row borders |
| text | `#E7E9EC` | body text |
| muted | `#9AA1AC` | secondary text |
| labels | `#848B97` | monospace labels, dates |
| accent | `#2DBFA8` | primary action, healthy coverage |
| High | `#F5B66A` | High-signal tier |
| Early | `#8DBDF5` | Early tier |

Fonts: **Inter** for UI, **JetBrains Mono** for labels, dates and
tickers, **Source Serif 4** for verbatim filing quotes.

The mockups also rely on six values the brief does not name. They are
recorded here because the implementation needs them and guessing
would drift:

| value | used for |
|---|---|
| `#0B0D10` | nav rail background |
| `#1A1E25` | active nav item |
| `#2A2F37` | input and pill borders |
| `#B8BEC7` | nav labels, inactive items |
| `#1D2127` | list dividers inside panels |
| `#4A505A` | inert segment of the thesis bar |

## Navigation

Identical across all three mockups: **Today · Feed · Companies ·
Themes · Theses · Coverage**, in a left rail above a `MY WATCHLIST`
block of starred companies. The search field sits at the top of the
rail with a `⌘K` hint.

## Two different things are called "watchlist"

The mockups settle this, and the distinction is the reason for the
rename:

- **`● EARLY`** in `#8DBDF5` is the TIER chip, on rows that are
  stored as `NewsMaterialityTier.WATCHLIST`.
- **"Watchlist only"** is a filter pill on the Feed, and
  `MY WATCHLIST` is the nav block — both meaning the user's own
  starred companies, which is a separate feature entirely.

The rename is **display-only**. The stored enum value and the Postgres
column stay `Watchlist`, so there is no migration and no rewrite of
the strings already written into `editorial_story_revisions`
(`tier:High Signal->Watchlist`). Renaming the stored value would
rewrite history that exists precisely to be immutable.
