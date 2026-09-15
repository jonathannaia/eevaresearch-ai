# Radar Filing Card — Cross-Market Title Clarity (Option A)

Scoped improvement batch for DART (Korea) and EDINET (Japan) filing
cards, chosen from the two options the cross-market Radar filings audit
proposed. Code and tests only — no schema change, no new field, no new
network call, no UI redesign.

## Audit findings

Read-only audit of `src/ui/components/radar_card.py`,
`src/logic/filing_display.py`, `src/data_access/dart/client.py`,
`src/data_access/edinet/client.py`, `src/data_access/dart/dart_rules.py`,
and `src/data_access/edinet/edinet_rules.py`, covering AI Buildout,
Memory, Humanoids, Space, and Photonics.

1. **Titles repeated the raw native form name, with no English event
   description.** `filing_display.display_title()` already gives EDGAR
   filings a deterministic, plain-English title via `_EDGAR_FORM_TITLES`
   (e.g. "Annual Report — Form 10-K") regardless of the SEC's own
   `primaryDocDescription`. DART and EDINET had no equivalent: the title
   was either a stored translation of the raw native title (when one
   exists) or the raw native title itself, verbatim — e.g. a real
   EDINET card previously showed only `自己株券買付状況報告書（法２４条
   の６第１項に基づくもの）` in native/Original mode, with no English
   description of what kind of filing it is, even though the pipeline's
   own rule engine (`dart_rules.py`/`edinet_rules.py`) had *already*
   independently classified the event category to decide whether to
   promote the filing to a candidate in the first place. This is the
   fixed issue — see "The fix" below.

2. **"Open original filing" links: DART is correct; EDINET's limitation
   is real, not a bug.** `DartClient.viewer_url()` builds a real, public,
   working per-filing viewer URL (`dart.fss.or.kr/dsaf001/main.do?rcpNo=
   ...`) — confirmed correct, no issue found. EDINET has no confirmed
   public per-document viewer URL (`EdinetClient.document_index_url()`
   points at a key-required API endpoint that returns HTTP 401 to a
   browser) — this is a genuine, documented external constraint, not an
   oversight, and the UI already handles it honestly:
   `src/logic/source_link.py`'s `public_source_url()` rewrites that
   key-required URL to the public EDINET portal root before it's ever
   rendered, and `radar_card.py` shows a distinct, correctly-labeled
   "Search original EDINET filing ↗" button with guidance text instead
   of a direct (and wrong) document link. No fix applied here — inventing
   an unconfirmed per-document EDINET URL pattern would violate this
   codebase's own "never guess a URL, always live-verify" discipline;
   the current honest fallback is the correct behavior given the real
   constraint.

3. **Translation controls are present for both providers, not missing.**
   The Original/English toggle (`radar_card.py`'s `_toggle_title_language`)
   is provider-symmetric — it reads `CandidateSignal.title_translation`/
   `excerpt_translation` identically for DART and EDINET. DART defaults
   to showing the translation; EDINET defaults to native/original. No
   translation-coverage gap was found in the live UI path — a separate,
   `FilingDerivedNewsCandidate` model with its own translation fields
   exists but is explicitly documented as an unused foundation model,
   not wired to any pipeline or UI, and out of scope here (no schema
   change, no new wiring to a dead model).

## The fix

`filing_display.display_title()` now prefixes a plain-English event-type
phrase onto the DART/EDINET title whenever this specific candidate's
`matched_rules` already establishes a known category — reusing data the
pipeline already computed and stored, never a new inference:

- **DART** categories come from `dart_rules.py`'s own
  `KOREAN_KEYWORD_LEXICON` (10 categories: earnings, guidance, capex/
  facility investment, supply/sales contract, equity/JV investment,
  financing, listing events, ownership changes, risk disclosures, rumor
  response). Every promoted DART candidate has at least one matched
  category by construction (a filing is never promoted to a candidate
  without one), so this applies to essentially all DART cards.
- **EDINET** categories come from `edinet_rules.py`'s own
  `DEFAULT_CODE_CATEGORY_MAP` (currently 3 live-verified triplets:
  annual securities report, share buyback status, extraordinary report).
  Phrasing reuses the exact official English document-type names already
  documented in that module's own comments — nothing invented here.

The original native/translated text is never dropped — the phrase is
prefixed onto it (`"{phrase} — {existing title}"`), collapsing to just
the phrase when the two are already identical (e.g. a stored translation
that already reads "Status Report of Purchase of Own Shares" is not
duplicated). A small coverage test
(`test_every_real_dart_lexicon_category_has_a_curated_display_title` /
`test_every_real_edinet_mapped_category_has_a_curated_display_title` in
`tests/test_filing_display.py`) asserts every real category in each
rules module has a curated phrase, so the two can never silently drift
apart as either lexicon grows.

## Before / after

| Provider | Before | After |
|---|---|---|
| EDINET (Shin-Etsu Chemical, share buyback) | `自己株券買付状況報告書（法２４条の６第１項に基づくもの）` | `Status Report of Purchase of Own Shares — 自己株券買付状況報告書（法２４条の６第１項に基づくもの）` |
| EDINET (ispace, extraordinary report) | `臨時報告書` | `Extraordinary Report — 臨時報告書` |
| DART (Samsung Electronics, earnings) | `실적 발표` | `Earnings or Results Report — 실적 발표` |
| DART (SK Hynix, facility investment) | `신규시설투자등 결정` | `Facility Investment — 신규시설투자등 결정` |

## Affected issuers / filing types

Every tracked EDINET issuer (18 companies across AI Buildout, Memory,
Humanoids, Space) and every tracked DART issuer (10 companies across AI
Buildout, Memory, Humanoids, Space) whose candidate matched a rule
category — in practice, essentially all DART candidates, and EDINET
candidates matching one of the 3 currently-mapped triplets. Photonics
has zero JP/KR-tracked issuers today (a pre-existing tracked-company
registry gap, out of scope for this batch — flagged separately, see the
note below).

## Cross-market Radar usefulness

Before this change, a reviewer scanning Radar cards across EDGAR, DART,
and EDINET saw a plain-English event description for every EDGAR filing
but had to either read raw Japanese/Korean or toggle to a translation
(when one existed) to understand what KIND of filing a DART/EDINET card
even was. This closes that specific gap for the categories the pipeline
already classifies — a DART/EDINET card's title now carries the same
"what is this filing" signal an EDGAR card already does, at a glance,
in both Original and English modes, without waiting on or depending on
the separate translation pipeline.

## Out of scope, flagged separately

While auditing EDINET admission, a real, current gap surfaced: **Kioxia**
— a major Japanese memory/flash company — is not a tracked company at
all (confirmed via `company_aliases.py`/`tracked_companies.py`), so its
real filings and news never reach Radar or Daily News regardless of any
change in this batch. This is a tracked-company-registry gap, not a
filing-card or Daily News source issue, and is outside every part of
this task (Option B — broadening issuer coverage — was explicitly not
chosen). Worth a separate, focused follow-up.
