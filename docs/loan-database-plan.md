# Waterline Market & Loan Data Platform — Build Plan

## 0. Why this exists

Waterline structures securitization/forward-flow deals between Nigerian lenders (banks, MFBs, fintechs, leasing/asset-finance companies) and investors. Two things run on data:

1. **Deal sourcing & diligence** — knowing which lenders have loan books worth structuring, what their portfolio quality looks like, and how they compare to peers, *before* a conversation starts.
2. **Investor confidence** — being able to say "here's how this pool compares to the market" with evidence, not a pitch deck.

Neither of these requires individual borrower-level loan records from public sources — and public sources can't give you that anyway. What they *can* give you is **institution-level and portfolio-level intelligence**: gross loans, NPL ratios, sector/geography mix, funding cost, growth. That's Tier 1 below.

The one place genuine **loan-level** data (individual facilities: ticket size, tenor, rate, collateral, vintage, status) shows up is a lender's own loan tape, shared under NDA once you're in a live deal (Step 01 "Discovery" on the site). That's Tier 2 — structurally different data, different legal basis, different storage, and it should not live in the same schema as scraped public filings.

```
Tier 1: Market Intelligence DB      Tier 2: Deal Loan-Tape DB
(public sources, aggregate)         (bilateral, loan-level, NDA'd)
────────────────────────────        ──────────────────────────────
Feeds: sourcing, benchmarking,      Feeds: pool structuring,
       investor pitch material            waterfall modeling,
                                           servicing/reporting
Refresh: quarterly/annual           Refresh: per deal lifecycle
Access: broad, internal analysts    Access: deal team only, NDA-gated
```

This plan covers building both, but sequences Tier 1 first since it's free, public, and needed immediately for pipeline-building — and Tier 2's schema is designed now so it doesn't need to be retrofitted when the first real loan tape arrives.

---

## 1. Data model

### 1.1 Tier 1 — Market Intelligence (institution/portfolio grain)

Core entities:

| Entity | Grain | Key fields |
|---|---|---|
| `institution` | one row per lender | name, type (DMB/MFB/fintech/leasing/other), CBN license category, NGX ticker (if listed), FCCPC registration status/ID, RC number, website |
| `filing` | one document | institution_id, source_type, period_end, filing_date, url, sha256 of stored file, extraction_status |
| `portfolio_snapshot` | institution × period | gross_loans, net_loans, stage_1/2/3 balances, NPL_ratio, NPL_coverage, avg_yield_on_loans, cost_of_funds, provisioning_charge |
| `portfolio_mix` | institution × period × dimension | dimension_type (sector / geography / product / currency / tenor_band), dimension_value, amount, pct_of_book |
| `rating_action` | institution × date | agency (Agusto/GCR/DataPro/Fitch...), rating, outlook, prior_rating, report_url, key_commentary_tags |
| `capital_market_instrument` | one row per bond/CP/note | institution_id, instrument_type, issue_size, tenor, coupon, use_of_proceeds, collateral_description (free text + tags), SEC_RIN, trustee, issuing_house |
| `disbursement_metric` | institution × period | source (investor deck/press release), disbursement_volume, active_customers, avg_ticket_size, portfolio_at_risk |
| `industry_aggregate` | period × segment | source=CBN, segment (e.g. "MFB sector"), total_credit, sectoral_breakdown, NPL_ratio_industry |
| `product_snapshot` | institution × product (scraped from website/app) | product_name, min/max ticket, tenor_range, indicative_rate_range, eligibility_notes, collateral_required, scrape_date |
| `provenance` | every fact row links here | source_document_id, extraction_method (manual/OCR/LLM/API), extracted_by, confidence_score, verified_by, verified_at |

Every numeric fact carries a `provenance` foreign key — non-negotiable, because you'll be citing this to investors and it has to survive an audit question ("where did this NPL number come from?").

### 1.2 Tier 2 — Deal Loan-Tape (true loan-level grain)

**Superseded by WCDS.** The sketch below was this plan's original placeholder schema, written before any real loan tape existed. It's since been replaced by the **Waterline Credit Data Standard (WCDS) v0.1** — a full canonical schema (18 entities, field dictionary, 30-rule validation rulebook, CRMS/IFRS9/credit-bureau/investor-tape adapters) published at [`standard.html`](../standard.html) and specified in full in [`standards/wcds/`](../standards/wcds/). Use WCDS's `Facility` + `FacilitySnapshot` entities (§5.6/§5.13 of the spec) as the actual Tier 2 schema — the table below is kept only as a historical note of the original, much thinner design.

