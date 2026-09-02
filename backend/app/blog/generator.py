"""Orchestrates one generation cycle: pick a topic, ground it in curated
facts + recent news, write a draft via OpenRouter, QA-review it, and persist
the result. Designed to be called once per topic per `blog.yml` run."""

import json
import logging
import re
from datetime import datetime, timezone

import markdown as markdown_lib
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.blog import knowledge_base, news_feed, prompts
from app.blog.openrouter_client import OpenRouterError, chat_completion
from app.blog.sanitize import sanitize_html
from app.config import get_settings
from app.models.blog import BlogPost, BlogTopic

logger = logging.getLogger(__name__)

MAX_QA_ATTEMPTS = 2  # initial draft + one self-correction retry


def _extract_json(raw: str) -> dict:
    """Models sometimes wrap JSON in markdown fences despite instructions
    not to — strip those before parsing, then fail loudly if it's still not
    valid JSON rather than silently publishing garbage."""
    text = raw.strip()
    fence_match = re.match(r"^```(?:json)?\s*(.*)```\s*$", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()
    return json.loads(text)


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:180] or "post"


def _unique_slug(db: Session, base_slug: str) -> str:
    slug = base_slug
    suffix = 2
    while db.scalar(select(BlogPost.id).where(BlogPost.slug == slug)) is not None:
        slug = f"{base_slug}-{suffix}"
        suffix += 1
    return slug


def _next_topic(db: Session) -> BlogTopic | None:
    return db.scalar(
        select(BlogTopic).where(BlogTopic.status == "pending").order_by(BlogTopic.priority.desc(), BlogTopic.created_at)
    )


def _existing_published_titles(db: Session) -> list[str]:
    rows = db.scalars(select(BlogPost.title).where(BlogPost.status == "published"))
    return list(rows)


def generate_one(db: Session) -> BlogPost | None:
    """Runs one full topic -> draft -> QA -> publish/fail cycle. Returns the
    resulting BlogPost, or None if there was no pending topic."""
    settings = get_settings()
    topic = _next_topic(db)
    if topic is None:
        logger.info("no pending blog topics")
        return None

    facts = knowledge_base.relevant_facts(topic.category, topic.target_keywords)
    all_news = news_feed.fetch_recent_items()
    news_items = [n.to_dict() for n in news_feed.relevant_items(all_news, topic.target_keywords)]

    qa_feedback: list[str] | None = None
    draft: dict = {}
    qa_verdict: dict = {"verdict": "fail", "issues": ["generation did not complete"]}
    attempts = 0

    for attempts in range(1, MAX_QA_ATTEMPTS + 1):
        user_prompt = prompts.build_writer_prompt(topic.prompt, topic.category, facts, news_items, qa_feedback)
        raw_draft = chat_completion(settings.openrouter_writer_model, prompts.WRITER_SYSTEM_PROMPT, user_prompt)
        draft = _extract_json(raw_draft)

        existing_titles = _existing_published_titles(db)
        qa_user_prompt = prompts.build_qa_prompt(draft, facts, news_items, existing_titles)
        raw_verdict = chat_completion(settings.openrouter_qa_model, prompts.QA_SYSTEM_PROMPT, qa_user_prompt)
        qa_verdict = _extract_json(raw_verdict)

        if qa_verdict.get("verdict") == "pass":
            break
        qa_feedback = qa_verdict.get("issues") or ["QA failed with no listed issues"]

    slug = _unique_slug(db, _slugify(draft.get("title", topic.prompt)))
    body_markdown = draft.get("body_markdown", "")
    word_count = len(body_markdown.split())

    post = BlogPost(
        topic_id=topic.id,
        slug=slug,
        title=draft.get("title", topic.prompt)[:300],
        meta_description=(draft.get("meta_description") or "")[:200],
        excerpt=draft.get("excerpt", ""),
        tags_json=json.dumps(draft.get("tags", [])),
        content_markdown=body_markdown,
        content_html=sanitize_html(markdown_lib.markdown(body_markdown, extensions=["fenced_code", "tables"])),
        faq_json=json.dumps(draft.get("faq", [])),
        news_refs_json=json.dumps(news_items),
        writer_model=settings.openrouter_writer_model,
        qa_model=settings.openrouter_qa_model,
        qa_verdict_json=json.dumps(qa_verdict),
        qa_attempts=attempts,
        word_count=word_count,
        reading_minutes=max(1, round(word_count / 200)),
    )

    if qa_verdict.get("verdict") == "pass":
        post.status = "published"
        post.published_at = datetime.now(timezone.utc)
    else:
        post.status = "qa_failed"

    topic.status = "used"
    db.add(post)
    db.commit()
    db.refresh(post)
    logger.info("generated post %s (%s) after %d attempt(s)", post.slug, post.status, attempts)
    return post


def run_once() -> BlogPost | None:
    """Entry point for CLI/CI use: opens its own session, generates one
    post, closes the session."""
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        return generate_one(db)
    except OpenRouterError:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_once()
