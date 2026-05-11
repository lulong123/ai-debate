"""Tests for the orchestrator with mocked LLM calls."""

from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.schemas import (
    AgentThinking,
    DebateMinutes,
    RoundJudgment,
    ScoreEntry,
    ScoreResult,
    Verdict,
)
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
    """Test a minimal debate: 2 positions, 1 round, conclude."""
    repo = SessionRepository(db)
    session = await repo.create_session("AI是否应该被监管", max_rounds=2)
    pos1 = await repo.add_position(session.id, "支持", "认为应该推行监管")
    pos2 = await repo.add_position(session.id, "反对", "认为不应该推行监管")

    call_count = {"n": 0}

    def mock_stream(*args, **kwargs):
        return _mock_stream_gen("这是流式辩论内容。")

    async def mock_complete(*args, **kwargs):
        return "这是完整回复内容。"

    async def mock_typed(messages, response_model, model=None, **kwargs):
        """Mock complete_typed that returns proper Pydantic model instances."""
        call_count["n"] += 1
        n = call_count["n"]
        if response_model is ScoreResult:
            if n <= 1:
                return ScoreResult(scores=[
                    ScoreEntry(
                        position_id=pos1.id, position_name="支持",
                        points=60, comment="论据充分",
                    ),
                    ScoreEntry(
                        position_id=pos2.id, position_name="反对",
                        points=40, comment="反驳较弱",
                    ),
                ])
            return ScoreResult(scores=[
                ScoreEntry(position_id=pos1.id, position_name="支持", points=55, comment=""),
                ScoreEntry(position_id=pos2.id, position_name="反对", points=45, comment=""),
            ])
        if response_model is RoundJudgment:
            return RoundJudgment(decision="CONCLUDE", reason="辩论充分", guidance="")
        if response_model is DebateMinutes:
            return DebateMinutes(
                core_conclusion="支持方获胜",
                position_arguments=[
                    {"position": "支持", "main_points": ["需要监管框架"], "defense": "论据有力"},
                    {"position": "反对", "main_points": ["创新受限"], "defense": "反驳不足"},
                ],
                key_clashes=["监管与创新平衡"],
                verdict=Verdict(winner="支持", rationale="论据更充分", score_summary="60:40"),
                summary="支持方以充分论据获胜。",
            )
        if response_model is AgentThinking:
            return AgentThinking(
                thinking="分析辩论局势...",
                chosen_strategy="ATTACK",
            )
        # Fallback for any other model
        return response_model()

    patches = [
        patch("app.services.llm.stream_completion", side_effect=mock_stream),
        patch("app.services.llm.complete", side_effect=mock_complete),
        patch("app.services.llm.complete_typed", side_effect=mock_typed),
        patch("app.agents.base.stream_completion", side_effect=mock_stream),
        patch("app.agents.base.complete", side_effect=mock_complete),
        patch("app.agents.base.complete_typed", side_effect=mock_typed),
        patch("app.services.orchestrator.publish"),
    ]

    for p in patches:
        p.start()

    try:
        orch = Orchestrator(db)
        await orch.start_discussion(session.id, [pos1.id, pos2.id])
    finally:
        for p in patches:
            p.stop()

    session = await repo.get_session(session.id)
    assert session.status == SessionStatus.COMPLETED
    assert session.minutes is not None
    assert session.minutes["core_conclusion"] == "支持方获胜"
    assert session.minutes["verdict"]["winner"] == "支持"

    messages = await repo.get_messages(session.id)
    assert len(messages) >= 3
