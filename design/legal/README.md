# Legal page drafts — read this first

These four files are **drafts for you to edit**, not finished pages, and
they are deliberately **not wired into the app**. Nothing here renders
anywhere yet.

## I am not a lawyer and these are not legal advice

They are a structured starting point so that a lawyer is reviewing a
document rather than a blank page, which is cheaper and faster than the
alternative. Every place a real decision is needed is marked
`[DECIDE: ...]`. There are 30-odd of them and none is rhetorical.

## Why this needs a real review, specifically

Two things make this more than a boilerplate exercise:

1. **You are about to charge money for information about securities.**
   EevaResearch is careful never to give advice — the existing
   Disclaimer page is unusually honest about it — but "paid research
   service" is a category Canadian securities regulators have opinions
   about. Whether you need to register, and what you may say in
   marketing, is a question for someone qualified, and the answer shapes
   the Terms. Do not guess it from how other products word their pages.
2. **You will hold personal data and take payment from businesses.**
   That brings PIPEDA (and Quebec's Law 25 if you have users there) and
   consumer-protection rules about automatic renewal into scope.

## Order of work

1. Fill in every `[DECIDE: ...]`. Leave one unresolved and the document
   says something you have not agreed to.
2. Have a lawyer review all four together — they cross-reference.
3. Then wire them up as pages. The mechanics are the easy part:
   `src/ui/pages/disclaimer.py` is the pattern, and `app.py`'s
   `_build_pages()` is where a page is registered.

Stripe will ask to see Terms and a refund policy during account review,
so step 1 is on the critical path for billing, not after it.

## The voice to keep

`src/ui/pages/disclaimer.py` already sets it: plain, specific, and
willing to say what the product does *not* do. These drafts match it on
purpose. A legal page that reads as evasive undercuts a product whose
whole pitch is evidence and honest labelling — and a reader who has just
read the Disclaimer will notice the difference immediately.

Resist the urge to add the usual defensive padding during review. Say
less, and mean it.

| File | Covers |
|---|---|
| `TERMS_OF_SERVICE_DRAFT.md` | The agreement: access, payment, acceptable use, liability, termination |
| `PRIVACY_POLICY_DRAFT.md` | What is collected, why, who it goes to, how long it is kept |
| `NOT_INVESTMENT_ADVICE_DRAFT.md` | The standalone advice disclaimer, strengthened for a paid tier |
| `REFUND_AND_CANCELLATION_DRAFT.md` | Cancellation, refunds, renewal, price changes |
