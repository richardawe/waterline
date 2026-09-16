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


def _posts_response(posts):
    r = Mock()
    r.raise_for_status = Mock()
    r.json.return_value = posts
    return r


def _env(monkeypatch):
    monkeypatch.setenv("BLOG_API_BASE", "https://api.example.com")
    monkeypatch.setenv("ADMIN_API_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_API_PASSWORD", "secret")


def _gateway_timeout() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.example.com/admin/blog/generate")
    response = httpx.Response(504, request=request, text="<html>504 Gateway Time-out</html>")
    return httpx.HTTPStatusError("504", request=request, response=response)


def test_gateway_timeout_publishes_the_post_that_landed_anyway(monkeypatch):
    """nginx gives up at ~300s but the backend keeps generating and commits the
    post. Failing the run there would discard a post that exists and leave the
    day unpublished."""
    _env(monkeypatch)
    before = [{"slug": "old", "status": "published", "title": "Old"}]
    after = before + [{"slug": "new", "status": "published", "title": "New"}]

    with patch.object(trigger_blog_generation.httpx, "get", side_effect=[_posts_response(before), _posts_response(after)]), \
         patch.object(trigger_blog_generation.httpx, "post", side_effect=_gateway_timeout()), \
         patch.object(trigger_blog_generation.notify, "notify_run_summary") as mock_summary, \
         patch.object(trigger_blog_generation.notify, "notify_failure") as mock_fail, \
         patch.object(trigger_blog_generation.time, "sleep"):
        assert trigger_blog_generation.main(1) == 0

    mock_fail.assert_not_called()
    published, qa_failed = mock_summary.call_args[0]
    assert [p["slug"] for p in published] == ["new"]
    assert qa_failed == []


def test_gateway_timeout_with_nothing_generated_still_fails(monkeypatch):
    """A timeout that really produced nothing must stay a failure — otherwise
    a dead backend reports green forever."""
    _env(monkeypatch)
    before = [{"slug": "old", "status": "published"}]

    with patch.object(trigger_blog_generation.httpx, "get", return_value=_posts_response(before)), \
         patch.object(trigger_blog_generation.httpx, "post", side_effect=_gateway_timeout()), \
         patch.object(trigger_blog_generation.notify, "notify_failure") as mock_fail, \
         patch.object(trigger_blog_generation, "RECONCILE_TIMEOUT", 0), \
         patch.object(trigger_blog_generation.time, "sleep"):
        assert trigger_blog_generation.main(1) == 1

    assert "504" in mock_fail.call_args[0][0]


def test_timeout_reconciliation_reports_a_qa_failed_post(monkeypatch):
    _env(monkeypatch)
    before = []
    after = [{"slug": "draft", "status": "qa_failed", "title": "Draft", "qa_verdict": "fail"}]

    with patch.object(trigger_blog_generation.httpx, "get", side_effect=[_posts_response(before), _posts_response(after)]), \
         patch.object(trigger_blog_generation.httpx, "post", side_effect=_gateway_timeout()), \
         patch.object(trigger_blog_generation.notify, "notify_run_summary") as mock_summary, \
         patch.object(trigger_blog_generation.time, "sleep"):
        assert trigger_blog_generation.main(1) == 0

    published, qa_failed = mock_summary.call_args[0]
    assert published == []
    assert [p["slug"] for p in qa_failed] == ["draft"]


def test_a_502_is_not_reconciled(monkeypatch):
    """502 is the backend's own considered answer that OpenRouter failed —
    nothing was written, so there is nothing to go looking for."""
    _env(monkeypatch)
    request = httpx.Request("POST", "https://api.example.com/admin/blog/generate")
    response = httpx.Response(502, request=request, json={"detail": "OpenRouter error: boom"})

    with patch.object(trigger_blog_generation.httpx, "get", return_value=_posts_response([])), \
         patch.object(trigger_blog_generation.httpx, "post", return_value=response), \
         patch.object(trigger_blog_generation.notify, "notify_failure") as mock_fail, \
         patch.object(trigger_blog_generation.time, "sleep") as mock_sleep:
        assert trigger_blog_generation.main(1) == 1

    mock_sleep.assert_not_called()  # no polling — it went straight to failure
    assert "OpenRouter error: boom" in mock_fail.call_args[0][0]
