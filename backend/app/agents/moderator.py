"""Moderator agent: clarifies topics, identifies positions, guides debate, generates verdict."""

from app.agents.base import BaseAgent, load_prompt
from app.models.schemas import (
    AgentThinking,
    ClarifyResult,
    DataClerkRecommendation,
    DataScope,
    DebateMinutes,
    PositionsResult,
    RoundJudgment,
    SubjectClarityCheck,
)


class ModeratorAgent(BaseAgent):
    def __init__(self):
        from app.config import settings
        model, api_key, base_url = settings.get_model_config("moderator")
        super().__init__(
            system_prompt=load_prompt("moderator.md"),
            model=model or None,
            api_key=api_key or None,
            base_url=base_url or None,
        )

    async def check_subject_clarity(self, topic: str) -> SubjectClarityCheck:
        """Quick check: is the main subject clear AND does the topic need search?

        Returns subject_clear=False when entities are unknown/fabricated.
        Returns needs_search=False when the topic is purely philosophical/abstract
        and doesn't benefit from web data.
        """
        return await self.respond_typed(
            SubjectClarityCheck,
            context="",
            user_message=(
                f"快速判断以下议题是否需要启动网络搜索：\n\n「{topic}」\n\n"
                "需要同时回答两个问题：\n\n"
                "**1. 主体是否清晰？（subject_clear）**\n"
                "主体不清晰的情况：\n"
                "- 完全陌生、搜索引擎搜不到的人名/事物\n"
                "- 编造/虚构的人物或事件\n"
                "- 模糊指代（如「那个人」「最近那件事」）\n"
                "主体清晰的情况：\n"
                "- 真实公众人物、历史人物\n"
                "- 具体事件、比赛、政策、产品\n"
                "- 哲学/价值观类议题（主体本身就是抽象概念）\n\n"
                "**2. 议题是否需要网络搜索？（needs_search）**\n"
                "判断标准：只有需要**最新、实时、时效性强**的数据才需要搜索。"
                "历史知识、文学常识、哲学观点等训练数据已充分覆盖的内容不需要搜索。\n"
                "不需要搜索的情况（needs_search=false）：\n"
                "- 纯哲学思辨（如「人性本善还是本恶」「自由与公平哪个更重要」）\n"
                "- 纯逻辑推理（如「先有鸡还是先有蛋」）\n"
                "- 纯价值观/道德讨论（如「善意的谎言是否可接受」）\n"
                "- 文学/艺术鉴赏（如「李白和杜甫谁的诗更好」）\n"
                "- 历史人物/事件评价（如「希望是好汉」「秦始皇功过」）\n"
                "- 通用知识对比（如「爱因斯坦和牛顿谁更伟大」）\n"
                "需要搜索的情况（needs_search=true）：\n"
                "- 最近发生的新闻事件（如「今天的NBA比赛谁赢了」）\n"
                "- 最新数据/统计/排名（如「2025年GDP排名」）\n"
                "- 实时变化的对比（如「当前比特币和黄金哪个更值得投资」）\n"
                "- 近期赛事/政策/产品发布等时效性话题\n\n"
                '输出JSON：{"subject_clear": true/false, "needs_search": true/false, '
                '"unclear_entities": ["不清晰的实体"], "reason": "简短原因"}'
            ),
        )

    async def think_before_clarifying(self, topic: str, data_context: str = "") -> AgentThinking:
        """First pass: free-form thinking before clarifying topic."""
        data_section = ""
        if data_context:
            data_section = f"\n\n搜索到的相关数据：\n{data_context}\n"
        return await self.think(
            AgentThinking,
            context="",
            user_message=(
                f"用户提交了一个议题：「{topic}」\n"
                f"{data_section}"
                "在正式分析前，先自由思考：\n"
                "1. 这个议题的核心问题是什么？涉及哪些关键概念和实体？\n"
                "2. 这是什么类型的辩论？（事实争议、价值判断、政策选择等）\n"
                "3. 可能有哪些对立立场？\n"
                "4. 需要什么数据支持？\n\n"
                "如果你需要搜索具体数据来帮助判断（如最新事实、统计数据等），"
                "在 data_need 中用自然语言描述你需要什么数据，"
                "例如：'詹姆斯在5月7日比赛的最新得分数据'。\n"
                "如果已有信息足够或不需要额外数据，data_need 留空。\n\n"
                '输出JSON：{"thinking": "你的自由思考过程", "data_need": "你需要什么数据（留空表示不需要）"}'
            ),
        )

    async def think_before_suggesting(self, topic: str, data_context: str = "") -> AgentThinking:
        """First pass: free-form thinking before suggesting positions."""
        data_section = ""
        if data_context:
            data_section = f"\n\n搜索到的相关数据：\n{data_context}\n"
        return await self.think(
            AgentThinking,
            context="",
            user_message=(
                f"需要为以下议题识别辩论立场：「{topic}」\n"
                f"{data_section}"
                "在提出立场前，先自由思考：\n"
                "1. 这个问题有哪些可能的答案方向？\n"
                "2. 哪些立场之间会形成有意义的对立？\n"
                "3. 每个立场的核心论据可能是什么？\n\n"
                "如果你需要搜索具体数据来帮助识别立场，"
                "在 data_need 中用自然语言描述你需要什么数据。\n"
                "如果已有信息足够或不需要额外数据，data_need 留空。\n\n"
                '输出JSON：{"thinking": "你的自由思考过程", "data_need": "你需要什么数据（留空表示不需要）"}'
            ),
        )

    async def clarify_topic(self, topic: str, data_context: str = "", round: int = 1) -> ClarifyResult:
        """Check if topic needs clarification. Returns typed result."""
        data_section = ""
        if data_context:
            data_section = (
                f"\n\n以下是通过网络搜索到的相关事实数据：\n{data_context}\n\n"
                "【重要规则】你必须先充分理解以上搜索数据，再判断是否需要追问：\n"
                "1. 先从搜索数据中提取关键事实（如球员所属球队、比赛日期、同队/对手关系等）\n"
                "2. 用这些事实来消除问题中的歧义——不要无视搜索结果去问搜索数据已经回答的问题\n"
                "3. 如果搜索数据消除了歧义，直接返回 valid=true\n"
                "4. 只有在搜索数据也未能消除歧义时，才向用户追问\n"
            )
        return await self.respond_typed(
            ClarifyResult,
            context="",
            user_message=(
                f"用户提交了以下问题：\n\n「{topic}」\n\n"
                f"当前是第 {round}/3 轮审议。\n\n"
                "请按以下优先级判断：\n\n"
                "**第一步：是否拒绝？**\n"
                "如果议题满足以下任一条件，设置 rejected=true：\n"
                "- 涉及敏感信息或违法违规内容\n"
                "- 完全编造的、不存在的人物或事件（搜索数据也没有相关信息）\n"
                "- 无意义的组合或纯粹恶搞（如\"爱因斯坦和孙悟空谁更强\"）\n"
                "- 网络搜索也无法找到任何相关信息的陌生话题\n"
                "拒绝时必须在 reason 中说明具体原因。\n\n"
                "**第二步：是否需要追问？**\n"
                "如果议题模糊、缺少关键信息（如\"那个人\"指谁、哪场比赛、哪个时间段），"
                "设置 valid=false 并在 question 中提出具体追问。\n"
                f"注意：当前是第 {round}/3 轮，如果是第3轮仍不清晰，建议拒绝。\n"
                "大多数问题不需要追问，直接返回 valid=true。\n\n"
                "**第三步：是否需要数据研究员？**\n"
                "如果议题涉及具体数据、时事、人物对比、历史事实等需要实时信息的话题，"
                "设置 need_data_clerk=true。\n"
                "纯逻辑推理、价值观讨论、哲学命题等不需要，设为 false。\n\n"
                f"{data_section}"
                "输出JSON格式：\n"
                '{"valid": true/false, "rejected": true/false, '
                '"reason": "原因（拒绝时必填）", '
                '"question": "追问的问题（valid=false时填写）", '
                '"suggestion": "修改建议", '
                '"need_data_clerk": true/false, '
                f'"clarify_round": {round}' + '}'
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

    async def establish_data_scope(
        self, topic: str, positions: list[dict],
        data_context: str = "",
    ) -> DataScope:
        """Analyze the topic and establish clear data boundaries for the debate."""
        pos_names = ", ".join(p["name"] for p in positions)
        data_section = ""
        if data_context:
            data_section = (
                f"\n\n以下是通过网络搜索到的相关事实数据：\n{data_context}\n"
                "请基于这些真实数据来界定数据边界，不要凭记忆猜测。\n"
            )
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
                "例如：议题「XX和YY今天谁表现更好」→\n"
                "  先看搜索数据确认XX和YY现在在哪个队，上一场是哪场，再界定边界\n"
                "  specific_event: '根据搜索数据确认的具体比赛'\n"
                "  time_range: '根据搜索数据确认的日期'\n"
                "  key_entities: ['根据搜索数据确认的参赛方']\n"
                "  relevance_rule: '只接受该场比赛的数据'\n"
                f"{data_section}"
                "输出JSON格式。"
            ),
        )

    async def think_before_judging(
        self, topic: str, round_number: int, max_rounds: int,
        discussion_summary: str, active_positions: list[dict],
    ) -> AgentThinking:
        """First pass: free-form thinking before judging."""
        pos_names = ", ".join(p["name"] for p in active_positions)
        return await self.think(
            AgentThinking,
            context="",
            user_message=(
                f"辩论议题：「{topic}」\n"
                f"当前第 {round_number}/{max_rounds} 轮\n"
                f"活跃立场：{pos_names}\n\n"
                f"本轮辩论内容：\n{discussion_summary}\n\n"
                "在做出判断前，自由分析当前局势。你可以思考：各方论点强度、"
                "未解决的冲突、逻辑谬误、辩论覆盖度、是否该继续等。"
                "不限角度，想清楚最重要。\n\n"
                "如果你需要搜索具体数据来帮助判断（如验证辩手引用的事实、"
                "查证某个统计数据等），在 data_need 中用自然语言描述你需要什么数据。\n"
                "如果已有信息足够或不需要额外数据，data_need 留空。\n\n"
                '输出JSON：{"thinking": "你的自由思考过程", "data_need": "你需要什么数据（留空表示不需要）"}'
            ),
        )

    async def think_before_minutes(
        self, topic: str, positions: list[dict], all_messages: list[dict],
    ) -> AgentThinking:
        """First pass: free-form thinking before generating minutes."""
        pos_desc = ", ".join(p["name"] for p in positions)
        transcript_lines = []
        for msg in all_messages:
            role = msg.get("agent_name") or msg.get("role", "")
            content = msg.get("content", "")
            transcript_lines.append(f"[{role}]: {content[:300]}")
        transcript = "\n\n".join(transcript_lines)
        return await self.think(
            AgentThinking,
            context="",
            user_message=(
                f"辩论议题：「{topic}」\n"
                f"参与立场：{pos_desc}\n\n"
                f"完整辩论记录：\n{transcript}\n\n"
                "在生成纪要前，自由分析整个辩论。你可以思考：整体质量、"
                "关键转折点、各方最强论点、裁决理由等。不限角度，想清楚最重要。\n\n"
                '输出JSON：{"thinking": "你的自由思考过程", "data_need": ""}'
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
        self, topic: str, positions: list[dict], all_messages: list[dict],
        thinking_text: str = "",
    ) -> dict:
        """Generate structured debate verdict, optionally informed by thinking."""
        transcript_lines = []
        for msg in all_messages:
            role = msg.get("agent_name") or msg.get("role", "")
            content = msg.get("content", "")
            transcript_lines.append(f"[{role}]: {content}")
        transcript = "\n\n".join(transcript_lines)

        pos_desc = ", ".join(p["name"] for p in positions)

        thinking_section = ""
        if thinking_text:
            thinking_section = f"\n\n【你的思考分析】\n{thinking_text}\n"

        result = await self.respond_typed(
            DebateMinutes,
            context="",
            user_message=(
                f"辩论议题：「{topic}」\n"
                f"参与立场：{pos_desc}\n\n"
                f"完整辩论记录：\n{transcript}\n"
                f"{thinking_section}"
                "请按照你系统提示中的辩论裁决格式，生成结构化裁决报告。"
            ),
        )
        return result.model_dump()

    async def re_think_with_data(
        self, topic: str, round_number: int, max_rounds: int,
        discussion_summary: str, active_positions: list[dict],
        fetched_data_summary: str, original_thinking: str,
    ) -> AgentThinking:
        """Second thinking pass after new data arrives for moderator."""
        pos_names = ", ".join(p["name"] for p in active_positions)
        return await self.think(
            AgentThinking,
            context="",
            user_message=(
                f"辩论议题：「{topic}」\n"
                f"当前第 {round_number}/{max_rounds} 轮\n"
                f"活跃立场：{pos_names}\n\n"
                f"【你的初始分析】\n{original_thinking}\n\n"
                f"【数据研究员刚获取的新数据】\n{fetched_data_summary}\n\n"
                f"本轮辩论内容：\n{discussion_summary}\n\n"
                "数据研究员为你获取了新数据。请重新分析：\n"
                "1. 新数据是否改变了你对各方论点强度的评估？\n"
                "2. 是否有辩手引用了与这些新数据矛盾的说法？\n"
                "3. 你的判断（继续/结束）是否需要调整？\n"
                "聚焦于新数据带来的影响，不必重复已有分析。\n\n"
                '输出JSON：{"thinking": "你的重新分析", "data_need": ""}'
            ),
        )
