"""Tests the OpenRouter fallback chain — the layer that decides whether a
model failure is survivable. This is the code path that matters when a free
model slug is pulled (which has now happened three times), so it's tested
without any real network calls: httpx.post is patched throughout."""

import json
from unittest.mock import Mock, patch

import pytest

from app.blog import openrouter_client
from app.blog.openrouter_client import OpenRouterError, chat_completion_with_fallback
from app.config import Settings, _model_chain


def _response(status_code: int, *, content: str = "ok", body: str = "") -> Mock:
    response = Mock()
    response.status_code = status_code
    response.text = body or json.dumps({"error": {"message": "boom"}})
    response.json.return_value = {"choices": [{"message": {"content": content}}]}
    return response


@pytest.fixture(autouse=True)
def _configured_key():
    """chat_completion refuses to call out without an API key — give every
    test in this module one so the chain logic itself is what's exercised."""
    settings = Settings(openrouter_api_key="test-key")
    with patch.object(openrouter_client, "get_settings", return_value=settings):
        yield


def test_first_model_answering_is_used_and_reported():
    with patch.object(openrouter_client.httpx, "post", return_value=_response(200, content="drafted")) as mock_post:
        result = chat_completion_with_fallback(["model-a", "model-b"], "sys", "user")

    assert result.model == "model-a"
    assert result.content == "drafted"
    assert mock_post.call_count == 1  # no needless hop when the first choice works


def test_falls_through_to_next_model_when_first_is_gone():
    """A 404 is what a slug pulled from the free tier looks like — the exact
    failure that took the pipeline down on 2026-09-08."""
    with patch.object(
        openrouter_client.httpx,
        "post",
        side_effect=[_response(404, body="No endpoints found for model"), _response(200, content="drafted")],
    ):
        result = chat_completion_with_fallback(["pulled-model:free", "working-model:free"], "sys", "user")

    assert result.model == "working-model:free"
    assert result.content == "drafted"


def test_falls_through_on_rate_limited_shared_free_pool():
    with patch.object(openrouter_client.httpx, "post", side_effect=[_response(429), _response(200)]):
        assert chat_completion_with_fallback(["busy:free", "spare:free"], "sys", "user").model == "spare:free"


def test_raises_listing_every_failure_when_the_whole_chain_is_dead():
    with patch.object(openrouter_client.httpx, "post", side_effect=[_response(404), _response(429)]):
        with pytest.raises(OpenRouterError) as exc:
            chat_completion_with_fallback(["a:free", "b:free"], "sys", "user")

    message = str(exc.value)
    assert "a:free" in message and "b:free" in message  # names both, so the issue says what to replace


def test_credentials_failure_stops_the_chain_immediately():
    """A bad key fails identically for every model — walking the chain would
    just burn requests and bury the real reason under N identical errors."""
    with patch.object(openrouter_client.httpx, "post", return_value=_response(401)) as mock_post:
        with pytest.raises(OpenRouterError) as exc:
            chat_completion_with_fallback(["a:free", "b:free", "c:free"], "sys", "user")

    assert mock_post.call_count == 1
    assert exc.value.fatal


def test_missing_api_key_is_fatal_and_not_retried_per_model():
    with patch.object(openrouter_client, "get_settings", return_value=Settings(openrouter_api_key=None)):
        with patch.object(openrouter_client.httpx, "post") as mock_post:
            with pytest.raises(OpenRouterError, match="OPENROUTER_API_KEY"):
                chat_completion_with_fallback(["a:free", "b:free"], "sys", "user")
    mock_post.assert_not_called()


def test_empty_chain_is_rejected():
    with pytest.raises(OpenRouterError, match="no OpenRouter model configured"):
        chat_completion_with_fallback([], "sys", "user")


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("only/one:free", ["only/one:free"]),  # a pre-chain single-id deployment keeps working
        ("a:free,b:free", ["a:free", "b:free"]),
        (" a:free , b:free ", ["a:free", "b:free"]),
        ("a:free,,b:free,", ["a:free", "b:free"]),
    ],
)
def test_model_chain_parsing(configured, expected):
    assert _model_chain(configured) == expected


