# Automated finance blog — pipeline reference

An automated blog on credit, loans and personal/SME finance in Nigeria and
across Africa, written by a free OpenRouter model, reviewed by a second pass
of the same model against a hard factual-grounding rubric, then
auto-published as static HTML through the existing FTP deploy pipeline.
This is the operational reference; the original design plan
(context/trade-offs) lived in the session that built it — this doc is what
to read when operating or extending the pipeline day to day.

**The model is a fallback chain, not a single id.** Free-tier availability
on OpenRouter rotates fast, and three separate slugs have now died under
this pipeline — `openai/gpt-oss-20b:free` and
`deepseek/deepseek-chat-v3.1:free` (pulled from the free tier / shared-pool
429s), then `minimax/minimax-m3:free`, which stopped existing on 2026-09-08
and broke daily generation for six consecutive runs. So
`OPENROUTER_WRITER_MODEL` / `OPENROUTER_QA_MODEL` are **comma-separated
lists tried left to right**, ending in `openrouter/free` — OpenRouter's own
router across whatever is currently free, which stays resolvable even when
every named slug ahead of it is gone. A single id is still valid config
(it's just a chain of one), but don't deploy one: that's the shape that
kept failing.

## Why the design looks like this

- **A free OSS model hallucinates more than a frontier model**, and this
  content states real interest-rate mechanics, regulator names and
  compliance rules under the Waterline name. So generation is grounded in
  two fetched/curated sources — not the model's own "knowledge" — and a
  second model reviews every draft against those same sources before
  anything is allowed to publish.
- **Auto-publish, not a pre-publish approval queue.** Posts go live the
  moment they pass automated QA; a human is notified afterward (a GitHub
  issue) and can edit, force-publish, or archive any post from `admin.html`
  — the safety valve, exercised after the fact, not a bottleneck before it.
- **Static HTML, not server-rendered.** AI-answer-engine crawlers (GPTBot,
  ClaudeBot, PerplexityBot) generally don't execute JavaScript, and the rest
  of the frontend already has no build step — so published posts become
  plain files under `/blog/`, shipped by the same FTP job that already
  deploys `index.html`.
- **CI never touches Postgres directly.** Generation runs *inside the
  backend process* (which already has `localhost` access to Postgres) and
  GitHub Actions triggers it over HTTPS, the same way `admin.html` already
  calls the backend. This was a deliberate pivot away from an earlier design
  that gave GitHub Actions its own direct database connection — that meant
  opening Postgres to the internet (firewall + `pg_hba.conf` +
  `listen_addresses` changes) just so a CI runner could reach it, when the
  backend already sits on an HTTPS endpoint that can do the same work
  without exposing the database at all.

## Architecture

```
.github/workflows/blog.yml (cron, daily 08:00 UTC, or workflow_dispatch)
  1. POST /admin/blog/seed-topics                 — idempotent: only inserts new prompts
  2. python scripts/trigger_blog_generation.py     — POST /admin/blog/generate, over HTTPS:
       (this step runs INSIDE the backend, on the server, not in CI)
       a. knowledge_base.relevant_facts()          — curated reference facts (backend/app/blog/facts/*.json)
       b. news_feed.fetch_recent_items()            — recent items from configured RSS feeds
       c. writer pass (OpenRouter, first model in the writer chain that answers)
                                                    — drafts title/body/FAQ/news section as JSON
       d. QA pass (OpenRouter, same for the QA chain) — reviews draft against the same facts/news; verdict JSON
       e. fail -> feed QA issues back to the writer, retry once, then give up
       f. save BlogPost: status=published (QA pass) or qa_failed (still failing after retry)
       <- backend returns {published: [...], qa_failed: [...]} as JSON
  3. python scripts/build_blog_static.py           — GET /admin/blog/posts?status=published, regenerates
                                                       /blog/*, sitemap.xml, robots.txt from the response
  4. commit + push blog/, sitemap.xml, robots.txt to main
       -> explicitly dispatches deploy.yml via the API (only if something was
          pushed) — see note below on why a plain push isn't enough
  5. notify.py                                      — opens a GitHub issue summarizing the run
```

**Why `blog.yml` explicitly dispatches `deploy.yml` instead of just relying on
its push to `main`:** GitHub Actions has a built-in anti-recursion guard — a
push authenticated with a workflow's own `GITHUB_TOKEN` does **not** fire
other workflows' `on: push` triggers (this is what stops automated commits
from chaining into infinite workflow runs). `blog.yml`'s bot commit landed on
`main` just fine the first time this was live, but `deploy.yml` silently
never ran because of this — the content sat committed but unshipped with no
error anywhere. The fix: `blog.yml`'s last step calls
`POST /repos/{repo}/actions/workflows/deploy.yml/dispatches` directly
(needs the `actions: write` permission, already set), which — being an
explicit `workflow_dispatch` via the API rather than an automatic `push`
event — is exempt from that guard. It only fires when the commit step
actually pushed something (`steps.publish.outputs.pushed == 'true'`), so a
day with no new topics doesn't force an unnecessary backend restart.

