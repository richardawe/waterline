"""Calls the admin-blog route functions directly against a rolled-back
transaction (same pattern as test_blog_generator.py) rather than through
TestClient/HTTP — a real HTTP round-trip through get_db would commit for
real against whatever DB TEST_DATABASE_URL points at, since FastAPI's
dependency-injected session isn't the rollback-wrapped one the fixture
hands out."""

import json
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.api.blog_admin import create_topic, generate_posts, list_topics, remove_topic, seed_topics
from app.blog.openrouter_client import OpenRouterError
from app.models.blog import BlogTopic
from app.schemas import BlogTopicCreate
from tests.conftest import requires_db


def _draft_json(title: str = "How NIBSS instant payments work") -> str:
    return json.dumps(
        {
            "title": title,
            "meta_description": "A plain-English explainer.",
            "excerpt": "NIBSS underpins instant transfers in Nigeria.",
            "tags": ["payments", "nigeria"],
            "faq": [
                {"question": "What is NIBSS?", "answer": "Nigeria's interbank settlement operator."},
                {"question": "Who uses it?", "answer": "Banks and licensed payment providers."},
                {"question": "Is it real-time?", "answer": "Yes, for most participating institutions."},
            ],
            "news_section_included": False,
            "body_markdown": "# NIBSS\n\n" + ("Instant payments in Nigeria rely on shared rails. " * 120),
        }
    )


@requires_db
def test_create_topic_persists_and_defaults_priority(db_session):
    topic = create_topic(
        BlogTopicCreate(prompt="Explain NIBSS instant payments", category="regulation", target_keywords="nibss, payments"),
        db_session,
    )
    assert topic["status"] == "pending"
    assert topic["priority"] == 100
    assert db_session.get(BlogTopic, topic["id"]) is not None


@requires_db
def test_create_topic_rejects_blank_prompt(db_session):
    with pytest.raises(HTTPException) as exc:
        create_topic(BlogTopicCreate(prompt="   ", category="lending"), db_session)
    assert exc.value.status_code == 422


@requires_db
def test_list_topics_filters_by_status(db_session):
    create_topic(BlogTopicCreate(prompt="Topic one", category="lending", priority=50), db_session)
    used = BlogTopic(prompt="Topic two", category="lending", status="used", priority=50)
    db_session.add(used)
    db_session.commit()

    pending = list_topics(db_session, status="pending")
    assert any(t["prompt"] == "Topic one" for t in pending)
    assert all(t["status"] == "pending" for t in pending)


@requires_db
def test_remove_topic_deletes_pending_topic(db_session):
    topic = create_topic(BlogTopicCreate(prompt="Delete me", category="lending"), db_session)
    result = remove_topic(topic["id"], db_session)
    assert result == {"deleted": True}
    assert db_session.get(BlogTopic, topic["id"]) is None


@requires_db
def test_remove_topic_refuses_non_pending_topic(db_session):
    used = BlogTopic(prompt="Already used", category="lending", status="used")
    db_session.add(used)
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        remove_topic(used.id, db_session)
    assert exc.value.status_code == 409
    assert db_session.get(BlogTopic, used.id) is not None


@requires_db
def test_remove_topic_404s_for_unknown_id(db_session):
    with pytest.raises(HTTPException) as exc:
        remove_topic("does-not-exist", db_session)
    assert exc.value.status_code == 404


@requires_db
def test_generate_posts_endpoint_returns_published_and_qa_failed(db_session):
    db_session.add(BlogTopic(prompt="Explain NIBSS", category="regulation", target_keywords="nibss", priority=100))
    db_session.commit()

    with patch("app.blog.generator.chat_completion") as mock_chat, patch(
        "app.blog.generator.news_feed.fetch_recent_items", return_value=[]
    ):
        mock_chat.side_effect = [_draft_json(), json.dumps({"verdict": "pass", "issues": []})]
        result = generate_posts(posts_per_run=1, db=db_session)

    assert len(result["published"]) == 1
    assert result["qa_failed"] == []
    assert result["published"][0]["status"] == "published"


@requires_db
def test_generate_posts_endpoint_caps_at_max_per_request(db_session):
    for i in range(5):
        db_session.add(BlogTopic(prompt=f"Topic {i}", category="lending", priority=100 - i))
    db_session.commit()

    with patch("app.blog.generator.chat_completion") as mock_chat, patch(
        "app.blog.generator.news_feed.fetch_recent_items", return_value=[]
    ):
        mock_chat.side_effect = [
            _draft_json(title="T0"), json.dumps({"verdict": "pass", "issues": []}),
            _draft_json(title="T1"), json.dumps({"verdict": "pass", "issues": []}),
            _draft_json(title="T2"), json.dumps({"verdict": "pass", "issues": []}),
        ]
        result = generate_posts(posts_per_run=10, db=db_session)  # asks for 10, only 3 allowed per request

    assert len(result["published"]) == 3
    assert mock_chat.call_count == 6


@requires_db
def test_generate_posts_endpoint_raises_502_on_openrouter_error(db_session):
    db_session.add(BlogTopic(prompt="Explain something", category="lending", priority=100))
    db_session.commit()

    with patch("app.blog.generator.chat_completion", side_effect=OpenRouterError("boom")), patch(
        "app.blog.generator.news_feed.fetch_recent_items", return_value=[]
    ):
        with pytest.raises(HTTPException) as exc:
            generate_posts(posts_per_run=1, db=db_session)
    assert exc.value.status_code == 502


def test_seed_topics_endpoint_calls_seed_function():
    with patch("app.seed.seed_blog_topics.seed") as mock_seed:
        result = seed_topics()
    assert result == {"seeded": True}
    mock_seed.assert_called_once()
