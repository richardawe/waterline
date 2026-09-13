"""Thin OpenRouter chat-completions client. OpenRouter exposes an
OpenAI-compatible API, so a raw httpx POST is enough — no SDK dependency."""

import json
import logging
from dataclasses import dataclass

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Statuses that mean "this key/account can't call OpenRouter at all", as
# opposed to "this particular model didn't work". Falling through the rest
# of the chain on these just multiplies the same failure, so they stop the
# chain immediately and surface the real reason.
FATAL_STATUS_CODES = frozenset({401, 403})


class OpenRouterError(RuntimeError):
    #: True when the failure is the account/key itself rather than the model,
    #: so walking further down a fallback chain would only repeat it.
    fatal: bool = False


@dataclass(frozen=True)
class ChatResult:
    """Which model actually answered, and what it said. The model is worth
    carrying back: with a fallback chain the responder isn't necessarily the
    configured first choice, and posts record the model that wrote them."""

    model: str
    content: str


def chat_completion(model: str, system_prompt: str, user_prompt: str, *, temperature: float = 0.4) -> str:
    """Calls OpenRouter for one specific model, returns the assistant message
    content as a string."""
    settings = get_settings()
    if not settings.openrouter_api_key:
        error = OpenRouterError("OPENROUTER_API_KEY is not configured")
        error.fatal = True
        raise error

    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": settings.blog_site_base_url,
        "X-Title": "Waterline Finance Blog",
    }
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    response = httpx.post(OPENROUTER_URL, headers=headers, json=payload, timeout=120)
    if response.status_code != 200:
        error = OpenRouterError(f"OpenRouter {model} returned {response.status_code}: {response.text[:500]}")
        error.fatal = response.status_code in FATAL_STATUS_CODES
        raise error

    data = response.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise OpenRouterError(f"Unexpected OpenRouter response shape: {json.dumps(data)[:500]}") from exc


def chat_completion_with_fallback(
    models: list[str], system_prompt: str, user_prompt: str, *, temperature: float = 0.4
) -> ChatResult:
    """Tries each model in order and returns the first one that answers.

    Free-tier model availability on OpenRouter rotates without notice — a
    slug that worked yesterday can 404 ("no longer free") or 429 ("shared
    free pool exhausted") today, which is exactly what has taken this
    pipeline down three times. Walking a chain turns that from an outage
    into a logged fallback hop. A credentials failure (401/403) is not a
    per-model problem, so it stops the chain instead of being retried
    against every entry.
    """
    if not models:
        raise OpenRouterError("no OpenRouter model configured")

    failures: list[str] = []
    for model in models:
        try:
            content = chat_completion(model, system_prompt, user_prompt, temperature=temperature)
        except OpenRouterError as exc:
            if exc.fatal:
                raise
            failures.append(str(exc))
            logger.warning("OpenRouter model %s unavailable, trying next in chain: %s", model, exc)
            continue
        if model != models[0]:
            logger.warning("OpenRouter fell back to %s (first choice %s failed)", model, models[0])
        return ChatResult(model=model, content=content)

    raise OpenRouterError("all configured OpenRouter models failed:\n" + "\n".join(failures))
