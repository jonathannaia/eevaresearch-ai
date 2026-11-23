# Refund and Cancellation Policy — DRAFT

> **Draft for review. Not legal advice. Not published.**
> Stripe asks to see this during account review, so it is on the
> critical path for billing rather than after it.
> Last revised: 2026-11-23.

**Effective date:** [DECIDE: date]

---

## Cancelling

You can cancel at any time. [DECIDE: from where? Self-service in the app
is what people expect, and the application has no billing UI yet — so
until it does, this says "email us", and that has to be true and
answered. Promising a self-service cancel button that does not exist is
the single most common way a subscription product generates chargebacks]

When you cancel:

- your access continues to the end of the period you have paid for
- it does not renew
- we do not charge you again

[DECIDE: or do you want cancellation to end access immediately with a
pro-rata refund? Keeping access to the paid period is the norm, is
simpler to operate, and generates fewer disputes]

## Renewal

Monthly and annual subscriptions renew automatically at the end of each
period, at the then-current price, until you cancel.

**We will email you before an annual renewal** at least
[DECIDE: 14? 30? Some provinces have specific advance-notice
requirements for automatic renewal — confirm, because this is exactly
the clause consumer-protection regulators look at] days in advance,
telling you the date and the amount.

[DECIDE: do you want to pre-notify monthly renewals too? Most products
do not. For an expensive monthly plan it reduces disputes]

## Refunds

[DECIDE: this is the section to actually decide rather than copy. Three
workable positions, and the third is probably right for you:

**A. No refunds.** Cleanest to operate, worst for trust, and you will
still refund people in practice when they complain — so the policy will
be a document you routinely ignore.

**B. Unconditional refund within N days.** Easiest to administer and
easiest to say in marketing. 14 days is common; 30 is a strong signal.
Note that you already offer a free trial, which is the usual argument
for a shorter window or none.

**C. Refund on request within N days, plus pro-rata on an annual plan
you cancel mid-term.** The most generous, and the most defensible for a
business customer paying a year up front. Costs you little if the
product is good.]

Our policy:

> [DECIDE: write the chosen policy here in one short paragraph. One
> paragraph. A refund policy a customer has to read twice is a refund
> policy that generates support email]

### When we refund without being asked

- we end your access for a reason other than your breach of the Terms —
  we refund the unused part of the period
- we charged you in error
- a technical failure on our side made the service substantially
  unusable for an extended period
  [DECIDE: define "extended"? Naming a number invites claims at exactly
  that threshold; leaving it vague invites argument. Most small services
  leave it vague deliberately]

### When we do not refund

- you did not use the service during a period you paid for
- you disagree with a signal, a classification or the coverage
- you breached the Terms and we ended your access

## Price changes

We may change prices. An existing subscription keeps its price until the
end of the current period. We will tell you at least
[DECIDE: 30 days?] before a new price applies to your renewal, and you
can cancel before it takes effect.

## Failed payments

If a payment fails we will retry [DECIDE: how many times, over how long?
Stripe's Smart Retries handles this and its defaults are reasonable —
you can simply describe them rather than invent a schedule] and email
you. If it keeps failing, access is suspended rather than ended, and
your data is kept for [DECIDE: 30? 90?] days so you can resume.

## Taxes

Refunds include any tax we charged on the refunded amount.

## How to ask

Email [DECIDE: billing contact address] from your account's email
address. We will respond within [DECIDE: 5 business days?] and, where a
refund is due, issue it to the original payment method.

## Business and invoiced customers

[DECIDE: only needed if you offer invoiced billing. If you do, it needs
its own short section: payment terms, what happens on late payment, and
whether annual invoiced plans are refundable at all. Businesses
negotiate these, so expect this section to be a starting point rather
than a rule]
