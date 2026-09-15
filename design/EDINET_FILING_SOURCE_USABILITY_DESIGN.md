# EDINET Filing Source Usability — Design Proposal

Status: research/design only, no code changed. Scope: Dashboard and Radar card rendering of EDINET filings' "filing type" label and "source" link/fallback. Does not touch EDGAR, DART, ingestion, workers, sources, discovery settings, Render configuration, or production data.

## 1. The problem, grounded in the live example

**Issuer:** Shin-Etsu Chemical Co., Ltd. (EDINET code `E00776`, TSE securities code `4063`, stored internally as `krx_code="40630"` — [`tracked_companies.py:957-958`](../src/config/tracked_companies.py#L957-L958)).

**Current UI label:** "Special Report" — a bare, unqualified English string with no Japanese original alongside it and no indication of what kind of report this legally is.

**Current source link:** `https://disclosure2.edinet-fsa.go.jp/` — the EDINET portal *homepage*, identical for every EDINET filing shown anywhere in the app, never a link to this specific document.

Both defects trace to real, identifiable code, not a bug in the ordinary sense — they are the deliberate, documented consequence of two correct safety decisions (never link to a 401-guaranteed credentialed API URL; never fabricate a filing-type translation) applied without enough surrounding context to stay useful to a reader. Section 4 traces exactly how each symptom arises.

## 2. Files inspected

Ingestion / metadata / persistence:
- `src/data_access/edinet/client.py`
- `src/data_access/edinet/scan_service.py`
- `src/data_access/edinet/edinet_pipeline.py`
- `src/data_access/edinet/edinet_rules.py`
- `src/data_access/edinet/edinet_code_resolver.py`
- `src/data_access/edinet/document_service.py`, `discovery_service.py` (skimmed — independent, parallel `source_url` construction, same limitation)
- `src/models/models.py` (`FilingEvent`, `CandidateSignal`, `Signal`)
- `src/config/tracked_companies.py`

Rendering / link-building:
- `src/logic/source_link.py` (the one shared EDINET URL-safety rewrite)
- `src/logic/filing_display.py` (title/category-label logic, `official_filing_reference`)
- `src/logic/signal_promotion.py` (`FilingEvent`/`CandidateSignal` → `Signal`, feeds `cards.py`)
- `src/ui/components/radar_card.py` (Radar's one shared card)
- `src/ui/components/recently_updated.py`, `regional_brief.py`, `recent_theme_activity.py`, `cards.py` (Dashboard's four independent card surfaces — there is no single shared Dashboard card component)

## 3. What EDINET metadata is already available today

### 3.1 Raw fields the client/scan layer already knows about

`scan_service.py` normalizes each raw `documents.json` row against two field lists ([`scan_service.py:52`](../src/data_access/edinet/scan_service.py#L52), [`:64-69`](../src/data_access/edinet/scan_service.py#L64-L69)):

- **Required:** `docID`, `docTypeCode`, `ordinanceCode`, `formCode`, `filerName`, `docDescription`
- **Optional:** `secCode`, `edinetCode`, `JCN`, `submitDateTime`, `periodStart`, `periodEnd`, `opeDateTime`, `issuerEdinetCode`, `subjectEdinetCode`, `subsidiaryEdinetCode`, `parentDocID`, `withdrawalStatus`, `docInfoEditStatus`, `disclosureStatus`, `legalStatus`, `xbrlFlag`, `pdfFlag`, `attachDocFlag`, `englishDocFlag`, `csvFlag`

### 3.2 What survives onto `FilingEvent` ([`scan_service.py:338-368`](../src/data_access/edinet/scan_service.py#L338-L368), field meanings documented at [`models.py:337-425`](../src/models/models.py#L337-L425))

| `FilingEvent` field | Source | Notes |
|---|---|---|
| `rcept_no` | `docID` | The EDINET document ID, stored verbatim. Doubles as the dedup key. |
| `corp_code` | tracked company's `corp_code`, else `edinetCode` | The EDINET issuer code (e.g. `E00776`). |
| `stock_code` | tracked company's `krx_code`, else `secCode` | **5-character, zero-padded** (e.g. `"40630"`) — see §3.4. |
| `report_nm` | `docDescription`, else `filerName` | Native Japanese title. |
| `rcept_dt` | `submitDateTime`'s date part | Date only, `YYYY-MM-DD`. |
| `filed_at` | full `submitDateTime` | **Date + time**, ISO 8601 — see §3.3. |
| `pblntf_ty` | `formCode` | EDINET's own form code. |
| `pblntf_detail_ty` | `docTypeCode` | EDINET's own document-type code. |
| `ordinance_code` | `ordinanceCode` | The governing ordinance code. |
| `source_url` | `EdinetClient.document_index_url(docID)` | The **credentialed API URL**, not a public link — see §3.5. |

**Confirmed available:** EDINET document ID (`rcept_no`), EDINET code (`corp_code`), the 5-character securities code (`stock_code`), official `formCode`/`docTypeCode`/`ordinanceCode` triplet, submission date (`rcept_dt`) and full submission date+time (`filed_at`).

**Confirmed discarded, never stored anywhere:** `JCN`, `periodStart`/`periodEnd`, `opeDateTime`, `issuerEdinetCode`/`subjectEdinetCode`/`subsidiaryEdinetCode`, `parentDocID`, `legalStatus`, `xbrlFlag`/`pdfFlag`/`attachDocFlag`/`englishDocFlag`/`csvFlag`. None of these are needed for the fix proposed here; noted for completeness only.

### 3.3 Filed date *and* time — already captured, inconsistently surfaced

`FilingEvent.filed_at` ([`models.py:414-425`](../src/models/models.py#L414-L425)) holds the full timestamp via `_derive_filed_at()` ([`scan_service.py:314-335`](../src/data_access/edinet/scan_service.py#L314-L335)) — populated **only** for EDINET (EDGAR/DART's current endpoints carry no time component). It is `None`, never fabricated, whenever `submitDateTime` is missing or unparseable.

It is already used for sorting/display in `recently_updated.py` ([`:204-222`](../src/ui/components/recently_updated.py#L204-L222)), but **Radar's own card ignores it** — `radar_card.py`'s `_filed_label()` ([`:147-151`](../src/ui/components/radar_card.py#L147-L151)) reads only `rcept_dt` (date-only), so the time-of-day the app already has for every EDINET filing never reaches the Radar card today.

### 3.4 4-digit vs. 5-character securities code

Only the 5-character, EDINET-native, zero-padded form is stored (`tracked_companies.py`, e.g. Shin-Etsu `krx_code="40630"`, SoftBank `"99840"`, Kioxia `"285A0"`). The module's own docstring documents this is "EDINET's own 5-character source-native securities code... NOT the bare 4-character TSE code."

The padding rule is codified once, for lookup-matching only, in `edinet_code_resolver._normalize_lookup_code()` ([`:256-267`](../src/data_access/edinet/edinet_code_resolver.py#L256-L267)): a 4-character code normalizes to `<code>0` (confirmed live: `"9984"→"99840"`, `"285A"→"285A0"`). That function's own docstring explicitly scopes this to "a lookup-matching convenience only, not a general identifier transform" — nothing today reverses it for display, and nothing re-validates per-entry that the trailing character really is `"0"` before any hypothetical strip.

**Conclusion:** the 4-digit form is derivable by convention (strip a trailing `"0"` from a 5-character code) but is not yet a display-time utility, and the existing resolver code deliberately declines to be reused for that purpose. A new, narrowly-scoped, defensive display helper is needed (§5.1).

### 3.5 Is any EDINET document URL ever constructed? Yes — but not a working public one

`EdinetClient.document_index_url(doc_id)` ([`client.py:152-160`](../src/data_access/edinet/client.py#L152-L160)) builds `https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}` — this is stored verbatim as `FilingEvent.source_url`. Its own docstring states plainly: "no dedicated public 'viewer' page was confirmed" — this is the *authenticated* API endpoint, which returns HTTP 401 without the app's own `Subscription-Key` ([`source_link.py:3-11`](../src/logic/source_link.py#L3-L11)). `discovery_service.py:226` builds the identical URL independently for its own candidate stream — same limitation, no second, better construction exists anywhere.

`radar_card.py`'s own module docstring ([`:45-52`](../src/ui/components/radar_card.py#L45-L52)) records that this was already investigated live and confirmed closed: "EDINET has no working direct document link (disclosure2.edinet-fsa.go.jp's per-row PDF action is a session-bound JS postback, not a derivable URL)." This design does not re-open that investigation — it is treated as an established, correct finding (see §6 for why, and the one alternative considered and rejected).

## 4. How the two observed symptoms actually arise

### 4.1 The bare-homepage link

Two-stage, both deliberate: (1) the *stored* `source_url` is the credentialed API URL (§3.5); (2) every rendering surface passes it through the one shared safety rewrite, `source_link.public_source_url()` ([`:19-48`](../src/logic/source_link.py#L19-L48)):

```python
_EDINET_API_HOST_PREFIX = "https://api.edinet-fsa.go.jp/"
EDINET_PUBLIC_PORTAL_URL = "https://disclosure2.edinet-fsa.go.jp/"

def public_source_url(url):
    if url.lower().startswith(_EDINET_API_HOST_PREFIX):
        return EDINET_PUBLIC_PORTAL_URL
    return url
```

This is correct — the alternative is handing a browser a link that will 401 — but every one of the five card-rendering surfaces calls it and stops there. Only Radar's card (`radar_card.py`) adds anything past the rewritten link: a fixed guidance sentence, `_EDINET_LOOKUP_GUIDANCE` ([`:210-213`](../src/ui/components/radar_card.py#L210-L213)), the same 25 words for every EDINET filing regardless of company, code, date, or type — its own docstring ([`:222-238`](../src/ui/components/radar_card.py#L222-L238)) states plainly that `filing`/`filed_label` are accepted but "no longer read." The four Dashboard surfaces (`recently_updated.py`, `regional_brief.py`, `recent_theme_activity.py`, `cards.py`) render **no fallback text at all** — just a "View source ↗"-style link to the bare homepage, with nothing telling the reader what to search for once they get there.

### 4.2 The bare, unqualified "Special Report" label

Two independent, narrower mapping tables exist, and neither covers most EDINET filings:

- `edinet_rules.DEFAULT_CODE_CATEGORY_MAP` ([`:125-129`](../src/data_access/edinet/edinet_rules.py#L125-L129)) — exactly **three** live-verified `ordinanceCode:formCode:docTypeCode` triplets (`annual_securities_report`, `share_buyback_status`, `extraordinary_report`). The module's own docstring is explicit that real Japanese statutory document-type codes "must NOT be guessed into this module" — every entry requires independent live re-verification before being added.
- `filing_display._EDINET_CATEGORY_TITLES` ([`:104-108`](../src/logic/filing_display.py#L104-L108)) mirrors those same three slugs to English phrases, consumed only by `display_title()` ([`:162-201`](../src/logic/filing_display.py#L162-L201)) — itself called only from `radar_card.py`, not from any of the three Dashboard surfaces that render `filing.report_nm` directly and unmapped ([`recently_updated.py:252`](../src/ui/components/recently_updated.py#L252), [`regional_brief.py:75`](../src/ui/components/regional_brief.py#L75)).

For any filing whose triplet is outside those three — the overwhelming majority of EDINET's real document-type universe — `display_title()` falls straight back to `native_or_translated`: either the raw native `report_nm`, or (when a translation is stored and the caller's toggle prefers it) `candidate.title_translation.translated_text`, DeepL's machine translation of that raw title, shown **alone**, with no category framing and no native-language text alongside it. "Special Report" is consistent with exactly this path — a plain DeepL rendering of a `docDescription` string whose triplet is not one of the three curated entries, shown without its Japanese original.

## 5. Proposed narrow fix

Two decisions anchor everything below, both intentional constraints, not oversights:

- **Do not attempt a direct per-filing link.** §3.5/§6 — none is derivable; the current homepage fallback is the correct target, not the defect. The fix is entirely in what surrounds that link.
- **Do not add new entries to `DEFAULT_CODE_CATEGORY_MAP`/`_EDINET_CATEGORY_TITLES`.** That table's "live-verify every triplet" discipline is a real, deliberate quality bar this design should not route around by mass-adding unverified codes. Instead, the fix uses metadata **already captured and already trusted** for every filing regardless of triplet: the native title (`report_nm`, always present) and its stored DeepL translation (`title_translation`, present whenever translation succeeded) — shown **together**, never one replacing the other, and never a translation invented when none is stored.

### 5.1 New shared, pure-function helpers (`src/logic/filing_display.py`)

Four small, source-scoped functions, following the existing module's own discipline (pure, no I/O, no fabrication, degrade field-by-field rather than guess):

```python
def edinet_display_securities_code(stock_code: str) -> str:
    """5-character EDINET securities code -> the public 4-digit TSE code,
    by convention (see edinet_code_resolver._normalize_lookup_code's own
    docstring: 4-char -> "<code>0"). Only strips when len == 5 AND the
    trailing character is genuinely "0" -- otherwise returns the stored
    value unchanged, never a guessed truncation."""

def edinet_bilingual_type_label(filing: FilingEvent, candidate: CandidateSignal | None) -> str:
    """Native title, plus its plain-English translation in parentheses
    when one is already stored (candidate.title_translation) or a
    curated category phrase is already established (existing
    _EDINET_CATEGORY_TITLES/_mapped_category_title -- unchanged, reused
    as-is, no new entries). Native-only when no translation/category
    exists for this filing -- never fabricated."""

def edinet_filed_label(filing: FilingEvent) -> str | None:
    """Prefers filing.filed_at (date + time) over rcept_dt (date-only)
    when filed_at is present and parses; falls back to today's
    date-only behavior otherwise. None on total absence, same as today."""

def edinet_fallback_instruction(filing: FilingEvent, candidate: CandidateSignal | None) -> str | None:
    """Builds ONE filing-specific sentence from already-known fields --
    e.g. 'Shin-Etsu Chemical Co., Ltd. (securities code 4063) -- Status
    Report of Purchase of Own Shares, filed Sep 4, 2026. Search EDINET
    (disclosure2.edinet-fsa.go.jp) by company name or securities code,
    filtered to this date and filing type.' Omits any clause whose
    underlying field is empty (no code -> drop that clause; no type
    label -> drop that clause) rather than filling a placeholder. None
    only if corp_name itself is empty (never renders an empty sentence)."""
```

These satisfy the stated requirements directly:
- **Filing type:** native label always retained; plain-English form added only when already-trusted data supports it (translation or curated category) — never invented.
- **4-digit stock code:** `edinet_display_securities_code`, defensive by construction.
- **Document ID and submitted time:** already stored (`rcept_no`, `filed_at`); `edinet_filed_label` is the only new logic, and it is a preference order, not a new fetch.
- **Fallback instruction with company name, 4-digit code, filing date, and type:** `edinet_fallback_instruction`, replacing the one fixed generic sentence and the four surfaces that show none at all.
- **Never the homepage as the only "View source" target when precise metadata exists:** the fallback sentence is rendered next to the homepage link on every surface, carrying the metadata the bare link itself cannot.

### 5.2 Wiring (proposed, not implemented)

- **`radar_card.py`**: `_edinet_locator_line()` calls `edinet_fallback_instruction()` instead of returning the fixed `_EDINET_LOOKUP_GUIDANCE` string; `_filed_label()` (or its EDINET call site) prefers `edinet_filed_label()`; `official_filing_reference()`'s EDINET branch renders `edinet_display_securities_code(filing.stock_code)` instead of the raw padded value; the card's title area (already calling `filing_display.display_title`) is unchanged in its toggle behavior, but the new `edinet_bilingual_type_label` is additionally available for the reference-line context where a toggle doesn't apply.
- **`recently_updated.py`, `cards.py`** (both already load `CandidateSignal`, confirmed §7): add the same small `if filing.source_name == "EDINET":` conditional block already used in `radar_card.py`, rendering the fallback sentence next to the existing unchanged "View source" link, and using `edinet_bilingual_type_label`/`edinet_display_securities_code` wherever they currently show `filing.report_nm`/`filing.stock_code` raw.
- **`regional_brief.py`, `recent_theme_activity.py`** (load `FilingEvent` only — see the gap in §7): gain the fallback sentence and the 4-digit code and the document-ID/filed-time display (all derivable from `FilingEvent` alone), but **not** the bilingual type label, since these two surfaces have no `CandidateSignal`/`title_translation` access today. Title stays native-only here, exactly as now — a real, named limitation, not silently worked around (§7).
- **EDGAR and DART**: no call sites change. `public_source_url()`, `EdgarClient.filing_index_url`, `DartClient.viewer_url`, `_EDGAR_FORM_TITLES`, `_DART_CATEGORY_TITLES`, and every existing EDGAR/DART branch in all five rendering surfaces are read, not modified, by this design.

## 6. Alternative considered and rejected: a server-side document proxy

A route that holds the `Subscription-Key` server-side, calls `EdinetClient.fetch_document()`, and streams the PDF back to the browser would produce a genuine, working direct link. Rejected for this design: it is not a display/usability change but a new backend capability (a new public-facing route, credential handling in a request path, proxy/caching/error-handling of binary EDINET responses) — categorically outside "the narrowest robust fix" this task calls for, and outside the explicit guardrail against source/worker/deployment changes. Worth recording as a legitimate future-phase idea if direct linking is ever required, not attempted here.

## 7. Exact data gaps and unresolved issues

1. **`regional_brief.py`/`recent_theme_activity.py` cannot show a bilingual type label without a repository change.** Both call `backend_factory.get_filing_event_repository(...).load_filing_events()` directly ([`regional_brief.py:46`](../src/ui/components/regional_brief.py#L46), confirmed identically in `recent_theme_activity.py`), never `get_candidate_repository`, so neither has access to `CandidateSignal.title_translation`. Two real options, each with a real tradeoff, deliberately left unresolved here:
   - Switch these two surfaces to read candidates instead of filings — but `FilingEvent`s exist for filings that were never promoted to a `CandidateSignal` at all (routine filings still ingest unconditionally — the "always ingest, selectively promote" contract); switching would silently drop those from these two Dashboard views, a behavior change beyond this task's scope.
   - Read the translation cache directly by `doc_id` as a side-channel, bypassing `CandidateSignal` — narrower, but introduces a new, direct dependency from a Dashboard component on the translation cache's on-disk shape, which today only `edinet_pipeline.py`/`radar_pipeline.py` know about.
   This design deliberately leaves the type label native-only on these two surfaces rather than picking one.
2. **The 5th-character-is-always-"0" convention is asserted, not runtime-verified, per entry.** `edinet_code_resolver.py`'s own docstring calls the 4↔5-character mapping "a lookup-matching convenience only, not a general identifier transform." `edinet_display_securities_code()` is specified defensively (§5.1) precisely because of this — it must degrade to the raw 5-character value for any entry that doesn't actually end in `"0"`, rather than trust the convention unconditionally. No currently-tracked EDINET company's code is known to violate it, but nothing asserts that going forward.
3. **No confirmed public per-document EDINET URL exists** (§3.5/§6) — already investigated live in this codebase; not re-opened by this design. If EDINET ever documents or exposes one (e.g. in a future API version), the fix collapses to changing `EdinetClient.document_index_url()`'s return value alone; everything else in this design (label, code, ID, time, fallback) stays useful regardless.
4. **`Signal.related_tickers`/`exchange_symbol`** (`signal_promotion.py`) also store the raw 5-character `stock_code` — any surface reading those fields for EDINET inherits the same 5-vs-4-digit issue and would need the same `edinet_display_securities_code()` treatment at render time.

## 8. Proposed changed-file list (for a later implementation phase — not made here)

- `src/logic/filing_display.py` — add the four helpers in §5.1 (additive; no existing function signature changes).
- `src/ui/components/radar_card.py` — wire `_edinet_locator_line`, `_filed_label`'s EDINET path, and `official_filing_reference`'s EDINET securities-code line to the new helpers.
- `src/ui/components/recently_updated.py` — add the EDINET fallback block and bilingual/4-digit display.
- `src/ui/components/cards.py` — same, for `signal_card`/`priority_signal_row`'s EDINET branch.
- `src/ui/components/regional_brief.py` — add the EDINET fallback block, document ID, filed time, and 4-digit code (native-only title — see gap 1).
- `src/ui/components/recent_theme_activity.py` — same as `regional_brief.py`.
- `tests/test_filing_display.py` (existing file, per the earlier cross-market title-clarity pass's own coverage-test precedent) — new unit tests for the four helpers, including the defensive non-"0"-suffix case.
- New/updated card-rendering tests for each of the five wiring sites, verifying EDGAR/DART output is byte-for-byte unchanged.

No other file needs to change. No ingestion, model, persistence, worker, source, discovery-settings, or deployment file is in this list.

## Summary for the requester

- **Metadata already available for every EDINET filing:** document ID, EDINET issuer code, 5-character securities code (4-digit derivable by trailing-zero convention, not yet a display utility), native title, official `formCode`/`docTypeCode`/`ordinanceCode`, submission date, and full submission date+time. All confirmed present in `FilingEvent`/`scan_service.py`.
- **Direct-link strategy:** none — already investigated and confirmed no derivable public per-document URL exists; this design preserves the existing homepage-fallback link unchanged and puts all the fix into the surrounding label, reference, and fallback text.
- **Fallback strategy:** a new, filing-specific sentence (company name, 4-digit code, filed date, bilingual type where available) replacing today's one fixed generic sentence (Radar-only) and the silent, context-free links on all four Dashboard surfaces.
- **Unresolved:** two Dashboard surfaces (`regional_brief.py`, `recent_theme_activity.py`) cannot show the bilingual type label without either a repository-access change (drops unpromoted filings from view) or a new cache side-channel — left as an open implementation decision, not resolved here.
