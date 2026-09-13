import pytest

from app.blog.generator import _extract_json, _slugify
from app.blog.openrouter_client import OpenRouterError


def test_extract_json_parses_plain_json():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_strips_markdown_fence():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_strips_bare_fence():
    assert _extract_json('```\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_ignores_preamble_around_a_fenced_block():
    """Reasoning-capable models narrate before answering however firmly the
    prompt says "JSON only" — the fence is still the payload."""
    raw = 'Sure! Here is the post:\n```json\n{"a": 1}\n```\nLet me know if you want edits.'
    assert _extract_json(raw) == {"a": 1}


def test_extract_json_recovers_a_bare_object_with_surrounding_prose():
    assert _extract_json('Thinking through this... {"a": 1, "b": {"c": 2}} — done.') == {"a": 1, "b": {"c": 2}}


def test_extract_json_prefers_the_whole_reply_when_it_is_already_valid():
    """Widening must never re-read a reply that parses as-is: a body
    containing a fenced code sample would otherwise get mined for the
    sample instead of the post."""
    raw = '{"body_markdown": "Example:\\n```json\\n{\\"not\\": \\"the payload\\"}\\n```"}'
    assert "body_markdown" in _extract_json(raw)


def test_extract_json_raises_on_garbage():
    with pytest.raises(Exception):
        _extract_json("not json at all")


def test_extract_json_rejects_a_non_object_reply():
    """A bare list or string parses as JSON but isn't a draft — better to
    fail loudly than to hand the generator something with no .get()."""
    with pytest.raises(Exception):
        _extract_json('["a", "b"]')


def test_slugify_basic():
    assert _slugify("How CRC Credit Bureau Works!") == "how-crc-credit-bureau-works"


def test_slugify_handles_unicode_and_punctuation():
    assert _slugify("What's the CBN's MPR? — Explained") == "what-s-the-cbn-s-mpr-explained"


def test_slugify_never_empty():
    assert _slugify("!!!") == "post"


def test_openrouter_error_is_exception():
    assert issubclass(OpenRouterError, RuntimeError)
