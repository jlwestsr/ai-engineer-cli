"""Conversation compaction under request budget pressure."""

from __future__ import annotations

from pathlib import Path

from ai_engineer.budget import Budget
from ai_engineer.loop import MAX_REQUEST_CHARS, AgentLoop
from ai_engineer.provider import MockProvider
from ai_engineer.tools.registry import ToolRegistry
from ai_engineer.workspace import WorkspaceTools


def test_compact_shrinks_old_tool_messages(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path)
    registry = ToolRegistry()
    tools.register(registry)
    loop = AgentLoop(
        MockProvider(),
        tools,
        registry,
        Budget(max_steps=3, max_tool_calls=10),
    )
    huge = "x" * 50_000
    loop.messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "review"},
        {"role": "tool", "tool_call_id": "t1", "content": huge},
        {"role": "tool", "tool_call_id": "t2", "content": huge},
        {"role": "tool", "tool_call_id": "t3", "content": huge},
    ]
    loop._compact_messages()
    assert loop._message_chars() <= MAX_REQUEST_CHARS
    assert all(
        len(message["content"]) < 50_000
        for message in loop.messages
        if message.get("role") == "tool"
    )
    assert any("compacted" in message["content"] for message in loop.messages)
