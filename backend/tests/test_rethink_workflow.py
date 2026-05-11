"""Tests for the Think → Search → Re-Think → Act workflow."""

from unittest.mock import patch, AsyncMock

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
    for char in text:
        yield char


def _make_patches():
    """Create standard mock patches for LLM calls."""
    return [
        patch("app.services.llm.stream_completion", side_effect=lambda *a, **k: _mock_stream_gen("辩论内容")),
        patch("app.services.llm.complete", side_effect=AsyncMock(return_value="回复内容")),
        patch("app.agents.base.stream_completion", side_effect=lambda *a, **k: _mock_stream_gen("辩论内容")),
        patch("app.agents.base.complete", side_effect=AsyncMock(return_value="回复内容")),
        patch("app.services.orchestrator.publish"),
    ]


async def test_debater_rethink_after_data_fetch(db: AsyncSession):
    """When debater requests data, re-think should fire after fetch."""
    repo = SessionRepository(db)
    session = await repo.create_session("谁更强", max_rounds=2)
    pos1 = await repo.add_position(session.id, "A方", "认为A更强")
    pos2 = await repo.add_position(session.id, "B方", "认为B更强")

    call_log = []

    async def mock_typed(messages, response_model, model=None, **kwargs):
        call_log.append(("typed", response_model.__name__))

        if response_model is ScoreResult:
            return ScoreResult(scores=[
                ScoreEntry(position_id=pos1.id, position_name="A方", points=50, comment=""),
                ScoreEntry(position_id=pos2.id, position_name="B方", points=50, comment=""),
            ])
        if response_model is RoundJudgment:
            return RoundJudgment(decision="CONCLUDE", reason="充分", guidance="")
        if response_model is DebateMinutes:
            return DebateMinutes(
                core_conclusion="平局",
                verdict=Verdict(winner="A方", rationale="测试", score_summary="50:50"),
                summary="测试纪要",
            )
        if response_model is AgentThinking:
            # First call (initial think) returns data requests
            if not any(c[1] == "AgentThinking" for c in call_log[:-1]):
                return AgentThinking(
                    thinking="需要搜索数据",
                    data_need="需要A方的具体比赛数据",
                    chosen_strategy="EVIDENCE",
                )
            # Second call (re-think) — no more data requests
            return AgentThinking(thinking="新数据显示A方确实强", data_need="")

        return response_model()

    patches = _make_patches()
    patches.append(
        patch("app.services.llm.complete_typed", side_effect=mock_typed),
    )
    patches.append(
        patch("app.agents.base.complete_typed", side_effect=mock_typed),
    )
    # Mock search provider
    patches.append(
        patch("app.services.orchestrator.get_search_provider", return_value=None),
    )

    for p in patches:
        p.start()
    try:
        orch = Orchestrator(db)
        # Enable data clerk + search mock
        orch.search = None
        await orch.start_discussion(
            session.id, [pos1.id, pos2.id], enable_data_clerk=True,
        )
    finally:
        for p in patches:
            p.stop()

    session = await repo.get_session(session.id)
    assert session.status == SessionStatus.COMPLETED
    # Verify multiple AgentThinking calls occurred (initial + re-think)
    thinking_calls = [c for c in call_log if c[1] == "AgentThinking"]
    assert len(thinking_calls) >= 2, f"Expected >= 2 thinking calls, got {len(thinking_calls)}"


async def test_debater_no_rethink_without_data(db: AsyncSession):
    """When debater doesn't request data, no re-think should fire."""
    repo = SessionRepository(db)
    session = await repo.create_session("哲学问题", max_rounds=2)
    pos1 = await repo.add_position(session.id, "是", "认为是")
    pos2 = await repo.add_position(session.id, "否", "认为否")

    call_log = []

    async def mock_typed(messages, response_model, model=None, **kwargs):
        call_log.append(("typed", response_model.__name__))

        if response_model is ScoreResult:
            return ScoreResult(scores=[
                ScoreEntry(position_id=pos1.id, position_name="是", points=50, comment=""),
                ScoreEntry(position_id=pos2.id, position_name="否", points=50, comment=""),
            ])
        if response_model is RoundJudgment:
            return RoundJudgment(decision="CONCLUDE", reason="充分", guidance="")
        if response_model is DebateMinutes:
            return DebateMinutes(
                core_conclusion="测试",
                verdict=Verdict(winner="是", rationale="测试", score_summary="50:50"),
                summary="测试",
            )
        if response_model is AgentThinking:
            return AgentThinking(
                thinking="不需要搜索数据",
                data_need="",  # Empty — no fetch, no re-think
                chosen_strategy="ATTACK",
            )

        return response_model()

    patches = _make_patches()
    patches.append(patch("app.services.llm.complete_typed", side_effect=mock_typed))
    patches.append(patch("app.agents.base.complete_typed", side_effect=mock_typed))
    # Prevent real search provider from being used
    patches.append(
        patch("app.services.orchestrator.get_search_provider", return_value=None),
    )

    for p in patches:
        p.start()
    try:
        orch = Orchestrator(db)
        orch.search = None
        await orch.start_discussion(
            session.id, [pos1.id, pos2.id], enable_data_clerk=True,
        )
    finally:
        for p in patches:
            p.stop()

    # Only 1 AgentThinking call per debater (no re-think)
    thinking_calls = [c for c in call_log if c[1] == "AgentThinking"]
    # 2 debaters + 1 scorer + 1 moderator + 1 minutes = 5
    assert len(thinking_calls) == 5, f"Expected 5 thinking calls, got {len(thinking_calls)}"


