# EevaResearch — UI restructure brief

**Reference prototype:** `design/eevaresearch-prototype.html` — a complete clickable prototype of every screen. Open it, click through it, and treat it as the source of truth for structure, tokens, and component anatomy. It is a wireframe, not a pixel spec.

---

## 0. Before you change anything

Read the whole codebase and map the current page structure. Show me the file map and your plan. Do not edit until I confirm.

## 1. What this app is

EevaResearch is an evidence-first thematic market-intelligence workspace covering five themes: **AI Buildout, Humanoids, Space, Memory, Photonics**. It tracks companies, supply chains, catalysts, bottlenecks, and capital flows.

It is not a stock chatbot and makes no buy/sell calls. Its job is to move a research analyst from a broad narrative ("AI spending is rising") to sharper questions: which supply-chain layer benefits, where the bottleneck sits, who has direct versus second-order exposure, whether leadership is broad or concentrated, and what would invalidate the thesis.

The primary user opens this daily to answer one question: **what changed since I last looked?**

Signals are extracted from primary filings across EDGAR, TDnet, DART, CNINFO, and HKEX — including non-English documents that English-language coverage misses. That is the product's edge, and the UI should make it visible.

## 2. What's wrong now

1. Home is a marketing landing page, not a workspace — the daily user gets nothing actionable above the fold.
2. Eight flat nav items with no hierarchy. The app describes a linear workflow but presents eight equal peers.
3. Overview, Signal Board, and Watchlists are one dataset with three filters, shipped as three products.
4. Capital Rotation is a lens, not a place — it answers a question about a theme you're already in.
5. No company page. Nowhere a company lives with its layer, exposure, filings, and catalysts.
6. Evidence separation (Fact / Interpretation / Inference / Uncertainty) is the core differentiator and is invisible in the UI.
7. Hero CTAs look identical to text inputs; two equal-weight primaries.
8. Watermark logo behind the hero, Streamlit chrome visible, a floating "Home" pill overlapping the logo, ~100px hero type against 16px body.

## 3. Non-goals

- Do not change data logic, API calls, scoring, or analysis code. Presentation and IA only.
- Stays Streamlit. No React.
- Do not delete existing marketing copy — relocate it to About.
- No gradients, glassmorphism, coloured glow, scroll animations, or animated backgrounds.

---

## 4. Information architecture

Move from a top nav to a **persistent left sidebar** (`st.sidebar`, always expanded).

**Sidebar, top to bottom:** logo + wordmark → search button showing the `⌘K` hint → primary nav (Dashboard · Themes · Signals · Research, with an unread badge on Signals) → **Watchlists** group listing the user's actual lists with counts → **Recent research** listing the last 5–8 threads by question text → pinned to the bottom, the data status chip and links to Methodology / Disclaimer / About.

**Routes:**

| Route | Absorbs | Notes |
|---|---|---|
| **Home** | Home | First visit only. No sidebar. Logo always returns here. |
| **Dashboard** | Overview | Default route on every subsequent load. |
| **Themes** | Themes + Capital Rotation | Theme detail has tabs: **Map** / **Rotation** / **Companies** / **Catalysts**. |
| **Signals** | Signal Board | Watchlists are sidebar entries filtering this same view. |
| **Research** | Research Chat | |
| **Company** | new | `/company/{ticker}`, not in nav — reached by clicking any ticker anywhere. |
| **Methodology / Disclaimer / About** | Methodology | Static pages from the sidebar footer. |

**Capital Rotation dissolves into two places:** cross-theme rotation is a Dashboard panel; within-theme rotation is the Rotation tab on each theme page.

**Company pages** hold: theme and supply-chain layer, direct vs. second-order exposure, KPI strip, recent signals, the user's thesis notes (why it's on the list, contrary evidence, invalidation criteria), and catalysts.

---

## 5. Design tokens

Consolidate all styling into one `assets/styles.css` loaded via `load_css()` in a shared `ui.py`. One `.streamlit/config.toml` theme block. No inline `<style>` blocks anywhere else.

```css
:root{
  --rail:#181818; --bg:#212121; --surface:#2A2A2A; --surface-2:#303030; --surface-3:#3A3A3A;
  --hairline:rgba(255,255,255,.08); --hairline-2:rgba(255,255,255,.14);
  --text:#ECECEC; --text-2:#B4B4B4; --text-3:#8F8F8F; --text-4:#6E6E6E;
  --invert-bg:#FFFFFF; --invert-fg:#0D0D0D; --glow:rgba(255,255,255,.20);
  --r-sm:8px; --r-md:12px; --r-lg:16px;
}
```

