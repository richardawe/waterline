# Waterline Collections — operating reference

Fee-recovery product at `waterline.ng/collections`. Built on the existing stack:
static HTML + vanilla JS pages under `collections/`, FastAPI routes under
`/collections/*` in `backend/app/collections/`, Postgres tables prefixed `cl_`
(migration `e0d3f5700598`). It shares no UI, routes or auth with the lender
product. Don't link to it from lender pages, and don't put lender, loan-book
or investor wording under `/collections`.

## Map

| Path | Who | File |
| --- | --- | --- |
| `/collections/` | Public landing | `collections/index.html` |
| `/collections/signup/`, `login/`, `verify/`, `reset/` | Clients | `assets/auth.js` |
| `/collections/app/…` (dashboard, uploads, uploads/new, accounts, accounts/<id>, confirmations, disputes, invoices, settings) | Clients | `assets/app.js` (picks the page from `<body data-page>`) |
| `/collections/ops/` | Waterline ops (tabs via `#hash`) | `assets/ops.js` |
| `/collections/p/<token>` | Debtors | `p/index.html` + `assets/pay.js` (~25 KB total) |
| `/collections/privacy/`, `terms/` (DPA at `terms/#dpa`) | Public | static (**drafts, need a Nigerian lawyer's review before launch**) |

`collections/.htaccess` rewrites `p/<token>` and `app/accounts/<uuid>`. A
plain local static server can't do that, so in local dev use
`/collections/p/?t=<token>` (account links switch to `view/?id=` on localhost
automatically).

Backend modules: `models.py` (schema), `service.py` (account, payment and
dispute state changes), `outreach.py` (queue and conduct rules), `billing.py`
(invoices and daily jobs), `templates.py` (fixed message templates and the
forbidden-wording check), `upload.py` (CSV/XLSX validation), `crypto.py`
(field encryption, blind indexes, signed URLs), `auth.py` (sessions and rate
limits), `api/*.py` (thin HTTP handlers).

## Auth model

- **Clients**: email + bcrypt password. A bearer token is kept in
  localStorage and sent in the `Authorization` header. There are no cookies,
  so a cross-site request can't carry the credential. That is how the
  build meets the CSRF requirement. Client sessions last 7 days.
- **Ops**: separate `cl_ops_user` table. Login needs password + TOTP code. The
  token is kept in sessionStorage and the session lasts 12 hours. Create
  users on the server with
  `python scripts/collections_create_ops_user.py --name … --email … --role Admin`.
  The script prints the authenticator secret. Only `Admin` can edit
  templates, config, fees and data deletion.
- **Debtors**: an unguessable 43-character token in the link. Any problem
  with the token returns the same 404. The link dies 30 days after the
  account closes. Ops can reissue a link, which revokes the old one.
- Every client handler loads rows through `_owned()`, which scopes them to
  the caller's `client_id`. `tests/test_collections_api.py::test_client_a_cannot…`
  covers this.
- Rate limits use an in-process sliding window, counted per Passenger worker.

## Rules enforced in code (each covered by tests)

- Contact window is 08:00–19:00 WAT, Monday to Saturday, with no public
  holidays. The holiday list is the `public_holidays` config (moveable feasts
  are estimates, so update them yearly). Sending outside the window is
  refused at **mark-sent time**, not just at queue build.
- At most 1 message per day. This is also a database unique constraint on
  (account, day).
- At most 2 messages per rolling 7 days. The window is **7 WAT calendar
  days** (today + 6). A 168-hour window would block the spec's own Day 7 plan
  offer.
- Sequence: Day 0 intro → Day 3 reminder → Day 7 plan offer → weekly. After
  6 messages with no response the account becomes Unresponsive. Any reply,
  plan, proof or dispute resets the count.
- Plan holders get only `INSTALMENT_DUE`: once from 2 days before the due
  date, and once 1+ days after a missed date. If an instalment is more than
  14 days late (`plan_broken_after_days`), the plan becomes Broken and the
  normal sequence resumes.
- Queued items are pulled immediately on opt-out, dispute, pause, withdraw
  or client suspension.
- **Email fallback**: when ops skip a phone send and the debtor has an email
  address, the same text goes by email and counts as that day's message.
  Turn this off with `email_fallback_enabled`.
- Templates must contain `{{client_name}}`, "via Waterline", `{{link}}` and
  "Reply STOP to stop messages." Threats, enforcement, blacklisting,
  exams/results/exclusion, shaming and third-party disclosure are rejected.
  The spec drafts of PLAN_OFFER, INSTALMENT_DUE and DISPUTE_RESOLVED lacked
  "via Waterline" (and DISPUTE_RESOLVED lacked the STOP line), so the
  defaults add them with minimal wording changes.
- Uploads reject any column outside the template.
  `dependant_first_name` must be a single word (no surnames). The row cap
  starts at `first_upload_row_cap` (500) and ops can raise it per client.
- If more than 15% of a batch opts out or disputes, the client is flagged.

## Money and fees

All amounts are integer kobo. A payment is **fee-eligible** when:

1. the client confirmed it,
2. the account had a Sent message on or before the payment date (WAT day),
   and
3. the payment date is within `attribution_days` of the most recent such
   message.

Withdrawn accounts and reversed confirmations never count. The fee is
`amount_confirmed × fee_percent`, rounded half-up to the kobo.

If a debtor pays the discounted full amount by the deadline, the client's
confirmation writes off the discount and the balance goes to zero.
"Undo confirmation" is allowed until the payment is on an issued invoice.

Invoices are drafted on the 1st for the previous month's confirmations, by
WAT confirmation date. Re-drafting adds new lines to an existing draft. Ops
issue the invoice, which emails the client with Waterline's bank details
(the `waterline_bank_details` config, **set this before launch**), and mark
it Paid. An Issued invoice past its due date becomes Overdue. More than 30
days overdue flags the client, and ops decide whether to suspend.

## Data protection

- Encrypted at rest (Fernet, `COLLECTIONS_ENCRYPTION_KEY`): debtor name,
  phone, email and dependant name; client bank details; user emails and
  phones; staged upload rows; message text; email bodies.
- Lookups use HMAC blind indexes (`COLLECTIONS_SIGNING_KEY`).
- **Losing the encryption key loses the data. Back it up outside the
  server.**
- Proofs and evidence are stored on disk in `COLLECTIONS_STORAGE_DIR`,
  outside the docroot. They're only served through 15-minute HMAC-signed
  URLs. Uploads are checked by magic bytes (JPG, PNG or PDF) and limited to
  5 MB.
- Retention: closed accounts are anonymised after `retention_months` (12),
  and their files are deleted.
- Clients can export a CSV from Settings and request deletion, which is
  logged and emailed to ops. An Admin carries out deletion with "Delete
  data…" in ops → Clients.
- `cl_audit_log` is append-only. Export it as CSV from ops → Audit.

## Deployment checklist

1. **Server `.env`** (backend):
   - `COLLECTIONS_ENCRYPTION_KEY`, `COLLECTIONS_SIGNING_KEY`: long random
     strings, **required in production**.
   - `COLLECTIONS_SITE_BASE_URL=https://waterline.ng`
   - `COLLECTIONS_API_BASE_URL=https://api.waterline.ng`
   - `COLLECTIONS_STORAGE_DIR`: an absolute path outside `public_html`.
   - `COLLECTIONS_CRON_TOKEN`: a random string.
   - `COLLECTIONS_OPS_ALERT_EMAIL`
   - `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`,
     `SMTP_FROM`, and optionally `SMTP_USE_SSL`. Use a sending subdomain
     such as `mail.waterline.ng` with SPF/DKIM. Without `SMTP_HOST`, emails
     are only logged to `cl_email`.
2. `pip install -r requirements.txt` (new: `bcrypt`, `cryptography`), then
   `alembic upgrade head`. Do both through cPanel Terminal, because SSH is
   still blocked.
3. Create the first Admin ops user with the script above.
4. **Daily job**: add a cPanel cron entry for 07:30 WAT (06:30 UTC):
   `curl -fsS -X POST -H "Authorization: Bearer $COLLECTIONS_CRON_TOKEN" https://api.waterline.ng/collections/jobs/daily`.
   The job:
   - builds the queue,
   - sends confirmation reminders,
   - checks for overdue invoices,
   - drafts invoices on the 1st,
   - applies retention.

   Ops can also run it from the Overview tab, and the queue tab's "Build"
   button is idempotent.
5. In ops → Config, set:
   - `waterline_bank_details`
   - `contact_whatsapp`
   - `contact_email`
   - this year's `public_holidays`
6. The frontend deploys with the existing FTP job.
   `deploy.yml` stamps `__ASSET_VERSION__` in `collections/**/*.html` and
   excludes `private_uploads/` from the backend upload.

## Phase-1 limitations

- Rate limits are counted per worker, not shared.
- There's no logo upload (`logo_url` exists, and the debtor page shows the
  client name).
- The Terms, DPA and Privacy text are drafts pending legal review.
- Only English templates (`LANGUAGES = {"en"}`).
