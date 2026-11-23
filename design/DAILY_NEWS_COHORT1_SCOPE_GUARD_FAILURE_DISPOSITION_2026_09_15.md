# Daily News Cohort 1 — Scope-Guard Failure Disposition (2026-09-15)

Read-only failure-provenance review. No file was edited to produce
this document — every claim below is grounded in a direct read of the
three failing tests' own source, their module docstrings, `git log`
history for each test file, and a live, read-only re-run of each
failing test in isolation. No discovery run, worker, scan, cache/
database write, staging, commit, push, PR, merge, or deployment
occurred. All previously changed and untracked files (the Cohort 1
implementation diff, and the 12 pre-existing untracked design
artifacts) remain exactly as they were.

---

## 1. The three tests, their asserted forbidden paths, and their original intended feature boundary

### 1.1 `tests/test_comparison_persistence.py::test_no_ui_daily_news_edgar_dart_edinet_worker_or_deployment_files_touched`

**Owning feature** (module docstring, line 1): *"Radar evidence-packet
foundation, Phase 3, Step 2 (design/DECISIONS.md) — append-only
persistence for ComparisonRecord across JSON, SQLite, and Postgres
backends."* Committed as `466ad42` — *"Radar Phase 3, Step 2:
append-only comparison-result persistence"* — a completed,
already-merged Radar feature with no relationship to Daily News.

**Sibling positive scope guard, immediately above the failing test**
(`test_scope_guard_only_approved_persistence_files_changed`, lines
537-556), quoted verbatim:
> "Runs against `git diff HEAD` — only meaningful in a real checkout
> with this step's changes present; spuriously fires while ANY other
> legitimate uncommitted change is present and resolves once
> committed — same documented convention as this repo's other
> phase-scoped scope guards."

Its own allowlist is exactly 6 paths, all Radar-comparison-specific:
`src/data_access/comparison_store.py`, `src/data_access/state_db/
schema.py`, `src/data_access/state_db/comparison_repository.py`,
`src/data_access/postgres_state_db/schema.py`, `src/data_access/
postgres_state_db/comparison_repository.py`, `tests/test_comparison_
persistence.py`.

**The failing test itself** (lines 559-579) is that same guard's
mirror-image negative check: a `forbidden_prefixes`/`forbidden_paths`
set naming Radar's UI, EDGAR/DART/EDINET, worker, and deployment
surfaces — including the prefix `"src/data_access/daily_news/"` —
that this phase's *own* diff must never have touched. It carries no
docstring of its own; its intent is established entirely by its
sibling immediately above it and by the module's own docstring.

### 1.2 `tests/test_evidence_packet_phase2_step2.py::test_no_ui_daily_news_edgar_dart_worker_or_deployment_files_touched`

**Owning feature** (module docstring, lines 1-13): *"Radar
evidence-packet foundation, Phase 2, Step 2 (design/DECISIONS.md) —
persisting truthful provenance for an excerpt extracted from a member
inside an official EDINET ZIP package..."* Committed as `e49e4e0` —
*"Radar Phase 2, Step 2: persist EDINET ZIP-member evidence
provenance"* — again a completed, already-merged Radar feature, no
relationship to Daily News.

**The failing test carries its own explicit docstring** (lines
328-334), quoted verbatim — the most directly self-documenting of the
three:
> "Runs against `git diff HEAD` — only meaningful in a real checkout
> with this step's changes present, so it spuriously fires while ANY
> other legitimate uncommitted change happens to touch one of these
> paths, and resolves once committed — same documented convention as
> this repo's other phase-scoped scope guards (tests/test_ui_audit_
> phase_r1.py etc.)."

This is the strongest, most explicit piece of evidence in this
review: the test's own author states, in the test itself, that it
(a) is only meaningful for that one phase's own commit, (b)
*spuriously* fires on any unrelated uncommitted change, (c) resolves
on commit, and (d) is one instance of a named, repo-wide convention —
`tests/test_ui_audit_phase_r1.py` (confirmed to exist in this repo)
is cited as another example.

### 1.3 `tests/test_radar_comparison_display.py::test_no_daily_news_navigation_theme_pipeline_client_or_deployment_files_touched`

**Owning feature** (module docstring, lines 1-8): *"Radar
evidence-packet foundation, Phase 3, Step 3B (design/DECISIONS.md) —
read-only Radar Inbox integration for the latest persisted comparison
record per rendered candidate..."* This file's git history traces to
the Radar Inbox/public-filings-feed unification work (`80a3ff5`,
`1b781ee`, `5bbf6d6`) — again, a completed, already-merged Radar-UI
feature.

