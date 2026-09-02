"""Admin-only control surface for the blog pipeline: view every post
regardless of status, edit content, and force-publish/archive. This is the
human safety valve for the auto-publish flow — exercised after the fact,
per the notification a run posts to GitHub.

Also where generation itself now runs (`/generate`): the backend process
already has `localhost` access to Postgres, so doing the OpenRouter
writer/QA work here — triggered over HTTPS by CI — means the database never
needs to be exposed to the internet. See docs/blog-pipeline.md."""

import json
from datetime import datetime, timezone

import markdown as markdown_lib
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.blog.generator import generate_one
from app.blog.openrouter_client import OpenRouterError
from app.blog.sanitize import sanitize_html
from app.blog.serialize import post_to_dict, topic_to_dict
from app.db import get_db
from app.models.blog import BlogPost, BlogTopic
from app.schemas import BlogPostUpdate, BlogTopicCreate
from app.security import require_admin

router = APIRouter(prefix="/admin/blog", tags=["admin-blog"], dependencies=[Depends(require_admin)])

MAX_POSTS_PER_REQUEST = 3  # generation runs synchronously in this HTTP request — keep it bounded


@router.get("/posts")
def list_posts(db: Session = Depends(get_db), status: str | None = None):
    stmt = select(BlogPost).order_by(BlogPost.created_at.desc())
    if status:
        stmt = stmt.where(BlogPost.status == status)
    return [post_to_dict(p) for p in db.execute(stmt).scalars()]


@router.get("/posts/{post_id}")
def get_post(post_id: str, db: Session = Depends(get_db)):
    post = db.get(BlogPost, post_id)
    if post is None:
        raise HTTPException(404, "post not found")
    return post_to_dict(post)


@router.patch("/posts/{post_id}")
def update_post(post_id: str, body: BlogPostUpdate, db: Session = Depends(get_db)):
    post = db.get(BlogPost, post_id)
    if post is None:
        raise HTTPException(404, "post not found")

    if body.title is not None:
        post.title = body.title
    if body.meta_description is not None:
        post.meta_description = body.meta_description
    if body.excerpt is not None:
        post.excerpt = body.excerpt
    if body.tags is not None:
        post.tags_json = json.dumps(body.tags)
    if body.faq is not None:
        post.faq_json = json.dumps(body.faq)
    if body.content_markdown is not None:
        post.content_markdown = body.content_markdown
        post.content_html = sanitize_html(markdown_lib.markdown(body.content_markdown, extensions=["fenced_code", "tables"]))
        post.word_count = len(body.content_markdown.split())
        post.reading_minutes = max(1, round(post.word_count / 200))

    db.commit()
    db.refresh(post)
    return post_to_dict(post)


@router.post("/posts/{post_id}/publish")
def force_publish(post_id: str, db: Session = Depends(get_db)):
    post = db.get(BlogPost, post_id)
    if post is None:
        raise HTTPException(404, "post not found")
    post.status = "published"
    if post.published_at is None:
        post.published_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(post)
    return post_to_dict(post)


@router.post("/posts/{post_id}/archive")
def archive_post(post_id: str, db: Session = Depends(get_db)):
    post = db.get(BlogPost, post_id)
    if post is None:
        raise HTTPException(404, "post not found")
    post.status = "archived"
    db.commit()
    db.refresh(post)
    return post_to_dict(post)


@router.post("/generate")
def generate_posts(posts_per_run: int = 1, db: Session = Depends(get_db)):
    """Runs writer -> QA -> publish/qa_failed synchronously, server-side,
    where Postgres and OpenRouter are both already reachable. This is the
    call CI makes over HTTPS instead of connecting to the database itself.

    Runs in-request (no task queue in this codebase), so `posts_per_run` is
    capped — a slow OpenRouter free-tier round trip times MAX_QA_ATTEMPTS
    per post can otherwise run long enough to hit a reverse-proxy timeout.
    """
    n = max(1, min(posts_per_run, MAX_POSTS_PER_REQUEST))
    published, qa_failed = [], []
    try:
        for _ in range(n):
            post = generate_one(db)
            if post is None:
                break  # no more pending topics
            (published if post.status == "published" else qa_failed).append(post)
    except OpenRouterError as exc:
        raise HTTPException(502, f"OpenRouter error: {exc}") from exc

    return {
        "published": [post_to_dict(p) for p in published],
        "qa_failed": [post_to_dict(p) for p in qa_failed],
    }


@router.post("/seed-topics")
def seed_topics():
    """Idempotent — only inserts prompts from TOPICS that aren't already in
    the queue. Lets CI top up the starter content calendar over HTTPS
    without a direct DB connection, same as /generate."""
    from app.seed.seed_blog_topics import seed

    seed()
    return {"seeded": True}


@router.get("/topics")
def list_topics(db: Session = Depends(get_db), status: str | None = None):
    stmt = select(BlogTopic).order_by(BlogTopic.priority.desc(), BlogTopic.created_at)
    if status:
        stmt = stmt.where(BlogTopic.status == status)
    return [topic_to_dict(t) for t in db.execute(stmt).scalars()]


@router.post("/topics")
def create_topic(body: BlogTopicCreate, db: Session = Depends(get_db)):
    prompt = body.prompt.strip()
    category = body.category.strip()
    if not prompt or not category:
        raise HTTPException(422, "prompt and category are required")

    topic = BlogTopic(
        prompt=prompt,
        category=category,
        target_keywords=(body.target_keywords or "").strip() or None,
        priority=body.priority,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return topic_to_dict(topic)


@router.delete("/topics/{topic_id}")
def remove_topic(topic_id: str, db: Session = Depends(get_db)):
    """Only for topics the generator hasn't touched yet (status=pending) —
    once a topic has produced a post, its history stays put rather than
    being deleted out from under that post's topic_id foreign key."""
    topic = db.get(BlogTopic, topic_id)
    if topic is None:
        raise HTTPException(404, "topic not found")
    if topic.status != "pending":
        raise HTTPException(409, "only pending topics can be removed")
    db.delete(topic)
    db.commit()
    return {"deleted": True}