`admin.html`'s **Blog** tab (`/admin/blog/*` API, admin-authenticated) shows
every post regardless of status, with QA notes, and lets you edit content,
force-publish a `qa_failed` post, or archive a `published` one. Force-publish
only flips the DB row — re-run `build_blog_static.py` (or wait for the next
scheduled `blog.yml` run) to actually ship the change.

## Where things live

| Concern | Path |
|---|---|
| Data models | `backend/app/models/blog.py` (`BlogTopic`, `BlogPost`) |
| Generation pipeline | `backend/app/blog/` (`openrouter_client.py`, `knowledge_base.py`, `news_feed.py`, `prompts.py`, `generator.py`, `notify.py`, `sanitize.py`, `serialize.py`) |
| Curated reference facts | `backend/app/blog/facts/*.json` — human-edited, git-tracked, small |
| Admin API | `backend/app/api/blog_admin.py` — `/admin/blog/posts` (CRUD), `/admin/blog/topics` (CRUD), `/admin/blog/generate` (runs generation server-side), `/admin/blog/seed-topics` |
| Static site build | `backend/scripts/build_blog_static.py` → `blog/`, `sitemap.xml`, `robots.txt` at repo root. Fetches from the live API by default (`--source api`); `--source db` is a local-dev-only fallback that queries Postgres directly. |
| CI entrypoint | `backend/scripts/trigger_blog_generation.py` — calls `/admin/blog/generate` over HTTPS |
| Direct-DB ops utility | `backend/scripts/generate_blog_posts.py` — same generation logic run locally/on-server against Postgres directly; CI doesn't use this, kept for local dev or a machine with `localhost` DB access |
| Topic seed | `backend/app/seed/seed_blog_topics.py` |
| Automation | `.github/workflows/blog.yml` |

## Required setup

**On the server (cPanel environment variables for the Python app, same
panel `DATABASE_URL` already lives in):**

| Name | What |
|---|---|
| `OPENROUTER_API_KEY` | OpenRouter API key — generation now runs in the backend process, so this needs to be here, not in GitHub. |

After adding it, restart the app (touch `tmp/restart.txt`, or via cPanel's
Application Manager) so Passenger picks up the new environment variable.
The backend code that reads it (`backend/app/config.py`,
`backend/app/blog/`) needs to already be deployed — pull the latest
`backend/` changes through your normal deploy process first.

**One-time production migration:** the `blog_topic`/`blog_post` tables
don't exist yet — earlier attempts to create them via a CI-driven direct
`alembic upgrade head` never got far enough (that's what this pivot away
from). Run it once via cPanel Terminal (or however backend migrations
normally happen — see `DEPLOYMENT.md`):
```
cd <app root> && source <venv>/bin/activate && alembic upgrade head
```

**GitHub Actions secrets/variables:**

| Name | Kind | What |
|---|---|---|
| `ADMIN_API_USERNAME` | Secret | Same admin API username `admin.html` already authenticates with — copy the value from cPanel's `ADMIN_API_USERNAME` environment variable. |
| `ADMIN_API_PASSWORD` | Secret | Same, for the password. |
| `BLOG_API_BASE` | Variable | Your backend's public HTTPS base, e.g. `https://api.waterline.ng`. Defaults to that if unset. |
| `BLOG_SITE_BASE_URL` | Variable | Public site base for canonical/OG/sitemap links, e.g. `https://waterline.ng`. Defaults to that if unset. |

