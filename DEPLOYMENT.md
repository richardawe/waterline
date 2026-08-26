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

1. **Postgres database.** Create a database and a dedicated user with full
   rights to that database. PostgreSQL must be reachable from the Python app
   as `localhost`. pgvector is not required for this release; the unused
   embedding table will be added later if the host enables the extension.
2. **Setup Python App** (cPanel → Software → Setup Python App):
   - App root: an empty directory *outside* `public_html`, e.g.
     `/home/youruser/waterline_backend`.
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

Create two narrowly scoped FTP accounts in cPanel before adding the GitHub
secrets:

- Frontend account directory: the real domain document root, normally
  `public_html`.
- Backend account directory: the Python application's app root, normally
  `waterline_backend`.

Do not reuse an account rooted at the cPanel home directory. With scoped
accounts, both remote directories below are `/`; FTP cannot escape into the
other application folder.

Settings → Secrets and variables → Actions. **Secrets** (sensitive):

| Name | What |
|---|---|
| `FTP_SERVER` | Your cPanel FTP hostname |
| `FRONTEND_FTP_USERNAME` | FTP account jailed to the real frontend document root |
| `FRONTEND_FTP_PASSWORD` | Password for the frontend-only FTP account |
| `BACKEND_FTP_USERNAME` | FTP account jailed to the Python application root |
| `BACKEND_FTP_PASSWORD` | Password for the backend-only FTP account |
| `ADMIN_BASIC_AUTH_USER` | Username required to open `admin.html` in production |
| `ADMIN_BASIC_AUTH_PASSWORD` | Password required to open `admin.html` — pick something real, this page can write to your production database |

**Variables** (not sensitive, visible in workflow logs):

| Name | What | Example |
|---|---|---|
| `PROD_API_BASE` | Where the frontend sends API calls — becomes `window.WATERLINE_API_BASE` at deploy time | `https://api.yourdomain.com` |
| `FRONTEND_REMOTE_DIR` | Path inside the frontend FTP account | `/` |
| `BACKEND_REMOTE_DIR` | Path inside the backend FTP account | `/` |
| `ENABLE_BACKEND_DEPLOY` | `false` skips backend; `bootstrap` uploads source only; `true` also builds, migrates, and restarts over SSH | `false` |
| `CPANEL_HTPASSWD_ABS_PATH` | Absolute server filesystem path to `.htpasswd` (Apache requires an absolute path, not a URL) — put it next to where it's uploaded | `/home/youruser/public_html/.htpasswd` |

**Required before setting `ENABLE_BACKEND_DEPLOY=true`.** These settings run
the build, migrations, and restart on the cPanel server. The workflow will not
upload backend code without them because doing so could restart against old
dependencies or an old database schema:

| Name | Kind | What |
|---|---|---|
| `CPANEL_SSH_HOST` | Variable | SSH hostname — leave unset to skip automation entirely |
| `CPANEL_SSH_PORT` | Variable | SSH port, defaults to 22 |
| `CPANEL_APP_ROOT` | Variable | Absolute server path to the Python app, e.g. `/home/youruser/waterline_backend` |
| `CPANEL_PYTHON_VENV_ACTIVATE` | Variable | Absolute path to the venv's `activate` script cPanel created, e.g. `/home/youruser/virtualenv/waterline_backend/3.11/bin/activate` |
| `CPANEL_SSH_USERNAME` | Secret | SSH username |
| `CPANEL_SSH_KEY` | Secret | SSH private key (not a password) |

## 3. What happens on push to `main`

- **Test job**: creates a temporary PostgreSQL 16 + pgvector database, installs
  the backend on Python 3.11, applies every Alembic migration, and runs all
  tests. No deployment runs if this fails.
- **Frontend job**: first rejects a missing or malformed remote directory,
  generates `assets/config.js` from `PROD_API_BASE`,
  renders `.htaccess`/`.htpasswd` from `ADMIN_BASIC_AUTH_*` to
  password-protect `admin.html`, then FTPs everything except `backend/`,
  `.github/`, `deploy/`, `docs/`, `data/` to `FRONTEND_REMOTE_DIR`. Both the
  API-URL and admin-password steps **fail the deploy** if their
  secrets/variables are missing, rather than silently shipping a broken or
  unprotected page.
- **Backend job**: stays skipped while `ENABLE_BACKEND_DEPLOY=false`.
  `bootstrap` uploads source only for the first manual server setup. `true`
  requires the server automation settings, then rejects a missing or malformed
  remote directory,
  then FTPs `backend/` to `BACKEND_REMOTE_DIR`, stamping
  `tmp/restart.txt` so Passenger reloads. If SSH secrets are configured, it
  also runs `pip install` and `alembic upgrade head` before the final
  restart; otherwise it warns you to do that by hand.

Frontend and backend deploy only after the test job passes.

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
