# Daily News Source Admission Policy

This document describes the typed, data-driven source registry in
`src/data_access/daily_news/source_registry.py`, and the admission and
polling rules that govern it. It began as a foundation only; the
registry is now the live source of truth for both Daily News lanes —
see "Wiring status" below for what is wired and what still isn't.

## Source categories

| Category | Meaning |
|---|---|
| `official_ir` | An issuer's own investor-relations press-release/news feed |
| `official_newsroom` | An issuer's own newsroom/PR feed (distinct hostname pattern from IR, same issuer) |
| `official_filing` | A regulatory filing feed/listing tied to a specific issuer (e.g. an issuer's own EDGAR/EDINET/DART filing stream) |
| `regulator_exchange` | A market-wide, issuer-agnostic regulator or exchange source (e.g. an exchange's own disclosure feed) |
| `independent_news` | Third-party journalism/reporting — the only category requiring explicit allowlisting (see below) |

## Source formats

| Format | Meaning |
|---|---|
| `rss_atom` | RSS 2.0 or Atom — the only format `rss_atom_client.py` currently knows how to fetch/parse |
| `official_api` | A structured official API response (not yet implemented by any fetch client) |
| `official_html_listing` | A static/paginated official HTML listing page (not yet implemented) |
| `licensed_feed` | A feed made available under an explicit commercial/licensing agreement (not yet implemented) |

Only `rss_atom` has a real fetch/parse implementation today
(`rss_atom_client.py`). The other three formats exist so the registry's
own shape doesn't need to change again when a later, separately-approved
slice adds a fetch client for one of them — admitting an entry with one
of those formats today is legal (it can be validated and reviewed) but
inert (nothing in the current pipeline can act on it).

## Required metadata (every entry)

`source_id`, `category`, `format`, `canonical_url` (HTTPS), `domains`
(the item-link allowlist), `jurisdiction`, `enabled`, `health_state`,
`attribution_label`, `licensing_classification`, `priority`, and exactly
one of `issuer_name` / `issuer_agnostic=True`. `last_verified_at` and
`allowed_event_filters` are optional. See
`source_registry.DailyNewsSourceEntry`'s own field-level docstrings for
the exact type of each.

## Validation rules

- `category`/`format`/`health_state` must be a real, supported enum
  value — an unrecognized string is rejected, never silently coerced.
- `canonical_url` must be a non-empty `https://` URL.
- `domains` must be non-empty, and no domain may be a known social-media
  domain (see "Explicit exclusions" below).
- Exactly one of `issuer_name` / `issuer_agnostic=True` must be set —
  never both, never neither. This makes "this source is intentionally
  not tied to one issuer" an explicit decision, never an accidental
  default.
- An `official_ir`/`official_newsroom`/`official_filing` entry whose
  `issuer_name` is set must resolve via the existing
  `feed_registry.tracked_company_for()` lookup — the exact same
  two-source check (`tracked_companies.py`, then
  `issuer_registry.DISCOVERY_STUBS`) the real pipeline already performs
  today, so an entry that validates here is guaranteed resolvable by the
  real pipeline too.
- `licensing_classification` must be non-empty for every entry, and
  `independent_news` additionally requires `allowlisted=True` — neither
  is sufficient on its own for that category.
- A duplicate (same normalized `canonical_url` + issuer + `category`)
  is rejected at the registry level.

## Source-health/review lifecycle

```
pending_review → verified → (degraded ⇄ verified) → failing → retired
```

- **`pending_review`**: a newly-admitted entry. **Polled**, so that the
  real fetch this state exists to wait for can actually happen — this
  is the state's only documented exit (`pending_review → verified`
  requires a successful fetch), so excluding it from polling would make
  that exit unreachable. Never treated as a live, *trustworthy* source
  until it has actually been fetched and confirmed working — never
  guessed or assumed working from its URL shape alone. Polling is
  evaluation, not endorsement: it confers neither `verified` nor
  trusted status (only an explicit, reviewed registry edit changes a
  health state — nothing promotes, demotes, or re-enables an entry
  automatically), and it grants a `pending_review` source's items no
  standing of any kind in admission, materiality, or High Signal
  ranking. None of those gates read `health_state`; every item from a
  polled source passes through them identically.
- **`verified`**: independently confirmed working (a real fetch
  succeeded and returned parseable, on-domain content). `last_verified_at`
  should be set to the real timestamp of that confirmation — never a
  fabricated or estimated date.
- **`degraded`**: a previously-`verified` source with intermittent or
  partial fetch failures. Still polled; flagged for review.
- **`failing`**: a previously-`verified` source now consistently
  failing to fetch or parse. Excluded from active polling — enforced
  for the editorial lane; see "Polling eligibility" below.
- **`retired`**: terminal. Never polled again, but the entry stays in
  the registry (never deleted) for audit/provenance history — matching
  this project's existing "never silently lose a prior state" discipline
  (e.g. `NewsStateTransition`'s own append-only history).

## Polling eligibility

An entry may be fetched on a discovery run only if it is `enabled`, its
`format` is `rss_atom` (the one format a fetch client implements — see
"Source formats" above), and its `health_state` is **not** `failing` or
`retired`. `degraded` and `pending_review` are both polled, per the
lifecycle section above. `source_registry.is_eligible_for_polling()` is
the single predicate for this; `eligible_for_polling()` is its
order-preserving filter over a tuple of entries. Both are pure: an
ineligible entry is skipped, never modified and never removed from the
registry, so its provenance history survives in full.

**Editorial lane (issuer-agnostic).** Enforced inside
`editorial_pipeline.run_editorial_discovery()`, applied to whatever
`source_entries` that call was handed, before the fetch loop — so an
ineligible source costs exactly zero network calls, and the count of
entries skipped this way is reported as
`EditorialScanReport.sources_skipped_ineligible`. The function is the
single point every real caller passes through (the worker, both dry-run
harnesses, and `scripts/run_daily_news_discovery.py`, which passes no
`source_entries` at all and so relies on this function's own
`EDITORIAL_SOURCE_REGISTRY` default), which is why the gate lives there
rather than in the worker's source assembly: filtering the assembly
alone would leave the other callers able to poll a disabled or retired
entry. Gated-source allow-lists are unaffected and still apply — they
decide which entries reach the call, this decides which of those may be
fetched, and neither can override the other.

**Issuer lane — not yet enforced.** `feed_registry.PILOT_FEEDS` filters
on `enabled` and `format` only, so a `failing` or `retired` *issuer*
entry would still be polled today. Deliberately unchanged by the
editorial-eligibility slice; closing it is a separate, separately
approved change, for which the shared predicate above is already
available in the module `feed_registry.py` imports.

## Attribution and licensing rules

- `attribution_label` is the human-readable publisher name shown for
  provenance/citation — required, non-empty, for every entry.
- `licensing_classification` states the actual usage rights this
  content is admitted under — required, non-empty, for every entry.
  Official company sources (IR/newsroom/filing) are classified as
  "official company source — public press-release content; headline,
  extractive excerpt, and direct link only," matching the project's
  existing no-full-article-reproduction policy
  (`summary_grounding.py`). An `independent_news` or `licensed_feed`
  entry must carry a classification that actually reflects its real
  licensing terms — never a copy-pasted official-source classification
  applied to different content by default.

## Explicit exclusions

- **No scraping of paywalled, access-controlled, blocked, or
  terms-restricted content.** This registry only ever describes a
  source's own official, publicly-served feed/API/listing — never a
  workaround for content the publisher has restricted.
- **No independent-news source without explicit allowlist +
  licensing metadata.** `allowlisted=True` and a non-empty
  `licensing_classification` are both mechanically required by
  `validate_source_entry()` — an independent-news entry missing either
  is rejected, not merely flagged.
- **No social-media domain**, ever, as a source domain: `twitter.com`,
  `x.com`, `reddit.com`, `facebook.com`, `instagram.com`,
  `linkedin.com`, `threads.net`, `tiktok.com`, `youtube.com`,
  `youtu.be`, `mastodon.social`, `bsky.app`, `substack.com`,
  `medium.com` are all hard-rejected by `validate_source_entry()`. This
  list is deliberately explicit and finite — a domain not on it is not
  thereby assumed safe; it simply isn't rejected by *this specific*
  check.
- **No SemiAnalysis, Citrini Research, or Serenity**, checked
  case-insensitively against `attribution_label`, `source_id`, and
  `issuer_name`. This is a name-based check, deliberately not a
  domain-based one — no domain for any of these three has been
  independently verified locally, and this project does not fabricate
  a domain it hasn't confirmed, even for an exclusion rule.

## Staged rollout (future, each stage separately approved)

1. **Official issuer feeds** (current state) — the 12 `PILOT_FEEDS`
   entries, all `official_ir`/`official_newsroom`, `rss_atom` format.
2. **Material filings** — `official_filing`-category entries linking
   already-tracked issuers' own regulatory filing streams (a natural
   extension once a fetch client for the relevant format exists).
3. **Regulator/exchange sources** — `regulator_exchange`-category,
   issuer-agnostic entries (e.g. an exchange's own market-wide
   disclosure feed).
4. **Vetted independent reporting** — `independent_news`-category
   entries, admitted only with explicit allowlisting and a real,
   verified licensing classification per source — never a blanket
   allowlist.

Each stage requires its own live-verification pass (real fetch,
confirmed on-domain content, confirmed licensing terms where
applicable) before any entry in it is marked `verified` — never
guessed, carried forward from memory, or assumed from a plausible URL
shape, matching this project's established discipline for every other
source registry (`tracked_companies.py`, `feed_registry.py`).

## Wiring status

`source_registry.py` is now imported by `feed_registry.py` (which
derives the live `PILOT_FEEDS` from `RUNTIME_SOURCE_REGISTRY` via
`to_daily_news_feed_source()`, as of source-expansion batch 1,
2026-09-04) and by `editorial_pipeline.py` (for the polling-eligibility
predicate above). It is still **not** imported directly by
`daily_news_pipeline.py`, `scripts/daily_news_worker.py`,
`src/ui/pages/daily_news.py`, or `src/ui/pages/daily_news_admin.py` —
enforced by `tests/test_daily_news_source_registry.py::
test_source_registry_module_is_not_imported_directly_by_the_real_pipeline_worker_or_ui`.
Those files reach the registry only through `feed_registry.PILOT_FEEDS`
and `editorial_pipeline`, and need no direct knowledge of this module.
