"""Tool registry validation tests."""

from __future__ import annotations

from ai_engineer.tools.registry import ToolRegistry
from ai_engineer.workspace import object_schema


def test_unknown_tool_returns_error() -> None:
    registry = ToolRegistry()
    assert registry.execute("missing", {})["error"].startswith("Unknown tool")


def test_required_field_validation() -> None:
    registry = ToolRegistry()
    registry.register(
        "echo",
        "Echo text",
        object_schema({"text": {"type": "string"}}, ("text",)),
        lambda arguments: arguments["text"],
    )
    assert "missing required" in registry.execute("echo", {})["error"]
    assert registry.execute("echo", {"text": "hi"}) == "hi"
