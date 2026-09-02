"""Generates the public static blog site (`/blog/*`, `sitemap.xml`,
`robots.txt`, `/blog/rss.xml`) from published BlogPost rows.

Deliberately plain Python string templates, not Jinja2 — the rest of the
frontend is hand-rolled static HTML with no build step or templating engine
(see AGENTS.md), and one template doesn't justify a new dependency.

Two data sources:
- `api` (default, what CI uses): fetches published posts from the live
  admin API over HTTPS (GET /admin/blog/posts?status=published) and
  reconstructs transient BlogPost objects from the JSON — no direct
  database connection, so this never needs Postgres exposed to the
  internet. Needs BLOG_API_BASE/ADMIN_API_USERNAME/ADMIN_API_PASSWORD.
- `db`: queries Postgres directly, for local dev convenience when the API
  isn't running. Needs DATABASE_URL.

Run from `backend/`: `python scripts/build_blog_static.py`.
"""

import html as html_lib
import json
import os
import sys
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.models.blog import BlogPost

REPO_ROOT = Path(__file__).resolve().parents[2]
BLOG_DIR = REPO_ROOT / "blog"

NAV = (
    '<nav><div class="nav-inner"><a class="brand" href="/"><span class="brand-mark">W</span>Waterline</a>'
    '<button class="nav-toggle" id="nav-toggle" aria-label="Open navigation" aria-expanded="false">☰</button>'
    '<div class="nav-links" id="nav-links"><a href="/blog/">Blog</a><a href="/#access">Request access</a>'
    '<a href="/preview/">Enter preview</a></div></div></nav>'
)
NAV_SCRIPT = (
    "<script>const toggle=document.getElementById('nav-toggle'),links=document.getElementById('nav-links');"
    "toggle.addEventListener('click',()=>{const open=links.classList.toggle('open');"
    "toggle.setAttribute('aria-expanded',String(open))});"
    "links.querySelectorAll('a').forEach(a=>a.addEventListener('click',()=>links.classList.remove('open')));</script>"
)

PAGE_STYLE = """
.blog-wrap{padding:56px 0 90px}.blog-head{max-width:700px;margin-bottom:40px}
.blog-head h1{font-size:clamp(29px,3.7vw,44px);line-height:1.12}.blog-head p{margin-top:12px;color:var(--text-dim);font-size:16px}
.blog-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}
.blog-card{display:block;padding:22px;border:1px solid var(--border);border-radius:var(--r-lg);background:var(--surface);box-shadow:var(--shadow-card);text-decoration:none}
.blog-card .kicker{margin-bottom:10px}.blog-card h2{font-size:19px;line-height:1.3}.blog-card p{margin-top:9px;color:var(--text-dim);font-size:14px}
.blog-card .meta{margin-top:14px;color:var(--text-faint);font-size:11px}
.article-wrap{max-width:760px;margin:0 auto;padding:48px 0 90px}
.article-head h1{font-size:clamp(28px,4vw,42px);line-height:1.15}
.article-meta{display:flex;gap:14px;flex-wrap:wrap;margin-top:16px;color:var(--text-faint);font-size:12px}
.article-tags{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
.article-disclosure{margin-top:24px;padding:14px 16px;border:1px solid var(--border);border-radius:var(--r-md);background:var(--surface-subtle);color:var(--text-dim);font-size:13px}
.article-body{margin-top:34px;font-size:16px;line-height:1.75;color:var(--text)}
.article-body h2{margin-top:38px;margin-bottom:12px;font-size:23px}.article-body h3{margin-top:26px;margin-bottom:10px;font-size:18px}
.article-body p{margin-top:14px}.article-body ul,.article-body ol{margin-top:14px;padding-left:22px}.article-body li{margin-top:6px}
.article-body a{color:var(--accent)}
.faq-block{margin-top:40px;padding-top:30px;border-top:1px solid var(--border)}
.faq-item{margin-top:16px}.faq-item strong{display:block;color:var(--navy);font-size:15px}.faq-item p{margin-top:6px;color:var(--text-dim);font-size:14px}
.sources-block{margin-top:40px;padding-top:24px;border-top:1px solid var(--border);font-size:13px;color:var(--text-dim)}
.sources-block li{margin-top:6px}
.footer-row{display:flex;justify-content:space-between;gap:20px;align-items:center}.footer-row a{color:var(--text-dim);text-decoration:none}
@media(max-width:720px){.blog-grid{grid-template-columns:1fr}.footer-row{align-items:flex-start;flex-direction:column}}
"""


def _escape(text: str | None) -> str:
    return html_lib.escape(text or "", quote=True)


