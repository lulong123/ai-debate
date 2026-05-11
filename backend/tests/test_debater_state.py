"""Tests for DebaterState dataclass and strategy handling."""

from pydantic import BaseModel

from app.agents.base import DebaterState


class _FakeThinking(BaseModel):
    """Minimal model mimicking AgentThinking fields used by DebaterState."""

    thinking: str = ""
    my_arguments_standing: list[str] = []
    my_arguments_refuted: list[str] = []
    opponent_weaknesses: list[str] = []
    chosen_strategy: str = ""


def test_initial_state():
    state = DebaterState()
    assert state.arguments_standing == []
    assert state.arguments_refuted == []
    assert state.opponent_weaknesses == []
    assert state.strategies_used == []


def test_initial_prompt_text():
    state = DebaterState()
    assert state.to_prompt_text() == "（第一轮，暂无历史状态）"


def test_update_adds_standing():
    state = DebaterState()
    thinking = _FakeThinking(
        thinking="分析...",
        my_arguments_standing=["论点A"],
        chosen_strategy="ATTACK",
    )
    state.update(thinking)
    assert state.arguments_standing == ["论点A"]
    assert state.strategies_used == ["ATTACK"]


def test_update_merges_and_truncates():
    state = DebaterState(
        arguments_standing=["旧1", "旧2", "旧3", "旧4", "旧5"],
    )
    thinking = _FakeThinking(my_arguments_standing=["新1", "新2"])
    state.update(thinking)
    # Should keep last 3 old + 2 new = 5 total
    assert state.arguments_standing == ["旧3", "旧4", "旧5", "新1", "新2"]


def test_update_truncates_at_max():
    state = DebaterState()
    thinking = _FakeThinking(
        my_arguments_standing=[f"论点{i}" for i in range(7)],
    )
    state.update(thinking)
    assert len(state.arguments_standing) == 5
    assert state.arguments_standing[-1] == "论点6"


def test_update_refuted_and_weaknesses():
    state = DebaterState()
    thinking = _FakeThinking(
        my_arguments_refuted=["被反驳1"],
        opponent_weaknesses=["漏洞A", "漏洞B"],
    )
    state.update(thinking)
    assert state.arguments_refuted == ["被反驳1"]
    assert state.opponent_weaknesses == ["漏洞A", "漏洞B"]


def test_strategy_history_appends():
    state = DebaterState()
    state.update(_FakeThinking(chosen_strategy="ATTACK"))
    state.update(_FakeThinking(chosen_strategy="DEFEND"))
    state.update(_FakeThinking(chosen_strategy="ATTACK"))
    assert state.strategies_used == ["ATTACK", "DEFEND", "ATTACK"]


def test_strategy_history_truncates():
    state = DebaterState()
    for s in ["ATTACK", "DEFEND", "REDIRECT", "EVIDENCE", "ATTACK", "DEFEND"]:
        state.update(_FakeThinking(chosen_strategy=s))
    assert len(state.strategies_used) == 5
    assert state.strategies_used == ["DEFEND", "REDIRECT", "EVIDENCE", "ATTACK", "DEFEND"]


def test_empty_strategy_not_appended():
    state = DebaterState()
    state.update(_FakeThinking(chosen_strategy=""))
    assert state.strategies_used == []


def test_prompt_text_format():
    state = DebaterState(
        arguments_standing=["哈登三分更强"],
        arguments_refuted=["哈登更全面"],
        opponent_weaknesses=["对手数据过时"],
        strategies_used=["ATTACK", "DEFEND"],
    )
    text = state.to_prompt_text()
    assert "✅" in text
    assert "❌" in text
    assert "🔍" in text
    assert "📊" in text
    assert "ATTACK → DEFEND" in text


def test_consecutive_strategy_count():
    state = DebaterState()
    assert state.consecutive_strategy_count() == 0
    state.update(_FakeThinking(chosen_strategy="ATTACK"))
    assert state.consecutive_strategy_count() == 1
    state.update(_FakeThinking(chosen_strategy="ATTACK"))
    assert state.consecutive_strategy_count() == 2
    state.update(_FakeThinking(chosen_strategy="DEFEND"))
    assert state.consecutive_strategy_count() == 1


def test_update_with_none_fields():
    """Ensure update handles empty/missing fields gracefully."""
    state = DebaterState()
    thinking = _FakeThinking(thinking="...", chosen_strategy="ATTACK")
    state.update(thinking)
    assert state.arguments_standing == []
    assert state.strategies_used == ["ATTACK"]


def test_multiple_updates_accumulate():
    state = DebaterState()
    state.update(_FakeThinking(
        my_arguments_standing=["论点1"],
        opponent_weaknesses=["漏洞A"],
        chosen_strategy="ATTACK",
    ))
    state.update(_FakeThinking(
        my_arguments_standing=["论点2"],
        my_arguments_refuted=["被反驳1"],
        opponent_weaknesses=["漏洞B"],
        chosen_strategy="DEFEND",
    ))
    assert state.arguments_standing == ["论点1", "论点2"]
    assert state.arguments_refuted == ["被反驳1"]
    assert state.opponent_weaknesses == ["漏洞A", "漏洞B"]
    assert state.strategies_used == ["ATTACK", "DEFEND"]
