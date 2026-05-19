import asyncio
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.storage.database import get_db
from app.storage.repository import SessionRepository

router = APIRouter()

# In-memory event bus: session_id -> list of asyncio.Queue
# Each queue is bounded to prevent memory leaks from abandoned subscribers
_MAX_QUEUE_SIZE = 200
_SUBSCRIBERS: dict[str, list[asyncio.Queue]] = {}

# Event history for replay on reconnect: session_id -> list of (timestamp, event)
_HISTORY: dict[str, list[tuple[float, dict]]] = {}
_MAX_HISTORY = 500


def subscribe(session_id: str) -> asyncio.Queue:
    queue = asyncio.Queue(maxsize=_MAX_QUEUE_SIZE)
    if session_id not in _SUBSCRIBERS:
        _SUBSCRIBERS[session_id] = []
    _SUBSCRIBERS[session_id].append(queue)
    return queue


def unsubscribe(session_id: str, queue: asyncio.Queue):
    if session_id in _SUBSCRIBERS:
        try:
            _SUBSCRIBERS[session_id].remove(queue)
        except ValueError:
            pass
        if not _SUBSCRIBERS[session_id]:
            del _SUBSCRIBERS[session_id]
            # Clean up history when no subscribers remain
            _HISTORY.pop(session_id, None)


async def publish(session_id: str, event: dict):
    """Publish an event to all subscribers and store in history."""
    # Store in history for reconnect replay
    if session_id not in _HISTORY:
        _HISTORY[session_id] = []
    _HISTORY[session_id].append((time.monotonic(), event))
    # Trim history if too large
    if len(_HISTORY[session_id]) > _MAX_HISTORY:
        _HISTORY[session_id] = _HISTORY[session_id][-_MAX_HISTORY:]

    # Push to all subscriber queues, drop oldest if full
    for q in _SUBSCRIBERS.get(session_id, []):
        if q.full():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        await q.put(event)


@router.get("/{session_id}/stream")
async def stream_session(
    session_id: str,
    request: Request,
    last_event_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """SSE stream for a discussion session.

    Supports reconnection via Last-Event-ID header: events after that ID
    are replayed from in-memory history.
    """
    repo = SessionRepository(db)
    session = await repo.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    queue = subscribe(session_id)

    async def event_generator():
        try:
            # Replay history on both first connection and reconnect.
            # On reconnect (last_event_id set), replay only events after that ID.
            # On first connection, replay all history so we don't miss early events.
            history = _HISTORY.get(session_id, [])
            if history:
                replay_after = None
                if last_event_id is not None:
                    # Find the position of the last seen event
                    for i, (_ts, evt) in enumerate(history):
                        eid = evt.get("message_id") or evt.get("round")
                        if eid is not None and str(eid) == last_event_id:
                            replay_after = i
                            break
                start_idx = (replay_after + 1) if replay_after is not None else 0
                for _ts, event in history[start_idx:]:
                    eid = event.get("message_id") or event.get("round")
                    yield {
                        "event": event.get("type", "message"),
                        "data": json.dumps(event, ensure_ascii=False),
                        **({"id": str(eid)} if eid is not None else {}),
                    }

            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    eid = event.get("message_id") or event.get("round")
                    yield {
                        "event": event.get("type", "message"),
                        "data": json.dumps(event, ensure_ascii=False),
                        **({"id": str(eid)} if eid is not None else {}),
                    }
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
        finally:
            unsubscribe(session_id, queue)

    return EventSourceResponse(event_generator())
