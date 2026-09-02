import pytest

from app.blog.generator import _extract_json, _slugify
from app.blog.openrouter_client import OpenRouterError


def test_extract_json_parses_plain_json():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_strips_markdown_fence():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_strips_bare_fence():
    assert _extract_json('```\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_raises_on_garbage():
    with pytest.raises(Exception):
        _extract_json("not json at all")


def test_slugify_basic():
    assert _slugify("How CRC Credit Bureau Works!") == "how-crc-credit-bureau-works"


def test_slugify_handles_unicode_and_punctuation():
    assert _slugify("What's the CBN's MPR? — Explained") == "what-s-the-cbn-s-mpr-explained"


def test_slugify_never_empty():
    assert _slugify("!!!") == "post"


def test_openrouter_error_is_exception():
    assert issubclass(OpenRouterError, RuntimeError)
