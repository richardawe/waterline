"""Shared JSON shape for BlogPost/BlogTopic. Used by both the admin API
(app/api/blog_admin.py) and the CI scripts that now talk to that API over
HTTPS instead of connecting to Postgres directly — keeping one source of
truth for the wire format means the CI side can reconstruct equivalent
objects without duplicating field lists."""

import json

from app.models.blog import BlogPost, BlogTopic


def post_to_dict(post: BlogPost) -> dict:
    return {
        "id": post.id,
        "slug": post.slug,
        "title": post.title,
        "meta_description": post.meta_description,
        "excerpt": post.excerpt,
        "tags": json.loads(post.tags_json) if post.tags_json else [],
        "content_markdown": post.content_markdown,
        "content_html": post.content_html,
        "faq": json.loads(post.faq_json) if post.faq_json else [],
        "news_refs": json.loads(post.news_refs_json) if post.news_refs_json else [],
        "status": post.status,
        "writer_model": post.writer_model,
        "qa_model": post.qa_model,
        "qa_verdict": json.loads(post.qa_verdict_json) if post.qa_verdict_json else None,
        "qa_attempts": post.qa_attempts,
        "word_count": post.word_count,
        "reading_minutes": post.reading_minutes,
        "published_at": post.published_at.isoformat() if post.published_at else None,
        "created_at": post.created_at.isoformat() if post.created_at else None,
        "updated_at": post.updated_at.isoformat() if post.updated_at else None,
    }


def topic_to_dict(topic: BlogTopic) -> dict:
    return {
        "id": topic.id,
        "prompt": topic.prompt,
        "category": topic.category,
        "target_keywords": topic.target_keywords,
        "priority": topic.priority,
        "status": topic.status,
    }