**No accent colour. No red or green anywhere, including price data.** Direction is carried by ▲/▼ glyphs plus text weight: positive is `--text`, negative is `--text-3`. This is deliberate — do not add colour back in.

**Typography — three faces, three jobs:**

- **Inter** — everything in the interface. Body 15px, `line-height:1.6`, `letter-spacing:-.006em`.
- **JetBrains Mono** — tickers, prices, dates, source names, timestamps only.
- **Source Serif 4** — **verbatim primary-source excerpts only.** Nothing else. Sans is the app talking; serif is the document talking. Ensure the stack falls through to a CJK-capable system face, because Source Serif 4 has no Japanese, Korean, or Chinese glyphs and those excerpts will render as boxes otherwise.

Do not use uppercase letterspaced mono for section headers — that reads as a Bloomberg terminal and fights the system. Section labels are 13px Inter in `--text-3`.

**Type scale:** 11 / 11.5 / 12.5 / 13.5 / 14.5 / 15 / 20 / 25px in the workspace; 40px only on the Home hero.

**Radii:** 16px panels, 12px small cards, 8px nav items, 999px buttons and chips.

**Borders:** avoid them. Separate surfaces by background value. Use `--hairline` only for dividers *inside* a panel. Panels get no outer border.

**Spacing:** follow the reference in nav and prose zones; tighten inside data panels. Sidebar nav items 8px vertical, panel headers 15/13, table rows 11–14px, section gaps 28px, main content max-width 920px with 32px gutters.

## 6. Buttons

One primary per screen. White fill, near-black text, full pill, **white glow** — it reads as a light source, not a neon outline, and introduces no accent colour.

```css
.btn-primary{
  background:var(--invert-bg); color:var(--invert-fg);
  box-shadow:0 0 0 1px rgba(255,255,255,.10), 0 0 22px var(--glow), 0 2px 10px rgba(0,0,0,.4);
  transition:box-shadow .22s ease, background .18s ease;
}
.btn-primary:hover{
  box-shadow:0 0 0 1px rgba(255,255,255,.22), 0 0 38px rgba(255,255,255,.34), 0 4px 16px rgba(0,0,0,.45);
}
.btn-primary:disabled{background:var(--surface-3); color:var(--text-4); box-shadow:none; cursor:not-allowed}
.btn:focus-visible{outline:2px solid #fff; outline-offset:3px}
```

No hover lift — the glow carries the state. **Glow appears in exactly three places app-wide:** primary button, leading theme's breadth bar, live status dot. **No button may resemble a text input.**

## 7. Evidence labels — the signature element

Four pill chips, used everywhere a claim appears. In a greyscale system, **contrast encodes confidence**.

| Label | Treatment | Meaning |
|---|---|---|
| **Fact** | White fill, near-black text | Source-backed; must link to the document |
| **Interpretation** | `rgba(255,255,255,.16)` fill, `--text` | A market read built on evidence shown above it |
| **Inference** | Transparent, solid `--hairline-2` outline | Reasoned but unproven |
| **Uncertainty** | Transparent, **dashed** outline, `--text-3` | An open question, named rather than smoothed over |

11px Inter, weight 500, full pill. **A Fact chip without a working source link is a bug — fail loudly in dev rather than render it unlinked.**

**The evidence spine.** In Research answers, each claim gets a 2px vertical rule in the left gutter. Segments abut to form one continuous line, so an answer's rigour has a visible shape before it's read. Grid is `2px | 96px | 1fr` — spine, right-aligned chip, body. **Segments must abut exactly; stray padding turns a continuous line into a dashed one and breaks the read.** Below 860px the chip column collapses and chips move inline; the spine stays.

Source excerpts render original-language text above the translation, one tone dimmer, ending in a `<cite>` with issuer · venue · date · document location.

## 8. Home

First visit only; Dashboard thereafter. No sidebar — single 900px column with a minimal top bar.

1. **Hero** — kicker, 40px headline *"Start with what was filed."*, lede paragraph, primary "Open Dashboard" plus ghost "What this tool won't do".
2. **How to use it** — six numbered steps in a 3-column grid.
3. **Every claim carries a label** — four chips rendered live with definitions.
4. **Five themes** — clickable row.
5. **What this tool does not do** — the limits section, as the closing block.

**Copy direction:** plain and specific. **No aspirational or ceremonial language** — no "Welcome, Researcher", no "begin the journey", no "unlock", "empower", "harness". The product's claim is rigour; quest-flavoured copy undercuts it.

