"""Moderator agent: clarifies topics, identifies positions, guides debate, generates verdict."""

from app.agents.base import BaseAgent, load_prompt
from app.models.schemas import (
    ClarifyResult,
    DataClerkRecommendation,
    DataScope,
    DebateMinutes,
    PositionsResult,
    RoundJudgment,
)


class ModeratorAgent(BaseAgent):
    def __init__(self):
        super().__init__(system_prompt=load_prompt("moderator.md"))

    async def clarify_topic(self, topic: str) -> ClarifyResult:
        """Check if topic needs clarification. Returns typed result."""
        return await self.respond_typed(
            ClarifyResult,
            context="",
            user_message=(
                f"用户提交了以下问题：\n\n「{topic}」\n\n"
                "判断这个问题是否足够清晰、可以直接展开辩论。\n\n"
                "重要：大多数问题都不需要追问，直接返回 valid=true。"
                "只有在问题极度模糊（如\"聊聊那个事\"、\"怎么样\"、无具体指向）时才追问。\n"
                "不要追问评价标准、具体定义等细节——辩手会在辩论中自然展开这些讨论。\n\n"
                "输出JSON格式：\n"
                '{"valid": true/false, "reason": "原因", '
                '"question": "追问的问题（如果需要）", '
                '"suggestion": "修改建议（如果有）"}'
            ),
        )

    async def suggest_positions(self, topic: str, data_context: str = "") -> list[dict]:
        """Identify possible positions/answers for a topic."""
        data_section = ""
        if data_context:
            data_section = (
                f"\n\n以下是一些相关的网络搜索数据：\n{data_context}\n"
                "请参考这些信息，提出更有依据、更具体的立场建议。\n"
            )
        result = await self.respond_typed(
            PositionsResult,
            context="",
            user_message=(
                f"为以下问题识别 2-6 个互相对立的立场：\n\n「{topic}」\n\n"
                "立场必须是该问题的具体答案，而非分析视角、评价方法或元讨论。\n"
                "- 如果问题问「谁/哪个」，立场必须是具体的人名、地名、事物名\n"
                "- 如果问题问「是否/应该」，立场必须是明确的支持或反对\n"
                "- 如果有「补充说明」，它只是对原问题的补充限定，不要把补充说明本身当成议题\n"
                "- 立场之间必须互相竞争、不能同时成立\n"
                f"{data_section}"
                "输出JSON格式：\n"
                '{"positions": [{"id": "英文简写", "name": "立场名称（具体答案）", '
                '"description": "该立场的主张说明"}]}'
            ),
        )
        return [p.model_dump() for p in result.positions]

    async def recommend_data_clerk(self, topic: str) -> DataClerkRecommendation:
        """Evaluate whether topic needs real-time data from the data clerk."""
        return await self.respond_typed(
            DataClerkRecommendation,
            context="",
            user_message=(
                f"判断以下辩论议题是否需要数据研究员提供实时网络信息：\n\n「{topic}」\n\n"
                "需要数据研究员的场景：涉及具体数据、时事、人物对比、历史事实等\n"
                "不需要的场景：纯逻辑推理、价值观讨论、哲学命题等\n\n"
                '输出JSON：{"recommended": true/false, "reason": "原因"}'
            ),
        )

    async def generate_opening(self, topic: str, positions: list[dict]) -> str:
        """Generate opening remarks for the debate."""
        pos_desc = "\n".join(f"- {p['name']}：{p['description']}" for p in positions)
        return await self.respond(
            context="",
            user_message=(
                f"辩论议题：「{topic}」\n\n"
                f"参与立场：\n{pos_desc}\n\n"
                "请生成一段开场白（不超过 150 字），介绍议题和各方立场，宣布辩论开始。\n"
                "如果议题涉及具体事件（如某场比赛、某条新闻），在开场白中明确指出该事件的"
                "具体日期、对阵双方等关键信息，以此界定本轮辩论的数据边界。"
            ),
        )

    async def establish_data_scope(self, topic: str, positions: list[dict]) -> DataScope:
        """Analyze the topic and establish clear data boundaries for the debate."""
        pos_names = ", ".join(p["name"] for p in positions)
        return await self.respond_typed(
            DataScope,
            context="",
            user_message=(
                f"辩论议题：「{topic}」\n"
                f"参与立场：{pos_names}\n\n"
                "分析这个议题，确定辩论的数据边界。你需要：\n"
                "1. 明确议题指向的具体事件（如某场具体比赛、某个具体事件）\n"
                "2. 确定时间范围（具体到日期，如'2026年5月7日'）\n"
                "3. 列出关键实体（人名、队名、组织名等）\n"
                "4. 制定相关性规则：什么样的数据是相关的，什么是不相关的\n\n"
                "例如：议题「哈登和米切尔今天谁表现更好」→\n"
                "  specific_event: '2026年5月7日NBA骑士队比赛'\n"
                "  time_range: '2026年5月7日'\n"
                "  key_entities: ['哈登', '米切尔', '骑士队']\n"
                "  relevance_rule: '只接受该场比赛的数据，其他比赛数据不可用'\n\n"
                "输出JSON格式。"
            ),
        )

    async def judge_round(
        self, topic: str, round_number: int, max_rounds: int,
        discussion_summary: str, active_positions: list[dict]
    ) -> RoundJudgment:
        """Judge whether to continue or conclude after a round."""
        pos_names = ", ".join(p["name"] for p in active_positions)
        return await self.respond_typed(
            RoundJudgment,
            context="",
            user_message=(
                f"辩论议题：「{topic}」\n"
                f"当前第 {round_number}/{max_rounds} 轮\n"
                f"活跃立场：{pos_names}\n\n"
                f"本轮辩论内容：\n{discussion_summary}\n\n"
                "判断是否继续辩论。输出JSON格式：\n"
                '{"decision": "CONTINUE" 或 "CONCLUDE", '
                '"reason": "判断理由", '
                '"guidance": "下一轮引导语，用「XX方」称呼各立场（如果继续）"}'
            ),
        )

    async def generate_minutes(
        self, topic: str, positions: list[dict], all_messages: list[dict]
    ) -> dict:
        """Generate structured debate verdict."""
        transcript_lines = []
        for msg in all_messages:
            role = msg.get("agent_name") or msg.get("role", "")
            content = msg.get("content", "")
            transcript_lines.append(f"[{role}]: {content}")
        transcript = "\n\n".join(transcript_lines)

        pos_desc = ", ".join(p["name"] for p in positions)

        result = await self.respond_typed(
            DebateMinutes,
            context="",
            user_message=(
                f"辩论议题：「{topic}」\n"
                f"参与立场：{pos_desc}\n\n"
                f"完整辩论记录：\n{transcript}\n\n"
                "请按照你系统提示中的辩论裁决格式，生成结构化裁决报告。"
            ),
        )
        return result.model_dump()
