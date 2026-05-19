"""Perspective agent: defends a debate position."""

from app.agents.base import BaseAgent, DebaterState, load_prompt
from app.models.schemas import AgentThinking

# Cache the template at module level to avoid disk reads on every turn
_PROMPT_TEMPLATE = load_prompt("perspective.md")


class PerspectiveAgent(BaseAgent):
    def __init__(self, position_id: str, position_name: str, position_description: str):
        from app.config import settings
        model, api_key, base_url = settings.get_model_config("debater")
        system_prompt = _PROMPT_TEMPLATE.format(
            position_name=position_name,
            position_description=position_description,
            context="",
        )
        super().__init__(
            system_prompt=system_prompt,
            model=model or None,
            api_key=api_key or None,
            base_url=base_url or None,
        )
        self.position_id = position_id
        self.position_name = position_name
        self.position_description = position_description
        self.state = DebaterState()

    def _build_messages(self, context: str, user_message: str) -> list[dict]:
        system_prompt = _PROMPT_TEMPLATE.format(
            position_name=self.position_name,
            position_description=self.position_description,
            context=context,
        )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

    async def think_before_speaking(
        self, context: str, round_num: int,
    ) -> AgentThinking:
        """First pass: free-form thinking before speaking.

        Replaces rigid 5-field analysis with open-ended strategic thinking.
        Agent can optionally request specific data from the data clerk.
        State is injected so the agent builds on cross-round memory.
        """
        # Anti-stagnation hint
        strategy_hint = ""
        if self.state.consecutive_strategy_count() >= 2:
            last_strategy = self.state.strategies_used[-1] if self.strategies_used else ""
            strategy_hint = f"⚠️ 注意：你已经连续使用 {last_strategy} 两次了，建议换策略。\n\n"

        return await self.think(
            AgentThinking,
            context=context,
            user_message=(
                f"你是「{self.position_name}」方的辩手，准备第 {round_num} 轮发言。\n\n"
                f"【你的状态】\n"
                f"{self.state.to_prompt_text()}\n\n"
                f"{strategy_hint}"
                "自由思考当前辩论局势。你可以从任何角度分析——对手的漏洞、"
                "我方被反驳的点、需要什么数据支撑、本轮策略、新论点构思、"
                "对手可能如何反击等。\n"
                "不限角度，不限结构，想清楚最重要。\n\n"
                "如果数据池中缺少你需要的关键数据（如具体比赛数据、最新新闻等），"
                "在 data_need 中用自然语言描述你需要什么数据，"
                "例如：'詹姆斯在5月7日比赛的具体得分和篮板数据'。\n"
                "如果已有数据足够或不需要额外数据，data_need 留空。\n\n"
                "分析完毕后，提取结构化状态：\n"
                '输出JSON：{"thinking": "你的自由思考过程", '
                '"data_need": "你需要什么数据（自然语言描述，留空表示不需要）", '
                '"my_arguments_standing": ["你方仍站得住的论点1"], '
                '"my_arguments_refuted": ["被反驳的论点1"], '
                '"opponent_weaknesses": ["对手弱点1"], '
                '"chosen_strategy": "ATTACK"}\n\n'
                "策略选项：ATTACK(进攻对手漏洞) / DEFEND(强化被挑战论点) / "
                "REDIRECT(换新角度) / EVIDENCE(引入新数据)"
            ),
        )

    async def re_think_with_data(
        self, context: str, round_num: int,
        fetched_data_summary: str, original_thinking: str,
    ) -> AgentThinking:
        """Second thinking pass after new data arrives. Focuses on data usage."""
        return await self.think(
            AgentThinking,
            context=context,
            user_message=(
                f"你是「{self.position_name}」方的辩手，准备第 {round_num} 轮发言。\n\n"
                f"【你的初始思考】\n{original_thinking}\n\n"
                f"【数据研究员刚获取的新数据】\n{fetched_data_summary}\n\n"
                "数据研究员刚刚为你搜索了新数据。请重新分析：\n"
                "1. 新数据是否支持或削弱你之前的论点？\n"
                "2. 新数据揭示了什么可以利用的新论点？\n"
                "3. 你是否需要调整策略或数据引用计划？\n"
                "聚焦于如何利用新数据，不必重复已有分析。\n\n"
                '输出JSON：{"thinking": "你的重新分析", "data_need": ""}'
            ),
        )
