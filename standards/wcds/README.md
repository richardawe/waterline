# Waterline Credit Data Standard (WCDS) v0.1

This is the **Tier 2 (Deal Loan-Tape)** schema referenced in
[`docs/loan-database-plan.md`](../../docs/loan-database-plan.md) — the canonical,
loan-level data format lenders map their loan tape to when engaging with Waterline,
whether or not a deal follows. It's presented publicly at
[`standard.html`](../../standard.html) (linked from the site nav as "Data Standard").

## Files

| File | What it is |
|---|---|
| `WCDS_v0.1_Full_Specification.docx` | The full spec: 18 canonical entities, field dictionary, key rules, canonical relationships, core codebook, 30-rule deterministic validation rulebook, reconciliation standard, CRMS mapping matrix, data exchange formats, provenance/security/versioning requirements. |
| `WCDS_v0.1_Anonymised_Lender_Datasets.zip` | The original upload — 3 synthetic lender loan tapes (Consumer, SME, mixed MFB) plus the dataset manifest, zipped. |
| `samples/` | The same 3 CSVs and manifest, extracted — this is what `standard.html`'s live validator demo fetches directly, so it always reflects whatever is in this directory. |

## What the samples actually are

Per `samples/WCDS_dataset_manifest.json`: 3,050 synthetic facilities total (1,200 +
850 + 1,000), no real customer/lender/BVN/TIN data, `borrower_external_id` values
are placeholders. Each file has **5 deliberate validation exceptions** seeded in for
testing — `standard.html`'s live demo runs a 9-rule subset of the full rulebook
against all three files in-browser and surfaces them. Confirmed by independently
re-deriving the exceptions against the spec's rulebook (§8) rather than trusting the
manifest's count blindly:

- **Lender A (Consumer):** negative `outstanding_principal`, negative `days_past_due`
  with an inconsistent bucket, `default_flag=true` with no `default_date`, a `CLOSED`
  facility with nonzero exposure.
- **Lender B (SME):** an invalid currency code, an interest rate encoded as `26.0`
  instead of `0.26`, a bucket/DPD mismatch, an `origination_date` after `maturity_date`.
- **Lender C (MFB):** `first_disbursement_date`/`snapshot_date` before `origination_date`
  on one facility, three facilities with `default_date` before `origination_date`, and
  two facilities sharing the same `crms_credit_id` (a duplicate-key violation).

## Status

Working draft, not an official CBN standard — the spec's own §18 is explicit about
what v0.1 deliberately does not claim (not a CBN standard, not a replacement for the
CBN Offline Validator, doesn't prescribe an IFRS 9 model, doesn't require borrower PII
disclosure to investors). Roadmap to v1.0 is in §19 of the spec.
