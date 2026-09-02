"""Human-in-the-loop notification for the auto-publish pipeline: since posts
go live without pre-publish approval, a human needs to hear about every run.
Defaults to opening a GitHub issue (zero new infra — reuses the Actions job's
own GITHUB_TOKEN); falls back to a log line for local/dry runs where no
token is configured."""

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


def notify_run_summary(published: list, qa_failed: list) -> None:
    lines = [f"**Published:** {len(published)}", f"**Needs attention (failed QA twice):** {len(qa_failed)}", ""]
    for post in published:
        lines.append(f"- ✅ [{post.title}](/blog/{post.slug}/)")
    for post in qa_failed:
        issues = post.qa_verdict_json or "(no issues recorded)"
        lines.append(f"- ⚠️ **{post.title}** — status `qa_failed`, edit/force-publish in admin.html\n  QA notes: {issues}")
    body = "\n".join(lines)
    title = f"Blog run: {len(published)} published, {len(qa_failed)} need review"

    token = os.environ.get("GITHUB_TOKEN")
    repo = _github_repo()
    if not token or not repo:
        logger.info("blog run summary (no GITHUB_TOKEN/repo — logging only):\n%s\n%s", title, body)
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
