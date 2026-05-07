import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.moderator import ModeratorAgent
from app.models import SessionStatus
from app.services.orchestrator import Orchestrator
from app.services.search import get_search_provider
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


class SelectPositionsRequest(BaseModel):
    position_ids: list[str] = Field(..., min_length=2, max_length=6)
    custom_positions: list[dict] | None = None
    enable_data_clerk: bool = False


class AddDataRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1, max_length=2000)
    url: str = Field(default="", max_length=500)


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
            "position_id": m.position_id,
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
    if result.valid:
        session.refined_topic = session.topic
    return result.model_dump()


@router.post("/{session_id}/refine")
async def refine_topic(session_id: str, req: ClarifyResponse, db: AsyncSession = Depends(get_db)):
    """Update topic after clarification."""
    repo = SessionRepository(db)
    session = await repo.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    # Merge user's answer with original topic, don't replace it
    session.refined_topic = f"{session.topic}\n补充说明：{req.answer}"
    await repo.update_session(session)
    return {"session_id": session.id, "refined_topic": session.refined_topic}


@router.post("/{session_id}/suggest-positions")
async def suggest_positions(session_id: str, db: AsyncSession = Depends(get_db)):
    """Get position suggestions and data clerk recommendation from moderator."""
    repo = SessionRepository(db)
    session = await repo.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    topic = session.refined_topic or session.topic

    # First, get data clerk recommendation
    clerk_rec = await moderator.recommend_data_clerk(topic)

    # If recommended, run preliminary search to enrich position suggestions
    preliminary_data = None
    data_context = ""
    if clerk_rec.recommended:
        try:
            from app.agents.data_clerk import DataClerkAgent
            data_clerk = DataClerkAgent()
            search_provider = get_search_provider()
            if search_provider:
                results = await data_clerk.fetch_for_topic(topic, search_provider)
                if results:
                    preliminary_data = results
                    lines = [f"- {r.get('title', '')}：{r.get('snippet', '')}" for r in results]
                    data_context = "\n".join(lines)
                    session.preliminary_data = results
        except Exception:
            pass  # Non-blocking: search failure doesn't prevent position suggestions

    # Get position suggestions, optionally enriched with data
    positions = await moderator.suggest_positions(topic, data_context=data_context)

    # Clear any existing positions (idempotent)
    existing = await repo.get_active_positions(session_id)
    for pos in existing:
        await repo.db.delete(pos)
    # Persist suggested positions
    for p in positions:
        await repo.add_position(session_id=session_id, name=p["name"], description=p["description"], position_id=p["id"])
    session.status = SessionStatus.SELECTING_POSITIONS
    await repo.update_session(session)
    return {
        "session_id": session_id,
        "positions": positions,
        "data_clerk_recommended": clerk_rec.recommended,
        "data_clerk_reason": clerk_rec.reason,
        "preliminary_data": preliminary_data,
    }


@router.post("/{session_id}/start")
async def start_discussion(
    session_id: str, req: SelectPositionsRequest, db: AsyncSession = Depends(get_db)
):
    """Start the debate with selected positions."""
    if session_id in _active_sessions:
        raise HTTPException(status_code=409, detail="Discussion already in progress")

    repo = SessionRepository(db)
    session = await repo.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status not in (SessionStatus.SELECTING_POSITIONS, SessionStatus.CLARIFYING):
        raise HTTPException(status_code=400, detail="Session cannot be started from current state")

    _active_sessions.add(session_id)

    from app.storage.database import async_session

    async def _run_orchestrator():
        try:
            async with async_session() as orch_db:
                orchestrator = Orchestrator(orch_db)
                await orchestrator.start_discussion(
                    session_id, req.position_ids, req.custom_positions,
                    enable_data_clerk=req.enable_data_clerk,
                )
        except Exception:
            pass  # Orchestrator handles its own errors internally
        finally:
            _active_sessions.discard(session_id)

    asyncio.create_task(_run_orchestrator())
    return {"session_id": session_id, "status": "discussing"}


@router.post("/{session_id}/data-pool")
async def add_user_data(
    session_id: str, req: AddDataRequest, db: AsyncSession = Depends(get_db)
):
    """Add user-contributed data to the shared data pool during debate."""
    repo = SessionRepository(db)
    session = await repo.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status != SessionStatus.DISCUSSING:
        raise HTTPException(status_code=400, detail="Can only add data during active debate")

    item = await repo.add_data_pool_item(
        session_id=session_id,
        source="user",
        title=req.title,
        snippet=req.content,
        url=req.url,
    )

    # Broadcast to SSE subscribers
    from app.routers.sse import publish
    await publish(session_id, {
        "type": "user_data_added",
        "data": item.to_dict(),
    })

    return {"id": item.id, "status": "added"}
