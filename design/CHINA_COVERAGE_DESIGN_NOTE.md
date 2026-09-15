# China Coverage — Design Note (no implementation)

Brief design note only, per the cross-market expansion task's own
scope: what adding China Daily News/Radar coverage would require, and
how it would reuse the gated-source architecture already built for
Korea/Japan (`GATED_MARKET_NEWS_SOURCE_REGISTRY`,
`GATED_JP_KR_SOURCE_REGISTRY`) and the pattern that architecture itself
established. No CN source, adapter, registry, or flag is added by this
note — this is explicitly a "what it would take" record, not a plan
that's already been approved to build.

## What the existing pattern already gives CN for free

The gated-source architecture (env-flag allow-list, defaulting to empty
== fully dormant; a separate `tuple[DailyNewsSourceEntry, ...]` registry,
never merged into the always-on `EDITORIAL_SOURCE_REGISTRY`; a read-only
dry-run CLI; regression tests for admission/rejection shapes) is already
provider-agnostic — nothing about it is Japan/Korea-specific. A future
`GATED_CN_SOURCE_REGISTRY` + `EDGE_DAILY_NEWS_ENABLED_SOURCES_CN` would
follow the exact same shape as `jp_kr_sources.py`/
`GATED_JP_KR_SOURCE_REGISTRY`/`daily_news_jp_kr_dry_run.py`:
`jurisdiction="China"` on each `DailyNewsSourceEntry`, a new
`cn_sources.py` module with the same `enabled_gated_sources()` filter, a
`scripts/daily_news_cn_dry_run.py` mirroring the JP/KR one, and the
worker's editorial tick extended with one more `+ cn_sources.
enabled_gated_sources(worker_settings)` term — the same additive,
independently-controllable pattern already proven twice (Light Reading,
then JP/KR).

Radar's own filing-card infrastructure is also already provider-generic:
`filing_display.py`'s `display_title()`/`metadata_only_summary()`/
`official_filing_reference()` all branch on `filing.source_name`, the
same seam DART and EDINET already share with EDGAR. A future China
regulator/exchange filing source (see below) would add one more branch
alongside the existing three, not a parallel code path.

## What would be new, and needs real verification before any build starts

1. **Source types.** The Korea/Japan search this round found the
   cleanest wins were sector-specific trade press (TheElec, Korea IT
   Times, Business Korea) rather than general national newspapers
   (which carry a near-zero relevant-content yield, confirmed twice
   this session for `japan-times-rss`/`jpx-market-news-rss`/
   `fsa-japan-news-rss`/general Mainichi). For China, the equivalent
   targets would likely be English-language sector trade press
   (semiconductor/AI-infrastructure/industrial-policy focused) rather
   than state media general feeds or Western wire-service China
   desks (which carry their own separate licensing complexity — see
   below). Every candidate would need the same live verification this
   session applied to JP/KR: real feed fetch under the worker's own
   signature, `robots.txt` read directly (never assumed), explicit
   licensing classification.

2. **Licensing is the harder question for China specifically.** Major
   English-language China business coverage is dominated by outlets
   with restrictive syndication terms (Bloomberg, Reuters, the WSJ) or
   by state-affiliated outlets (Xinhua, Global Times, CGTN, China Daily)
   whose editorial content raises a different, non-technical question:
   whether syndicating state-affiliated-press headlines fits this
   product's "independent journalism" posture the same way Korea
   Herald/Business Korea/TheElec do. This needs an explicit policy
   decision before any candidate search, not a technical one — flagged
   here, not answered here.

3. **Regulator/exchange feeds.** China's rough analogs to
   EDINET/DART/JPX/FSA would be CSRC (China Securities Regulatory
   Commission) disclosure feeds and the Shanghai/Shenzhen Stock Exchange
   (SSE/SZSE) disclosure systems. Neither has been checked this round —
   unlike EDINET/DART, which this codebase already has live, verified,
   working adapters for, a CSRC/SSE/SZSE adapter would be new work from
   zero: no confirmed API, feed format, or access terms. This is
   materially more work than adding an editorial RSS source and would
   need its own audit pass (comparable in scope to the original DART/
   EDINET pilots), not a quick registry addition.

4. **Language/translation.** The existing `Translation`/
   `title_translation`/`excerpt_translation` machinery
   (`src/data_access/translation/translation_service.py`) is already
   language-agnostic in its data model — it stores `source_lang`/
   `target_lang` per translation, not a hardcoded ja/ko pair. Adding
   Simplified Chinese would need the underlying translation provider
   confirmed to support `zh` (not verified this round — out of scope),
   plus the same category-glossary treatment this batch just added for
   DART/EDINET titles (Part 4) once a real CSRC/SSE/SZSE rule-category
   taxonomy exists to draw from — that glossary work is naturally
   downstream of item 3 above, not something to build ahead of it.

## Sequencing, if this is picked up later

The natural order, given what's already proven vs. new: (a) an
editorial-only CN gated-source registry first (lowest risk, reuses 100%
of the existing pattern, no new adapter code) — but only after the
licensing-policy question in item 2 is resolved; (b) a CSRC/SSE/SZSE
filing adapter as a separate, later, materially larger effort, modeled
on the original DART/EDINET pilot process (gated live-verification
milestones, no guessed URLs/codes) rather than treated as a quick
registry addition.
