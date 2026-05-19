"""Data Clerk agent: fetches real-time data for debate participants."""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Awaitable, Callable

from app.agents.base import BaseAgent, load_prompt
from app.models.schemas import (
    CrossValidatedFacts,
    DataGapDetection,
    NeedDecomposition,
    DataSufficiency,
    ExtractedFacts,
    NeedDecomposition,
    PoolSufficiency,
    RecencyDecision,
    RefinementQueries,
    ResearchOutcome,
    ResearchPlan,
    ResearchStep,
    ScreenedResults,
    SearchQueries,
    TopicDecomposition,
    VerifiedResults,
)
from app.services.search import SearchProvider, SearchResult, fetch_page_content

# Callback type: (queries, results) -> None
# Called after each search batch so callers can emit SSE events.
OnSearchCallback = Callable[[list[str], list[dict]], Awaitable[None]] | None

logger = logging.getLogger(__name__)

MAX_QUERIES = 2           # Max queries per step (steps 1+)
FIRST_STEP_QUERIES = 4    # Step 0 gets more queries for broad coverage
MAX_TOTAL_RESULTS = 10
MAX_EXTRACT_URLS = 10  # All screened results deserve fact extraction

# Semaphore to limit concurrent LLM calls (prevents API rate limiting)
_llm_semaphore = asyncio.Semaphore(2)


def _normalize_query(q: str) -> str:
    """Normalize a search query for dedup comparison.

    Lowercases, strips whitespace, collapses internal spaces,
    removes common Chinese/English search noise words.
    """
    import re
    q = q.lower().strip()
    q = re.sub(r"\s+", " ", q)  # collapse multiple spaces
    return q


def format_result_with_facts(r: dict) -> str:
    """Format a search result for agent consumption, preferring key_facts over raw snippet.

    When key_facts are available, shows structured bullet points.
    Falls back to title + snippet when key_facts are absent or malformed.
    """
    title = r.get("title", "")
    snippet = r.get("snippet", "")
    url = r.get("url", "")
    pub_date = r.get("publish_date", "")

    date_tag = f" ({pub_date})" if pub_date else ""

    key_facts_raw = r.get("key_facts", "")
    if key_facts_raw:
        try:
            parsed = json.loads(key_facts_raw) if isinstance(key_facts_raw, str) else key_facts_raw
            facts = parsed.get("key_facts", [])
            if facts:
                lines = [f"-{date_tag} {title}"]
                for f in facts[:5]:
                    lines.append(f"  · {f}")
                if url:
                    lines.append(f"  来源: {url}")
                return "\n".join(lines)
        except (json.JSONDecodeError, AttributeError):
            pass

    # Fallback to snippet
    line = f"-{date_tag} {title}：{snippet}"
    if url:
        line += f"\n  来源: {url}"
    return line


