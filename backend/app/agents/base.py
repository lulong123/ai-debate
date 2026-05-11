"""Agent base class."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from app.config import settings
from app.services.llm import complete, complete_json, complete_typed, stream_completion

PROMPTS_DIR = Path(__file__).parent / "prompts"

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)


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


@dataclass
class DebaterState:
    """Cross-round state for a debater agent.

    Lives on the PerspectiveAgent instance (in-memory, dies with discussion).
    Updated by orchestrator after each thinking pass.
    """

    arguments_standing: list[str] = field(default_factory=list)
    arguments_refuted: list[str] = field(default_factory=list)
    opponent_weaknesses: list[str] = field(default_factory=list)
    strategies_used: list[str] = field(default_factory=list)
    MAX_PER_CATEGORY: int = 5

    def update(self, thinking: BaseModel) -> None:
        """Merge AgentThinking result into state, truncating to MAX_PER_CATEGORY."""
        new_standing = getattr(thinking, "my_arguments_standing", []) or []
        new_refuted = getattr(thinking, "my_arguments_refuted", []) or []
        new_weaknesses = getattr(thinking, "opponent_weaknesses", []) or []
        chosen = getattr(thinking, "chosen_strategy", "") or ""

        # Keep old items that still fit, then append new ones
        cap = self.MAX_PER_CATEGORY
        self.arguments_standing = (
            self.arguments_standing[-(cap - len(new_standing)):] + new_standing
        )[-cap:]
        self.arguments_refuted = (
            self.arguments_refuted[-(cap - len(new_refuted)):] + new_refuted
        )[-cap:]
        self.opponent_weaknesses = (
            self.opponent_weaknesses[-(cap - len(new_weaknesses)):] + new_weaknesses
        )[-cap:]

        if chosen:
            self.strategies_used.append(chosen)
            self.strategies_used = self.strategies_used[-cap:]

    def to_prompt_text(self) -> str:
        """Format state for injection into thinking prompt."""
        lines = []
        if self.arguments_standing:
            lines.append("✅ 仍站得住的论点：" + "；".join(self.arguments_standing))
        if self.arguments_refuted:
            lines.append("❌ 被反驳的论点：" + "；".join(self.arguments_refuted))
        if self.opponent_weaknesses:
            lines.append("🔍 发现的对手弱点：" + "；".join(self.opponent_weaknesses))
        if self.strategies_used:
            lines.append("📊 已用策略：" + " → ".join(self.strategies_used))
        return "\n".join(lines) if lines else "（第一轮，暂无历史状态）"

    def consecutive_strategy_count(self) -> int:
        """Count how many times the last strategy was used consecutively."""
        if not self.strategies_used:
            return 0
        last = self.strategies_used[-1]
        count = 0
        for s in reversed(self.strategies_used):
            if s == last:
                count += 1
            else:
                break
        return count


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

    async def think(
        self, think_model: type[T], context: str, user_message: str,
    ) -> T:
        """First pass: generate structured thinking before acting.

        Returns a ThinkResult Pydantic model. The orchestrator will
        inject this thinking into the second-pass context.
        """
        messages = self._build_messages(context, user_message)
        result = await complete_typed(messages, think_model, model=self._model_override)
        logger.info(
            "Agent thinking (%s): %s",
            think_model.__name__,
            result.model_dump_json()[:500],
        )
        return result

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
