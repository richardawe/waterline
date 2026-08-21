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
| `institutions.json` | one row per lender | **Seeded** — 68 institutions (60 in a deduped view — see below): 51 CBN-licensed DMBs/merchant banks/holdcos, 9 fintech-operated MFBs (LAPO, AB Microfinance, Moniepoint, FairMoney, Carbon, Renmoney, Kuda, Advans La Fayette, Accion), 9 additional fintech lenders (OPay/Blue Ridge MFB, PalmPay, PalmCredit/Newedge Finance, Branch International, M-Kopa, Aella, Migo, Payhippo/Rivy, Umba), and 7 leasing/asset-finance companies (C&I Leasing — NGX-listed, plus Aquila, VT, Emerald, PicPlus, Tamseed, Nowerox Leasing). `sector_tag: "fintech"` marks all fintech-model institutions regardless of underlying license type; `type: "leasing_company"` marks the leasing/asset-finance segment. Several fintech and leasing entries came back with thin/no financials but real, useful licensing-structure findings (e.g. two "PalmCredit"/"PalmPay" brands are legally separate entities; Migo and Payhippo have pivoted away from Nigerian consumer lending; most named leasing companies' CBN licensing status is unconfirmed). |
| `fccpc_digital_lenders.json` | one row per FCCPC-approved lender/app | **Seeded** — 559 entries (524 full + 35 conditional approval), fetched directly from FCCPC's own published tables. This is the broad digital-lender universe (source #9 in the plan); not yet cross-referenced to `institution_id` in `institutions.json`. |
| `portfolio_snapshots.json` | institution × period | **Partial** — populated only where a real, cited figure was found, including some multi-year series (Zenith, UBA, GTCO, Access) for trend charts. Most institutions are not yet covered; this is expected at this stage, not a bug. |
| `portfolio_mix.json` | institution × period × dimension | **Partial** — sector breakdowns for Zenith (2 sectors, partial), GTCO (top 3, partial), and Access Holdings (18 sectors, ~97% complete but with an unresolved period ambiguity — see the record's own notes). None of the three are independently confirmed against the primary document; all `confidence: "low"`. Geography/product dimensions not started. |
| `industry_aggregates.json` | period × segment | **Partial** — CBN sector-wide figures (total credit, NPL ratio) with a sparse 2021–2026 trend. |
| `rating_actions.json` | institution × date | **Partial** — 6 Agusto & Co / GCR Ratings public rating summaries for MFB/fintech institutions; large banks not yet covered. No DataPro summaries found yet. |
| `capital_market_instruments.json` | one row per bond/CP series | **Seeded** — 7 real issuances (LAPO bond, 4 AB Microfinance CP series, Accion CP, FairMoney CP), mostly sourced directly from FMDQ Exchange listing/programme pages. None disclose loan-portfolio collateral — all are unsecured against the issuer's general credit. SEC prospectus research (as opposed to FMDQ CP/bond listings) not yet run. |
| `disbursement_metrics.json` | institution × period | **Partial** — self-reported disbursement volumes for Moniepoint, FairMoney, Renmoney (all flagged `self_reported: true`). |
| `product_snapshots.json` | institution × product | **Just started** — 1 entry (Carbon, medium confidence). Most lender sites either blocked the scrape (Renmoney 403) or the product-page URL guessed was wrong (Moniepoint 404, LAPO/Branch returned no substantive content) — this needs either a higher per-institution research budget or manual capture, not a bulk automated pass. |

## Interactive browser

`database.html` (repo root) is a searchable/sortable/filterable UI over these files — it fetches
the JSON at load time (no build step), so it always reflects whatever is in this directory.
Requires serving over HTTP(S), not `file://` (browsers block local `fetch()`): run
`python3 -m http.server` from the repo root and open `http://localhost:8000/database.html`, or
view it once deployed to a static host. Mobile-responsive; includes a "Fintech only" filter and
trend charts (industry NPL ratio, per-institution loan-book growth where ≥2 dated snapshots
exist, and market-composition bars).

**Grouping:** an operating bank and its listed holdco (e.g. Access Bank Limited / Access Holdings
Plc) are two distinct legal entities in `institutions.json` — correct for the data, but shown as
one row in the UI (via `group_holdco_id`) so the table doesn't read as duplicates. The detail view
lists both and pools their data. If you're consuming this JSON directly rather than through the
UI, remember `institutions.json` itself still has both rows — grouping is a display-layer concern,
not a data-layer one.

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

- The 559-entry FCCPC list has not been cross-referenced against `institutions.json` — several
  of the fintech entries almost certainly also appear there under their app name(s).
- `product_snapshots.json` is the thinnest file relative to effort spent — most lender websites
  either blocked automated fetches or the guessed URL was wrong. Getting real coverage here needs
  either manual capture per lender or a much higher per-institution search/fetch budget, not
  another bulk automated pass.
- `portfolio_mix.json`'s three entries are all `confidence: "low"` — none were independently
  confirmed by directly reading the source PDF (all came from search-result synthesis). The Access
  Holdings entry in particular has an unresolved period ambiguity (may be H1 2024 or HY 2025).
  Treat all three as "worth having, needs verification" rather than final.
- `capital_market_instruments.json` covers FMDQ-listed bonds/CP only — SEC prospectus research
  (a distinct source in the build plan, and the more likely place to find loan-portfolio-backed
  structures rather than plain unsecured corporate CP/bonds) has not been run.
- Every record here should be treated as **as-of the `retrieved_date`**, not live. Before using
  any figure in an investor-facing document, re-check it against the cited source if more than a
  few months old.
- Migration path: each JSON array maps directly to the Postgres tables in
  `docs/loan-database-plan.md` §1.1. When migrating, keep the `source` object as the seed for the
  `provenance` table rather than flattening it away.
