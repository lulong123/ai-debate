"""Tests for StatMuseProvider."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.statmuse import StatMuseProvider
from app.services.search import SearchResult


# --- _classify tests ---


def test_classify_nba():
    p = StatMuseProvider()
    assert p._classify("James Harden last game stats") == "nba"
    assert p._classify("nba playoffs scores") == "nba"
    assert p._classify("lebron points per game") == "nba"


def test_classify_fc():
    p = StatMuseProvider()
    assert p._classify("Declan Rice last game") == "fc"
    assert p._classify("arsenal premier league standings") == "fc"
    assert p._classify("Messi goals this season") == "fc"


def test_classify_nfl():
    p = StatMuseProvider()
    assert p._classify("nfl touchdown leaders") == "nfl"
    assert p._classify("super bowl quarterback stats") == "nfl"


def test_classify_mlb():
    p = StatMuseProvider()
    assert p._classify("mlb home run leaders") == "mlb"
    assert p._classify("baseball pitcher strikeout") == "mlb"


def test_classify_money():
    p = StatMuseProvider()
    assert p._classify("apple stock price") == "money"
    assert p._classify("nvidia market cap") == "money"
    assert p._classify("tsla earnings") == "money"


def test_classify_no_match():
    p = StatMuseProvider()
    assert p._classify("拜登最新政策") is None
    assert p._classify("今天天气怎么样") is None
    assert p._classify("how to cook pasta") is None


def test_classify_chinese_returns_none():
    """Chinese queries should not match (StatMuse is English-only)."""
    p = StatMuseProvider()
    assert p._classify("哈登上一场比赛数据") is None
    assert p._classify("梅西进了几个球") is None


# --- _build_url tests ---


def test_build_url():
    p = StatMuseProvider()
    url = p._build_url("James Harden last game stats", "nba")
    assert url == "https://www.statmuse.com/nba/ask/james-harden-last-game-stats"


def test_build_url_special_chars():
    p = StatMuseProvider()
    url = p._build_url("Who's the MVP? (2024-25)", "nba")
    assert "whos-the-mvp-2024-25" in url
    assert "?" not in url
    assert "(" not in url


def test_build_url_extra_spaces():
    p = StatMuseProvider()
    url = p._build_url("  lebron   points   per  game  ", "nba")
    assert url == "https://www.statmuse.com/nba/ask/lebron-points-per-game"


# --- _parse_html tests ---


def test_parse_html_success():
    p = StatMuseProvider()
    html = """
    <html>
    <head>
        <meta property="og:description"
              content="James Harden had 24 points and 11 assists in Game 4." />
        <meta name="analytics:event:properties" content='{"is_error": false}' />
    </head>
    <body>
        <table>
            <tr><th>NAME</th><th>PTS</th><th>AST</th></tr>
            <tr><td>James Harden</td><td>24</td><td>11</td></tr>
        </table>
    </body>
    </html>
    """
    results = p._parse_html(html, "https://www.statmuse.com/nba/ask/james-harden-last-game")
    assert len(results) == 1
    assert "24 points" in results[0].title
    assert "24 points" in results[0].snippet
    assert results[0].url == "https://www.statmuse.com/nba/ask/james-harden-last-game"


def test_parse_html_error_page():
    p = StatMuseProvider()
    html = """
    <html>
    <head>
        <title>StatMuse | Search StatMuse, save time.</title>
        <meta property="og:title" content="StatMuse" />
        <meta name="analytics:event:properties" content='{"is_error": true}' />
    </head>
    <body><p>I didn't understand your question.</p></body>
    </html>
    """
    results = p._parse_html(html, "https://www.statmuse.com/nba/ask/xyz")
    assert results == []


def test_parse_html_not_understood():
    p = StatMuseProvider()
    html = """
    <html>
    <head>
        <title>StatMuse | Search StatMuse, save time.</title>
        <meta property="og:title" content="StatMuse" />
        <meta name="analytics:event:properties"
              content='{"disposition": {"responseType": "not-understood"}}' />
    </head>
    <body></body>
    </html>
    """
    results = p._parse_html(html, "https://www.statmuse.com/nba/ask/xyz")
    assert results == []


def test_parse_html_no_description():
    p = StatMuseProvider()
    html = "<html><head></head><body><p>No useful data</p></body></html>"
    results = p._parse_html(html, "https://www.statmuse.com/nba/ask/xyz")
    assert results == []


def test_parse_html_with_date():
    p = StatMuseProvider()
    html = """
    <html>
    <head>
        <meta property="og:description" content="Harden scored 24 points." />
    </head>
    <body>
        <table>
            <tr><th>NAME</th><th>DATE</th><th>PTS</th></tr>
            <tr><td>Harden</td><td>2026-05-12</td><td>24</td></tr>
        </table>
    </body>
    </html>
    """
    results = p._parse_html(html, "https://www.statmuse.com/nba/ask/harden-last-game")
    assert len(results) == 1
    assert results[0].publish_date == "2026-05-12"


def test_parse_html_with_table_summary():
    p = StatMuseProvider()
    html = """
    <html>
    <head>
        <meta property="og:description" content="Harden scored 24 points." />
    </head>
    <body>
        <table>
            <tr><th>NAME</th><th>PTS</th><th>REB</th><th>AST</th></tr>
            <tr><td>Harden</td><td>24</td><td>8</td><td>11</td></tr>
            <tr><td>SGA</td><td>33</td><td>5</td><td>7</td></tr>
        </table>
    </body>
    </html>
    """
    results = p._parse_html(html, "https://www.statmuse.com/nba/ask/harden-last-game")
    assert len(results) == 1
    assert "Stats:" in results[0].snippet
    assert "Harden" in results[0].snippet
    assert "24" in results[0].snippet


# --- search integration test ---


async def test_search_non_sports_returns_empty():
    """Non-sports queries should return [] immediately (no HTTP call)."""
    p = StatMuseProvider()
    results = await p.search("拜登最新政策")
    assert results == []


async def test_search_nba_query():
    """NBA query should attempt HTTP fetch."""
    p = StatMuseProvider()
    html = """
    <html><head>
        <meta property="og:description" content="Harden had 24 points." />
    </head><body><table><tr><th>PTS</th></tr><tr><td>24</td></tr></table></body></html>
    """
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = html

    with patch.object(p, "_client") as mock_client:
        mock_client.get = AsyncMock(return_value=mock_resp)
        results = await p.search("James Harden last game")
        assert len(results) == 1
        assert "24 points" in results[0].title


async def test_search_http_error():
    """HTTP errors should return [] gracefully."""
    p = StatMuseProvider()
    mock_resp = MagicMock()
    mock_resp.status_code = 500

    with patch.object(p, "_client") as mock_client:
        mock_client.get = AsyncMock(return_value=mock_resp)
        results = await p.search("James Harden last game")
        assert results == []


async def test_search_network_failure():
    """Network failures should return [] gracefully."""
    p = StatMuseProvider()

    with patch.object(p, "_client") as mock_client:
        mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))
        results = await p.search("James Harden last game")
        assert results == []


# --- Cache tests ---


def test_cache_set_and_get():
    p = StatMuseProvider()
    url = "https://www.statmuse.com/nba/ask/harden-last-game"
    results = [SearchResult("Test", "snippet", url)]
    p._set_cached(url, results)
    cached = p._get_cached(url)
    assert cached == results


def test_cache_miss():
    p = StatMuseProvider()
    assert p._get_cached("https://nonexistent") is None


def test_cache_eviction():
    """Cache should evict oldest entries when > 100."""
    p = StatMuseProvider()
    for i in range(110):
        p._set_cached(f"https://url-{i}", [SearchResult(f"Item {i}", "", "")])
    # First entry should be evicted
    assert p._get_cached("https://url-0") is None
    # Later entries should still exist
    assert p._get_cached("https://url-109") is not None


# --- _statmuse_query in data_clerk ---


async def test_data_clerk_statmuse_query():
    """_statmuse_query should call provider and tag results."""
    from app.agents.data_clerk import DataClerkAgent

    clerk = DataClerkAgent()
    mock_provider = MagicMock()
    mock_provider.search = AsyncMock(return_value=[
        SearchResult("Harden 24pts", "24 points, 11 assists", "https://statmuse.com/..."),
    ])

    results = await clerk._statmuse_query("James Harden last game", mock_provider)
    assert len(results) == 1
    assert results[0]["source"] == "statmuse"
    assert "24 points" in results[0]["snippet"]


async def test_data_clerk_statmuse_query_none_provider():
    """_statmuse_query with None provider should return []."""
    from app.agents.data_clerk import DataClerkAgent

    clerk = DataClerkAgent()
    results = await clerk._statmuse_query("query", None)
    assert results == []


async def test_data_clerk_statmuse_query_failure():
    """_statmuse_query should return [] on provider failure."""
    from app.agents.data_clerk import DataClerkAgent

    clerk = DataClerkAgent()
    mock_provider = MagicMock()
    mock_provider.search = AsyncMock(side_effect=Exception("Network error"))

    results = await clerk._statmuse_query("James Harden last game", mock_provider)
    assert results == []


# --- close ---


async def test_close():
    p = StatMuseProvider()
    with patch.object(p, "_client") as mock_client:
        mock_client.aclose = AsyncMock()
        await p.close()
        mock_client.aclose.assert_called_once()


# --- StatMuse trust: screen_results bypass ---


async def test_screen_results_auto_keeps_statmuse():
    """StatMuse-tagged results should bypass LLM screening."""
    from app.agents.data_clerk import DataClerkAgent

    clerk = DataClerkAgent()
    statmuse_result = {
        "title": "Harden 24pts", "snippet": "24 points, 11 assists",
        "url": "https://statmuse.com/...", "source": "statmuse",
    }
    normal_result = {
        "title": "Some random article", "snippet": "Unrelated content",
        "url": "https://example.com/...",
    }
    # Mock LLM to reject everything — StatMuse should still survive
    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_rt:
        mock_rt.return_value = MagicMock(kept=[], rejected=["irrelevant"])
        results = await clerk.screen_results(
            [statmuse_result, normal_result], topic="Harden last game",
        )
    assert any(r.get("source") == "statmuse" for r in results)
    assert statmuse_result in results


async def test_screen_results_statmuse_only():
    """When ALL results are StatMuse, no LLM call needed."""
    from app.agents.data_clerk import DataClerkAgent

    clerk = DataClerkAgent()
    sm1 = {"title": "A", "snippet": "s1", "url": "u1", "source": "statmuse"}
    sm2 = {"title": "B", "snippet": "s2", "url": "u2", "source": "statmuse"}

    with patch.object(clerk, "respond_typed", new_callable=AsyncMock) as mock_rt:
        results = await clerk.screen_results(
            [sm1, sm2], topic="Harden last game",
        )
        # LLM should NOT be called
        mock_rt.assert_not_called()
    assert len(results) == 2


# --- StatMuse trust: _map_validated_to_results ---


def test_map_validated_statmuse_always_public():
    """StatMuse results should be public even without cross-validation."""
    from app.agents.data_clerk import DataClerkAgent
    from app.models.schemas import CrossValidatedFacts

    sm = {"title": "SM", "key_facts": '{"key_facts": ["fact1"]}', "source": "statmuse"}
    other = {"title": "Other", "key_facts": '{"key_facts": ["fact2"]}'}

    # No validated facts at all
    validation = CrossValidatedFacts()
    public, private = DataClerkAgent._map_validated_to_results([sm, other], validation)
    assert sm in public
    assert other in private


def test_map_validated_statmuse_with_validation():
    """When validated facts exist, ALL results go public."""
    from app.agents.data_clerk import DataClerkAgent
    from app.models.schemas import CrossValidatedFacts

    sm = {"title": "SM", "key_facts": '{"key_facts": ["statmuse fact"]}', "source": "statmuse"}
    other_validated = {"title": "Other", "key_facts": '{"key_facts": ["validated fact"]}'}
    other_unvalidated = {"title": "Other2", "key_facts": '{"key_facts": ["unknown fact"]}'}
    other_empty = {"title": "Other3", "key_facts": ""}

    validation = CrossValidatedFacts(validated=[{"fact": "validated fact", "source_count": 2}])
    public, private = DataClerkAgent._map_validated_to_results(
        [sm, other_validated, other_unvalidated, other_empty], validation,
    )
    # ALL go public when validation has validated facts
    assert len(public) == 4
    assert len(private) == 0
