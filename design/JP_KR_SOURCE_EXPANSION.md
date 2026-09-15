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

## Round 2 audit (2026-09-15) — KR + Light Reading rollout, deeper Japan search

Assumes the operator will set `EDGE_DAILY_NEWS_ENABLED_SOURCES=light-reading-rss`
and `EDGE_DAILY_NEWS_ENABLED_SOURCES_JP_KR=businesskorea-industries-rss,
businesskorea-science-tech-rss` on the Daily News worker. No registry or
flag default changed by this round — verification and further candidate
search only.

### Quality-review harness

`scripts/daily_news_quality_review.py` (read-only, no network, no
writes) classifies a pasted batch of real, already-published Daily News
titles/snippets — copied by hand from the live app after the rollout —
into HIGH VALUE / BORDERLINE / IRRELEVANT, reusing the exact same
matching/materiality/admission functions the production pipeline runs.
It also prints a rejection-reason histogram for spotting systematic
noise patterns across a batch. See the script's own docstring for the
input format and usage; run it against a real pasted sample once the
worker has been live for 48 hours.

A live production classification pass (real story titles from
app.eevaresearch.com) was intentionally NOT run this round — Part 1's
own guardrail is to wait for a human to paste real samples from the
live app rather than have this session pull production data directly.

### A confirmed noise pattern: personnel-appointment headlines

Constructing representative KR/Light Reading noise-shape fixtures (now
regression-tested in `tests/test_daily_news_quality_review.py`) surfaced
one concrete, currently-live gap: a personnel-appointment headline
naming a tracked company in the title (e.g. "Samsung Electronics
Appoints New Head of Memory Division") is admitted today as BORDERLINE
— `editorial_admission.py`'s identity check accepts title placement
alone as sufficient for a non-ambiguous company, with no distinction for
a personnel-only action. This is exactly the kind of noise pattern Part
1 asked to identify; no admission-rule change is made this round (the
task's own guardrail: propose tweaks from live data, don't act on a
single constructed example) — flagged here for the next live-data
review pass, where the harness's IRRELEVANT/BORDERLINE histogram can
show how frequently this pattern actually recurs in real KR/Light
Reading output before any rule change is proposed.

### Japan candidate search, round 2

Continuing from round 1's exhausted list (METI, NHK World, JETRO, Kyodo
News, Yomiuri, SEMI, Japan Today — all still rejected on recheck or
still unreachable). Five new candidates checked live this round:

| Candidate | Outcome |
|---|---|
| Nikkei Asia (`asia.nikkei.com/rss/feed/nar`) | **Rejected** — real RSS 2.0 feed, genuinely on-theme (semiconductor/AI/Asia business content), robots.txt does not technically disallow the feed path for an unlisted UA. But its robots.txt explicitly names and blocks `ClaudeBot`/`Claude-Web`/`Claude-SearchBot`/`Claude-User`/`anthropic-ai` (allowing only a narrow safe list of pages for those bots). This worker is Claude-built; using an unbranded custom User-Agent specifically to access content a publisher has robots.txt-blocked for Claude-branded crawlers would be the same kind of workaround the task's own guardrails forbid for Japan Times Business's 403, even though our literal UA string isn't in the named list. Not added. |
| The Mainichi (`mainichi.jp/rss/etc/english_latest.rss`) | **Rejected**, two independent reasons: (1) same explicit `ClaudeBot`/`anthropic-ai`/`Claude-Web`/`Claude-SearchBot`/`Claude-User` block in robots.txt (`Disallow: /` with only `/sp/` allowed) as Nikkei Asia, same concern. (2) Even setting that aside, this is the paper's only English feed (no dedicated business/tech section) — a live sample showed centenarians, ninja re-enactments, sumo, and haiku dominating, with only 2/20 items even touching business and none semiconductor/AI-specific — the same near-zero-yield general-news problem the existing `japan-times-rss`/`jpx-market-news-rss`/`fsa-japan-news-rss` sources already have. |
| Japan Times additional sections (`/tag/technology/feed/`, `/news/business/tech/feed/`, `/news/business/corporate/feed/`, `/asia-pacific/feed/`) | **Rejected** — all four return `HTTPError:403` under the real worker fetch signature, same as `/business/feed/`. No new working Japan Times path found. |
| JAXA (`global.jaxa.jp`) | **Inconclusive/rejected** — the English press subdomain returns `403` to every fetch attempt from this environment (including a full browser User-Agent), independent of the worker's own UA — plausibly a geo/IP-level block rather than a bot-UA check. Not verifiable as usable; not added without live confirmation. |
| Japan Industry News (`japan-industry-news.com`) | **Rejected** — domain does not resolve/respond from this environment (DNS/connection failure on both http and https). |

Note for comparison: `japan-times-rss`, `businesskorea.co.kr`, and
`koreaherald.com` (all already-approved sources) were checked for the
same named-crawler pattern and carry no such block — plain,
crawler-agnostic robots.txt. The Nikkei Asia/Mainichi finding is new
and specific to those two publishers, not a reason to revisit any
already-approved source.

**Result: 0 new Japan sources added this round**, same disappointing but
honest outcome as round 1. `japan-times-business-rss` remains
`PENDING_REVIEW`, unchanged, with no header tricks or workaround
attempted (per the task's own explicit instruction) — its 403 is still
unresolved. If a licensing conversation with Nikkei (whose block is a
named-crawler policy, not a hard technical wall) is ever worth pursuing,
that would need to happen at the business/licensing level, not by
routing around robots.txt with a different User-Agent string.

## Running the dry run

```
.venv/bin/python -m scripts.daily_news_jp_kr_dry_run [--source <id>]
```

Read-only: runs the full fetch → match → materiality → admission
pipeline against all `GATED_JP_KR_SOURCE_REGISTRY` entries (or a single
named one), against a temporary cache directory, and prints
fetched/admitted/rejected-by-reason counts plus example titles and
reasons. No database writes.
