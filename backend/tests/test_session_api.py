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
    assert "原始议题" in data["refined_topic"]
    assert "澄清后的议题" in data["refined_topic"]


async def test_get_messages_empty(client: AsyncClient):
    create = await client.post("/api/sessions", json={"topic": "消息测试"})
    session_id = create.json()["session_id"]
    resp = await client.get(f"/api/sessions/{session_id}/messages")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_get_data_pool_empty(client: AsyncClient):
    create = await client.post("/api/sessions", json={"topic": "数据池测试"})
    session_id = create.json()["session_id"]
    resp = await client.get(f"/api/sessions/{session_id}/data-pool")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_get_data_pool_with_items(client: AsyncClient):
    import json

    create = await client.post("/api/sessions", json={"topic": "数据池测试"})
    session_id = create.json()["session_id"]
    # Add items directly via repo (POST /data-pool requires DISCUSSING status)
    from app.storage.database import async_session
    from app.storage.repository import SessionRepository

    async with async_session() as db:
        repo = SessionRepository(db)
        await repo.add_data_pool_item(
            session_id, source="clarify", title="文章1",
            snippet="内容1", url="https://a.com/1",
            key_facts=json.dumps({"key_facts": ["事实1"], "summary": "摘要1"}, ensure_ascii=False),
            is_public=True,
        )
        await repo.add_data_pool_item(
            session_id, source="data_clerk", title="文章2",
            snippet="内容2", url="https://b.com/2",
            is_public=True,
        )
    resp = await client.get(f"/api/sessions/{session_id}/data-pool")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["citation_num"] == 1
    assert data[1]["citation_num"] == 2
    assert data[0]["title"] == "文章1"
    assert data[1]["title"] == "文章2"
    assert data[0]["source"] == "clarify"


async def test_get_positions_empty(client: AsyncClient):
    create = await client.post("/api/sessions", json={"topic": "立场测试"})
    session_id = create.json()["session_id"]
    resp = await client.get(f"/api/sessions/{session_id}/positions")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_get_positions_with_items(client: AsyncClient):
    create = await client.post("/api/sessions", json={"topic": "立场测试"})
    session_id = create.json()["session_id"]
    # Add positions directly (normally done by suggest-positions endpoint)
    from app.storage.database import async_session
    from app.storage.repository import SessionRepository

    async with async_session() as db:
        repo = SessionRepository(db)
        await repo.add_position(session_id, "支持", "认为应该推行")
        await repo.add_position(session_id, "反对", "认为不应该推行")

    resp = await client.get(f"/api/sessions/{session_id}/positions")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["name"] == "支持"
    assert data[1]["name"] == "反对"


async def test_get_data_pool_not_found(client: AsyncClient):
    resp = await client.get("/api/sessions/nonexistent/data-pool")
    assert resp.status_code == 404


async def test_get_positions_not_found(client: AsyncClient):
    resp = await client.get("/api/sessions/nonexistent/positions")
    assert resp.status_code == 404


async def test_delete_session(client: AsyncClient):
    create = await client.post("/api/sessions", json={"topic": "待删除"})
    session_id = create.json()["session_id"]
    # Verify it exists
    resp = await client.get(f"/api/sessions/{session_id}")
    assert resp.status_code == 200
    # Delete
    resp = await client.delete(f"/api/sessions/{session_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"
    # Verify gone
    resp = await client.get(f"/api/sessions/{session_id}")
    assert resp.status_code == 404


async def test_delete_session_not_found(client: AsyncClient):
    resp = await client.delete("/api/sessions/nonexistent")
    assert resp.status_code == 404


async def test_update_session_topic(client: AsyncClient):
    create = await client.post("/api/sessions", json={"topic": "旧议题"})
    session_id = create.json()["session_id"]
    resp = await client.patch(
        f"/api/sessions/{session_id}",
        json={"topic": "新议题"},
    )
    assert resp.status_code == 200
    assert resp.json()["topic"] == "新议题"
    # Verify persisted
    resp = await client.get(f"/api/sessions/{session_id}")
    assert resp.json()["topic"] == "新议题"


async def test_update_session_not_found(client: AsyncClient):
    resp = await client.patch("/api/sessions/nonexistent", json={"topic": "xxx"})
    assert resp.status_code == 404


async def test_list_sessions_with_search(client: AsyncClient):
    await client.post("/api/sessions", json={"topic": "篮球谁更强"})
    await client.post("/api/sessions", json={"topic": "足球谁更强"})
    await client.post("/api/sessions", json={"topic": "哲学问题"})
    # Search for 篮球
    resp = await client.get("/api/sessions", params={"search": "篮球"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert "篮球" in data[0]["topic"]
    # Search for 谁更强 (matches both 篮球 and 足球)
    resp = await client.get("/api/sessions", params={"search": "谁更强"})
    data = resp.json()
    assert len(data) == 2


async def test_list_sessions_with_status_filter(client: AsyncClient):
    await client.post("/api/sessions", json={"topic": "议题A"})
    # All sessions start as "clarifying" status
    resp = await client.get("/api/sessions", params={"status": "clarifying"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    # Filter for completed (none yet)
    resp = await client.get("/api/sessions", params={"status": "completed"})
    data = resp.json()
    assert len(data) == 0


async def test_list_sessions_with_winner(client: AsyncClient):
    """Verify winner field is extracted from minutes for completed sessions."""
    create = await client.post("/api/sessions", json={"topic": "胜方测试"})
    session_id = create.json()["session_id"]
    # Simulate a completed session with minutes
    from app.storage.database import async_session
    from app.storage.repository import SessionRepository

    async with async_session() as db:
        repo = SessionRepository(db)
        session = await repo.get_session(session_id)
        session.status = "completed"
        session.minutes = {"verdict": {"winner": "正方", "rationale": "测试"}}
        await repo.update_session(session)

    resp = await client.get("/api/sessions")
    data = resp.json()
    match = [s for s in data if s["session_id"] == session_id]
    assert len(match) == 1
    assert match[0]["winner"] == "正方"
