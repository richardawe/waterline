# Automated finance blog — pipeline reference

An automated blog on credit, loans and personal/SME finance in Nigeria and
across Africa, written by a free OpenRouter model, reviewed by a second
model against a hard factual-grounding rubric, then auto-published as static
HTML through the existing FTP deploy pipeline. This is the operational
reference; the original design plan (context/trade-offs) lived in the
session that built it — this doc is what to read when operating or
extending the pipeline day to day.

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
.github/workflows/blog.yml (cron, 3x/week, or workflow_dispatch)
  1. alembic upgrade head                     — ensure blog_topic/blog_post tables exist
  2. python -m app.seed.seed_blog_topics       — idempotent: only inserts new prompts
  3. python scripts/generate_blog_posts.py     — for each of N pending topics:
       a. knowledge_base.relevant_facts()      — curated reference facts (backend/app/blog/facts/*.json)
       b. news_feed.fetch_recent_items()        — recent items from configured RSS feeds
       c. writer model (OpenRouter)             — drafts title/body/FAQ/news section as JSON
       d. QA model (OpenRouter, different model)— reviews draft against the same facts/news; verdict JSON
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
| `BLOG_DATABASE_URL` | Secret | Same production Postgres the backend already uses — content must persist there, not in an ephemeral CI database. This workflow also runs `alembic upgrade head` against it. |
| `OPENROUTER_API_KEY` | Secret | OpenRouter API key (free tier is enough for the default models). |
| `BLOG_SITE_BASE_URL` | Variable | Absolute base URL for canonical/OG/sitemap links, e.g. `https://waterline.ng`. Defaults to that value if unset. |

`GITHUB_TOKEN` (automatic) needs `contents: write` and `issues: write`,
already set in `blog.yml`'s `permissions:` block, for the auto-commit/push
and the run-summary issue.

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
  `OPENROUTER_QA_MODEL` env vars (see `backend/.env.example`). Free-tier
  model availability on OpenRouter rotates — if the default starts 404ing,
  swap in whatever's currently free.

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
