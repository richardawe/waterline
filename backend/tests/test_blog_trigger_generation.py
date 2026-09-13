"""Tests scripts/trigger_blog_generation.py — the CI entrypoint that calls
the live backend over HTTPS instead of connecting to Postgres directly.
httpx and notify are patched via patch.object on the loaded module's own
attribute references, not string-path patching, since the script is loaded
via importlib rather than a normal package import."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "trigger_blog_generation.py"
spec = importlib.util.spec_from_file_location("trigger_blog_generation", SCRIPT_PATH)
trigger_blog_generation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trigger_blog_generation)


def test_main_requires_env_vars(monkeypatch):
    monkeypatch.delenv("BLOG_API_BASE", raising=False)
    monkeypatch.delenv("ADMIN_API_USERNAME", raising=False)
    monkeypatch.delenv("ADMIN_API_PASSWORD", raising=False)
    assert trigger_blog_generation.main(1) == 1


def test_main_success_calls_notify_run_summary(monkeypatch):
    monkeypatch.setenv("BLOG_API_BASE", "https://api.example.com")
    monkeypatch.setenv("ADMIN_API_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_API_PASSWORD", "secret")

    mock_response = Mock()
    mock_response.raise_for_status = Mock()
    mock_response.json.return_value = {"published": [{"title": "A", "slug": "a"}], "qa_failed": []}

    with patch.object(trigger_blog_generation.httpx, "post", return_value=mock_response) as mock_post, patch.object(
        trigger_blog_generation.notify, "notify_run_summary"
    ) as mock_notify:
        assert trigger_blog_generation.main(1) == 0

    assert mock_post.call_args[1]["auth"] == ("admin", "secret")
    mock_notify.assert_called_once()


def test_main_no_pending_topics_skips_notify(monkeypatch):
    monkeypatch.setenv("BLOG_API_BASE", "https://api.example.com")
    monkeypatch.setenv("ADMIN_API_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_API_PASSWORD", "secret")

    mock_response = Mock()
    mock_response.raise_for_status = Mock()
    mock_response.json.return_value = {"published": [], "qa_failed": []}

    with patch.object(trigger_blog_generation.httpx, "post", return_value=mock_response), patch.object(
        trigger_blog_generation.notify, "notify_run_summary"
    ) as mock_notify:
        assert trigger_blog_generation.main(1) == 0
    mock_notify.assert_not_called()


def test_main_http_error_calls_notify_failure_and_returns_1(monkeypatch):
    monkeypatch.setenv("BLOG_API_BASE", "https://api.example.com")
    monkeypatch.setenv("ADMIN_API_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_API_PASSWORD", "secret")

    with patch.object(
        trigger_blog_generation.httpx, "post", side_effect=httpx.ConnectError("boom")
    ), patch.object(trigger_blog_generation.notify, "notify_failure") as mock_fail:
        assert trigger_blog_generation.main(1) == 1
    mock_fail.assert_called_once()


def test_http_status_error_reports_the_response_body(monkeypatch):
    """A bare "502 Bad Gateway" in the failure issue is undiagnosable — the
    backend puts the actual OpenRouter reason (e.g. a model pulled from the
    free tier) in the response body, so the notification has to carry it."""
    monkeypatch.setenv("BLOG_API_BASE", "https://api.example.com")
    monkeypatch.setenv("ADMIN_API_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_API_PASSWORD", "secret")

    request = httpx.Request("POST", "https://api.example.com/admin/blog/generate")
    response = httpx.Response(
        502, request=request, json={"detail": "OpenRouter error: OpenRouter some/model:free returned 404"}
    )

    with patch.object(trigger_blog_generation.httpx, "post", return_value=response), patch.object(
        trigger_blog_generation.notify, "notify_failure"
    ) as mock_fail:
        assert trigger_blog_generation.main(1) == 1

    message = mock_fail.call_args[0][0]
    assert "502" in message
    assert "some/model:free returned 404" in message


def test_transport_error_without_a_response_still_notifies(monkeypatch):
    """httpx.ConnectError carries no response — formatting the detail must
    not raise a second error on top of the original failure."""
    monkeypatch.setenv("BLOG_API_BASE", "https://api.example.com")
    monkeypatch.setenv("ADMIN_API_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_API_PASSWORD", "secret")

    with patch.object(
        trigger_blog_generation.httpx, "post", side_effect=httpx.ConnectError("boom")
    ), patch.object(trigger_blog_generation.notify, "notify_failure") as mock_fail:
        assert trigger_blog_generation.main(1) == 1

    assert "boom" in mock_fail.call_args[0][0]
