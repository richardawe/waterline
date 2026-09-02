"""Admin-only control surface for the blog pipeline: view every post
regardless of status, edit content, and force-publish/archive. This is the
human safety valve for the auto-publish flow — exercised after the fact,
per the notification a run posts to GitHub."""

import json
from datetime import datetime, timezone

import markdown as markdown_lib
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.blog.sanitize import sanitize_html
from app.db import get_db
from app.models.blog import BlogPost, BlogTopic
from app.schemas import BlogPostUpdate, BlogTopicCreate
from app.security import require_admin

router = APIRouter(prefix="/admin/blog", tags=["admin-blog"], dependencies=[Depends(require_admin)])


def _post_to_dict(post: BlogPost) -> dict:
    return {
        "id": post.id,
        "slug": post.slug,
        "title": post.title,
        "meta_description": post.meta_description,
        "excerpt": post.excerpt,
        "tags": json.loads(post.tags_json) if post.tags_json else [],
        "content_markdown": post.content_markdown,
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


@router.get("/posts")
def list_posts(db: Session = Depends(get_db), status: str | None = None):
    stmt = select(BlogPost).order_by(BlogPost.created_at.desc())
    if status:
        stmt = stmt.where(BlogPost.status == status)
    return [_post_to_dict(p) for p in db.execute(stmt).scalars()]


@router.get("/posts/{post_id}")
def get_post(post_id: str, db: Session = Depends(get_db)):
    post = db.get(BlogPost, post_id)
    if post is None:
        raise HTTPException(404, "post not found")
    return _post_to_dict(post)


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
    return _post_to_dict(post)


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
    return _post_to_dict(post)


@router.post("/posts/{post_id}/archive")
def archive_post(post_id: str, db: Session = Depends(get_db)):
    post = db.get(BlogPost, post_id)
    if post is None:
        raise HTTPException(404, "post not found")
    post.status = "archived"
    db.commit()
    db.refresh(post)
    return _post_to_dict(post)


def _topic_to_dict(topic: BlogTopic) -> dict:
    return {
        "id": topic.id,
        "prompt": topic.prompt,
        "category": topic.category,
        "target_keywords": topic.target_keywords,
        "priority": topic.priority,
        "status": topic.status,
    }


@router.get("/topics")
def list_topics(db: Session = Depends(get_db), status: str | None = None):
    stmt = select(BlogTopic).order_by(BlogTopic.priority.desc(), BlogTopic.created_at)
    if status:
        stmt = stmt.where(BlogTopic.status == status)
    return [_topic_to_dict(t) for t in db.execute(stmt).scalars()]


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
    return _topic_to_dict(topic)


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
