"""Tests for the data clerk agent and related integration."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.agents.data_clerk import DataClerkAgent, MAX_QUERIES, MAX_TOTAL_RESULTS
from app.models.schemas import (
    DebateMinutes,
    RoundJudgment,
    ScoreResult,
    ScoreEntry,
    SearchQueries,
    Verdict,
)
from app.models.session import SessionStatus
from app.services.orchestrator import Orchestrator
from app.services.search import SearchResult
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


# --- DataClerkAgent unit tests ---


async def test_decide_queries_returns_max_two():
    """decide_queries should cap at MAX_QUERIES even if LLM returns more."""
    clerk = DataClerkAgent()
    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        mock_typed.return_value = SearchQueries(searches=["q1", "q2", "q3", "q4"])
        queries = await clerk.decide_queries("topic", "context", "pos", 1)
        assert len(queries) == MAX_QUERIES
        assert queries == ["q1", "q2"]


async def test_decide_queries_returns_empty():
    """decide_queries should return [] when LLM says no search needed."""
    clerk = DataClerkAgent()
    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        mock_typed.return_value = SearchQueries(searches=[])
        queries = await clerk.decide_queries("topic", "context", "pos", 1)
        assert queries == []


async def test_fetch_for_agent_caps_results():
    """fetch_for_agent should cap at MAX_TOTAL_RESULTS."""
    clerk = DataClerkAgent()
    search_provider = MagicMock()
    many_results = [SearchResult(f"title{i}", f"snippet{i}", f"url{i}") for i in range(10)]
    search_provider.search = AsyncMock(return_value=many_results)

    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        mock_typed.return_value = SearchQueries(searches=["query1", "query2"])
        results = await clerk.fetch_for_agent("topic", "ctx", "pos", 1, search_provider)
        assert len(results) <= MAX_TOTAL_RESULTS


async def test_fetch_for_agent_no_queries():
    """fetch_for_agent should return [] when no queries are needed."""
    clerk = DataClerkAgent()
    search_provider = MagicMock()
    search_provider.search = AsyncMock()

    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        mock_typed.return_value = SearchQueries(searches=[])
        results = await clerk.fetch_for_agent("topic", "ctx", "pos", 1, search_provider)
        assert results == []
        search_provider.search.assert_not_called()


async def test_fetch_for_agent_handles_search_failure():
    """fetch_for_agent should handle search provider failures gracefully."""
    clerk = DataClerkAgent()
    search_provider = MagicMock()
    search_provider.search = AsyncMock(side_effect=Exception("search failed"))

    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        mock_typed.return_value = SearchQueries(searches=["query1"])
        results = await clerk.fetch_for_agent("topic", "ctx", "pos", 1, search_provider)
        assert results == []


# --- Orchestrator with data clerk ---


async def _mock_stream_gen(text: str):
    for char in text:
        yield char


async def _make_mock_typed(pos1_id, pos2_id):
    """Factory for mock_typed that handles all response_model types."""
    call_count = {"n": 0}

    async def mock_typed(messages, response_model, model=None, **kwargs):
        call_count["n"] += 1
        n = call_count["n"]
        if response_model is SearchQueries:
            return SearchQueries(searches=["测试查询"])
        if response_model is ScoreResult:
            return ScoreResult(scores=[
                ScoreEntry(position_id=pos1_id, position_name="梅西", points=60, comment=""),
                ScoreEntry(position_id=pos2_id, position_name="C罗", points=40, comment=""),
            ])
        if response_model is RoundJudgment:
            return RoundJudgment(decision="CONCLUDE", reason="充分", guidance="")
        if response_model is DebateMinutes:
            return DebateMinutes(
                core_conclusion="梅西获胜",
                position_arguments=[],
                key_clashes=[],
                verdict=Verdict(winner="梅西", rationale="数据更好", score_summary="60:40"),
                summary="梅西获胜。",
            )
        return response_model()

    return mock_typed


async def test_orchestrator_with_data_clerk(db: AsyncSession):
    """Orchestrator should emit data_fetch events when data clerk is enabled."""
    repo = SessionRepository(db)
    session = await repo.create_session("梅西还是C罗", max_rounds=2)
    pos1 = await repo.add_position(session.id, "梅西", "支持梅西")
    pos2 = await repo.add_position(session.id, "C罗", "支持C罗")

    emitted_events = []

    def mock_stream(*args, **kwargs):
        return _mock_stream_gen("这是流式辩论内容。")

    async def mock_complete(*args, **kwargs):
        return "完整回复。"

    async def mock_publish(session_id, event):
        emitted_events.append(event)

    mock_search = MagicMock()
    mock_search.search = AsyncMock(return_value=[
        SearchResult("测试标题", "测试摘要", "https://example.com")
    ])

    mock_typed = await _make_mock_typed(pos1.id, pos2.id)

    patches = [
        patch("app.services.llm.stream_completion", side_effect=mock_stream),
        patch("app.services.llm.complete", side_effect=mock_complete),
        patch("app.services.llm.complete_typed", side_effect=mock_typed),
        patch("app.agents.base.stream_completion", side_effect=mock_stream),
        patch("app.agents.base.complete", side_effect=mock_complete),
        patch("app.agents.base.complete_typed", side_effect=mock_typed),
        patch("app.services.orchestrator.publish", side_effect=mock_publish),
        patch("app.services.orchestrator.get_search_provider", return_value=mock_search),
    ]

    for p in patches:
        p.start()

    try:
        orch = Orchestrator(db)
        await orch.start_discussion(
            session.id, [pos1.id, pos2.id],
            enable_data_clerk=True,
        )
    finally:
        for p in patches:
            p.stop()

    session = await repo.get_session(session.id)
    assert session.status == SessionStatus.COMPLETED
    assert session.has_data_clerk is True

    fetch_starts = [e for e in emitted_events if e.get("type") == "data_fetch_start"]
    fetch_completes = [e for e in emitted_events if e.get("type") == "data_fetch_complete"]
    assert len(fetch_starts) >= 2
    assert len(fetch_completes) >= 2
    for e in fetch_starts:
        assert e["message_id"].startswith("data_")
    for e in fetch_completes:
        assert e["message_id"].startswith("data_")


async def test_orchestrator_without_data_clerk(db: AsyncSession):
    """Orchestrator should work normally when data clerk is disabled (default)."""
    repo = SessionRepository(db)
    session = await repo.create_session("AI监管", max_rounds=2)
    pos1 = await repo.add_position(session.id, "支持", "支持监管")
    pos2 = await repo.add_position(session.id, "反对", "反对监管")

    emitted_events = []

    def mock_stream(*args, **kwargs):
        return _mock_stream_gen("辩论内容。")

    async def mock_complete(*args, **kwargs):
        return "回复。"

    async def mock_publish(session_id, event):
        emitted_events.append(event)

    mock_typed = await _make_mock_typed(pos1.id, pos2.id)

    patches = [
        patch("app.services.llm.stream_completion", side_effect=mock_stream),
        patch("app.services.llm.complete", side_effect=mock_complete),
        patch("app.services.llm.complete_typed", side_effect=mock_typed),
        patch("app.agents.base.stream_completion", side_effect=mock_stream),
        patch("app.agents.base.complete", side_effect=mock_complete),
        patch("app.agents.base.complete_typed", side_effect=mock_typed),
        patch("app.services.orchestrator.publish", side_effect=mock_publish),
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
    assert session.has_data_clerk is False

    fetch_events = [e for e in emitted_events if "data_fetch" in e.get("type", "")]
    assert len(fetch_events) == 0