**Sibling positive scope guard, immediately above** (`test_scope_
guard_only_approved_ui_files_and_tests_changed`, lines 427-444),
quoted verbatim:
> "Runs against `git diff HEAD` — only meaningful in a real checkout
> with this step's changes present; spuriously fires while ANY other
> legitimate uncommitted change is present and resolves once
> committed — same documented convention as this repo's other
> phase-scoped scope guards."

Its own allowlist is exactly 4 paths: `src/ui/pages/radar_inbox.py`,
`src/ui/components/radar_card.py`, `src/ui/components/radar_
status.py`, `tests/test_radar_comparison_display.py`.

**The failing test itself** (lines 447-472) is, again, that guard's
negative mirror — a larger forbidden set including the prefix
`"src/data_access/daily_news/"` and the literal path `src/ui/pages/
daily_news.py`.

**Corroborating scale**: the literal phrase "resolves once committed"
appears in at least 4 distinct test files across this repository
(`test_comparison_bulk_retrieval.py`, `test_comparison_persistence.py`,
`test_evidence_packet_phase2_step2.py`, `test_prior_disclosure_
comparison.py`), and `test_radar_comparison_display.py` independently
states the same convention in its own words — this is a deliberate,
named, recurring repository pattern, not an isolated or accidental
one.

---

## 2. Is the only triggering changed path `source_registry.py`?

**Confirmed, precisely, by direct re-execution.** `git diff --name-
only HEAD` in the current working tree returns exactly 6 paths:
```
src/data_access/daily_news/source_registry.py
tests/test_daily_news_dedup.py
tests/test_daily_news_feed_registry.py
tests/test_daily_news_source_registry.py
tests/test_editorial_admission.py
tests/test_localization_dedup.py
```
Each of the three failing tests was re-run in isolation this session,
and each one's own `AssertionError` payload — the exact `hit` set it
computed — was captured directly from pytest's own output:
```
AssertionError: {'src/data_access/daily_news/source_registry.py'}
```
**All three, independently, identically.** None of the 5 modified
`tests/test_daily_news_*.py` files appears in any of the three tests'
own `forbidden_paths`/`forbidden_prefixes` sets (test files are never
listed as forbidden in any of the three — only `src/...`, a handful of
`design/*.md`, `render.yaml`, and `requirements.txt` are). The sole
triggering path, confirmed three times over, is `src/data_access/
daily_news/source_registry.py` — exactly the one production file this
Cohort 1 branch is scoped to change.

---

## 3. Classification: functional regression test, temporary phase-specific diff guard, or still-active repository policy?

**All three are temporary, phase-specific diff guards — none is a
functional regression test, and none is a still-active repository
policy in the sense of governing future, unrelated work.**

Evidence for this classification, converging from four independent
angles:
1. **Self-documentation.** Two of the three carry an explicit
   docstring (or an identically-worded sibling immediately above them)
   stating outright that they are meaningful "only... with this step's
   changes present," that they "spuriously fire" otherwise, and that
   they "resolve once committed." This is not an inference — it is the
   test authors' own stated intent, in the code itself.
2. **Mechanism.** All three compare `git diff --name-only HEAD` — the
   *currently uncommitted working-tree diff* — against a fixed
   snapshot of "files this one historical phase's own commit was
   allowed to touch." A `git diff HEAD` scope guard is definitionally
   a **point-in-time diff assertion**, not a content/behavior
   assertion about any code path — it has no way to distinguish "this
   phase's own work spilled into Daily News" (the real defect it was
   built to catch) from "an entirely different, later, legitimate
   Daily News change happens to be uncommitted in the same working
   tree right now" (what is actually happening here).
3. **Already-fulfilled purpose.** All three owning phases are fully
   committed and merged (`466ad42`, `e49e4e0`, and the Radar Inbox
   unification commits) — confirmed via `git log`, and confirmed that
   none of the three test files themselves appears anywhere in the
   current uncommitted diff. Each guard's own one-time job — proving
   *that specific historical commit* didn't spill into Daily News/UI/
   EDGAR/DART/EDINET/deployment files — was already satisfied at the
   moment that commit was made. They now run permanently against every
   future session's own uncommitted state, which was never their
   stated purpose.
4. **No functional assertion of any kind.** None of the three tests
   imports, calls, or exercises any Daily News code, model, or
   behavior — they assert nothing about what `source_registry.py`
   *does*; they assert only that its path string does not appear in a
   `git diff` output. A change to `source_registry.py`'s actual
   runtime behavior (a bug in a new source entry, a broken freshness
   window, anything) would be caught by the Daily News package's own
   test suite (already exercised and green — 378 passed, 4 skipped)
   — never by these three, which are structurally incapable of
   detecting any such thing.

---

## 4. May the Daily News Cohort 1 PR legitimately be considered green with these three excluded, or must the guard be amended first?

