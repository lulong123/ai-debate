"""Tests for screen_results and iterative search in data_clerk."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.data_clerk import DataClerkAgent
from app.models.schemas import (
    CrossValidatedFacts,
    NeedDecomposition,
    PoolSufficiency,
    RefinementQueries,
    ScreenedResults,
)
from app.services.search import SearchResult


# --- screen_results tests ---


async def test_screen_results_keeps_relevant():
    """Relevant results should pass screening."""
    clerk = DataClerkAgent()
    results = [
        {"title": "NBA", "snippet": "110-105", "url": "https://a.com"},
        {"title": "food", "snippet": "recipe", "url": "https://b.com"},
    ]
    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        mock_typed.return_value = ScreenedResults(
            kept=[results[0]], rejected=["irrelevant"], screening_note="ok",
        )
        kept = await clerk.screen_results(results, "NBA topic")
        assert len(kept) == 1
        assert kept[0]["title"] == "NBA"


async def test_screen_results_rejects_contradictory():
    """Results contradicting known info should be excluded."""
    clerk = DataClerkAgent()
    results = [
        {"title": "James 19pts", "snippet": "Lakers lost", "url": "https://a.com"},
        {"title": "James 0pts", "snippet": "shut down", "url": "https://b.com"},
    ]
    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        mock_typed.return_value = ScreenedResults(
            kept=[results[0]],
            rejected=["0pts contradicts known 19pts"],
            screening_note="excluded contradiction",
        )
        kept = await clerk.screen_results(
            results, "James performance",
            existing_context="James had 19pts 8ast",
        )
        assert len(kept) == 1


async def test_screen_results_fallback_on_error():
    """On LLM error, all results should pass through."""
    clerk = DataClerkAgent()
    results = [
        {"title": "A", "snippet": "s1", "url": "https://a.com"},
        {"title": "B", "snippet": "s2", "url": "https://b.com"},
    ]
    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        mock_typed.side_effect = Exception("LLM timeout")
        kept = await clerk.screen_results(results, "topic")
        assert kept == results


async def test_screen_results_empty_input():
    """Empty input should return empty."""
    clerk = DataClerkAgent()
    kept = await clerk.screen_results([], "topic")
    assert kept == []


async def test_screen_results_all_rejected():
    """When LLM rejects all, should return empty."""
    clerk = DataClerkAgent()
    results = [
        {"title": "weather", "snippet": "sunny", "url": "https://a.com"},
    ]
    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        mock_typed.return_value = ScreenedResults(
            kept=[], rejected=["irrelevant"], screening_note="all excluded",
        )
        kept = await clerk.screen_results(results, "NBA game")
        assert kept == []


# --- iterative loop tests ---


async def test_iterative_no_retry_when_validated():
    """When validated >= 2 on first pass, no retry should happen."""
    clerk = DataClerkAgent()
    with patch.object(clerk, "research_topic", new_callable=AsyncMock) as mock_research, \
         patch.object(clerk, "screen_results", new_callable=AsyncMock) as mock_screen, \
         patch.object(clerk, "extract_facts_batch", new_callable=AsyncMock) as mock_extract, \
         patch.object(clerk, "cross_validate_facts", new_callable=AsyncMock) as mock_validate:
        mock_research.return_value = [
            {"title": "A", "url": "https://a.com"},
            {"title": "B", "url": "https://b.com"},
        ]
        mock_screen.return_value = mock_research.return_value
        mock_extract.side_effect = lambda r, *a, **kw: r
        mock_validate.return_value = CrossValidatedFacts(
            validated=[
                {"fact": "f1", "source_count": 2},
                {"fact": "f2", "source_count": 2},
            ],
        )
        provider = MagicMock()
        results, validation = await clerk.research_with_validation("topic", provider)
        assert len(validation.validated) == 2
        assert mock_validate.call_count == 1


async def test_iterative_one_retry():
    """First pass validated < 2 should trigger one retry that succeeds."""
    clerk = DataClerkAgent()
    with patch.object(clerk, "research_topic", new_callable=AsyncMock) as mock_research, \
         patch.object(clerk, "screen_results", new_callable=AsyncMock) as mock_screen, \
         patch.object(clerk, "extract_facts_batch", new_callable=AsyncMock) as mock_extract, \
         patch.object(clerk, "cross_validate_facts", new_callable=AsyncMock) as mock_validate, \
         patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        mock_research.return_value = [{"title": "A", "url": "https://a.com"}]
        mock_screen.return_value = [{"title": "A", "url": "https://a.com"}]
        mock_extract.side_effect = lambda r, *a, **kw: r
        mock_validate.side_effect = [
            CrossValidatedFacts(validated=[], unique=[{"fact": "f1", "source": "A"}]),
            CrossValidatedFacts(validated=[
                {"fact": "f1", "source_count": 2},
                {"fact": "f2", "source_count": 2},
            ]),
        ]
        mock_typed.return_value = RefinementQueries(
            queries=["q1"], reasoning="test", focus="test",
        )
        provider = MagicMock()
        provider.search = AsyncMock(return_value=[
            SearchResult("B", "sb", "https://b.com"),
        ])
        results, validation = await clerk.research_with_validation("topic", provider)
        assert len(validation.validated) >= 1
        assert mock_validate.call_count == 2


async def test_iterative_max_rounds():
    """After max iterations with validated < 2, return best available."""
    clerk = DataClerkAgent()
    with patch.object(clerk, "research_topic", new_callable=AsyncMock) as mock_research, \
         patch.object(clerk, "screen_results", new_callable=AsyncMock) as mock_screen, \
         patch.object(clerk, "extract_facts_batch", new_callable=AsyncMock) as mock_extract, \
         patch.object(clerk, "cross_validate_facts", new_callable=AsyncMock) as mock_validate, \
         patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        mock_research.return_value = [{"title": "A", "url": "https://a.com"}]
        mock_screen.return_value = [{"title": "A", "url": "https://a.com"}]
        mock_extract.side_effect = lambda r, *a, **kw: r
        mock_validate.return_value = CrossValidatedFacts(validated=[])
        mock_typed.return_value = RefinementQueries(
            queries=["q1"], reasoning="test", focus="test",
        )
        provider = MagicMock()
        provider.search = AsyncMock(return_value=[])
        results, validation = await clerk.research_with_validation(
            "topic", provider, max_iterations=2,
        )
        assert results is not None
        assert len(validation.validated) == 0


async def test_iterative_refinement_targets_contradiction():
    """Refinement queries should include contradiction details."""
    clerk = DataClerkAgent()
    with patch.object(clerk, "research_topic", new_callable=AsyncMock) as mock_research, \
         patch.object(clerk, "screen_results", new_callable=AsyncMock) as mock_screen, \
         patch.object(clerk, "extract_facts_batch", new_callable=AsyncMock) as mock_extract, \
         patch.object(clerk, "cross_validate_facts", new_callable=AsyncMock) as mock_validate, \
         patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        mock_research.return_value = [{"title": "A", "url": "https://a.com"}]
        mock_screen.return_value = [{"title": "A", "url": "https://a.com"}]
        mock_extract.side_effect = lambda r, *a, **kw: r
        mock_validate.side_effect = [
            CrossValidatedFacts(
                validated=[],
                contradictions=[{"conflicting_facts": ["A says 10", "B says 20"]}],
            ),
            CrossValidatedFacts(
                validated=[
                    {"fact": "15pts", "source_count": 2},
                    {"fact": "5reb", "source_count": 2},
                ],
            ),
        ]
        mock_typed.return_value = RefinementQueries(
            queries=["latest game data"],
            reasoning="verify score",
            focus="score contradiction",
        )
        provider = MagicMock()
        provider.search = AsyncMock(return_value=[
            SearchResult("C", "15pts 5reb", "https://c.com"),
        ])
        results, validation = await clerk.research_with_validation("topic", provider)
        assert mock_typed.called


# --- full pipeline tests ---


async def test_research_with_validation_no_results():
    """When research returns no results, should return empty."""
    clerk = DataClerkAgent()
    with patch.object(clerk, "research_topic", new_callable=AsyncMock) as mock_research:
        mock_research.return_value = []
        provider = MagicMock()
        results, validation = await clerk.research_with_validation("topic", provider)
        assert results == []
        assert validation.validated == []


async def test_merge_deduplication():
    """Same URL in round 1 and round 2 should only be kept once."""
    clerk = DataClerkAgent()
    r1 = {"title": "A", "url": "https://a.com", "key_facts": "{}"}
    r2 = {"title": "A2", "url": "https://a.com", "key_facts": "{}"}
    r3 = {"title": "B", "url": "https://b.com", "key_facts": "{}"}
    with patch.object(clerk, "research_topic", new_callable=AsyncMock) as mock_research, \
         patch.object(clerk, "screen_results", new_callable=AsyncMock) as mock_screen, \
         patch.object(clerk, "extract_facts_batch", new_callable=AsyncMock) as mock_extract, \
         patch.object(clerk, "cross_validate_facts", new_callable=AsyncMock) as mock_validate, \
         patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        mock_research.return_value = [r1]
        mock_screen.side_effect = [[r1], [r2, r3]]
        mock_extract.side_effect = lambda r, *a, **kw: r
        mock_validate.side_effect = [
            CrossValidatedFacts(validated=[]),
            CrossValidatedFacts(validated=[
                {"fact": "f1", "source_count": 2},
                {"fact": "f2", "source_count": 2},
            ]),
        ]
        mock_typed.return_value = RefinementQueries(queries=["retry"])
        provider = MagicMock()
        provider.search = AsyncMock(return_value=[
            SearchResult("A2", "s", "https://a.com"),
            SearchResult("B", "s", "https://b.com"),
        ])
        results, validation = await clerk.research_with_validation("topic", provider)
        urls = [r.get("url") for r in results]
        assert urls.count("https://a.com") == 1
        assert "https://b.com" in urls


# --- research_for_agent tests (Semantic Intent Protocol) ---


async def test_research_for_agent_pool_sufficient():
    """Pool check returns sufficient=true → no search needed."""
    clerk = DataClerkAgent()
    provider = MagicMock()
    provider.search = AsyncMock(return_value=[])

    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        mock_typed.return_value = PoolSufficiency(
            sufficient=True, reasoning="数据池已有足够信息",
        )
        results, validation = await clerk.research_for_agent(
            "topic", "需要具体数据", provider,
            pool_summary="[1] 已有数据 - 摘要",
        )
        assert results == []
        assert "已满足" in validation.note
        provider.search.assert_not_called()


async def test_research_for_agent_no_queries():
    """Decomposition returns empty → return empty."""
    clerk = DataClerkAgent()
    provider = MagicMock()
    provider.search = AsyncMock(return_value=[])

    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        # No pool_summary → pool check is skipped
        # First call: decomposition (empty)
        mock_typed.return_value = NeedDecomposition(queries=[], reasoning="无法分解")
        results, validation = await clerk.research_for_agent(
            "topic", "需要什么", provider,
            pool_summary="",
        )
        assert results == []


async def test_research_for_agent_first_round_sufficient():
    """First search produces validated facts."""
    clerk = DataClerkAgent()
    provider = MagicMock()
    provider.search = AsyncMock(return_value=[
        SearchResult("NBA Game", "Lakers 110-105", "https://nba.com"),
    ])

    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed, \
         patch.object(clerk, "screen_results", new_callable=AsyncMock) as mock_screen, \
         patch.object(clerk, "extract_facts_batch", new_callable=AsyncMock) as mock_extract, \
         patch.object(clerk, "cross_validate_facts", new_callable=AsyncMock) as mock_validate:
        # No pool_summary → pool check skipped
        # Only NeedDecomposition now (DataSufficiency removed)
        mock_typed.side_effect = [
            NeedDecomposition(queries=["Lakers score", "湖人比分"], reasoning="search"),
        ]
        mock_screen.return_value = [{"title": "NBA", "snippet": "110-105", "url": "https://nba.com"}]
        mock_extract.side_effect = lambda r, *a, **kw: r
        mock_validate.return_value = CrossValidatedFacts(
            validated=[{"fact": "Lakers 110-105", "source_count": 1}],
        )
        results, validation = await clerk.research_for_agent(
            "topic", "湖人最新比分", provider,
        )
        assert len(results) >= 1


async def test_research_for_agent_iterates_on_gaps():
    """First round no validated facts, second round succeeds."""
    clerk = DataClerkAgent()
    provider = MagicMock()
    provider.search = AsyncMock(return_value=[
        SearchResult("Stats", "35pts 8reb", "https://stats.com"),
    ])

    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed, \
         patch.object(clerk, "screen_results", new_callable=AsyncMock) as mock_screen, \
         patch.object(clerk, "extract_facts_batch", new_callable=AsyncMock) as mock_extract, \
         patch.object(clerk, "cross_validate_facts", new_callable=AsyncMock) as mock_validate:
        # No pool_summary → pool check skipped
        # Only NeedDecomposition calls now (DataSufficiency removed)
        mock_typed.side_effect = [
            NeedDecomposition(queries=["query1"], reasoning="first try"),
            NeedDecomposition(queries=["query2"], reasoning="retry with gaps"),
        ]
        mock_screen.return_value = [{"title": "Stats", "snippet": "35pts", "url": "https://stats.com"}]
        mock_extract.side_effect = lambda r, *a, **kw: r
        # First iteration: no validated → triggers second iteration
        # Second iteration: validated facts found
        mock_validate.side_effect = [
            CrossValidatedFacts(validated=[], unique=[{"fact": "35pts", "source": "Stats"}]),
            CrossValidatedFacts(validated=[{"fact": "35pts 8reb", "source_count": 2}]),
        ]
        results, validation = await clerk.research_for_agent(
            "topic", "詹姆斯得分篮板", provider,
        )
        assert len(results) >= 1
        assert len(validation.validated) >= 1


async def test_research_for_agent_max_iterations():
    """All rounds insufficient, return best available."""
    clerk = DataClerkAgent()
    provider = MagicMock()
    provider.search = AsyncMock(return_value=[
        SearchResult("A", "snippet", "https://a.com"),
    ])

    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed, \
         patch.object(clerk, "screen_results", new_callable=AsyncMock) as mock_screen, \
         patch.object(clerk, "extract_facts_batch", new_callable=AsyncMock) as mock_extract, \
         patch.object(clerk, "cross_validate_facts", new_callable=AsyncMock) as mock_validate:
        # No pool_summary → pool check skipped
        # Only NeedDecomposition calls now (DataSufficiency removed from pipeline)
        mock_typed.side_effect = [
            NeedDecomposition(queries=["q1"], reasoning="try"),
            NeedDecomposition(queries=["q2"], reasoning="retry"),
        ]
        mock_screen.return_value = [{"title": "A", "snippet": "s", "url": "https://a.com"}]
        mock_extract.side_effect = lambda r, *a, **kw: r
        mock_validate.return_value = CrossValidatedFacts(validated=[])
        results, validation = await clerk.research_for_agent(
            "topic", "需要什么", provider, max_iterations=2,
        )
        assert results is not None


async def test_research_for_agent_search_failure():
    """Search provider fails gracefully."""
    clerk = DataClerkAgent()
    provider = MagicMock()
    provider.search = AsyncMock(side_effect=Exception("search failed"))

    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        mock_typed.side_effect = [
            NeedDecomposition(queries=["query1"], reasoning="try"),
        ]
        results, validation = await clerk.research_for_agent(
            "topic", "需要数据", provider,
        )
        assert results == []


async def test_research_for_agent_empty_need():
    """Empty semantic_need → return empty."""
    clerk = DataClerkAgent()
    provider = MagicMock()
    results, validation = await clerk.research_for_agent(
        "topic", "", provider,
    )
    assert results == []
    assert validation.validated == []
    provider.search.assert_not_called()


async def test_collect_facts_text():
    """Helper correctly extracts facts from enriched results."""
    import json
    enriched = [
        {
            "title": "A",
            "key_facts": json.dumps({"key_facts": ["fact1", "fact2"], "summary": ""}),
        },
        {
            "title": "B",
            "key_facts": json.dumps({"key_facts": ["fact3"], "summary": ""}),
        },
        {"title": "C", "key_facts": ""},
    ]
    text = DataClerkAgent._collect_facts_text(enriched)
    assert "- fact1" in text
    assert "- fact2" in text
    assert "- fact3" in text
    assert "fact4" not in text


# --- scope filter tests ---


def test_scope_filter_no_scope():
    """Empty scope → all results pass through."""
    results = [
        {"title": "火箭胜湖人", "snippet": "test", "url": "https://a.com"},
    ]
    assert DataClerkAgent._scope_filter(results, "") == results


def test_scope_filter_no_results():
    """Empty results → empty."""
    assert DataClerkAgent._scope_filter([], "事件：湖人比赛") == []


def test_scope_filter_entity_match():
    """Results mentioning key entities pass through."""
    results = [
        {"title": "雷霆击败湖人", "snippet": "精彩比赛", "url": "https://a.com"},
        {"title": "火箭大胜勇士", "snippet": "哈登爆发", "url": "https://b.com"},
    ]
    scope = "事件：湖人对雷霆的比赛\n关键实体：湖人, 雷霆, 詹姆斯"
    kept = DataClerkAgent._scope_filter(results, scope)
    assert len(kept) == 1
    assert "雷霆" in kept[0]["title"]


def test_scope_filter_event_match():
    """Results matching event description pass."""
    results = [
        {"title": "湖人对雷霆战报", "snippet": "NBA比赛", "url": "https://a.com"},
        {"title": "火箭4-2胜湖人", "snippet": "不同比赛", "url": "https://b.com"},
    ]
    scope = "事件：湖人对雷霆的比赛\n关键实体：湖人, 雷霆"
    kept = DataClerkAgent._scope_filter(results, scope)
    assert len(kept) == 2  # Both mention 湖人


def test_scope_filter_vs_style():
    """Results with 'vs' in event scope."""
    results = [
        {"title": "Lakers vs Thunder recap", "snippet": "great game", "url": "https://a.com"},
        {"title": "Rockets win series", "snippet": "Houston advances", "url": "https://b.com"},
    ]
    scope = "事件：Lakers vs Thunder\n关键实体：Lakers, Thunder"
    kept = DataClerkAgent._scope_filter(results, scope)
    assert len(kept) == 1
    assert "Lakers" in kept[0]["title"]


def test_scope_filter_all_filtered():
    """When all results are out of scope, returns empty."""
    results = [
        {"title": "勇士夺冠", "snippet": "库里绝杀", "url": "https://a.com"},
        {"title": "凯尔特人连胜", "snippet": "塔图姆30分", "url": "https://b.com"},
    ]
    scope = "事件：湖人对雷霆的比赛\n关键实体：湖人, 雷霆, 詹姆斯"
    kept = DataClerkAgent._scope_filter(results, scope)
    assert kept == []
