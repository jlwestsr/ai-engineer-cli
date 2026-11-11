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


class AlwaysToolsProvider:
    """Requests tools even when the schema is empty (tool budget gone)."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelResponse:
        self.calls += 1
        if tools:
            return ModelResponse(tool_calls=[ToolCall("t1", "list_files", {})])
        # Empty tools list: still "asks" for a tool (bad model); harness wraps up.
        if any(
            message.get("role") == "user"
            and "Tools are disabled" in str(message.get("content", ""))
            for message in messages
        ):
            return ModelResponse("Answer after tool-budget wrap-up.")
        return ModelResponse(tool_calls=[ToolCall("t2", "list_files", {})])


def test_wrap_up_when_tool_budget_exhausted_before_steps(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path)
    registry = ToolRegistry()
    tools.register(registry)
    provider = AlwaysToolsProvider()
    loop = AgentLoop(
        provider,
        tools,
        registry,
        Budget(max_steps=5, max_tool_calls=1),
        max_tools_per_turn=6,
    )
    result = loop.run_turn("review")
    assert result.status == "blocked"
    assert "wrap-up" in result.answer.lower() or "Answer after" in result.answer


def test_evict_old_messages_keeps_system_and_recent(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path)
    registry = ToolRegistry()
    tools.register(registry)
    loop = AgentLoop(
        MockProvider(),
        tools,
        registry,
        Budget(max_steps=3, max_tool_calls=5),
    )
    # Inflate history past the soft eviction target.
    filler = "x" * 8_000
    for index in range(40):
        loop.messages.append({"role": "user", "content": f"u{index}:{filler}"})
        loop.messages.append({"role": "assistant", "content": f"a{index}:{filler}"})
    before = len(loop.messages)
    loop._evict_old_messages()
    assert loop.messages[0]["role"] == "system"
    assert len(loop.messages) < before
    assert len([message for message in loop.messages if message.get("role") != "system"]) <= 24


def test_evict_keeps_tool_call_pairing(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path)
    registry = ToolRegistry()
    tools.register(registry)
    loop = AgentLoop(
        MockProvider(),
        tools,
        registry,
        Budget(max_steps=3, max_tool_calls=5),
    )
    filler = "y" * 10_000
    for index in range(30):
        loop.messages.append({"role": "user", "content": f"u{index}:{filler}"})
        loop.messages.append({"role": "assistant", "content": f"a{index}:{filler}"})
    loop.messages.append(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-keep",
                    "type": "function",
                    "function": {"name": "list_files", "arguments": "{}"},
                }
            ],
        }
    )
    loop.messages.append(
        {"role": "tool", "tool_call_id": "call-keep", "content": '{"files":[]}'}
    )
    loop._evict_old_messages()
    roles = [message.get("role") for message in loop.messages]
    if "tool" in roles:
        tool_index = roles.index("tool")
        assert loop.messages[tool_index - 1].get("role") == "assistant"
        assert loop.messages[tool_index - 1].get("tool_calls")
        assert loop.messages[tool_index]["tool_call_id"] == "call-keep"


def test_session_store_collision_mints_new_id(tmp_path: Path) -> None:
    first = SessionStore(tmp_path, session_id="fixed")
    first.close()
    second = SessionStore(tmp_path, session_id="fixed")
    try:
        assert second.session_id != "fixed"
        assert second.path.exists()
        assert first.path.exists()
        assert first.path != second.path
    finally:
        second.close()


def test_provider_exception_backfills_without_polluting_history(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path)
    registry = ToolRegistry()
    tools.register(registry)

    class BoomProvider:
        def complete(
            self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
        ) -> ModelResponse:
            if tools:
                return ModelResponse(tool_calls=[ToolCall("boom", "list_files", {})])
            raise ConnectionError("upstream down")

    def explode(name: str, arguments: dict[str, Any]) -> Any:
        raise ConnectionError("tool transport failed")

    registry.execute = explode  # type: ignore[method-assign]
    loop = AgentLoop(
        BoomProvider(),
        tools,
        registry,
        Budget(max_steps=4, max_tool_calls=10),
    )
    result = loop.run_turn("go")
    assert result.status == "failed"
    assert any(
        message.get("role") == "tool" and message.get("tool_call_id") == "boom"
        for message in loop.messages
    )
    assert loop.messages[-1].get("role") != "assistant" or "upstream" not in str(
        loop.messages[-1].get("content", "")
    )
    loop._assert_transcript_invariants()


def test_interrupt_backfills_pending_tool_results(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path)
    registry = ToolRegistry()
    tools.register(registry)

    class InterruptAfterToolsListed:
        def complete(
            self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
        ) -> ModelResponse:
            if tools:
                return ModelResponse(
                    tool_calls=[
                        ToolCall("c1", "list_files", {}),
                        ToolCall("c2", "list_files", {}),
                    ]
                )
            return ModelResponse("should not reach")

    real_execute = registry.execute

    def execute_and_interrupt(name: str, arguments: dict[str, Any]) -> Any:
        raise KeyboardInterrupt

    registry.execute = execute_and_interrupt  # type: ignore[method-assign]
    loop = AgentLoop(
        InterruptAfterToolsListed(),
        tools,
        registry,
        Budget(max_steps=4, max_tool_calls=10),
    )
    result = loop.run_turn("go")
    assert result.status == "blocked"
    tool_ids = [
        message.get("tool_call_id")
        for message in loop.messages
        if message.get("role") == "tool"
    ]
    assert "c1" in tool_ids
    assert "c2" in tool_ids
    registry.execute = real_execute  # type: ignore[method-assign]
