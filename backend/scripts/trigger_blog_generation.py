"""CI entrypoint: triggers generation on the live backend over HTTPS
(POST /admin/blog/generate) instead of connecting to Postgres directly —
the backend already has localhost DB access, so this is how CI avoids
needing the database exposed to the internet at all. Run from `backend/`
with BLOG_API_BASE, ADMIN_API_USERNAME, ADMIN_API_PASSWORD set."""

import argparse
import logging
import os
import sys
import time
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

# When the proxy gives up mid-generation, the backend keeps going and usually
# commits the post anyway — the answer is lost, not the work. So a timeout is
# a question ("did it land?"), not a verdict, and these bound how long we wait
# for the answer before calling the run failed.
RECONCILE_TIMEOUT = 420
RECONCILE_POLL_INTERVAL = 30

# Statuses that mean the request outlived something between us and the app,
# rather than the app answering. 502 is excluded deliberately: that is the
# backend's own considered "OpenRouter failed" reply, and nothing was written.
TIMEOUT_STATUS_CODES = frozenset({408, 504})


def _list_posts(base_url: str, auth: tuple[str, str]) -> list[dict] | None:
    """Every post the admin API knows about, whatever its status. Returns None
    if the listing itself fails, so callers can tell "nothing new" apart from
    "couldn't look"."""
    try:
        response = httpx.get(f"{base_url}/admin/blog/posts", auth=auth, timeout=60)
        response.raise_for_status()
        posts = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("could not list existing posts: %s", exc)
        return None
    return posts if isinstance(posts, list) else None


def _reconcile_after_timeout(base_url: str, auth: tuple[str, str], known_slugs: set[str]) -> list[dict] | None:
    """Waits to see whether the generation we stopped waiting for still landed.

    Generation runs synchronously inside the request, so when nginx returns a
    504 at its ~300s ceiling the backend is still working and will commit the
    post a moment later. Failing the run there would throw away a post that
    exists, leave the day unpublished, and open a "Blog run failed" issue about
    work that actually succeeded. Polling the posts list answers it directly.
    """
    deadline = time.monotonic() + RECONCILE_TIMEOUT
    while True:
        posts = _list_posts(base_url, auth)
        if posts is not None:
            fresh = [p for p in posts if p.get("slug") not in known_slugs]
            if fresh:
                return fresh
        if time.monotonic() >= deadline:
            return None
        time.sleep(RECONCILE_POLL_INTERVAL)


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

    auth = (username, password)

    # Captured before triggering so a timeout can be reconciled against it.
    before = _list_posts(base_url, auth)
    known_slugs = {p.get("slug") for p in before} if before is not None else None

    try:
        response = httpx.post(
            f"{base_url}/admin/blog/generate",
            params={"posts_per_run": posts_per_run},
            auth=auth,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        timed_out = isinstance(exc, httpx.TimeoutException) or status in TIMEOUT_STATUS_CODES

        if timed_out and known_slugs is not None:
            logger.warning("generation request timed out (%s) — checking whether it landed anyway", exc)
            fresh = _reconcile_after_timeout(base_url, auth, known_slugs)
            if fresh:
                published = [p for p in fresh if p.get("status") == "published"]
                qa_failed = [p for p in fresh if p.get("status") == "qa_failed"]
                logger.info("generation completed server-side despite the timeout: %d post(s)", len(fresh))
                notify.notify_run_summary(published, qa_failed)
                return 0
            logger.error("no new post appeared after the timeout — treating the run as failed")

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
