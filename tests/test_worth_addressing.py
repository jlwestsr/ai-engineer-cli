"""Coverage for review 'worth addressing' items."""

from __future__ import annotations

from pathlib import Path

from ai_engineer.budget import Budget
from ai_engineer.instructions import load_skill_snippets
from ai_engineer.loop import AgentLoop
from ai_engineer.provider import MockProvider
from ai_engineer.tools.registry import ToolRegistry
from ai_engineer.workspace import WorkspaceTools


def test_list_files_reports_limit_reached(tmp_path: Path) -> None:
    for index in range(5):
        (tmp_path / f"f{index}.txt").write_text("x\n", encoding="utf-8")
    tools = WorkspaceTools(tmp_path)
    result = tools.list_files({"limit": 2})
    assert result["limit_reached"] is True
    assert result["scan_truncated"] is False
    assert len(result["files"]) == 2


def test_search_text_reports_distinct_flags(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("needle here\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("needle again\n", encoding="utf-8")
    tools = WorkspaceTools(tmp_path)
    result = tools.search_text({"text": "needle", "limit": 1})
    assert result["limit_reached"] is True
    assert result["timed_out"] is False
    assert "time_limit_seconds" in result


def test_skill_symlink_is_ignored(tmp_path: Path) -> None:
    skills = tmp_path / ".aie" / "skills" / "demo"
    skills.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("steal me\n", encoding="utf-8")
    (skills / "SKILL.md").symlink_to(outside)
    assert load_skill_snippets(tmp_path) == ""


def test_skill_file_loads_when_safe(tmp_path: Path) -> None:
    skills = tmp_path / ".aie" / "skills" / "demo"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("Use careful edits.\n", encoding="utf-8")
    text = load_skill_snippets(tmp_path)
    assert "Use careful edits." in text
    assert "untrusted text" in text


def test_clear_history_resets_messages(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path)
    registry = ToolRegistry()
    tools.register(registry)
    loop = AgentLoop(MockProvider(), tools, registry, Budget(max_steps=3, max_tool_calls=5))
    loop.run_turn("list files")
    assert len(loop.messages) > 1
    loop.clear_history()
    assert len(loop.messages) == 1
    assert loop.messages[0]["role"] == "system"


def test_mock_handles_compacted_tool_payload() -> None:
    provider = MockProvider()
    response = provider.complete(
        [
            {"role": "user", "content": "list files"},
            {
                "role": "tool",
                "tool_call_id": "mock-engineering",
                "content": '{"compacted": true, "preview": "x"}',
            },
        ],
        [],
    )
    assert "compacted" in response.content or "result" in response.content.lower()


def test_mock_handles_missing_user_message() -> None:
    provider = MockProvider()
    response = provider.complete([{"role": "system", "content": "sys"}], [])
    assert "user message" in response.content.lower()
