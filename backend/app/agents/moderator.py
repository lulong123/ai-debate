"""Moderator agent: clarifies topics, suggests angles, guides discussion, generates minutes."""

import json

from app.agents.base import BaseAgent, load_prompt


class ModeratorAgent(BaseAgent):
    def __init__(self):
        super().__init__(system_prompt=load_prompt("moderator.md"))

    async def clarify_topic(self, topic: str) -> dict:
        """Check if topic needs clarification. Returns clarification result."""
        result = await self.respond_json(
            context="",
            user_message=(
                f"用户提交了以下议题：\n\n「{topic}」\n\n"
                "请判断这个议题是否清晰。如果需要澄清，提出你的问题。\n\n"
                "输出JSON格式：\n"
                '{"valid": true/false, "reason": "原因", '
                '"question": "追问的问题（如果需要）", '
                '"suggestion": "修改建议（如果有）"}'
            ),
        )
        return {
            "valid": result.get("valid", True),
            "reason": result.get("reason", ""),
            "question": result.get("question", ""),
            "suggestion": result.get("suggestion", ""),
        }

    async def suggest_angles(self, topic: str) -> list[dict]:
        """Suggest discussion angles for a topic."""
        result = await self.respond_json(
            context="",
            user_message=(
                f"为以下议题建议 3-5 个不同的讨论角度：\n\n「{topic}」\n\n"
                "输出JSON格式：\n"
                '{"angles": [{"id": "英文简写", "name": "角度名称", '
                '"description": "角度说明"}]}'
            ),
        )
        return result.get("angles", [])

    async def generate_opening(self, topic: str, angles: list[dict]) -> str:
        """Generate opening remarks for the discussion."""
        angle_desc = "\n".join(f"- {a['name']}：{a['description']}" for a in angles)
        return await self.respond(
            context="",
            user_message=(
                f"讨论议题：「{topic}」\n\n"
                f"参与角度：\n{angle_desc}\n\n"
                "请生成一段开场白（不超过 150 字），介绍议题和各角度，宣布讨论开始。"
            ),
        )

    async def judge_round(
        self, topic: str, round_number: int, max_rounds: int,
        discussion_summary: str, active_angles: list[dict]
    ) -> dict:
        """Judge whether to continue or conclude after a round."""
        angle_names = ", ".join(a["name"] for a in active_angles)
        result = await self.respond_json(
            context="",
            user_message=(
                f"讨论议题：「{topic}」\n"
                f"当前第 {round_number}/{max_rounds} 轮\n"
                f"活跃角度：{angle_names}\n\n"
                f"本轮讨论内容：\n{discussion_summary}\n\n"
                "判断是否继续讨论。输出JSON格式：\n"
                '{"decision": "CONTINUE" 或 "CONCLUDE", '
                '"reason": "判断理由", '
                '"guidance": "下一轮引导语（如果继续）"}'
            ),
        )
        return {
            "decision": result.get("decision", "CONTINUE"),
            "reason": result.get("reason", ""),
            "guidance": result.get("guidance", ""),
        }

    async def generate_minutes(
        self, topic: str, angles: list[dict], all_messages: list[dict]
    ) -> dict:
        """Generate structured meeting minutes."""
        # Build discussion transcript
        transcript_lines = []
        for msg in all_messages:
            role = msg.get("agent_name") or msg.get("role", "")
            content = msg.get("content", "")
            transcript_lines.append(f"[{role}]: {content}")
        transcript = "\n\n".join(transcript_lines)

        angle_desc = ", ".join(a["name"] for a in angles)

        result = await self.respond_json(
            context="",
            user_message=(
                f"讨论议题：「{topic}」\n"
                f"参与角度：{angle_desc}\n\n"
                f"完整讨论记录：\n{transcript}\n\n"
                "请按照你系统提示中的会议纪要格式，生成结构化会议纪要。"
            ),
        )
        return result
