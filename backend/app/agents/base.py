"""Agent base class."""

from datetime import datetime, timezone
from pathlib import Path
from collections.abc import AsyncGenerator
from typing import TypeVar

from pydantic import BaseModel

from app.services.llm import stream_completion, complete, complete_json, complete_typed

PROMPTS_DIR = Path(__file__).parent / "prompts"

T = TypeVar("T", bound=BaseModel)


def load_prompt(filename: str) -> str:
    """Load a prompt template from the prompts directory."""
    return (PROMPTS_DIR / filename).read_text(encoding="utf-8").strip()


def build_system_context() -> str:
    """Build dynamic system context injected into every agent."""
    now = datetime.now(timezone.utc)
    weekdays = ["一", "二", "三", "四", "五", "六", "日"]
    return (
        f"【系统信息】\n"
        f"当前日期时间（UTC）：{now.strftime('%Y年%m月%d日 %H:%M')} 星期{weekdays[now.weekday()]}\n"
        f"当前年份：{now.year}年"
    )


class BaseAgent:
    """Base class for all agents."""

    def __init__(self, system_prompt: str, model: str | None = None):
        self.system_prompt = system_prompt
        self._model_override = model

    def _build_messages(self, context: str, user_message: str) -> list[dict]:
        system_content = f"{build_system_context()}\n\n{self.system_prompt}"
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": f"{context}\n\n{user_message}"},
        ]

    async def stream(self, context: str, user_message: str) -> AsyncGenerator[str, None]:
        """Stream agent response token by token."""
        messages = self._build_messages(context, user_message)
        async for token in stream_completion(messages, model=self._model_override):
            yield token

    async def respond(self, context: str, user_message: str) -> str:
        """Get complete response from agent."""
        messages = self._build_messages(context, user_message)
        return await complete(messages, model=self._model_override)

    async def respond_json(self, context: str, user_message: str) -> dict:
        """Get JSON response from agent."""
        messages = self._build_messages(context, user_message)
        return await complete_json(messages, model=self._model_override)

    async def respond_typed(self, response_model: type[T], context: str, user_message: str) -> T:
        """Get typed response validated against a Pydantic model."""
        messages = self._build_messages(context, user_message)
        return await complete_typed(messages, response_model, model=self._model_override)
