# Automated finance blog — pipeline reference

An automated blog on credit, loans and personal/SME finance in Nigeria and
across Africa, written by a free OpenRouter model (`openai/gpt-oss-20b:free`
— the free-tier model that was actually confirmed working; several others
were tried and didn't), reviewed by a second pass of the same model against
a hard factual-grounding rubric, then auto-published as static HTML through
the existing FTP deploy pipeline. This is the operational reference; the
original design plan (context/trade-offs) lived in the session that built
it — this doc is what to read when operating or extending the pipeline day
to day.

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

## Architecture

```
.github/workflows/blog.yml (cron, daily 08:00 UTC, or workflow_dispatch)
  1. alembic upgrade head                     — ensure blog_topic/blog_post tables exist
  2. python -m app.seed.seed_blog_topics       — idempotent: only inserts new prompts
  3. python scripts/generate_blog_posts.py     — for each of N pending topics:
       a. knowledge_base.relevant_facts()      — curated reference facts (backend/app/blog/facts/*.json)
       b. news_feed.fetch_recent_items()        — recent items from configured RSS feeds
       c. writer pass (OpenRouter, gpt-oss-20b:free) — drafts title/body/FAQ/news section as JSON
       d. QA pass (OpenRouter, gpt-oss-20b:free)     — reviews draft against the same facts/news; verdict JSON
       e. fail -> feed QA issues back to the writer, retry once, then give up
       f. save BlogPost: status=published (QA pass) or qa_failed (still failing after retry)
  4. python scripts/build_blog_static.py       — regenerates /blog/*, sitemap.xml, robots.txt from published posts
  5. commit + push blog/, sitemap.xml, robots.txt to main
       -> existing deploy.yml triggers on that push, FTPs the static site as always
  6. notify.py                                  — opens a GitHub issue summarizing the run
```

`admin.html`'s **Blog** tab (`/admin/blog/*` API, admin-authenticated) shows
every post regardless of status, with QA notes, and lets you edit content,
force-publish a `qa_failed` post, or archive a `published` one. Force-publish
only flips the DB row — re-run `build_blog_static.py` (or wait for the next
scheduled `blog.yml` run) to actually ship the change.

## Where things live

| Concern | Path |
|---|---|
| Data models | `backend/app/models/blog.py` (`BlogTopic`, `BlogPost`) |
| Generation pipeline | `backend/app/blog/` (`openrouter_client.py`, `knowledge_base.py`, `news_feed.py`, `prompts.py`, `generator.py`, `notify.py`, `sanitize.py`) |
| Curated reference facts | `backend/app/blog/facts/*.json` — human-edited, git-tracked, small |
| Admin API | `backend/app/api/blog_admin.py` (`/admin/blog/posts`, `/admin/blog/topics`) |
| Static site build | `backend/scripts/build_blog_static.py` → `blog/`, `sitemap.xml`, `robots.txt` at repo root |
| CI entrypoint | `backend/scripts/generate_blog_posts.py` |
| Topic seed | `backend/app/seed/seed_blog_topics.py` |
| Automation | `.github/workflows/blog.yml` |

## Required secrets/variables (GitHub Actions)

| Name | Kind | What |
|---|---|---|
| `BLOG_DATABASE_URL` | Secret | The **same production Postgres database the backend already uses** — set it to that exact connection string. There is no separate blog database; `BlogTopic`/`BlogPost` are two more tables in the same schema (same `alembic` chain, same `Base.metadata` as `Institution`, `Deal`, etc. — see `backend/app/models/blog.py`). Content must persist there, not in an ephemeral CI database. This workflow also runs `alembic upgrade head` against it, which is what actually creates the two blog tables the first time it runs. |
| `OPENROUTER_API_KEY` | Secret | OpenRouter API key (free tier is enough — both roles run on `openai/gpt-oss-20b:free`). |
| `BLOG_SITE_BASE_URL` | Variable | Absolute base URL for canonical/OG/sitemap links, e.g. `https://waterline.ng`. Defaults to that value if unset. |

`GITHUB_TOKEN` (automatic) needs `contents: write` and `issues: write`,
already set in `blog.yml`'s `permissions:` block, for the auto-commit/push
and the run-summary issue.

**Why a separate secret for the same database:** production `DATABASE_URL`
lives only in cPanel's "Environment variables" panel for the Python app
(per `DEPLOYMENT.md`) — GitHub Actions has no access to that. `blog.yml`
runs on GitHub's own runners, so it needs its own copy of that same
connection string as a GitHub secret, and that connection string has to be
reachable from the public internet (GitHub-hosted runners, not `localhost`)
— confirm your Postgres host allows external connections before relying on
the scheduled runs.

**Opening Postgres to external connections (cPanel/WHM):** the production
server's Postgres, like most cPanel installs, defaults to accepting
connections only from `localhost` — which is why `BLOG_DATABASE_URL` can't
just reuse `localhost` the way the app's own `DATABASE_URL` does. To open
it up (requires WHM/root access, or your host's support team):

1. Check cPanel first for a **Remote PostgreSQL** panel (the Postgres
   equivalent of the common "Remote MySQL" feature) — if present, this is
   the self-service path: add access for the database user.
2. If not available, via WHM/server root:
   - `postgresql.conf` (typically `/var/lib/pgsql/data/postgresql.conf` or
     `/var/lib/pgsql/<version>/data/postgresql.conf`): set
     `listen_addresses = '*'`.
   - `pg_hba.conf` (same directory): add a line scoping access to the
     specific user/database rather than opening everything, e.g.
     `host    <dbname>    <dbuser>    0.0.0.0/0    scram-sha-256`
     (GitHub-hosted runners don't have a fixed, allowlist-able IP range, so
     the password — and SSL — are the real security boundary here, not the
     source IP).
   - Restart PostgreSQL (`systemctl restart postgresql`, or via WHM's
     "Restart Services").
   - Open port 5432 in the firewall (CSF: add `5432` to `TCP_IN` in
     `/etc/csf/csf.conf`, then `csf -r`; firewalld:
     `firewall-cmd --permanent --add-port=5432/tcp && firewall-cmd --reload`).
3. Use a strong, dedicated password for this user and append
   `?sslmode=require` to `BLOG_DATABASE_URL` if the server supports SSL —
   worth doing given the port faces the internet.
4. Sanity-check reachability independently of the workflow before relying
   on it: `psql "postgresql://<user>:<password>@<host>:5432/<dbname>?sslmode=require"`
   or, at minimum, `nc -zv <host> 5432` from any external machine.

**A direct push to `main` from the workflow's `GITHUB_TOKEN` requires that
branch protection on `main` (if any) allows it.** If it doesn't, the push
step fails loudly rather than silently losing content — the generated
`BlogPost` rows are still in Postgres either way, just not yet built/shipped;
re-run `build_blog_static.py` and push manually, or adjust branch protection.

## Extending it

- **Add a reference fact**: append an entry to
  `backend/app/blog/facts/nigeria_africa_credit_facts.json` (or add a new
  `*.json` file in that directory — all of them get loaded). Include
  `keywords`, `category`, `fact`, `source`, `as_of`.
- **Add a news source**: add its RSS URL to `blog_news_feed_urls`
  (comma-separated) in `backend/app/config.py` or via env var. Verify it's
  real RSS 2.0 first — `businessday.ng/feed/` was tried during design and
  403s to a generic fetch; Nairametrics and TechCabal are confirmed working.
- **Add a topic**: add a row to `TOPICS` in
  `backend/app/seed/seed_blog_topics.py`, or insert a `BlogTopic` directly
  (`prompt`, `category`, `target_keywords`, `priority`).
- **Change the writer/QA model**: `OPENROUTER_WRITER_MODEL` /
  `OPENROUTER_QA_MODEL` env vars (see `backend/.env.example`), independently
  configurable even though both currently point at `openai/gpt-oss-20b:free`.
  Free-tier model availability on OpenRouter rotates — if that starts
  404ing/erroring, swap in whatever's currently free and actually working
  (test it directly against OpenRouter's API first — several other
  "free" models were tried while building this and didn't work).

## Local dry run

```
cd backend
export OPENROUTER_API_KEY=... DATABASE_URL=postgresql+psycopg://waterline:waterline@localhost:5432/waterline
alembic upgrade head
python -m app.seed.seed_blog_topics
python scripts/generate_blog_posts.py --posts-per-run 1
python scripts/build_blog_static.py
python3 -m http.server 5500   # from repo root, then open /blog/
```