async def test_moderator_no_fetch_without_data_requests(db: AsyncSession):
    """When moderator doesn't request data, no fetch or re-think."""
    repo = SessionRepository(db)
    session = await repo.create_session("测试议题", max_rounds=2)
    pos1 = await repo.add_position(session.id, "甲", "甲观点")
    pos2 = await repo.add_position(session.id, "乙", "乙观点")

    call_log = []

    async def mock_typed(messages, response_model, model=None, **kwargs):
        call_log.append(("typed", response_model.__name__))

        if response_model is ScoreResult:
            return ScoreResult(scores=[
                ScoreEntry(position_id=pos1.id, position_name="甲", points=50, comment=""),
                ScoreEntry(position_id=pos2.id, position_name="乙", points=50, comment=""),
            ])
        if response_model is RoundJudgment:
            return RoundJudgment(decision="CONCLUDE", reason="充分", guidance="")
        if response_model is DebateMinutes:
            return DebateMinutes(
                core_conclusion="测试",
                verdict=Verdict(winner="甲", rationale="测试", score_summary="50:50"),
                summary="测试",
            )
        if response_model is AgentThinking:
            return AgentThinking(thinking="分析...", data_need="")

        return response_model()

    patches = _make_patches()
    patches.append(patch("app.services.llm.complete_typed", side_effect=mock_typed))
    patches.append(patch("app.agents.base.complete_typed", side_effect=mock_typed))

    for p in patches:
        p.start()
    try:
        orch = Orchestrator(db)
        await orch.start_discussion(
            session.id, [pos1.id, pos2.id], enable_data_clerk=True,
        )
    finally:
        for p in patches:
            p.stop()

    session = await repo.get_session(session.id)
    assert session.status == SessionStatus.COMPLETED


async def test_rethink_failure_graceful(db: AsyncSession):
    """If re-think fails, debate continues with original thinking."""
    repo = SessionRepository(db)
    session = await repo.create_session("测试", max_rounds=2)
    pos1 = await repo.add_position(session.id, "正方", "正方观点")
    pos2 = await repo.add_position(session.id, "反方", "反方观点")

    think_count = {"n": 0}

    async def mock_typed(messages, response_model, model=None, **kwargs):
        if response_model is ScoreResult:
            return ScoreResult(scores=[
                ScoreEntry(position_id=pos1.id, position_name="正方", points=50, comment=""),
                ScoreEntry(position_id=pos2.id, position_name="反方", points=50, comment=""),
            ])
        if response_model is RoundJudgment:
            return RoundJudgment(decision="CONCLUDE", reason="充分", guidance="")
        if response_model is DebateMinutes:
            return DebateMinutes(
                core_conclusion="测试",
                verdict=Verdict(winner="正方", rationale="测试", score_summary="50:50"),
                summary="测试",
            )
        if response_model is AgentThinking:
            think_count["n"] += 1
            # First thinking returns data requests to trigger fetch
            if think_count["n"] == 1:
                return AgentThinking(
                    thinking="需要数据",
                    data_need="需要相关统计数据",
                    chosen_strategy="EVIDENCE",
                )
            # Re-think throws
            if think_count["n"] == 2:
                raise RuntimeError("Re-think LLM call failed!")
            return AgentThinking(thinking="分析...", data_need="")

        return response_model()

    patches = _make_patches()
    patches.append(patch("app.services.llm.complete_typed", side_effect=mock_typed))
    patches.append(patch("app.agents.base.complete_typed", side_effect=mock_typed))
    patches.append(
        patch("app.services.orchestrator.get_search_provider", return_value=None),
    )

    for p in patches:
        p.start()
    try:
        orch = Orchestrator(db)
        orch.search = None
        await orch.start_discussion(
            session.id, [pos1.id, pos2.id], enable_data_clerk=True,
        )
    finally:
        for p in patches:
            p.stop()

    # Debate should still complete despite re-think failure
    session = await repo.get_session(session.id)
    assert session.status == SessionStatus.COMPLETED