def test_settings_expose_writer_and_qa_chains():
    settings = Settings(openrouter_writer_model="w1,w2", openrouter_qa_model="q1")
    assert settings.openrouter_writer_models == ["w1", "w2"]
    assert settings.openrouter_qa_models == ["q1"]


def test_default_chain_ends_in_the_free_router():
    """The last entry has to be a slug that survives free-tier rotation,
    otherwise the chain has the same single-point-of-failure it replaced."""
    assert Settings().openrouter_writer_models[-1] == "openrouter/free"
    assert Settings().openrouter_qa_models[-1] == "openrouter/free"


def _captured_payload(mock_post, call_index: int = 0) -> dict:
    return mock_post.call_args_list[call_index][1]["json"]


def test_request_turns_reasoning_off_and_caps_output():
    """Left thinking, the free models burn past the 300s proxy ceiling — this
    is the flag that keeps generation inside the window."""
    with patch.object(openrouter_client.httpx, "post", return_value=_response(200)) as mock_post:
        chat_completion_with_fallback(["m"], "sys", "user")

    payload = _captured_payload(mock_post)
    assert payload["reasoning"] == {"enabled": False, "effort": "none"}
    assert payload["max_tokens"] == 8000


def test_json_mode_is_requested_only_when_asked():
    with patch.object(openrouter_client.httpx, "post", return_value=_response(200)) as mock_post:
        chat_completion_with_fallback(["m"], "sys", "user", json_mode=True)
    assert _captured_payload(mock_post)["response_format"] == {"type": "json_object"}

    with patch.object(openrouter_client.httpx, "post", return_value=_response(200)) as mock_post:
        chat_completion_with_fallback(["m"], "sys", "user")
    assert "response_format" not in _captured_payload(mock_post)


def test_rejecting_the_reasoning_flag_costs_only_that_flag():
    """Not every upstream drops parameters it doesn't understand. A 400 should
    cost one parameter, not the model and not the rest of the tuning — an
    all-or-nothing retry would drop JSON mode too and undo half the fix."""
    with patch.object(
        openrouter_client.httpx,
        "post",
        side_effect=[_response(400, body="unknown parameter: reasoning"), _response(200, content="drafted")],
    ) as mock_post:
        result = chat_completion_with_fallback(["picky:free"], "sys", "user", json_mode=True)

    assert result.model == "picky:free"
    assert result.content == "drafted"
    assert "reasoning" in _captured_payload(mock_post, 0)

    retried = _captured_payload(mock_post, 1)
    assert "reasoning" not in retried
    assert retried["response_format"] == {"type": "json_object"}  # JSON mode survives
    assert retried["max_tokens"] == 8000


def test_ladder_drops_json_mode_before_giving_up_on_the_model():
    with patch.object(
        openrouter_client.httpx,
        "post",
        side_effect=[_response(400), _response(400), _response(200, content="drafted")],
    ) as mock_post:
        result = chat_completion_with_fallback(["picky:free"], "sys", "user", json_mode=True)

    assert result.content == "drafted"
    third = _captured_payload(mock_post, 2)
    assert "response_format" not in third and "reasoning" not in third


def test_bad_request_through_the_whole_ladder_moves_on_to_the_next_model():
    """Three 400s exhaust a:free's variants (no json_mode -> 3 rungs); the
    fourth call is b:free."""
    with patch.object(
        openrouter_client.httpx,
        "post",
        side_effect=[_response(400), _response(400), _response(400), _response(200)],
    ):
        assert chat_completion_with_fallback(["a:free", "b:free"], "sys", "user").model == "b:free"


def test_empty_message_counts_as_a_model_failure():
    """A model that spends its whole budget thinking returns 200 with an empty
    message — handing "" back would surface as a JSON parse error much later."""
    with patch.object(openrouter_client.httpx, "post", side_effect=[_response(200, content="   "), _response(200, content="real")]):
        result = chat_completion_with_fallback(["quiet:free", "spare:free"], "sys", "user")
    assert result.model == "spare:free"
    assert result.content == "real"