**Yes — legitimately green with these three excluded, without
amending anything first.** The reasoning:
- The three failures are proven, by the tests' own authors' own
  words and by mechanical necessity (item 2 above), to be a
  **known, accepted, self-documented class of false positive**
  inherent to how a `git diff HEAD`-based phase-scope guard works —
  not specific to this branch, this change, or Daily News.
- The *only* path that trips them is exactly the one file this
  Cohort 1 branch is scoped to change (§2) — there is no other,
  unexplained, or out-of-scope file contributing to any of the three
  failures.
- Once this branch's own changes are committed (the guards' own
  documented resolution condition — "resolves once committed"), `git
  diff --name-only HEAD` against the new commit will no longer
  contain `source_registry.py` as an uncommitted delta, and all three
  will pass again on the next run — this is not a hoped-for outcome,
  it is the literal, stated mechanism of how these guards work.
- No amendment is required to reach a legitimate "green" determination
  for *this* PR specifically — amending the guard is a separate,
  independent, lower-urgency improvement (§5), not a precondition.

---

## 5. If an amendment is warranted, the narrowest follow-up (not implemented here)

Not required for this PR, but worth naming as a real, general
repository-hygiene gap this review surfaced: **every phase-scoped
`git diff HEAD` scope guard in this repository is permanently live**
and will keep producing this exact class of false positive for any
future, unrelated, legitimate uncommitted change that happens to
touch one of its forbidden paths — this is not unique to Daily News
Cohort 1, and will recur for the next contributor working on any of
these forbidden-path prefixes with anything else uncommitted in the
tree at the same time.

**Narrowest possible follow-up** (a separate, future test-governance
change — explicitly not implemented in this branch or this document):
add a single, shared, opt-in mechanism (e.g. a `conftest.py`-level
`pytest.mark.skipif` gate keyed on a repo-local marker file, or a
documented `SKIP_PHASE_SCOPE_GUARDS` environment variable) that lets a
contributor explicitly acknowledge "I am not this phase's own commit;
skip its diff-scope guards for this run" — without deleting, weakening,
or renaming any of the guards' own real, working logic, and without
touching any of the dozens of individual scope-guard test bodies
already spread across the suite. This preserves every guard's original
value (it still fires, and still fails, for the one case it was
actually built to catch — a genuine spill from that phase's own real
commit) while removing the permanent, repo-wide false-positive tax on
every later, unrelated change. Scoping the exact mechanism, its
default (opt-in vs. opt-out), and which of the many existing phase
guards it should apply to is deliberately left to that separate,
future proposal — not decided or implemented here.

---

## 6. Final release recommendation

# READY AFTER DOCUMENTED EXCEPTION

The Daily News Cohort 1 branch is ready for commit with these exact
three tests treated as a documented, evidence-backed exception —
**not** unconditionally "ready for commit" (since a bare "ignore these
three" without this record would be an undocumented judgment call),
and **not** "blocked" (since blocking on a guard that is proven, by
its own authors' own words, to be structurally incapable of detecting
any real regression in this change would trade a false sense of safety
for real delay, with no offsetting benefit).

This exception is scoped narrowly: it applies to exactly these three
named tests, for exactly this reason (a `git diff HEAD`-based
phase-scope guard tripping on the sole approved production file this
branch touches), and is expected to resolve itself — with zero further
action — the moment this branch's changes are committed.

---

## Files read / created

**Read this session (all read-only)**: `tests/test_comparison_
persistence.py` (module docstring, lines 1-40, and lines 530-579 in
full), `tests/test_evidence_packet_phase2_step2.py` (module docstring,
lines 1-15, and lines 300-354 in full), `tests/test_radar_comparison_
display.py` (module docstring, lines 1-9, and lines 420-472 in full);
`git log --oneline -- <each file>` for provenance; `git diff --name-
only HEAD` (current working-tree state); each of the three failing
tests re-run individually via `pytest` to capture its own exact
`AssertionError` payload; a repo-wide grep confirming the "resolves
once committed" convention's recurrence across 4+ test files and the
existence of the cited sibling `tests/test_ui_audit_phase_r1.py`.

**Not touched**: `src/data_access/daily_news/source_registry.py`,
`tests/test_daily_news_dedup.py`, `tests/test_daily_news_feed_
registry.py`, `tests/test_daily_news_source_registry.py`, `tests/
test_editorial_admission.py`, `tests/test_localization_dedup.py`
(the Cohort 1 implementation diff — left exactly as it was); all 12
pre-existing untracked design artifacts and `tests/test_radar_
status.py`.

**Created**: `design/DAILY_NEWS_COHORT1_SCOPE_GUARD_FAILURE_
DISPOSITION_2026_09_15.md` (this document — untracked, not staged,
not committed).

No file was modified. No discovery run, worker, scan, cache/database
write, staging, commit, push, PR, merge, or deployment occurred.
