from app.blog.sanitize import sanitize_html


def test_strips_script_tags():
    assert "<script>" not in sanitize_html("<p>hi</p><script>alert(1)</script>")


def test_strips_event_handler_attributes():
    out = sanitize_html('<img src="x.png" onerror="alert(1)">')
    assert "onerror" not in out


def test_neutralizes_javascript_href():
    out = sanitize_html('<a href="javascript:alert(1)">click</a>')
    assert "javascript:" not in out


def test_leaves_normal_content_untouched():
    html = "<h2>Heading</h2><p>Some <strong>text</strong> and a <a href=\"https://example.com\">link</a>.</p>"
    assert sanitize_html(html) == html
