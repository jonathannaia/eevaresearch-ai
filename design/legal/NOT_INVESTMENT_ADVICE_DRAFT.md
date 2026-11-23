# Not Investment Advice — DRAFT

> **Draft for review. Not legal advice. Not published.**
> Last revised: 2026-11-23.

## What this is for

The product already has a Disclaimer page
(`src/ui/pages/disclaimer.py`) and it is good — specific, honest, and
willing to say what the tool does not do. **Do not replace it.**

This document covers what changes when people start paying, which is
narrower than it sounds but matters more than it sounds.

## Why a paid tier changes the exposure

Three things, none of them hypothetical:

1. **A paying customer who loses money has a stronger grievance** than a
   free beta user with the same loss. Not necessarily a stronger legal
   claim, but a stronger inclination to make one.
2. **Charging for research invites the question of whether you are
   selling advice.** You are not, and the product is built so that you
   are visibly not. But the question gets asked of paid services and not
   of free ones, and the answer needs to be ready before it is asked,
   not after. [DECIDE: this is the single question to put to a
   securities lawyer first. Whether a paid filing-intelligence
   subscription triggers any registration requirement in your province
   determines what you may say in marketing, which determines your
   landing page. Do not infer the answer from how competitors word
   theirs — some of them are wrong, and some are registered]
3. **A signal track record is performance advertising.** The moment you
   publish "our signal preceded this move", you have moved from
   describing a tool to making a claim about results. That is the most
   regulated thing in the whole roadmap. See the note at the end.

## Additions to the existing Disclaimer page

Keep every section that is there. Add these.

### Paid access does not change what this is

> Paying for EevaResearch buys you access to a research tool. It does
> not buy advice, a recommendation, or an opinion about what you should
> do. We are not a registered investment adviser, dealer or analyst, and
> no payment creates an advisory or fiduciary relationship.
>
> Nothing here is tailored to your circumstances, your portfolio, your
> tax position or your risk tolerance. We do not know any of those and
> do not ask.

### You are paying for detection, not for being right

> EevaResearch finds and organises disclosures. It does not predict what
> happens next and makes no claim about the future performance of any
> company or security.
>
> Detection is automated and is wrong sometimes — the sections above say
> exactly how. A paid tier gets you more coverage and more features. It
> does not get you a guarantee of accuracy, and no subscription tier is
> more correct than another.

### No past-performance claim

> Where EevaResearch shows historical signals, it is showing what was
> published and when. That is a record of this tool's output, not a
> track record of investment returns, and it is not a basis for
> expecting any future result.
>
> We do not claim that a signal caused, predicted or preceded any price
> movement.

[DECIDE: that last paragraph is the one to get reviewed word by word
before the track-record feature ships. The feature is a good idea and
the data honestly supports a neutral, timestamped record of what was
published. The risk is entirely in the framing, and the framing is
where it will be judged — including the framing in your marketing, which
these terms cannot fix]

### Jurisdiction

> EevaResearch is operated from Canada and is not directed at, and not
> offered to, persons in any jurisdiction where doing so would require
> registration we do not hold.

[DECIDE: do you want to restrict by jurisdiction at all? Blocking a
country you have never heard from is cheap insurance; blocking one with
real customers is not. A geo-restriction you state but do not enforce is
worse than none, so decide whether this is a statement or a control]

## Where this has to appear

| Place | Treatment |
|---|---|
| Disclaimer page | Full text, added to the existing sections |
| Terms of Service | Section 2 cross-references it; keep the two consistent |
| Checkout | A short acknowledgement before payment — [DECIDE: a tick box, or a line of text with a link? A tick box is more defensible and adds friction at the worst possible moment. A lawyer will say tick box] |
| Footer | The existing "Not investment advice" line already covers this; leave it |
| Landing page | [DECIDE: the marketing copy is what a regulator would read first, and it is the thing most likely to overclaim. Review it against this document at the same time] |
