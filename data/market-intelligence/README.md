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
| `institutions.json` | one row per lender | **Seeded** — 51 CBN-licensed DMBs/merchant banks/holdcos plus 9 MFB/fintech lenders (LAPO, AB Microfinance, Moniepoint, FairMoney, Carbon, Renmoney, Kuda, Advans La Fayette, Accion), verified against a CBN circular and per-institution sources. Leasing/asset-finance companies not yet covered. |
| `fccpc_digital_lenders.json` | one row per FCCPC-approved lender/app | **Seeded** — 559 entries (524 full + 35 conditional approval), fetched directly from FCCPC's own published tables. This is the broad digital-lender universe (source #9 in the plan); not yet cross-referenced to `institution_id` in `institutions.json`. |
| `portfolio_snapshots.json` | institution × period | **Partial** — populated only where a real, cited figure was found. Most institutions are not yet covered; this is expected at this stage, not a bug. |
| `portfolio_mix.json` | institution × period × dimension | **Empty/pending** — sector/geography/product breakdowns require deeper per-institution annual-report extraction than fits an initial pass. |
| `industry_aggregates.json` | period × segment | **Partial** — CBN sector-wide figures (total credit, NPL ratio). |
| `rating_actions.json` | institution × date | **Partial** — 6 Agusto & Co / GCR Ratings public rating summaries for MFB/fintech institutions; large banks not yet covered. No DataPro summaries found yet. |
| `capital_market_instruments.json` | one row per bond/CP/note | **Not started** — SEC prospectus/bond-filing research not yet run. |
| `disbursement_metrics.json` | institution × period | **Partial** — self-reported disbursement volumes for Moniepoint, FairMoney, Renmoney (all flagged `self_reported: true`). |
| `product_snapshots.json` | institution × product | **Not started** — lender website/app scraping not yet run. |

## Interactive browser

`database.html` (repo root) is a searchable/sortable/filterable UI over these files — it fetches
the JSON at load time (no build step), so it always reflects whatever is in this directory.
Requires serving over HTTP(S), not `file://` (browsers block local `fetch()`): run
`python3 -m http.server` from the repo root and open `http://localhost:8000/database.html`, or
view it once deployed to a static host.

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

- Coverage today is the ~7 largest NGX-listed banking groups (with financials) plus the full
  CBN-licensed bank universe (names only for most), 9 MFB/fintech lenders with rating/portfolio
  data, and the full FCCPC digital-lender registry (names/approval status only). Leasing/asset
  finance companies — also part of Waterline's target originator segment — are not yet covered.
  The 559-entry FCCPC list has not been cross-referenced against `institutions.json` — several
  of the 9 MFB/fintech entries almost certainly also appear there under their app name(s).
- `portfolio_mix`, `capital_market_instruments`, `disbursement_metrics`, and `product_snapshots`
  are unstarted. Per the build plan, these are lower-priority "enrichment" sources — sequence
  them after core coverage of target-segment institutions is solid.
- Every record here should be treated as **as-of the `retrieved_date`**, not live. Before using
  any figure in an investor-facing document, re-check it against the cited source if more than a
  few months old.
- Migration path: each JSON array maps directly to the Postgres tables in
  `docs/loan-database-plan.md` §1.1. When migrating, keep the `source` object as the seed for the
  `provenance` table rather than flattening it away.
