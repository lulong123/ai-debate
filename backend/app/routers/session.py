import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.moderator import ModeratorAgent
from app.config import settings
from app.models import SessionStatus
from app.routers.sse import publish
from app.services.orchestrator import Orchestrator
from app.services.search import get_search_provider
from app.storage.database import async_session, get_db
from app.storage.repository import SessionRepository

logger = logging.getLogger(__name__)

# ── Data-request detection ──────────────────────────────────────────

import re

_DATA_NEED_PATTERNS = [
    "需要数据", "需要.*数据", "缺少数据", "数据不足",
    "需要.*统计", "需要.*证据", "需要.*搜索",
    "没有.*数据", "缺少.*数据", "缺乏.*数据",
    "需要查", "需要搜", "需要找",
    "具体.*数据", "详细.*数据", "准确.*数据",
]


def _thinking_mentions_data(thinking: str) -> bool:
    """Check if thinking text indicates a need for more data."""
    return any(re.search(p, thinking) for p in _DATA_NEED_PATTERNS)

router = APIRouter()
moderator = ModeratorAgent()

_active_sessions: set[str] = set()
_clarify_tasks: set[str] = set()
_suggest_tasks: set[str] = set()


async def _emit_search_events(
    session_id: str, phase: str, agent_name: str = "数据研究员",
):
    async def _on_search(queries: list[str], results: list[dict]):
        logger.info(
            "on_search callback fired for session %s phase=%s queries=%s results=%d",
            session_id, phase, queries, len(results),
        )
        await publish(session_id, {
            "type": "search_queries",
            "phase": phase,
            "agent_name": agent_name,
            "queries": queries,
        })
        if results:
            await publish(session_id, {
                "type": "search_results",
                "phase": phase,
                "agent_name": agent_name,
                "results": [
                    {"title": r.get("title", ""), "snippet": r.get("snippet", ""), "url": r.get("url", "")}
                    for r in results
                ],
            })
    return _on_search


class CreateSessionRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=500)
    max_rounds: int = Field(default=3, ge=1, le=10)


class ClarifyResponse(BaseModel):
    answer: str = Field(..., min_length=1, max_length=1000)


class SelectPositionsRequest(BaseModel):
    position_ids: list[str] = Field(..., min_length=2, max_length=6)
    custom_positions: list[dict] | None = None
    enable_data_clerk: bool = False


class AddDataRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1, max_length=2000)
    url: str = Field(default="", max_length=500)


@router.post("")
async def create_session(req: CreateSessionRequest, db: AsyncSession = Depends(get_db)):
    repo = SessionRepository(db)
    session = await repo.create_session(topic=req.topic, max_rounds=req.max_rounds)
    return {"session_id": session.id, "status": session.status}


@router.get("")
async def list_sessions(db: AsyncSession = Depends(get_db)):
    repo = SessionRepository(db)
    sessions = await repo.list_sessions()
    return [
        {
            "session_id": s.id, "topic": s.topic, "status": s.status,
            "current_round": s.current_round,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in sessions
    ]


@router.get("/{session_id}")
async def get_session(session_id: str, db: AsyncSession = Depends(get_db)):
    repo = SessionRepository(db)
    session = await repo.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session.id, "topic": session.topic,
        "refined_topic": session.refined_topic, "status": session.status,
        "current_round": session.current_round, "max_rounds": session.max_rounds,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "completed_at": session.completed_at.isoformat() if session.completed_at else None,
    }


