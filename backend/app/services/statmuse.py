"""StatMuse data provider — sports and finance real-time stats.

Queries statmuse.com for authoritative, structured data. The site uses
Astro SSR so all content is available via plain HTTP (no JS rendering).

URL pattern: https://www.statmuse.com/{category}/ask/{hyphenated-question}
Categories: nba, nfl, nhl, fc (soccer), mlb, wnba, cfb, pga, money
"""

import asyncio
import logging
import re
import time
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.services.search import SearchProvider, SearchResult

logger = logging.getLogger(__name__)

_STATMUSE_BASE = "https://www.statmuse.com"

# Each category maps to a list of keywords that indicate the query belongs there.
# The lists are intentionally English-only — data_clerk generates English queries
# alongside Chinese ones, and StatMuse only works with English.
_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "nba": [
        "nba", "basketball", "points per game", "rebounds", "assists",
        "field goal", "three point", "free throw", "triple double",
        "double double", "turnover", "steals", "blocks",
        # Common team/player hints
        "lakers", "warriors", "celtics", "bucks", "nuggets",
        "playoffs", "finals mvp", "all-star", "all star",
        "ppg", "rpg", "apg", "spg", "bpg",
        # Player surname fragments that are uniquely NBA
        "lebron", "curry", "harden", "antetokounmpo", "jokic",
        "doncic", "tatum", "embiid", "wembanyama",
    ],
    "fc": [
        "soccer", "football", "premier league", "la liga", "serie a",
        "bundesliga", "ligue 1", "champions league", "europa league",
        "goal", "assist", "clean sheet", "penalty", "yellow card", "red card",
        "passing yards", "xg", "expected goals", "shots on target",
        "ballon d'or", "world cup", "euro",
        # Player names
        "messi", "ronaldo", "haaland", "mbappe", "salah", "kane",
        "declan rice", "saka", "odegaard", "trossard",
        # Club names
        "manchester", "arsenal", "liverpool", "chelsea", "barcelona",
        "real madrid", "bayern", "psg",
    ],
    "nfl": [
        "nfl", "super bowl", "touchdown", "quarterback", "running back",
        "wide receiver", "passing yards", "rushing yards", "sack",
        "interception", "field goal", "completion", "passer rating",
    ],
    "mlb": [
        "mlb", "baseball", "home run", "pitcher", "batting average",
        "earned run", "rbi", "strikeout", "walk", "hit by pitch",
        "world series", "no-hitter",
    ],
    "nhl": [
        "nhl", "hockey", "goalie", "stanley cup", "power play",
        "penalty kill", "save percentage", "shutout", "hat trick",
        "points", "assists",
    ],
    "money": [
        "stock price", "market cap", "revenue", "earnings", "pe ratio",
        "dividend", "52 week", "52-week", "share price", "stock market",
        "ipo", "valuation", "profit margin",
        # Company/ticker hints
        "apple stock", "tesla stock", "nvidia stock", "microsoft stock",
        "amazon stock", "google stock", "meta stock",
        "aapl", "tsla", "nvda", "msft", "amzn", "googl", "meta",
    ],
}

# Cache TTL in seconds
_CACHE_TTL = 300  # 5 minutes


# Complementary query templates per category.
# After the initial query, StatMuseProvider fires these to gather missing
# stat dimensions (e.g. passing/defensive/aerial for soccer, shooting for NBA).
# {entity} is replaced with the extracted player/team name.
_FOLLOWUP_TEMPLATES: dict[str, list[str]] = {
    "fc": [
        "{entity} last game passing stats",
        "{entity} last game tackles interceptions",
        "{entity} last game chances created",
        "{entity} last game aerial duels",
        "{entity} last game dribbles fouls",
    ],
    "nba": [
        "{entity} last game shooting splits",
        "{entity} last game advanced stats",
        "{entity} last game plus minus",
    ],
    "nfl": [
        "{entity} last game rushing stats",
        "{entity} last game receiving stats",
        "{entity} last game passing splits",
    ],
    "mlb": [
        "{entity} last game batting splits",
        "{entity} last game pitching stats",
    ],
    "nhl": [
        "{entity} last game advanced stats",
        "{entity} last game power play stats",
    ],
    "money": [],  # Single query already comprehensive
}


