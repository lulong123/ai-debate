"""Tests for the repository layer."""

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.session import MessageRole, SessionStatus
from app.storage.database import Base
from app.storage.repository import SessionRepository

# Use in-memory SQLite for tests
TEST_URL = "sqlite+aiosqlite:///:memory:"
test_engine = create_async_engine(TEST_URL)
TestSession = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def setup_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def db():
    async with TestSession() as session:
        yield session


async def test_create_session(db: AsyncSession):
    repo = SessionRepository(db)
    session = await repo.create_session("测试议题", max_rounds=3)
    assert session.id
    assert session.topic == "测试议题"
    assert session.max_rounds == 3
    assert session.status == SessionStatus.CLARIFYING


async def test_get_session(db: AsyncSession):
    repo = SessionRepository(db)
    created = await repo.create_session("获取测试")
    found = await repo.get_session(created.id)
    assert found is not None
    assert found.topic == "获取测试"


async def test_get_session_not_found(db: AsyncSession):
    repo = SessionRepository(db)
    found = await repo.get_session("nonexistent")
    assert found is None


async def test_add_and_get_messages(db: AsyncSession):
    repo = SessionRepository(db)
    session = await repo.create_session("消息测试")
    await repo.add_message(session.id, seq=1, role=MessageRole.MODERATOR, content="开场白")
    await repo.add_message(
        session.id, seq=2, role=MessageRole.PERSPECTIVE,
        content="辩手发言", agent_name="支持方", round_number=1
    )
    msgs = await repo.get_messages(session.id)
    assert len(msgs) == 2
    assert msgs[0].content == "开场白"
    assert msgs[1].agent_name == "支持方"


async def test_add_and_get_positions(db: AsyncSession):
    repo = SessionRepository(db)
    session = await repo.create_session("立场测试")
    await repo.add_position(session.id, "支持", "认为应该推行")
    await repo.add_position(session.id, "反对", "认为不应该推行")
    positions = await repo.get_active_positions(session.id)
    assert len(positions) == 2
    assert positions[0].name == "支持"


async def test_list_sessions(db: AsyncSession):
    repo = SessionRepository(db)
    await repo.create_session("议题1")
    await repo.create_session("议题2")
    await repo.create_session("议题3")
    sessions = await repo.list_sessions(limit=2)
    assert len(sessions) == 2


async def test_update_session_status(db: AsyncSession):
    repo = SessionRepository(db)
    session = await repo.create_session("状态测试")
    session.status = SessionStatus.DISCUSSING
    await repo.update_session(session)
    found = await repo.get_session(session.id)
    assert found.status == SessionStatus.DISCUSSING


# --- Unified data pool tests ---


async def test_persist_research_results_basic(db: AsyncSession):
    """persist_research_results should add items to data pool."""
    repo = SessionRepository(db)
    session = await repo.create_session("pool test")
    results = [
        {"title": "Article 1", "snippet": "content 1", "url": "https://a.com/1"},
        {"title": "Article 2", "snippet": "content 2", "url": "https://b.com/2"},
    ]
    new_items = await repo.persist_research_results(
        session.id, results, source="clarify",
    )
    assert len(new_items) == 2
    pool = await repo.get_data_pool(session.id)
    assert len(pool) == 2
    assert pool[0].source == "clarify"
    assert pool[0].title == "Article 1"


async def test_persist_research_results_dedup_url(db: AsyncSession):
    """persist_research_results should skip URLs already in pool."""
    repo = SessionRepository(db)
    session = await repo.create_session("dedup test")
    # First batch
    await repo.persist_research_results(
        session.id,
        [{"title": "A1", "snippet": "s1", "url": "https://a.com/1"}],
        source="clarify",
    )
    # Second batch with same URL
    new_items = await repo.persist_research_results(
        session.id,
        [
            {"title": "A1 Updated", "snippet": "s1 new", "url": "https://a.com/1"},
            {"title": "B1", "snippet": "s2", "url": "https://b.com/1"},
        ],
        source="suggest",
    )
    # Only the new URL should be added
    assert len(new_items) == 1
    assert new_items[0].title == "B1"
    pool = await repo.get_data_pool(session.id)
    assert len(pool) == 2


async def test_persist_research_results_no_url(db: AsyncSession):
    """persist_research_results should always add items without URL."""
    repo = SessionRepository(db)
    session = await repo.create_session("no url test")
    results = [
        {"title": "No URL", "snippet": "content", "url": ""},
        {"title": "Also No URL", "snippet": "more content"},
    ]
    new_items = await repo.persist_research_results(
        session.id, results, source="user",
    )
    assert len(new_items) == 2


async def test_persist_research_results_with_key_facts(db: AsyncSession):
    """persist_research_results should persist key_facts."""
    repo = SessionRepository(db)
    session = await repo.create_session("key facts test")
    key_facts = json.dumps(
        {"key_facts": ["fact1", "fact2"], "summary": "test"},
        ensure_ascii=False,
    )
    results = [
        {"title": "Article", "snippet": "s", "url": "https://a.com",
         "key_facts": key_facts},
    ]
    new_items = await repo.persist_research_results(
        session.id, results, source="data_clerk",
    )
    assert len(new_items) == 1
    assert new_items[0].key_facts == key_facts


async def test_get_pool_summary_empty(db: AsyncSession):
    """get_pool_summary should return empty string when pool is empty."""
    repo = SessionRepository(db)
    session = await repo.create_session("empty pool")
    summary = await repo.get_pool_summary(session.id)
    assert summary == ""


async def test_get_pool_summary_with_facts(db: AsyncSession):
    """get_pool_summary should return formatted key_facts summary."""
    repo = SessionRepository(db)
    session = await repo.create_session("summary test")
    key_facts = json.dumps(
        {"key_facts": ["哈登得到35分", "火箭获胜"], "summary": "哈登全场最佳"},
        ensure_ascii=False,
    )
    await repo.add_data_pool_item(
        session.id, source="clarify", title="哈登战报",
        snippet="NBA常规赛...", url="https://example.com",
        key_facts=key_facts, is_public=True,
    )
    summary = await repo.get_pool_summary(session.id)
    assert "[1]" in summary
    assert "哈登得到35分" in summary
    assert "火箭获胜" in summary
