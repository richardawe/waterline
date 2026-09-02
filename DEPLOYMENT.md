# Deploying to production (cPanel)

`.github/workflows/deploy.yml` runs on every push to `main` and deploys two
independent things over FTP:

- **Static frontend** (`index.html`, `preview/`, `admin.html`, `assets/`) →
  your cPanel `public_html` (or
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
| `PREVIEW_BASIC_AUTH_USER` | Username issued to approved private-preview users |
| `PREVIEW_BASIC_AUTH_PASSWORD` | Password issued to approved private-preview users |

**Variables** (not sensitive, visible in workflow logs):

| Name | What | Example |
|---|---|---|
| `PROD_API_BASE` | Where the frontend sends API calls — becomes `window.WATERLINE_API_BASE` at deploy time | `https://api.yourdomain.com` |
| `FRONTEND_REMOTE_DIR` | Path inside the frontend FTP account | `/` |
| `BACKEND_REMOTE_DIR` | Path inside the backend FTP account | `/` |
| `ENABLE_BACKEND_DEPLOY` | `false` skips backend; `bootstrap` uploads source only; `true` also builds, migrates, and restarts over SSH | `false` |
| `CPANEL_HTPASSWD_ABS_PATH` | Absolute path for the generated preview password file | `/home/youruser/public_html/.htpasswd` |

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
  generates `assets/config.js` from `PROD_API_BASE`, protects `/preview/`
  with the private-preview credentials, and FTPs the static site to
  `FRONTEND_REMOTE_DIR`. The homepage stays open. The admin workspace has a
  separate API-backed sign-in and is not linked from public navigation.

Approved users open `https://waterline.ng/preview/` and enter the preview
credentials once. The preview contains no live database or API access.
- **Backend job**: stays skipped while `ENABLE_BACKEND_DEPLOY=false`.
  `bootstrap` uploads source and restarts Passenger without SSH. `true`
  requires the server automation settings, then rejects a missing or malformed
  remote directory,
  then FTPs `backend/` to `BACKEND_REMOTE_DIR`, stamping
  `tmp/restart.txt` so Passenger reloads. If SSH secrets are configured, it
  also runs `pip install` and `alembic upgrade head` before the final
  restart; otherwise it warns you to do that by hand.

Frontend and backend deploy only after the test job passes.

## 4. Local dev is unaffected

`assets/config.js` in the repo is a committed no-op placeholder (see the file
itself) — locally, `admin.html` falls back to `http://localhost:8001`. The workflow overwrites its own checkout's
copy before uploading; it never commits the generated version back to git.

## 5. Automated blog pipeline (separate workflow)

`.github/workflows/blog.yml` runs on a schedule (and `workflow_dispatch`) —
independent from the `main`-push deploy workflow above, though its own
commits to `main` are what trigger that deploy for blog content. Full design
in `docs/blog-pipeline.md`. Requires these on top of everything above:

| Name | Kind | What |
|---|---|---|
| `BLOG_DATABASE_URL` | Secret | Same production Postgres `DATABASE_URL` already uses. |
| `OPENROUTER_API_KEY` | Secret | OpenRouter API key for the writer/QA models. |
| `BLOG_SITE_BASE_URL` | Variable | Public base URL, e.g. `https://waterline.ng` (defaults to that if unset). |

It pushes generated `blog/`, `sitemap.xml` and `robots.txt` straight to
`main` with the workflow's own `GITHUB_TOKEN` — if branch protection blocks
direct pushes from Actions, that step will fail loudly (content stays saved
in Postgres either way; nothing is silently lost).

## Known limitations, honestly

- **No zero-downtime deploy.** Passenger restarts on `tmp/restart.txt`
  touch; there's a brief gap while it reloads. Fine for this traffic level,
  not what you'd want at scale.
- **No automated rollback.** A bad deploy needs a manual `git revert` +
  re-push, or restoring from cPanel's file backups.
- The backend deployment bundles a copy of `data/market-intelligence` for the
  production seed command. The repository files remain the source of truth.
  WCDS documents are bundled with the backend and served only through the
  authenticated admin-download endpoint.
