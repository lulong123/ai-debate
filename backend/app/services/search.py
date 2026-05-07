"""Abstract search service supporting multiple providers."""

import json
import logging
from abc import ABC, abstractmethod

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

MCP_URL = "https://open.bigmodel.cn/api/mcp/web_search_prime/mcp"


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


def _parse_sse_json(text: str) -> dict:
    """Extract JSON from SSE response."""
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("data:"):
            data = line[5:].strip()
            if data:
                return json.loads(data)
    raise ValueError("SSE response contains no data")


def _extract_results(raw_text: str) -> list[SearchResult]:
    """Parse MCP search result text into SearchResult list."""
    if raw_text.startswith("MCP error"):
        logger.warning("MCP search blocked: %s", raw_text)
        return []

    try:
        inner = json.loads(raw_text)
        if isinstance(inner, str):
            items = json.loads(inner)
        else:
            items = inner

        if isinstance(items, list):
            return [
                SearchResult(
                    title=item.get("title", ""),
                    snippet=item.get("content", ""),
                    url=item.get("link", ""),
                )
                for item in items
            ]
    except (json.JSONDecodeError, TypeError, KeyError):
        pass

    return []


class ZhipuMCPSearchProvider(SearchProvider):
    """Search via Zhipu MCP web_search_prime. Uses the same API key as LLM."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                # 1. Initialize MCP session
                init_resp = await client.post(MCP_URL, headers=headers, json={
                    "jsonrpc": "2.0",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "ai-roundtable", "version": "1.0.0"},
                    },
                    "id": 1,
                })
                sid = init_resp.headers.get("mcp-session-id")
                if sid:
                    headers["Mcp-Session-Id"] = sid

                # 2. Initialized notification
                await client.post(MCP_URL, headers=headers, json={
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                })

                # 3. Search call
                search_resp = await client.post(MCP_URL, headers=headers, json={
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {
                        "name": "web_search_prime",
                        "arguments": {
                            "search_query": query,
                            "location": "cn",
                            "content_size": "medium",
                        },
                    },
                    "id": 2,
                })

                result = _parse_sse_json(search_resp.text)

                if "result" in result:
                    content = result["result"].get("content", [])
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            return _extract_results(item["text"])[:max_results]

                logger.warning("MCP search unexpected response: %s", json.dumps(result, ensure_ascii=False)[:200])
                return []

        except Exception as e:
            logger.warning("Zhipu MCP search error: %s", e)
            return []

    async def close(self):
        pass


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
    if provider == "zhipu" and settings.llm_api_key:
        return ZhipuMCPSearchProvider(settings.llm_api_key)
    elif provider == "tavily" and settings.tavily_api_key:
        return TavilySearchProvider(settings.tavily_api_key)
    return NoOpSearchProvider()
