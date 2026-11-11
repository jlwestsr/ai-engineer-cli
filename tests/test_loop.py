"""Mock provider loop smoke tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_engineer.budget import Budget
from ai_engineer.loop import AgentLoop
from ai_engineer.provider import MockProvider, ModelResponse, ToolCall
from ai_engineer.session import SessionStore
from ai_engineer.tools.registry import ToolRegistry
from ai_engineer.workspace import WorkspaceTools


def test_mock_read_agents(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# Agents\nBe careful.\n", encoding="utf-8")
    tools = WorkspaceTools(tmp_path)
    registry = ToolRegistry()
    tools.register(registry)
    session = SessionStore(tmp_path)
    try:
        loop = AgentLoop(
            MockProvider(),
            tools,
            registry,
            Budget(max_steps=5, max_tool_calls=10),
            session=session,
        )
        result = loop.run_turn("read AGENTS.md")
        assert result.status == "done"
        assert "Be careful" in result.answer or "AGENTS" in result.answer
        assert session.path.exists()
        text = session.path.read_text(encoding="utf-8")
        assert "session_start" in text
        assert "tool" in text
        assert "Be careful" not in text  # metadata-only: no source contents
    finally:
        session.close()


def test_last_step_is_answer_only(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path)
    registry = ToolRegistry()
    tools.register(registry)
    loop = AgentLoop(
        MockProvider(),
        tools,
        registry,
        Budget(max_steps=1, max_tool_calls=10),
    )
    result = loop.run_turn("read AGENTS.md")
    # With one step, tools are withheld; mock reports the tool unavailable.
    assert result.status in {"done", "blocked"}
    assert "unavailable" in result.answer.lower() or "budget" in result.answer.lower()


class GreedyProvider:
    """Always requests a tool until tools are withheld, then answers."""

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelResponse:
        names = {tool["function"]["name"] for tool in tools}
        if "list_files" in names:
            return ModelResponse(tool_calls=[ToolCall("g1", "list_files", {})])
        return ModelResponse("Final review from wrap-up.")


def test_wrap_up_after_tool_steps_exhausted(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path)
    registry = ToolRegistry()
    tools.register(registry)
    loop = AgentLoop(
        GreedyProvider(),
        tools,
        registry,
        Budget(max_steps=2, max_tool_calls=20),
        max_tools_per_turn=6,
    )
    result = loop.run_turn("review")
    assert result.status == "done"
    assert "Final review" in result.answer
