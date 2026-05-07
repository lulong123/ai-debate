"""Tests for the repository layer."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.models.session import Position, DiscussionSession, Message, MessageRole, SessionStatus
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
