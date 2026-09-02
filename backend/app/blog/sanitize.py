"""Lightweight defensive stripping of dangerous raw HTML before rendered
markdown reaches a public static page. Python-Markdown passes inline raw HTML
through unchanged, and this content is LLM-generated and auto-published
without human pre-review — so strip script-execution vectors even though a
compliant model/prompt shouldn't emit them. Regex-based on purpose: this is a
defense-in-depth pass over our own generated HTML, not a general untrusted-
HTML sanitizer; reach for a real library (bleach/nh3) if that need grows."""

import re

_DANGEROUS_TAGS = re.compile(r"<(script|style|iframe|object|embed)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_DANGEROUS_SELF_CLOSING = re.compile(r"<(script|style|iframe|object|embed)\b[^>]*/?>", re.IGNORECASE)
_ON_ATTR = re.compile(r'\son\w+\s*=\s*(".*?"|\'.*?\'|[^\s>]+)', re.IGNORECASE)
_JS_HREF = re.compile(r'(href|src)\s*=\s*(["\'])\s*javascript:', re.IGNORECASE)


def sanitize_html(html: str) -> str:
    html = _DANGEROUS_TAGS.sub("", html)
    html = _DANGEROUS_SELF_CLOSING.sub("", html)
    html = _ON_ATTR.sub("", html)
    html = _JS_HREF.sub(r'\1=\2#', html)
    return html
