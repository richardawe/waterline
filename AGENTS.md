# Agent handoff notes — Waterline

Read this before touching deploy config, `admin.html`, or the frontend↔backend
wiring. It's the operational context a fresh session/agent won't have —
architecture is easy to re-derive by reading code; the state below isn't.

Last updated: 2026-08-23, mid-way through the first production deploy.

## What this is

A loan-book structured-finance platform for Nigerian lenders, in three parts:

1. **Tier 1 market intelligence** (`database.html`) — public institution/
   portfolio data (NPL ratios, ratings, capital markets activity), scraped
   from public filings, seeded into Postgres.
2. **WCDS loan-tape ingestion** (`standard.html`) — a canonical loan-data
   standard (`standards/wcds/`) with a 30-rule validation engine
   (`backend/app/ingest/validator.py`). Lenders' raw CSV/XLSX exports get
   mapped → canonicalized → validated → reconciled.
3. **SPV structuring** (`admin.html` only — no public UI yet) — eligibility
   screening, tranche sizing (Senior/Mezz/Equity), and a monthly cashflow
   waterfall simulation (`backend/app/spv/`).

## Architecture

- **Frontend**: plain static HTML + vanilla JS, no build step, no framework.
  `index.html` (marketing), `database.html`, `standard.html`, `admin.html`
  (internal console, see below). Shared look via `assets/theme.css`. Each
  API-calling page reads `window.WATERLINE_API_BASE` (set by
  `assets/config.js`, falls back to `http://localhost:8001` for local dev).
- **Backend**: FastAPI + SQLAlchemy + Postgres (`backend/app/`), routes in
  `backend/app/api/*.py`. Alembic migrations in `backend/alembic/`.
- **`admin.html`** is the *only* UI for creating deals, uploading loan tapes,
  and structuring SPVs — there's no public-facing flow for this yet, by
  design (see "Known decisions" below). It also has an "Explain this,
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

### Current state (as of last update — verify before trusting any of this)

- **Frontend deploy pipeline: working**, verified end-to-end (see run
  `32654702335` on GitHub Actions). But it's currently pointed at the
  **wrong directory**: `FRONTEND_REMOTE_DIR` (GitHub Actions variable) is
  set to `/`, which resolves to `/home/waterline/waterline.ng/prod` — a
  staging folder, **not** `waterline.ng`'s actual Document Root. The site
  owner can't edit the domain's Document Root setting, so the fix is a new,
  narrowly-scoped FTP account pointed at `public_html` (cPanel → FTP
  Accounts → Create → Directory: `public_html`), then update
  `FTP_USERNAME`/`FTP_PASSWORD`/`FRONTEND_REMOTE_DIR` to match. **This was
  requested but the new account hadn't been created/confirmed as of last
  update — check before assuming it's fixed.**

- **Private preview access:** `database.html` and `standard.html` are protected
  by one Apache Basic Auth credential generated during frontend deployment.
  The public homepage remains open and invites visitors to request access.
  `admin.html` has no Apache Basic Auth; its own sign-in protects API actions.

- **Backend deploy: intentionally not run yet.** `BACKEND_REMOTE_DIR` is
  unset on purpose — the workflow has a guard
  (`.github/workflows/deploy.yml`, "Refuse to deploy backend without an
  explicit target directory") that hard-fails rather than defaulting to the
  FTP account's root, because until a real backend target directory is
  configured, that root is the *same* directory the frontend deploys to —
  an unset `server-dir` would dump Python source into the public site.
  **Do not remove that guard or set `BACKEND_REMOTE_DIR` casually** — only
  once the cPanel Application Manager app is actually registered and its
  Application Path is known.

- **cPanel Application Manager registration: unconfirmed.** Last known
  state: the user was filling out the registration form (Application Name
  `waterline-backend`, Deployment Domain `api.waterline.ng`, Application
  Path `waterline_backend`, env vars for `DATABASE_URL`/`ENVIRONMENT`/
  `API_CORS_ORIGINS`) but hadn't confirmed submission, and reported not
  seeing the expected post-registration venv-activation command. **Ask the
  user for current status rather than assuming either way.**

- **`DATABASE_URL` computed but not yet confirmed placed.** The Postgres
  credentials the user provided contained an unescaped `@` in the password
  (`7&LAdj^_lHJJ}T4@`), which will break URL parsing unless percent-encoded.
  Correct value (verify before reusing — do not regenerate from the raw
  password without re-checking it against this):
  ```
  postgresql+psycopg://waterline_admin:7%26LAdj%5E_lHJJ%7DT4%40@localhost:5432/waterline1
  ```
  This belongs in cPanel Application Manager's Environment Variables, not a
  GitHub secret — it's read by the running app, not by CI. Postgres is
  bound to `localhost` on that server (5432 isn't reachable externally —
  confirmed via timeout, this is expected/correct, not a bug), so it can't
  be validated except by SSH/Terminal on the box itself or by the deployed
  app actually connecting.

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
  still in the repo, not deleted**, even though the frontend no longer
  fetches them directly. They're the backend's actual seed source
  (`backend/app/seed/seed_tier1_from_json.py`) and legitimate sample
  downloads linked from `standard.html`'s Downloads section — deleting them
  would break re-seeding, not clean anything up.
- **`admin.html` is deliberately not linked from the public nav** on
  `index.html`/`database.html`/`standard.html`. It's reachable only by
  direct URL (`/admin.html`). Its sensitive operations are gated by backend
  API authentication. Don't add public nav links to it.
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
