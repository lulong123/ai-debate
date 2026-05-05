"""Tests for SSE event bus (publish/subscribe/history)."""

import asyncio

import pytest

from app.routers.sse import subscribe, unsubscribe, publish, _SUBSCRIBERS, _HISTORY


@pytest.fixture(autouse=True)
def cleanup():
    yield
    _SUBSCRIBERS.clear()
    _HISTORY.clear()


async def test_subscribe_and_unsubscribe():
    q = subscribe("sess1")
    assert "sess1" in _SUBSCRIBERS
    assert q in _SUBSCRIBERS["sess1"]

    unsubscribe("sess1", q)
    assert "sess1" not in _SUBSCRIBERS


async def test_publish_delivers_to_subscriber():
    q = subscribe("sess1")
    event = {"type": "test", "data": "hello"}
    await publish("sess1", event)
    received = q.get_nowait()
    assert received == event


async def test_publish_stores_history():
    await publish("sess1", {"type": "msg1", "round": 1})
    await publish("sess1", {"type": "msg2", "round": 2})
    assert len(_HISTORY.get("sess1", [])) == 2


async def test_history_trimmed_at_max():
    for i in range(600):
        await publish("sess1", {"type": "msg", "i": i})
    assert len(_HISTORY["sess1"]) == 500


async def test_publish_to_no_subscribers_no_error():
    await publish("nonexistent", {"type": "test"})
    assert len(_HISTORY.get("nonexistent", [])) == 1


async def test_bounded_queue_drops_oldest():
    q = subscribe("sess1")
    # Fill the queue to max
    for i in range(250):
        await publish("sess1", {"type": "msg", "i": i})
    # Queue should not exceed max size
    assert q.qsize() <= 200


async def test_unsubscribe_cleans_up_history():
    q1 = subscribe("sess1")
    await publish("sess1", {"type": "test"})
    assert "sess1" in _HISTORY

    # Still has subscriber, history stays
    unsubscribe("sess1", q1)
    assert "sess1" not in _SUBSCRIBERS
    assert "sess1" not in _HISTORY
