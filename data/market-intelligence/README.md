# Market Intelligence Layer — data files

This is the **Tier 1 (Market Intelligence)** database described in
[`docs/loan-database-plan.md`](../../docs/loan-database-plan.md): institution/portfolio-grain
facts pulled from public sources (annual reports, NGX/CBN/SEC filings, rating agency summaries,
press releases, FCCPC registry, lender websites). It is explicitly **not** loan-level data —
see the plan doc for why that distinction matters and how Tier 2 (deal loan-tapes) differs.

Stored as flat JSON files for now, one per entity type, matching the schema in the plan doc.
Each is designed to become a Postgres table later with minimal transformation — `institution_id`
is the join key everywhere.

## Files

| File | Grain | Status |
|---|---|---|
| `institutions.json` | one row per lender | **Seeded** — 42 CBN-licensed DMBs/merchant banks/holdcos, verified against a CBN circular. MFBs, fintech lenders, and FCCPC-registered digital lenders are being added as research completes. |
| `portfolio_snapshots.json` | institution × period | **Partial** — populated only where a real, cited figure was found. Most institutions are not yet covered; this is expected at this stage, not a bug. |
| `portfolio_mix.json` | institution × period × dimension | **Empty/pending** — sector/geography/product breakdowns require deeper per-institution annual-report extraction than fits an initial pass. |
| `industry_aggregates.json` | period × segment | **Partial** — CBN sector-wide figures (total credit, NPL ratio). |
| `rating_actions.json` | institution × date | **Pending/partial** — Agusto & Co / GCR / DataPro public rating summaries. |
| `capital_market_instruments.json` | one row per bond/CP/note | **Not started** — SEC prospectus/bond-filing research not yet run. |
| `disbursement_metrics.json` | institution × period | **Not started** — investor deck/press-release volumes not yet run. |
| `product_snapshots.json` | institution × product | **Not started** — lender website/app scraping not yet run. |

## Provenance convention

Every fact-bearing record carries a `source` object:

```json
"source": {
  "source_name": "human-readable description of the document",
  "source_url": "exact URL fetched",
  "retrieved_date": "YYYY-MM-DD"
}
```

**No number in these files should exist without one.** If a figure couldn't be verified from a
real, fetchable source, it was left out rather than estimated — an empty field is honest, a
guessed one is a liability the first time an investor asks "where did this come from?"

`confidence` (`high` / `medium` / `low`) reflects source directness: `high` = primary document
(bank's own annual report/IR page, CBN/NGX/SEC filing); `medium` = reputable secondary source
explicitly citing and reproducing a primary figure; `low` = secondary source without a clear
primary citation, or a figure with unit/period ambiguity worth double-checking before use.

## Known gaps / next steps

- Coverage today is the ~7 largest NGX-listed banking groups plus the full CBN-licensed bank
  universe (names only, no financials yet for most). MFBs, fintech lenders, and leasing/asset
  finance companies — Waterline's actual target originator segment — are the priority to extend
  next, not the large banks (large banks are useful as market benchmarks, but are not likely
  Waterline deal counterparties).
- `portfolio_mix`, `capital_market_instruments`, `disbursement_metrics`, and `product_snapshots`
  are unstarted. Per the build plan, these are lower-priority "enrichment" sources — sequence
  them after core coverage of target-segment institutions is solid.
- Every record here should be treated as **as-of the `retrieved_date`**, not live. Before using
  any figure in an investor-facing document, re-check it against the cited source if more than a
  few months old.
- Migration path: each JSON array maps directly to the Postgres tables in
  `docs/loan-database-plan.md` §1.1. When migrating, keep the `source` object as the seed for the
  `provenance` table rather than flattening it away.
