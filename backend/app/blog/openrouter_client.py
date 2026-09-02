"""Thin OpenRouter chat-completions client. OpenRouter exposes an
OpenAI-compatible API, so a raw httpx POST is enough — no SDK dependency."""

import json

import httpx

from app.config import get_settings

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterError(RuntimeError):
    pass


def chat_completion(model: str, system_prompt: str, user_prompt: str, *, temperature: float = 0.4) -> str:
    """Calls OpenRouter, returns the assistant message content as a string."""
    settings = get_settings()
    if not settings.openrouter_api_key:
        raise OpenRouterError("OPENROUTER_API_KEY is not configured")

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
        raise OpenRouterError(f"OpenRouter {model} returned {response.status_code}: {response.text[:500]}")

    data = response.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise OpenRouterError(f"Unexpected OpenRouter response shape: {json.dumps(data)[:500]}") from exc
