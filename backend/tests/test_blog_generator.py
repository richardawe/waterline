"""Generator pipeline against a live Postgres transaction (rolled back after
each test, same pattern as test_pipeline_integration.py). OpenRouter itself is
always mocked — no real network calls in CI.

The mocked seam is `chat_completion_with_fallback`, which returns a ChatResult
(which model answered + what it said) rather than a bare string, since the
model that answers isn't necessarily the first one configured."""

import json
from unittest.mock import patch

import pytest

from sqlalchemy import update

from app.blog.generator import generate_one
from app.blog.openrouter_client import ChatResult, OpenRouterError
from app.models.blog import BlogTopic
from tests.conftest import requires_db


def _clear_pending_topics(db_session) -> None:
    """The db_session fixture rolls back this test's own writes, but not rows
    committed outside it (e.g. by a manual seed run against the same DB) — so
    tests that assert "no pending topics" need to neutralize those first
    rather than assume a pristine table."""
    db_session.execute(update(BlogTopic).where(BlogTopic.status == "pending").values(status="used"))


def _reply(content: str, model: str = "writer-model:free") -> ChatResult:
    return ChatResult(model=model, content=content)


def _draft_json(title: str = "How CRC Credit Bureau works in Nigeria") -> str:
    return json.dumps(
        {
            "title": title,
            "meta_description": "A plain-English explainer on how CRC Credit Bureau works in Nigeria.",
            "excerpt": "CRC Credit Bureau compiles Nigerian borrowers' repayment history for lenders.",
            "tags": ["credit bureau", "nigeria"],
            "faq": [
                {"question": "What is CRC Credit Bureau?", "answer": "A CBN-licensed Nigerian credit bureau."},
                {"question": "Who reports to it?", "answer": "Lenders report borrower repayment history."},
                {"question": "Is a credit report free?", "answer": "Reports may carry a fee depending on the bureau."},
            ],
            "news_section_included": False,
            "body_markdown": "# How CRC Credit Bureau works\n\n" + ("Nigerian lenders use credit bureaus. " * 120),
        }
    )


@requires_db
def test_generate_one_publishes_when_qa_passes(db_session):
    db_session.add(
        BlogTopic(prompt="Explain CRC Credit Bureau", category="credit-score", target_keywords="credit bureau, crc", priority=100)
    )
    db_session.commit()

    with patch("app.blog.generator.chat_completion_with_fallback") as mock_chat, patch(
        "app.blog.generator.news_feed.fetch_recent_items", return_value=[]
    ):
        mock_chat.side_effect = [_reply(_draft_json()), _reply(json.dumps({"verdict": "pass", "issues": []}))]
        post = generate_one(db_session)

    assert post is not None
    assert post.status == "published"
    assert post.published_at is not None
    assert post.qa_attempts == 1
    assert post.slug  # non-empty, derived from title


@requires_db
def test_generate_one_retries_once_then_marks_qa_failed(db_session):
    db_session.add(
        BlogTopic(prompt="Explain the CBN GSI policy", category="loan-recovery", target_keywords="gsi, nibss", priority=100)
    )
    db_session.commit()

    with patch("app.blog.generator.chat_completion_with_fallback") as mock_chat, patch(
        "app.blog.generator.news_feed.fetch_recent_items", return_value=[]
    ):
        mock_chat.side_effect = [
            _reply(_draft_json(title="Draft 1")),
            _reply(json.dumps({"verdict": "fail", "issues": ["invented a rate not in reference facts"]})),
            _reply(_draft_json(title="Draft 2")),
            _reply(json.dumps({"verdict": "fail", "issues": ["still invented a rate"]})),
        ]
        post = generate_one(db_session)

    assert post.status == "qa_failed"
    assert post.qa_attempts == 2
    assert mock_chat.call_count == 4  # writer+QA, twice


@requires_db
def test_generate_one_returns_none_with_no_pending_topics(db_session):
    _clear_pending_topics(db_session)
    assert generate_one(db_session) is None


@requires_db
def test_generate_one_produces_unique_slugs_for_duplicate_titles(db_session):
    db_session.add_all(
        [
            BlogTopic(prompt="Topic A", category="lending", target_keywords="loan", priority=100),
            BlogTopic(prompt="Topic B", category="lending", target_keywords="loan", priority=99),
        ]
    )
    db_session.commit()

    with patch("app.blog.generator.chat_completion_with_fallback") as mock_chat, patch(
        "app.blog.generator.news_feed.fetch_recent_items", return_value=[]
    ):
        mock_chat.side_effect = [
            _reply(_draft_json(title="Same Title")),
            _reply(json.dumps({"verdict": "pass", "issues": []})),
            _reply(_draft_json(title="Same Title")),
            _reply(json.dumps({"verdict": "pass", "issues": []})),
        ]
        post_a = generate_one(db_session)
        post_b = generate_one(db_session)

    assert post_a.slug != post_b.slug