| Entity | Grain | Key fields |
|---|---|---|
| `deal` | one per transaction | originator_institution_id, deal_stage (discovery/structure/validate/close/live), spv_name, senior_tranche_size, equity_tranche_size |
| `loan_tape_snapshot` | deal × as-of-date | file_hash, row_count, received_date, NDA_reference |
| ~~`loan_record`~~ | ~~one per underlying loan~~ | Replaced by WCDS `Facility` + `FacilitySnapshot` — see above. |
| ~~`loan_performance_history`~~ | ~~loan_record × month~~ | Replaced by WCDS `Payment` + `Schedule` entities. |

`deal` and `loan_tape_snapshot` still apply as-is — WCDS doesn't define deal-lifecycle tracking, only the loan data itself. Tier 2 sits behind stricter access control (§6) and is never merged into Tier 1's analytics warehouse without anonymization; WCDS's own PII classification and masking requirements (spec §14) apply on top of that.

---

## 2. Source-by-source ingestion plan

| # | Source | Format | Cadence | Extraction method | Notes / gotchas |
|---|---|---|---|---|---|
| 1 | Bank/MFB annual reports | PDF, sometimes HTML | Annual (+ some banks publish H1) | PDF table extraction (Camelot/pdfplumber) + LLM-assisted parsing of narrative risk sections for ECL stage disclosures | Layout varies wildly bank to bank — expect per-institution parsing templates, not one universal parser |
| 2 | NGX filings | PDF via NGX X-Compliance portal | Quarterly | Same as above; NGX also republishes as press releases sometimes | NGX site has no public bulk API — plan for scheduled scraping of the filing index page, not the report content itself |
| 3 | CBN reports (Statistical Bulletin, FSR, Economic Report) | PDF/XLS | Quarterly/annual, some monthly | XLS ingested directly; PDF via table extraction | These are your only source of true industry denominators — needed to compute institution market share |
| 4 | SEC prospectuses / bond filings | PDF | Per issuance (irregular) | Manual-first (low volume, high info density), template extraction once a pattern library exists | SEC Nigeria doesn't have a clean structured filings API — monitor SEC bulletins + issuing house press pages |
| 5 | Fintech financial reports | PDF/website, often thin | Annual or ad hoc | Manual review — few fintechs publish full financials; more likely from NDIC/CBN-supervised entity disclosures or investor updates | Treat as low-confidence/low-coverage source; flag heavily in provenance |
| 6 | Rating reports (Agusto/GCR/DataPro) | PDF (public summary), full report may be paywalled | On rating action | Manual extraction — these documents are dense and narrative, not tabular; an analyst reading + structured tagging is more reliable than automated parsing here | Highest signal-to-noise source for portfolio quality commentary; worth prioritizing analyst time here over pure scraping |
| 7 | Investor decks / press releases | PDF/PPT/web | Ad hoc | Manual + lightweight scraping of press-release pages (Google Alerts / RSS as trigger) | Numbers here are marketing-adjacent — always mark `self_reported=true`, don't let them silently override audited figures |
| 8 | Job ads / eng docs / APIs | Web (LinkedIn, careers pages, GitHub, API docs) | Continuous, low priority | Manual/periodic review; not a scraping target, more a "read occasionally and log qualitative notes" source | This is qualitative underwriting-intelligence, not structured data — store as tagged notes linked to institution, not as numeric facts |
| 9 | FCCPC lender registry | Web (list/table) | Monthly refresh | Scheduled scrape (it's a public compliance list, low legal risk) | This is your **universe list** — the seed for `institution` — run this first, everything else enriches it |
| 10 | Lender websites/apps | Web/mobile | Quarterly refresh | Scheduled scraping for product pages; manual for app-store/app-only lenders (screenshot + manual entry) | Rates/terms change without notice — timestamp every scrape, never treat as point-in-time-stable |

**Sequencing:** Start with #9 (FCCPC registry) to build the institution universe, then #3 (CBN) for industry benchmarks and #1/#2 (annual reports/NGX) for the ~25-30 licensed banks and larger MFBs, since those are the highest-value, best-structured documents. Fintech (#5), decks (#7), and websites (#10) are enrichment, not foundation — sequence them after the core institution/portfolio tables are populated and validated.

---

## 3. Pipeline architecture

```
┌──────────────┐   ┌──────────────┐   ┌───────────────┐   ┌────────────┐   ┌─────────────┐
│  Collectors   │→ │ Raw document │→ │  Extraction    │→ │ Normalize/  │→ │  Warehouse   │
│ (scrapers,    │  │   store      │  │ (table/LLM     │  │ validate    │  │  (Postgres)  │
│  manual upload)│  │ (S3 + hash) │  │  parsing)      │  │ + review    │  │              │
└──────────────┘   └──────────────┘   └───────────────┘   └────────────┘   └─────────────┘
                                                                                    │
                                                                     ┌──────────────┴───────────────┐
                                                                     │                               │
                                                              Analytics/BI layer            Internal API
                                                            (deal sourcing dashboards,     (feeds diligence
                                                             investor comparables)          memos, pitch decks)
```

- **Collectors**: source-specific scripts (one per source type, not one generic scraper — the sources are too heterogeneous). Each collector's only job is "find new/updated documents and drop them in raw storage with metadata." No parsing here.
- **Raw document store**: every PDF/XLS/HTML snapshot kept immutably (object storage + content hash). This is your audit trail — if a number is ever questioned, you produce the source PDF.
- **Extraction**: hybrid — deterministic table extraction (Camelot/pdfplumber/tabula) where tables are well-formed (CBN XLS, some annual report tables), LLM-assisted extraction with a structured schema + citation requirement for narrative/inconsistent layouts (rating reports, ECL stage disclosures buried in notes). Every LLM extraction gets a confidence score and routes low-confidence rows to human review — don't auto-publish unverified LLM output as a fact investors will see.
- **Normalize/validate**: unit consistency (₦'000 vs ₦'million — a classic annual-report trap), period-end alignment (calendar year vs fiscal year vs quarter), sector/product taxonomy mapping to a controlled vocabulary (so "Agriculture" and "Agric & Allied" become the same `sector` value), sanity-check rules (NPL ratio between 0-100%, gross ≥ net loans, etc.).
- **Warehouse**: Postgres is enough at this scale (dozens of institutions, low-thousands of filings/year) — no need for a big-data stack. Star-schema-ish: `institution` and `filing`/`provenance` as dimensions, `portfolio_snapshot`/`portfolio_mix` as facts.
- **Access layer**: internal analytics (Metabase/simple BI on top of Postgres) for the deal team; a thin internal API if/when this needs to feed the website or investor-facing tooling.

---

## 4. Tech stack (recommended, kept boring on purpose)

- **Storage**: Postgres (facts) + S3-compatible object storage (raw documents)
- **Extraction**: Python — `pdfplumber`/`camelot` for tables, LLM (Claude) for narrative/irregular-layout extraction with structured JSON output + source-span citation, `openpyxl`/`pandas` for CBN XLS
- **Scheduling**: cron/simple job scheduler per collector (no need for Airflow at this volume yet — revisit if source count grows past ~30-40)
- **Review UI**: a lightweight internal tool (even a spreadsheet-backed review queue is fine at MVP) where extracted rows sit as `pending_review` until an analyst confirms — don't skip this step to move faster; a wrong NPL number in front of an investor is expensive
- **BI**: Metabase (fast to stand up, good enough for internal dashboards and export-to-deck use)

---

## 5. Phasing

**Phase 0 (2 weeks) — Universe + schema**
Build the FCCPC-sourced `institution` table (the full universe of licensed digital lenders), stand up Postgres schema above, pick top ~30 banks/MFBs by asset size as the initial coverage set.

**Phase 1 (4-6 weeks) — Core financials, manual-first**
For the top 30 institutions: pull last 3 years of annual reports + NGX filings, extract `portfolio_snapshot`/`portfolio_mix` — manually at first (an analyst working through PDFs with a structured intake template) to validate the schema and taxonomy before automating. Automating extraction before you know the schema is right just means re-parsing everything later.

**Phase 2 (4 weeks, parallel-able) — Automate what's proven**
Build collectors + extraction pipelines for the source/institution pairs where Phase 1 showed a consistent, parseable layout (CBN XLS bulletins are the easiest win — fully structured). Keep rating reports and prospectuses manual — low volume, high value, not worth automating.

**Phase 3 (ongoing) — Enrichment + refresh**
FCCPC registry monthly refresh, lender website/product scraping quarterly, rating action monitoring, investor deck/press-release capture as they appear. Add fintechs/leasing companies as coverage priority 2.

**Phase 4 (per deal, and now for standardization engagements generally) — Tier 2 loan-tape ingestion**
Build the loan-tape intake path (secure upload, anonymization step, validation against WCDS's deterministic rulebook) — this is no longer gated on the first live deal, since WCDS standardization is now offered as its own service (`standard.html`, "How a lender engages with us"). The reference implementation still needs building: a mapper (source → WCDS field mapping), a validator (the 30-rule rulebook), a reconciler (control-total checks), and destination adapters (CRMS/bureau/IFRS9/investor-tape) — see the spec's §17 "Minimum viable WCDS implementation" for the component list. The 3 synthetic sample datasets in `standards/wcds/samples/` exist specifically to build and test this against before the first real loan tape arrives.

---

## 6. Legal, compliance & data governance

- **Tier 1 is all public information** (annual reports, NGX/SEC/CBN filings, rating agency public summaries, company websites) — low legal risk, but still: respect robots.txt/ToS on scraped sites, keep scrape frequency reasonable (no aggressive polling), and don't republish full copyrighted PDFs externally — store internally, cite/link externally.
- **NDPR (Nigeria Data Protection Regulation) applies the moment Tier 2 loan tapes arrive.** Loan records are financial data about identifiable individuals. Before any loan tape is ingested:
  - Get it under a signed NDA with the originator that explicitly covers data handling.
  - **Never ingest raw BVN, phone numbers, names, or addresses into the analytics warehouse.** Hash/tokenize borrower identifiers at intake (ideally the originator provides pre-anonymized tapes; if not, anonymize on receipt, before the file leaves the secure intake path).
  - Tier 2 data should live in a separate, access-restricted schema/database from Tier 1, with deal-team-only access — not queryable from the general BI layer.
- **Rating agency reports**: excerpts are fine to use with attribution; don't scrape/republish full paywalled reports.
- **FCCPC registry**: this is a public compliance list — safe to scrape on a schedule, but it's a *list* (name/status/registration), not financial data, so low sensitivity.

---

## 7. Immediate next steps

1. Confirm the top ~30 institution coverage list (by asset size / relevance to Waterline's target deal size ₦500M–₦5B pools).
2. ~~Stand up Postgres schema (§1.1) — this repo currently has no backend, so this is a new service, not an extension of the existing landing page.~~ **Done** — see `backend/` (Postgres 16 + pgvector, FastAPI, SQLAlchemy/Alembic). Tier 1 tables now match the real `data/market-intelligence/*.json` field shapes (adapted from this section's original sketch during implementation — see `backend/app/models/tier1.py`); Tier 2 is the full WCDS v0.1 canonical model (`backend/app/models/wcds.py`). `backend/app/seed/seed_tier1_from_json.py` migrates the existing JSON into Postgres.
3. Run the FCCPC registry pull manually once to seed `institution` and sanity-check the field list before building a scraper.
4. Pick 3-5 institutions and do a fully manual Phase-1 pass end-to-end (annual report → structured rows) to pressure-test the schema before writing any extraction code.
5. **Done** — the Phase 4 loan-tape intake path (mapper + validator + reconciler, §5) is built: `backend/app/ingest/` implements field mapping, canonicalization, the WCDS-R001..R030 deterministic rulebook, and control-total reconciliation, end-to-end tested against all three `standards/wcds/samples/*.csv` synthetic tapes (their deliberate exceptions are all caught — see `backend/tests/test_pipeline_integration.py`). Not yet built: destination adapters (CRMS/bureau/IFRS9/investor-tape export, spec §10) and the deeper WCDS entities' intake paths (Collateral, Restructure, Payment, Schedule, CreditRisk, BVN/TIN identity) — the mapper currently covers the flattened Facility+FacilitySnapshot+Party shape only.
6. **New** — SPV structuring pipeline (`backend/app/spv/`): eligibility screening against configurable criteria (status/DPD/default/currency/tenor/concentration limits), senior/mezzanine/equity tranche sizing, and a cashflow waterfall simulation (per-facility amortization schedules aggregated, with a CDR/CPR-style default/prepayment overlay and sequential-pay allocation). Exposed via `POST /deals/{id}/spv`. See `backend/README.md`'s "Design notes" for the simplifications this makes.
