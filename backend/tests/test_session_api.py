"""Tests for session API endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.storage.database import Base, engine


@pytest.fixture(autouse=True)
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_health(client: AsyncClient):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


async def test_create_session(client: AsyncClient):
    resp = await client.post("/api/sessions", json={"topic": "测试议题", "max_rounds": 2})
    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data
    assert data["status"] == "clarifying"


async def test_list_sessions(client: AsyncClient):
    # Create two sessions
    await client.post("/api/sessions", json={"topic": "议题1"})
    await client.post("/api/sessions", json={"topic": "议题2"})
    resp = await client.get("/api/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2


async def test_get_session(client: AsyncClient):
    create = await client.post("/api/sessions", json={"topic": "获取测试"})
    session_id = create.json()["session_id"]
    resp = await client.get(f"/api/sessions/{session_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["topic"] == "获取测试"
    assert data["session_id"] == session_id


async def test_get_session_not_found(client: AsyncClient):
    resp = await client.get("/api/sessions/nonexistent")
    assert resp.status_code == 404


async def test_refine_topic(client: AsyncClient):
    create = await client.post("/api/sessions", json={"topic": "原始议题"})
    session_id = create.json()["session_id"]
    resp = await client.post(
        f"/api/sessions/{session_id}/refine",
        json={"answer": "澄清后的议题"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["refined_topic"] == "澄清后的议题"


async def test_get_messages_empty(client: AsyncClient):
    create = await client.post("/api/sessions", json={"topic": "消息测试"})
    session_id = create.json()["session_id"]
    resp = await client.get(f"/api/sessions/{session_id}/messages")
    assert resp.status_code == 200
    assert resp.json() == []
