import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.moderator import ModeratorAgent
from app.models import SessionStatus
from app.services.orchestrator import Orchestrator
from app.storage.database import get_db
from app.storage.repository import SessionRepository

router = APIRouter()
moderator = ModeratorAgent()

# Track in-flight discussions to prevent concurrent starts
_active_sessions: set[str] = set()


class CreateSessionRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=500)
    max_rounds: int = Field(default=3, ge=1, le=10)


class ClarifyResponse(BaseModel):
    answer: str = Field(..., min_length=1, max_length=1000)


class SelectAnglesRequest(BaseModel):
    angle_ids: list[str] = Field(..., min_length=2, max_length=6)
    custom_angles: list[dict] | None = None


@router.post("")
async def create_session(
    req: CreateSessionRequest,
    db: AsyncSession = Depends(get_db),
):
    repo = SessionRepository(db)
    session = await repo.create_session(topic=req.topic, max_rounds=req.max_rounds)
    return {"session_id": session.id, "status": session.status}


@router.get("")
async def list_sessions(db: AsyncSession = Depends(get_db)):
    repo = SessionRepository(db)
    sessions = await repo.list_sessions()
    return [
        {
            "session_id": s.id,
            "topic": s.topic,
            "status": s.status,
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
        "session_id": session.id,
        "topic": session.topic,
        "refined_topic": session.refined_topic,
        "status": session.status,
        "current_round": session.current_round,
        "max_rounds": session.max_rounds,
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
            "id": m.id,
            "role": m.role,
            "agent_name": m.agent_name,
            "angle_id": m.angle_id,
            "round_number": m.round_number,
            "content": m.content,
            "scores": m.scores,
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
    """Run topic clarification via moderator."""
    repo = SessionRepository(db)
    session = await repo.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    result = await moderator.clarify_topic(session.topic)
    if result.get("valid"):
        session.refined_topic = session.topic
    return result


@router.post("/{session_id}/refine")
async def refine_topic(session_id: str, req: ClarifyResponse, db: AsyncSession = Depends(get_db)):
    """Update topic after clarification."""
    repo = SessionRepository(db)
    session = await repo.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session.refined_topic = req.answer
    await repo.update_session(session)
    return {"session_id": session.id, "refined_topic": session.refined_topic}


@router.post("/{session_id}/suggest-angles")
async def suggest_angles(session_id: str, db: AsyncSession = Depends(get_db)):
    """Get angle suggestions from moderator."""
    repo = SessionRepository(db)
    session = await repo.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    topic = session.refined_topic or session.topic
    angles = await moderator.suggest_angles(topic)
    # Persist suggested angles
    for a in angles:
        await repo.add_angle(session_id=session_id, name=a["name"], description=a["description"])
    session.status = SessionStatus.SELECTING_ANGLES
    await repo.update_session(session)
    return {"session_id": session_id, "angles": angles}


@router.post("/{session_id}/start")
async def start_discussion(
    session_id: str, req: SelectAnglesRequest, db: AsyncSession = Depends(get_db)
):
    """Start the discussion with selected angles."""
    if session_id in _active_sessions:
        raise HTTPException(status_code=409, detail="Discussion already in progress")

    repo = SessionRepository(db)
    session = await repo.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status not in (SessionStatus.SELECTING_ANGLES, SessionStatus.CLARIFYING):
        raise HTTPException(status_code=400, detail="Session cannot be started from current state")

    _active_sessions.add(session_id)

    from app.storage.database import async_session

    async def _run_orchestrator():
        try:
            async with async_session() as orch_db:
                orchestrator = Orchestrator(orch_db)
                await orchestrator.start_discussion(session_id, req.angle_ids, req.custom_angles)
        except Exception:
            pass  # Orchestrator handles its own errors internally
        finally:
            _active_sessions.discard(session_id)

    asyncio.create_task(_run_orchestrator())
    return {"session_id": session_id, "status": "discussing"}
