"""LLM adapter — single entry point for all skill loops.

Wraps the OpenAI SDK against a configurable base URL so the same code works
with RouteTokens (GLM/DeepSeek) and Kimi (Moonshot). Both are
OpenAI-compatible. MiniMax uses a different protocol and is not handled here.

Usage from a loop:

    from skill.llm import chat

    reply = chat([
        {"role": "system", "content": "You are the auto-broadcaster."},
        {"role": "user",   "content": feed_chunk},
    ])
"""

from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    api_key = os.environ.get("LLM_API_KEY")
    base_url = os.environ.get("LLM_BASE_URL")
    if not api_key or not base_url:
        raise RuntimeError(
            "LLM_API_KEY and LLM_BASE_URL must be set in .env "
            "(see .env.example for the GOSIM provider options)."
        )
    return OpenAI(api_key=api_key, base_url=base_url)


def chat(messages: list[dict], *, model: str | None = None, **kwargs) -> str:
    """Send a chat completion and return the assistant's text reply."""
    response = _client().chat.completions.create(
        model=model or os.environ["LLM_MODEL"],
        messages=messages,
        **kwargs,
    )
    return response.choices[0].message.content or ""
