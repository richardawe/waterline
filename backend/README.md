# Waterline Backend

Postgres-backed API implementing the three-step funnel described on the site
(`../index.html`): browse the Tier 1 market-intelligence database, map/validate
a loan tape against the Waterline Credit Data Standard (WCDS v0.1), and
structure an SPV from the resulting clean loan book. See
`../docs/loan-database-plan.md` for the data-model rationale and
`../standards/wcds/` for the full WCDS spec this ingestion pipeline implements.

## Stack

- **Postgres 16 + pgvector** — one database, two tiers (Tier 1 market intel,
  Tier 2 WCDS loan-tape) plus a generic `document_embedding` table for future
  semantic search over filings/rating commentary/narrative fields.
- **FastAPI + SQLAlchemy 2.0 + Alembic** — API, ORM, migrations.
- **pandas** — loan-tape parsing (CSV/XLSX).

## Layout

```
app/
  models/       SQLAlchemy models: tier1.py (market intel), wcds.py (WCDS v0.1
                canonical entities), deal.py (Deal/SPV/Tranche/Waterfall), embedding.py
  ingest/       WCDS ingestion pipeline: loader -> mapping -> canonicalize ->
                validator (30-rule rulebook) -> reconcile -> pipeline (orchestrator)
  spv/          eligibility screening, tranche sizing, cashflow waterfall
  api/          FastAPI routers
  seed/         Tier 1 JSON -> Postgres migration script
alembic/        migrations
scripts/        manual run scripts (validate_sample.py, run_spv_demo.py)
tests/          pytest — unit tests (no DB) + integration tests (live Postgres)
```

## Running locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Postgres with pgvector — either docker compose:
docker compose up -d db
# ...or a local Postgres with the pgvector extension installed.

cp .env.example .env   # adjust DATABASE_URL if needed
export DATABASE_URL=postgresql+psycopg://waterline:waterline@localhost:5432/waterline

alembic upgrade head
python app/seed/seed_tier1_from_json.py   # loads data/market-intelligence/*.json

uvicorn app.main:app --reload
```

Or the whole stack via `docker compose up --build`.

## Trying the pipeline without the API

```bash
python scripts/validate_sample.py Lender_A_Consumer   # ingest + validate a WCDS sample tape
python scripts/run_spv_demo.py LENDER_C                # screen/size/waterfall an ingested book
```

## API walkthrough

```bash
# 1. Register a deal against an originator institution
curl -s -X POST localhost:8000/deals -H 'Content-Type: application/json' \
  -d '{"originator_institution_id":"LENDER_C","name":"LENDER_C Pilot Pool"}'

# 2. Upload their loan tape — runs the full WCDS pipeline (map/canonicalize/
#    validate/reconcile) and persists Facility/FacilitySnapshot/Party rows
curl -s -X POST "localhost:8000/deals/$DEAL_ID/loan-tapes" -F "file=@tape.csv"

# 3. Structure an SPV against the ingested book — eligibility screen, tranche
#    sizing, cashflow waterfall
curl -s -X POST "localhost:8000/deals/$DEAL_ID/spv" -H 'Content-Type: application/json' \
  -d '{"name":"SPV 1","as_of_date":"2026-08-21"}'
```

See `app/api/*.py` for the full route list; interactive docs at `/docs` once
the server is running.

## Design notes / deliberate simplifications

- **Ingestion mapper covers the flattened one-row-per-facility loan-tape shape**
  (Facility + latest FacilitySnapshot + primary borrower Party joined) — this is
  what `standards/wcds/samples/*.csv` look like and what most lenders' own loan
  tapes look like. Deeper WCDS entities (Collateral, Restructure, Payment,
  Schedule, CreditRisk, BVN/TIN identity) have full ORM models but no intake
  path yet — validator rules that depend on them report `SKIPPED`, not silently
  omitted. See `app/ingest/validator.py`'s module docstring.
- **TransformationEvent lineage rows are off by default** (`full_lineage=False`)
  — a 3,000-facility tape times ~25 mapped fields is 75k rows of "we parsed a
  date." Lineage is reproducible deterministically from (file hash + mapping +
  canonicalize.py version) without persisting every value; pass
  `full_lineage=True` for a smaller/critical tape where per-field audit rows
  are worth the storage.
- **The waterfall engine applies a single pool-level CDR/CPR attrition curve to
  aggregate scheduled cashflows** (summed from per-facility amortization
  schedules), not a loan-level Monte Carlo. Standard simplification for sizing
  and stress-testing a structure; not a substitute for a full cashflow engine
  before a real closing. See `app/spv/waterfall.py`'s module docstring for the
  exact payment priority modeled.
- **Concentration limit caps are computed once against the hard-screened
  pool**, not recomputed as facilities are trimmed. Documented in
  `app/spv/eligibility.py`.
