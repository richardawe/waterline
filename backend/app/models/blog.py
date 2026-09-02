"""Automated finance blog: content calendar (BlogTopic) + generated articles
(BlogPost). See docs/blog-pipeline.md for the generation/QA/publish pipeline
this feeds."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, new_uuid


class BlogTopic(Base, TimestampMixin):
    """One queued content-calendar entry. `generator.py` picks the
    highest-priority pending topic, generates a post from it, then marks it
    used regardless of whether the post ends up published or qa_failed."""

    __tablename__ = "blog_topic"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    target_keywords: Mapped[Optional[str]] = mapped_column(String(500))
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")  # pending/used

    posts: Mapped[list["BlogPost"]] = relationship(back_populates="topic")


class BlogPost(Base, TimestampMixin):
    __tablename__ = "blog_post"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    topic_id: Mapped[Optional[str]] = mapped_column(ForeignKey("blog_topic.id"))
    slug: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    meta_description: Mapped[str] = mapped_column(String(200), nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    tags_json: Mapped[Optional[str]] = mapped_column(Text)  # JSON array of strings
    content_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    content_html: Mapped[Optional[str]] = mapped_column(Text)  # cached render
    faq_json: Mapped[Optional[str]] = mapped_column(Text)  # JSON array of {question, answer}
    news_refs_json: Mapped[Optional[str]] = mapped_column(Text)  # JSON array of {title, link, source, published}

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft"
    )  # draft/qa_failed/published/archived
    writer_model: Mapped[Optional[str]] = mapped_column(String(120))
    qa_model: Mapped[Optional[str]] = mapped_column(String(120))
    qa_verdict_json: Mapped[Optional[str]] = mapped_column(Text)  # {"verdict": "pass"/"fail", "issues": [...]}
    qa_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reading_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    topic: Mapped[Optional["BlogTopic"]] = relationship(back_populates="posts")
