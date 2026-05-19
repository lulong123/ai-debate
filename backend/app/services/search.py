"""Abstract search service supporting multiple providers."""

import json
import logging
from abc import ABC, abstractmethod

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

MCP_SEARCH_URL = "https://open.bigmodel.cn/api/mcp/web_search_prime/mcp"
MCP_READER_URL = "https://open.bigmodel.cn/api/mcp/web_reader/mcp"


class SearchResult:
    def __init__(self, title: str, snippet: str, url: str, publish_date: str = ""):
        self.title = title
        self.snippet = snippet
        self.url = url
        self.publish_date = publish_date

    def to_dict(self):
        d = {"title": self.title, "snippet": self.snippet, "url": self.url}
        if self.publish_date:
            d["publish_date"] = self.publish_date
        return d


# Recency filter values: oneDay/oneWeek/oneMonth/oneYear/noLimit
# Maps to Tavily `days` parameter (Zhipu uses the string directly)
RECENCY_DAY_MAP: dict[str, int | None] = {
    "oneDay": 1, "oneWeek": 7, "oneMonth": 30, "oneYear": 365, "noLimit": None,
}


class SearchProvider(ABC):
    @abstractmethod
    async def search(
        self, query: str, max_results: int = 5, recency: str = "noLimit",
    ) -> list[SearchResult]:
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
                    publish_date=item.get("date", ""),
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

    async def search(
        self, query: str, max_results: int = 5, recency: str = "noLimit",
    ) -> list[SearchResult]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                # 1. Initialize MCP session
                init_resp = await client.post(MCP_SEARCH_URL, headers=headers, json={
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
                await client.post(MCP_SEARCH_URL, headers=headers, json={
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                })

                # 3. Search call
                arguments = {
                    "search_query": query,
                    "location": "cn",
                    "content_size": "high",
                }
                if recency != "noLimit":
                    arguments["search_recency_filter"] = recency

                search_resp = await client.post(MCP_SEARCH_URL, headers=headers, json={
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {
                        "name": "web_search_prime",
                        "arguments": arguments,
                    },
                    "id": 2,
                })

                result = _parse_sse_json(search_resp.text)

                if "result" in result:
                    content = result["result"].get("content", [])
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            return _extract_results(item["text"])[:max_results]

                logger.warning(
                    "MCP search unexpected response: %s",
                    json.dumps(result, ensure_ascii=False)[:200],
                )
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

    async def search(
        self, query: str, max_results: int = 5, recency: str = "noLimit",
    ) -> list[SearchResult]:
        try:
            params = {
                "api_key": self.api_key,
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
            }
            days = RECENCY_DAY_MAP.get(recency)
            if days is not None:
                params["days"] = days

            resp = await self._client.post(
                "https://api.tavily.com/search",
                json=params,
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
                    publish_date=item.get("published_date", ""),
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

    async def search(
        self, query: str, max_results: int = 5, recency: str = "noLimit",
    ) -> list[SearchResult]:
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


def get_statmuse_provider() -> "StatMuseProvider | None":
    """Create StatMuse provider if enabled in config."""
    if settings.statmuse_enabled:
        from app.services.statmuse import StatMuseProvider
        return StatMuseProvider()
    return None


async def fetch_page_content(url: str, timeout: int = 30) -> str:
    """Fetch clean content from a URL. Three-route strategy:

    1. Zhipu MCP Web Reader (server-side JS rendering, best for SPA sites)
    2. Direct httpx fetch + BeautifulSoup (good for SSR sites)
    3. Empty string on total failure.

    Returns extracted text (capped at 5000 chars), or empty string on failure.
    """
    api_key = settings.llm_api_key

    # Route 1: Zhipu MCP Web Reader (handles JS rendering)
    if api_key:
        try:
            content = await _fetch_via_mcp_reader(url, api_key, timeout)
            if content:
                logger.info("MCP Reader succeeded for %s (%d chars)", url[:80], len(content))
                return content[:5000]
        except Exception as e:
            logger.info("MCP Reader failed for %s: %s, trying BeautifulSoup", url[:80], e)

    # Route 2: Direct fetch + BeautifulSoup
    return await _fetch_and_extract_html(url, timeout=10)


async def _fetch_via_mcp_reader(url: str, api_key: str, timeout: int = 30) -> str:
    """Fetch page content via Zhipu MCP web_reader (server-side JS rendering)."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        # 1. Initialize MCP session
        init_resp = await client.post(MCP_READER_URL, headers=headers, json={
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "ai-roundtable", "version": "1.0.0"},
            },
            "id": 1,
        })
        init_resp.raise_for_status()
        sid = init_resp.headers.get("mcp-session-id")
        if sid:
            headers["Mcp-Session-Id"] = sid

        # 2. Initialized notification
        await client.post(MCP_READER_URL, headers=headers, json={
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })

        # 3. Call webReader tool
        read_resp = await client.post(MCP_READER_URL, headers=headers, json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "webReader",
                "arguments": {"url": url},
            },
            "id": 2,
        })

        result = _parse_sse_json(read_resp.text)

        if "result" in result:
            content_list = result["result"].get("content", [])
            for item in content_list:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_val = item["text"]
                    # Response is a JSON string with title/content/url
                    if isinstance(text_val, str):
                        try:
                            parsed = json.loads(text_val)
                            if isinstance(parsed, dict):
                                page_content = parsed.get("content", "")
                                page_title = parsed.get("title", "")
                                if page_content:
                                    cleaned = page_content.strip()
                                    if page_title:
                                        cleaned = f"# {page_title}\n\n{cleaned}"
                                    return cleaned[:5000]
                            elif isinstance(parsed, str):
                                # JSON decoded to a string (double-encoded)
                                return parsed.strip()[:5000]
                            elif isinstance(parsed, list):
                                # JSON decoded to a list — join items
                                parts = [str(p) for p in parsed if p]
                                if parts:
                                    return "\n".join(parts)[:5000]
                        except (json.JSONDecodeError, TypeError):
                            # Plain text response
                            if text_val.strip():
                                return text_val.strip()[:5000]
                    elif isinstance(text_val, dict):
                        page_content = text_val.get("content", "")
                        if page_content:
                            return page_content[:5000]

        return ""


async def _fetch_and_extract_html(url: str, timeout: int = 10) -> str:
    """Fetch HTML directly and extract main text content via BeautifulSoup."""
    try:
        from bs4 import BeautifulSoup, UnicodeDammit

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml,*/*",
                },
            )
            resp.raise_for_status()

        # Robust encoding detection: let BeautifulSoup's UnicodeDammit handle it
        # This correctly detects GBK, GB2312, Big5 etc. common in Chinese sites
        dammit = UnicodeDammit(resp.content)
        html_text = dammit.unicode_markup or resp.content.decode("utf-8", errors="replace")

        soup = BeautifulSoup(html_text, "lxml")

        # Remove noise elements
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
            tag.decompose()

        # Try to find main content area
        main = (
            soup.find("article")
            or soup.find("main")
            or soup.find(class_=lambda c: c and any(w in str(c).lower() for w in ("content", "article", "body")))
            or soup.find("body")
            or soup
        )

        text = main.get_text(separator="\n", strip=True) if main else ""

        # Clean up: remove excessive blank lines
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        cleaned = "\n".join(lines)

        # SPA detection: if very little content extracted, likely a JS-rendered page
        if len(cleaned) < 500:
            logger.warning(
                "Direct fetch got only %d chars from %s (likely SPA/JS-rendered). "
                "Content preview: %s",
                len(cleaned), url[:80], cleaned[:200],
            )
        else:
            logger.info(
                "Direct fetch extracted %d chars from %s (encoding: %s). Preview: %s",
                len(cleaned), url[:80], dammit.original_encoding, cleaned[:200],
            )
        return cleaned[:5000]

    except Exception as e:
        logger.warning("Direct fetch failed for %s: %s", url, e)
        return ""
