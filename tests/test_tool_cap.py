"""Per-turn tool cap and soft deferral."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_engineer.budget import Budget
from ai_engineer.loop import AgentLoop
from ai_engineer.provider import ModelResponse, ToolCall
from ai_engineer.tools.registry import ToolRegistry
from ai_engineer.workspace import WorkspaceTools


class BatchyProvider:
    """Request many tools once, then answer after tool results."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelResponse:
        self.calls += 1
        if messages[-1]["role"] == "tool" or any(m.get("role") == "tool" for m in messages[-5:]):
            # After any tool round, finish.
            if self.calls >= 2:
                return ModelResponse("Reviewed with capped tools.")
        return ModelResponse(
            tool_calls=[
                ToolCall(f"id-{i}", "list_files", {})
                for i in range(10)
            ]
        )


def test_defers_extra_tools_without_blocking(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path)
    registry = ToolRegistry()
    tools.register(registry)
    loop = AgentLoop(
        BatchyProvider(),
        tools,
        registry,
        Budget(max_steps=5, max_tool_calls=20),
        max_tools_per_turn=3,
    )
    result = loop.run_turn("review")
    assert result.status == "done"
    assert "Reviewed" in result.answer
    # Only 3 tools should have been consumed from the first oversized batch.
    assert result.budget["tool_calls_used"] == 3
