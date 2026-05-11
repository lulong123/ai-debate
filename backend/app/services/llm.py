"""LLM service using LiteLLM for async streaming."""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import TypeVar

import litellm
from pydantic import BaseModel, ValidationError

from app.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Suppress litellm's verbose logging
litellm.suppress_debug_info = True

# Global semaphore to limit concurrent LLM calls (prevents 429 burst)
_llm_semaphore = asyncio.Semaphore(3)

# Rate limit retry settings
_RATE_LIMIT_RETRIES = 3
_RATE_LIMIT_BASE_DELAY = 2.0  # seconds


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

    async with _llm_semaphore:
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


async def _call_with_retry(fn, *args, **kwargs):
    """Call an async function with rate-limit retry (exponential backoff)."""
    async with _llm_semaphore:
        for attempt in range(_RATE_LIMIT_RETRIES + 1):
            try:
                return await fn(*args, **kwargs)
            except litellm.RateLimitError:
                if attempt >= _RATE_LIMIT_RETRIES:
                    raise
                delay = _RATE_LIMIT_BASE_DELAY * (2 ** attempt)
                logger.warning("Rate limited (429), retrying in %.1fs (attempt %d/%d)",
                               delay, attempt + 1, _RATE_LIMIT_RETRIES)
                await asyncio.sleep(delay)


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

    response = await _call_with_retry(
        litellm.acompletion,
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
    """Complete with forced JSON mode. Returns {} on parse failure."""
    model_name = model or settings.llm_model
    api_base = settings.llm_base_url or None

    response = await _call_with_retry(
        litellm.acompletion,
        model=model_name,
        messages=messages,
        temperature=0.3,
        max_tokens=settings.llm_max_tokens,
        stream=False,
        api_key=settings.llm_api_key or None,
        api_base=api_base,
        timeout=120,
        response_format={"type": "json_object"},
    )

    text = response.choices[0].message.content or ""
    try:
        # Extract JSON from markdown code blocks if present
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        return json.loads(text.strip())
    except (json.JSONDecodeError, IndexError):
        logger.warning("Failed to parse JSON response: %s", text[:200])
        return {}


async def complete_typed(
    messages: list[dict],
    response_model: type[T],
    model: str | None = None,
) -> T:
    """Complete with forced JSON mode, validate against Pydantic model.

    Falls back to model defaults if validation fails.
    """
    model_name = model or settings.llm_model
    api_base = settings.llm_base_url or None

    response = await _call_with_retry(
        litellm.acompletion,
        model=model_name,
        messages=messages,
        temperature=0.3,
        max_tokens=settings.llm_max_tokens,
        stream=False,
        api_key=settings.llm_api_key or None,
        api_base=api_base,
        timeout=120,
        response_format={"type": "json_object"},
    )

    text = response.choices[0].message.content or ""
    try:
        data = json.loads(text.strip())
        return response_model.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as e:
        logger.warning(
            "Failed to parse/validate typed response (%s): %s | text: %s",
            response_model.__name__, e, text[:200],
        )
        # Try partial parsing: salvage valid fields from raw JSON
        if isinstance(data, dict):
            try:
                return _partial_validate(response_model, data)
            except Exception:
                pass
        return response_model()


def _partial_validate(model: type[T], data: dict) -> T:
    """Attempt to build a model with whatever fields parsed correctly.

    For each field, try to validate it individually. If a field fails,
    use its default. This prevents one bad field (e.g. data_need
    receiving unexpected types) from destroying valid data
    in other fields (e.g. thinking text).
    """
    import pydantic

    fields = model.model_fields
    clean = {}
    for name, field_info in fields.items():
        if name not in data:
            continue
        try:
            # Validate just this field's value
            field_type = field_info.annotation
            value = data[name]
            # Use Pydantic's TypeAdapter for per-field validation
            adapter = pydantic.TypeAdapter(field_type)
            clean[name] = adapter.validate_python(value)
        except (pydantic.ValidationError, Exception):
            # Skip invalid fields — defaults will be used
            pass
    return model(**clean)
