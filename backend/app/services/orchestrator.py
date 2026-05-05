"""Discussion orchestrator: manages the full discussion lifecycle."""

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.moderator import ModeratorAgent
from app.agents.perspective import PerspectiveAgent
from app.agents.scorer import ScorerAgent
from app.models import MessageRole, SessionStatus
from app.routers.sse import publish
from app.services.search import get_search_provider
from app.storage.repository import SessionRepository

logger = logging.getLogger(__name__)


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
        """Publish SSE event and persist as needed."""
        await publish(session_id, event)

    async def start_discussion(self, session_id: str, selected_angle_ids: list[str], custom_angles: list[dict] | None = None):
        """Run the full discussion flow for a session."""
        session = await self.repo.get_session(session_id)
        if not session:
            logger.error("Session %s not found", session_id)
            return

        try:
            await self._init_seq(session_id)

            # Get angles
            all_angles = await self.repo.get_active_angles(session_id)

            # Filter to selected angles
            active_angles = [a for a in all_angles if a.id in selected_angle_ids]

            # Add custom angles
            if custom_angles:
                for ca in custom_angles:
                    angle = await self.repo.add_angle(
                        session_id=session_id,
                        name=ca["name"],
                        description=ca["description"],
                        is_custom=True,
                    )
                    active_angles.append(angle)

            # Update status
            session.status = SessionStatus.DISCUSSING
            await self.repo.update_session(session)

            angles_data = [
                {"id": a.id, "name": a.name, "description": a.description}
                for a in active_angles
            ]

            # 1. Opening remarks
            opening = await self.moderator.generate_opening(session.topic, angles_data)
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
                "angles": angles_data,
            })

            # Create perspective agents
            agents = [
                PerspectiveAgent(a.id, a.name, a.description)
                for a in active_angles
            ]

            # Track messages per round for scoring
            all_scores = []

            # 2. Discussion rounds
            for round_num in range(1, session.max_rounds + 1):
                session.current_round = round_num
                await self.repo.update_session(session)

                await self._emit(session_id, {
                    "type": "round_start",
                    "round": round_num,
                    "max_rounds": session.max_rounds,
                })

                round_messages = []

                # Each agent speaks sequentially
                for agent in agents:
                    if agent.conceded:
                        continue

                    # Build context from previous messages
                    context = self._build_context(session.topic, round_messages, round_num)
                    user_msg = (
                        f"第 {round_num} 轮讨论。\n"
                        f"请从「{agent.angle_name}」角度发言。"
                    )

                    # Optional search phase
                    search_results = await self._maybe_search(session.topic, agent, round_num)

                    if search_results:
                        user_msg += f"\n\n搜索结果参考：\n{self._format_search_results(search_results)}"

                    # Stream agent response
                    full_response = []
                    msg_id = f"msg_{session_id}_{self._seq + 1}"
                    await self._emit(session_id, {
                        "type": "agent_message_start",
                        "agent": agent.angle_id,
                        "agent_name": agent.angle_name,
                        "round": round_num,
                        "message_id": msg_id,
                    })

                    async for token in agent.stream(context, user_msg):
                        full_response.append(token)
                        await self._emit(session_id, {
                            "type": "agent_message_chunk",
                            "agent": agent.angle_id,
                            "agent_name": agent.angle_name,
                            "chunk": token,
                            "round": round_num,
                            "message_id": msg_id,
                        })

                    response_text = "".join(full_response)

                    # Check concession
                    conceded = agent.check_concede(response_text)
                    if conceded:
                        agent.conceded = True
                        # Mark angle as conceded in DB
                        for a in active_angles:
                            if a.id == agent.angle_id:
                                a.conceded = True
                                await self.repo.update_session(session)

                    if conceded:
                        response_text = response_text.replace("[CONCEDE]", "").strip()

                    # Persist message
                    await self.repo.add_message(
                        session_id=session_id,
                        seq=self._next_seq(),
                        role=MessageRole.PERSPECTIVE,
                        agent_name=agent.angle_name,
                        angle_id=agent.angle_id,
                        content=response_text,
                        round_number=round_num,
                    )

                    round_messages.append({
                        "agent_name": agent.angle_name,
                        "angle_id": agent.angle_id,
                        "content": response_text,
                    })

                    await self._emit(session_id, {
                        "type": "agent_message_complete",
                        "agent": agent.angle_id,
                        "agent_name": agent.angle_name,
                        "content": response_text,
                        "round": round_num,
                        "message_id": msg_id,
                        "conceded": conceded,
                    })

                # 3. Score this round
                active_for_scoring = [
                    {"id": a.id, "name": a.name, "description": a.description}
                    for a in active_angles if not a.conceded
                ]

                if active_for_scoring:
                    score_result = await self.scorer.score_round(
                        session.topic, round_messages, active_for_scoring
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
                still_active = [a for a in active_angles if not a.conceded]

                if len(still_active) < 2:
                    await self._emit(session_id, {
                        "type": "round_complete",
                        "round": round_num,
                        "reason": "活跃角度不足 2 个，讨论自动结束",
                    })
                    break

                judgment = await self.moderator.judge_round(
                    session.topic, round_num, session.max_rounds,
                    summary, [{"name": a.name, "description": a.description} for a in still_active]
                )

                # Emit guidance
                if judgment.get("guidance"):
                    await self.repo.add_message(
                        session_id=session_id,
                        seq=self._next_seq(),
                        role=MessageRole.MODERATOR,
                        agent_name="主持人",
                        content=judgment["guidance"],
                        round_number=round_num,
                    )
                    await self._emit(session_id, {
                        "type": "moderator_guidance",
                        "content": judgment["guidance"],
                        "round": round_num,
                    })

                await self._emit(session_id, {
                    "type": "round_complete",
                    "round": round_num,
                    "decision": judgment.get("decision", "CONTINUE"),
                    "reason": judgment.get("reason", ""),
                })

                if judgment.get("decision") == "CONCLUDE":
                    break

            # 5. Generate meeting minutes
            all_messages = await self.repo.get_messages(session_id)
            minutes = await self.moderator.generate_minutes(
                session.topic, angles_data,
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
            logger.exception("Discussion failed for session %s", session_id)
            session = await self.repo.get_session(session_id)
            if session:
                session.status = SessionStatus.FAILED
                await self.repo.update_session(session)
            # Sanitize error message - don't expose internal details
            user_message = "讨论过程中发生错误"
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

    async def _maybe_search(self, topic: str, agent: PerspectiveAgent, round_num: int) -> list:
        """Optional search phase for an agent."""
        try:
            results = await self.search.search(
                f"{topic} {agent.angle_name}",
                max_results=3,
            )
            return [r.to_dict() for r in results]
        except Exception:
            return []

    def _build_context(self, topic: str, round_messages: list[dict], current_round: int) -> str:
        """Build discussion context for an agent."""
        lines = [f"讨论议题：{topic}"]
        if round_messages:
            lines.append("\n本轮之前的发言：")
            for msg in round_messages:
                lines.append(f"- {msg['agent_name']}：{msg['content'][:300]}")
        return "\n".join(lines)

    def _format_search_results(self, results: list[dict]) -> str:
        """Format search results for inclusion in prompt."""
        lines = []
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.get('title', '')} - {r.get('snippet', '')}")
        return "\n".join(lines)
