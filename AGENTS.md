# Agent handoff notes — Waterline

Read this before touching deploy config, `admin.html`, or the frontend↔backend
wiring. It's the operational context a fresh session/agent won't have —
architecture is easy to re-derive by reading code; the state below isn't.

Last updated: 2026-08-26.

## What this is

A loan-book structured-finance platform for Nigerian lenders, in three parts:

1. **Tier 1 market intelligence** (admin workspace) — private institution/
   portfolio data (NPL ratios, ratings, capital markets activity), scraped
   from public filings, seeded into Postgres.
2. **WCDS loan-tape ingestion** (admin workspace) — a canonical loan-data
   standard (`standards/wcds/`) with a 30-rule validation engine
   (`backend/app/ingest/validator.py`). Lenders' raw CSV/XLSX exports get
   mapped → canonicalized → validated → reconciled.
3. **SPV structuring** (`admin.html` only — no public UI yet) — eligibility
   screening, tranche sizing (Senior/Mezz/Equity), and a monthly cashflow
   waterfall simulation (`backend/app/spv/`).
4. **Automated finance blog** (`/blog/`, public) — SEO/AEO content on credit,
   loans and finance in Nigeria/Africa, written by a free OpenRouter model,
   QA-reviewed by a second model against curated facts + fetched news, and
   auto-published as static HTML via `.github/workflows/blog.yml`. Full
   reference: `docs/blog-pipeline.md`. Human safety valve: `admin.html`'s
   Blog tab (edit/force-publish/archive any post after the fact).

## Architecture

- **Frontend**: plain static HTML + vanilla JS, no build step, no framework.
  `index.html` is the public stealth page, `preview/index.html` is the
  password-protected product preview, and `admin.html` is the unified internal
  workspace. Shared look via `assets/theme.css`. Each
  API-calling page reads `window.WATERLINE_API_BASE` (set by
  `assets/config.js`, falls back to `http://localhost:8001` for local dev).
- **Backend**: FastAPI + SQLAlchemy + Postgres (`backend/app/`), routes in
  `backend/app/api/*.py`. Alembic migrations in `backend/alembic/`.
- **`admin.html`** is the only UI for live market data, WCDS validation and
  downloads, deals, SPVs, and founder profiles. It also has an "Explain this,
  simply" button that renders a plain-English tranche/waterfall walkthrough
  computed from the real per-period waterfall output, not restated
  assumptions.

## Local dev

`cd backend && uvicorn app.main:app --reload` (needs Postgres + pgvector —
`docker-compose.yml` in `backend/`, or Homebrew Postgres 16+pgvector). Static
frontend: `python3 -m http.server 5500` from repo root. Full details in
`backend/README.md`. `assets/config.js` in git is an intentional no-op
placeholder — don't hardcode a URL into it, that's what the deploy workflow
overwrites at push time.

## Production deployment — cPanel, GitHub Actions, FTP

Full mechanics in `DEPLOYMENT.md`. Summary: `.github/workflows/deploy.yml`
runs on push to `main`, deploys the static frontend and backend separately
over FTP to a cPanel account, using Phusion Passenger
(`backend/passenger_wsgi.py`, an ASGI-to-WSGI bridge via `a2wsgi`) to run the
backend.

### Current state

- **Frontend deploy pipeline is working** with an FTP account scoped to the
  live document root.

- **Access model:** `/` is public; `/preview/` has one Apache Basic Auth gate;
  `admin.html` uses its own API credentials. All data API routes require admin
  authentication. Only `/health` is public, and API docs are off in production.

- **Backend is live** under `/home/waterline/waterline_backend`, served by
  Passenger at `api.waterline.ng`. `bootstrap` FTP deployment stamps
  `tmp/restart.txt`; full server automation still needs SSH.

- **Postgres is live and migrated** to Alembic head `c17443101dd9`; Tier 1
  seed data is loaded. Keep `DATABASE_URL` only in the server `.env`. Never
  copy database credentials into this repository.

- **SSH: blocked.** Ports 22/2222/22022 all timeout from outside, while
  cPanel (2083)/WHM (2087)/Webmail (2096) connect fine — looks like a
  firewall dropping non-allowlisted IPs rather than SSH being disabled
  outright. cPanel does have a browser-based Terminal (cPanel → Advanced →
  Terminal) that doesn't need SSH and goes over 2083, which is the
  documented fallback for running `pip install`, `alembic upgrade head`,
  and `CREATE EXTENSION vector` by hand. The GitHub Actions workflow has an
  *optional* SSH-based automation path (`CPANEL_SSH_HOST` etc., see
  `DEPLOYMENT.md`) that's simply skipped with a warning if unconfigured —
  it is currently unconfigured, so every backend deploy needs a manual
  `pip install`/`alembic upgrade head` pass via that Terminal until/unless
  SSH access is sorted out.

- **`CREATE EXTENSION vector;` unresolved, deliberately deprioritized.**
  Failed via phpPgAdmin's query box (it wraps every query in
  `SELECT COUNT(*) FROM (...) AS sub` for pagination, which chokes on DDL —
  not a real syntax error). Nothing in the app currently depends on
  pgvector (`backend/app/models/embedding.py`'s `document_embedding` table
  is unused elsewhere), so this only matters once semantic search actually
  gets built. Don't spend time on it unless that's the task at hand.

## Known decisions — don't "fix" these without reading why first

- **`data/market-intelligence/*.json` and `standards/wcds/samples/*.csv` are
  still in the repo, not deleted.** They are backend seed sources and private
  admin download sources; deleting them would break deployment or re-seeding.
- **`admin.html` is deliberately not linked from public navigation.** It is
  reachable only by direct URL (`/admin.html`). Its sensitive operations are
  gated by backend API authentication. Don't add public nav links to it.
- **Frontend and backend deploy as two independent FTP targets on purpose**,
  even though right now they'd resolve to the same account/root if
  misconfigured (see the guard above). This split exists because cPanel's
  Application Manager expects the Python app in its own directory, separate
  from the static docroot — don't collapse them into one job/target.
- **The backend API URL is injected at deploy time via `assets/config.js`**,
  not hardcoded into the HTML and not read from a build tool. If you're
  adding a new page that calls the API, follow the same pattern: `const
  API_BASE = window.WATERLINE_API_BASE || 'http://localhost:8001';`, and
  include `<script src="assets/config.js"></script>` in `<head>` before your
  page's own script block.

## Where to look next

- Full deploy secrets/variables reference and one-time cPanel setup steps:
  `DEPLOYMENT.md`
- Backend local setup: `backend/README.md`
- WCDS spec/validation rules: `standards/wcds/`, `backend/app/ingest/`
- SPV eligibility/sizing/waterfall logic: `backend/app/spv/`
- Blog pipeline (generation/QA/publish, secrets, how to extend):
  `docs/blog-pipeline.md`
