"""Scorer agent: evaluates each perspective agent's contribution."""

from app.agents.base import BaseAgent, load_prompt


class ScorerAgent(BaseAgent):
    def __init__(self):
        super().__init__(system_prompt=load_prompt("scorer.md"))

    async def score_round(
        self, topic: str, round_messages: list[dict], active_angles: list[dict]
    ) -> dict:
        """Score each active angle based on their round contributions."""
        # Build context of this round's messages
        msg_lines = []
        for msg in round_messages:
            agent = msg.get("agent_name", "unknown")
            content = msg.get("content", "")
            msg_lines.append(f"[{agent}]: {content}")
        messages_text = "\n\n".join(msg_lines)

        angle_list = "\n".join(
            f"- {a['name']} (ID: {a['id']}): {a.get('description', '')}"
            for a in active_angles
        )

        result = await self.respond_json(
            context="",
            user_message=(
                f"讨论议题：「{topic}」\n\n"
                f"本轮发言的活跃角度：\n{angle_list}\n\n"
                f"本轮讨论内容：\n{messages_text}\n\n"
                "请按照评分标准为每个角度评分。输出JSON格式：\n"
                '{"scores": [{"angle_id": "...", "angle_name": "...", "total": 85, '
                '"dimensions": {"evidence": 90, "responsiveness": 80, "novelty": 75}, '
                '"comment": "简短评价"}]}'
            ),
        )
        return result
