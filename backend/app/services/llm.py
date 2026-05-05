"""LLM service using LiteLLM for async streaming."""

import json
import logging
from collections.abc import AsyncGenerator

import litellm

from app.config import settings

logger = logging.getLogger(__name__)

# Suppress litellm's verbose logging
litellm.suppress_debug_info = True


async def stream_completion(
    messages: list[dict],
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> AsyncGenerator[str, None]:
    """Stream token-level output from LLM. Yields content chunks."""
    model = model or settings.llm_model
    temperature = temperature or settings.llm_temperature
    max_tokens = max_tokens or settings.llm_max_tokens

    api_base = settings.llm_base_url or None

    response = await litellm.acompletion(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
        api_key=settings.llm_api_key or None,
        api_base=api_base,
        timeout=120,
    )

    async for chunk in response:
        delta = chunk.choices[0].delta
        if delta.content:
            yield delta.content


async def complete(
    messages: list[dict],
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    """Non-streaming completion. Returns full response text."""
    model = model or settings.llm_model
    temperature = temperature or settings.llm_temperature
    max_tokens = max_tokens or settings.llm_max_tokens

    api_base = settings.llm_base_url or None

    response = await litellm.acompletion(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=False,
        api_key=settings.llm_api_key or None,
        api_base=api_base,
        timeout=120,
    )

    return response.choices[0].message.content or ""


async def complete_json(
    messages: list[dict],
    model: str | None = None,
) -> dict:
    """Complete and parse JSON response. Returns {} on parse failure."""
    text = await complete(messages, model=model, temperature=0.3)
    try:
        # Try to extract JSON from markdown code blocks
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        return json.loads(text.strip())
    except (json.JSONDecodeError, IndexError):
        logger.warning("Failed to parse JSON response: %s", text[:200])
        return {}
