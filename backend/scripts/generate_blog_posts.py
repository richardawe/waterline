"""CI entrypoint: generate up to N posts in one run, then notify. Run as
`python -m scripts.generate_blog_posts` (or `python scripts/generate_blog_posts.py`)
from `backend/`, with `DATABASE_URL`/`OPENROUTER_API_KEY` set."""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.blog import notify
from app.blog.generator import generate_one
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
            (published if post.status == "published" else qa_failed).append(post)
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
