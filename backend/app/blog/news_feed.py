"""Fetches recent items from configured RSS feeds so blog posts can cite real,
dated current-events context instead of the writer model inventing news it
has no way of actually knowing (free OpenRouter models have no live
browsing). Plain stdlib XML parsing — RSS 2.0 is simple enough that pulling
in a feed-parsing dependency isn't worth it."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx

from app.config import get_settings


@dataclass
class NewsItem:
    title: str
    link: str
    published: datetime | None
    summary: str
    source: str

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "link": self.link,
            "published": self.published.isoformat() if self.published else None,
            "summary": self.summary,
            "source": self.source,
        }


def _parse_pubdate(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _parse_feed(xml_text: str, source: str) -> list[NewsItem]:
    items: list[NewsItem] = []
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return items

    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        items.append(
            NewsItem(
                title=title,
                link=link,
                published=_parse_pubdate(item.findtext("pubDate")),
                summary=(item.findtext("description") or "").strip(),
                source=source,
            )
        )
    return items


def fetch_recent_items(feed_urls: list[str] | None = None, max_age_days: int | None = None) -> list[NewsItem]:
    """Best-effort fetch across all configured feeds. A feed that times out,
    404s, or returns malformed XML is skipped rather than failing the whole
    generation run — freshness is a nice-to-have, not a hard requirement."""
    settings = get_settings()
    feed_urls = feed_urls or settings.blog_news_feeds
    max_age_days = max_age_days if max_age_days is not None else settings.blog_news_max_age_days
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    all_items: list[NewsItem] = []
    for url in feed_urls:
        source = urlparse(url).netloc.removeprefix("www.")
        try:
            response = httpx.get(url, timeout=20, follow_redirects=True, headers={"User-Agent": "WaterlineBlogBot/1.0"})
            response.raise_for_status()
        except httpx.HTTPError:
            continue
        for parsed_item in _parse_feed(response.text, source):
            if parsed_item.published is None or parsed_item.published >= cutoff:
                all_items.append(parsed_item)

    return all_items


def relevant_items(items: list[NewsItem], keywords: str | None, limit: int = 5) -> list[NewsItem]:
    """Keyword-overlap ranking against a topic's target_keywords, most recent
    first among ties. Items with zero keyword overlap are dropped — better to
    run a post with no news section than to force in an unrelated headline."""
    terms = [t.strip().lower() for t in (keywords or "").split(",") if t.strip()]
    if not terms:
        return []

    scored: list[tuple[int, NewsItem]] = []
    for news_item in items:
        haystack = f"{news_item.title} {news_item.summary}".lower()
        score = sum(1 for term in terms if term in haystack)
        if score > 0:
            scored.append((score, news_item))

    scored.sort(key=lambda pair: (pair[0], pair[1].published or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    return [news_item for _, news_item in scored[:limit]]
