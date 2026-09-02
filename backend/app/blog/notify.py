"""Human-in-the-loop notification for the auto-publish pipeline: since posts
go live without pre-publish approval, a human needs to hear about every run.
Defaults to opening a GitHub issue (zero new infra — reuses the Actions job's
own GITHUB_TOKEN); falls back to a log line for local/dry runs where no
token is configured.

Takes plain dicts (the same shape app.blog.serialize.post_to_dict produces),
not ORM objects — the CI side that calls this only ever has JSON from the
admin API's HTTP response, never a live BlogPost."""

import logging
import os

import httpx

logger = logging.getLogger(__name__)


def _github_repo() -> tuple[str, str] | None:
    repo = os.environ.get("GITHUB_REPOSITORY")  # "owner/repo", set by Actions
    if not repo or "/" not in repo:
        return None
    owner, name = repo.split("/", 1)
    return owner, name


def _open_issue(title: str, body: str) -> None:
    token = os.environ.get("GITHUB_TOKEN")
    repo = _github_repo()
    if not token or not repo:
        logger.info("blog notification (no GITHUB_TOKEN/repo — logging only):\n%s\n%s", title, body)
        return

    owner, name = repo
    response = httpx.post(
        f"https://api.github.com/repos/{owner}/{name}/issues",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json={"title": title, "body": body, "labels": ["blog"]},
        timeout=30,
    )
    if response.status_code >= 300:
        logger.warning("failed to open notification issue (%s): %s", response.status_code, response.text[:300])


def notify_run_summary(published: list[dict], qa_failed: list[dict]) -> None:
    lines = [f"**Published:** {len(published)}", f"**Needs attention (failed QA twice):** {len(qa_failed)}", ""]
    for post in published:
        lines.append(f"- ✅ [{post['title']}](/blog/{post['slug']}/)")
    for post in qa_failed:
        issues = post.get("qa_verdict") or "(no issues recorded)"
        lines.append(f"- ⚠️ **{post['title']}** — status `qa_failed`, edit/force-publish in admin.html\n  QA notes: {issues}")
    body = "\n".join(lines)
    title = f"Blog run: {len(published)} published, {len(qa_failed)} need review"
    _open_issue(title, body)


def notify_failure(message: str) -> None:
    _open_issue("Blog run failed", f"The scheduled/triggered blog generation run did not complete:\n\n```\n{message}\n```")
