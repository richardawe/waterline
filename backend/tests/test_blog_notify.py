from unittest.mock import Mock, patch

from app.blog import notify


def test_notify_run_summary_logs_when_no_token(monkeypatch, caplog):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    with caplog.at_level("INFO"):
        notify.notify_run_summary([{"title": "A", "slug": "a"}], [])
    assert "no GITHUB_TOKEN" in caplog.text


def test_notify_run_summary_posts_issue_when_configured(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    mock_response = Mock(status_code=201, text="")
    with patch("app.blog.notify.httpx.post", return_value=mock_response) as mock_post:
        notify.notify_run_summary(
            [{"title": "Post A", "slug": "post-a"}],
            [{"title": "Post B", "slug": "post-b", "qa_verdict": {"verdict": "fail", "issues": ["bad"]}}],
        )
    assert mock_post.call_count == 1
    url = mock_post.call_args[0][0]
    body = mock_post.call_args[1]["json"]["body"]
    assert url == "https://api.github.com/repos/owner/repo/issues"
    assert "Post A" in body
    assert "Post B" in body


def test_notify_failure_posts_issue(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    mock_response = Mock(status_code=201, text="")
    with patch("app.blog.notify.httpx.post", return_value=mock_response) as mock_post:
        notify.notify_failure("connection refused")
    assert mock_post.call_count == 1
    assert "connection refused" in mock_post.call_args[1]["json"]["body"]


def test_notify_run_summary_warns_on_non_2xx(monkeypatch, caplog):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    mock_response = Mock(status_code=403, text="forbidden")
    with caplog.at_level("WARNING"), patch("app.blog.notify.httpx.post", return_value=mock_response):
        notify.notify_run_summary([{"title": "A", "slug": "a"}], [])
    assert "failed to open notification issue" in caplog.text
