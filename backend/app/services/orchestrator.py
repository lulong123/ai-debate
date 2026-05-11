"""Discussion orchestrator: manages the full debate lifecycle."""

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.data_clerk import DataClerkAgent
from app.agents.moderator import ModeratorAgent
from app.agents.perspective import PerspectiveAgent
from app.agents.scorer import ScorerAgent
from app.config import settings
from app.models import MessageRole, SessionStatus
from app.models.schemas import AgentThinking, RoundJudgment
from app.routers.sse import publish
from app.services.search import get_search_provider
from app.storage.repository import SessionRepository

logger = logging.getLogger(__name__)

MAX_POOL_DISPLAY = 20  # Max results to inject into agent prompts

STRATEGY_GUIDANCE = {
    "ATTACK": "你的策略是进攻。找出对手论点的具体漏洞，集中攻击。",
    "DEFEND": "你的策略是防守。强化你被挑战的论点，提供更多支撑。",
    "REDIRECT": "你的策略是转向。换一个全新的角度来论证你的立场。",
    "EVIDENCE": "你的策略是引证。引入新的数据或事实来支撑你的论点。",
}


class Orchestrator:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = SessionRepository(db)
        self.moderator = ModeratorAgent()
        self.scorer = ScorerAgent()
        self.search = get_search_provider()
        self._seq = 0

    async def _init_seq(self, session_id: str):
        """Initialize seq counter from DB to handle restarts."""
        messages = await self.repo.get_messages(session_id)
        if messages:
            self._seq = max(m.seq for m in messages)

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def _emit(self, session_id: str, event: dict):
        """Publish SSE event."""
        await publish(session_id, event)

    async def start_discussion(
        self, session_id: str, selected_position_ids: list[str],
        custom_positions: list[dict] | None = None,
        enable_data_clerk: bool = False,
    ):
        """Run the full debate flow for a session."""
        session = await self.repo.get_session(session_id)
        if not session:
            logger.error("Session %s not found", session_id)
            return

        try:
            await self._init_seq(session_id)

            # Get positions
            all_positions = await self.repo.get_active_positions(session_id)

            # Filter to selected positions
            active_positions = [p for p in all_positions if p.id in selected_position_ids]

            # Add custom positions
            if custom_positions:
                for cp in custom_positions:
                    position = await self.repo.add_position(
                        session_id=session_id,
                        name=cp["name"],
                        description=cp["description"],
                        is_custom=True,
                    )
                    active_positions.append(position)

            # Update status and data clerk flag
            session.status = SessionStatus.DISCUSSING
            session.has_data_clerk = enable_data_clerk
            await self.repo.update_session(session)

            positions_data = [
                {"id": p.id, "name": p.name, "description": p.description}
                for p in active_positions
            ]

            # Create data clerk if enabled
            data_clerk = DataClerkAgent() if enable_data_clerk else None

            # 0.5 Establish data scope (if data clerk is enabled)
            data_scope_text = ""
            if data_clerk is not None:
                # Search topic facts first so scope is based on real data, not training data
                scope_data_context = ""
                try:
                    scope_results = await data_clerk.research_topic(session.topic, self.search)
                    if scope_results:
                        lines = [
                            f"- {r.get('title', '')}：{r.get('snippet', '')}"
                            for r in scope_results
                        ]
                        scope_data_context = "\n".join(lines)
                        logger.info(
                            "Scope search got %d results for session %s",
                            len(scope_results), session_id,
                        )
                except Exception as e:
                    logger.warning("Scope search failed for session %s: %s", session_id, e)

                scope = await self.moderator.establish_data_scope(
                    session.topic, positions_data, data_context=scope_data_context
                )
                data_scope_text = (
                    f"事件：{scope.specific_event}\n"
                    f"时间范围：{scope.time_range}\n"
                    f"关键实体：{', '.join(scope.key_entities)}\n"
                    f"相关性规则：{scope.relevance_rule}"
                )
                logger.info("Data scope for session %s: %s", session_id, scope.scope_summary)

            # 1. Opening remarks
            opening = await self.moderator.generate_opening(session.topic, positions_data)
            await self.repo.add_message(
                session_id=session_id,
                seq=self._next_seq(),
                role=MessageRole.MODERATOR,
                agent_name="主持人",
                content=opening,
                round_number=0,
            )
            await self._emit(session_id, {
                "type": "discussion_start",
                "opening": opening,
                "positions": positions_data,
            })

            # Create perspective agents
            agents = [
                PerspectiveAgent(p.id, p.name, p.description)
                for p in active_positions
            ]

            # Track all scores
            all_scores = []

            # Track all debate messages across rounds for context
            all_debate_messages = []

            # Public data pool: shared across all agents, accumulates across rounds
            # Initialize from preliminary_data (pre-debate search) + DB-persisted items
            public_data_pool: list[dict] = []
            if session.preliminary_data:
                public_data_pool.extend(session.preliminary_data)
            existing_pool = await self.repo.get_data_pool(session_id)
            for item in existing_pool:
                public_data_pool.append(item.to_dict())

            # 2. Debate rounds
            for round_num in range(1, session.max_rounds + 1):
                session.current_round = round_num
                await self.repo.update_session(session)

                # Re-read data pool from DB to pick up user contributions
                db_pool = await self.repo.get_data_pool(session_id)
                if db_pool:
                    db_dicts = [item.to_dict() for item in db_pool]
                    # Merge: keep preliminary_data + DB items, avoiding duplicates
                    existing_ids = {d.get("id") for d in public_data_pool if d.get("id")}
                    for d in db_dicts:
                        if d.get("id") not in existing_ids:
                            public_data_pool.append(d)

                await self._emit(session_id, {
                    "type": "round_start",
                    "round": round_num,
                    "max_rounds": session.max_rounds,
                })

                round_messages = []

                # Each agent speaks sequentially
                for agent in agents:
                    # Build context from ALL previous messages (not just this round)
                    context = self._build_context(
                        session.topic, all_debate_messages,
                        round_messages, round_num,
                    )
                    user_msg = (
                        f"第 {round_num} 轮辩论。\n"
                        f"你是「{agent.position_name}」方的辩手。\n"
                        f"请发言：先指出对手论证中的具体漏洞或错误，再提出一个你方尚未提出的新论点。\n"
                        f"注意：不要重复上下文中已经出现过的论点。"
                    )

                    # Two-pass thinking: debater analyzes before speaking
                    thinking_text = ""
                    agent_data_need = ""
                    strategy = ""
                    if settings.enable_cot:
                        try:
                            think_result = await agent.think_before_speaking(
                                context, round_num,
                            )
                            thinking_text = think_result.thinking
                            agent_data_need = think_result.data_need

                            # Fallback: extract semantic need from thinking text
                            # when data_need is empty but thinking mentions data
                            if (
                                data_clerk is not None
                                and not agent_data_need
                                and thinking_text
                                and self._thinking_mentions_data(thinking_text)
                            ):
                                agent_data_need = await self._extract_semantic_need(
                                    session.topic, thinking_text,
                                )
                                if agent_data_need:
                                    logger.info(
                                        "Extracted semantic need from thinking "
                                        "for %s round %d: %s",
                                        agent.position_name, round_num,
                                        agent_data_need[:80],
                                    )

                            await self._emit(session_id, {
                                "type": "agent_thinking",
                                "agent": agent.position_id,
                                "agent_name": agent.position_name,
                                "thinking": thinking_text,
                                "round": round_num,
                            })

                            # Update debater state from thinking result
                            agent.state.update(think_result)

                            # Extract and validate strategy
                            raw_strategy = (
                                think_result.chosen_strategy.upper().strip()
                            )
                            if raw_strategy and raw_strategy not in STRATEGY_GUIDANCE:
                                logger.warning(
                                    "Unknown strategy '%s' from %s, ignoring",
                                    raw_strategy, agent.position_name,
                                )
                                raw_strategy = ""
                            strategy = raw_strategy
                        except Exception as e:
                            logger.warning(
                                "Debater thinking failed for %s: %s",
                                agent.position_name, e,
                            )

                    # ReAct: thinking-driven data fetch
                    # Agent expresses data need via semantic natural language
                    fetched_results: list[dict] = []
                    if data_clerk is not None and agent_data_need:
                        fetched_results = await self._fetch_and_persist_data(
                            session_id=session_id,
                            agent_id=agent.position_id,
                            agent_name=agent.position_name,
                            round_num=round_num,
                            topic=session.topic,
                            agent_context=context[:1500],
                            data_need=agent_data_need,
                            data_clerk=data_clerk,
                            public_data_pool=public_data_pool,
                            data_scope_text=data_scope_text,
                        )

                        # Re-think: second thinking pass with new data
                        if fetched_results and settings.enable_cot:
                            try:
                                fetched_summary = self._format_data_pool(fetched_results)
                                re_think = await agent.re_think_with_data(
                                    context, round_num,
                                    fetched_summary, thinking_text,
                                )
                                thinking_text = (
                                    f"{thinking_text}\n\n"
                                    f"【数据获取后的重新分析】\n{re_think.thinking}"
                                )
                                await self._emit(session_id, {
                                    "type": "agent_thinking",
                                    "phase": "rethink",
                                    "agent": agent.position_id,
                                    "agent_name": agent.position_name,
                                    "thinking": re_think.thinking,
                                    "round": round_num,
                                })
                            except Exception as e:
                                logger.warning(
                                    "Debater re-think failed for %s: %s",
                                    agent.position_name, e,
                                )

                    # Inject data scope + data pool into the agent's prompt
                    if data_clerk is not None:
                        pool_text = self._format_data_pool(public_data_pool)
                        scope_injection = ""
                        if data_scope_text:
                            scope_injection = (
                                f"\n\n【数据边界】（只使用以下范围内的数据）\n"
                                f"{data_scope_text}\n"
                                "如果数据池中有超出此边界的数据，不要引用。"
                            )
                        user_msg += (
                            f"{scope_injection}\n\n"
                            f"【公开数据池】（所有辩手、主持人、评委共享）\n"
                            f"{pool_text}\n"
                            "引用数据时使用编号标记，如 [1]、[2]。"
                            "如果数据不足，指出你需要什么数据。"
                        )

                    # Stream agent response
                    # Inject thinking context if available
                    if thinking_text:
                        user_msg = (
                            f"【你的思考分析】\n{thinking_text}\n\n"
                            f"基于以上分析，现在发言：\n{user_msg}"
                        )
                    # Inject strategy guidance if available
                    if strategy and strategy in STRATEGY_GUIDANCE:
                        guidance = STRATEGY_GUIDANCE[strategy]
                        user_msg = (
                            f"【策略指导】{guidance}"
                            "（如果策略与发言规则冲突，策略优先）\n\n"
                            f"{user_msg}"
                        )
                    full_response = []
                    msg_id = f"msg_{session_id}_{self._seq + 1}"
                    await self._emit(session_id, {
                        "type": "agent_message_start",
                        "agent": agent.position_id,
                        "agent_name": agent.position_name,
                        "round": round_num,
                        "message_id": msg_id,
                    })

                    async for token in agent.stream(context, user_msg):
                        full_response.append(token)
                        await self._emit(session_id, {
                            "type": "agent_message_chunk",
                            "agent": agent.position_id,
                            "agent_name": agent.position_name,
                            "chunk": token,
                            "round": round_num,
                            "message_id": msg_id,
                        })

                    response_text = "".join(full_response)

                    # Persist message
                    await self.repo.add_message(
                        session_id=session_id,
                        seq=self._next_seq(),
                        role=MessageRole.PERSPECTIVE,
                        agent_name=agent.position_name,
                        position_id=agent.position_id,
                        content=response_text,
                        round_number=round_num,
                    )

                    round_messages.append({
                        "agent_name": agent.position_name,
                        "position_id": agent.position_id,
                        "content": response_text,
                    })

                    await self._emit(session_id, {
                        "type": "agent_message_complete",
                        "agent": agent.position_id,
                        "agent_name": agent.position_name,
                        "content": response_text,
                        "round": round_num,
                        "message_id": msg_id,
                    })

                # 3. Score this round
                all_debate_messages.extend(round_messages)

                # Two-pass thinking: scorer analyzes before scoring
                scorer_thinking_text = ""
                if settings.enable_cot:
                    try:
                        scorer_think = await self.scorer.think_before_scoring(
                            session.topic, round_messages, positions_data,
                        )
                        scorer_thinking_text = scorer_think.thinking
                        await self._emit(session_id, {
                            "type": "agent_thinking",
                            "agent": "scorer",
                            "agent_name": "评委",
                            "thinking": scorer_thinking_text,
                            "round": round_num,
                        })
                    except Exception as e:
                        logger.warning("Scorer thinking failed: %s", e)

                # Build scorer prompt, optionally with data pool and thinking
                score_result = await self._score_with_data(
                    session.topic, round_messages, positions_data,
                    public_data_pool if data_clerk else None,
                    scorer_thinking_text,
                )
                scores = score_result.get("scores", [])
                all_scores.extend(scores)

                await self._emit(session_id, {
                    "type": "score_update",
                    "scores": scores,
                    "round": round_num,
                })

                # 4. Moderator judges round
                summary = "\n".join(
                    f"{m['agent_name']}：{m['content'][:200]}"
                    for m in round_messages
                )

                # Two-pass thinking: moderator analyzes before judging
                judge_thinking_text = ""
                moderator_data_need = ""
                if settings.enable_cot:
                    try:
                        judge_think = await self.moderator.think_before_judging(
                            session.topic, round_num, session.max_rounds,
                            summary, positions_data,
                        )
                        judge_thinking_text = judge_think.thinking
                        moderator_data_need = judge_think.data_need

                        # Fallback: extract semantic need from thinking text
                        # when data_need is empty but thinking mentions data
                        if (
                            data_clerk is not None
                            and not moderator_data_need
                            and judge_thinking_text
                            and self._thinking_mentions_data(judge_thinking_text)
                        ):
                            moderator_data_need = await self._extract_semantic_need(
                                session.topic, judge_thinking_text,
                            )
                            if moderator_data_need:
                                logger.info(
                                    "Extracted semantic need from moderator "
                                    "thinking round %d: %s",
                                    round_num, moderator_data_need[:80],
                                )

                        await self._emit(session_id, {
                            "type": "agent_thinking",
                            "agent": "moderator",
                            "agent_name": "主持人",
                            "thinking": judge_thinking_text,
                            "round": round_num,
                        })
                    except Exception as e:
                        logger.warning("Moderator judge thinking failed: %s", e)

                # Moderator data fetch + re-think
                if (
                    data_clerk is not None
                    and moderator_data_need
                    and settings.enable_cot
                ):
                    mod_results = await self._fetch_and_persist_data(
                        session_id=session_id,
                        agent_id="moderator",
                        agent_name="主持人",
                        round_num=round_num,
                        topic=session.topic,
                        agent_context=summary[:1500],
                        data_need=moderator_data_need,
                        data_clerk=data_clerk,
                        public_data_pool=public_data_pool,
                        data_scope_text=data_scope_text,
                    )
                    if mod_results:
                        try:
                            fetched_summary = self._format_data_pool(mod_results)
                            re_think = await self.moderator.re_think_with_data(
                                session.topic, round_num, session.max_rounds,
                                summary, positions_data,
                                fetched_summary, judge_thinking_text,
                            )
                            judge_thinking_text = (
                                f"{judge_thinking_text}\n\n"
                                f"【数据获取后的重新分析】\n{re_think.thinking}"
                            )
                            await self._emit(session_id, {
                                "type": "agent_thinking",
                                "phase": "rethink",
                                "agent": "moderator",
                                "agent_name": "主持人",
                                "thinking": re_think.thinking,
                                "round": round_num,
                            })
                        except Exception as e:
                            logger.warning("Moderator re-think failed: %s", e)

                judgment = await self._judge_with_data(
                    session.topic, round_num, session.max_rounds,
                    summary, positions_data,
                    public_data_pool if data_clerk else None,
                    judge_thinking_text,
                )

                # Emit guidance
                if judgment.guidance:
                    await self.repo.add_message(
                        session_id=session_id,
                        seq=self._next_seq(),
                        role=MessageRole.MODERATOR,
                        agent_name="主持人",
                        content=judgment.guidance,
                        round_number=round_num,
                    )
                    await self._emit(session_id, {
                        "type": "moderator_guidance",
                        "content": judgment.guidance,
                        "round": round_num,
                    })

                await self._emit(session_id, {
                    "type": "round_complete",
                    "round": round_num,
                    "decision": judgment.decision,
                    "reason": judgment.reason,
                })

                if judgment.decision == "CONCLUDE":
                    break

            # 5. Generate debate verdict
            all_messages = await self.repo.get_messages(session_id)

            # Two-pass thinking: moderator analyzes before generating minutes
            minutes_thinking_text = ""
            if settings.enable_cot:
                try:
                    minutes_think = await self.moderator.think_before_minutes(
                        session.topic, positions_data,
                        [
                            {"agent_name": m.agent_name, "role": m.role, "content": m.content}
                            for m in all_messages
                        ],
                    )
                    minutes_thinking_text = minutes_think.thinking
                    await self._emit(session_id, {
                        "type": "agent_thinking",
                        "agent": "moderator",
                        "agent_name": "主持人",
                        "thinking": minutes_thinking_text,
                        "round": 0,
                    })
                except Exception as e:
                    logger.warning("Moderator minutes thinking failed: %s", e)

            minutes = await self.moderator.generate_minutes(
                session.topic, positions_data,
                [
                    {"agent_name": m.agent_name, "role": m.role, "content": m.content}
                    for m in all_messages
                ],
                thinking_text=minutes_thinking_text,
            )
            minutes["all_scores"] = all_scores

            session.minutes = minutes
            session.status = SessionStatus.COMPLETED
            session.completed_at = datetime.now(timezone.utc)
            await self.repo.update_session(session)

            await self._emit(session_id, {
                "type": "discussion_end",
                "minutes": minutes,
            })

        except Exception as e:
            logger.exception("Debate failed for session %s", session_id)
            session = await self.repo.get_session(session_id)
            if session:
                session.status = SessionStatus.FAILED
                await self.repo.update_session(session)
            user_message = "辩论过程中发生错误"
            if "timeout" in str(e).lower():
                user_message = "AI 服务响应超时，请稍后重试"
            elif "auth" in str(e).lower() or "key" in str(e).lower():
                user_message = "AI 服务认证失败，请检查 API Key 配置"
            elif "rate" in str(e).lower():
                user_message = "AI 服务请求频率超限，请稍后重试"
            await self._emit(session_id, {
                "type": "error",
                "message": user_message,
            })

    def _build_context(
        self, topic: str, all_messages: list[dict],
        round_messages: list[dict], current_round: int,
    ) -> str:
        """Build debate context for an agent, including full history."""
        lines = [f"辩论议题：{topic}"]

        if all_messages:
            lines.append("\n【之前轮次的历史发言】（你的新论点不能与这些重复）")
            for msg in all_messages:
                lines.append(f"- {msg['agent_name']}方：{msg['content'][:300]}")

        if round_messages:
            lines.append("\n【本轮已经发言的辩手】（引用他们的话来反驳）")
            for msg in round_messages:
                lines.append(f"- {msg['agent_name']}方：{msg['content'][:300]}")

        return "\n".join(lines)

    def _format_data_pool(self, pool: list[dict]) -> str:
        """Format the public data pool with [N] citation markers. Truncate to recent results."""
        display = pool[-MAX_POOL_DISPLAY:]
        lines = []
        for i, r in enumerate(display, 1):
            title = r.get('title', '')
            snippet = r.get('snippet', '')
            url = r.get('url', '')
            lines.append(f"[{i}] {title}\n    {snippet}")
            if url:
                lines.append(f"    来源: {url}")
        return "\n".join(lines)

    def _format_data_pool_summary(self, pool: list[dict]) -> str:
        """Short summary of existing pool for data clerk (to avoid re-searching)."""
        display = pool[-MAX_POOL_DISPLAY:]
        lines = []
        for i, r in enumerate(display, 1):
            lines.append(f"[{i}] {r.get('title', '')} - {r.get('snippet', '')[:80]}")
        return "\n".join(lines)

    async def _fetch_and_persist_data(
        self,
        session_id: str,
        agent_id: str,
        agent_name: str,
        round_num: int,
        topic: str,
        agent_context: str,
        data_need: str,
        data_clerk: DataClerkAgent,
        public_data_pool: list[dict],
        data_scope_text: str = "",
    ) -> list[dict]:
        """Fetch data via data clerk's semantic intent protocol, persist to DB, emit SSE events."""
        data_msg_id = f"data_{agent_id}_{round_num}"
        pool_summary = (
            self._format_data_pool_summary(public_data_pool)
            if public_data_pool else ""
        )

        await self._emit(session_id, {
            "type": "data_fetch_start",
            "message_id": data_msg_id,
            "agent": agent_id,
            "agent_name": agent_name,
            "round": round_num,
            "data_need": data_need,
        })

        on_search = self._make_search_callback(
            session_id, agent_name, round_num,
        )

        # Semantic intent protocol: pool check → decompose → search → sufficiency
        results, validation = await data_clerk.research_for_agent(
            topic, data_need, self.search,
            pool_summary=pool_summary,
            data_scope=data_scope_text,
            on_search=on_search,
            on_progress=lambda evt: self._emit(session_id, {
                **evt,
                "agent_name": agent_name,
                "phase": "debate",
            }),
        )

        # Emit cross-validation result
        if validation.validated or validation.unique:
            await self._emit(session_id, {
                "type": "cross_validation_result",
                "validated": validation.validated,
                "unique": validation.unique,
                "contradictions": validation.contradictions,
                "note": validation.note,
            })

        # Persist verified results to DB (with extracted key_facts)
        for r in results:
            try:
                await self.repo.add_data_pool_item(
                    session_id=session_id,
                    source="data_clerk",
                    title=r.get("title", ""),
                    snippet=r.get("snippet", ""),
                    url=r.get("url", ""),
                    round_number=round_num,
                    key_facts=r.get("key_facts"),
                )
            except Exception:
                pass

        public_data_pool.extend(results)

        await self._emit(session_id, {
            "type": "data_fetch_complete",
            "message_id": data_msg_id,
            "agent": agent_id,
            "agent_name": agent_name,
            "results": results,
            "round": round_num,
        })

        return results

    async def _score_with_data(
        self, topic: str, round_messages: list[dict],
        positions_data: list[dict], data_pool: list[dict] | None,
        thinking_text: str = "",
    ) -> dict:
        """Score round, optionally injecting data pool and thinking context."""
        enriched_topic = topic
        if data_pool:
            pool_text = self._format_data_pool(data_pool)
            enriched_topic = f"{topic}\n\n【公开数据池】\n{pool_text}"
        if thinking_text:
            enriched_topic = (
                f"{enriched_topic}\n\n【评委分析】\n{thinking_text}"
            )
        return await self.scorer.score_round(enriched_topic, round_messages, positions_data)

    async def _judge_with_data(
        self, topic: str, round_num: int, max_rounds: int,
        summary: str, positions_data: list[dict],
        data_pool: list[dict] | None,
        thinking_text: str = "",
    ) -> RoundJudgment:
        """Judge round, optionally injecting data pool and thinking context."""
        enriched_summary = summary
        if data_pool:
            pool_text = self._format_data_pool(data_pool)
            enriched_summary = f"{summary}\n\n【公开数据池】\n{pool_text}"
        if thinking_text:
            enriched_summary = (
                f"【主持人分析】\n{thinking_text}\n\n{enriched_summary}"
            )
        return await self.moderator.judge_round(
            topic, round_num, max_rounds, enriched_summary, positions_data
        )

    @staticmethod
    def _format_thinking(think_result: AgentThinking) -> str:
        """Format agent thinking for SSE display and prompt injection.

        With the new free-form schema, thinking is already a single text field.
        """
        return think_result.thinking

    @staticmethod
    def _thinking_mentions_data(thinking: str) -> bool:
        """Check if thinking text mentions needing data."""
        keywords = [
            "需要数据", "需要.*数据", "缺少数据", "数据不足",
            "需要.*统计", "需要.*证据", "需要.*搜索",
            "没有.*数据", "缺少.*数据", "缺乏.*数据",
            "需要查", "需要搜", "需要找",
            "具体.*数据", "详细.*数据", "准确.*数据",
        ]
        import re
        for kw in keywords:
            if re.search(kw, thinking):
                return True
        return False

    async def _extract_semantic_need(
        self, topic: str, thinking_text: str,
    ) -> str:
        """Extract a one-sentence semantic data need from thinking text.

        Fallback when data_need is empty but _thinking_mentions_data() matches.
        Returns empty string on failure.
        """
        from app.services.llm import complete

        try:
            result = await complete([
                {
                    "role": "system",
                    "content": (
                        "你是一个数据需求提取器。根据辩手/主持人的思考文本，"
                        "用一句话总结他们需要什么具体数据。"
                        "只返回一句话，不要解释。"
                        "如果思考中没有明确需要数据的内容，返回空字符串。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"辩论议题：{topic}\n\n"
                        f"思考内容：\n{thinking_text[:500]}\n\n"
                        "用一句话总结：具体需要什么数据？"
                    ),
                },
            ])
            need = result.strip()
            return need if need else ""
        except Exception as e:
            logger.warning("Semantic need extraction failed: %s", e)
            return ""

    def _make_search_callback(self, session_id: str, agent_name: str, round_num: int):
        """Create an on_search callback that emits SSE events for queries and results."""
        async def _on_search(queries: list[str], results: list[dict]):
            await self._emit(session_id, {
                "type": "search_queries",
                "phase": "debate",
                "agent_name": agent_name,
                "round": round_num,
                "queries": queries,
            })
            if results:
                await self._emit(session_id, {
                    "type": "search_results",
                    "phase": "debate",
                    "agent_name": agent_name,
                    "round": round_num,
                    "results": [
                        {"title": r.get("title", ""), "snippet": r.get("snippet", ""), "url": r.get("url", "")}
                        for r in results
                    ],
                })
        return _on_search
