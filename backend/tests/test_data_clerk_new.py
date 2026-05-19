"""Tests for screen_results and iterative search in data_clerk."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.data_clerk import DataClerkAgent
from app.models.schemas import (
    CrossValidatedFacts,
    NeedDecomposition,
    PoolSufficiency,
    RecencyDecision,
    RefinementQueries,
    ResearchOutcome,
    ResearchPlan,
    ResearchStep,
    ScreenedResults,
    TopicDecomposition,
    TopicEntity,
    HiddenSubTopic,
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
        outcome = await clerk.research_with_validation("topic", provider)
        assert len(outcome.validation.validated) == 2
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
        outcome = await clerk.research_with_validation("topic", provider)
        assert len(outcome.validation.validated) >= 1
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
        outcome = await clerk.research_with_validation(
            "topic", provider, max_iterations=2,
        )
        assert outcome.public_results is not None
        assert len(outcome.validation.validated) == 0


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
        outcome = await clerk.research_with_validation("topic", provider)
        assert mock_typed.called


# --- full pipeline tests ---


async def test_research_with_validation_no_results():
    """When research returns no results, should return empty."""
    clerk = DataClerkAgent()
    with patch.object(clerk, "research_topic", new_callable=AsyncMock) as mock_research:
        mock_research.return_value = []
        provider = MagicMock()
        outcome = await clerk.research_with_validation("topic", provider)
        assert outcome.public_results == []
        assert outcome.validation.validated == []


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
        outcome = await clerk.research_with_validation("topic", provider)
        all_results = outcome.public_results + outcome.private_results
        urls = [r.get("url") for r in all_results]
        assert urls.count("https://a.com") == 1
        assert "https://b.com" in urls


# --- research_for_agent tests (Semantic Intent Protocol) ---


async def test_research_for_agent_pool_sufficient():
    """Pool check returns sufficient=true → no search needed."""
    clerk = DataClerkAgent()
    provider = MagicMock()
    provider.search = AsyncMock(return_value=[])

    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        mock_typed.side_effect = [
            RecencyDecision(needs_recent=False, recency="noLimit", reasoning="general"),
            PoolSufficiency(sufficient=True, reasoning="数据池已有足够信息"),
        ]
        outcome = await clerk.research_for_agent(
            "topic", "需要具体数据", provider,
            pool_summary="[1] 已有数据 - 摘要",
        )
        assert outcome.public_results == []
        provider.search.assert_not_called()


async def test_research_for_agent_no_queries():
    """Decomposition returns empty → return empty."""
    clerk = DataClerkAgent()
    provider = MagicMock()
    provider.search = AsyncMock(return_value=[])

    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        # No pool_summary → pool check is skipped
        # RecencyDecision + decomposition (empty)
        mock_typed.side_effect = [
            RecencyDecision(needs_recent=False, recency="noLimit", reasoning="general"),
            NeedDecomposition(queries=[], reasoning="无法分解"),
        ]
        outcome = await clerk.research_for_agent(
            "topic", "需要什么", provider,
            pool_summary="",
        )
        assert outcome.public_results == []


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
        # RecencyDecision + NeedDecomposition
        mock_typed.side_effect = [
            RecencyDecision(needs_recent=True, recency="oneWeek", reasoning="sports"),
            NeedDecomposition(queries=["Lakers score", "湖人比分"], reasoning="search"),
        ]
        mock_screen.return_value = [{"title": "NBA", "snippet": "110-105", "url": "https://nba.com"}]
        mock_extract.side_effect = lambda r, *a, **kw: r
        mock_validate.return_value = CrossValidatedFacts(
            validated=[{"fact": "Lakers 110-105", "source_count": 1}],
        )
        outcome = await clerk.research_for_agent(
            "topic", "湖人最新比分", provider,
        )
        all_results = outcome.public_results + outcome.private_results
        assert len(all_results) >= 1


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
        # RecencyDecision + NeedDecomposition calls
        mock_typed.side_effect = [
            RecencyDecision(needs_recent=True, recency="oneWeek", reasoning="sports"),
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
        outcome = await clerk.research_for_agent(
            "topic", "詹姆斯得分篮板", provider,
        )
        all_results = outcome.public_results + outcome.private_results
        assert len(all_results) >= 1
        assert len(outcome.validation.validated) >= 1


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
        # RecencyDecision + NeedDecomposition calls
        mock_typed.side_effect = [
            RecencyDecision(needs_recent=False, recency="noLimit", reasoning="general"),
            NeedDecomposition(queries=["q1"], reasoning="try"),
            NeedDecomposition(queries=["q2"], reasoning="retry"),
        ]
        mock_screen.return_value = [{"title": "A", "snippet": "s", "url": "https://a.com"}]
        mock_extract.side_effect = lambda r, *a, **kw: r
        mock_validate.return_value = CrossValidatedFacts(validated=[])
        outcome = await clerk.research_for_agent(
            "topic", "需要什么", provider, max_iterations=2,
        )
        all_results = outcome.public_results + outcome.private_results
        assert all_results is not None


async def test_research_for_agent_search_failure():
    """Search provider fails gracefully."""
    clerk = DataClerkAgent()
    provider = MagicMock()
    provider.search = AsyncMock(side_effect=Exception("search failed"))

    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        mock_typed.side_effect = [
            RecencyDecision(needs_recent=False, recency="noLimit", reasoning="general"),
            NeedDecomposition(queries=["query1"], reasoning="try"),
        ]
        outcome = await clerk.research_for_agent(
            "topic", "需要数据", provider,
        )
        assert outcome.public_results == []


async def test_research_for_agent_empty_need():
    """Empty semantic_need → return empty."""
    clerk = DataClerkAgent()
    provider = MagicMock()
    outcome = await clerk.research_for_agent(
        "topic", "", provider,
    )
    assert outcome.public_results == []
    assert outcome.validation.validated == []
    provider.search.assert_not_called()


async def test_collect_facts_text():
    """Helper correctly extracts facts from enriched results."""
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


# --- topic decomposition tests ---


async def test_decompose_topic_extracts_entities():
    """_decompose_topic should extract entities and hidden sub-topics."""
    clerk = DataClerkAgent()
    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        mock_typed.return_value = TopicDecomposition(
            entities=[
                TopicEntity(name="赖斯", entity_type="球员", aliases=["Declan Rice"]),
            ],
            hidden_sub_topics=[
                HiddenSubTopic(
                    question="赖斯最近一场比赛是哪场？",
                    depends_on=["赖斯"],
                    resolution_strategy="先搜索赖斯最近比赛",
                ),
            ],
            search_strategy_hint="先确定具体比赛，再搜该场比赛数据",
        )
        result = await clerk._decompose_topic("赖斯上一场比赛表现如何")
        assert len(result.entities) == 1
        assert result.entities[0].name == "赖斯"
        assert "Declan Rice" in result.entities[0].aliases
        assert len(result.hidden_sub_topics) == 1
        assert "赖斯最近" in result.hidden_sub_topics[0].question


async def test_decompose_topic_empty():
    """When no sub-topics exist, decomposition returns empty lists."""
    clerk = DataClerkAgent()
    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        mock_typed.return_value = TopicDecomposition(
            entities=[
                TopicEntity(name="特斯拉", entity_type="公司", aliases=["Tesla"]),
            ],
        )
        result = await clerk._decompose_topic("特斯拉Q1财报表现")
        assert len(result.entities) == 1
        assert result.hidden_sub_topics == []


async def test_decompose_topic_failure():
    """On LLM failure, should return empty TopicDecomposition."""
    clerk = DataClerkAgent()
    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        mock_typed.side_effect = Exception("LLM timeout")
        result = await clerk._decompose_topic("任何议题")
        assert result.entities == []
        assert result.hidden_sub_topics == []


# --- research_topic with decomposition tests ---


async def test_research_topic_with_decomposition():
    """research_topic should use decomposition to inform plan and queries."""
    clerk = DataClerkAgent()

    # Mock decomposition → plan → search
    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        # Call 1: _decompose_topic
        # Call 2: ResearchPlan generation
        mock_typed.side_effect = [
            TopicDecomposition(
                entities=[
                    TopicEntity(name="赖斯", entity_type="球员", aliases=["Declan Rice"]),
                ],
                hidden_sub_topics=[
                    HiddenSubTopic(
                        question="赖斯最近一场比赛是哪场？",
                        resolution_strategy="先搜索赖斯最近比赛",
                    ),
                ],
            ),
            ResearchPlan(steps=[
                ResearchStep(
                    reasoning="搜索赖斯最近比赛",
                    search_queries=["赖斯 最新比赛", "Declan Rice latest", "赖斯 战绩", "Declan Rice stats"],
                ),
                ResearchStep(
                    reasoning="搜索那场比赛的数据",
                    search_queries=["赖斯 上一场 数据"],
                ),
            ]),
            # Step 1 adjustment: discovers match info
            ResearchStep(
                reasoning="发现是5月10日阿森纳vs西汉姆",
                search_queries=["阿森纳 西汉姆 5月10日 战报"],
                discovered_entities=["5月10日", "西汉姆联", "阿森纳", "英超第36轮"],
                discovered_facts=["赖斯所在阿森纳5月10日1-0胜西汉姆", "英超第36轮"],
                resolved_sub_topic="赖斯最近一场比赛是5月10日阿森纳vs西汉姆",
            ),
        ]

        provider = MagicMock()
        provider.search = AsyncMock(return_value=[
            SearchResult("阿森纳1-0西汉姆", "赖斯在比赛中进球", "https://a.com"),
        ])

        results = await clerk.research_topic(
            "赖斯上一场比赛表现如何", provider, max_steps=2,
        )
        assert len(results) > 0

        # Verify decomposition was called
        assert mock_typed.call_count == 3  # decompose + plan + step1 adjustment

        # Verify plan generation received decomposition hint
        plan_call = mock_typed.call_args_list[1]
        plan_msg = plan_call[1].get("user_message", "")
        assert "赖斯" in plan_msg
        assert "子问题" in plan_msg

        # Verify step adjustment received previous step results and entity-switching prompt
        adjust_call = mock_typed.call_args_list[2]
        adjust_msg = adjust_call[1].get("user_message", "")
        assert "可以不包含主要实体名" in adjust_msg


async def test_research_topic_backward_compatible():
    """When decomposition returns nothing, research_topic still works."""
    clerk = DataClerkAgent()
    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        mock_typed.side_effect = [
            TopicDecomposition(),  # empty decomposition
            ResearchPlan(steps=[
                ResearchStep(
                    reasoning="搜索信息",
                    search_queries=["测试 搜索"],
                ),
            ]),
        ]

        provider = MagicMock()
        provider.search = AsyncMock(return_value=[
            SearchResult("结果", "摘要", "https://a.com"),
        ])

        results = await clerk.research_topic("简单议题", provider, max_steps=1)
        assert len(results) > 0


async def test_research_topic_step_adjustment_extracts_facts():
    """Step adjustment should populate discovered_facts for context."""
    clerk = DataClerkAgent()
    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        mock_typed.side_effect = [
            TopicDecomposition(),
            ResearchPlan(steps=[
                ResearchStep(
                    reasoning="第一步",
                    search_queries=["q1", "q2"],
                ),
                ResearchStep(
                    reasoning="第二步",
                    search_queries=["original"],
                ),
            ]),
            ResearchStep(
                reasoning="基于第一步发现调整",
                search_queries=["adjusted1"],
                discovered_facts=["阿森纳5月10日1-0西汉姆", "赖斯进球"],
                discovered_entities=["5月10日", "西汉姆联"],
                resolved_sub_topic="赖斯上一场是5月10日阿森纳vs西汉姆",
            ),
        ]

        provider = MagicMock()
        provider.search = AsyncMock(return_value=[
            SearchResult("标题", "摘要内容", "https://a.com"),
        ])

        results = await clerk.research_topic("赖斯上一场比赛", provider, max_steps=2)
        assert len(results) > 0

        # The second respond_typed call's context should contain structured info
        adjust_call = mock_typed.call_args_list[2]
        context = adjust_call[1].get("context", "")
        # Step 0 results are in context as raw snippets
        assert "标题" in context or "赖斯" in context


# --- Search keyword deduplication tests ---


def test_normalize_query_basic():
    """Normalize query lowercases and strips whitespace."""
    from app.agents.data_clerk import _normalize_query
    assert _normalize_query("  勒布朗 最新  ") == "勒布朗 最新"
    assert _normalize_query("LeBron James Stats") == "lebron james stats"


def test_normalize_query_collapses_spaces():
    """Normalize query collapses multiple spaces."""
    from app.agents.data_clerk import _normalize_query
    assert _normalize_query("勒布朗   G4  关键") == "勒布朗 g4 关键"


async def test_research_for_agent_skips_duplicate_queries():
    """research_for_agent should skip queries already in searched_queries set."""
    clerk = DataClerkAgent()

    decomposition = NeedDecomposition(
        queries=["勒布朗 G4 关键节点得分", "LeBron James Game 4 clutch stats"],
        reasoning="Need clutch stats",
    )
    pool_check = PoolSufficiency(sufficient=False, reasoning="Need more")

    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        mock_typed.side_effect = [pool_check, decomposition]

        provider = MagicMock()
        provider.search = AsyncMock(return_value=[])

        searched = {"勒布朗 g4 关键节点得分"}  # Already searched the Chinese query

        outcome = await clerk.research_for_agent(
            "勒布朗G4表现", "勒布朗G4关键数据", provider,
            searched_queries=searched,
        )

        # Only the English query should have been searched
        searched_queries_actual = [call.args[0] for call in provider.search.call_args_list]
        assert len(searched_queries_actual) == 1
        assert "LeBron James Game 4 clutch stats" in searched_queries_actual[0]

        # The skipped query should NOT be in the set (it was already there)
        # But the new query should be added
        assert "lebron james game 4 clutch stats" in searched


async def test_research_for_agent_all_duplicates_returns_empty():
    """When all queries are duplicates, research_for_agent returns empty outcome."""
    clerk = DataClerkAgent()

    decomposition = NeedDecomposition(
        queries=["勒布朗 G4 关键节点得分", "LeBron James Game 4 clutch stats"],
        reasoning="Need clutch stats",
    )
    pool_check = PoolSufficiency(sufficient=False, reasoning="Need more")

    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        mock_typed.side_effect = [pool_check, decomposition]

        provider = MagicMock()
        provider.search = AsyncMock(return_value=[])

        # Both queries already searched
        searched = {
            "勒布朗 g4 关键节点得分",
            "lebron james game 4 clutch stats",
        }

        outcome = await clerk.research_for_agent(
            "勒布朗G4表现", "勒布朗G4关键数据", provider,
            searched_queries=searched,
        )

        # No searches should have been executed
        provider.search.assert_not_called()

        # Outcome should be empty
        assert outcome.public_results == []
        assert outcome.private_results == []


async def test_research_for_agent_none_searched_queries():
    """Passing None for searched_queries should work (backward compat)."""
    clerk = DataClerkAgent()
    decomposition = NeedDecomposition(
        queries=["测试关键词"],
        reasoning="test",
    )
    pool_check = PoolSufficiency(sufficient=False, reasoning="need data")

    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        mock_typed.side_effect = [pool_check, decomposition]

        provider = MagicMock()
        provider.search = AsyncMock(return_value=[])

        outcome = await clerk.research_for_agent(
            "测试议题", "测试需求", provider,
            searched_queries=None,
        )

        # Query should execute (no dedup set)
        provider.search.assert_called_once()


async def test_dedup_across_two_research_calls():
    """searched_queries set should accumulate across multiple research_for_agent calls."""
    clerk = DataClerkAgent()

    decomp1 = NeedDecomposition(queries=["query A"], reasoning="r1")
    decomp2 = NeedDecomposition(queries=["query A", "query B"], reasoning="r2")
    pool_check = PoolSufficiency(sufficient=False, reasoning="need data")

    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_typed:
        provider = MagicMock()
        provider.search = AsyncMock(return_value=[])

        # First call
        searched: set[str] = set()
        mock_typed.side_effect = [PoolSufficiency(sufficient=False, reasoning=""), decomp1]
        await clerk.research_for_agent(
            "topic", "need", provider, searched_queries=searched,
        )

        # "query a" should now be in the set
        assert "query a" in searched

        # Second call with overlapping query
        mock_typed.side_effect = [PoolSufficiency(sufficient=False, reasoning=""), decomp2]
        await clerk.research_for_agent(
            "topic", "need2", provider, searched_queries=searched,
        )

        # Only "query B" should have been searched in the second call
        second_call_queries = [
            call.args[0] for call in provider.search.call_args_list[1:]
        ]
        assert "query B" in second_call_queries
        # "query A" was already in searched, so it should NOT be searched again
        assert "query A" not in second_call_queries


# --- _facts_match fuzzy matching tests ---


def test_facts_match_exact_substring():
    """Exact substring should match."""
    assert DataClerkAgent._facts_match(
        "詹姆斯得到24分12篮板",
        "詹姆斯在2026年5月11日湖人对雷霆的G4中得到24分12篮板3助攻",
    )


def test_facts_match_number_overlap():
    """Key stat numbers overlap → match."""
    assert DataClerkAgent._facts_match(
        "詹姆斯全场出战24分12篮板3助攻",
        "詹姆斯在这场比赛中得到24分、12篮板、3助攻和1盖帽",
    )


def test_facts_match_no_match():
    """Completely different facts → no match."""
    assert not DataClerkAgent._facts_match(
        "库里投中8个三分球",
        "詹姆斯得到24分12篮板",
    )


def test_facts_match_empty_strings():
    """Empty strings → no match."""
    assert not DataClerkAgent._facts_match("", "")
    assert not DataClerkAgent._facts_match("some fact", "")
    assert not DataClerkAgent._facts_match("", "validated")


def test_facts_match_short_string():
    """Short strings without enough overlap → no match."""
    assert not DataClerkAgent._facts_match("abc", "abcdef")
    assert not DataClerkAgent._facts_match("24分", "该球员得到24分")  # only 1 number overlap


def test_facts_match_year_excluded():
    """Year-like numbers (2026) should not count for overlap."""
    assert not DataClerkAgent._facts_match(
        "2026年NBA季后赛",
        "2026年总决赛MVP",
    )


def test_facts_match_token_overlap():
    """Token overlap above threshold should match."""
    # "湖人" and "击败火箭" are shared tokens, "110" and "98" overlap numbers
    assert DataClerkAgent._facts_match(
        "湖人客场110-98击败火箭晋级下一轮",
        "湖人客场110比98击败火箭，系列赛大比分4-2",
    )


# --- _map_validated_to_results ---


def test_map_validated_all_go_public_when_validated():
    """When validated facts exist, ALL results go public (even without extracted facts)."""
    enriched = [
        {
            "title": "StatMuse",
            "url": "https://statmuse.com/a",
            "source": "statmuse",
            "key_facts": json.dumps({"key_facts": ["StatMuse fact"], "summary": ""}),
        },
        {
            "title": "球米屋",
            "url": "https://qiumiwu.com/b",
            "source": "page",
            "key_facts": json.dumps({
                "key_facts": ["詹姆斯全场出战24分12篮板3助攻1盖帽"],
                "summary": "",
            }),
        },
        {
            "title": "新浪",
            "url": "https://sina.com/c",
            "source": "page",
            "key_facts": json.dumps({
                "key_facts": ["湖人110-115不敌雷霆"],
                "summary": "",
            }),
        },
        {
            "title": "EmptyFacts",
            "url": "https://empty.com/d",
            "source": "page",
            "key_facts": "",
        },
    ]
    validation = CrossValidatedFacts(
        validated=[{"fact": "詹姆斯在G4中得到24分12篮板", "source_count": 3}],
    )
    public, private = DataClerkAgent._map_validated_to_results(enriched, validation)
    # ALL results go public when validation has validated facts
    assert len(public) == 4
    assert len(private) == 0
    public_urls = [r["url"] for r in public]
    assert "https://statmuse.com/a" in public_urls
    assert "https://qiumiwu.com/b" in public_urls
    assert "https://sina.com/c" in public_urls
    assert "https://empty.com/d" in public_urls


def test_map_validated_no_validated_facts():
    """No validated facts → only StatMuse public."""
    enriched = [
        {
            "title": "StatMuse",
            "url": "https://statmuse.com",
            "source": "statmuse",
            "key_facts": json.dumps({"key_facts": ["sm fact"], "summary": ""}),
        },
        {
            "title": "Other",
            "url": "https://other.com",
            "source": "page",
            "key_facts": json.dumps({"key_facts": ["other fact"], "summary": ""}),
        },
    ]
    validation = CrossValidatedFacts()
    public, private = DataClerkAgent._map_validated_to_results(enriched, validation)
    assert len(public) == 1
    assert public[0]["source"] == "statmuse"


def test_has_extracted_facts():
    """Test _has_extracted_facts helper."""
    # Has facts
    assert DataClerkAgent._has_extracted_facts({
        "key_facts": json.dumps({"key_facts": ["fact1"], "summary": ""}),
    })
    # Empty facts list
    assert not DataClerkAgent._has_extracted_facts({
        "key_facts": json.dumps({"key_facts": [], "summary": ""}),
    })
    # Empty string
    assert not DataClerkAgent._has_extracted_facts({"key_facts": ""})
    # No key
    assert not DataClerkAgent._has_extracted_facts({})
