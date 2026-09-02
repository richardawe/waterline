"""Direct-database ops utility for local dev or a machine that already has
`localhost` Postgres access (e.g. run on the server itself via cPanel
Terminal). CI no longer uses this — it calls the /admin/blog/generate HTTPS
endpoint instead (scripts/trigger_blog_generation.py) so it never needs a
direct database connection. Run as `python scripts/generate_blog_posts.py`
from `backend/`, with `DATABASE_URL`/`OPENROUTER_API_KEY` set."""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.blog import notify
from app.blog.generator import generate_one
from app.blog.serialize import post_to_dict
from app.db import SessionLocal

logger = logging.getLogger(__name__)


def main(posts_per_run: int) -> None:
    db = SessionLocal()
    published, qa_failed = [], []
    try:
        for _ in range(posts_per_run):
            post = generate_one(db)
            if post is None:
                break  # no more pending topics
            (published if post.status == "published" else qa_failed).append(post_to_dict(post))
    finally:
        db.close()

    if published or qa_failed:
        notify.notify_run_summary(published, qa_failed)
    else:
        logger.info("no pending blog topics — nothing generated this run")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--posts-per-run", type=int, default=2)
    args = parser.parse_args()
    main(args.posts_per_run)
