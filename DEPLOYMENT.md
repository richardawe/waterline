# Deploying to production (cPanel)

`.github/workflows/deploy.yml` runs on every push to `main` and deploys two
independent things over FTP:

- **Static frontend** (`index.html`, `database.html`, `standard.html`,
  `admin.html`, `assets/`, `standards/`) → your cPanel `public_html` (or
  wherever `FRONTEND_REMOTE_DIR` points).
- **Backend** (`backend/`) → a separate directory that cPanel's "Setup Python
  App" serves via Passenger.

The workflow updates *code*. It does not provision cPanel itself — the steps
below are one-time manual setup, done once before the first deploy.

## 1. One-time cPanel setup (manual, do this first)

1. **Postgres database.** You said Postgres is already available on this
   cPanel account. Create a database + user for this app if you haven't, and
   note the connection details. **Check whether `CREATE EXTENSION vector;`
   works on it** — run that one command via phpPgAdmin or `psql` before
   relying on it. If it fails, the `document_embedding` table
   (`app/models/embedding.py`) won't work; nothing else in the app depends on
   pgvector, so this is only a blocker if/when semantic search gets built.
2. **Setup Python App** (cPanel → Software → Setup Python App):
   - App root: an empty directory *outside* `public_html`, e.g.
     `/home/youruser/waterline_backend` — this is what `BACKEND_REMOTE_DIR`
     (below) must point to.
   - Python version: 3.11+.
   - Application startup file: `passenger_wsgi.py` (already committed in
     `backend/`).
   - Application Entry point: `application` (the variable
     `passenger_wsgi.py` exports).
   - Domain/URI: whatever subdomain or path you want the API reachable at —
     this becomes `PROD_API_BASE` below (e.g.
     `https://api.yourdomain.com`).
3. After the app is created, cPanel drops a `.env`-like "Environment
   variables" panel — set `DATABASE_URL`, `ENVIRONMENT=production`, and
   `API_CORS_ORIGINS` (your frontend's real domain, e.g.
   `https://yourdomain.com`) there. `backend/app/config.py` reads these via
   `pydantic-settings`; nothing needs to be hardcoded.
4. Click **Run Pip Install** once in that same cPanel UI to create the
   venv and install `backend/requirements.txt` for the first time — the
   automated workflow below can do this on later deploys *if* you set up SSH
   (step 4), but the very first install needs to happen through the UI
   because the venv doesn't exist yet.
5. Run migrations once, from that same cPanel UI's terminal (or SSH):
   `cd <app root> && alembic upgrade head`, then seed Tier 1 data:
   `python app/seed/seed_tier1_from_json.py`.

## 2. GitHub repo configuration

Settings → Secrets and variables → Actions. **Secrets** (sensitive):

| Name | What |
|---|---|
| `FTP_SERVER` | Your cPanel FTP hostname |
| `FTP_USERNAME` | FTP account username |
| `FTP_PASSWORD` | FTP account password |
| `ADMIN_BASIC_AUTH_USER` | Username required to open `admin.html` in production |
| `ADMIN_BASIC_AUTH_PASSWORD` | Password required to open `admin.html` — pick something real, this page can write to your production database |

**Variables** (not sensitive, visible in workflow logs):

| Name | What | Example |
|---|---|---|
| `PROD_API_BASE` | Where the frontend sends API calls — becomes `window.WATERLINE_API_BASE` at deploy time | `https://api.yourdomain.com` |
| `FRONTEND_REMOTE_DIR` | FTP path the static site deploys to | `/public_html/` |
| `BACKEND_REMOTE_DIR` | FTP path the backend deploys to — **must match the Python App's App root from step 1.2** | `/waterline_backend/` |
| `CPANEL_HTPASSWD_ABS_PATH` | Absolute server filesystem path to `.htpasswd` (Apache requires an absolute path, not a URL) — put it next to where it's uploaded | `/home/youruser/public_html/.htpasswd` |

**Optional — automates `pip install` + `alembic upgrade head` on every deploy.**
Without these, the workflow still deploys backend code and restarts the app,
but leaves dependency/schema changes for you to run manually (it prints a
warning telling you so):

| Name | Kind | What |
|---|---|---|
| `CPANEL_SSH_HOST` | Variable | SSH hostname — leave unset to skip automation entirely |
| `CPANEL_SSH_PORT` | Variable | SSH port, defaults to 22 |
| `CPANEL_PYTHON_VENV_ACTIVATE` | Variable | Absolute path to the venv's `activate` script cPanel created, e.g. `/home/youruser/virtualenv/waterline_backend/3.11/bin/activate` |
| `CPANEL_SSH_USERNAME` | Secret | SSH username |
| `CPANEL_SSH_KEY` | Secret | SSH private key (not a password) |

## 3. What happens on push to `main`

- **Frontend job**: generates `assets/config.js` from `PROD_API_BASE`,
  renders `.htaccess`/`.htpasswd` from `ADMIN_BASIC_AUTH_*` to
  password-protect `admin.html`, then FTPs everything except `backend/`,
  `.github/`, `deploy/`, `docs/`, `data/` to `FRONTEND_REMOTE_DIR`. Both the
  API-URL and admin-password steps **fail the deploy** if their
  secrets/variables are missing, rather than silently shipping a broken or
  unprotected page.
- **Backend job**: FTPs `backend/` to `BACKEND_REMOTE_DIR`, stamping
  `tmp/restart.txt` so Passenger reloads. If SSH secrets are configured, it
  also runs `pip install` and `alembic upgrade head` before the final
  restart; otherwise it warns you to do that by hand.

Both jobs run in parallel and are independent — a failure in one doesn't
block the other.

## 4. Local dev is unaffected

`assets/config.js` in the repo is a committed no-op placeholder (see the file
itself) — locally, `database.html`/`standard.html`/`admin.html` keep falling
back to `http://localhost:8001`. The workflow overwrites its own checkout's
copy before uploading; it never commits the generated version back to git.

## Known limitations, honestly

- **No zero-downtime deploy.** Passenger restarts on `tmp/restart.txt`
  touch; there's a brief gap while it reloads. Fine for this traffic level,
  not what you'd want at scale.
- **No automated rollback.** A bad deploy needs a manual `git revert` +
  re-push, or restoring from cPanel's file backups.
- **`data/market-intelligence/*.json` and `standards/wcds/samples/*.csv` are
  not duplicated to the backend server** — the backend reads them from its
  own `backend/../data` and `backend/../standards` at seed/runtime, which
  only exist if you deploy the whole repo there, not just `backend/`. If
  `BACKEND_REMOTE_DIR` only contains `backend/`'s contents, run
  `seed_tier1_from_json.py` with those files uploaded alongside once during
  initial setup (step 1.5), rather than expecting the deploy workflow to keep
  them in sync — they don't change often enough to justify wiring that up now.
