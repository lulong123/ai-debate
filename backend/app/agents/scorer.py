"""Scorer agent: distributes 100 points among debate positions."""

from app.agents.base import BaseAgent, load_prompt
from app.models.schemas import ScoreResult


class ScorerAgent(BaseAgent):
    def __init__(self):
        super().__init__(system_prompt=load_prompt("scorer.md"))

    async def score_round(
        self, topic: str, round_messages: list[dict], active_positions: list[dict]
    ) -> dict:
        """Distribute 100 points among active positions based on round performance."""
        msg_lines = []
        for msg in round_messages:
            agent = msg.get("agent_name", "unknown")
            content = msg.get("content", "")
            msg_lines.append(f"[{agent}]: {content}")
        messages_text = "\n\n".join(msg_lines)

        pos_list = "\n".join(
            f"- {p['name']} (ID: {p['id']}): {p.get('description', '')}"
            for p in active_positions
        )

        result = await self.respond_typed(
            ScoreResult,
            context="",
            user_message=(
                f"辩论议题：「{topic}」\n\n"
                f"本轮发言的活跃立场：\n{pos_list}\n\n"
                f"本轮辩论内容：\n{messages_text}\n\n"
                "请将 100 分分配给各立场，分数之和必须等于 100。输出JSON格式：\n"
                '{"scores": [{"position_id": "...", "position_name": "...", '
                '"points": 60, "comment": "简短评价"}]}'
            ),
        )
        return result.model_dump()
