# Signals Admission & Materiality Calibration (2026-09-15)

Read-only design document. No file under `src/`, no test, no source
registry, tracked-company, configuration, worker, database, cache, or
production data was modified to produce this document. Starts from
`origin/main` @ `b39ac24` (post PR #59 — confirmed via `git log`/`git
status` this session). No discovery run, scan, or write of any kind
was performed against this project's own data/cache.

**Evidence discipline for this document**: every root-cause claim
below is grounded in a direct read of the real, current code
(`materiality_classification.py`, `editorial_admission.py`,
`editorial_matching.py`, `editorial_pipeline.py`, `daily_news_
pipeline.py` — all read in full this session) plus, where the live
article's real text materially affects the analysis, an independent
web search/fetch of the real published piece (Fixtures A, D, E, F —
see each fixture's own "Evidence" line). Where the exact stored
excerpt text for a fixture was not independently retrievable this
session, this is stated explicitly rather than assumed — the
mechanism is described as "plausible, given the code's real behavior"
rather than "confirmed," per this task's own validation constraints.

---

## 1. Root-cause trace per fixture

### Fixture A — Intel Nova Lake-S rumor (Tom's Hardware)

**Trace**: Tom's Hardware is `INDEPENDENT_NEWS` (Lane B, editorial).
`editorial_matching.match_companies()` matches "Intel Corp." via its
mechanical alias "Intel" (bare, present in the title). `editorial_
admission._identified_subject_companies()` treats "Intel Corp." as an
**ambiguous-alias company** (`_AMBIGUOUS_ALIAS_COMPANIES` — "Intel"
collides with the common word "intel"/intelligence), so identity
requires **both** `in_title` (true — "Intel" is in the headline) **and**
`_company_has_nearby_action_language()` (a `_COMPANY_ACTION_KEYWORDS`
hit in the same sentence as "Intel"). The real headline verb is
"cans" and the subhead uses "said to resurface" — neither is in
`_COMPANY_ACTION_KEYWORDS` (announces/reports/discloses/launches/
acquires/expands/invests/...). **Evidence (live search, this
session)**: the real article (confirmed via web search,
`tomshardware.com/pc-components/cpus/intel-reportedly-cans-...`)
attributes the claim to "tipster Jaykihn" and states "The leaker
says..." — explicitly rumor/leak-sourced, exactly as the fixture
describes; Intel itself is not quoted or shown to have commented.

**Two independent, compounding defects, both real:**

1. **Materiality Gate D (`credible_editorial_reporting`) has no
   negative signal for rumor/tipster language.** Gate D fires on
   `is_independent_news=True` + a `_REPORTING_VERB_KEYWORDS` hit +
   substantive length + an on-taxonomy or anchor hit. The keyword list
   (`confirmed`, `disclosed`, `revealed`, `according to sources`,
   `filed with`, `reported exclusively`, ...) makes no distinction
   between "the company confirmed X" (strong evidence) and "sources
   told us X"/"a leaker claims X" (weak, unconfirmed evidence) — both
   shapes of language can satisfy the same gate. **This is the primary,
   most direct root cause**, and it is a self-acknowledged limitation
   in the module's own docstring ("a deterministic proxy for 'new
   attributable facts,' not true NLP fact extraction"). Separately, a
   plausible secondary path is Gate C (`taxonomy_anchored_consequence`)
   — "GPU"/"chip" language in a CPU/APU article would very likely match
   the `ai_compute_and_data_center` taxonomy bucket, and if any
   materiality-anchor word (e.g. "shipment," "capacity") also appears
   in the article body, Gate C fires with **zero rumor-awareness of any
   kind**, since Gate C has no reporting-verb check at all.
2. **`matched_companies` (raw, pre-identity-check) — not `identified_
   companies` (the admission gate's own subject-confirmation result)
   — is what gets persisted on the story and used for every downstream
   company-scoped view.** Confirmed by direct code read: `editorial_
   admission.assess_admission()` computes `identified_companies`
   internally but returns only `AdmissionDecision(admitted: bool,
   reason: str)` — the identity list itself is discarded. `editorial_
   pipeline.py` (line 291, then line 361) stores `EditorialStory(...,
   matched_companies=matched_companies, ...)` using the **original,
   un-narrowed** match from `matched_companies_and_themes()`, not
   anything admission-gate-refined. `select_visible_editorial_stories_
   for_company()` (line 438) filters on `company_name in s.matched_
   companies` — so even a story admitted only via `theme_subject` (no
   company ever confirmed as subject) can still surface under "Intel"
   on every company-scoped view, including the Dashboard. **This
   defect applies identically to Fixture B** — see below.

**Classification**: primarily a **materiality classification gap**
(Gate D/Gate C have no rumor-awareness), compounded by a genuine
**entity-attribution-propagation defect** (admission's own identity
refinement is computed and then thrown away before persistence). Not
an admission-policy-category gap in the "missing rule" sense (Gate D
already exists and was *intended* to gate exactly this kind of
independent-reporting content — it is simply mis-calibrated, not
absent) — but see Rule 1 (§2) for why a *new*, explicit rumor-negative
signal is still the right fix, layered on top of the existing gate
rather than replacing it.

### Fixture B — "New Math Data Achieves Premier Tier Status in the AWS Partner Network"

**Trace**: this is very likely a PR-Newswire-syndicated or similar
wire item (Lane B, `INDEPENDENT_NEWS` or `independent_news`-shaped
wire content), matched to "Amazon.com, Inc." via its **curated brand
alias** "Amazon Web Services" (`company_aliases._BRAND_ALIAS_OVERLAY`
— matched case-sensitively, present verbatim in the title). Amazon.com,
Inc. is also in `_AMBIGUOUS_ALIAS_COMPANIES` (the whole company, not
just its bare "Amazon.com" mechanical alias — the code checks
`_AMBIGUOUS_ALIAS_COMPANIES` by company name, regardless of which
specific alias matched), so identity again requires `in_title` (true)
**and** same-sentence action language. The one sentence — "New Math
Data Achieves Premier Tier Status in the Amazon Web Services Partner
Network" — uses "Achieves," which is **not** in `_COMPANY_ACTION_
KEYWORDS`. If that is the entirety of the qualifying text, identity
fails and — since no `THEME_KEYWORDS` phrase ("AI cloud," "AI
infrastructure," etc.) appears in this exact headline either —
admission should correctly return `False` under today's code
(`company_mention_not_subject_worthy`, since `matched_themes` would
also be empty). **This session could not retrieve the item's real
stored excerpt/description text**, so two real possibilities remain
open, both pointing to the same underlying gaps:

1. The full body text contains a nearby action-keyword (e.g. boilerplate
   partner-program prose like "AWS **expanded** its partner network to
   recognize..." or "...achieves status, **partners with** AWS on...")
   — `_company_has_nearby_action_language()` would then grant Amazon
   identity, and the story is admitted as `company_subject:Amazon.com,
   Inc.` even though **the actual disclosed event is a third party's
   certification, not any action Amazon itself took.**
2. The body text contains an incidental cloud/AI-infrastructure phrase
   elsewhere, `matched_themes` becomes non-empty, admission succeeds via
   `theme_subject`, and — via the **exact same `matched_companies`-not-
   `identified_companies` persistence gap identified in Fixture A** —
   Amazon still ends up tagged on the story regardless.

**The deeper, structural finding**: **no rule anywhere in this
codebase — admission or materiality — recognizes "marketing/
certification/partner-tier/award/badge" as its own content shape.**
`_CONSUMER_FORMAT_PHRASES` covers retail-consumer framing; nothing
covers B2B partner-ecosystem framing ("achieves … status," "certified
partner," "named a leader," "partner network," "competency," "awarded,"
"recognized as"). This is a **missing rule**, not a mis-calibrated one
— Fixture A had an existing-but-too-permissive Gate D; Fixture B has no
gate addressing this content shape at all.

**Classification**: **admission-layer gap** (a new content-exclusion
category is needed, parallel to `_CONSUMER_FORMAT_PHRASES`) **plus
entity attribution** (the action-language check cannot distinguish "the
tracked company performed an action" from "a third party was named
inside the tracked company's own branded program" — the grammatical
subject of the sentence is "New Math Data," not Amazon, and nothing in
`_COMPANY_ACTION_KEYWORDS` checks true grammatical agency) **plus the
same attribution-propagation defect as Fixture A**.

### Fixture C — "DAF establishes space technology portfolio executive"

**Trace**: this is a Space Force (`spaceforce-news-rss`) item. Direct
code read of `editorial_pipeline.py`'s own qualifying loop:
```python
if source.source_id == _SPACEFORCE_SOURCE_ID:
    pass
elif source.source_id == _NIST_SOURCE_ID:
    ...
elif not matched_companies and not matched_themes:
    continue
else:
    admission = assess_admission(...)
    if not admission.admitted:
        continue
```
**Every Space Force item is unconditionally admitted** — neither the
company/theme fail-closed pre-check nor `assess_admission()`'s
precision-first content gate ever runs for this source. This is a real,
deliberate, already-approved design choice (the module's own docstring:
"Deliberately not applied to SpaceForce/NIST: an existing, narrower,
separately-approved bypass this fix does not touch or expand" — from
the Government/Public Sector Daily News lane work), not an accident —
but it means **zero content-based filtering of any kind applies to this
source**, including for routine personnel/governance news that has
nothing to do with a contract, budget, program, or technology
milestone.

For materiality: `SourceCategory.GOVERNMENT_POLICY` requires a `_FORMAL_
ACTION_KEYWORDS` hit for Gate A3 (rule/ruling/order/sanction/
restriction/ban/approve/license/revoke/enforcement/investigation/
settlement/export control/entity list/tariff/rate decision/policy
statement/final rule/proposed rule) — "establishes" matches none of
these. `materiality_classification.TAXONOMY_KEYWORDS` has **no `space`
bucket at all** (its 5 buckets are semiconductors, AI/datacenter,
memory/networking/power, wafers/materials, capital/trade-policy — none
cover launch/satellite/space-program language), so Gate C almost
certainly cannot fire for this headline either. This item most likely
already classifies as `BACKGROUND` (`off_taxonomy_no_anchor`) under
today's materiality rules — **the defect is that it is admitted
(displayed) at all, unconditionally, regardless of tier**, not that it
is mistiered.

**Classification**: **source-level policy gap** (the Space Force
admission bypass is too broad for a source that also carries routine
governance/personnel news) **plus a genuinely missing rule** (no
personnel/leadership/governance-announcement rejection exists anywhere
in the system, for any source) — the fix needs both: the new rule
itself (Rule 3, §2), applied in a way that reaches even sources with an
otherwise-broader bypass.

### Fixture D — "Scott Bergs, CEO of Kirkwood IG: Fiber and the AI Data Center Buildout"

**Trace**: Data Center Frontier is `INDEPENDENT_NEWS`. Kirkwood IG
(**Evidence, live search this session**: a Blue Owl-backed dark-fiber/
data-center-infrastructure platform, CEO Scott Bergs, not a public
company and not in `tracked_companies.py` or any Daily-News-only
`DISCOVERY_STUBS` entry) produces **no company match at all** —
`matched_companies = ()`. The headline's own "AI Data Center" phrase is
an **exact, literal entry** in `editorial_matching.THEME_KEYWORDS`'s
`ai-buildout` bucket, so `matched_themes = ("ai-buildout",)`. Per
`assess_admission()`'s own logic, when `matched_companies` is empty the
`if matched_companies:` block is skipped entirely and the function
falls straight to `if matched_themes: return AdmissionDecision(True,
"theme_subject:ai-buildout")` — **admitted, correctly, with zero
company ever attached** (this part of the system is working exactly as
designed: a theme-only item is meant to be admissible, since "relevant
but not company-specific" is a real, legitimate category).

The real defect is materiality. "AI data center" is **also** a literal
entry in `materiality_classification.TAXONOMY_KEYWORDS`'s `ai_compute_
and_data_center` bucket, so `taxonomy_hits` fires. Gate C
(`taxonomy_anchored_consequence`) requires only a co-occurring
materiality-anchor word **anywhere in the text, with no numeric
requirement at all** — and a long-form CEO interview about "fiber and
the AI data center buildout" is extremely likely to use at least one of
`capacity`, `expansion`, `investment`, `contract`, or `backlog`
somewhere in its body, none of which need to describe a *new,
measurable, attributable* fact. **This session could not retrieve the
exact fixture article's stored text** (the live search found related
Data Center Frontier coverage of Scott Bergs/Kirkwood-adjacent DF&I,
confirming the interview-format/private-company framing described in
the fixture, but not this specific piece verbatim) — the mechanism
above is the well-evidenced, code-certain explanation for how a
theme-only, no-company, interview-format item can reach `HIGH_SIGNAL`
today, even without confirming the exact anchor word.

**Classification**: **materiality classification gap** — Gate C has no
awareness of (a) interview/Q&A/opinion format (lower evidentiary weight
than hard news by construction — an executive restating industry
narrative in conversation is not the same as a disclosed fact) or (b)
whether the taxonomy+anchor co-occurrence reflects a genuinely *new*
disclosure versus generic scene-setting language. Not an entity-
attribution defect (no company is wrongly attached here — the
admission-layer theme_subject path is behaving correctly); not a
source-level policy gap (Data Center Frontier carries no special
bypass).

### Fixture E — "Introducing Meta One: A Subscription Service With More Features and AI..."

**Trace**: Lane A (Meta's own official IR/newsroom feed — `meta-ir-rss`
or `meta-newsroom-rss`). **Structural fact, confirmed by direct code
read**: `daily_news_pipeline.py` has **no `editorial_admission.py`-
equivalent gate at all** — Lane A items are never "rejected," only
tiered, via `classify_issuer_story()` alone (`is_primary_disclosure`
is always `False` for Lane A — `SourceClass` is always `OFFICIAL_
COMPANY`, never `REGULATORY_FILING`; `is_independent_news` is always
`False` — Gate D never applies to Lane A at all). So the only levers
are Gates A2 (earnings — no), A4 (dividend — no), B (anchor + a
qualifying numeric pattern), B2 (capital return — no), and C
(taxonomy + anchor, no number).

**Evidence (live search, this session)**: the real Meta One launch
carries concrete consumer pricing (Instagram/Facebook Plus $3.99/mo,
WhatsApp Plus $2.99/mo, Meta One Plus $7.99/mo, Meta One Premium
$19.99/mo) — **none of these figures match `_NUMERIC_MAGNITUDE_
PATTERNS`** (which require `billion/million/bn/mn/b/m`, `GW/MW`, a bare
`%`, or a `thousand/million/billion`-scaled unit count — a bare
`$3.99` never qualifies), so **Gate B almost certainly does not fire
from the pricing itself**. The $125–145B AI-datacenter capex figure
found in this session's research is Meta's own separately-reported,
company-wide guidance (from unrelated coverage, e.g. Reuters/Yahoo
Finance reporting on the broader subscription strategy) — **not
necessarily present inside the "Introducing Meta One" post's own
text**, and this session could not independently confirm the exact
stored excerpt for the live fixture item. The most plausible mechanism,
if this item does reach `HIGH_SIGNAL` today, is **Gate C**: Meta's own
2026-era first-party posts routinely carry boilerplate AI-investment/
AI-infrastructure framing (a real, literal `TAXONOMY_KEYWORDS` phrase
like "AI infrastructure") **somewhere in the piece**, co-occurring
(anywhere in the combined title+excerpt text, no proximity requirement)
with a generic anchor word like "investment" — with **zero requirement
that the taxonomy phrase and the anchor actually describe the SAME
disclosed fact**, let alone one specific to Meta One itself rather than
Meta's general AI narrative.

**Classification**: **materiality classification gap**, same
underlying shape as Fixture D — Gate C's lack of a proximity/co-
reference requirement lets an unrelated boilerplate paragraph
(anywhere in the piece) manufacture materiality for a genuinely
separate, lower-materiality event (a consumer subscription-tier
launch). Not an admission gap (Lane A has none by design, correctly —
see Rule 5's framing in §2) and not an entity-attribution issue (Meta
is genuinely the subject of its own press release).

### Fixture F — "From Megawatts to Tokens: How NVIDIA Maximizes AI Factory Production" (positive control)

**Trace**: Lane A, NVIDIA's own official newsroom feed. **Evidence
(live search, this session)**: NVIDIA's own real "AI factory" content
family (the exact title searched did not resolve to one single
canonical URL, but closely related, contemporaneous NVIDIA first-party
posts on this same theme were found and read) consistently reports
concrete, quantified operating metrics — "tokens per megawatt,"
"performance per watt," and, in one closely-related NVIDIA post, an
explicit "1,000,000x increase in inference throughput per megawatt over
six generations." `_NUMERIC_MAGNITUDE_PATTERNS` explicitly matches
`\d+(\.\d+)?\s?(GW|MW|gigawatts?|megawatts?)` — a real "megawatts"/"MW"
figure co-occurring with any `_MATERIALITY_ANCHOR_KEYWORDS` anchor
(most plausibly "capacity" or "investment," both extremely likely in
AI-factory infrastructure content) satisfies **Gate B directly**, which
is exactly the gate the module's own docstring names as the reason the
real, analogous AMD/Anthropic and Bloom Energy/Oracle gigawatt-scale
announcements correctly reach `HIGH_SIGNAL` today.

**Classification**: **correctly classified today; no defect.** This is
the positive control every proposed rule in §2 must be checked against
— see §4's regression plan. The one refinement this fixture invites
(not a defect fix, an enhancement) is distinguishing **measured,
already-achieved figures** ("achieved a 1,000,000x increase") from
**forward-looking projections** ("could enable up to...") — both are
legitimate first-party disclosure and both should stay eligible, but a
transparency/audit-trail distinction between the two is worth adding
(see Rule 5b, §2) without changing either one's admitted/eligible
status.

---

## 2. Proposed internal rules

**Design principle carried through all six rules**: extend the
existing, already-calibrated gate shapes (a new curated phrase list,
a new suppression condition on an existing gate, a new proximity
requirement) — never a broad keyword ban, never an LLM call, never a
change to the fundamental admission/materiality architecture. Every
rule below is additive and independently toggleable; none requires
touching EDGAR/EDINET/DART, source registries, or the issuer-lane
Lane A "no admission gate" design (Fixture E/F's own architecture,
which is correct and stays unchanged).

### Rule 1 — Unconfirmed-report / rumor rule

**Where**: a new, narrow, curated `_RUMOR_HEDGE_PATTERNS` list —
`reportedly`, `said to`, `rumored`, `rumor`, `leaked`, `leaker`,
`tipster`, `according to leaks`, `purportedly`, `allegedly`, `claims to`,
`sources say` (deliberately distinct from and narrower than Gate D's
own `according to sources`, which stays as a *positive* signal — see
below), `unconfirmed`.

**Materiality effect**: when a rumor-hedge phrase is present, **Gate D
is suppressed** (the same suppression shape Gates B/C already use for
survey/research language — `_is_survey_or_research_content()` is the
direct precedent). Gate C is suppressed too when the rumor-hedge phrase
appears in the **same sentence** as the taxonomy/anchor match (reusing
`editorial_admission._sentences()`'s own sentence-scoping helper, not a
new mechanism) — a rumor about an unrelated fact elsewhere in a longer,
otherwise-substantive article should not lose all of its real gates,
only the ones the rumor language directly taints.

**Admission effect**: a new admission-layer check, structurally
parallel to `_matched_consumer_format()` — a rumor-hedge hit is not an
outright rejection (unlike consumer format), it is a **downgrade
signal**: the item may still be admitted (so a real, on-topic rumor
about a tracked company can appear as `Watchlist`/`Background`, per
this task's own product policy that relevant-but-low-conviction
material belongs there, not hidden entirely), but it can never itself
justify `HIGH_SIGNAL`.

**Evidence hierarchy (exceptions), most to least authoritative**:
1. **Primary-source confirmation** — the company's own statement, an
   `OFFICIAL_FILING`/regulatory disclosure, or a named, on-the-record
   company spokesperson quote **in the same story**. Always eligible
   for `HIGH_SIGNAL` if it independently satisfies an existing hard
   gate (A/A2/A4/B/B2) — the rumor-hedge phrase, if still present
   elsewhere in the piece (e.g. "sources first reported this in June;
   the company confirmed it today"), no longer suppresses Gate D/C once
   an unambiguous confirmation phrase is also present, mirroring
   exactly how `_UNAMBIGUOUS_ACTUAL_RESULT_KEYWORDS` already overrides
   `_SCHEDULING_KEYWORDS` for the earnings-vs-scheduling case (same
   established pattern, reused, not invented).
2. **Well-corroborated, specific reporting** — reporting that names a
   real regulatory filing, a specific dated document, or multiple
   independently named outlets, with a real quantified figure
   (satisfies Gate B on its own hard evidence, independent of the
   attribution-verb language) — eligible via Gate B/B2/A directly; the
   rumor-hedge suppression only ever touches Gates C/D, never the
   harder, already-quantification-gated B/B2/A/A2/A4.
3. **Single-tipster/leaker-sourced rumor, no company confirmation** —
   the Fixture A shape. Never `HIGH_SIGNAL`. May be `Watchlist`/
   `Background` if independently on-taxonomy.

### Rule 2 — Marketing-designation / partner-tier / certification rule

**Where**: a new, narrow, curated `_MARKETING_DESIGNATION_PHRASES` list
in `editorial_admission.py`, structurally identical in shape to
`_CONSUMER_FORMAT_PHRASES` — `"achieves ... status"` (regex, bounded
proximity, not a fixed phrase — "Achieves Premier Tier Status,"
"Achieves Gold Partner Status" both need to match), `"tier status"`,
`"certified partner"`, `"partner network"`, `"competency partner"`,
`"partner of the year"`, `"accredited"`, `"named a leader in"`,
`"recognized as a"`, `"wins award"`, `"award-winning"`, `"inducted
into"`, `"membership"`, `"badge"`, `"designation"`.

**Effect**: identical exception shape to the existing consumer-format
gate — rejected outright **unless** the story independently carries
`anchor_evidence` (`_HARD_MATERIAL_REASON_PREFIXES` — a real Gate
A/A2/A4/B/B2 hit) **and** a genuinely identified company subject (not
merely the platform-owner being named). This directly implements the
task's stated exception: "Allow only if the item independently
discloses a material contract, named customer deployment, measurable
capacity, procurement, revenue impact, or other concrete business
event."

**Entity-attribution note**: this rule alone does not fix Fixture B's
deeper "is Amazon even the right company to attach" question — that
is Rule 6's job (below). Rule 2 rejects the *content shape*; Rule 6
fixes the *attribution mechanism* so that even a story this rule
doesn't outright reject (because it does independently disclose real
Amazon-side material, e.g. a genuine "AWS signs $X contract with
Company Y" story) attaches the correct, narrowed company set.

### Rule 3 — Personnel / leadership / governance-announcement rule

**Where**: a new, narrow, curated `_PERSONNEL_ANNOUNCEMENT_PHRASES`
list — `"appoints"`, `"names [x] as"` (regex, bounded proximity),
`"joins as"`, `"executive appointment"`, `"establishes ... executive"`
/`"establishes ... portfolio"` (regex, bounded proximity — the exact
Fixture C shape), `"creates new role"`, `"new position"`, `"promotes"`,
`"steps down"`, `"resigns"`, `"retires"`, `"succession plan"`,
`"portfolio executive"`, `"board appointment"`, `"joins the board"`.
Deliberately does **not** reuse `_COMPANY_ACTION_KEYWORDS`'s existing
`"executives"`/`"board of directors"` terms directly (those exist for a
different purpose — identity establishment — and remain unchanged;
this is a separate, content-shape-level check, same non-interference
design already established between `_CONSUMER_FORMAT_PHRASES` and
`_COMPANY_ACTION_KEYWORDS`).

**Effect**: same exception shape as Rules 1/2 — rejected unless
independently tied to `anchor_evidence` representing a disclosed
strategic transaction, formal program, material restructuring, or
capital-allocation event (e.g. "names new CEO following $2B
acquisition" keeps its Gate B/A hit and is admitted; "DAF establishes
space technology portfolio executive" has no such hit and is rejected).

**Critical implementation requirement (distinct from Rules 1/2/4)**:
this rule must run **even for sources with a broader admission bypass**
(Space Force, NIST) — Fixture C's root cause is precisely that today's
bypass skips content filtering entirely. The narrowest fix is **not**
to remove the Space Force bypass generally (a separately-approved,
deliberate design choice this document does not revisit), but to carve
out a small, explicit, content-shape check that still runs
unconditionally, before the bypass, for exactly this one category —
matching the same "narrow, source-scoped exception to an exception"
pattern `editorial_pipeline.py` already uses for NIST's own CHIPS/
semiconductor allow-list.

### Rule 4 — Interview / podcast rule

**Where**: a new, narrow, curated `_INTERVIEW_FORMAT_PATTERNS` list of
regexes matching the real, observed title shapes: `r"^[A-Z][a-z]+
\s[A-Z][a-z]+,\s.{0,40}(CEO|CTO|CFO|President|Founder)\sof\s"` (the
exact Fixture D shape — "Scott Bergs, CEO of Kirkwood IG:"), plus
literal phrase matches for `"podcast"`, `"interview"`, `"Q&A with"`,
`"in conversation with"`, `"on the show"`, `"webinar"`.

**Effect**: when interview-format is detected, **Gate C is suppressed**
for that item (the same suppression shape as survey-language and
rumor-hedge language above — three independent, additive suppression
conditions on the same gate, each narrowly scoped and each documented
with its own rationale, matching this module's own established pattern
of layering calibration fixes onto Gate C/B rather than rewriting them).
Gate D (already `is_independent_news`-gated) is **not** automatically
suppressed — a genuine investigative interview that discloses a new,
attributable, measurable fact ("the CEO disclosed a $400M contract
during the interview") can still reach `HIGH_SIGNAL` via Gate B
(quantified) or Gate D itself (if it independently carries real
fact-attribution language, not just conversational format). This
directly implements the task's explicit requirement: "Do not
automatically reject all interviews/podcasts... Admit only when they
contain a new, attributable, measurable disclosure."

**Admission effect**: none needed beyond what Rule 6 already provides —
an interview about a non-tracked private company (Fixture D's exact
shape) is correctly admitted today via `theme_subject` with zero
company attached; Rule 4's job is purely to stop it from being
mistiered as `HIGH_SIGNAL`, not to change whether it's shown at all.

### Rule 5 — First-party product-launch materiality rule

**Where**: two related, narrow changes to `materiality_
classification.py`, both scoped to Gate C only:

**5a. Sentence-proximity requirement for Gate C.** Today, `taxonomy_
hits` and `taxonomy_pairing_anchor_hits` are each computed
independently against the *entire* combined text — a taxonomy phrase
in paragraph 1 can pair with an anchor word in paragraph 4 with no
relationship between them. Require the two to co-occur in the **same
sentence** (reusing `editorial_admission._sentences()`, already a
proven, tested helper — no new mechanism), mirroring exactly the
"Incidental/historical-mention calibration fix" already applied to
`_company_has_nearby_action_language()` for the identical class of
problem (an unrelated fact elsewhere in the article satisfying a gate
that should require co-reference). This is the direct fix for Fixture
E's plausible mechanism (boilerplate AI-investment framing elsewhere in
the piece) and reinforces Fixture D's fix (Rule 4) with a second,
independent, general-purpose tightening that also helps every future
case shaped like it, not just interviews specifically.

**5b. Measured-vs-projected annotation (non-suppressing, transparency
only).** A narrow `_PROJECTION_LANGUAGE_PATTERNS` check (`"could
enable"`, `"is expected to"`, `"aims to"`, `"by 2030"`/`"by 2027"`-style
future-year framing, `"projected"`, `"forecast"`) that **never changes
a gate's pass/fail outcome** — it only appends a `reasons` tag
(`"measured_result"` vs. `"forward_projection"`) alongside whatever gate
already fired, purely for downstream transparency/audit (e.g. a future
UI affordance distinguishing "NVIDIA reports it achieved X" from
"NVIDIA projects it could achieve X" without suppressing either).
Explicitly designed to leave Fixture F's own eligibility completely
unchanged — see §4's positive-control check.

### Rule 6 — Stricter company-attribution rule

**Where**: `editorial_admission.AdmissionDecision` gains a new field,
`identified_companies: tuple[str, ...]` (populated from the function's
own already-computed `_identified_subject_companies()` result — no new
computation, just no longer discarding an existing one).
`editorial_pipeline.py`'s story-construction call (line 357-364) is
changed to build `matched_companies` for the persisted `EditorialStory`
from `admission.identified_companies` when the admission reason is
`company_subject:...`, and from an **empty tuple** when the admission
reason is `theme_subject:...` (no company was ever confirmed as the
actual subject — only a theme was).

**Effect**: this is the single structural fix that closes both Fixture
A's and Fixture B's shared attribution-propagation gap, and it composes
correctly with Rules 1-4: a story admitted via `theme_subject` no
longer silently carries a raw, unconfirmed company match into every
downstream company-filtered view (the Dashboard's own per-company
"Recently Updated" section, `select_visible_editorial_stories_for_
company()`, and any future consumer of `EditorialStory.matched_
companies`). A genuine company-subject story (Fixture F's shape, or a
real AWS contract announcement — see §4's positive control) is
completely unaffected, since its `identified_companies` already equals
its `matched_companies` today in the common case.

---

## 3. Classification matrix

| Fixture | Expected result | Internal reason (never shown to users) |
|---|---|---|
| A — Intel Nova Lake-S rumor | **Reject before Signals** (Watchlist/Background at most if independently on-taxonomy; never High Signal without primary confirmation) | `rumor_hedge_suppressed_gate_d` / `rumor_hedge_suppressed_gate_c` |
| B — Math Data / AWS Partner Network | **Reject** (marketing-designation content; Amazon not the disclosed subject) | `marketing_designation_no_anchor_evidence` |
| C — DAF portfolio executive | **Reject** (routine personnel/governance announcement, no disclosed transaction/program/budget) | `personnel_announcement_no_anchor_evidence` |
| D — Kirkwood IG / Data Center Frontier interview | **Background or Review needed** (theme-relevant, no tracked-issuer linkage, no new measurable disclosure) — never High Signal | `interview_format_gate_c_suppressed` |
| E — Meta One subscription | **Background or Watchlist by default** (first-party product launch; stays visible, correctly downgraded absent concrete pricing-at-scale/adoption/revenue/capex evidence specific to the event itself) | `product_launch_no_proximate_anchor` |
| F — NVIDIA Megawatts to Tokens | **High Signal preserved** (quantified GW/MW + anchor, Gate B) | `quantified_change:capacity` (or equivalent) — unchanged |

### User-visible behavior, kept strictly separate from internal reason codes

- **Rejected items**: never rendered, anywhere — no card, no row, no
  count badge referencing them. This is unchanged from today's
  architecture (an admission `False` result already means "never
  constructed," per `editorial_matching.py`'s own module docstring).
- **Background / Review needed**: rendered using the *existing*,
  already-approved card vocabulary this project already ships (per the
  companion Dashboard Signals work already merged in PR #57) — a plain
  tier label only ("Background," "Watchlist"/"Review needed" per
  whatever exact label the live UI already uses for `WATCHLIST`), never
  a rule name, never a reason code, never the word "gate."
- **High Signal**: rendered exactly as today for every item that
  legitimately earns it (Fixture F unchanged).
- **How this stays enforced going forward**: `materiality_reasons`/
  `AdmissionDecision.reason` remain exactly what they are today —
  internal, machine-readable diagnostic strings, consumed only by
  logs, the dry-run scripts (`daily_news_jp_kr_dry_run.py`, `daily_
  news_market_news_dry_run.py`), and test assertions. **No UI
  component reads either field today** (confirmed by grepping `src/
  ui/` for `materiality_reasons`/`AdmissionDecision` — no match) —
  this document's own implementation plan (§5) explicitly forbids
  introducing the first such reference; any future card-level "why is
  this here" affordance must render a separately-curated, human-
  written label mapped from the tier alone (e.g. `HIGH_SIGNAL` →
  "High Signal"), never the internal reason string verbatim.

---

## 4. Regression fixture plan

Every new rule gets a negative control (proves the rule fires) and a
positive control (proves the rule does not over-fire on adjacent,
legitimate content). The 6 specifically-requested controls below map
directly onto the task's own list:

| Control | Fixture shape | Proves |
|---|---|---|
| **Negative — Rule 1** | Fixture A itself | Rumor-hedge language suppresses Gate C/D; item does not reach High Signal |
| **Positive — Rule 1 (task-required)** | A genuine Intel primary-source event — e.g. "Intel Reports Third Quarter 2026 Financial Results" (real earnings language, `_UNAMBIGUOUS_ACTUAL_RESULT_KEYWORDS` hit, Gate A2) or an Intel 8-K/official IR capacity announcement with a real $ or unit figure (Gate B) | A confirmed, primary-source Intel disclosure remains fully eligible for High Signal — rumor suppression never touches Gates A/A2/A4/B/B2 |
| **Negative — Rule 2** | Fixture B itself | Marketing-designation content is rejected absent anchor evidence |
| **Positive — Rule 2 (task-required)** | A real, material AWS story — e.g. "AWS Signs $500M Multi-Year Cloud Contract With [Named Customer]" or "AWS Deploys 2 GW of New Data Center Capacity" (real contract/capacity language + a qualifying number, Gate B) | A genuinely material AWS event still reaches admission as `company_subject:Amazon.com, Inc.` and, via Rule 6, attaches to Amazon specifically because Amazon (via AWS) is the actual disclosed actor, not a third party |
| **Negative — Rule 3** | Fixture C itself | Personnel/governance announcements are rejected absent anchor evidence, even from a bypass-eligible source |
| **Positive — Rule 3 (task-required)** | A space-sector executive change tied to a funded program — e.g. "Space Force Names New Program Executive Officer for $1.2B Resilient GPS Program" (a real dollar figure + "program," satisfying Gate B) | A material, funded-program-linked leadership change remains eligible — the personnel-phrase rejection defers to `anchor_evidence` exactly like Rules 1/2 |
| **Negative — Rule 4** | Fixture D itself | Interview-format Gate C suppression keeps a no-new-fact industry-narrative interview out of High Signal |
| **Positive — Rule 4 (task-required)** | An interview containing a measurable, attributable infrastructure commitment — e.g. "In an interview, [Named CEO] disclosed a $300M expansion of the company's Ohio fiber route, adding 40,000 route-miles by 2027" (a real $ figure + "expansion," Gate B fires independent of interview-format suppression, since Rule 4 only suppresses Gate C, never Gate B) | A genuinely newsworthy interview remains eligible for High Signal — Rule 4's suppression is scoped to Gate C only, by design |
| **Negative — Rule 5** | Fixture E itself | A first-party product launch with only boilerplate AI framing (no proximate anchor) is capped at Background/Watchlist |
| **Positive — Rule 5 (task-required)** | A first-party Meta announcement with real evidence — e.g. "Meta One Premium reaches 50 million paid subscribers, adding an estimated $2B in annualized subscription revenue" (a real, qualifying `million`/`$...B` figure co-occurring in the same sentence as the disclosure) | Qualifies directly via Gate B (unaffected by Rule 5a's sentence-proximity tightening, since the number and the disclosure ARE in the same sentence here) |
| **Positive control — Rule 6 composition** | Fixture F itself, re-run through the full rule set | NVIDIA's quantified GW/MW figure (Gate B, same sentence as "capacity"/"investment") is completely unaffected by any of Rules 1-5 (no rumor-hedge, no marketing-designation, no personnel-announcement, no interview-format, no boilerplate-only framing in this fixture) — the **key regression check this document is built around**, confirming the new rules are narrow enough not to suppress genuine first-party AI-infrastructure/power/cooling/grid material |

Every negative-control fixture above must also be checked against the
**existing** regression suite already covering the false positives
Rules 1-6 do *not* target (Nintendo/Amazon retail, Oracle-of-Omaha,
CEVA/CEVA-Logistics, TOWA/VTuber, the Asus RTX 5090 consumer-retail
case, the Meta/Facebook localized-duplicate case) — none of the six new
rules touch `_CONSUMER_FORMAT_PHRASES`, `_AMBIGUOUS_ALIAS_COMPANIES`,
`_RIVAL_ENTITY_EXCLUSION_PHRASES`, or `_HISTORICAL_BACKGROUND_
PATTERNS`, so every existing test in `tests/test_daily_news_editorial_
admission.py` and `tests/test_daily_news_materiality_classification.py`
is expected to keep passing unchanged.

---

## 5. Implementation plan

### Exact production files a later implementation would touch

| File | Change |
|---|---|
| `src/data_access/daily_news/editorial_admission.py` | Add `_RUMOR_HEDGE_PATTERNS`/rumor-hedge check (Rule 1's admission-layer downgrade signal), `_MARKETING_DESIGNATION_PHRASES` + exception logic (Rule 2, structurally parallel to `_CONSUMER_FORMAT_PHRASES`), `_PERSONNEL_ANNOUNCEMENT_PHRASES` + exception logic (Rule 3), add `identified_companies: tuple[str, ...]` field to `AdmissionDecision` and populate it from the already-computed `_identified_subject_companies()` result (Rule 6). |
| `src/data_access/daily_news/materiality_classification.py` | Add rumor-hedge suppression to Gates C/D (Rule 1's materiality-layer half), add `_INTERVIEW_FORMAT_PATTERNS` + Gate C suppression (Rule 4), add sentence-proximity requirement to Gate C's taxonomy/anchor pairing (Rule 5a), add `_PROJECTION_LANGUAGE_PATTERNS` non-suppressing annotation (Rule 5b). |
| `src/data_access/daily_news/editorial_pipeline.py` | Change the `EditorialStory(matched_companies=...)` construction (around today's line 357-364) to use `admission.identified_companies` when the admission reason is `company_subject:...`, an empty tuple when it is `theme_subject:...` (Rule 6's other half). Add the Rule 3 personnel-announcement check as an unconditional pre-bypass step for `_SPACEFORCE_SOURCE_ID` specifically (the one source-level wiring change this document proposes, narrowly scoped). |

### Exact test files a later implementation would touch

| File | Change |
|---|---|
| `tests/test_daily_news_editorial_admission.py` | New fixtures for Rules 1/2/3/6 (rumor-hedge rejection + primary-source exception, marketing-designation rejection + material-AWS-event exception, personnel-announcement rejection + funded-program exception, `identified_companies` field presence and correct narrowing for both `company_subject` and `theme_subject` outcomes). |
| `tests/test_daily_news_materiality_classification.py` | New fixtures for Rules 1/4/5a/5b (rumor-hedge Gate C/D suppression, interview-format Gate C suppression + Gate B non-suppression, sentence-proximity Gate C tightening, measured-vs-projected annotation) plus the Fixture-F-shaped positive-control regression test (a real GW/MW figure still reaches `HIGH_SIGNAL` unchanged). |
| `tests/test_daily_news_editorial_pipeline.py` (or equivalent existing pipeline test file) | New end-to-end fixture proving a Space Force item shaped like Fixture C is rejected despite the source's own broader bypass, and a Space Force item shaped like the Rule 3 positive control (funded program + personnel change) is still admitted. |
| `tests/test_daily_news_scope_guard.py` (if it exists / the established scope-guard pattern for this package) | Re-run only — confirms no new forbidden import was introduced by any of the above (this package's own established discipline, unchanged by this design). |

### Non-goals, explicit

- **No source-expansion work** — no new `DailyNewsSourceEntry`, no
  change to any registry batch, no touching the Cohort 1 Daily News
  design work from the prior session.
- **No taxonomy migration** — `TAXONOMY_KEYWORDS`/`THEME_KEYWORDS`'s
  own phrase lists are read, never edited, by any rule in this
  document (the `space` taxonomy-bucket gap noted in Fixture C's trace
  is named as an observation, not proposed as a fix here — a future,
  separately-scoped decision).
- **No relationship-graph work** — nothing in §2 touches any entity-
  relationship, supply-chain, or company-linkage data structure beyond
  the existing `matched_companies`/`identified_companies` distinction
  already native to this module.
- **No broad media ban** — every new admission rule is a narrow,
  curated phrase/pattern list with an explicit, evidence-gated
  exception clause, never a source- or publisher-level exclusion; no
  existing source (Tom's Hardware, Data Center Frontier, PR Newswire,
  Space Force, or any other) is proposed for removal or demotion as a
  *source* — only specific *content shapes* from any source are
  targeted.
- **No change to EDGAR/EDINET/DART filing behavior** — every file
  touched in §5 lives under `src/data_access/daily_news/`; no filing-
  adapter, rule, or promotion-logic file is referenced anywhere in
  this document.

---

## 6. Validation constraints — how this document honored them

- No claim of "verified," "contract exists," or "company is related"
  is made anywhere above without the underlying evidence being named
  explicitly (a direct code read, a live web search/fetch this
  session, or an explicit "this session could not confirm X" caveat
  where the real stored excerpt was unavailable — Fixtures B and D's
  exact body text, and Fixture E's exact triggering keyword).
- Live item content was used wherever independently retrievable this
  session (Fixtures A, D-adjacent, E, F) via read-only web search/
  fetch — nothing was written to any repository file, cache, or data
  store in the process.
- Every primary-source disclosure and concrete economic/capacity/
  contractual/regulatory item named as a positive control in §4 is
  designed to remain fully eligible under every proposed rule — none
  of the six rules narrows an existing hard gate (A/A2/A4/B/B2); all
  six either add a new, narrowly-scoped exclusion category with its
  own evidence-gated exception, or tighten Gate C/D specifically
  (the two "no number required" gates, which this project's own prior
  calibration work already identified as the most over-permissive
  pair — see `materiality_classification.py`'s own "Deployment
  qualification" and "Survey statistics" calibration notes, which this
  document's Rules 1/4/5a extend using the exact same proven pattern).

---

## Files read / created

**Read this session (all read-only)**: `src/data_access/daily_news/
materiality_classification.py` (full file), `src/data_access/daily_
news/editorial_admission.py` (full file), `src/data_access/daily_news/
editorial_matching.py` (full file), `src/data_access/daily_news/
editorial_pipeline.py` (the qualifying-loop and story-construction
call sites), `src/data_access/daily_news/daily_news_pipeline.py`
(grepped, confirming Lane A has no admission-gate equivalent), `git
log`/`git status` (branch/state confirmation). Live external
verification this session (read-only web search/fetch, nothing written
to any repository file): the real Tom's Hardware Intel/Nova Lake-S
article, the real Meta One launch coverage (pricing tiers, capex
framing), the real NVIDIA "megawatts to tokens"/AI-factory content
family, and the real Scott Bergs/Kirkwood IG/Data Center Frontier
context (confirming Kirkwood IG's private, Blue Owl-backed status).

**Not touched**: all 9 pre-existing untracked artifacts (`design/
COVERAGE_EXPANSION_CANDIDATES_TIER1_2026_09_15.md`, `design/COVERAGE_
EXPANSION_FIRST10_REGISTRY_READINESS_2026_09_15.md`, `design/COVERAGE_
TAXONOMY_AUDIT_2026_09_15.md`, `design/DAILY_NEWS_SOURCE_CENSUS_AND_
COHORT1_DESIGN_2026_09_15.md`, `design/RESEARCH_THESES_ACTIVE_
DORMANT.md`, `design/THEMATIC_COVERAGE_UNIVERSE_AUDIT_2026_09_15.md`,
`design/TIER1_COHORT1_REGISTRY_READINESS_2026_09_15.md`, `design/
TIER1_COHORT1_TRACKED_COMPANIES_IMPLEMENTATION_DESIGN_2026_09_15.md`,
`tests/test_radar_status.py`).

**Created**: `design/SIGNALS_ADMISSION_MATERIALITY_CALIBRATION_2026_
09_15.md` (this document — untracked, not staged, not committed).

No code, config, schema, test, worker, or production-data file was
modified. No git operation beyond read-only status/log checks was
performed. Nothing was staged, committed, pushed, merged, or deployed.
