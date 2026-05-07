"""Data Clerk agent: fetches real-time data for debate participants."""

import asyncio
import logging

from datetime import datetime, timezone

from app.agents.base import BaseAgent, load_prompt
from app.models.schemas import SearchQueries, VerifiedResults
from app.services.search import SearchProvider

logger = logging.getLogger(__name__)

MAX_QUERIES = 2
MAX_TOTAL_RESULTS = 6


def _time_awareness_hint() -> str:
    """Build a time-awareness hint from current date for data clerk prompts."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y年%m月%d日")
    return (
        f"注意：今天是 {date_str}。\n"
        "议题中如果出现「今天」「昨晚」「最近」「本赛季」等时间词，"
        "你必须将它们转换为搜索关键词中的具体日期。\n"
        f"例：议题说「今天」→ 搜索关键词带「{now.strftime("%m月%d日")}」或「{date_str}」；"
        f"议题说「本赛季」→ 带「{now.year-1}-{now.year}赛季」。"
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
        model = settings.data_clerk_model or None
        super().__init__(
            system_prompt=load_prompt("data_clerk.md"),
            model=model,
        )

    async def decide_queries(
        self, topic: str, agent_context: str, position_name: str, round_num: int,
        existing_pool_summary: str = "", data_scope: str = "",
    ) -> list[str]:
        """Let LLM decide what search queries are needed for this agent."""
        pool_note = ""
        if existing_pool_summary:
            pool_note = (
                f"\n\n【当前数据池已有信息】\n{existing_pool_summary}\n"
                "如果已有信息足够，不需要重复搜索。只搜索数据池中缺失的最新信息。"
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
                f"{pool_note}\n"
                '输出JSON：{"searches": ["关键词1", "关键词2"]}\n'
                '如果不需要搜索（已有信息足够或没有明确的信息需求）：{"searches": []}'
            ),
        )
        return result.searches[:MAX_QUERIES]

    async def fetch_for_agent(
        self, topic: str, agent_context: str, position_name: str,
        round_num: int, search_provider: SearchProvider,
        existing_pool_summary: str = "", data_scope: str = "",
    ) -> list[dict]:
        """Full fetch cycle: decide queries -> parallel search -> return results."""
        queries = await self.decide_queries(
            topic, agent_context, position_name, round_num,
            existing_pool_summary=existing_pool_summary,
            data_scope=data_scope,
        )
        if not queries:
            return []

        async def _safe_search(query: str) -> list[dict]:
            try:
                results = await search_provider.search(query, max_results=3)
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
        return flat[:MAX_TOTAL_RESULTS]

    async def fetch_for_topic(
        self, topic: str, search_provider: SearchProvider
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
                "只搜索模型可能不知道的最新信息，不要搜索常识。\n\n"
                '输出JSON：{"searches": ["关键词1", "关键词2"]}\n'
                '如果不需要搜索：{"searches": []}'
            ),
        )
        queries = result.searches[:MAX_QUERIES]
        if not queries:
            return []

        async def _safe_search(query: str) -> list[dict]:
            try:
                results = await search_provider.search(query, max_results=3)
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
        return flat[:MAX_TOTAL_RESULTS]

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