@router.get("/{session_id}/messages")
async def get_messages(session_id: str, db: AsyncSession = Depends(get_db)):
    repo = SessionRepository(db)
    session = await repo.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = await repo.get_messages(session_id)
    return [
        {
            "id": m.id, "role": m.role, "agent_name": m.agent_name,
            "position_id": m.position_id, "round_number": m.round_number,
            "content": m.content, "scores": m.scores,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in messages
    ]


@router.get("/{session_id}/minutes")
async def get_minutes(session_id: str, db: AsyncSession = Depends(get_db)):
    repo = SessionRepository(db)
    session = await repo.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status != SessionStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Discussion not completed yet")
    return {"session_id": session.id, "minutes": session.minutes}


@router.post("/{session_id}/clarify")
async def clarify_topic(session_id: str, db: AsyncSession = Depends(get_db)):
    if session_id in _clarify_tasks:
        return JSONResponse(status_code=202, content={"status": "processing"})
    repo = SessionRepository(db)
    session = await repo.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    _clarify_tasks.add(session_id)
    asyncio.create_task(_run_clarify(session_id, session.topic))
    return JSONResponse(status_code=202, content={"status": "processing"})


async def _run_clarify(session_id: str, topic: str):
    try:
        async with async_session() as db:
            repo = SessionRepository(db)
            session = await repo.get_session(session_id)
            data_context = ""
            try:
                search_provider = get_search_provider()
                if search_provider:
                    await publish(session_id, {
                        "type": "analysis_progress", "step": "searching",
                        "message": "正在搜索相关数据...",
                    })
                    from app.agents.data_clerk import DataClerkAgent
                    data_clerk = DataClerkAgent()
                    on_search = await _emit_search_events(session_id, "clarify")

                    async def _on_progress(evt: dict):
                        await publish(session_id, {**evt, "phase": "clarify"})

                    results, validation = await data_clerk.research_with_validation(
                        topic, search_provider,
                        on_search=on_search,
                        on_progress=_on_progress,
                    )
                    if results:
                        if validation.validated or validation.unique:
                            await publish(session_id, {
                                "type": "cross_validation_result",
                                "validated": validation.validated,
                                "unique": validation.unique,
                                "contradictions": validation.contradictions,
                                "note": validation.note, "phase": "clarify",
                            })
                        lines = [f"- {r.get('title', '')}：{r.get('snippet', '')}" for r in results]
                        data_context = "\n".join(lines)
                        session.preliminary_data = results
                        await repo.update_session(session)
            except Exception as e:
                logger.warning("Clarify research failed: %s", e)

            if settings.enable_cot:
                try:
                    await publish(session_id, {
                        "type": "analysis_progress", "step": "thinking",
                        "message": "主持人正在分析议题...",
                    })
                    think_result = await moderator.think_before_clarifying(topic, data_context)
                    await publish(session_id, {
                        "type": "agent_thinking", "agent": "moderator",
                        "agent_name": "主持人", "thinking": think_result.thinking, "round": 0,
                    })

                    # ── Data-request loop: if thinking reveals data needs, search again ──
                    semantic_need = think_result.data_need
                    if not semantic_need and _thinking_mentions_data(think_result.thinking):
                        from app.services.llm import complete
                        try:
                            need_result = await complete([
                                {"role": "system", "content": "用一句话总结需要什么具体数据。只返回一句话。"},
                                {"role": "user", "content": (
                                    f"议题：{topic}\n思考：{think_result.thinking[:500]}\n\n"
                                    "用一句话总结：具体需要什么数据？"
                                )},
                            ])
                            semantic_need = need_result.strip()
                        except Exception:
                            pass
                    if semantic_need:
                        logger.info("Clarify data-request loop: %s", semantic_need[:80])
                        try:
                            sp = get_search_provider()
                            if sp:
                                from app.agents.data_clerk import DataClerkAgent
                                dc = DataClerkAgent()
                                pool_summary = (
                                    "\n".join(f"- {r.get('title', '')}：{r.get('snippet', '')}" for r in (session.preliminary_data or []))
                                    if session.preliminary_data else ""
                                )
                                extra_results, _ = await dc.research_for_agent(
                                    topic, semantic_need, sp,
                                    pool_summary=pool_summary,
                                )
                                if extra_results:
                                    extra_lines = [f"- {r.get('title', '')}：{r.get('snippet', '')}" for r in extra_results]
                                    data_context += "\n\n【补充搜索】\n" + "\n".join(extra_lines)
                                    session.preliminary_data = (session.preliminary_data or []) + extra_results
                                    await repo.update_session(session)
                                    await publish(session_id, {
                                        "type": "data_fetch_complete",
                                        "results": extra_results, "phase": "clarify_followup",
                                    })
                        except Exception as e:
                            logger.warning("Clarify follow-up search failed: %s", e)
                except Exception as e:
                    logger.warning("Moderator clarify thinking failed: %s", e)

            await publish(session_id, {
                "type": "analysis_progress", "step": "analyzing",
                "message": "正在生成分析结果...",
            })
            result = await moderator.clarify_topic(topic, data_context=data_context)
            if result.valid:
                session.refined_topic = topic
                await repo.update_session(session)
            await publish(session_id, {
                "type": "clarify_result", "message_id": f"clarify_{session_id}",
                "valid": result.valid, "reason": result.reason,
                "question": result.question, "suggestion": result.suggestion,
            })
    except Exception as e:
        logger.exception("Clarify failed for session %s", session_id)
        await publish(session_id, {
            "type": "error", "message_id": f"clarify_err_{session_id}",
            "source": "clarify", "message": "主题分析失败，请重试",
        })
    finally:
        _clarify_tasks.discard(session_id)


@router.post("/{session_id}/refine")
async def refine_topic(session_id: str, req: ClarifyResponse, db: AsyncSession = Depends(get_db)):
    repo = SessionRepository(db)
    session = await repo.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session.refined_topic = f"{session.topic}\n补充说明：{req.answer}"
    await repo.update_session(session)
    return {"session_id": session.id, "refined_topic": session.refined_topic}


@router.post("/{session_id}/suggest-positions")
async def suggest_positions(session_id: str, db: AsyncSession = Depends(get_db)):
    if session_id in _suggest_tasks:
        return JSONResponse(status_code=202, content={"status": "processing"})
    repo = SessionRepository(db)
    session = await repo.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    topic = session.refined_topic or session.topic
    _suggest_tasks.add(session_id)
    asyncio.create_task(_run_suggest(session_id, topic))
    return JSONResponse(status_code=202, content={"status": "processing"})


async def _run_suggest(session_id: str, topic: str):
    try:
        async with async_session() as db:
            repo = SessionRepository(db)
            session = await repo.get_session(session_id)

            await publish(session_id, {
                "type": "analysis_progress", "step": "evaluating",
                "message": "正在评估数据需求...",
            })
            clerk_rec = await moderator.recommend_data_clerk(topic)

            preliminary_data = None
            data_context = ""
            if clerk_rec.recommended:
                try:
                    search_provider = get_search_provider()
                    if search_provider:
                        await publish(session_id, {
                            "type": "analysis_progress", "step": "searching",
                            "message": "正在搜索相关数据...",
                        })
                        from app.agents.data_clerk import DataClerkAgent
                        data_clerk = DataClerkAgent()
                        on_search = await _emit_search_events(session_id, "suggest")

                        async def _on_progress(evt: dict):
                            await publish(session_id, {**evt, "phase": "suggest"})

                        results, validation = await data_clerk.research_with_validation(
                            topic, search_provider,
                            on_search=on_search,
                            on_progress=_on_progress,
                        )
                        if results:
                            if validation.validated or validation.unique:
                                await publish(session_id, {
                                    "type": "cross_validation_result",
                                    "validated": validation.validated,
                                    "unique": validation.unique,
                                    "contradictions": validation.contradictions,
                                    "note": validation.note, "phase": "suggest",
                                })
                            preliminary_data = results
                            lines = [f"- {r.get('title', '')}：{r.get('snippet', '')}" for r in results]
                            data_context = "\n".join(lines)
                            session.preliminary_data = results
                except Exception:
                    pass

            if settings.enable_cot:
                try:
                    await publish(session_id, {
                        "type": "analysis_progress", "step": "thinking",
                        "message": "主持人正在思考辩论立场...",
                    })
                    think_result = await moderator.think_before_suggesting(topic, data_context)
                    await publish(session_id, {
                        "type": "agent_thinking", "agent": "moderator",
                        "agent_name": "主持人", "thinking": think_result.thinking, "round": 0,
                    })

                    # ── Data-request loop: if thinking reveals data needs, search again ──
                    semantic_need = think_result.data_need
                    if not semantic_need and _thinking_mentions_data(think_result.thinking):
                        from app.services.llm import complete
                        try:
                            need_result = await complete([
                                {"role": "system", "content": "用一句话总结需要什么具体数据。只返回一句话。"},
                                {"role": "user", "content": (
                                    f"议题：{topic}\n思考：{think_result.thinking[:500]}\n\n"
                                    "用一句话总结：具体需要什么数据？"
                                )},
                            ])
                            semantic_need = need_result.strip()
                        except Exception:
                            pass
                    if semantic_need:
                        logger.info("Suggest data-request loop: %s", semantic_need[:80])
                        try:
                            sp = get_search_provider()
                            if sp:
                                from app.agents.data_clerk import DataClerkAgent
                                dc = DataClerkAgent()
                                pool_summary = (
                                    "\n".join(f"- {r.get('title', '')}：{r.get('snippet', '')}" for r in (preliminary_data or []))
                                    if preliminary_data else ""
                                )
                                extra_results, _ = await dc.research_for_agent(
                                    topic, semantic_need, sp,
                                    pool_summary=pool_summary,
                                )
                                if extra_results:
                                    extra_lines = [f"- {r.get('title', '')}：{r.get('snippet', '')}" for r in extra_results]
                                    data_context += "\n\n【补充搜索】\n" + "\n".join(extra_lines)
                                    preliminary_data = (preliminary_data or []) + extra_results
                                    session.preliminary_data = preliminary_data
                                    await repo.update_session(session)
                                    await publish(session_id, {
                                        "type": "data_fetch_complete",
                                        "results": extra_results, "phase": "suggest_followup",
                                    })
                        except Exception as e:
                            logger.warning("Suggest follow-up search failed: %s", e)
                except Exception as e:
                    logger.warning("Moderator position thinking failed: %s", e)

            await publish(session_id, {
                "type": "analysis_progress", "step": "suggesting",
                "message": "正在生成立场建议...",
            })
            positions = await moderator.suggest_positions(topic, data_context=data_context)

            existing = await repo.get_active_positions(session_id)
            for pos in existing:
                await repo.db.delete(pos)
            for p in positions:
                await repo.add_position(
                    session_id=session_id, name=p["name"],
                    description=p["description"], position_id=p["id"],
                )
            session.status = SessionStatus.SELECTING_POSITIONS
            await repo.update_session(session)

            await publish(session_id, {
                "type": "positions_result", "message_id": f"suggest_{session_id}",
                "session_id": session_id, "positions": positions,
                "data_clerk_recommended": clerk_rec.recommended,
                "data_clerk_reason": clerk_rec.reason,
                "preliminary_data": preliminary_data,
            })
    except Exception as e:
        logger.exception("Suggest positions failed for session %s", session_id)
        await publish(session_id, {
            "type": "error", "message_id": f"suggest_err_{session_id}",
            "source": "suggest", "message": "立场建议生成失败，请重试",
        })
    finally:
        _suggest_tasks.discard(session_id)


@router.post("/{session_id}/start")
async def start_discussion(
    session_id: str, req: SelectPositionsRequest, db: AsyncSession = Depends(get_db)
):
    if session_id in _active_sessions:
        raise HTTPException(status_code=409, detail="Discussion already in progress")
    repo = SessionRepository(db)
    session = await repo.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status not in (SessionStatus.SELECTING_POSITIONS, SessionStatus.CLARIFYING):
        raise HTTPException(status_code=400, detail="Session cannot be started from current state")
    _active_sessions.add(session_id)

    async def _run_orchestrator():
        try:
            async with async_session() as orch_db:
                orchestrator = Orchestrator(orch_db)
                await orchestrator.start_discussion(
                    session_id, req.position_ids, req.custom_positions,
                    enable_data_clerk=req.enable_data_clerk,
                )
        except Exception:
            pass
        finally:
            _active_sessions.discard(session_id)

    asyncio.create_task(_run_orchestrator())
    return {"session_id": session_id, "status": "discussing"}


@router.post("/{session_id}/data-pool")
async def add_user_data(
    session_id: str, req: AddDataRequest, db: AsyncSession = Depends(get_db)
):
    repo = SessionRepository(db)
    session = await repo.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status != SessionStatus.DISCUSSING:
        raise HTTPException(status_code=400, detail="Can only add data during active debate")
    item = await repo.add_data_pool_item(
        session_id=session_id, source="user",
        title=req.title, snippet=req.content, url=req.url,
    )
    await publish(session_id, {"type": "user_data_added", "data": item.to_dict()})
    return {"id": item.id, "status": "added"}
