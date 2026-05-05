"""Abstract search service supporting multiple providers."""

import logging
from abc import ABC, abstractmethod

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class SearchResult:
    def __init__(self, title: str, snippet: str, url: str):
        self.title = title
        self.snippet = snippet
        self.url = url

    def to_dict(self):
        return {"title": self.title, "snippet": self.snippet, "url": self.url}


class SearchProvider(ABC):
    @abstractmethod
    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        ...


class ZhipuSearchProvider(SearchProvider):
    """Search via Zhipu API."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client = httpx.AsyncClient(timeout=30)

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        try:
            resp = await self._client.post(
                "https://open.bigmodel.cn/api/paas/v4/tools",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "tool": "web_search_pro",
                    "messages": [{"role": "user", "content": query}],
                    "stream": False,
                },
            )
            if resp.status_code != 200:
                logger.warning("Zhipu search failed: %d", resp.status_code)
                return []
            data = resp.json()
            return [
                SearchResult(
                    title=item.get("title", ""),
                    snippet=item.get("content", ""),
                    url=item.get("link", ""),
                )
                for item in data.get("search_result", [])[:max_results]
            ]
        except Exception as e:
            logger.warning("Zhipu search error: %s", e)
            return []

    async def close(self):
        await self._client.aclose()


class TavilySearchProvider(SearchProvider):
    """Search via Tavily API."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client = httpx.AsyncClient(timeout=30)

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        try:
            resp = await self._client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": self.api_key,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",
                },
            )
            if resp.status_code != 200:
                logger.warning("Tavily search failed: %d", resp.status_code)
                return []
            data = resp.json()
            return [
                SearchResult(
                    title=item.get("title", ""),
                    snippet=item.get("content", ""),
                    url=item.get("url", ""),
                )
                for item in data.get("results", [])
            ]
        except Exception as e:
            logger.warning("Tavily search error: %s", e)
            return []

    async def close(self):
        await self._client.aclose()


class NoOpSearchProvider(SearchProvider):
    """Returns empty results when search is disabled."""

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        return []

    async def close(self):
        pass


def get_search_provider() -> SearchProvider:
    """Factory: create search provider based on config."""
    provider = settings.search_provider.lower()
    if provider == "zhipu" and settings.zhipu_search_api_key:
        return ZhipuSearchProvider(settings.zhipu_search_api_key)
    elif provider == "tavily" and settings.tavily_api_key:
        return TavilySearchProvider(settings.tavily_api_key)
    return NoOpSearchProvider()
