# Japan/Korea Gated Source Expansion

This document describes the gated Japan/Korea editorial source expansion
introduced alongside `GATED_JP_KR_SOURCE_REGISTRY` in
`src/data_access/daily_news/source_registry.py`. It is a sibling
mechanism to the existing Light Reading gated market-news expansion
(`GATED_MARKET_NEWS_SOURCE_REGISTRY` / `EDGE_DAILY_NEWS_ENABLED_SOURCES`)
— a separate registry, a separate allow-list flag, independently
controllable, never merged into the always-on `EDITORIAL_SOURCE_REGISTRY`.

## Read-only audit findings

Of the 8 JP/KR sources already in `EDITORIAL_SOURCE_REGISTRY`, a real
dry run (`run_editorial_discovery(dry_run=True)`, live network, no
writes) showed Japan's 3 sources (`japan-times-rss`,
`jpx-market-news-rss`, `fsa-japan-news-rss`) and `yonhap-news-rss`
producing **zero** admitted stories in a current sample despite fetching
successfully — technically live but effectively inert. Korea's 4
sources (`korea-herald-business-rss`, `korea-times-rss`,
`korea-it-times-rss`, `thelec-rss`) contributed real admitted content.
This is the gap this expansion targets, particularly for Japan.

## Approved sources

| Source ID | Jurisdiction | Health state | Notes |
|---|---|---|---|
| `businesskorea-industries-rss` | South Korea | `VERIFIED` | Business Korea, Industries section |
| `businesskorea-science-tech-rss` | South Korea | `VERIFIED` | Business Korea, Science & Technology section |
| `japan-times-business-rss` | Japan | `PENDING_REVIEW` | See "Known issue" below — **not recommended for activation** |

Both Business Korea feeds are permissively licensed for syndicated
headline/excerpt/link use, on-domain, and carry genuinely
research-worthy corporate/market content (earnings, capex, supply
chain, export-control policy) rather than consumer deals or lifestyle
content — confirmed via representative real headlines such as "Samsung
Unveils Processing DRAM as HBM Alternative" and "SK Hynix Faces New US
Export Control Review on HBM Chips."

## Known issue: `japan-times-business-rss`

Browser-context verification showed this feed live (HTTP 200,
`application/rss+xml`, 20 dated items, all on-domain, on-theme). A real
dry run using the worker's own fetch signature (`requests` +
`User-Agent: EevaResearch-DailyNews/1.0`, the same client
`rss_atom_client.py` uses in production) recorded `HTTPError:403` for
this exact URL — reproduced directly and deterministically outside the
dry run as well. The already-registered general `japan-times-rss`
(`/feed/`, same publisher/robots.txt) returns 200 under the identical
header, so this 403 is specific to the `/business/` path, not a general
Japan Times block. The entry is registered as `health_state=
PENDING_REVIEW` (not `VERIFIED`) so it is never treated as fetchable
until re-confirmed working, and a real network test
(`test_japan_times_business_real_worker_fetch_signature_returns_403`)
documents and guards this finding.

Candidates checked and rejected for Japan (kept out of the registry
entirely): METI, NHK World, JETRO, Kyodo News, Yomiuri's Japan News,
SEMI, Japan Today — no live/legal/on-theme feed found for any of them
in this pass. Revisit only if a working feed becomes available for one
of these, or if the Japan Times `/business/` 403 is resolved (e.g. a
different endpoint, or direct publisher contact).

## Activation

Both flags default to empty (fully dormant — zero network calls):

```
EDGE_DAILY_NEWS_ENABLED_SOURCES_JP_KR=   # unset or empty = dormant
```

To enable a source once approved, set a comma-separated list of source
IDs, e.g. `EDGE_DAILY_NEWS_ENABLED_SOURCES_JP_KR=businesskorea-industries-rss,businesskorea-science-tech-rss`.
`japan-times-business-rss` should not be added to this list until its
`PENDING_REVIEW` state is resolved.

## Running the dry run

```
.venv/bin/python -m scripts.daily_news_jp_kr_dry_run [--source <id>]
```

Read-only: runs the full fetch → match → materiality → admission
pipeline against all `GATED_JP_KR_SOURCE_REGISTRY` entries (or a single
named one), against a temporary cache directory, and prints
fetched/admitted/rejected-by-reason counts plus example titles and
reasons. No database writes.
