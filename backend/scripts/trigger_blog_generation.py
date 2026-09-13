"""CI entrypoint: triggers generation on the live backend over HTTPS
(POST /admin/blog/generate) instead of connecting to Postgres directly —
the backend already has localhost DB access, so this is how CI avoids
needing the database exposed to the internet at all. Run from `backend/`
with BLOG_API_BASE, ADMIN_API_USERNAME, ADMIN_API_PASSWORD set."""

import argparse
import logging
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.blog import notify  # noqa: E402

logger = logging.getLogger(__name__)

# Generation is synchronous server-side (writer + QA per post, possibly
# retried once) — a generous client timeout so this script isn't what times
# out first; if the reverse proxy in front of the backend has its own
# shorter timeout, that's the real ceiling (see docs/blog-pipeline.md).
REQUEST_TIMEOUT = 300


def _error_detail(exc: httpx.HTTPError) -> str:
    """The status line alone says nothing actionable — a `502` from
    /admin/blog/generate is the backend reporting an OpenRouter failure, and
    the *reason* (model pulled from the free tier, rate limit, bad key) is
    only in the response body. Without this, a run that broke because a model
    slug disappeared reports as a bare "502 Bad Gateway" in the GitHub issue
    and needs a server-side reproduction to diagnose; that cost this pipeline
    six days of silent failures. Best-effort: never let formatting an error
    raise a second one."""
    response = getattr(exc, "response", None)
    if response is None:
        return ""
    try:
        return f"\nResponse body: {response.text[:1000]}"
    except Exception:  # noqa: BLE001 - diagnostics must not mask the original failure
        return ""


def main(posts_per_run: int) -> int:
    base_url = os.environ.get("BLOG_API_BASE", "").rstrip("/")
    username = os.environ.get("ADMIN_API_USERNAME")
    password = os.environ.get("ADMIN_API_PASSWORD")
    if not base_url or not username or not password:
        logger.error("BLOG_API_BASE, ADMIN_API_USERNAME and ADMIN_API_PASSWORD must all be set")
        return 1

    try:
        response = httpx.post(
            f"{base_url}/admin/blog/generate",
            params={"posts_per_run": posts_per_run},
            auth=(username, password),
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        message = f"POST {base_url}/admin/blog/generate failed: {exc}{_error_detail(exc)}"
        logger.error(message)
        notify.notify_failure(message)
        return 1

    result = response.json()
    published, qa_failed = result.get("published", []), result.get("qa_failed", [])
    logger.info("generated %d published, %d qa_failed", len(published), len(qa_failed))

    if published or qa_failed:
        notify.notify_run_summary(published, qa_failed)
    else:
        logger.info("no pending blog topics — nothing generated this run")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--posts-per-run", type=int, default=1)
    args = parser.parse_args()
    sys.exit(main(args.posts_per_run))
