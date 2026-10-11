# Waterline Collections — Credit Bureau Reporting (planned, not built)

Status: **plan only, approved in principle on 2026-10-11. No code.** Build
starts only after the Phase 0 gates below are cleared.

## What it is

An opt-in, paid add-on: Waterline reports long-overdue, undisputed balances
to a licensed Nigerian credit bureau (CRC, FirstCentral, CreditRegistry) **as
the client's agent**. It gives recovery "bite" through one factual notice,
not threats.

- **Segments:** schools first; BNPL providers and equipment lessors when those
  segments open. **Not** estates (owner decision). **Not** lenders, who
  already report to bureaus themselves.
- **Pricing (decided):** a fee to the client **per report filed**. "Settled"
  and correction updates are included. Nothing is ever charged to debtors.
  Set the amount once the bureaus quote their costs.

## Legal basis (research, 2026-10-11, to be confirmed by counsel)

- **Credit Reporting Act 2017 (CRA):**
  - The creditor is the "credit information provider". This reportedly
    includes suppliers of services on a deferred/instalment basis and
    leasing companies.
  - Reporting reportedly needs no prior consent (s.9(2)(a)).
  - Disputes must be resolved within 10 working days, then the CBN has 10
    more.
  - False information is an offence. Data is retained for at least 6 years.
- **CBN Credit Bureau Guidelines (Nov 2013):** reportedly require the data
  provider's prior authorisation, which conflicts with CRA s.9(2)(a).
  Design for both:
  - a reporting clause in clients' fee or customer terms for new debts;
  - written notice for debts that predate the clause.
- **Permitted reasons for bureau searches (CRA s.7(2)):** no explicit "debt
  recovery/tracing" purpose found. s.7(2)(m) lets the CBN add one. Bureau
  lookups to *find* a debtor are therefore unconfirmed.
- **FCCPC Digital Lending Regulations 2025** (covers BNPL):
  - Reg 25(1)(c): lenders must furnish data to recognised bureaus when
    directed.
  - Complaints must be resolved within 24h, 48h at most.
  - Penalties up to ₦100m or 1% of turnover.
- **NDPA 2023:**
  - Handling NIN/BVN needs a lawful basis and a data protection impact
    assessment.
  - Privacy notices for debtors and clients must be updated.

## The flow (school example)

1. **Client opts in** and signs an addendum confirming:
   - balances are accurate and owed;
   - its fee terms carry the reporting clause (we supply template wording);
   - it will help with identification.
2. **Eligible balances only:** older than 60 days, above a minimum amount,
   not disputed, not withdrawn.
3. **Day 0 (payment page):** one calm line, shown only if the account is
   genuinely reportable:
   > *Greenfield School reports long-overdue balances to licensed credit
   > bureaus. Paying, agreeing a payment plan, or telling us if this is wrong
   > keeps it off your credit record.*
4. **About day 7:** one reminder carries the line *"…clearing this balance,
   or agreeing a plan, keeps your credit record clear."* Once only, never in
   every message.
5. **Identity confirmed** (see below) before any final notice.
6. **About day 45: formal final notice** by WhatsApp and email. It states the
   amount, a report date at least 30 days away, the masked ID, and the three
   ways out (pay, plan, dispute):
   > *…Greenfield School will report it to a licensed credit bureau, linked to
   > your name, phone number and National Identification Number (ending
   > •••4821). This can affect your future access to credit. Pay, choose a
   > plan, or tell us it's wrong: … Reply STOP to stop messages.*
7. **About day 75: report queue.**
   - The client confirms that day.
   - Ops submit in the bureau's format (portal first, API later).
   - The per-report fee is invoiced.
8. **After reporting:**
   - **Paid:** update to "settled" within days.
   - **Disputed:** hold or withdraw the report, resolve within statutory
     time.
   - **Plan kept:** no further adverse updates.

BNPL and leasing follow the same flow, with two differences:
- the reporting clause is already in their customer agreements, and BVN/NIN
  come with the upload;
- complaint clocks are 24–48h.

## Identifying the debtor (NIN/BVN)

Owner decision: don't skip accounts without IDs. Waterline helps identify the
person, and the final notice names the ID (masked) to prompt payment or a
dispute. **Lawful routes only**, in this order:

1. **The client's own records** (admission forms, KYC). These go into new
   optional, encrypted ID fields used only for reporting.
2. **Bureau-side matching.** We send name, phone, address and date of birth.
   The bureau matches and attaches identifiers on its side, so we never
   receive or store the BVN. Needs a bureau that offers this, and counsel's
   sign-off that it's a permitted reason.
3. **The debtor confirms** their details on the payment page (for example,
   to set up a plan).

**Never:**
- buy or look up BVN/NIN from data sellers or "lookup" services;
- search a bureau without a confirmed permitted reason;
- put a full NIN/BVN in any message (show the last 4 digits only).

## Hard rules (to enforce in code later)

- **When the notice may show:** only if the client has opted in, a live
  bureau arrangement exists, and the account is eligible. A warning that
  can't be carried out is a misleading threat under the FCCPA.
- **Never report an account that is:**
  - disputed, or in a plan that's being kept;
  - withdrawn, or below the minimum amount;
  - without a final notice at least 30 days old;
  - without an identity match;
  - not confirmed by the client that day.
- **Wording:** "credit bureau" is allowed only in the approved notice
  templates. *blacklist / mark / police / court* stay banned, and the child is
  never mentioned.
- **Audit:** every notice, ID match, confirmation, submission, update and
  dispute goes into `cl_audit_log`.

## Build plan

**Phase 0: gates (about 3–6 weeks, no code)**

- **Bureaus (draft email ready):**
  - Do they accept schools, BNPL providers and lessors as providers?
  - Can one agent submit for many clients?
  - What fields are needed to match a record? Do they match on their side
    without disclosing BVN?
  - Default-only files, or full monthly reporting?
  - Update and dispute process, data format (Common Data Template),
    API/portal, fees.
- **Lawyer (brief ready):**
  - Are fees, BNPL balances and lease rentals reportable?
  - Our agent liability.
  - Consent vs notice for old and new debts.
  - Identity matching and permitted reasons.
  - NDPA impact assessment.
  - FCCPA review of the notice wording.
  - Contracts.
- **Paperwork:** client addendum, school fee-terms clause, debtor notices,
  privacy policy and DPA updates.

**Phase 1: schools MVP (about 2–3 weeks once the gates are green)**

- Opt-in per client and the addendum, stored with a timestamp.
- Encrypted optional ID fields (NIN/BVN/date of birth). A separate, minimal
  path, not part of the general upload.
- Identity-match workflow (client records, bureau matching, debtor
  confirmation) with a masked display.
- Eligibility engine, the payment-page line and the once-only reminder line.
- Final-notice template and its 30-day clock.
- Ops report queue with client confirmation, and export in the bureau's
  format.
- Settled and corrected updates, dispute hold and withdraw.
- Per-report invoice line, audit coverage and tests.

**Phase 2: BNPL and leasing**

- New segments with their own columns (agreement reference, BVN, instalment
  schedule, asset).
- 24–48h complaint clocks.
- API submission.
- Optional full-book monthly reporting.

## Open decisions

- The per-report price, once bureau costs are known.
- The minimum amount and minimum age of a debt before it can be reported.
- Which bureau(s) to start with.
