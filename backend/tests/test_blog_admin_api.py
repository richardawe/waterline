"""Calls the admin-blog route functions directly against a rolled-back
transaction (same pattern as test_blog_generator.py) rather than through
TestClient/HTTP — a real HTTP round-trip through get_db would commit for
real against whatever DB TEST_DATABASE_URL points at, since FastAPI's
dependency-injected session isn't the rollback-wrapped one the fixture
hands out."""

import pytest
from fastapi import HTTPException

from app.api.blog_admin import create_topic, list_topics, remove_topic
from app.models.blog import BlogTopic
from app.schemas import BlogTopicCreate
from tests.conftest import requires_db


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
