"""Static-build helpers only — never calls build() itself, which writes into
the real repo tree (blog/, sitemap.xml, robots.txt) and would pollute the
working directory during a test run."""

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from app.models.blog import BlogPost

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "build_blog_static.py"
spec = importlib.util.spec_from_file_location("build_blog_static", SCRIPT_PATH)
build_blog_static = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_blog_static)


def _post(**overrides) -> BlogPost:
    defaults = dict(
        slug="how-crc-credit-bureau-works",
        title="How CRC Credit Bureau Works",
        meta_description="A short, factual explainer.",
        excerpt="CRC compiles Nigerian credit history.",
        content_markdown="# Heading\n\nBody text.",
        content_html="<h1>Heading</h1><p>Body text.</p>",
        tags_json=json.dumps(["credit bureau", "nigeria"]),
        faq_json=json.dumps([{"question": "What is it?", "answer": "A credit bureau."}]),
        news_refs_json=json.dumps([{"title": "CBN news", "link": "https://example.com/a", "source": "example.com", "published": "2026-08-01T00:00:00+00:00"}]),
        word_count=500,
        reading_minutes=3,
        published_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    post = BlogPost(**defaults)
    post.updated_at = defaults["published_at"]
    return post


def test_build_sitemap_includes_home_blog_and_posts():
    xml = build_blog_static._build_sitemap([_post()], "https://waterline.ng")
    assert "<loc>https://waterline.ng/</loc>" in xml
    assert "<loc>https://waterline.ng/blog/</loc>" in xml
    assert "<loc>https://waterline.ng/blog/how-crc-credit-bureau-works/</loc>" in xml


def test_build_robots_allows_blog_and_ai_crawlers_blocks_private_pages():
    robots = build_blog_static._build_robots("https://waterline.ng")
    assert "Disallow: /preview/" in robots
    assert "Disallow: /admin.html" in robots
    assert "GPTBot" in robots
    assert "Sitemap: https://waterline.ng/sitemap.xml" in robots


def test_build_rss_contains_post_title_and_link():
    rss = build_blog_static._build_rss([_post()], "https://waterline.ng")
    assert "How CRC Credit Bureau Works" in rss
    assert "https://waterline.ng/blog/how-crc-credit-bureau-works/" in rss


def test_build_article_embeds_valid_article_and_faq_json_ld():
    html = build_blog_static._build_article(_post(), "https://waterline.ng")
    ld_blocks = html.split('<script type="application/ld+json">')[1:]
    parsed = [json.loads(block.split("</script>")[0]) for block in ld_blocks]
    types = {p["@type"] for p in parsed}
    assert {"Article", "FAQPage"} <= types


def test_escape_neutralizes_html_special_characters():
    assert build_blog_static._escape('<script>"quote"</script>') == "&lt;script&gt;&quot;quote&quot;&lt;/script&gt;"


def _api_post_dict(**overrides) -> dict:
    defaults = {
        "id": "abc123",
        "slug": "how-nibss-works",
        "title": "How NIBSS Works",
        "meta_description": "A short explainer.",
        "excerpt": "NIBSS underpins interbank payments.",
        "content_markdown": "# Heading\n\nBody.",
        "content_html": "<h1>Heading</h1><p>Body.</p>",
        "tags": ["payments"],
        "faq": [{"question": "What is it?", "answer": "An operator."}],
        "news_refs": [],
        "word_count": 400,
        "reading_minutes": 2,
        "published_at": "2026-08-01T00:00:00+00:00",
        "updated_at": "2026-08-02T00:00:00+00:00",
    }
    defaults.update(overrides)
    return defaults


def test_post_from_api_dict_reconstructs_equivalent_blogpost():
    post = build_blog_static._post_from_api_dict(_api_post_dict())
    assert post.slug == "how-nibss-works"
    assert json.loads(post.tags_json) == ["payments"]
    assert json.loads(post.faq_json)[0]["question"] == "What is it?"
    assert post.published_at == datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert post.content_html == "<h1>Heading</h1><p>Body.</p>"


def test_fetch_published_posts_via_api_uses_basic_auth(monkeypatch):
    monkeypatch.setenv("BLOG_API_BASE", "https://api.example.com")
    monkeypatch.setenv("ADMIN_API_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_API_PASSWORD", "secret")

    mock_response = Mock()
    mock_response.raise_for_status = Mock()
    mock_response.json.return_value = [_api_post_dict(slug="a", published_at="2026-08-01T00:00:00+00:00"), _api_post_dict(slug="b", published_at="2026-08-05T00:00:00+00:00")]

    with patch.object(build_blog_static.httpx, "get", return_value=mock_response) as mock_get:
        posts = build_blog_static._fetch_published_posts_via_api()

    assert mock_get.call_args[1]["auth"] == ("admin", "secret")
    assert mock_get.call_args[1]["params"] == {"status": "published"}
    # newest published_at first
    assert [p.slug for p in posts] == ["b", "a"]


def test_fetch_published_posts_via_api_requires_env_vars(monkeypatch):
    monkeypatch.delenv("BLOG_API_BASE", raising=False)
    try:
        build_blog_static._fetch_published_posts_via_api()
        assert False, "expected SystemExit"
    except SystemExit:
        pass
