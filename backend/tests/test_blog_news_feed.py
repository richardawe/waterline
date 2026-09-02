from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import Mock, patch

from app.blog import news_feed

SAMPLE_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<title>Sample Feed</title>
<item>
<title>CBN raises interest rate again</title>
<link>https://example.com/cbn-rate</link>
<pubDate>{recent}</pubDate>
<description>The Central Bank of Nigeria adjusted its policy rate.</description>
</item>
<item>
<title>Unrelated sports story</title>
<link>https://example.com/sports</link>
<pubDate>{recent}</pubDate>
<description>A football match happened.</description>
</item>
<item>
<title>Old CBN news from last year</title>
<link>https://example.com/old</link>
<pubDate>{old}</pubDate>
<description>Old central bank news.</description>
</item>
</channel></rss>"""


def _rss(recent: datetime, old: datetime) -> str:
    return SAMPLE_RSS.format(recent=format_datetime(recent), old=format_datetime(old))


def test_parse_feed_extracts_items():
    now = datetime.now(timezone.utc)
    items = news_feed._parse_feed(_rss(now, now - timedelta(days=400)), source="example.com")
    assert len(items) == 3
    assert items[0].title == "CBN raises interest rate again"
    assert items[0].link == "https://example.com/cbn-rate"


def test_parse_feed_handles_malformed_xml_gracefully():
    assert news_feed._parse_feed("<not-xml", source="example.com") == []


def test_fetch_recent_items_filters_by_age():
    now = datetime.now(timezone.utc)
    body = _rss(now, now - timedelta(days=400))
    mock_response = Mock(text=body)
    mock_response.raise_for_status = Mock()
    with patch("app.blog.news_feed.httpx.get", return_value=mock_response):
        items = news_feed.fetch_recent_items(["https://example.com/feed"], max_age_days=14)
    titles = {i.title for i in items}
    assert "CBN raises interest rate again" in titles
    assert "Old CBN news from last year" not in titles


def test_fetch_recent_items_skips_unreachable_feeds():
    import httpx

    with patch("app.blog.news_feed.httpx.get", side_effect=httpx.ConnectError("boom")):
        assert news_feed.fetch_recent_items(["https://unreachable.example/feed"]) == []


def test_relevant_items_ranks_by_keyword_overlap():
    now = datetime.now(timezone.utc)
    items = news_feed._parse_feed(_rss(now, now - timedelta(days=1)), source="example.com")
    ranked = news_feed.relevant_items(items, "cbn, interest rate")
    assert ranked
    assert ranked[0].title.startswith("CBN raises")
    assert all("sports" not in i.title.lower() for i in ranked)


def test_relevant_items_empty_without_keywords():
    items = news_feed._parse_feed(_rss(datetime.now(timezone.utc), datetime.now(timezone.utc)), source="example.com")
    assert news_feed.relevant_items(items, None) == []