def _time_awareness_hint() -> str:
    """Build a time-awareness hint from current date for data clerk prompts."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y年%m月%d日")
    season = f"{now.year-1}-{now.year}"
    return (
        f"今天是 {date_str}（星期{'一二三四五六日'[now.weekday()]}），"
        f"{season}赛季。\n"
        "关键词要短：实体名+1个修饰词（如「詹姆斯 最新」「LeBron stats」）。\n"
        "不要把实体+事件+日期+类型全塞进一个关键词。\n"
        "错误：「哈登 05月11日 比赛 数据」→ 正确：「哈登 最新比赛」"
    )


def _scope_hint(data_scope: str) -> str:
    """Build a data scope constraint hint for search queries."""
    if not data_scope:
        return ""
    return (
        f"\n\n【数据边界】\n{data_scope}\n"
        "搜索关键词必须与数据边界匹配，不要搜索边界之外的信息。"
    )


class DataClerkAgent(BaseAgent):
    """数据研究员：不参与辩论，为所有参与者提供公开的事实数据。"""

    def __init__(self):
        from app.config import settings
        model, api_key, base_url = settings.get_model_config("data_clerk")
        super().__init__(
            system_prompt=load_prompt("data_clerk.md"),
            model=model or None,
            api_key=api_key or None,
            base_url=base_url or None,
        )

    async def decide_queries(
        self, topic: str, agent_context: str, position_name: str, round_num: int,
        existing_pool_summary: str = "", data_scope: str = "",
        agent_requests: list[str] | None = None,
    ) -> list[str]:
        """Let LLM decide what search queries are needed for this agent."""
        pool_note = ""
        if existing_pool_summary:
            pool_note = (
                f"\n\n【当前数据池已有信息】\n{existing_pool_summary}\n"
                "如果已有信息足够，不需要重复搜索。只搜索数据池中缺失的最新信息。"
            )
        request_note = ""
        if agent_requests:
            request_note = (
                f"\n\n【辩手请求的数据】\n"
                f"辩手明确需要以下方面的数据：{'、'.join(agent_requests)}\n"
                "优先搜索辩手请求的数据，但如果请求不合理或已有信息覆盖，可以不搜索。"
            )
        result = await self.respond_typed(
            SearchQueries,
            context=agent_context,
            user_message=(
                f"辩论议题：「{topic}」\n"
                f"辩手「{position_name}」正在准备第 {round_num} 轮发言。\n"
                "判断该辩手是否需要搜索最新的事实信息来支撑论点。\n"
                f"{_time_awareness_hint()}{_scope_hint(data_scope)}\n"
                "提出最多 2 个搜索关键词（中英文均可）。\n"
                f"{pool_note}{request_note}\n"
                '输出JSON：{"searches": ["关键词1", "关键词2"]}\n'
                '如果不需要搜索（已有信息足够或没有明确的信息需求）：{"searches": []}'
            ),
        )
        logger.info(
            "Data clerk queries for '%s' round %d: %s",
            position_name, round_num, result.searches[:MAX_QUERIES],
        )
        return result.searches[:MAX_QUERIES]

    async def _decide_recency(
        self, topic: str, search_context: str = "", data_scope: str = "",
    ) -> str:
        """Ask LLM whether this search needs recent data. Returns recency level."""
        scope_note = f"\n\n【数据边界】\n{data_scope}" if data_scope else ""
        context_note = f"\n\n【搜索背景】\n{search_context}" if search_context else ""
        try:
            async with _llm_semaphore:
                decision = await self.respond_typed(
                    RecencyDecision,
                    context="",
                    user_message=(
                        f"辩论议题：「{topic}」\n"
                        f"{_time_awareness_hint()}\n"
                        f"{context_note}{scope_note}\n\n"
                        "判断即将进行的搜索需要多新的数据。\n\n"
                        "选择标准：\n"
                        "- oneDay：只要最近1天的数据（如今天刚发生的比赛、突发新闻）\n"
                        "- oneWeek：只要最近1周（如本周赛事、近几天动态）\n"
                        "- oneMonth：只要最近1个月（如本月赛程、近期战绩统计、球员近期状态）\n"
                        "- noLimit：不限时间（如历史分析、理论知识、百科信息）\n\n"
                        "注意：\n"
                        "- 体育赛事、比赛数据、球员表现 → 优先 oneMonth（比赛可能在最近几周任何一天）\n"
                        "- 议题提到「今天」「本轮」→ oneDay 或 oneWeek\n"
                        "- 时事新闻、最新动态 → oneWeek\n"
                        "- 历史讨论、理论分析、通用知识 → noLimit\n"
                        "- 宁可放宽（oneMonth）也不要选太严（oneDay），避免漏掉有用数据\n\n"
                        '输出JSON：{"needs_recent": true/false, '
                        '"recency": "oneDay/oneWeek/oneMonth/noLimit", '
                        '"reasoning": "一句话理由"}'
                    ),
                )
            recency = decision.recency if decision.needs_recent else "noLimit"
            logger.info(
                "Recency decision for '%s': %s (reason: %s)",
                topic[:50], recency, decision.reasoning[:80],
            )
            return recency
        except Exception as e:
            logger.warning("Recency decision failed, defaulting to noLimit: %s", e)
            return "noLimit"

    async def _decompose_topic(self, topic: str) -> TopicDecomposition:
        """Decompose topic into searchable entities and hidden sub-topics.

        Identifies named entities (with aliases for multi-language queries)
        and hidden sub-questions that must be resolved before the main
        question can be answered (e.g. "which game?" before "how did he play?").

        Single LLM call. Returns empty TopicDecomposition on failure.
        """
        try:
            decomposition = await self.respond_typed(
                TopicDecomposition,
                context="",
                user_message=(
                    f"分析以下辩论议题，提取其中的实体和隐藏子问题：\n"
                    f"「{topic}」\n\n"
                    f"{_time_awareness_hint()}\n\n"
                    "【任务】\n"
                    "1. 提取议题中涉及的所有实体（人物、球队、赛事等），包括别名\n"
                    "2. 识别隐藏的子问题——那些在回答主问题之前必须先解决的问题\n\n"
                    "【隐藏子问题示例】\n"
                    "- 「赖斯上一场比赛表现如何」→ 隐藏子问题：赖斯的上一场比赛是哪场？\n"
                    "- 「詹姆斯最近一场季后赛的命中率」→ 隐藏子问题：詹姆斯最近一场季后赛是哪场？\n"
                    "- 「特斯拉Q1财报表现」→ 没有隐藏子问题（直接搜索即可）\n\n"
                    "【注意】\n"
                    "- 只列出真正需要先搜索才能回答的子问题\n"
                    "- 如果议题可以直接搜索，不需要分解子问题\n"
                    "- 实体要给出英文名等别名，方便生成英文搜索关键词\n"
                ),
            )
            logger.info(
                "Topic decomposition for '%s': entities=%s, sub_topics=%s",
                topic[:50],
                [e.name for e in decomposition.entities],
                [st.question for st in decomposition.hidden_sub_topics],
            )
            return decomposition
        except Exception as e:
            logger.warning("Topic decomposition failed, continuing without: %s", e)
            return TopicDecomposition()

    async def _supplementary_search(
        self,
        topic: str,
        search_provider: SearchProvider,
        existing_results: list[dict],
        min_queries: int = 6,
        recency: str = "noLimit",
        on_search: OnSearchCallback = None,
        on_progress: Callable[[dict], Awaitable[None]] | None = None,
        statmuse_provider: "SearchProvider | None" = None,
        searched_queries: set[str] | None = None,
    ) -> list[dict]:
        """Generate diverse supplementary queries when initial search was too narrow.

        Generates queries from multiple fixed angles (entity+latest, entity+stats,
        entity+news, English variant) regardless of LLM plan decisions.
        """
        # Extract core entity from topic (first meaningful phrase)
        entity = topic.split("谁")[-1].split("和")[-1].split("与")[-1].split("vs")[-1]
        entity = entity.strip().rstrip("？?。！!")[:10]
        # Also try the first few chars of the topic as entity
        alt_entity = topic[:6].strip()

        # Generate diverse queries from fixed angles
        candidate_queries = [
            f"{entity} 最新动态",
            f"{entity} 数据统计",
            f"{entity} 新闻",
            f"{entity} latest",
            f"{entity} stats",
            f"{entity} news",
            f"{alt_entity} 最新",
            f"{alt_entity} data",
        ]

        # Filter out queries too similar to what existing results already cover
        # (simple heuristic: check if query words appear in existing titles/snippets)
        existing_text = " ".join(
            r.get("title", "") + " " + r.get("snippet", "")
            for r in existing_results[:5]
        ).lower()

        _searched = searched_queries if searched_queries is not None else set()
        filtered_queries = []
        for q in candidate_queries:
            # Skip if already searched in this session
            if _normalize_query(q) in _searched:
                continue
            # Keep query if less than half its words appear in existing results
            words = q.lower().split()
            overlap = sum(1 for w in words if w in existing_text)
            if overlap < len(words) * 0.5:
                filtered_queries.append(q)

        # Take up to min_queries diverse queries
        queries = filtered_queries[:min_queries]
        if not queries:
            logger.info("Supplementary search: all queries overlap with existing results, skipping")
            return []

        logger.info("Supplementary search: %d diverse queries: %s", len(queries), queries)

        if on_progress:
            try:
                await on_progress({
                    "type": "iterative_search",
                    "round": 0,
                    "reason": f"补充搜索：扩大搜索覆盖（{len(queries)} 个多角度关键词）",
                    "queries": queries,
                })
            except Exception:
                pass

        # Execute searches in parallel (2 batches to avoid rate limiting)
        all_results = []
        batch_size = 3
        for i in range(0, len(queries), batch_size):
            batch = queries[i:i + batch_size]
            batch_results = await asyncio.gather(*[
                self._safe_single_search(q, search_provider, recency=recency)
                for q in batch
            ])
            for r in batch_results:
                all_results.extend(r)

        # StatMuse supplementary for sports/finance queries
        if statmuse_provider:
            for q in queries:
                sm = await self._statmuse_query(q, statmuse_provider)
                all_results.extend(sm)

        # Record executed queries to session dedup set
        for q in queries:
            _searched.add(_normalize_query(q))

        if on_search and all_results:
            try:
                await on_search(queries, all_results)
            except Exception:
                pass

        logger.info("Supplementary search returned %d results", len(all_results))
        return all_results

    async def _safe_single_search(
        self, query: str, search_provider: SearchProvider, recency: str = "noLimit",
    ) -> list[dict]:
        """Single search with rate limit retry. Returns list of dicts."""
        try:
            results = await search_provider.search(query, max_results=3, recency=recency)
            logger.info("Supplementary query '%s': %d results", query, len(results))
            return [r.to_dict() for r in results]
        except Exception as e:
            if "429" in str(e) or "rate" in str(e).lower():
                await asyncio.sleep(1)
                try:
                    results = await search_provider.search(query, max_results=3, recency=recency)
                    return [r.to_dict() for r in results]
                except Exception:
                    return []
            logger.warning("Supplementary search failed for '%s': %s", query, e)
            return []

    async def _statmuse_query(
        self, query: str, statmuse_provider: "SearchProvider | None",
    ) -> list[dict]:
        """Query StatMuse for supplementary sports/finance data.

        Returns [] if provider is None, query doesn't match, or on any error.
        Never blocks the main pipeline.
        """
        if not statmuse_provider:
            return []
        try:
            results = await statmuse_provider.search(query, max_results=2)
            if results:
                dicts = [r.to_dict() for r in results]
                for d in dicts:
                    d["source"] = "statmuse"
                logger.info(
                    "StatMuse '%s': %d results", query[:50], len(dicts),
                )
                return dicts
        except Exception as e:
            logger.debug("StatMuse query '%s' failed: %s", query[:50], e)
        return []

    async def fetch_for_agent(
        self, topic: str, agent_context: str, position_name: str,
        round_num: int, search_provider: SearchProvider,
        existing_pool_summary: str = "", data_scope: str = "",
        agent_requests: list[str] | None = None,
        on_search: OnSearchCallback = None,
        recency: str = "noLimit",
        statmuse_provider: "SearchProvider | None" = None,
    ) -> list[dict]:
        """Full fetch cycle: decide queries -> parallel search -> return results."""
        queries = await self.decide_queries(
            topic, agent_context, position_name, round_num,
            existing_pool_summary=existing_pool_summary,
            data_scope=data_scope,
            agent_requests=agent_requests,
        )
        if not queries:
            return []

        async def _safe_search(query: str) -> list[dict]:
            try:
                results = await search_provider.search(query, max_results=3, recency=recency)
                logger.info("Search results for '%s': %d items", query, len(results))
                for r in results:
                    logger.info("  - %s | %s", r.title[:80], r.snippet[:120])
                return [r.to_dict() for r in results]
            except Exception as e:
                if "429" in str(e) or "rate" in str(e).lower():
                    logger.warning("Rate limited on query '%s', backing off 1s", query)
                    await asyncio.sleep(1)
                    try:
                        results = await search_provider.search(query, max_results=3, recency=recency)
                        return [r.to_dict() for r in results]
                    except Exception:
                        return []
                logger.warning("Search failed for query '%s': %s", query, e)
                return []

        batch_results = await asyncio.gather(*[_safe_search(q) for q in queries])
        flat = [r for batch in batch_results for r in batch]

        # StatMuse supplementary: query in parallel for sports/finance topics
        if statmuse_provider and flat:
            sm_tasks = [self._statmuse_query(q, statmuse_provider) for q in queries]
            sm_results = await asyncio.gather(*sm_tasks)
            for sm_batch in sm_results:
                flat.extend(sm_batch)

        results = flat[:MAX_TOTAL_RESULTS]

        if on_search:
            try:
                await on_search(queries, results)
            except Exception as e:
                logger.warning("on_search callback failed: %s", e)

        return results

    async def research_topic(
        self, topic: str, search_provider: SearchProvider, max_steps: int = 3,
        on_search: OnSearchCallback = None, recency: str = "noLimit",
        statmuse_provider: "SearchProvider | None" = None,
    ) -> list[dict]:
        """Chain-of-thought research with topic decomposition and structured analysis.

        Phase 0: Decompose topic into entities + hidden sub-topics.
        Phase 1: Generate research plan informed by decomposition.
        Phase 2: Execute steps, each step adjusting based on prior discoveries.
        """
        # Phase 0: Topic decomposition
        decomposition = await self._decompose_topic(topic)

        # Build decomposition hint for plan generation
        decomp_hint = ""
        if decomposition.entities:
            entity_lines = []
            for e in decomposition.entities:
                aliases = f"（别名：{'、'.join(e.aliases)}）" if e.aliases else ""
                entity_lines.append(f"- {e.name}{aliases} [{e.entity_type}]")
            decomp_hint += "\n\n【议题实体】\n" + "\n".join(entity_lines)

        if decomposition.hidden_sub_topics:
            subtopic_lines = []
            for st in decomposition.hidden_sub_topics:
                subtopic_lines.append(f"- {st.question}\n  策略：{st.resolution_strategy}")
            decomp_hint += "\n\n【需要先解决的子问题】\n" + "\n".join(subtopic_lines)
            decomp_hint += (
                "\n\n重要：你的搜索计划必须先解决上述子问题，再搜索具体数据。\n"
                "例如：如果子问题是「赖斯最近一场比赛是哪场？」，\n"
                "那么第1步应该搜索赖斯最近比赛来定位具体场次，\n"
                "第2步用发现的比赛信息（对手、日期）来搜索该场比赛的详细数据。\n"
                "**一旦确定了具体事件，后续步骤可以用事件本身作为搜索关键词，"
                "不必总是包含主要实体名。**"
            )

        if decomposition.search_strategy_hint:
            decomp_hint += f"\n\n【搜索策略提示】\n{decomposition.search_strategy_hint}"

        # Phase 1: LLM generates a multi-step research plan
        plan = await self.respond_typed(
            ResearchPlan,
            context="",
            user_message=(
                f"辩论议题：「{topic}」\n"
                f"{_time_awareness_hint()}{decomp_hint}\n\n"
                "你需要制定一个分步搜索计划来获取这个议题所需的事实数据。\n\n"
                "重要原则：\n"
                "1. 不要假设任何你可能记错的信息（如球员所属球队、比赛日期等）\n"
                "2. 每一步搜索都应该基于**已知事实**，而不是你的训练数据记忆\n"
                "3. **至少给出 3 步计划**，宁可多搜不要少搜\n"
                "4. 每步的搜索关键词必须互不相同，覆盖不同角度\n\n"
                "推荐的搜索策略（每步都要给出搜索关键词，不要跳过）：\n"
                "- 第1步：搜索议题中的关键实体当前状态（如球员现在在哪个队、最新动态）\n"
                "  关键词用灵活的「最新」「latest」「战报」类\n"
                "- 第2步：搜索具体事件/比赛/数据（如具体哪场比赛、近期战绩）\n"
                "  用第1步发现的信息指导这一步的关键词\n"
                "- 第3步：搜索补充数据（如统计数据、评分、对比分析）\n"
                "  用不同角度的关键词获取更全面的数据\n"
                "- 第4步+：如果议题复杂，继续搜索更深层次的数据\n\n"
                f"第1步必须给出 {FIRST_STEP_QUERIES} 个搜索关键词（覆盖不同角度：中文灵活查询、英文查询、实体+数据类型、实体+最新动态等）。\n"
                f"后续步骤每步最多 {MAX_QUERIES} 个关键词，总共最多 {max_steps} 步。\n"
                "**不要跳过任何步骤**，即使你觉得信息可能够了，也给出关键词让系统去搜。\n"
                "唯一的例外：纯哲学/价值观讨论（如「人性本善还是本恶」）可以只给 1 步。\n\n"
                "输出JSON格式，每步包含 reasoning（为什么搜这个）和 search_queries（关键词列表）。"
            ),
        )

        if not plan.steps:
            logger.info("Research plan empty for topic '%s', falling back to simple search", topic)
            return await self.fetch_for_topic(
                topic, search_provider, on_search=on_search,
                statmuse_provider=statmuse_provider,
            )

        logger.info(
            "Research plan for '%s': %d steps (entities=%s, sub_topics=%s)",
            topic, len(plan.steps),
            [e.name for e in decomposition.entities],
            [st.question for st in decomposition.hidden_sub_topics],
        )

        # Phase 2: Execute steps as a true chain.
        all_results: list[dict] = []
        accumulated_context = ""
        prev_step_results: list[dict] = []

        for i, step in enumerate(plan.steps[:max_steps]):
            if i == 0:
                queries = step.search_queries[:FIRST_STEP_QUERIES]
            else:
                # Build results text: prefer enriched content (from webFetch),
                # fall back to title+snippet for results that weren't fetched.
                # StatMuse results are tagged as authoritative.
                prev_results_text = ""
                for r in prev_step_results[:6]:
                    title = r.get("title", "")
                    snippet = r.get("snippet", "")[:150]
                    enriched = r.get("_enriched_content", "")
                    auth_tag = "[权威来源StatMuse] " if r.get("source") == "statmuse" else ""
                    if enriched:
                        prev_results_text += (
                            f"  - {auth_tag}{title}\n"
                            f"    页面内容摘要：{enriched[:500]}\n"
                        )
                    else:
                        prev_results_text += f"  - {auth_tag}{title}: {snippet}\n"
                try:
                    adjusted = await self.respond_typed(
                        ResearchStep,
                        context=accumulated_context,
                        user_message=(
                            f"辩论议题：「{topic}」\n"
                            f"研究计划第 {i+1} 步的目标：{step.reasoning}\n\n"
                            f"【第{i}步搜索到的原始结果】\n{prev_results_text}\n\n"
                            "请先分析上面第{i}步的搜索结果，提取关键发现，"
                            "然后生成本步的搜索关键词。\n\n"
                            "**重要：标有[权威来源StatMuse]的结果是专业体育/金融数据网站的权威数据，"
                            "其日期、数值、赛事信息应被优先采信。"
                            "当StatMuse数据与其他来源矛盾时，以StatMuse为准。**\n\n"
                            "提取要求：\n"
                            "- discovered_entities: 发现的具名实体（具体日期、球队名、比赛名、对手等）\n"
                            "- discovered_facts: 带具体数值/日期的关键事实\n"
                            "- resolved_sub_topic: 如果某个子问题已解决，写出答案\n\n"
                            "关键词生成原则：\n"
                            "- 使用提取的具体信息（日期、对手名）构造更精确的关键词\n"
                            "- 如果已确定具体事件（如某场比赛），可以不包含主要实体名来搜索\n"
                            "  例：已知赖斯上一场是5月10日阿森纳vs西汉姆，可以直接搜「阿森纳 西汉姆 5月10日 战报」\n"
                            "- 覆盖不同角度和不同语言\n"
                            f"- 最多 {MAX_QUERIES} 个关键词。{_time_awareness_hint()}"
                        ),
                    )
                    queries = adjusted.search_queries[:MAX_QUERIES]

                    # Build structured context from discoveries
                    if adjusted.discovered_facts:
                        facts_str = "\n".join(
                            f"  · {f}" for f in adjusted.discovered_facts
                        )
                        accumulated_context += f"\n第{i}步发现：\n{facts_str}\n"
                    else:
                        findings = "\n".join(
                            f"  · {r.get('title', '')}: {r.get('snippet', '')[:100]}"
                            for r in prev_step_results[:4]
                        )
                        accumulated_context += f"\n第{i}步发现：\n{findings}\n"

                    if adjusted.discovered_entities:
                        accumulated_context += (
                            f"可用实体：{'、'.join(adjusted.discovered_entities)}\n"
                        )
                    if adjusted.resolved_sub_topic:
                        accumulated_context += f"已解决：{adjusted.resolved_sub_topic}\n"
                        # Lock resolved facts — prevent later steps from
                        # overwriting with older/stale data.
                        accumulated_context += (
                            "⚠️ 以上事实已确认锁定。后续步骤必须以此为准，"
                            "不得搜索更早的事件或用旧数据覆盖。\n"
                        )

                    logger.info(
                        "Step %d adjusted: queries=%s, discoveries=%s, resolved='%s'",
                        i + 1, queries,
                        adjusted.discovered_facts[:3] if adjusted.discovered_facts else [],
                        adjusted.resolved_sub_topic[:60],
                    )
                except Exception as e:
                    logger.warning(
                        "Step %d query adjustment failed, using original: %s",
                        i + 1, e,
                    )
                    queries = step.search_queries[:MAX_QUERIES]

            if not queries:
                logger.info("Step %d skipped (no queries needed)", i + 1)
                continue

            logger.info(
                "Research step %d/%d: %s | queries=%s",
                i + 1, min(len(plan.steps), max_steps),
                step.reasoning[:80], queries,
            )

            step_results = []
            for q in queries:
                try:
                    results = await search_provider.search(q, max_results=3, recency=recency)
                    logger.info("  Query '%s': %d results", q, len(results))
                    for r in results:
                        logger.info("    - %s | %s", r.title[:60], r.snippet[:80])
                    step_results.extend([r.to_dict() for r in results])
                except Exception as e:
                    logger.warning("  Search failed for '%s': %s", q, e)

            # StatMuse supplementary for sports/finance queries
            if statmuse_provider:
                for q in queries:
                    sm = await self._statmuse_query(q, statmuse_provider)
                    step_results.extend(sm)

            # Save for next iteration's analysis
            prev_step_results = step_results

            if step_results:
                all_results.extend(step_results)

                # Enrich top results with web content for better analysis
                enriched_texts = await self._fetch_top_results_content(
                    step_results, max_fetch=2,
                )

                if i == 0:
                    # Step 0: build context with enriched content
                    accumulated_context += f"\n第{i+1}步发现：\n{enriched_texts}\n"
                else:
                    # Later steps: enriched_texts already captured in
                    # the step adjustment prompt's prev_results_text
                    pass

            if on_search:
                try:
                    await on_search(queries, step_results)
                except Exception as e:
                    logger.warning("on_search callback failed in step %d: %s", i + 1, e)

        logger.info("Research complete: %d total results for '%s'", len(all_results), topic)
        return all_results[:MAX_TOTAL_RESULTS * 2]

    async def _fetch_top_results_content(
        self, results: list[dict], max_fetch: int = 2,
    ) -> str:
        """Fetch page content for top search results to enrich chain analysis.

        Returns a formatted text block with title + content excerpt for each
        successfully fetched result. Also sets ``_enriched_content`` on the
        result dicts so subsequent steps can use it.
        """
        candidates = [r for r in results if r.get("url")][:max_fetch]
        if not candidates:
            return ""

        async def _fetch_one(r: dict) -> tuple[dict, str]:
            url = r.get("url", "")
            try:
                content = await fetch_page_content(url)
                if content and len(content) >= 200:
                    excerpt = content[:600]
                    r["_enriched_content"] = excerpt
                    return r, excerpt
            except Exception as e:
                logger.debug("Chain webFetch failed for %s: %s", url[:60], e)
            return r, ""

        fetched = await asyncio.gather(*[_fetch_one(r) for r in candidates])

        lines: list[str] = []
        for r, content in fetched:
            title = r.get("title", "")
            snippet = r.get("snippet", "")[:100]
            if content:
                lines.append(f"  · {title}\n    内容：{content[:400]}")
            else:
                lines.append(f"  · {title}: {snippet}")
        return "\n".join(lines)

    async def fetch_for_topic(
        self, topic: str, search_provider: SearchProvider,
        on_search: OnSearchCallback = None, recency: str = "noLimit",
        statmuse_provider: "SearchProvider | None" = None,
    ) -> list[dict]:
        """Topic-level search: decide queries from topic alone, then search."""
        result = await self.respond_typed(
            SearchQueries,
            context="",
            user_message=(
                f"辩论议题：「{topic}」\n"
                "为了帮助主持人提出更有依据的辩论角度，"
                "请提出最多 2 个搜索关键词来获取相关的最新事实数据。\n"
                f"{_time_awareness_hint()}\n"
                "只搜索模型可能不知道的最新信息，不要搜索常识。\n"
                "关键词策略：一个中文灵活查询（如「XX 最新比赛」）+ "
                "一个英文查询（如 \"XX latest game\"），不要都用死板日期。\n\n"
                '输出JSON：{"searches": ["关键词1", "关键词2"]}\n'
                '如果不需要搜索：{"searches": []}'
            ),
        )
        queries = result.searches[:MAX_QUERIES]
        logger.info("Data clerk topic-level queries for '%s': %s", topic, queries)
        if not queries:
            return []

        async def _safe_search(query: str) -> list[dict]:
            try:
                results = await search_provider.search(query, max_results=3, recency=recency)
                logger.info("Search results for '%s': %d items", query, len(results))
                for r in results:
                    logger.info("  - %s | %s", r.title[:80], r.snippet[:120])
                return [r.to_dict() for r in results]
            except Exception as e:
                if "429" in str(e) or "rate" in str(e).lower():
                    logger.warning("Rate limited on query '%s', backing off 1s", query)
                    await asyncio.sleep(1)
                    try:
                        results = await search_provider.search(query, max_results=3, recency=recency)
                        return [r.to_dict() for r in results]
                    except Exception:
                        return []
                logger.warning("Search failed for query '%s': %s", query, e)
                return []

        batch_results = await asyncio.gather(*[_safe_search(q) for q in queries])
        flat = [r for batch in batch_results for r in batch]

        # StatMuse supplementary for sports/finance topics
        if statmuse_provider and flat:
            sm_tasks = [self._statmuse_query(q, statmuse_provider) for q in queries]
            sm_results = await asyncio.gather(*sm_tasks)
            for sm_batch in sm_results:
                flat.extend(sm_batch)

        results = flat[:MAX_TOTAL_RESULTS]
        logger.info("fetch_for_topic done: %d results, on_search=%s", len(results), on_search is not None)

        if on_search:
            try:
                await on_search(queries, results)
            except Exception as e:
                logger.warning("on_search callback failed in fetch_for_topic: %s", e)

        return results

    async def verify_results(
        self, results: list[dict], topic: str, data_scope: str,
        existing_pool_summary: str = "",
    ) -> list[dict]:
        """Cross-reference search results against data scope and existing pool.

        Returns only verified results that are relevant and consistent.
        Falls back to returning all results if verification fails.
        """
        if not results:
            return []

        results_text = "\n".join(
            f"[{i+1}] {r.get('title', '')} | {r.get('snippet', '')[:120]}"
            for i, r in enumerate(results)
        )

        pool_note = ""
        if existing_pool_summary:
            pool_note = (
                f"\n\n【已有数据池】\n{existing_pool_summary}\n"
                "与新数据相互矛盾的结果应被排除。与已有数据相互佐证的结果优先保留。"
            )

        try:
            verified = await self.respond_typed(
                VerifiedResults,
                context="",
                user_message=(
                    f"辩论议题：「{topic}」\n"
                    f"【数据边界】\n{data_scope}\n\n"
                    f"【新搜索结果】\n{results_text}\n\n"
                    "分析每条搜索结果：\n"
                    "1. 是否在数据边界范围内？（如边界限定'2026年5月7日比赛'，"
                    "其他日期的数据应排除）\n"
                    "2. 是否与已有数据池中的信息一致？（矛盾的数据应排除）\n"
                    "3. 多条结果是否相互佐证？（相互佐证的数据更可信）\n"
                    f"{pool_note}\n\n"
                    "将结果分为 verified（通过验证的）和 rejected（排除的）。\n"
                    "如果所有结果都不在边界内或互相矛盾，可以全部排除。\n"
                    '输出JSON：{"verified": [{"title":"...", "snippet":"...", "url":"..."}], '
                    '"rejected": ["排除原因1", ...], '
                    '"verification_note": "验证说明"}'
                ),
            )
            return verified.verified if verified.verified else []
        except Exception as e:
            logger.warning("Data verification failed, using all results: %s", e)
            return results

    # ── Content extraction ──────────────────────────────────────────

    async def extract_facts(
        self, url: str, query: str, fallback_content: str = "",
        topic: str = "",
    ) -> ExtractedFacts:
        """Fetch page content and extract key facts using LLM.

        Tries Jina Reader first; falls back to fallback_content (e.g. search
        snippet) when Jina is unavailable (common in China mainland).
        Also falls back to snippet when page is SPA (too little content).
        """
        content = await fetch_page_content(url)

        # SPA detection: if page returned very little content but we have a
        # search snippet, the snippet is likely more useful than the empty shell.
        if content and len(content) < 300 and fallback_content:
            logger.info(
                "Page content too short (%d chars) for %s, using search snippet (%d chars) instead",
                len(content), url[:80], len(fallback_content),
            )
            content = fallback_content
        elif not content:
            content = fallback_content

        if not content:
            logger.info("No content for %s (Jina unreachable, no fallback)", url[:80])
            return ExtractedFacts()

        # Truncate for LLM input budget
        content_for_llm = content[:3000]
        source = "page" if len(content) >= 300 else "snippet"
        logger.info(
            "Extracting facts from %s (%d chars, source=%s): %s",
            url[:80], len(content), source, content[:150],
        )

        # Build topic relevance hint if topic is provided
        topic_hint = ""
        if topic:
            topic_hint = (
                f"\n\n【原始议题】：{topic}\n"
                f"{_time_awareness_hint()}\n"
                "提示：提取与议题相关的所有有用事实，包括但不限于：\n"
                "- 主体人物/球队的数据和表现\n"
                "- 对手、队友、相关人物的数据和表现\n"
                "- 比赛/事件的比分、结果、关键时刻\n"
                "- 系列赛/赛季的整体形势和背景\n"
                "- 历史对比和里程碑记录\n"
                "注意：如果页面内容的时间与议题完全无关（如议题问2026年比赛但页面只有2020年数据），才返回空列表。\n"
            )

        try:
            result = await self.respond_typed(
                ExtractedFacts,
                context="",
                user_message=(
                    f"搜索关键词：{query}\n"
                    f"{topic_hint}\n"
                    f"以下是从网页提取的原始内容：\n{content_for_llm}\n\n"
                    "请从以上内容中提取所有有用的客观事实。\n"
                    "要求：\n"
                    "- 每条事实必须是一个完整、自包含的陈述（不需要看原文就能理解）\n"
                    "- 提取客观事实（数据、日期、事件、人名、比分等），不要提取观点或推测\n"
                    "- 不仅提取主体的信息，也提取对手、队友、相关人物、球队的表现数据\n"
                    "- 比赛类页面：提取得分、篮板、助攻等数据，以及比赛结果、关键时刻\n"
                    "- 每条不超过100字\n"
                    "- 最多提取8条关键事实\n"
                    "- 如果页面内容完全是广告、导航或与议题完全无关的杂讯，才返回空列表\n\n"
                    '输出JSON：{"key_facts": ["事实1", "事实2"], '
                    '"summary": "一句话概括页面中与关键词相关的内容"}'
                ),
            )
            logger.info(
                "Extracted %d facts from %s: %s",
                len(result.key_facts), url[:80],
                result.key_facts[:5],  # log first 5 facts for debugging
            )
            return result
        except Exception as e:
            logger.warning("Fact extraction failed for %s: %s", url[:80], e)
            # Fallback: use first 200 chars of raw content as a single fact
            return ExtractedFacts(
                key_facts=[content[:200]] if content else [],
                summary=content[:100] if content else "",
            )

    async def extract_facts_batch(
        self,
        results: list[dict],
        query: str,
        on_extract: Callable[[str, ExtractedFacts], Awaitable[None]] | None = None,
        max_urls: int = MAX_EXTRACT_URLS,
        topic: str = "",
    ) -> list[dict]:
        """Batch extract facts from multiple URLs (rate-limited).

        Only processes the first ``max_urls`` results with full LLM
        extraction; remaining results keep their search snippets as-is.
        Uses a semaphore to limit concurrent LLM calls.

        Returns results enriched with key_facts field (JSON string).
        """
        if not results:
            return results

        async def _extract_one(r: dict) -> dict:
            url = r.get("url", "")
            snippet = r.get("snippet", "") or r.get("content", "")
            if not url and not snippet:
                return r
            try:
                async with _llm_semaphore:
                    facts = await self.extract_facts(
                        url or "", query, fallback_content=snippet, topic=topic,
                    )
                enriched = {**r, "key_facts": json.dumps(
                    {"key_facts": facts.key_facts, "summary": facts.summary},
                    ensure_ascii=False,
                )}
                if on_extract:
                    try:
                        await on_extract(url, facts)
                    except Exception as e:
                        logger.warning("on_extract callback failed: %s", e)
                return enriched
            except Exception as e:
                logger.warning("Batch extraction failed for %s: %s", url[:80], e)
                return r

        # Only extract from top max_urls results; pass others through
        to_extract = results[:max_urls]
        remaining = results[max_urls:]

        enriched = await asyncio.gather(*[_extract_one(r) for r in to_extract])
        return list(enriched) + remaining

    async def cross_validate_facts(
        self, enriched_results: list[dict], query: str,
    ) -> CrossValidatedFacts:
        """Cross-validate extracted facts across multiple sources.

        Compares key_facts from each source, identifies:
        - validated: facts confirmed by 2+ sources
        - unique: facts from only one source
        - contradictions: conflicting facts across sources
        """
        # Collect all extracted facts with source attribution
        all_facts_text = ""
        for i, r in enumerate(enriched_results):
            kf = r.get("key_facts", "")
            if not kf:
                continue
            try:
                parsed = json.loads(kf) if isinstance(kf, str) else kf
                facts_list = parsed.get("key_facts", [])
                title = r.get("title", f"来源{i+1}")
                if facts_list:
                    all_facts_text += f"\n【{title}】\n"
                    for f in facts_list:
                        all_facts_text += f"  - {f}\n"
            except (json.JSONDecodeError, AttributeError):
                continue

        if not all_facts_text.strip():
            return CrossValidatedFacts()

        try:
            async with _llm_semaphore:
                result = await self.respond_typed(
                    CrossValidatedFacts,
                context="",
                user_message=(
                    f"辩论议题搜索关键词：{query}\n\n"
                    f"以下是从多个来源提取的关键事实：{all_facts_text}\n"
                    "请对比各来源的事实，找出：\n"
                    "1. validated: 多个来源都提到的事实（相互佐证），附 source_count\n"
                    "2. unique: 仅单一来源提到的事实\n"
                    "3. contradictions: 各来源之间矛盾的事实\n\n"
                    '输出JSON：{"validated": [{"fact": "...", "source_count": N}], '
                    '"unique": [{"fact": "...", "source": "来源名"}], '
                    '"contradictions": [{"conflicting_facts": ["A说...", "B说..."], '
                    '"sources": ["来源1", "来源2"]}], '
                    '"note": "验证说明"}'
                ),
            )
            logger.info(
                "Cross-validation: %d validated, %d unique, %d contradictions | validated=%s unique=%s",
                len(result.validated), len(result.unique), len(result.contradictions),
                [v.get("fact", "")[:80] for v in result.validated[:5]],
                [u.get("fact", "")[:80] for u in result.unique[:5]],
            )
            return result
        except Exception as e:
            logger.warning("Cross-validation failed: %s", e)
            return CrossValidatedFacts()

    @staticmethod
    @staticmethod
    def _facts_match(fact: str, validated_text: str) -> bool:
        """Check if a key_fact is semantically related to a validated fact.

        Cross-validation produces SYNTHESIZED composite facts that rarely
        match source key_facts verbatim.  We use three heuristics:
          1. Exact substring containment (handles partial quotes)
          2. Number overlap: 2+ shared numbers → same statistical claim
          3. Token-set overlap: shared significant tokens above threshold
        """
        if not fact or not validated_text:
            return False
        # 1) Substring containment
        shorter, longer = (fact, validated_text) if len(fact) <= len(validated_text) else (validated_text, fact)
        if len(shorter) >= 6 and shorter in longer:
            return True
        # 2) Number overlap — key for sports / statistical facts
        nums_f = set(re.findall(r"\d+\.?\d*", fact))
        nums_v = set(re.findall(r"\d+\.?\d*", validated_text))
        # Filter out trivially common numbers (year-like 2026, single digits)
        significant_f = {n for n in nums_f if len(n) >= 2 and not n.startswith("202")}
        significant_v = {n for n in nums_v if len(n) >= 2 and not n.startswith("202")}
        if significant_f and significant_v and len(significant_f & significant_v) >= 2:
            return True
        # 3) Token overlap — extract Chinese segments (2+ chars), numbers, English words
        def _tokens(text: str) -> set[str]:
            tokens: set[str] = set()
            # Chinese character sequences (2+ chars)
            for m in re.finditer(r"[\u4e00-\u9fff]{2,}", text):
                tokens.add(m.group())
            # Numbers (2+ digits, not year-like)
            for m in re.finditer(r"\d{2,}(?:\.\d+)?", text):
                n = m.group()
                if not n.startswith("202"):
                    tokens.add(n)
            # English words (3+ chars)
            for m in re.finditer(r"[a-zA-Z]{3,}", text):
                tokens.add(m.group().lower())
            return tokens
        toks_f = _tokens(fact)
        toks_v = _tokens(validated_text)
        if toks_f and toks_v:
            overlap = len(toks_f & toks_v)
            ratio = overlap / min(len(toks_f), len(toks_v))
            if ratio >= 0.4 and overlap >= 2:
                return True
        return False

    @staticmethod
    def _has_extracted_facts(result: dict) -> bool:
        """Check if a result has non-empty key_facts from extraction."""
        kf = result.get("key_facts", "")
        if not kf:
            return False
        try:
            parsed = json.loads(kf) if isinstance(kf, str) else kf
            facts = parsed.get("key_facts", [])
            return bool(facts)
        except (json.JSONDecodeError, AttributeError):
            return False

    @staticmethod
    def _map_validated_to_results(
        enriched_results: list[dict],
        validation: CrossValidatedFacts,
    ) -> tuple[list[dict], list[dict]]:
        """Split results into public (shared with all agents) and private.

        When cross-validation finds validated facts (2+ source agreement),
        ALL results are made public — screening already confirmed relevance,
        and the research overall is trustworthy.  Even results where the LLM
        failed to extract specific facts still carry useful title/snippet
        context for debaters.

        If no validated facts exist at all, only StatMuse goes public.
        """
        if not validation.validated:
            # No validated facts — only StatMuse public
            statmuse = [r for r in enriched_results if r.get("source") == "statmuse"]
            others = [r for r in enriched_results if r.get("source") != "statmuse"]
            return statmuse, others

        # Validated facts exist → ALL results go public
        # Screening already confirmed relevance; even results without
        # extracted facts carry useful title/snippet context.
        return list(enriched_results), []

    # ── Pre-filter + iterative search ────────────────────────────────

    @staticmethod
    def _scope_filter(
        results: list[dict],
        data_scope: str,
    ) -> list[dict]:
        """Rule-based pre-filter: drop results clearly outside data scope.

        Zero LLM cost. Extracts key entities from data_scope text and
        requires each result to mention at least one entity in its title
        or snippet. If data_scope is empty, returns all results.

        This catches obvious mismatches like:
          - Scope says "湖人对雷霆" but result mentions "火箭"
          - Scope says "2026年5月10日" but result is about a different date
        """
        if not data_scope or not results:
            return results

        # Extract entities from scope lines like "关键实体：湖人, 雷霆, 詹姆斯"
        entities: list[str] = []
        for line in data_scope.split("\n"):
            line = line.strip()
            if line.startswith("关键实体：") or line.startswith("关键实体:"):
                entity_str = line.split("：", 1)[-1].split(":", 1)[-1]
                entities.extend(e.strip() for e in entity_str.split(",") if e.strip())
            elif line.startswith("事件：") or line.startswith("事件:"):
                # Extract event keywords (e.g. "湖人对雷霆的比赛")
                event = line.split("：", 1)[-1].split(":", 1)[-1].strip()
                if event:
                    entities.append(event)

        if not entities:
            return results

        kept = []
        for r in results:
            title = r.get("title", "")
            snippet = r.get("snippet", "")
            text = f"{title} {snippet}"

            # Check if at least one entity is mentioned
            matched = False
            for entity in entities:
                # Multi-word entity: check if any core word matches
                # e.g. "湖人对雷霆的比赛" → check "湖人" OR "雷霆"
                if len(entity) >= 4 and ("对" in entity or "vs" in entity.lower()):
                    parts = entity.replace("vs", "对").split("对")
                    for part in parts:
                        part = part.strip().rstrip("的比赛")
                        if part and part in text:
                            matched = True
                            break
                elif entity in text:
                    matched = True
                    break

            if matched:
                kept.append(r)
            else:
                logger.info(
                    "Scope-filtered out: '%s' — no scope entity found in title/snippet",
                    title[:80],
                )

        if len(kept) < len(results):
            logger.info(
                "Scope filter: %d → %d results (dropped %d outside boundary)",
                len(results), len(kept), len(results) - len(kept),
            )
        return kept

    async def screen_results(
        self,
        results: list[dict],
        topic: str,
        existing_context: str = "",
        data_scope: str = "",
        semantic_need: str = "",
        pool_summary: str = "",
    ) -> list[dict]:
        """Pre-filter search results using title+snippet only.

        Excludes results that are:
        - Obviously contradictory to known information
        - Clearly irrelevant to the topic
        - Duplicates of each other

        Falls back to returning all results on any error.
        Returns empty list if all results are genuinely irrelevant.
        """
        if not results:
            return []

        # StatMuse results are authoritative — bypass LLM screening entirely.
        statmuse_results = [r for r in results if r.get("source") == "statmuse"]
        non_statmuse = [r for r in results if r.get("source") != "statmuse"]

        if statmuse_results:
            logger.info(
                "StatMuse trust: auto-keeping %d authoritative results, screening %d others",
                len(statmuse_results), len(non_statmuse),
            )
            if not non_statmuse:
                return statmuse_results

        results = non_statmuse

        results_text = "\n".join(
            f"[{i+1}] 标题：{r.get('title', '')}\n"
            f"    URL：{r.get('url', '')}\n"
            f"    日期：{r.get('publish_date', '未知')}\n"
            f"    摘要：{r.get('snippet', '')[:150]}"
            for i, r in enumerate(results)
        )
        # Build title→original-result index for URL recovery
        _title_to_original: dict[str, dict] = {}
        for r in results:
            t = r.get("title", "")
            if t:
                _title_to_original[t] = r

        context_note = ""
        if existing_context:
            context_note = (
                f"\n\n【已知信息】\n{existing_context}\n"
                "与新信息明显矛盾的结果应排除。"
            )

        scope_note = ""
        if data_scope:
            scope_note = (
                f"\n\n【数据边界】\n{data_scope}\n"
                "超出边界的结果应排除。"
            )

        need_note = ""
        if semantic_need:
            need_note = (
                f"\n\n【具体数据需求】\n{semantic_need}\n"
                "优先保留与数据需求直接相关的结果。"
                "如果一条结果与议题相关但与具体需求无关，可以排除。"
            )

        pool_note = ""
        if pool_summary:
            pool_note = (
                f"\n\n【数据池已有信息】\n{pool_summary}\n"
                "如果搜索结果的核心信息已在数据池中，排除该重复结果。"
            )

        try:
            async with _llm_semaphore:
                screened = await self.respond_typed(
                    ScreenedResults,
                    context="",
                user_message=(
                    f"辩论议题：「{topic}」\n"
                    f"{_time_awareness_hint()}\n\n"
                    f"【搜索结果（仅标题和摘要）】\n{results_text}\n\n"
                    "快速筛查每条结果：\n"
                    "1. 是否与议题相关？（不相关的排除）\n"
                    "2. 是否与已知信息矛盾？（矛盾的排除）\n"
                    "3. 是否重复？（重复的保留最详细的一条）\n"
                    "4. 是否超出数据边界？（超出的排除）\n"
                    "5. **时间和对手是否匹配？** 这是筛查中最重要的一步：\n"
                    "   - 如果议题涉及「上一场/最近一场/今天」等时间词，"
                    "必须识别出具体是哪一场比赛（哪个对手、哪个系列赛第几场）。\n"
                    "   - 只保留属于**同一场比赛/同一个系列赛**的结果，"
                    "其他比赛/系列赛的结果即使时间也接近也要排除。\n"
                    "   - 例如：议题问「詹姆斯上一场」，搜索结果中同时出现了：\n"
                    "     * 雷霆vs湖人G4（5月11日）→ 这是上一场，保留\n"
                    "     * 火箭vs湖人G2（5月某日）→ 这不是上一场，排除\n"
                    "   - 关键判断方法：看摘要中的对手球队名、系列赛比分、具体日期。\n"
                    "   如果多条结果指向不同比赛，只保留日期最近的那一场。\n\n"
                    "注意：只根据标题和摘要判断，不需要读取网页内容。\n"
                    "排除标准（必须排除）：\n"
                    "- 标题和摘要完全不涉及议题中的核心实体（如人名、球队、赛事）\n"
                    "- 例如：议题关于足球运动员，但结果是关于特斯拉、经济数据、5G等 → 必须排除\n"
                    "- 时间明显不对的旧结果\n"
                    "- 与已知信息矛盾的\n"
                    "- **属于不同比赛/不同系列赛的**（即使也提到了同一个球员）\n\n"
                    "保留标准（可以保留）：\n"
                    "- 结果中明确提到了议题的核心实体或相关事件\n"
                    "- 属于同一场比赛/同一个系列赛的相关信息（包括队友、对手表现）\n"
                    "- 不确定是否相关时，宁可保留\n"
                    f"{context_note}{scope_note}{need_note}{pool_note}\n"
                    '输出JSON：{"kept": [{"title":"...", "snippet":"...", "url":"...", "publish_date":"..."}], '
                    '"rejected": ["排除原因1", ...], '
                    '"screening_note": "筛查说明"}'
                ),
            )

            if screened.kept:
                # Recover original URLs: LLM screening may lose URL data
                recovered = []
                for kept in screened.kept:
                    title = kept.get("title", "")
                    original = _title_to_original.get(title)
                    if original:
                        # Merge: use original as base, overlay LLM fields
                        merged = {**original}
                        if kept.get("snippet"):
                            merged["snippet"] = kept["snippet"]
                        if kept.get("publish_date"):
                            merged["publish_date"] = kept["publish_date"]
                        recovered.append(merged)
                    else:
                        recovered.append(kept)
                logger.info(
                    "Screened %d -> %d results (rejected %d: %s)",
                    len(results), len(recovered), len(screened.rejected),
                    screened.rejected[:3],
                )
                return statmuse_results + recovered

            # All rejected — genuine "no relevant results"
            if screened.rejected:
                logger.info(
                    "All %d results screened out: %s",
                    len(results), screened.rejected[:3],
                )
                return statmuse_results  # Still keep StatMuse

            return statmuse_results + results  # LLM returned nothing useful, use all
        except Exception as e:
            logger.warning("Screening failed, using all results: %s", e)
            return statmuse_results + results

    async def research_with_validation(
        self,
        topic: str,
        search_provider: SearchProvider,
        existing_context: str = "",
        data_scope: str = "",
        max_iterations: int = 3,
        on_search: OnSearchCallback = None,
        on_progress: Callable[[dict], Awaitable[None]] | None = None,
        pool_summary: str = "",
        recency: str = "auto",
        max_steps: int = 3,
        min_queries: int = 0,
        statmuse_provider: "SearchProvider | None" = None,
        searched_queries: set[str] | None = None,
    ) -> ResearchOutcome:
        """Full pipeline: research -> screen -> extract -> validate -> iterate.

        Returns ResearchOutcome with public/private split.
        Public results have validated facts; private results do not.

        min_queries: guarantee at least this many search queries are issued.
        If research_topic uses fewer, supplementary queries are generated
        from different angles to broaden coverage.

        NOTE: DB persistence is NOT handled here. The caller is responsible
        for persisting results to the data pool.

        on_progress receives a dict (event payload) WITHOUT session_id.
        Caller wraps: lambda evt: self._emit(session_id, evt)
                  or: lambda evt: publish(session_id, evt)

        searched_queries: session-level set of normalized queries already executed.
        """
        # Decide recency if auto
        if recency == "auto":
            recency = await self._decide_recency(
                topic, search_context=existing_context, data_scope=data_scope,
            )

        # Session-level query dedup set
        _searched: set[str] = searched_queries if searched_queries is not None else set()

        # Phase 1: Initial research
        raw_results = await self.research_topic(
            topic, search_provider, max_steps=max_steps,
            on_search=on_search, recency=recency,
            statmuse_provider=statmuse_provider,
        )

        # Zero-result early exit: if initial search found nothing,
        # supplementary search is unlikely to help either.
        if not raw_results:
            logger.info("Initial search returned 0 results, skipping supplementary search")
            return ResearchOutcome()

        # Phase 1.5: Supplementary search if not enough queries were used
        if min_queries > 0 and len(raw_results) < min_queries * 3:
            supp_results = await self._supplementary_search(
                topic, search_provider, raw_results,
                min_queries=min_queries, recency=recency,
                on_search=on_search, on_progress=on_progress,
                statmuse_provider=statmuse_provider,
                searched_queries=_searched,
            )
            if supp_results:
                # Merge, deduplicate by URL
                existing_urls = {r.get("url", "") for r in raw_results if r.get("url")}
                for r in supp_results:
                    if r.get("url", "") not in existing_urls:
                        raw_results.append(r)
                        if r.get("url"):
                            existing_urls.add(r.get("url"))
                logger.info(
                    "After supplementary search: %d total results (added %d)",
                    len(raw_results), len(supp_results),
                )
        if not raw_results:
            return ResearchOutcome()

        # Phase 2: Pre-filter
        if on_progress:
            try:
                await on_progress({"type": "screening_start", "total": len(raw_results)})
            except Exception:
                pass

        # Rule-based scope filter (zero LLM cost)
        scope_filtered = self._scope_filter(raw_results, data_scope)

        # LLM-based screen (catches subtler mismatches)
        screened = await self.screen_results(
            scope_filtered, topic,
            existing_context=existing_context,
            data_scope=data_scope,
            pool_summary=pool_summary,
        )

        if on_progress:
            try:
                await on_progress({
                    "type": "screening_result",
                    "kept": len(screened),
                    "rejected": len(raw_results) - len(screened),
                })
            except Exception:
                pass

        if not screened:
            return ResearchOutcome()

        # Phase 3: Extract facts (web_reader only for screened results)
        enriched = await self.extract_facts_batch(screened, topic, topic=topic)

        # Phase 4: Cross-validate
        validation = await self.cross_validate_facts(enriched, topic)

        # Phase 5: Iterative refinement if needed
        all_enriched = list(enriched)
        seen_urls = {r.get("url", "") for r in enriched}
        iterations_done = 1

        for iteration in range(1, max_iterations):
            # Exit condition: validated >= 2 -> good enough
            if len(validation.validated) >= 2:
                logger.info(
                    "Validation passed after %d round(s): %d validated, %d contradictions",
                    iterations_done, len(validation.validated), len(validation.contradictions),
                )
                break

            # Generate refinement queries targeting contradictions
            contradictions_text = ""
            for c in (validation.contradictions or [])[:3]:
                contradictions_text += f"- {c}\n"
            unique_text = "\n".join(
                f"- {u.get('fact', '')[:100]}" for u in (validation.unique or [])[:3]
            )

            # When extract_facts returned nothing (validated=0, unique=0),
            # use the screened results' titles and snippets as context
            # so the LLM can generate precise queries based on what was found.
            screened_summary = ""
            if not validation.validated and not validation.unique and screened:
                screened_summary = "\n\n【已筛查通过的搜索结果（但未能提取事实）】\n"
                for r in screened[:6]:
                    screened_summary += (
                        f"- {r.get('title', '')} | "
                        f"{r.get('snippet', '')[:120]}\n"
                    )
                screened_summary += (
                    "\n以上结果已经找到了相关信息，但未能提取出结构化事实。\n"
                    "请基于这些结果中的具体信息（日期、对手、赛事、比分等）\n"
                    "生成精确的搜索关键词来获取更多佐证。\n"
                    "不要使用宽泛的「最新」「latest」类关键词，"
                    "要用具体的日期和赛事名。\n"
                )

            try:
                refinement = await self.respond_typed(
                    RefinementQueries,
                    context="",
                    user_message=(
                        f"辩论议题：「{topic}」\n"
                        f"{_time_awareness_hint()}\n\n"
                        f"【已验证的事实】\n"
                        f"{[v.get('fact', '')[:100] for v in validation.validated]}\n\n"
                        f"【矛盾的事实】\n{contradictions_text or '无'}\n\n"
                        f"【未验证的单源事实】\n{unique_text or '无'}\n"
                        f"{screened_summary}\n"
                        "当前的搜索结果中只有单源事实（未被其他来源佐证）。\n"
                        "请从以上单源事实中提取关键实体和数值，\n"
                        "生成搜索关键词来寻找能够佐证这些事实的额外来源。\n"
                        "例如：如果单源事实是'爱德华兹36分6篮板'，\n"
                        "可以搜索'爱德华兹 36分 战报'或'Edwards 36 points box score'。\n"
                        "如果存在矛盾事实，也针对矛盾焦点搜索。\n\n"
                        '输出JSON：{"queries": ["关键词1", "关键词2"], '
                        '"reasoning": "为什么要搜这些", '
                        '"focus": "矛盾的焦点是什么"}'
                    ),
                )
            except Exception as e:
                logger.warning("Refinement query generation failed: %s", e)
                break

            if not refinement.queries:
                logger.info("No refinement queries generated, stopping iteration")
                break

            queries = refinement.queries[:MAX_QUERIES]
            logger.info(
                "Iterative search round %d: %s (focus: %s)",
                iteration + 1, queries, refinement.focus[:80],
            )

            # Deduplicate refinement queries against session history
            new_queries = []
            for q in queries:
                nq = _normalize_query(q)
                if nq not in _searched:
                    new_queries.append(q)
                else:
                    logger.info("Skipped duplicate refinement query: %s", q)
            queries = new_queries
            if not queries:
                logger.info(
                    "All refinement queries were duplicates in round %d, skipping",
                    iteration + 1,
                )
                iterations_done += 1
                continue

            if on_progress:
                try:
                    await on_progress({
                        "type": "iterative_search",
                        "round": iteration + 1,
                        "reason": f"补充搜索：{refinement.focus[:50]}",
                        "queries": queries,
                    })
                except Exception:
                    pass

            # Search with refinement queries
            new_results = []
            for q in queries:
                try:
                    results = await search_provider.search(q, max_results=3, recency=recency)
                    new_results.extend([r.to_dict() for r in results])
                except Exception as e:
                    logger.warning("Iterative search failed for '%s': %s", q, e)

            # StatMuse supplementary for iterative rounds
            if statmuse_provider:
                for q in queries:
                    sm = await self._statmuse_query(q, statmuse_provider)
                    new_results.extend(sm)

            # Record executed queries to session dedup set
            for q in queries:
                _searched.add(_normalize_query(q))

            if not new_results:
                logger.info("Iterative search round %d: no new results", iteration + 1)
                iterations_done += 1
                continue

            # Rule-based scope filter + LLM screen
            scope_filtered_new = self._scope_filter(new_results, data_scope)
            if not scope_filtered_new:
                logger.info(
                    "Iterative round %d: all results filtered by scope boundary",
                    iteration + 1,
                )
                iterations_done += 1
                continue

            new_screened = await self.screen_results(
                scope_filtered_new, topic,
                existing_context=existing_context,
                data_scope=data_scope,
                pool_summary=pool_summary,
            )

            if not new_screened:
                iterations_done += 1
                continue

            # Extract facts from new results
            new_enriched = await self.extract_facts_batch(new_screened, topic, topic=topic)

            # Merge: deduplicate by URL, append new results
            for r in new_enriched:
                url = r.get("url", "")
                if url not in seen_urls:
                    all_enriched.append(r)
                    seen_urls.add(url)

            # Re-validate all results together
            validation = await self.cross_validate_facts(all_enriched, topic)
            iterations_done += 1

        # Final quality annotation
        quality = (
            "high" if len(validation.validated) >= 2 else (
                "medium" if validation.validated else "low"
            )
        )
        logger.info(
            "research_with_validation complete: %d results, quality=%s, "
            "validated=%d, unique=%d, contradictions=%d, iterations=%d",
            len(all_enriched), quality,
            len(validation.validated), len(validation.unique),
            len(validation.contradictions), iterations_done,
        )
        if on_progress:
            try:
                await on_progress({
                    "type": "validation_complete",
                    "validated": len(validation.validated),
                    "unique": len(validation.unique),
                    "contradictions": len(validation.contradictions),
                    "quality": quality,
                    "iterations": iterations_done,
                })
            except Exception:
                pass

        public, private = self._map_validated_to_results(all_enriched, validation)
        # No fallback: only validated results become public.
        # Unvalidated results stay as private (available to agents, not to frontend).
        if not public and all_enriched:
            logger.info(
                "No validated facts for '%s'; %d results remain private",
                topic[:50], len(all_enriched),
            )

        return ResearchOutcome(
            public_results=public,
            private_results=private,
            validation=validation,
        )

    # ── Semantic Intent Protocol ────────────────────────────────────

    async def research_for_agent(
        self,
        topic: str,
        semantic_need: str,
        search_provider: SearchProvider,
        pool_summary: str = "",
        full_research_context: str = "",
        data_scope: str = "",
        max_iterations: int = 2,
        on_search: OnSearchCallback = None,
        on_progress: Callable[[dict], Awaitable[None]] | None = None,
        recency: str = "auto",
        statmuse_provider: "SearchProvider | None" = None,
        searched_queries: set[str] | None = None,
    ) -> ResearchOutcome:
        """Targeted research driven by an agent's semantic data need.

        Optimized pipeline (reduces LLM calls to avoid rate limiting):
        1. Pool sufficiency — skip search if pool already answers the question
        2. Need decomposition — convert semantic need → 1-2 search queries
        3. Search → screen → extract (top 3 URLs only) → validate
        4. If validated < 2, iterate once more with gap-targeted queries

        full_research_context: the data clerk's "research notebook" containing
        both public (validated) and private (unvalidated) pool items, so the
        clerk knows what it has already searched and rejected.

        searched_queries: session-level set of normalized queries already executed.
        Queries matching this set are skipped. New queries are added after execution.
        """
        if not semantic_need:
            return ResearchOutcome()

        # Decide recency if auto
        if recency == "auto":
            recency = await self._decide_recency(
                topic, search_context=semantic_need, data_scope=data_scope,
            )

        # Step 1: Pool sufficiency check (uses full context if available)
        sufficiency_context = full_research_context or pool_summary
        if sufficiency_context:
            try:
                async with _llm_semaphore:
                    pool_check = await self.respond_typed(
                        PoolSufficiency,
                        context="",
                        user_message=(
                            f"辩论议题：「{topic}」\n\n"
                            f"【辩手/主持人的数据需求】\n{semantic_need}\n\n"
                            f"【当前研究数据（公开+私有）】\n{sufficiency_context}\n\n"
                            "判断：已有数据中是否已有足够信息回答这个需求？\n"
                            "- 已有数据包含需求中的核心数据（如具体比赛、球员数据等）"
                            " → sufficient=true\n"
                            "- 有部分相关数据，但需求中还涉及明显不同的新维度"
                            " → sufficient=false，说明缺什么\n"
                            "- 完全不相关 → sufficient=false\n\n"
                            "注意：即使某些数据是「未验证」的，如果包含需求的具体数据，也视为 sufficient=true。"
                        ),
                    )
                if pool_check.sufficient:
                    logger.info(
                        "Pool sufficient for need '%s...': %s",
                        semantic_need[:50], pool_check.reasoning,
                    )
                    return ResearchOutcome()
            except Exception as e:
                logger.warning(
                    "Pool sufficiency check failed for need '%s...': %s",
                    semantic_need[:50], e,
                )

        # Iterative search loop
        all_enriched: list[dict] = []
        seen_urls: set[str] = set()
        missing_aspects: list[str] = []
        validation = CrossValidatedFacts()

        # Session-level query dedup: track normalized queries to prevent repeats
        _searched: set[str] = searched_queries if searched_queries is not None else set()

        for iteration in range(max_iterations):
            # Step 2: Need decomposition
            # Build context from previous iteration results for refinement
            found_context = ""
            if iteration > 0 and all_enriched:
                snippets = []
                for r in all_enriched[-4:]:
                    title = r.get("title", "")
                    snip = r.get("snippet", "")[:80]
                    if title:
                        snippets.append(f"- {title}: {snip}")
                if snippets:
                    found_context = (
                        "\n\n【已搜索到的信息】\n"
                        + "\n".join(snippets)
                        + "\n基于以上信息，如果已回答需求返回空关键词。"
                        "如果需要更具体的信息，生成更精确的关键词。"
                    )

            missing_note = ""
            if missing_aspects:
                missing_note = f"\n已知缺失：{'、'.join(missing_aspects)}"

            try:
                async with _llm_semaphore:
                    # Build pool context for decomposition
                    pool_hint = ""
                    if pool_summary:
                        pool_hint = (
                            f"【数据池已有公开信息（不要重复搜索这些内容）】\n"
                            f"{pool_summary}\n\n"
                        )
                    # Full research context: shows what was already searched
                    # (both public and private) so clerk doesn't re-search
                    research_hint = ""
                    if full_research_context:
                        research_hint = (
                            f"【研究笔记（已搜索过的全部数据）】\n"
                            f"{full_research_context}\n\n"
                            "注意：已搜索但未验证的数据说明之前搜过但不相关，"
                            "不要用相同或类似的关键词重复搜索。\n\n"
                        )
                    decomposition = await self.respond_typed(
                        NeedDecomposition,
                        context="",
                        user_message=(
                            f"辩论议题：「{topic}」\n\n"
                            f"【数据需求】\n{semantic_need}\n\n"
                            f"{pool_hint}{research_hint}"
                            f"{missing_note}{found_context}\n\n"
                            "判断流程：\n"
                            "1. 先检查数据池/研究笔记中是否已有足够数据回答这个需求\n"
                            "   → 如果已有，设 sufficient=true，queries 留空\n"
                            "2. 如果确实缺少数据，生成 1-2 个简短搜索关键词\n"
                            "   → sufficient=false\n\n"
                            "关键词要求：\n"
                            "- 关键词要短：实体名+1个修饰词（如「詹姆斯 最新」「Edwards stats」）\n"
                            "- 先用宽泛关键词获取最新信息，不要一次编码太多细节\n"
                            "- 一个中文 + 一个英文为佳\n"
                            "- 只搜数据池中没有的新信息\n"
                            "- 不要搜索研究笔记中已有结果的关键词（即使未验证）\n"
                            f"{_time_awareness_hint()}\n\n"
                            '输出JSON：{"queries": ["关键词1", "关键词2"], '
                            '"reasoning": "分析过程", "sufficient": true/false}'
                        ),
                    )
            except Exception as e:
                logger.warning("Need decomposition failed: %s", e)
                break

            queries = decomposition.queries[:MAX_QUERIES]

            # Check if LLM explicitly says pool is sufficient
            if decomposition.sufficient and not queries:
                logger.info(
                    "Decomposition says sufficient (iteration %d): %s",
                    iteration + 1, decomposition.reasoning[:100],
                )
                return ResearchOutcome()

            if not queries:
                # Fallback: extract entity from topic (not raw semantic_need)
                # to avoid generating garbage like "需要詹姆斯在该场"
                entity = topic.split()[0][:8] if topic.split() else semantic_need[:8]
                queries = [f"{entity} 最新"]
                if len(entity) > 2:
                    queries.append(f"{entity} latest")
                queries = queries[:MAX_QUERIES]
                logger.info(
                    "Need decomposition returned empty, using fallback queries: %s",
                    queries,
                )

            logger.info(
                "Need decomposition iteration %d: %s (sufficient=%s, reasoning: %s)",
                iteration + 1, queries, decomposition.sufficient,
                decomposition.reasoning[:80],
            )

            # Deduplicate queries against session history
            new_queries = []
            skipped_queries = []
            for q in queries:
                nq = _normalize_query(q)
                if nq in _searched:
                    skipped_queries.append(q)
                else:
                    new_queries.append(q)
            if skipped_queries:
                logger.info(
                    "Skipped %d duplicate queries: %s",
                    len(skipped_queries), skipped_queries,
                )
            queries = new_queries

            if not queries:
                logger.info(
                    "All queries were duplicates in iteration %d, skipping",
                    iteration + 1,
                )
                continue

            if on_progress:
                try:
                    await on_progress({
                        "type": "need_decomposition",
                        "iteration": iteration + 1,
                        "queries": queries,
                        "reasoning": decomposition.reasoning[:100],
                    })
                except Exception:
                    pass

            # Step 3: Search → screen → extract → validate
            raw_results = []
            for q in queries:
                try:
                    results = await search_provider.search(q, max_results=3, recency=recency)
                    raw_results.extend([r.to_dict() for r in results])
                except Exception as e:
                    logger.warning("Search failed for '%s': %s", q, e)

            # StatMuse supplementary
            if statmuse_provider:
                for q in queries:
                    sm = await self._statmuse_query(q, statmuse_provider)
                    raw_results.extend(sm)

            # Record executed queries to session dedup set
            for q in queries:
                _searched.add(_normalize_query(q))

            if on_search:
                try:
                    await on_search(queries, raw_results)
                except Exception as e:
                    logger.warning("on_search callback failed: %s", e)

            if not raw_results:
                logger.info("No search results in iteration %d", iteration + 1)
                continue

            # Rule-based scope filter (zero LLM cost)
            scope_filtered = self._scope_filter(raw_results, data_scope)

            if not scope_filtered:
                logger.info(
                    "All %d results filtered out by scope boundary in iteration %d",
                    len(raw_results), iteration + 1,
                )
                continue

            # LLM-based screen (catches subtler mismatches, semantic-aware)
            screened = await self.screen_results(
                scope_filtered, topic,
                existing_context=full_research_context or pool_summary,
                data_scope=data_scope,
                pool_summary=pool_summary,
                semantic_need=semantic_need,
            )
            if not screened:
                continue

            # Extract facts (limited to top 3 URLs to control LLM calls)
            enriched = await self.extract_facts_batch(
                screened, topic, max_urls=MAX_EXTRACT_URLS, topic=topic,
            )

            # Deduplicate and merge
            for r in enriched:
                url = r.get("url", "")
                if url not in seen_urls:
                    all_enriched.append(r)
                    seen_urls.add(url)

            # Cross-validate all accumulated results
            validation = await self.cross_validate_facts(all_enriched, topic)

            # Data gap detection: do new unique facts reveal missing events?
            if validation.unique and pool_summary:
                unique_facts = [u.get("fact", "") for u in validation.unique if u.get("fact")]
                existing_validated = [v.get("fact", "") for v in validation.validated if v.get("fact")]
                if unique_facts:
                    has_gap, gap_queries = await self._detect_data_gap(
                        topic, pool_summary, unique_facts, existing_validated,
                    )
                    if has_gap and gap_queries:
                        # Filter out already-searched gap queries
                        gap_queries = [
                            gq for gq in gap_queries
                            if _normalize_query(gq) not in _searched
                        ]
                        if not gap_queries:
                            logger.info("All gap queries already searched, skipping")
                        else:
                            # Run supplementary search for the detected gap
                            logger.info(
                                "Data gap supplementary search: %s", gap_queries,
                            )
                            for gq in gap_queries:
                                try:
                                    gq_results = await search_provider.search(gq, max_results=3, recency=recency)
                                    raw_results.extend([r.to_dict() for r in gq_results])
                                except Exception as e:
                                    logger.warning("Gap search failed for '%s': %s", gq, e)

                            # StatMuse supplementary for gap queries
                            if statmuse_provider:
                                for gq in gap_queries:
                                    sm = await self._statmuse_query(gq, statmuse_provider)
                                    raw_results.extend(sm)

                            # Record gap queries to dedup set
                            for gq in gap_queries:
                                _searched.add(_normalize_query(gq))

                            # Screen and extract gap results
                            gap_scope = self._scope_filter(raw_results, data_scope)
                            if gap_scope:
                                gap_screened = await self.screen_results(
                                    gap_scope, topic,
                                    existing_context=pool_summary,
                                    data_scope=data_scope,
                                    pool_summary=pool_summary,
                                    semantic_need=semantic_need,
                                )
                                if gap_screened:
                                    gap_enriched = await self.extract_facts_batch(
                                        gap_screened, topic, max_urls=MAX_EXTRACT_URLS,
                                        topic=topic,
                                    )
                                    for r in gap_enriched:
                                        url = r.get("url", "")
                                        if url not in seen_urls:
                                            all_enriched.append(r)
                                            seen_urls.add(url)
                                    # Re-validate with gap results included
                                    validation = await self.cross_validate_facts(all_enriched, topic)

            # Early exit if we have enough validated facts (≥2 for quality)
            if len(validation.validated) >= 2:
                logger.info(
                    "research_for_agent: %d validated facts after %d iteration(s), done",
                    len(validation.validated), iteration + 1,
                )
                if on_progress:
                    try:
                        await on_progress({
                            "type": "data_sufficiency",
                            "sufficient": True,
                            "iteration": iteration + 1,
                        })
                    except Exception:
                        pass
                public, private = self._map_validated_to_results(all_enriched, validation)
                return ResearchOutcome(
                    public_results=public,
                    private_results=private,
                    validation=validation,
                )

            # Collect gaps for next iteration
            missing_aspects = []
            for u in (validation.unique or [])[:3]:
                missing_aspects.append(str(u.get("fact", ""))[:80])

        # Relevance filter: only keep results with facts relevant to semantic_need
        if all_enriched:
            sufficiency = await self._check_data_sufficiency(
                topic, semantic_need, all_enriched,
            )
            if sufficiency.relevant_facts:
                filtered = self._filter_by_relevance(
                    all_enriched, sufficiency.relevant_facts,
                )
                if filtered:
                    all_enriched = filtered
                    logger.info(
                        "Relevance filter applied: %d/%d results relevant "
                        "(sufficient=%s, %d relevant facts)",
                        len(filtered), len(all_enriched) + (len(all_enriched) - len(filtered)),
                        sufficiency.sufficient,
                        len(sufficiency.relevant_facts),
                    )

        # Return best available even if not fully sufficient
        logger.info(
            "research_for_agent complete: %d results, validated=%d",
            len(all_enriched), len(validation.validated),
        )
        public, private = self._map_validated_to_results(all_enriched, validation)
        if not public and all_enriched:
            logger.info(
                "No validated facts for agent need; %d results remain private",
                len(all_enriched),
            )
        return ResearchOutcome(
            public_results=public,
            private_results=private,
            validation=validation,
        )

    @staticmethod
    def _collect_facts_text(enriched_results: list[dict]) -> str:
        """Extract all key facts from enriched results into a flat text."""
        lines = []
        for r in enriched_results:
            kf = r.get("key_facts", "")
            if not kf:
                continue
            try:
                parsed = json.loads(kf) if isinstance(kf, str) else kf
                for f in parsed.get("key_facts", []):
                    lines.append(f"- {f}")
            except (json.JSONDecodeError, AttributeError):
                continue
        return "\n".join(lines)

    async def _check_data_sufficiency(
        self, topic: str, semantic_need: str, enriched_results: list[dict],
    ) -> DataSufficiency:
        """Check if extracted facts answer the agent's specific data need.

        Uses the DataSufficiency schema to evaluate which facts are relevant
        and whether the data sufficiently answers the question.
        """
        facts_text = self._collect_facts_text(enriched_results)
        if not facts_text:
            return DataSufficiency()

        try:
            async with _llm_semaphore:
                return await self.respond_typed(
                    DataSufficiency,
                    context="",
                    user_message=(
                        f"辩论议题：「{topic}」\n\n"
                        f"【具体数据需求】\n{semantic_need}\n\n"
                        f"【搜索到的关键事实】\n{facts_text}\n\n"
                        "判断：这些事实中哪些直接回答了上述数据需求？\n"
                        "- 将直接回答需求的事实列入 relevant_facts\n"
                        "- 如果需求已基本满足 → sufficient=true\n"
                        "- 如果关键信息仍缺失 → sufficient=false，说明缺什么\n"
                    ),
                )
        except Exception as e:
            logger.warning("Data sufficiency check failed: %s", e)
            return DataSufficiency()

    @staticmethod
    def _filter_by_relevance(
        enriched_results: list[dict], relevant_facts: list[str],
    ) -> list[dict]:
        """Keep only results whose key_facts overlap with relevant_facts.

        Uses keyword overlap: if any key_fact in a result shares significant
        words with any relevant_fact, the result is kept. Falls back to
        keeping results without key_facts (no extraction happened).
        """
        if not relevant_facts:
            return enriched_results

        # Extract meaningful keywords from relevant_facts for matching
        relevance_keywords: list[str] = []
        for rf in relevant_facts:
            # Split on common delimiters and keep meaningful tokens
            for word in rf.replace("，", " ").replace("。", " ").replace("、", " ").split():
                if len(word) >= 2:
                    relevance_keywords.append(word)

        if not relevance_keywords:
            return enriched_results

        kept = []
        for r in enriched_results:
            kf = r.get("key_facts", "")
            if not kf:
                kept.append(r)  # No extraction happened, keep as-is
                continue
            try:
                parsed = json.loads(kf) if isinstance(kf, str) else kf
                facts = parsed.get("key_facts", [])
                if not facts:
                    kept.append(r)
                    continue
                # Check if any fact in this result contains keywords from relevant_facts
                facts_text = " ".join(facts)
                is_relevant = any(kw in facts_text for kw in relevance_keywords)
                if is_relevant:
                    kept.append(r)
                else:
                    logger.info(
                        "Relevance-filtered out: '%s' — no relevant facts found",
                        r.get("title", "")[:80],
                    )
            except (json.JSONDecodeError, AttributeError):
                kept.append(r)  # Keep on parse failure

        if len(kept) < len(enriched_results):
            logger.info(
                "Relevance filter: %d → %d results (dropped %d irrelevant)",
                len(enriched_results), len(kept), len(enriched_results) - len(kept),
            )
        return kept

    async def _detect_data_gap(
        self,
        topic: str,
        pool_summary: str,
        new_facts: list[str],
        existing_validated: list[str],
    ) -> tuple[bool, list[str]]:
        """Check if new facts reveal a critical gap vs existing pool data.

        Returns (has_gap, supplementary_queries).
        """
        if not new_facts or not pool_summary:
            return False, []

        new_text = "\n".join(f"- {f[:120]}" for f in new_facts[:6])
        existing_text = "\n".join(f"- {f[:120]}" for f in existing_validated[:6])

        try:
            gap = await self.respond_typed(
                DataGapDetection,
                context="",
                user_message=(
                    f"辩论议题：「{topic}」\n\n"
                    f"【数据池已有的已验证事实】\n{existing_text or '（空）'}\n\n"
                    f"【本次搜索新发现的事实】\n{new_text}\n\n"
                    "判断：新发现的事实是否揭示了一个数据池遗漏的关键事件或信息？\n"
                    "例如：数据池只有欧冠比赛数据，但新事实提到了另一场英超联赛。\n"
                    "或者：数据池只有A球员的数据，但新事实涉及B球员的关键表现。\n\n"
                    "标准：\n"
                    "- 新事实提到的时间/事件/实体与数据池中已有的明显不同 "
                    "→ has_gap=true，说明需要补充搜索什么\n"
                    "- 新事实只是对已有信息的补充或佐证 "
                    "→ has_gap=false\n"
                    "- 新事实提到的信息对辩论议题有直接影响 "
                    "→ has_gap=true\n\n"
                    "如果 has_gap=true，生成 1-2 个补充搜索关键词来填补这个缺口。\n"
                    f"{_time_awareness_hint()}\n\n"
                    '输出JSON：{"has_gap": true/false, "gap_description": "缺口描述", '
                    '"supplementary_queries": ["关键词1"], "reasoning": "为什么"}'
                ),
            )
            if gap.has_gap and gap.supplementary_queries:
                logger.info(
                    "Data gap detected: %s → supplementary queries: %s",
                    gap.gap_description[:80], gap.supplementary_queries,
                )
                return True, gap.supplementary_queries[:MAX_QUERIES]
        except Exception as e:
            logger.warning("Data gap detection failed: %s", e)

        return False, []