**The four limits** (exact content in the prototype): it does not give financial advice; the conversational agent can be wrong; it does not replace the primary source; the data may be delayed or incomplete. Close with *"If that's the arrangement you want, the Dashboard is where the work starts."* and the primary button.

> **Note for the repo owner, not for implementation:** this wording is written for clarity, not legal sufficiency. If the tool is ever monetised — particularly if sold to funds — have a securities lawyer review it first.

## 9. Dashboard

1. **Page head** — "Dashboard" at 25px, summary line (`3 new signals · 3 themes moved · 2 watchlist alerts`), status chip.
2. **Theme health strip** — five cards: name, breadth (% above 50-day), 5-day change with ▲/▼, progress bar. Leading theme's bar is `--text` with glow; rest are `--text-4`.
3. **New signals** — see §10.
4. **Watchlist table** — company + ticker, last, 5-day, breadth rank. Tabular mono, right-aligned.
5. **Capital rotation** — diverging bars against a centre line, 5-day relative strength vs. the equal-weight universe. Bar lightness encodes rank.

## 10. Since your last visit

Persist `last_seen_at` per user, updated when Signals is opened.

- Unread signals: 5px white dot in the left margin, `--text` headline
- Read signals: `--text-2` — recessive but legible
- One divider between the groups: hairline with a centered mono label, *"You were last here Thursday, 8:14pm"*
- Sidebar badge counts unread only; panel header says *"3 unread"*, not a total
- Mark read on **drawer open**, not on hover

## 11. Signal drawer

Opens over the feed without navigating away. Renders all eight spec fields:

header (source · date · theme/subtheme, then headline) → three-across meta strip (Direction, Strength, Horizon) → What changed → Evidence (serif excerpt) → **Contrary evidence** on `rgba(255,255,255,.025)` → Validates if → Invalidates if → footer actions (Save to watchlist · Open filing · Ask about this).

**When contrary evidence is absent, render the section with "None recorded" rather than hiding it.** A hidden section reads as *no counter-argument exists*; "None recorded" reads as *nobody has looked yet*. Those are different claims and the second is usually the true one.

## 12. Command palette (⌘K / Ctrl+K)

Fuzzy search over companies (ticker + name + layer), themes, signals, watchlists, research threads, and actions. Opens over a dimmed canvas.

`⌘K` opens from anywhere · `Esc` closes and restores focus · `↑` `↓` move · `↵` opens · `⌘↵` opens in a new research thread. Results grouped by kind, first pre-selected, key hints in the footer. Typing `inp` should surface AXTI and the substrate signals.

Streamlit has no native palette — build it as a custom component or an HTML/JS overlay via `st.components.v1.html` with a callback into session state. **Test the real keyboard shortcut before settling for a fallback**; the iframe may swallow the listener. If it genuinely can't work, fall back to the sidebar search button showing the `⌘K` hint.

## 13. Data freshness — three states

| State | Treatment | Condition |
|---|---|---|
| **Live** | White dot, pulsing, `--surface-2` chip | Last fetch under 15 min |
| **Stale** | `--text-2` dot with static ring, `--surface-3` chip, inline **Retry** | Over 15 min |
| **Demo** | Grey dot, dashed transparent chip, no pulse | No live connection |

**Stale is the dangerous state** — connected but behind, indistinguishable from live under a binary indicator. Give it more weight than live, not less. **Every panel header carries its own timestamp**; a global one hides which panel went cold.

## 14. Empty and loading states

Empty states get a title, one line of guidance, and a primary action — never an apology.

- Empty watchlist → "Nothing here yet" / "Add companies from a theme map, a signal drawer, or by ticker." / **Add a company**
- No signals → "No new signals since Thursday" / "The feed checks EDGAR, TDnet, DART, CNINFO, and HKEX every 15 minutes." / **View all signals**
- No threads → "No research yet" / "Ask about a company, theme, filing, or market move." / **New thread**

Skeletons match real row geometry so nothing shifts when data lands. Subtle shimmer, 1.4s. Never a bare spinner where the content shape is known.

## 15. The watchlist enforces its own rule

Home states that nothing goes on a watchlist without a written invalidation condition. Make the interface enforce it.

Save dialog has two fields: watchlist selection, and **"What would invalidate this"** marked `required`. **The Save button is disabled until it has content.** Placeholder models the right specificity: *"Domestic China routing falls below 40% of revenue, or an export permit denial is disclosed."* Hint below: *"You'll be reminded of this the next time the position moves against you."* — then actually do it: surface the stored text on the watchlist row when that name moves against the thesis. A promise the interface doesn't keep is worse than no promise.

## 16. Motion and texture

Motion is only ever functional. Two instances:

1. **Breadth bars fill from zero on load**, staggered 40ms, `.9s cubic-bezier(.22,.9,.3,1)`. Use a `--w` custom property with `@keyframes fill{to{width:var(--w)}}`.
2. **The live status dot pulses; the demo dot does not.** 2.4s expanding-ring `box-shadow`.

**No animated background.** Instead, static film grain at 3% opacity over the canvas — material depth, no moving pixels:

```css
body::before{
  content:""; position:fixed; inset:0; pointer-events:none; z-index:9998; opacity:.03;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='3'/%3E%3C/filter%3E%3Crect width='140' height='140' filter='url(%23n)'/%3E%3C/svg%3E");
}
```

Wrap all motion in `@media (prefers-reduced-motion: reduce)`.

## 17. Logo, disclaimers, Streamlit hygiene

**Logo** — find the existing asset (`assets/`, `static/`, `img/`) and use it at 22px in the sidebar beside a 14.5px semibold wordmark. The prototype has a placeholder SVG. **If no suitable asset exists, tell me — do not invent one.** Remove the watermark logo behind the hero.

**Disclaimer placement** — four weights, not one:

| Where | Content |
|---|---|
| Home | The full four-item limits section |
| Research composer | Permanent 12px `--text-4` line: *"Answers can be wrong. Check the linked source before acting on anything."* |
| First Research session | One-time dismissible note above the thread; dismissal persists |
| Every page footer | "Disclaimer" link |

No banner on every page load — it trains people to dismiss without reading.

**Streamlit:**

```python
st.set_page_config(page_title="EevaResearch", layout="wide", initial_sidebar_state="expanded")
```

Hide `#MainMenu`, `header`, `footer`, `[data-testid="stToolbar"]`, `[data-testid="stDecoration"]`, `[data-testid="stStatusWidget"]`. Restyle `[data-testid="stSidebar"]` to `--rail`, remove its default border. Use `st.tabs` for theme sub-views and `@st.cache_data` with a sensible TTL.

---

## Acceptance checklist

**Structure**
- [ ] Sidebar with four nav items, watchlists, recent research, status chip, footer links
- [ ] Home renders on first visit only; Dashboard default thereafter; logo returns to Home
- [ ] Home has no sidebar and uses the same tokens as the workspace
- [ ] Company pages at `/company/{ticker}`; every ticker in the app links to one
- [ ] Capital Rotation appears as a Dashboard panel and a theme tab, never as a nav item
- [ ] Marketing copy relocated to About, not deleted

**Visual system**
- [ ] Zero accent colour; no red or green; all direction carries a ▲/▼ glyph
- [ ] Inter for UI, JetBrains Mono for data, Source Serif 4 for source excerpts only
- [ ] CJK fallback verified — Japanese and Korean excerpts render, no tofu boxes
- [ ] No uppercase letterspaced mono headers anywhere
- [ ] Panels have no outer border; separation is by surface value
- [ ] One white pill primary button per screen with glow, no hover lift
- [ ] Glow in exactly three places; no button resembles an input
- [ ] Real logo in the sidebar; watermark removed
- [ ] All colours from CSS variables; no hardcoded hex outside `styles.css`

**Evidence**
- [ ] Four chips render; every Fact chip has a working source link
- [ ] Spine renders with four distinct treatments and unbroken segments
- [ ] Spine survives below 860px; chip column collapses
- [ ] Excerpts show original language above translation with a full `<cite>`
- [ ] Contrary evidence renders "None recorded" rather than hiding when absent

**Behaviour**
- [ ] `⌘K` opens the palette; ↑↓ / ↵ / ⌘↵ / Esc all work; focus restored on close
- [ ] Unread dots and "last here" divider work; marked read on drawer open, not hover
- [ ] Signal drawer renders all eight fields
- [ ] Three freshness states; stale carries more weight than live and offers Retry
- [ ] Every panel header shows its own fetch timestamp
- [ ] Every empty state has a title, guidance, and an action
- [ ] Skeletons match real row geometry; no bare spinners
- [ ] Watchlist save disabled until an invalidation condition is written
- [ ] Stored invalidation text surfaces when a position moves against the thesis

**Quality floor**
- [ ] Streamlit toolbar, menu, footer hidden
- [ ] Motion limited to breadth bars and the live dot; film grain present; no animated background
- [ ] `prefers-reduced-motion` disables all motion
- [ ] Visible keyboard focus on every interactive element
- [ ] Layout holds at 1440px, 1280px, and 1024px

---

Work through sections 4 → 17 in order. After each, show me a screenshot and a short summary before moving on.
