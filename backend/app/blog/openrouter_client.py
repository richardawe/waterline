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

# Statuses that mean "this request was malformed for this model", as opposed
# to the model being unavailable. Worth retrying with fewer parameters before
# giving up on the model — see _payload_variants.
BAD_REQUEST_STATUS_CODES = frozenset({400, 422})

# Every free text model on OpenRouter is now reasoning-capable and most think
# by default; there is no plain instruct model left to pick. Left alone they
# spend minutes and thousands of tokens narrating before they answer, which
# is what pushed /admin/blog/generate past the ~300s nginx ceiling (a 504)
# and what makes a reply arrive wrapped in commentary instead of the strict
# JSON the prompts ask for (an unparseable reply, previously a bare 500).
#
# `enabled: false` and `effort: "none"` are two spellings of the same request.
# Providers honour different ones and the two agree, so sending both buys
# coverage without giving a validator anything to object to.
NO_REASONING = {"enabled": False, "effort": "none"}

# Generous on purpose: max_tokens counts reasoning tokens too on any provider
# that ignores NO_REASONING, and a budget that runs out mid-thought comes back
# as an empty message rather than a short one.
MAX_OUTPUT_TOKENS = 8000


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


def _payload_variants(
    model: str, system_prompt: str, user_prompt: str, temperature: float, json_mode: bool
) -> list[dict]:
    """The same request, from most tuned to least.

    Tried in order on a 400/422, so a provider that rejects one parameter
    costs only that parameter. An all-or-nothing retry would drop JSON mode
    along with the reasoning flag and quietly undo half the fix.
    """
    base = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    bounded = {**base, "max_tokens": MAX_OUTPUT_TOKENS}
    if json_mode:
        bounded["response_format"] = {"type": "json_object"}

    variants = [{**bounded, "reasoning": NO_REASONING}, bounded]
    if json_mode:
        variants.append({**base, "max_tokens": MAX_OUTPUT_TOKENS})
    variants.append(base)
    return variants


def chat_completion(
    model: str, system_prompt: str, user_prompt: str, *, temperature: float = 0.4, json_mode: bool = False
) -> str:
    """Calls OpenRouter for one specific model, returns the assistant message
    content as a string. `json_mode` asks the model for a JSON object where it
    supports response_format — the prompts ask for strict JSON anyway, this
    just makes the model enforce it too."""
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

    variants = _payload_variants(model, system_prompt, user_prompt, temperature, json_mode)
    response = None
    for index, payload in enumerate(variants):
        response = httpx.post(OPENROUTER_URL, headers=headers, json=payload, timeout=120)
        if response.status_code == 200:
            break
        last_variant = index == len(variants) - 1
        if last_variant or response.status_code not in BAD_REQUEST_STATUS_CODES:
            break
        logger.warning(
            "OpenRouter %s rejected request parameters (%s): %s — retrying with fewer",
            model,
            response.status_code,
            response.text[:200],
        )

    if response.status_code != 200:
        error = OpenRouterError(f"OpenRouter {model} returned {response.status_code}: {response.text[:500]}")
        error.fatal = response.status_code in FATAL_STATUS_CODES
        raise error

    data = response.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise OpenRouterError(f"Unexpected OpenRouter response shape: {json.dumps(data)[:500]}") from exc

    if not (content or "").strip():
        # A model that spent its whole budget thinking returns an empty
        # message rather than an error. Treated as this model failing so the
        # chain moves on, instead of handing the caller "" to parse as JSON.
        raise OpenRouterError(f"OpenRouter {model} returned an empty message")
    return content


def chat_completion_with_fallback(
    models: list[str], system_prompt: str, user_prompt: str, *, temperature: float = 0.4, json_mode: bool = False
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
            content = chat_completion(
                model, system_prompt, user_prompt, temperature=temperature, json_mode=json_mode
            )
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