def _json_ld_safe(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def _page_shell(title: str, description: str, canonical: str, extra_head: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="{_escape(description)}">
<title>{_escape(title)}</title>
<link rel="canonical" href="{_escape(canonical)}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/assets/theme.css">
<style>{PAGE_STYLE}</style>
{extra_head}
</head>
<body>
{NAV}
<main>
{body}
</main>
<footer><div class="wrap footer-row"><span>© 2026 Waterline. Lagos, Nigeria.</span><a href="mailto:richard.awe@3d7tech.com">richard.awe@3d7tech.com</a></div></footer>
{NAV_SCRIPT}
</body>
</html>
"""


def _build_index(posts: list[BlogPost], base_url: str) -> str:
    cards = []
    for post in posts:
        tags = json.loads(post.tags_json) if post.tags_json else []
        kicker = tags[0] if tags else "Finance"
        cards.append(
            f"""<a class="blog-card" href="/blog/{post.slug}/">
<span class="kicker">{_escape(kicker)}</span>
<h2>{_escape(post.title)}</h2>
<p>{_escape(post.excerpt)}</p>
<div class="meta">{post.published_at.strftime('%d %b %Y') if post.published_at else ''} · {post.reading_minutes} min read</div>
</a>"""
        )
    body = f"""<div class="wrap blog-wrap">
<div class="blog-head"><div class="kicker">Waterline Blog</div>
<h1>Credit, loans and finance in Nigeria and Africa, explained.</h1>
<p>Factual, plain-English articles on how lending, credit scoring and structured finance actually work — drafted with AI assistance and automated fact-review. Verify current rates and regulations independently before relying on this for a financial decision.</p></div>
<div class="blog-grid">{''.join(cards) if cards else '<p>New articles are on the way.</p>'}</div>
</div>"""
    return _page_shell(
        "Waterline Blog — Credit & finance in Nigeria and Africa",
        "Factual articles on credit, loans and finance in Nigeria and across Africa.",
        f"{base_url}/blog/",
        "",
        body,
    )


def _build_article(post: BlogPost, base_url: str) -> str:
    tags = json.loads(post.tags_json) if post.tags_json else []
    faq = json.loads(post.faq_json) if post.faq_json else []
    news_refs = json.loads(post.news_refs_json) if post.news_refs_json else []
    canonical = f"{base_url}/blog/{post.slug}/"
    published_iso = post.published_at.isoformat() if post.published_at else ""
    updated_iso = post.updated_at.isoformat() if post.updated_at else published_iso

    article_ld = _json_ld_safe(
        {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": post.title,
            "description": post.meta_description,
            "datePublished": published_iso,
            "dateModified": updated_iso,
            "author": {"@type": "Organization", "name": "Waterline"},
            "publisher": {"@type": "Organization", "name": "Waterline"},
            "mainEntityOfPage": canonical,
        }
    )
    extra_head = f'<meta property="og:type" content="article">\n<meta property="og:title" content="{_escape(post.title)}">\n<meta property="og:description" content="{_escape(post.meta_description)}">\n<meta property="og:url" content="{_escape(canonical)}">\n<meta name="twitter:card" content="summary">\n<script type="application/ld+json">{article_ld}</script>'

    if faq:
        faq_ld = _json_ld_safe(
            {
                "@context": "https://schema.org",
                "@type": "FAQPage",
                "mainEntity": [
                    {
                        "@type": "Question",
                        "name": item.get("question", ""),
                        "acceptedAnswer": {"@type": "Answer", "text": item.get("answer", "")},
                    }
                    for item in faq
                ],
            }
        )
        extra_head += f'\n<script type="application/ld+json">{faq_ld}</script>'

    tag_chips = "".join(f'<span class="chip">{_escape(t)}</span>' for t in tags)
    faq_html = "".join(
        f'<div class="faq-item"><strong>{_escape(item.get("question",""))}</strong><p>{_escape(item.get("answer",""))}</p></div>'
        for item in faq
    )
    sources_html = "".join(
        f'<li><a href="{_escape(n.get("link",""))}">{_escape(n.get("title",""))}</a> — {_escape(n.get("source",""))}'
        f'{", " + n["published"][:10] if n.get("published") else ""}</li>'
        for n in news_refs
    )

    body = f"""<div class="wrap article-wrap">
<article>
<div class="article-head">
<h1>{_escape(post.title)}</h1>
<div class="article-meta"><span>{post.published_at.strftime('%d %B %Y') if post.published_at else ''}</span><span>{post.reading_minutes} min read</span></div>
<div class="article-tags">{tag_chips}</div>
<div class="article-disclosure">Drafted with AI assistance and reviewed by an automated fact-check pass against curated reference data and recent news — not personalized financial or legal advice. Verify current rates and regulations independently before relying on this for a decision.</div>
</div>
<div class="article-body">{post.content_html or ""}</div>
{f'<div class="faq-block"><h2>FAQ</h2>{faq_html}</div>' if faq else ''}
{f'<div class="sources-block"><strong>Sources</strong><ul>{sources_html}</ul></div>' if news_refs else ''}
</article>
</div>"""
    return _page_shell(f"{post.title} — Waterline Blog", post.meta_description, canonical, extra_head, body)


def _build_sitemap(posts: list[BlogPost], base_url: str) -> str:
    urls = [f"{base_url}/", f"{base_url}/blog/"] + [f"{base_url}/blog/{p.slug}/" for p in posts]
    entries = "".join(f"<url><loc>{_escape(u)}</loc></url>" for u in urls)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>\n'


def _build_robots(base_url: str) -> str:
    return f"""User-agent: *
Allow: /
Allow: /blog/
Disallow: /preview/
Disallow: /admin.html

User-agent: GPTBot
Allow: /
Allow: /blog/

User-agent: ClaudeBot
Allow: /
Allow: /blog/

User-agent: PerplexityBot
Allow: /
Allow: /blog/

User-agent: Google-Extended
Allow: /
Allow: /blog/

Sitemap: {base_url}/sitemap.xml
"""


def _build_rss(posts: list[BlogPost], base_url: str) -> str:
    items = []
    for post in posts[:20]:
        pub = format_datetime(post.published_at) if post.published_at else ""
        items.append(
            f"<item><title>{_escape(post.title)}</title><link>{base_url}/blog/{post.slug}/</link>"
            f"<guid>{base_url}/blog/{post.slug}/</guid><pubDate>{pub}</pubDate>"
            f"<description>{_escape(post.meta_description)}</description></item>"
        )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Waterline Blog</title>
<link>{base_url}/blog/</link>
<description>Credit, loans and finance in Nigeria and Africa.</description>
{''.join(items)}
</channel></rss>
"""


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _post_from_api_dict(item: dict) -> BlogPost:
    """Reconstructs a transient (unpersisted) BlogPost from the admin API's
    JSON shape (app.blog.serialize.post_to_dict) so the template functions
    below — which only ever read attributes, never touch the DB — work
    identically regardless of data source."""
    return BlogPost(
        id=item["id"],
        slug=item["slug"],
        title=item["title"],
        meta_description=item["meta_description"],
        excerpt=item["excerpt"],
        tags_json=json.dumps(item.get("tags") or []),
        content_markdown=item["content_markdown"],
        content_html=item.get("content_html"),
        faq_json=json.dumps(item.get("faq") or []),
        news_refs_json=json.dumps(item.get("news_refs") or []),
        word_count=item.get("word_count", 0),
        reading_minutes=item.get("reading_minutes", 1),
        published_at=_parse_dt(item.get("published_at")),
        updated_at=_parse_dt(item.get("updated_at")) or _parse_dt(item.get("published_at")),
    )


def _fetch_published_posts_via_api() -> list[BlogPost]:
    base_url = os.environ.get("BLOG_API_BASE", "").rstrip("/")
    username = os.environ.get("ADMIN_API_USERNAME")
    password = os.environ.get("ADMIN_API_PASSWORD")
    if not base_url or not username or not password:
        raise SystemExit("BLOG_API_BASE, ADMIN_API_USERNAME and ADMIN_API_PASSWORD must all be set for --source api")

    response = httpx.get(
        f"{base_url}/admin/blog/posts", params={"status": "published"}, auth=(username, password), timeout=60
    )
    response.raise_for_status()
    posts = [_post_from_api_dict(item) for item in response.json()]
    posts.sort(key=lambda p: p.published_at or datetime.min, reverse=True)
    return posts


def _fetch_published_posts_via_db() -> list[BlogPost]:
    from sqlalchemy import select

    from app.db import SessionLocal

    db = SessionLocal()
    try:
        return list(
            db.execute(
                select(BlogPost).where(BlogPost.status == "published").order_by(BlogPost.published_at.desc())
            ).scalars()
        )
    finally:
        db.close()


def build(source: str = "api") -> None:
    settings = get_settings()
    base_url = settings.blog_site_base_url.rstrip("/")

    posts = _fetch_published_posts_via_api() if source == "api" else _fetch_published_posts_via_db()

    BLOG_DIR.mkdir(exist_ok=True)
    (BLOG_DIR / "index.html").write_text(_build_index(posts, base_url))
    (BLOG_DIR / "rss.xml").write_text(_build_rss(posts, base_url))

    for post in posts:
        post_dir = BLOG_DIR / post.slug
        post_dir.mkdir(exist_ok=True)
        (post_dir / "index.html").write_text(_build_article(post, base_url))

    (REPO_ROOT / "sitemap.xml").write_text(_build_sitemap(posts, base_url))
    (REPO_ROOT / "robots.txt").write_text(_build_robots(base_url))

    print(f"built {len(posts)} post page(s) into {BLOG_DIR}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["api", "db"], default=os.environ.get("BLOG_STATIC_SOURCE", "api"))
    args = parser.parse_args()
    build(args.source)
