"""LLM-assisted comparison of two comparable corporate disclosures.

Pure, Streamlit-free and serializable — the foundation for the planned
customer-facing API, not an authorization to expose one. See
contracts.py for the typed input/output, provider.py for the replaceable
model boundary, and service.py for the order in which refusals, budget,
scrubbing and validation are applied.

--- Supported scope ---

The component itself is source-agnostic: it consumes evidence plus the
comparability facts `prior_disclosure_comparison` already computes, and
never fetches or parses anything. What it can say is bounded three ways:

  * every statement is scoped to the two excerpts (`ClaimScope` has one
    value, so a filing-level claim is not representable);
  * the strongest absence claim is `NO_DIFFERENCE_IN_COMPARED_FIELDS`,
    which must name the fields it compared and is licensed only by
    explicit per-side `FieldEvidence` — field identity, extracted value,
    the exact supporting span, and any qualifications;
  * nothing may be narrated as a revision, increase or decrease unless
    the request carries `RelationshipEvidence`: a verbatim span from one
    of the excerpts that is not merely the issuer, the form type, a date
    or a rule category. Otherwise the comparison is `SIDE_BY_SIDE` and
    `evidenced_changes` must be empty;
  * comparability status is an input gate, never a claim — see
    contracts.py for what `ComparisonStatus` does and does not
    establish.

--- What the local evidence actually supports (measured 2026-11-03) ---

Re-measuring the local pilot cache against the rules above changed the
earlier plan, so the earlier note is corrected rather than kept:

**The Samsung major-shareholder pair is NOT usable as the first
experiment.** Both filings (`최대주주등소유주식변동신고서`, rcept
`20260721801260` and `20260731801296`) are present with
`extraction_state = Extracted`, and all seven named header fields
(회사명, 회사코드, 담당부서명, 담당자명, 보통주식총수, 종류주식총수,
발행주식총수) are locatable at identical offsets on both sides. But the
pipeline classifies both excerpts `excerpt_quality = Likely
boilerplate`, and that classification is correct: the 600-character
window is almost entirely issuer-identification and share-count header,
with the actual per-holder change data only beginning at the cut. So
`ComparisonEvidence.is_usable` is False on both sides and
`precondition_refusal` returns INSUFFICIENT_EVIDENCE before any
provider call. The pair is a good test of the refusal path and cannot
serve as a comparison fixture.

**The usable pair is SK hynix `신규시설투자등` (new facility
investment), rcept `20260722800829` (2026-07-22) and `20260807800613`
(2026-08-07)** — same issuer, same form family, `Usable text` and
`Extracted` on both sides, with genuinely comparable named fields
(투자구분, 투자대상, 투자금액(원), 자기자본(원), 자기자본대비(%)) and
differing values (투자금액 7,093,100,000,000 vs 35,224,600,000,000
against an identical 자기자본 of 120,666,750,890,698).

That pair is also the NEGATIVE CONTROL this component is now gated
against, and it is why the relationship contract exists: the two
filings announce two DIFFERENT capital projects (청주 P&T7 and 용인
2기 Fab), not two versions of one disclosure. The deterministic layer
compares rule categories only, so it reports them comparable, and the
most likely real-model failure on real data is narrating two
independent filings as one revised figure — "investment increased from
7.09 to 35.22 trillion won".

Neither excerpt contains any text linking the two projects, so no
`RelationshipEvidence` can be constructed for them and the request is
`SIDE_BY_SIDE`. The application then refuses that narrative: a payload
carrying `evidenced_changes` is rejected
(`evidenced_change_without_established_revision_relationship`), and a
payload whose prose says "increased" is rejected
(`revision_language_without_established_relationship`). This gates the
APPLICATION, not the model — a model may still produce the false
sentence, and the claim made here is only that such a sentence does not
become an accepted result.

Both excerpts also end mid-sentence at exactly `MAX_EXCERPT_CHARS`
(600), and neither extractor records truncation — `ExtractionResult`
has no such field. Every existing record must therefore be declared
`ExcerptCompleteness.UNKNOWN`, which makes
`NO_DIFFERENCE_IN_COMPARED_FIELDS` reachable on real data only where
per-field evidence shows a span that demonstrably ended inside what was
extracted.

By contrast the local EDGAR cache holds five candidates across five
distinct issuers — no comparable pair at all — and the EDINET cache
holds one candidate with no extracted text.

--- Fixture provenance, stated plainly ---

Those cache files are gitignored local pilot state, not committable
fixtures. Nothing in tests/ depends on them, and every fixture in
tests/test_filing_comparison.py is SYNTHETIC: EDGAR-shaped for the
general contract, and a Latin-script named-field form shaped after the
DART layout for the scoped-absence rules. They demonstrate the CONTRACT
and prove nothing about real-world accuracy.

The synthetic fixtures are labelled as such in the test module and in
the fixture names themselves, and the Latin-script named-field form is
shaped after the DART layout without reproducing any of its text.

Redistribution of the authentic excerpts is NOT cleared and is not
claimed here. Authentic examples are referenced by receipt number and
public source URL so they can be re-fetched by whoever has the right to
do so; their text is deliberately not copied into this repository. The
Korean-to-English translations in the local cache are a separate
derived artifact produced by a paid third-party API and carry their own
unresolved licensing question.
"""