No database connection string is needed in GitHub at all anymore.
`GITHUB_TOKEN` (automatic) needs `contents: write` and `issues: write`,
already set in `blog.yml`'s `permissions:` block, for the auto-commit/push
and the run-summary issue.

**If you already opened Postgres to external connections** while on the
earlier direct-DB-connection design (firewall rule for 5432,
`listen_addresses`, `pg_hba.conf` entry): none of that is needed anymore
and it's worth reverting — closing 5432 back up in the firewall removes an
attack surface that this design no longer requires. The `BLOG_DATABASE_URL`
secret, if you added it, can be deleted from GitHub.

**A direct push to `main` from the workflow's `GITHUB_TOKEN` requires that
branch protection on `main` (if any) allows it.** If it doesn't, the push
step fails loudly rather than silently losing content — the generated
`BlogPost` rows are still in Postgres either way, just not yet built/shipped;
re-run `build_blog_static.py` and push manually, or adjust branch protection.

**A note on request duration:** `/admin/blog/generate` runs synchronously —
the HTTP request doesn't return until the writer/QA calls finish (there's
no background task queue in this codebase). Each post can mean up to 4
OpenRouter round trips (writer, QA, and a retry of both). Keep
`posts_per_run` low (the default is 1) so this stays well inside typical
reverse-proxy timeout windows; if requests start timing out at the
Apache/Passenger layer with a higher value, that's the fix.

**nginx cuts the request off at ~300s**, and two things keep that from
costing a day's post:

- *Reasoning is turned off in every request* (`NO_REASONING` in
  `openrouter_client.py`). Every free text model on OpenRouter is now
  reasoning-capable and most think by default — there is no plain instruct
  model left to pick — and left alone they spend minutes narrating before
  answering. That is what produced the 504 on 2026-09-16. The request also
  caps `max_tokens` and asks for JSON mode where the model supports it.
  These ride a *ladder*: on a 400/422 the client retries with one fewer
  parameter rather than dropping all of them, so a provider that rejects the
  reasoning flag doesn't also lose JSON mode.
- *A timeout is reconciled, not trusted.* When nginx gives up, the backend
  keeps going and usually commits the post a moment later — the answer is
  lost, not the work. `trigger_blog_generation.py` snapshots the posts list
  before triggering and, on a 504 or client timeout, polls it for up to 7
  minutes; if the post landed, the run publishes it and reports success
  instead of failing and opening an issue about work that succeeded. A 502
  is deliberately *not* reconciled — that is the backend's own considered
  "OpenRouter failed" answer, and nothing was written.

**A malformed model reply is not a crash.** An unparseable writer reply is
fed back as a failed attempt and retried; if every attempt is unparseable
the endpoint answers 502 naming the model. An unparseable *QA verdict*
keeps the draft and marks it `qa_failed` — a human can pass it in
`admin.html` — rather than throwing away a good post over a bad review.
Before this, either case escaped as a bare `500 Internal Server Error` with
no indication of which model or prompt caused it (2026-09-16, run 25).

## Extending it

- **Add a reference fact**: append an entry to
  `backend/app/blog/facts/nigeria_africa_credit_facts.json` (or add a new
  `*.json` file in that directory — all of them get loaded). Include
  `keywords`, `category`, `fact`, `source`, `as_of`.
- **Add a news source**: add its RSS URL to `blog_news_feed_urls`
  (comma-separated) in `backend/app/config.py` or via env var. Verify it's
  real RSS 2.0 first — `businessday.ng/feed/` was tried during design and
  403s to a generic fetch; Nairametrics and TechCabal are confirmed working.
- **Add a topic**: use the "Content queue" panel in `admin.html`'s Blog tab
  (add/remove pending topics without touching code), add a row to `TOPICS`
  in `backend/app/seed/seed_blog_topics.py`, or insert a `BlogTopic`
  directly (`prompt`, `category`, `target_keywords`, `priority`).
- **Change or refresh the writer/QA model chain**: `OPENROUTER_WRITER_MODEL` /
  `OPENROUTER_QA_MODEL` env vars (see `backend/.env.example`) — comma-separated,
  tried left to right, independently configurable for the two roles. Keep
  `openrouter/free` last (see the note at the top of this doc). Refresh the
  named entries ahead of it when the logs show the chain falling through, using
  the recipe under "When generation starts failing" below.

