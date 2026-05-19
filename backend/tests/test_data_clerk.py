"""Tests for the data clerk agent and related integration."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import json

from app.agents.data_clerk import MAX_QUERIES, MAX_TOTAL_RESULTS, DataClerkAgent, format_result_with_facts
from app.models.schemas import (
    AgentThinking,
    CrossValidatedFacts,
    DebateMinutes,
    ExtractedFacts,
    RoundJudgment,
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
        if response_model is AgentThinking:
            return AgentThinking(
                thinking="分析当前辩论局势...",
                data_need="需要测试数据",
            )
        if response_model is SearchQueries:
            return SearchQueries(searches=["测试查询"])
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


# --- Extraction pipeline tests ---


async def test_extract_facts_success():
    """extract_facts should fetch page content and extract structured facts via LLM."""
    clerk = DataClerkAgent()
    with patch("app.agents.data_clerk.fetch_page_content", new_callable=AsyncMock) as mock_fetch, \
         patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        mock_fetch.return_value = "哈登在今天的比赛中得到35分8篮板10助攻。火箭队以120-115获胜。"
        mock_typed.return_value = ExtractedFacts(
            key_facts=["哈登得到35分8篮板10助攻", "火箭队以120-115获胜"],
            summary="哈登全场最佳表现",
        )
        result = await clerk.extract_facts("https://example.com/game", "哈登 数据")
        assert len(result.key_facts) == 2
        assert "35分" in result.key_facts[0]
        assert result.summary == "哈登全场最佳表现"


async def test_extract_facts_jina_timeout():
    """extract_facts should return empty facts when Jina fetch fails."""
    clerk = DataClerkAgent()
    with patch("app.agents.data_clerk.fetch_page_content", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = ""
        result = await clerk.extract_facts("https://example.com/timeout", "query")
        assert result.key_facts == []
        assert result.summary == ""


async def test_extract_facts_llm_failure():
    """extract_facts should fallback to raw_content when LLM extraction fails."""
    clerk = DataClerkAgent()
    with patch("app.agents.data_clerk.fetch_page_content", new_callable=AsyncMock) as mock_fetch, \
         patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        mock_fetch.return_value = "这是一些网页内容，包含相关信息。"
        mock_typed.side_effect = Exception("LLM failed")
        result = await clerk.extract_facts("https://example.com/page", "query")
        # Fallback: first 200 chars as a single fact
        assert len(result.key_facts) == 1
        assert "网页内容" in result.key_facts[0]


async def test_extract_facts_batch_parallel():
    """extract_facts_batch should process multiple URLs in parallel."""
    clerk = DataClerkAgent()
    results = [
        {"title": "Article 1", "snippet": "s1", "url": "https://a.com/1"},
        {"title": "Article 2", "snippet": "s2", "url": "https://b.com/2"},
        {"title": "No URL", "snippet": "s3", "url": ""},
    ]

    async def mock_extract_facts(url, query, fallback_content="", topic=""):
        if "a.com" in url:
            return ExtractedFacts(key_facts=["fact A1", "fact A2"], summary="Summary A")
        return ExtractedFacts(key_facts=["fact B1"], summary="Summary B")

    with patch.object(clerk, "extract_facts", side_effect=mock_extract_facts):
        enriched = await clerk.extract_facts_batch(results, "test query")
    assert len(enriched) == 3
    # First two have key_facts enriched
    assert json.loads(enriched[0]["key_facts"])["key_facts"] == ["fact A1", "fact A2"]
    assert json.loads(enriched[1]["key_facts"])["key_facts"] == ["fact B1"]
    # Third has no URL but has snippet fallback, so it also gets extraction
    assert "key_facts" in enriched[2]


async def test_cross_validate_matching_facts():
    """cross_validate_facts should identify validated facts from multiple sources."""
    clerk = DataClerkAgent()
    enriched = [
        {
            "title": "Source A",
            "key_facts": json.dumps({"key_facts": ["哈登得到35分", "火箭获胜"], "summary": ""}),
        },
        {
            "title": "Source B",
            "key_facts": json.dumps({"key_facts": ["哈登砍下35分", "火箭队赢球"], "summary": ""}),
        },
    ]
    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        mock_typed.return_value = CrossValidatedFacts(
            validated=[{"fact": "哈登得到35分", "source_count": 2}],
            unique=[],
            contradictions=[],
            note="两个来源均确认哈登35分",
        )
        result = await clerk.cross_validate_facts(enriched, "哈登 数据")
        assert len(result.validated) == 1
        assert result.validated[0]["source_count"] == 2


async def test_cross_validate_empty():
    """cross_validate_facts should return empty result when no facts available."""
    clerk = DataClerkAgent()
    enriched = [
        {"title": "Empty", "key_facts": ""},
        {"title": "No facts", "key_facts": json.dumps({"key_facts": [], "summary": ""})},
    ]
    result = await clerk.cross_validate_facts(enriched, "query")
    assert result.validated == []
    assert result.unique == []


async def test_cross_validate_failure():
    """cross_validate_facts should return empty result on LLM failure."""
    clerk = DataClerkAgent()
    enriched = [
        {
            "title": "Source",
            "key_facts": json.dumps({"key_facts": ["fact1"], "summary": ""}),
        },
    ]
    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        mock_typed.side_effect = Exception("LLM error")
        result = await clerk.cross_validate_facts(enriched, "query")
        assert result.validated == []


async def test_data_pool_item_with_key_facts(db: AsyncSession):
    """DataPoolItem should persist and retrieve key_facts field."""
    repo = SessionRepository(db)
    session = await repo.create_session("test topic")
    key_facts_json = json.dumps(
        {"key_facts": ["fact1", "fact2"], "summary": "test summary"},
        ensure_ascii=False,
    )
    item = await repo.add_data_pool_item(
        session_id=session.id,
        source="data_clerk",
        title="Test Article",
        snippet="Test snippet",
        url="https://example.com",
        key_facts=key_facts_json,
    )
    assert item.key_facts == key_facts_json
    # Verify to_dict includes key_facts
    d = item.to_dict()
    assert d["key_facts"] == key_facts_json


# --- format_result_with_facts tests ---


def test_format_result_with_facts_has_facts():
    """Should show structured bullet points when key_facts are available."""
    r = {
        "title": "哈登35分8板10助",
        "snippet": "NBA常规赛...",
        "url": "https://example.com/game",
        "key_facts": json.dumps({
            "key_facts": ["哈登得到35分8篮板10助攻", "火箭120-115获胜"],
            "summary": "哈登全场最佳",
        }),
    }
    result = format_result_with_facts(r)
    assert "哈登得到35分" in result
    assert "·" in result
    assert "火箭120-115" in result
    assert "来源" in result
    # Should NOT show raw snippet
    assert "NBA常规赛" not in result


def test_format_result_with_facts_no_facts():
    """Should fall back to title + snippet when no key_facts."""
    r = {
        "title": "NBA战报",
        "snippet": "哈登35分",
        "url": "https://example.com",
    }
    result = format_result_with_facts(r)
    assert "NBA战报" in result
    assert "哈登35分" in result
    assert "来源" in result


def test_format_result_with_facts_empty_facts():
    """Should fall back to snippet when key_facts is empty list."""
    r = {
        "title": "Test",
        "snippet": "snippet text",
        "url": "",
        "key_facts": json.dumps({"key_facts": [], "summary": ""}),
    }
    result = format_result_with_facts(r)
    assert "snippet text" in result


def test_format_result_with_facts_bad_json():
    """Should fall back gracefully when key_facts JSON is malformed."""
    r = {
        "title": "Broken",
        "snippet": "fallback text",
        "key_facts": "not valid json{{{",
    }
    result = format_result_with_facts(r)
    assert "fallback text" in result


def test_format_result_with_facts_no_url():
    """Should work without URL."""
    r = {
        "title": "No URL Article",
        "snippet": "some content",
        "key_facts": json.dumps({"key_facts": ["fact1"], "summary": ""}),
    }
    result = format_result_with_facts(r)
    assert "来源" not in result
    assert "fact1" in result


# --- _filter_by_relevance tests ---


def test_filter_by_relevance_keeps_matching():
    """Should keep results whose facts contain keywords from relevant_facts."""
    results = [
        {
            "title": "G3战报",
            "key_facts": json.dumps({
                "key_facts": ["湖人108-131负雷霆，詹姆斯19分6板8助", "系列赛0-3落后"],
            }),
        },
        {
            "title": "G2战报",
            "key_facts": json.dumps({
                "key_facts": ["哈登37分8板9助，雷霆109-104胜湖人"],
            }),
        },
    ]
    # "詹姆斯19分" only appears in G3, "哈登37分" only appears in G2
    relevant_facts = ["詹姆斯19分6板8助"]
    kept = DataClerkAgent._filter_by_relevance(results, relevant_facts)
    assert len(kept) == 1
    assert kept[0]["title"] == "G3战报"


def test_filter_by_relevance_keeps_no_facts():
    """Should keep results without key_facts (no extraction happened)."""
    results = [
        {"title": "Raw result", "snippet": "no extraction"},
    ]
    kept = DataClerkAgent._filter_by_relevance(results, ["something"])
    assert len(kept) == 1


def test_filter_by_relevance_empty_relevant():
    """Should return all results when relevant_facts is empty."""
    results = [{"title": "A"}, {"title": "B"}]
    kept = DataClerkAgent._filter_by_relevance(results, [])
    assert len(kept) == 2


def test_filter_by_relevance_bad_json():
    """Should keep results with malformed key_facts."""
    results = [
        {"title": "Bad", "key_facts": "not json"},
    ]
    kept = DataClerkAgent._filter_by_relevance(results, ["keyword"])
    assert len(kept) == 1