@requires_db
def test_post_records_the_model_that_actually_answered(db_session):
    """With a fallback chain the responder isn't necessarily the configured
    first choice, so the post's provenance has to come from the reply rather
    than from config — otherwise every post claims it was written by a model
    that may have been unreachable at the time."""
    db_session.add(BlogTopic(prompt="Explain the CBN MPR", category="lending", target_keywords="mpr", priority=100))
    db_session.commit()

    with patch("app.blog.generator.chat_completion_with_fallback") as mock_chat, patch(
        "app.blog.generator.news_feed.fetch_recent_items", return_value=[]
    ):
        mock_chat.side_effect = [
            _reply(_draft_json(), model="fallback-writer:free"),
            _reply(json.dumps({"verdict": "pass", "issues": []}), model="fallback-qa:free"),
        ]
        post = generate_one(db_session)

    assert post.writer_model == "fallback-writer:free"
    assert post.qa_model == "fallback-qa:free"


@requires_db
def test_unparseable_writer_reply_retries_instead_of_crashing(db_session):
    """An unparseable reply used to escape as ValueError and surface as a bare
    500 from /admin/blog/generate — no model named, no reason given."""
    db_session.add(BlogTopic(prompt="Explain NDPA", category="regulation", target_keywords="ndpa", priority=100))
    db_session.commit()

    with patch("app.blog.generator.chat_completion_with_fallback") as mock_chat, patch(
        "app.blog.generator.news_feed.fetch_recent_items", return_value=[]
    ):
        mock_chat.side_effect = [
            _reply("Sure! Let me think about that for a moment."),  # no JSON at all
            _reply(_draft_json(title="Recovered On Retry")),
            _reply(json.dumps({"verdict": "pass", "issues": []})),
        ]
        post = generate_one(db_session)

    assert post.status == "published"
    assert post.title == "Recovered On Retry"


@requires_db
def test_writer_unparseable_on_every_attempt_raises_openrouter_error(db_session):
    """No draft means no post to save — it should fail as a 502 naming the
    model, not a 500 naming nothing."""
    db_session.add(BlogTopic(prompt="Explain GSI", category="lending", priority=100))
    db_session.commit()

    with patch("app.blog.generator.chat_completion_with_fallback") as mock_chat, patch(
        "app.blog.generator.news_feed.fetch_recent_items", return_value=[]
    ):
        mock_chat.side_effect = [_reply("not json"), _reply("still not json")]
        with pytest.raises(OpenRouterError, match="unparseable JSON"):
            generate_one(db_session)


@requires_db
def test_unparseable_qa_verdict_keeps_the_draft_for_review(db_session):
    """The draft is fine; only the review is unreadable. Discarding a good post
    over a malformed verdict throws away the expensive half of the work."""
    db_session.add(BlogTopic(prompt="Explain BVN", category="lending", priority=100))
    db_session.commit()

    with patch("app.blog.generator.chat_completion_with_fallback") as mock_chat, patch(
        "app.blog.generator.news_feed.fetch_recent_items", return_value=[]
    ):
        mock_chat.side_effect = [
            _reply(_draft_json(title="Good Draft")),
            _reply("the post looks fine to me honestly"),
            _reply(_draft_json(title="Good Draft")),
            _reply("still prose"),
        ]
        post = generate_one(db_session)

    assert post is not None
    assert post.status == "qa_failed"
    assert post.title == "Good Draft"
    assert "unparseable" in post.qa_verdict_json


@requires_db
def test_generation_requests_json_mode(db_session):
    db_session.add(BlogTopic(prompt="Explain CRC", category="lending", priority=100))
    db_session.commit()

    with patch("app.blog.generator.chat_completion_with_fallback") as mock_chat, patch(
        "app.blog.generator.news_feed.fetch_recent_items", return_value=[]
    ):
        mock_chat.side_effect = [_reply(_draft_json()), _reply(json.dumps({"verdict": "pass", "issues": []}))]
        generate_one(db_session)

    assert all(call[1].get("json_mode") is True for call in mock_chat.call_args_list)
