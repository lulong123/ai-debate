import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.storage.database import get_db
from app.storage.repository import SessionRepository

router = APIRouter()

# In-memory event bus: session_id -> list of asyncio.Queue
_subscribers: dict[str, list[asyncio.Queue]] = {}


def subscribe(session_id: str) -> asyncio.Queue:
    queue = asyncio.Queue()
    if session_id not in _subscribers:
        _subscribers[session_id] = []
    _subscribers[session_id].append(queue)
    return queue


def unsubscribe(session_id: str, queue: asyncio.Queue):
    if session_id in _subscribers:
        try:
            _subscribers[session_id].remove(queue)
        except ValueError:
            pass
        if not _subscribers[session_id]:
            del _subscribers[session_id]


async def publish(session_id: str, event: dict):
    """Publish an event to all subscribers of a session."""
    queues = _subscribers.get(session_id, [])
    for q in queues:
        await q.put(event)


@router.get("/{session_id}/stream")
async def stream_session(session_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    repo = SessionRepository(db)
    session = await repo.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    queue = subscribe(session_id)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield {"event": event.get("type", "message"), "data": json.dumps(event, ensure_ascii=False)}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
        finally:
            unsubscribe(session_id, queue)

    return EventSourceResponse(event_generator())