## When generation starts failing

Read the status code first — the three mean different things:

| Status | What it is | Where to look |
|---|---|---|
| `502` | `OpenRouterError` — the model call failed. Never a bug in the request path around it. | The steps below |
| `504` | nginx's ~300s ceiling, not the app. The post has usually landed anyway. | Reconciliation should have caught it; if the run still failed, nothing was generated |
| `500` | An unhandled exception in the app — a real bug, not a model problem | Server logs; the generator now handles malformed replies, so a 500 means something else |

For a `502`, work it in this order:

1. **Read the failure issue.** `notify.py` opens a "Blog run failed" issue
   per bad run, and it now carries the backend's response body, not just the
   status line — so the actual reason (`... returned 404: No endpoints found
   for model`, a 429, a bad key) is in the issue text. Before this was
   included, six days of failures all read as an identical bare
   "502 Bad Gateway" with nothing to act on; that is the whole reason the
   body is there.
2. **If it names a model, check whether that slug still exists.** Ask
   OpenRouter's own API rather than assuming a previously-known-good slug
   survived — this needs no API key:
   ```
   curl -s https://openrouter.ai/api/v1/models \
     | python3 -c "import json,sys; print('\n'.join(m['id'] for m in json.load(sys.stdin)['data'] if m['pricing']['prompt'] == '0'))"
   ```
   Filter on `pricing.prompt == "0"`, not on the `:free` suffix — some free
   models don't carry the suffix, and a `:free` id can outlive its free
   pricing. A model missing from that list is gone: drop it from the chain
   and put a current one at the front.
3. **If the whole chain failed, the error lists every attempt** — if they
   all 404, the chain is stale; if they all 429, the shared free pool is
   saturated and the next scheduled run will likely recover on its own.
4. **A 401/403 is the key, not the models.** The chain stops on those
   without trying the rest, so the message names it directly: re-check
   `OPENROUTER_API_KEY` in cPanel and restart the app.
5. **Only then reproduce on the server**, where a real traceback is visible:
   ```
   cd <app root> && source .venv/bin/activate
   python -c "
   from app.blog.openrouter_client import chat_completion
   print(chat_completion('<candidate-model>:free', 'You are a helpful assistant.', 'Say hello in one sentence.'))
   "
   ```
   Once a candidate responds, verify it actually follows the strict-JSON
   instruction before putting it at the front of the chain:
   ```
   python -c "
   import json
   from app.blog import knowledge_base, prompts
   from app.blog.openrouter_client import chat_completion
   facts = knowledge_base.relevant_facts('lending', 'loan')
   user_prompt = prompts.build_writer_prompt('A test topic', 'lending', facts, [])
   raw = chat_completion('<candidate-model>:free', prompts.WRITER_SYSTEM_PROMPT, user_prompt)
   json.loads(raw)  # raises if the model didn't return clean JSON
   print('OK')
   "
   ```
   `generator._extract_json` tolerates fenced and prose-padded replies, so a
   model that fails *this* strict check may still work in the pipeline — but
   a model that passes it is the safer bet.

A post records the model that actually wrote and reviewed it
(`writer_model` / `qa_model`, visible in `admin.html`'s Blog tab), which
with a fallback chain is not necessarily the first one configured. Those
fields are how you tell whether the chain has been quietly falling through.

## Local dry run

Two ways, matching the two data-source modes:

**Direct DB (fastest for local dev):**
```
cd backend
export OPENROUTER_API_KEY=... DATABASE_URL=postgresql+psycopg://waterline:waterline@localhost:5432/waterline
alembic upgrade head
python -m app.seed.seed_blog_topics
python scripts/generate_blog_posts.py --posts-per-run 1
python scripts/build_blog_static.py --source db
python3 -m http.server 5500   # from repo root, then open /blog/
```

**Via the HTTP API (matches what production CI actually does):**
```
cd backend && uvicorn app.main:app --reload   # in one terminal, with OPENROUTER_API_KEY set
# in another terminal:
export BLOG_API_BASE=http://localhost:8001 ADMIN_API_USERNAME=... ADMIN_API_PASSWORD=...
python scripts/trigger_blog_generation.py --posts-per-run 1
python scripts/build_blog_static.py --source api
python3 -m http.server 5500   # from repo root, then open /blog/
```