class StatMuseProvider(SearchProvider):
    """SearchProvider that queries statmuse.com for sports and finance data.

    Designed as a supplementary source — call alongside a general search
    provider (e.g. Zhipu) and merge results. Non-sports/finance queries
    return [] immediately (zero cost).

    For sports queries, automatically fires complementary queries to gather
    comprehensive stat dimensions (passing, defensive, aerial, etc.) and
    merges into a single enriched result.
    """

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,*/*",
            },
        )
        self._cache: dict[str, tuple[float, list[SearchResult]]] = {}

    async def search(
        self, query: str, max_results: int = 5, recency: str = "noLimit",
    ) -> list[SearchResult]:
        """Search StatMuse with automatic multi-query aggregation.

        1. Classify query into sport/finance category
        2. Fetch initial result
        3. If sports: extract entity, fire complementary queries for
           additional stat dimensions (passing, defensive, aerial, etc.)
        4. Merge all data into one comprehensive result
        """
        category = self._classify(query)
        if not category:
            return []

        url = self._build_url(query, category)

        # Check cache
        cached = self._get_cached(url)
        if cached is not None:
            return cached

        results = await self._fetch_and_parse(url)
        if not results:
            # 422 retry: simplify the query and try again
            simplified = self._simplify_query(query)
            if simplified != query.lower().strip():
                simp_url = self._build_url(simplified, category)
                logger.info(
                    "StatMuse retry simplified: '%s' -> '%s'",
                    query[:50], simplified,
                )
                results = await self._fetch_and_parse(simp_url)
            if not results:
                return []

        # Phase 2: complementary queries for comprehensive data
        templates = _FOLLOWUP_TEMPLATES.get(category, [])
        if templates:
            entity = self._extract_entity(results[0])
            if entity:
                results = await self._aggregate_comprehensive(
                    results[0], entity, category,
                )

        self._set_cached(url, results)
        return results[:max_results]

    def _classify(self, query: str) -> str | None:
        """Detect sport/finance category from query keywords.

        Returns None if the query doesn't match any category.
        """
        q_lower = query.lower()

        # Score each category by how many keywords match
        best_category: str | None = None
        best_score = 0

        for category, keywords in _CATEGORY_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in q_lower)
            if score > best_score:
                best_score = score
                best_category = category

        # Need at least 1 keyword match to classify
        if best_score == 0:
            return None

        return best_category

    def _build_url(self, query: str, category: str) -> str:
        """Convert query to StatMuse URL slug."""
        slug = query.lower().strip()
        # Remove non-alphanumeric (keep spaces and hyphens)
        slug = re.sub(r"[^a-z0-9\s-]", "", slug)
        # Collapse whitespace to single hyphens
        slug = re.sub(r"[\s-]+", "-", slug).strip("-")
        return f"{_STATMUSE_BASE}/{category}/ask/{slug}"

    def _extract_entity(self, result: SearchResult) -> str:
        """Extract player/team name from a StatMuse answer.

        Titles always start with the entity, e.g.:
        "Declan Rice played 90 minutes..."
        "James Harden had a double-double..."
        "Erling Haaland has scored 26 goals..."
        """
        title = result.title
        # Common verbs that follow the entity name
        verbs = [
            " played", " had ", " completed", " scored", " has ",
            " created", " won ", " is ", " was ", " averaged",
            " went", " recorded", " made", " gave", " received",
            " missed", " hit ", " reached", " managed",
            " The ", " their ",
        ]
        for v in verbs:
            idx = title.find(v)
            if idx > 0:
                return title[:idx].strip()
        # Fallback: take first 2-3 words
        words = title.split()
        if len(words) >= 3:
            return " ".join(words[:2])
        return title.split()[0] if title else ""

    def _simplify_query(self, query: str) -> str:
        """Shorten a query for StatMuse retry when 422 occurs.

        Strategy: keep first N words, strip filler, cap at ~5 words.
        """
        # Remove common filler words
        filler = {"the", "a", "an", "in", "on", "at", "vs", "how", "did",
                  "what", "when", "where", "who", "is", "was", "are", "were",
                  "of", "for", "to", "and", "this", "that", "last", "game",
                  "match", "stats", "performance", "ratings", "may", "2026",
                  "2025", "2024", "season"}
        words = query.lower().split()
        # Keep first entity-like words + one intent keyword
        kept = []
        for w in words:
            if w not in filler:
                kept.append(w)
            if len(kept) >= 5:
                break
        return " ".join(kept) if kept else " ".join(words[:4])

    async def _aggregate_comprehensive(
        self,
        base: SearchResult,
        entity: str,
        category: str,
    ) -> list[SearchResult]:
        """Fire complementary queries and merge into one comprehensive result.

        Each complementary query targets a different stat dimension.
        All answers and table data are merged into the base result.
        """
        templates = _FOLLOWUP_TEMPLATES.get(category, [])
        if not templates:
            return [base]

        # Build follow-up URLs
        followup_urls = []
        for tpl in templates:
            q = tpl.format(entity=entity)
            url = self._build_url(q, category)
            followup_urls.append(url)

        # Fetch all in parallel
        followup_tasks = [self._fetch_and_parse(url) for url in followup_urls]
        followup_batches = await asyncio.gather(*followup_tasks)

        # Collect all unique answers and stat lines
        all_answers = []
        seen_answers = set()
        all_stat_lines = []
        seen_stat_headers = set()

        # Parse base result
        base_answer, base_stats = self._split_snippet(base.snippet)
        all_answers.append(base_answer)
        seen_answers.add(base_answer.strip())
        if base_stats:
            header = self._extract_stat_header(base_stats)
            if header:
                seen_stat_headers.add(header)
            all_stat_lines.append(base_stats)

        # Parse follow-up results
        for batch in followup_batches:
            for r in batch:
                answer, stats = self._split_snippet(r.snippet)
                answer_stripped = answer.strip()
                # Deduplicate answers (different queries may give same answer)
                if answer_stripped and answer_stripped not in seen_answers:
                    all_answers.append(answer_stripped)
                    seen_answers.add(answer_stripped)

                if stats:
                    header = self._extract_stat_header(stats)
                    # Only add stat line if it has new columns
                    if header and header not in seen_stat_headers:
                        all_stat_lines.append(stats)
                        seen_stat_headers.add(header)

        # Merge into one comprehensive snippet
        merged_snippet = "\n".join(all_answers)
        if all_stat_lines:
            merged_snippet += "\n\nStats:\n" + "\n".join(all_stat_lines)

        return [SearchResult(
            title=base.title,
            snippet=merged_snippet,
            url=base.url,
            publish_date=base.publish_date,
        )]

    @staticmethod
    def _split_snippet(snippet: str) -> tuple[str, str]:
        """Split snippet into (answer, stats_table) parts."""
        if "\nStats:" in snippet:
            parts = snippet.split("\nStats:", 1)
            return parts[0], parts[1].strip()
        if "\nStats: " in snippet:
            parts = snippet.split("\nStats: ", 1)
            return parts[0], parts[1].strip()
        return snippet, ""

    @staticmethod
    def _extract_stat_header(stats: str) -> str:
        """Extract the header portion (first [...] block) for dedup."""
        match = re.search(r"\[([^\]]+)\]", stats)
        if match:
            return match.group(1).strip()
        return stats[:60]

    async def _fetch_and_parse(self, url: str) -> list[SearchResult]:
        """Fetch StatMuse page and extract structured data."""
        try:
            resp = await self._client.get(url)
            if resp.status_code != 200:
                logger.info(
                    "StatMuse returned %d for %s", resp.status_code, url[:80],
                )
                return []
        except Exception as e:
            logger.warning("StatMuse fetch failed for %s: %s", url[:80], e)
            return []

        try:
            return self._parse_html(resp.text, url)
        except Exception as e:
            logger.warning("StatMuse parse failed for %s: %s", url[:80], e)
            return []

    def _parse_html(self, html: str, url: str) -> list[SearchResult]:
        """Parse StatMuse HTML page into SearchResult list."""
        soup = BeautifulSoup(html, "lxml")

        # 1. Check for error via analytics metadata
        if self._is_error_page(soup):
            logger.info("StatMuse could not understand query: %s", url[:80])
            return []

        # 2. Extract natural language answer from og:description
        description = self._get_meta_content(soup, "og:description")
        if not description:
            # Fallback: try the first h1 or h2
            heading = soup.find("h1") or soup.find("h2")
            if heading:
                description = heading.get_text(strip=True)

        if not description:
            logger.info("StatMuse no answer found for %s", url[:80])
            return []

        # 3. Extract stats table summary
        table_summary = self._extract_table_summary(soup)

        # 4. Extract publish date from table data
        publish_date = self._extract_date_from_table(soup)

        # 5. Build snippet: answer + table data
        snippet_parts = [description]
        if table_summary:
            snippet_parts.append(f"\nStats: {table_summary}")

        snippet = "\n".join(snippet_parts)

        # Title: first 120 chars of the answer
        title = description[:120] + ("..." if len(description) > 120 else "")

        return [SearchResult(
            title=title,
            snippet=snippet,
            url=url,
            publish_date=publish_date,
        )]

    def _is_error_page(self, soup: BeautifulSoup) -> bool:
        """Check if StatMuse returned an error (query not understood)."""
        # Check analytics metadata for is_error flag
        analytics_meta = soup.find(
            "meta", attrs={"name": "analytics:event:properties"},
        )
        if analytics_meta:
            try:
                import json
                content = analytics_meta.get("content", "")
                if content:
                    data = json.loads(content)
                    if data.get("is_error"):
                        return True
                    # Also check disposition
                    disposition = data.get("disposition", {})
                    if disposition.get("responseType") == "not-understood":
                        return True
            except (json.JSONDecodeError, TypeError):
                pass

        # Fallback: check for error text in title
        title_tag = soup.find("title")
        if title_tag:
            title_text = title_tag.get_text(strip=True).lower()
            if "statmuse" in title_text and "save time" in title_text:
                # Generic title means query wasn't understood
                # (real answers have the question in the title)
                og_title = self._get_meta_content(soup, "og:title")
                if not og_title or "statmuse" in og_title.lower():
                    return True

        return False

    def _get_meta_content(self, soup: BeautifulSoup, prop: str) -> str:
        """Extract content from a meta tag by property or name."""
        tag = soup.find("meta", attrs={"property": prop})
        if not tag:
            tag = soup.find("meta", attrs={"name": prop})
        if tag:
            return tag.get("content", "")
        return ""

    def _extract_table_summary(self, soup: BeautifulSoup) -> str:
        """Extract a summary of the first data table on the page.

        Strategy: extract header text + first data row text as-is.
        The LLM downstream can parse the raw table — trying to
        align columns perfectly is fragile due to StatMuse's nested
        HTML structure (row numbers, empty spacer cols, merged name cells).
        """
        table = soup.find("table")
        if not table:
            return ""

        rows = table.find_all("tr")
        if len(rows) < 2:
            return ""

        # Get header text
        header_text = rows[0].get_text(separator=" | ", strip=True)
        # Clean up: remove leading row-number and empty pipes
        header_text = re.sub(r"^\d+\s*\|", "", header_text)
        header_text = re.sub(r"\|\s*\|", "|", header_text)
        header_text = header_text.strip("| ")

        # Get first data row text
        data_text = rows[1].get_text(separator=" | ", strip=True)
        # Clean up: split name+shortname concatenations (e.g. "Declan RiceD. Rice")
        data_text = re.sub(r"([a-z])([A-Z][a-z]*\. )", r"\1, \2", data_text)
        # Remove leading row number
        data_text = re.sub(r"^\d+\s*\|", "", data_text)
        data_text = re.sub(r"\|\s*\|", "|", data_text)
        data_text = data_text.strip("| ")

        # Combine: "Headers: ... | Data: ..."
        if header_text and data_text:
            return f"[{header_text}] → [{data_text}]"
        return data_text or header_text

    def _extract_date_from_table(self, soup: BeautifulSoup) -> str:
        """Try to extract a date from the first table row."""
        table = soup.find("table")
        if not table:
            return ""

        # Look for a date-like cell in the first data row
        rows = table.find_all("tr")
        for row in rows[1:4]:  # Skip header, check first 3 data rows
            cells = row.find_all("td")
            for cell in cells:
                text = cell.get_text(strip=True)
                # Match common date formats
                if re.match(r"\d{4}-\d{2}-\d{2}", text):
                    return text
                if re.match(r"\w+ \d{1,2},? \d{4}", text):
                    return text
                if re.match(r"\d{1,2}/\d{1,2}/\d{4}", text):
                    return text

        return ""

    # --- Cache ---

    def _get_cached(self, url: str) -> list[SearchResult] | None:
        """Get cached results if still valid."""
        entry = self._cache.get(url)
        if entry is None:
            return None
        ts, results = entry
        if time.monotonic() - ts > _CACHE_TTL:
            del self._cache[url]
            return None
        return results

    def _set_cached(self, url: str, results: list[SearchResult]) -> None:
        """Cache results with current timestamp."""
        self._cache[url] = (time.monotonic(), results)
        # Evict oldest entries if cache grows too large
        if len(self._cache) > 100:
            oldest_key = min(self._cache, key=lambda k: self._cache[k][0])
            del self._cache[oldest_key]

    async def close(self) -> None:
        """Clean up HTTP client."""
        await self._client.aclose()
        self._cache.clear()
