"""Agent base class."""

from pathlib import Path
from collections.abc import AsyncGenerator

from app.services.llm import stream_completion, complete, complete_json

PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(filename: str) -> str:
    """Load a prompt template from the prompts directory."""
    return (PROMPTS_DIR / filename).read_text(encoding="utf-8").strip()


class BaseAgent:
    """Base class for all agents."""

    def __init__(self, system_prompt: str):
        self.system_prompt = system_prompt

    def _build_messages(self, context: str, user_message: str) -> list[dict]:
        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"{context}\n\n{user_message}"},
        ]

    async def stream(self, context: str, user_message: str) -> AsyncGenerator[str, None]:
        """Stream agent response token by token."""
        messages = self._build_messages(context, user_message)
        async for token in stream_completion(messages):
            yield token

    async def respond(self, context: str, user_message: str) -> str:
        """Get complete response from agent."""
        messages = self._build_messages(context, user_message)
        return await complete(messages)

    async def respond_json(self, context: str, user_message: str) -> dict:
        """Get JSON response from agent."""
        messages = self._build_messages(context, user_message)
        return await complete_json(messages)
