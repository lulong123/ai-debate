"""Discussion orchestrator: manages the full debate lifecycle."""

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.data_clerk import DataClerkAgent
from app.agents.moderator import ModeratorAgent
from app.agents.perspective import PerspectiveAgent
from app.agents.scorer import ScorerAgent
from app.models import MessageRole, SessionStatus
from app.models.schemas import RoundJudgment
from app.routers.sse import publish
from app.services.search import get_search_provider
from app.storage.repository import SessionRepository

logger = logging.getLogger(__name__)

MAX_POOL_DISPLAY = 20  # Max results to inject into agent prompts


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
                scope = await self.moderator.establish_data_scope(session.topic, positions_data)
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
                    context = self._build_context(session.topic, all_debate_messages, round_messages, round_num)
                    user_msg = (
                        f"第 {round_num} 轮辩论。\n"
                        f"你是「{agent.position_name}」方的辩手。\n"
                        f"请发言：先指出对手论证中的具体漏洞或错误，再提出一个你方尚未提出的新论点。\n"
                        f"注意：不要重复上下文中已经出现过的论点。"
                    )

                    # Data clerk search phase (conservative: data clerk decides if search is needed)
                    if data_clerk is not None:
                        data_msg_id = f"data_{agent.position_id}_{round_num}"
                        truncated_context = context[:1500]

                        # Build summary of existing pool so data clerk avoids re-searching
                        pool_summary = self._format_data_pool_summary(public_data_pool) if public_data_pool else ""

                        await self._emit(session_id, {
                            "type": "data_fetch_start",
                            "message_id": data_msg_id,
                            "agent": agent.position_id,
                            "agent_name": agent.position_name,
                            "round": round_num,
                        })

                        raw_results = await data_clerk.fetch_for_agent(
                            session.topic, truncated_context,
                            agent.position_name, round_num, self.search,
                            existing_pool_summary=pool_summary,
                            data_scope=data_scope_text,
                        )

                        # Verify results against data scope and existing pool
                        if raw_results and data_scope_text:
                            results = await data_clerk.verify_results(
                                raw_results, session.topic, data_scope_text,
                                existing_pool_summary=pool_summary,
                            )
                        else:
                            results = raw_results

                        # Persist verified results to DB
                        for r in results:
                            try:
                                await self.repo.add_data_pool_item(
                                    session_id=session_id,
                                    source="data_clerk",
                                    title=r.get("title", ""),
                                    snippet=r.get("snippet", ""),
                                    url=r.get("url", ""),
                                    round_number=round_num,
                                )
                            except Exception:
                                pass  # Non-blocking persistence

                        public_data_pool.extend(results)

                        await self._emit(session_id, {
                            "type": "data_fetch_complete",
                            "message_id": data_msg_id,
                            "agent": agent.position_id,
                            "agent_name": agent.position_name,
                            "results": results,
                            "round": round_num,
                        })

                        # Inject data scope + data pool into the agent's prompt
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

                # Build scorer prompt, optionally with data pool
                score_result = await self._score_with_data(
                    session.topic, round_messages, positions_data,
                    public_data_pool if data_clerk else None
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

                judgment = await self._judge_with_data(
                    session.topic, round_num, session.max_rounds,
                    summary, positions_data,
                    public_data_pool if data_clerk else None
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
            minutes = await self.moderator.generate_minutes(
                session.topic, positions_data,
                [{"agent_name": m.agent_name, "role": m.role, "content": m.content} for m in all_messages]
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

    def _build_context(self, topic: str, all_messages: list[dict], round_messages: list[dict], current_round: int) -> str:
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

    async def _score_with_data(
        self, topic: str, round_messages: list[dict],
        positions_data: list[dict], data_pool: list[dict] | None
    ) -> dict:
        """Score round, optionally injecting data pool context."""
        if data_pool:
            # Append data pool to scorer context via topic
            pool_text = self._format_data_pool(data_pool)
            # Scorer gets the pool as extra context in the topic string
            enriched_topic = f"{topic}\n\n【公开数据池】\n{pool_text}"
            return await self.scorer.score_round(enriched_topic, round_messages, positions_data)
        return await self.scorer.score_round(topic, round_messages, positions_data)

    async def _judge_with_data(
        self, topic: str, round_num: int, max_rounds: int,
        summary: str, positions_data: list[dict],
        data_pool: list[dict] | None
    ) -> RoundJudgment:
        """Judge round, optionally injecting data pool context."""
        if data_pool:
            pool_text = self._format_data_pool(data_pool)
            enriched_summary = f"{summary}\n\n【公开数据池】\n{pool_text}"
            return await self.moderator.judge_round(
                topic, round_num, max_rounds, enriched_summary, positions_data
            )
        return await self.moderator.judge_round(
            topic, round_num, max_rounds, summary, positions_data
        )
