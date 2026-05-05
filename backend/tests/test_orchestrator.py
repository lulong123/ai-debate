"""Tests for the orchestrator with mocked LLM calls."""

import json
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.models.session import SessionStatus
from app.services.orchestrator import Orchestrator
from app.storage.database import Base
from app.storage.repository import SessionRepository

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


async def _mock_stream_gen(text: str):
    """Async generator that yields tokens."""
    for char in text:
        yield char


async def test_orchestrator_full_flow(db: AsyncSession):
    """Test a minimal discussion: 2 angles, 1 round, conclude."""
    repo = SessionRepository(db)
    session = await repo.create_session("AI是否应该被监管", max_rounds=2)
    angle1 = await repo.add_angle(session.id, "技术视角", "从技术角度分析")
    angle2 = await repo.add_angle(session.id, "法律视角", "从法律角度分析")

    call_count = {"n": 0}

    # stream_completion is an async generator function, so the mock must
    # return an async generator directly (not a coroutine that returns one).
    def mock_stream(*args, **kwargs):
        return _mock_stream_gen("这是流式发言内容。")

    async def mock_complete(*args, **kwargs):
        return "这是完整回复内容。"

    async def mock_json(*args, **kwargs):
        call_count["n"] += 1
        n = call_count["n"]
        if n == 1:
            return {"scores": [
                {"angle_id": angle1.id, "angle_name": "技术视角", "total": 80,
                 "dimensions": {"evidence": 85, "responsiveness": 75, "novelty": 80}, "comment": "不错"},
                {"angle_id": angle2.id, "angle_name": "法律视角", "total": 75,
                 "dimensions": {"evidence": 70, "responsiveness": 80, "novelty": 75}, "comment": "可以"},
            ]}
        elif n == 2:
            return {"decision": "CONCLUDE", "reason": "讨论充分", "guidance": ""}
        else:
            return {
                "core_conclusion": "AI需要适度监管",
                "standpoints": [
                    {"angle": "技术视角", "main_points": ["技术挑战大"], "position": "需要技术创新"},
                    {"angle": "法律视角", "main_points": ["需要新框架"], "position": "需要法律创新"},
                ],
                "disagreements": ["监管力度"],
                "actionable_items": ["建立AI监管沙盒"],
                "summary": "讨论认为AI需要适度监管，需要技术和法律双轨并行。"
            }

    # Patch at the source module AND at all import sites
    patches = [
        patch("app.services.llm.stream_completion", side_effect=mock_stream),
        patch("app.services.llm.complete", side_effect=mock_complete),
        patch("app.services.llm.complete_json", side_effect=mock_json),
        patch("app.agents.base.stream_completion", side_effect=mock_stream),
        patch("app.agents.base.complete", side_effect=mock_complete),
        patch("app.agents.base.complete_json", side_effect=mock_json),
        patch("app.services.orchestrator.publish"),
    ]

    for p in patches:
        p.start()

    try:
        orch = Orchestrator(db)
        await orch.start_discussion(session.id, [angle1.id, angle2.id])
    finally:
        for p in patches:
            p.stop()

    session = await repo.get_session(session.id)
    assert session.status == SessionStatus.COMPLETED
    assert session.minutes is not None
    assert session.minutes["core_conclusion"] == "AI需要适度监管"

    messages = await repo.get_messages(session.id)
    assert len(messages) >= 3
