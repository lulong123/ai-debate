"""Data Clerk agent: fetches real-time data for debate participants."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable

from app.agents.base import BaseAgent, load_prompt
from app.models.schemas import (
    CrossValidatedFacts,
    DataSufficiency,
    ExtractedFacts,
    NeedDecomposition,
    PoolSufficiency,
    RefinementQueries,
    ResearchPlan,
    ResearchStep,
    ScreenedResults,
    SearchQueries,
    VerifiedResults,
)
from app.services.search import SearchProvider, fetch_page_content

# Callback type: (queries, results) -> None
# Called after each search batch so callers can emit SSE events.
OnSearchCallback = Callable[[list[str], list[dict]], Awaitable[None]] | None

logger = logging.getLogger(__name__)

MAX_QUERIES = 2
MAX_TOTAL_RESULTS = 6
MAX_EXTRACT_URLS = 3  # Limit fact extraction to top-N URLs to avoid rate limits

# Semaphore to limit concurrent LLM calls (prevents API rate limiting)
_llm_semaphore = asyncio.Semaphore(2)


def _time_awareness_hint() -> str:
    """Build a time-awareness hint from current date for data clerk prompts."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y年%m月%d日")
    day_str = now.strftime("%m月%d日")
    yesterday = now.replace(day=now.day - 1) if now.day > 1 else now
    yesterday_str = yesterday.strftime("%m月%d日")
    return (
        f"注意：今天是 {date_str}（星期{'一二三四五六日'[now.weekday()]}）。\n"
        "议题中如果出现「今天」「昨晚」「最近」「本赛季」等时间词，\n"
        "你需要选择最可能搜到结果的查询策略：\n\n"
        "【关键规则】根据时间远近选择策略：\n"
        "- 议题说「今天/昨天/上一场」→ 距今太近，搜索引擎可能还没索引到。"
        "不要死磕具体日期！用「XX 最新比赛」「XX latest game」「XX 最近表现」"
        "等灵活查询，配合球员/球队名。一个关键词用日期，另一个用「最新」类。\n"
        "- 议题说「本赛季」→ 用「{season}赛季」配合球员名。\n"
        "- 议题说更早的时间 → 可以用具体日期。\n\n"
        "【查询策略多样性】\n"
        "- 一个中文关键词 + 一个英文关键词（如「詹姆斯 最新比赛」+ "
        "\"LeBron James latest game stats\"）\n"
        "- 不要两个关键词都带精确日期，至少一个用「最新」「recent」等灵活词\n"
        "- 体育数据类：搜「XX 比分」「XX 战报」比搜「XX 数据」更可能找到新闻\n\n"
        f"错误示例：「哈登 {day_str} 比赛 数据」（太具体，昨天比赛可能没文章）\n"
        f"正确示例：「哈登 最新比赛」或「哈登 {yesterday_str} 战报」（一个灵活+一个具体）"
    ).format(season=f"{now.year-1}-{now.year}")


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
        model = settings.data_clerk_model or None
        super().__init__(
            system_prompt=load_prompt("data_clerk.md"),
            model=model,
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

    async def fetch_for_agent(
        self, topic: str, agent_context: str, position_name: str,
        round_num: int, search_provider: SearchProvider,
        existing_pool_summary: str = "", data_scope: str = "",
        agent_requests: list[str] | None = None,
        on_search: OnSearchCallback = None,
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
                results = await search_provider.search(query, max_results=3)
                logger.info("Search results for '%s': %d items", query, len(results))
                for r in results:
                    logger.info("  - %s | %s", r.title[:80], r.snippet[:120])
                return [r.to_dict() for r in results]
            except Exception as e:
                if "429" in str(e) or "rate" in str(e).lower():
                    logger.warning("Rate limited on query '%s', backing off 1s", query)
                    await asyncio.sleep(1)
                    try:
                        results = await search_provider.search(query, max_results=3)
                        return [r.to_dict() for r in results]
                    except Exception:
                        return []
                logger.warning("Search failed for query '%s': %s", query, e)
                return []

        batch_results = await asyncio.gather(*[_safe_search(q) for q in queries])
        flat = [r for batch in batch_results for r in batch]
        results = flat[:MAX_TOTAL_RESULTS]

        if on_search:
            try:
                await on_search(queries, results)
            except Exception as e:
                logger.warning("on_search callback failed: %s", e)

        return results

    async def research_topic(
        self, topic: str, search_provider: SearchProvider, max_steps: int = 3,
        on_search: OnSearchCallback = None,
    ) -> list[dict]:
        """Chain-of-thought research: decompose topic into steps, search sequentially.

        Step 1: Identify entities and their current context (who? where?)
        Step 2: Based on step 1 findings, find the specific event (which game/event?)
        Step 3: Based on step 2, get specific data (stats/scores)

        Each step builds on the findings of previous steps.
        """
        # Phase 1: LLM generates a multi-step research plan
        plan = await self.respond_typed(
            ResearchPlan,
            context="",
            user_message=(
                f"辩论议题：「{topic}」\n"
                f"{_time_awareness_hint()}\n\n"
                "你需要制定一个分步搜索计划来获取这个议题所需的事实数据。\n\n"
                "重要原则：不要假设任何你可能记错的信息（如球员所属球队、比赛日期等）。\n"
                "每一步搜索都应该基于**已知事实**，而不是你的训练数据记忆。\n\n"
                "推荐的搜索策略：\n"
                "- 第1步：搜索议题中的关键实体当前状态（如球员现在在哪个队、人物最新动态）\n"
                "  关键词用灵活的「最新」「latest」「战报」类，不要死磕具体日期\n"
                "- 第2步：基于第1步发现的真实信息，搜索具体事件（如具体哪场比赛）\n"
                "  如果第1步找到了具体日期/比赛，这一步用具体信息搜索\n"
                "- 第3步：基于第2步，搜索具体数据（如比赛统计、评分等）\n\n"
                "每步最多 2 个搜索关键词，总共最多 {0} 步。\n"
                "如果议题不需要多步搜索（如纯观点讨论），可以只给 1 步。\n\n"
                "输出JSON格式，每步包含 reasoning（为什么搜这个）和 search_queries（关键词列表）。"
            ),
        )

        if not plan.steps:
            logger.info("Research plan empty for topic '%s', falling back to simple search", topic)
            return await self.fetch_for_topic(topic, search_provider, on_search=on_search)

        logger.info(
            "Research plan for '%s': %d steps",
            topic, len(plan.steps),
        )

        # Phase 2: Execute steps as a true chain — each step's queries are
        # informed by the accumulated findings from all previous steps.
        all_results: list[dict] = []
        accumulated_context = ""

        for i, step in enumerate(plan.steps[:max_steps]):
            # Step 0 uses the original plan queries.
            # Step 1+ asks the LLM to adjust queries based on what we found.
            if i == 0:
                queries = step.search_queries[:MAX_QUERIES]
            else:
                try:
                    adjusted = await self.respond_typed(
                        ResearchStep,
                        context=accumulated_context,
                        user_message=(
                            f"辩论议题：「{topic}」\n"
                            f"研究计划第 {i+1} 步的目标：{step.reasoning}\n\n"
                            "基于前面步骤的实际发现，生成调整后的搜索关键词。\n"
                            "如果前面的发现已经足够回答这一步的目标，"
                            '返回 {"reasoning": "...", "search_queries": []}。\n'
                            f"最多 2 个关键词。{_time_awareness_hint()}"
                        ),
                    )
                    queries = adjusted.search_queries[:MAX_QUERIES]
                    logger.info(
                        "Step %d queries adjusted: %s → %s",
                        i + 1,
                        step.search_queries[:MAX_QUERIES], queries,
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
                    results = await search_provider.search(q, max_results=3)
                    logger.info("  Query '%s': %d results", q, len(results))
                    for r in results:
                        logger.info("    - %s | %s", r.title[:60], r.snippet[:80])
                    step_results.extend([r.to_dict() for r in results])
                except Exception as e:
                    logger.warning("  Search failed for '%s': %s", q, e)

            if step_results:
                all_results.extend(step_results)
                findings = "\n".join(
                    f"  - {r.get('title', '')}: {r.get('snippet', '')[:100]}"
                    for r in step_results[:4]
                )
                accumulated_context += f"\n第{i+1}步发现：\n{findings}\n"

            # Notify caller of this step's queries and results
            if on_search:
                try:
                    await on_search(queries, step_results)
                except Exception as e:
                    logger.warning("on_search callback failed in step %d: %s", i + 1, e)

        logger.info("Research complete: %d total results for '%s'", len(all_results), topic)
        return all_results[:MAX_TOTAL_RESULTS * 2]

    async def fetch_for_topic(
        self, topic: str, search_provider: SearchProvider,
        on_search: OnSearchCallback = None,
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
                results = await search_provider.search(query, max_results=3)
                logger.info("Search results for '%s': %d items", query, len(results))
                for r in results:
                    logger.info("  - %s | %s", r.title[:80], r.snippet[:120])
                return [r.to_dict() for r in results]
            except Exception as e:
                if "429" in str(e) or "rate" in str(e).lower():
                    logger.warning("Rate limited on query '%s', backing off 1s", query)
                    await asyncio.sleep(1)
                    try:
                        results = await search_provider.search(query, max_results=3)
                        return [r.to_dict() for r in results]
                    except Exception:
                        return []
                logger.warning("Search failed for query '%s': %s", query, e)
                return []

        batch_results = await asyncio.gather(*[_safe_search(q) for q in queries])
        flat = [r for batch in batch_results for r in batch]
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

    async def extract_facts(self, url: str, query: str, fallback_content: str = "") -> ExtractedFacts:
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

        try:
            result = await self.respond_typed(
                ExtractedFacts,
                context="",
                user_message=(
                    f"搜索关键词：{query}\n\n"
                    f"以下是从网页提取的原始内容：\n{content_for_llm}\n\n"
                    "请从以上内容中提取与搜索关键词直接相关的关键事实。\n"
                    "要求：\n"
                    "- 每条事实必须是一个完整、自包含的陈述（不需要看原文就能理解）\n"
                    "- 只提取客观事实（数据、日期、事件、人名等），不要提取观点或推测\n"
                    "- 每条不超过100字\n"
                    "- 最多提取5条最关键的事实\n"
                    "- 如果内容与关键词无关，返回空列表\n\n"
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
                    facts = await self.extract_facts(url or "", query, fallback_content=snippet)
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

        results_text = "\n".join(
            f"[{i+1}] 标题：{r.get('title', '')}\n    摘要：{r.get('snippet', '')[:150]}"
            for i, r in enumerate(results)
        )

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

        try:
            async with _llm_semaphore:
                screened = await self.respond_typed(
                    ScreenedResults,
                    context="",
                user_message=(
                    f"辩论议题：「{topic}」\n\n"
                    f"【搜索结果（仅标题和摘要）】\n{results_text}\n\n"
                    "快速筛查每条结果：\n"
                    "1. 是否与议题相关？（不相关的排除）\n"
                    "2. 是否与已知信息矛盾？（矛盾的排除）\n"
                    "3. 是否重复？（重复的保留最详细的一条）\n"
                    "4. 是否超出数据边界？（超出的排除）\n\n"
                    "注意：只根据标题和摘要判断，不需要读取网页内容。\n"
                    "宁可多保留（宁可多读一个网页），不要误排除。\n"
                    "只有明显不相关或明显矛盾的才排除。\n"
                    f"{context_note}{scope_note}\n"
                    '输出JSON：{"kept": [{"title":"...", "snippet":"...", "url":"..."}], '
                    '"rejected": ["排除原因1", ...], '
                    '"screening_note": "筛查说明"}'
                ),
            )

            if screened.kept:
                logger.info(
                    "Screened %d -> %d results (rejected %d: %s)",
                    len(results), len(screened.kept), len(screened.rejected),
                    screened.rejected[:3],
                )
                return screened.kept

            # All rejected — genuine "no relevant results"
            if screened.rejected:
                logger.info(
                    "All %d results screened out: %s",
                    len(results), screened.rejected[:3],
                )
                return []

            return results  # LLM returned nothing useful, use all
        except Exception as e:
            logger.warning("Screening failed, using all results: %s", e)
            return results

    async def research_with_validation(
        self,
        topic: str,
        search_provider: SearchProvider,
        existing_context: str = "",
        data_scope: str = "",
        max_iterations: int = 3,
        on_search: OnSearchCallback = None,
        on_progress: Callable[[dict], Awaitable[None]] | None = None,
    ) -> tuple[list[dict], CrossValidatedFacts]:
        """Full pipeline: research -> screen -> extract -> validate -> iterate.

        Returns (enriched_results, final_validation).

        NOTE: DB persistence is NOT handled here. The caller is responsible
        for persisting results to the data pool.

        on_progress receives a dict (event payload) WITHOUT session_id.
        Caller wraps: lambda evt: self._emit(session_id, evt)
                  or: lambda evt: publish(session_id, evt)
        """
        # Phase 1: Initial research
        raw_results = await self.research_topic(
            topic, search_provider, on_search=on_search,
        )
        if not raw_results:
            return [], CrossValidatedFacts()

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
            return [], CrossValidatedFacts(note="所有搜索结果被筛查排除")

        # Phase 3: Extract facts (web_reader only for screened results)
        enriched = await self.extract_facts_batch(screened, topic)

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

            try:
                refinement = await self.respond_typed(
                    RefinementQueries,
                    context="",
                    user_message=(
                        f"辩论议题：「{topic}」\n\n"
                        f"【已验证的事实】\n"
                        f"{[v.get('fact', '')[:100] for v in validation.validated]}\n\n"
                        f"【矛盾的事实】\n{contradictions_text or '无'}\n\n"
                        f"【未验证的单源事实】\n{unique_text or '无'}\n\n"
                        "当前的搜索结果验证不充分（互相佐证的事实不足2条）。\n"
                        "请生成1-2个新的搜索关键词，"
                        "专门针对矛盾焦点或未验证事实进行补充搜索。\n"
                        f"{_time_awareness_hint()}\n\n"
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
                    results = await search_provider.search(q, max_results=3)
                    new_results.extend([r.to_dict() for r in results])
                except Exception as e:
                    logger.warning("Iterative search failed for '%s': %s", q, e)

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
            )

            if not new_screened:
                iterations_done += 1
                continue

            # Extract facts from new results
            new_enriched = await self.extract_facts_batch(new_screened, topic)

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

        return all_enriched, validation

    # ── Semantic Intent Protocol ────────────────────────────────────

    async def research_for_agent(
        self,
        topic: str,
        semantic_need: str,
        search_provider: SearchProvider,
        pool_summary: str = "",
        data_scope: str = "",
        max_iterations: int = 2,
        on_search: OnSearchCallback = None,
        on_progress: Callable[[dict], Awaitable[None]] | None = None,
    ) -> tuple[list[dict], CrossValidatedFacts]:
        """Targeted research driven by an agent's semantic data need.

        Optimized pipeline (reduces LLM calls to avoid rate limiting):
        1. Pool sufficiency — skip search if pool already answers the question
        2. Need decomposition — convert semantic need → 1-2 search queries
        3. Search → screen → extract (top 3 URLs only) → validate
        4. If validated < 1, iterate once more with gap-targeted queries
        """
        if not semantic_need:
            return [], CrossValidatedFacts()

        # Step 1: Pool sufficiency check
        if pool_summary:
            try:
                async with _llm_semaphore:
                    pool_check = await self.respond_typed(
                        PoolSufficiency,
                        context="",
                        user_message=(
                            f"辩论议题：「{topic}」\n\n"
                            f"【辩手/主持人的数据需求】\n{semantic_need}\n\n"
                            f"【当前数据池已有信息】\n{pool_summary}\n\n"
                            "判断：数据池中是否已有足够信息回答这个需求？\n"
                            "- 完整回答 → sufficient=true\n"
                            "- 部分满足 → sufficient=false，说明缺什么\n"
                            "- 完全不相关 → sufficient=false"
                        ),
                    )
                if pool_check.sufficient:
                    logger.info(
                        "Pool sufficient for need '%s...': %s",
                        semantic_need[:50], pool_check.reasoning,
                    )
                    return [], CrossValidatedFacts(note="数据池已满足需求")
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

        for iteration in range(max_iterations):
            # Step 2: Need decomposition
            missing_note = ""
            if missing_aspects:
                missing_note = f"\n已知缺失：{'、'.join(missing_aspects)}"

            try:
                async with _llm_semaphore:
                    decomposition = await self.respond_typed(
                        NeedDecomposition,
                        context="",
                        user_message=(
                            f"辩论议题：「{topic}」\n\n"
                            f"【数据需求】\n{semantic_need}\n\n"
                            f"{missing_note}\n\n"
                            "将数据需求分解为 1-2 个搜索关键词。\n"
                            "- 关键词必须能搜到具体数据\n"
                            "- 一个中文 + 一个英文为佳\n"
                            "- 只搜模型可能不知道的最新信息\n"
                            "- 必须至少返回1个搜索关键词\n"
                            f"{_time_awareness_hint()}\n\n"
                            '输出JSON：{"queries": ["关键词1", "关键词2"], "reasoning": "为什么搜这些"}'
                        ),
                    )
            except Exception as e:
                logger.warning("Need decomposition failed: %s", e)
                break

            queries = decomposition.queries[:MAX_QUERIES]
            if not queries:
                # Fallback: extract keywords directly from semantic need
                queries = [semantic_need[:50]]
                if len(semantic_need) > 10:
                    queries.append(f"{semantic_need[:30]} stats data")
                queries = queries[:MAX_QUERIES]
                logger.info(
                    "Need decomposition returned empty, using fallback queries: %s",
                    queries,
                )

            logger.info(
                "Need decomposition iteration %d: %s (reasoning: %s)",
                iteration + 1, queries, decomposition.reasoning[:80],
            )

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
                    results = await search_provider.search(q, max_results=3)
                    raw_results.extend([r.to_dict() for r in results])
                except Exception as e:
                    logger.warning("Search failed for '%s': %s", q, e)

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

            # LLM-based screen (catches subtler mismatches)
            screened = await self.screen_results(
                scope_filtered, topic,
                existing_context=pool_summary,
                data_scope=data_scope,
            )
            if not screened:
                continue

            # Extract facts (limited to top 3 URLs to control LLM calls)
            enriched = await self.extract_facts_batch(
                screened, topic, max_urls=MAX_EXTRACT_URLS,
            )

            # Deduplicate and merge
            for r in enriched:
                url = r.get("url", "")
                if url not in seen_urls:
                    all_enriched.append(r)
                    seen_urls.add(url)

            # Cross-validate all accumulated results
            validation = await self.cross_validate_facts(all_enriched, topic)

            # Early exit if we have validated facts
            if validation.validated:
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
                return all_enriched, validation

            # Collect gaps for next iteration
            missing_aspects = []
            for u in (validation.unique or [])[:3]:
                missing_aspects.append(str(u.get("fact", ""))[:80])

        # Return best available even if not fully sufficient
        logger.info(
            "research_for_agent complete: %d results, validated=%d",
            len(all_enriched), len(validation.validated),
        )
        return all_enriched, validation

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
