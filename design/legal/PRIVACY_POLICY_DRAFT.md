# Privacy Policy — DRAFT

> **Draft for review. Not legal advice. Not published.**
> Last revised: 2026-11-23.

**Effective date:** [DECIDE: date]

This explains what EevaResearch collects, why, and what happens to it.
It is written against what the application actually stores, which was
read out of the schema rather than assumed — so if you change the
schema, change this.

Operated by [DECIDE: legal entity name], [DECIDE: address], Canada.

---

## What we collect

### When you sign in

Sign-in is through Google. We receive, and store:

| Field | Why |
|---|---|
| Email address | Identifies your account; the key everything else is stored against |
| Display name | Shown in the interface; taken only from Google's own `name` claim |
| First sign-in timestamp | Account administration |
| Last sign-in timestamp | Account administration |
| Sign-in count | Account administration |

**We do not store your Google password, and we never see it.** We do not
store Google access or refresh tokens beyond the session.

### When you ask for access

If you submit the access-request form we store what you typed: your
stated reason for wanting access, and optionally your organisation and
role. These are self-reported, unverified, and used only to decide your
request.

### When access is granted or changed

We keep a record of your access status, tier, when it was decided and by
whom, and when it expires. Every change to it is appended to an
audit log we do not edit or delete, so that "why does this account have
access" is always answerable.

### When you submit feedback

The feedback form stores your answers alongside your email and display
name.

### What we deliberately do not collect

No IP addresses. No device or browser fingerprints. No page-view or
clickstream history. No cross-site tracking, advertising identifiers or
third-party analytics. No payment card details — those go directly to
Stripe and never reach us.

This is a design constraint in the code, not a policy promise laid over
a system that could do otherwise: the tables have no columns for any of
it. [DECIDE: if you later add analytics — even self-hosted — this
section must change first, and it is the section a privacy-conscious
reader will check. Changing it quietly would be the worst version of
this]

## Why we collect it

- To let you sign in and to control who has access
- To decide access requests
- To take payment for paid access
- To answer your support messages
- To keep a record of access decisions, which we need for our own
  accountability

We do not sell your personal information. We do not share it for
advertising. We do not use it to train any model.

## Who else sees it

| Processor | What they get | Why |
|---|---|---|
| Google | Your email and name, as part of sign-in | Authentication |
| Stripe | Your email, and the payment details you give them directly | Payment processing |
| Render | Everything in our database, as our hosting provider | Hosting |
| [DECIDE: DeepL] | **Nothing about you.** Only filing and headline text for translation | Translation |

[DECIDE: Render and Stripe both process data outside Canada. You must
say so, and say which countries — Render's region here is Ohio, United
States. Cross-border transfer disclosure is a PIPEDA requirement, not
optional, and Quebec's Law 25 is stricter if you have users there]

## How long we keep it

| Data | Retained |
|---|---|
| Account record | [DECIDE: while your account exists, plus how long after?] |
| Access audit log | [DECIDE: this is the hard one. The log is append-only by design, which is the point of it — but "we never delete it" conflicts with a deletion request. The usual resolution is to keep the decision record and strip the identifier. Decide with your lawyer and then make the code match] |
| Access request text | [DECIDE: delete after a decision, or keep?] |
| Feedback | [DECIDE] |
| Payment records | Retained as long as tax law requires — [DECIDE: six years in Canada? confirm with your accountant] |

## Your rights

You can ask us to:

- tell you what we hold about you
- correct it
- delete it
- send you a copy

Email [DECIDE: privacy contact address]. We will respond within
[DECIDE: 30 days is the PIPEDA expectation].

[DECIDE: deletion needs a real answer, not a sentence. Today there is no
self-service deletion and no admin tooling for it — honouring a request
means someone running SQL by hand against production. That is workable
at your current scale and will not be. Either say "email us and we will
do it manually" and mean it, or build the tooling. Do not imply a
self-service control that does not exist]

## Cookies

We use only what is needed to keep you signed in. No advertising or
tracking cookies, and no third-party analytics cookies.

[DECIDE: confirm this against what Streamlit and the Google OAuth flow
actually set. It is accurate as far as the application's own code goes —
no analytics are installed — but the framework and the auth flow set
their own cookies and you should be able to name them if asked]

## Children

EevaResearch is not for anyone under [DECIDE: 18? the age in your ToS —
keep the two documents consistent] and we do not knowingly collect their
information.

## Changes

If we change this materially we will notify you by
[DECIDE: email? in-app?] before it takes effect.

## Contact

[DECIDE: privacy contact address]

[DECIDE: do you need to name a formal Privacy Officer? PIPEDA expects an
accountable individual to be identified. For a one-person company that
is you, but it has to be stated]
