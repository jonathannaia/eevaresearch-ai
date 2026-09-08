# Issuer Registry Foundation — Phase A

Status: **foundation only**. This is not the autonomous radar — it is the
local model, static registry data, and compatibility layer that later,
separately-approved phases build on. No network call, cache write,
candidate, Signal, or UI change happened as part of this work. See
`design/DECISIONS.md` for the full phased rollout plan (Phase A–H) this was
approved out of.

## Why issuers are source-agnostic while source adapters stay source-specific

`src/config/tracked_companies.py`'s `TrackedCompany` is, and remains, a
per-source **scan configuration** record: "which company, from which
source, with which source-native identifier." It has no way to express that
a company might eventually be tracked through more than one channel at
once — an EDGAR filing pipeline *and* a future IR-adapter pull, say —
because identity and source config are the same record.

`src/models/issuer.py`'s `Issuer` separates those two concerns. An issuer's
identity (legal name, jurisdiction, theme/layer classification) lives
independently of any one source; `identifiers` is an open
`{source_name: native_id}` mapping precisely so a new source — EDGAR, DART,
EDINET today, an IR adapter or a new national regulator tomorrow — can
attach its own identifier to an existing issuer without changing the model
or touching any other source's data. Source adapters themselves (the
`client.py`/`*_rules.py`/`*_pipeline.py` modules under
`src/data_access/{dart,edgar,edinet}/`) stay exactly as source-specific as
they are today — each source's real request shape, auth mechanism, and
document format are genuinely different, and nothing about this phase
changes that.

## SEED coverage vs. DISCOVERED coverage

`CoverageState.SEED` means an issuer is part of the curated, actively-
scanned universe — today, concretely, that every `SEED_ISSUERS` entry
round-trips losslessly back into a `TrackedCompany` via
`tracked_companies_from_issuer_registry()`, so it's provably equivalent to
what every existing DART/EDGAR/EDINET pipeline already scans.

`CoverageState.DISCOVERED` means an issuer has been *proposed* for
coverage — surfaced as a candidate worth considering — but has not been
reviewed or promoted. In this phase, the only `DISCOVERED` issuers are the
21 static `DISCOVERY_STUBS` built from the user-provided portfolio-map seed
list, for companies not already covered by `SEED_ISSUERS`. A future
discovery engine (Phase B onward) would add more `DISCOVERED` issuers over
time, always through the same gate: proposed, never auto-scanned.

## Why unverified discovery stubs are excluded from scan eligibility

Every `DISCOVERY_STUBS` entry has `identifiers={}` — no CIK, corp_code, or
EDINET code was invented for any of them, even where a plausible source
match exists (e.g. `PDFS`/`AXTI`/`FCEL` are plausibly SEC EDGAR-eligible,
but none has a confirmed CIK on file). This isn't just a data-completeness
gap — it's structural: `tracked_companies_from_issuer_registry()` only ever
reads `SEED_ISSUERS`, never `DISCOVERY_STUBS`, so a stub cannot flow into
the one function that produces the shape every existing pipeline consumes.
`tests/test_issuer_registry.py::test_discovery_stubs_never_appear_in_
compatibility_output` checks this directly, and 13 of the 21 stubs
additionally sit in jurisdictions (Taiwan, Germany, UK, France, Sweden)
that have no source adapter of any kind today — there is nothing for them
to be scanned *by* yet regardless.

## The Phase A compatibility guarantee

`src/config/tracked_companies.py` is **untouched** — zero lines changed.
This was a deliberate design choice over the alternative (repointing
existing pipelines at a new adapter): leaving the file byte-identical is
the strongest possible compatibility guarantee, with zero risk of a subtle
behavioral change to EDGAR/DART/EDINET request shapes, CIK/corp-code/
EDINET-code resolution, or anything else three fully-verified, live-tested
pipelines depend on.

Instead, `SEED_ISSUERS` is *generated* from the live `TRACKED_COMPANIES`
tuple (never hand-transcribed, so it can't drift out of sync or introduce a
transcription error), and `tracked_companies_from_issuer_registry()`
converts it back. Both directions are tested for exact equivalence:
`orig == regen` holds for every field, on every one of the 29 entries
currently in the registry, for both `active_only=True` and
`active_only=False`. No existing page, pipeline, or test imports anything
from this phase's new modules — the guarantee is that they *could* be
swapped in later with no behavior change, not that they have been.

## What this phase found, corrected from the approval message

The approval assumed 28 existing `TrackedCompany` entries. The actual,
freshly-counted total (verified via `get_tracked_companies(active_only=
False)`, not assumed) is **29** — 2 OpenDART/DART, 22 SEC EDGAR, 5 EDINET.
`SEED_ISSUERS` reflects the real count. See the Phase A final report for
the full breakdown.

## Explicitly deferred — not done in this phase

- Live verification of any discovery stub's identity, ticker, or exchange.
- Any new-jurisdiction source adapter (Taiwan/TWSE, Germany/Bundesanzeiger,
  UK/FCA, France/AMF, Sweden — none exist today, for stubs or otherwise).
- IR/earnings, presentations, or news adapters of any kind.
- Any scheduled/autonomous scan execution.
- Any cache write, candidate creation, translation, or Signal-eligibility
  change — `signal_promotion.py`'s `PUBLISHED`-only gate is untouched.
- Any UI change (Coverage page, Radar Inbox, dashboard, themes) — this
  phase is data-model and registry only.
- Resolving the four documented `KNOWN_CATEGORY_CONFLICTS` (MRVL, TSEM,
  the networking-interconnect/interconnect-switching naming overlap,
  Kioxia's ticker-format question) — recorded as explicit unresolved
  metadata, not silently picked one way.
