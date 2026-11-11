"""Workspace path sandbox and edit SHA checks."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from ai_engineer.tools.registry import ToolRegistry
from ai_engineer.workspace import WorkspaceTools, scrub_subprocess_env


def test_path_rejects_traversal(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path)
    with pytest.raises(ValueError, match="workspace"):
        tools._path("../outside")


def test_path_rejects_env_files(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")
    tools = WorkspaceTools(tmp_path)
    with pytest.raises(ValueError, match="excluded"):
        tools._path(".env")


def test_read_and_edit_with_sha(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("hello world\n", encoding="utf-8")
    tools = WorkspaceTools(tmp_path, allow_edits=True)
    read = tools.read_file({"path": "note.txt"})
    assert "1: hello world" in read["content"]
    edited = tools.edit_file(
        {
            "path": "note.txt",
            "expected_sha256": read["sha256"],
            "old_text": "hello world",
            "new_text": "hello aie",
        }
    )
    assert target.read_text(encoding="utf-8") == "hello aie\n"
    assert edited["sha256"] == hashlib.sha256(b"hello aie\n").hexdigest()


def test_edit_requires_permission(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("hello\n", encoding="utf-8")
    tools = WorkspaceTools(tmp_path, allow_edits=False)
    read = tools.read_file({"path": "note.txt"})
    with pytest.raises(PermissionError):
        tools.edit_file(
            {
                "path": "note.txt",
                "expected_sha256": read["sha256"],
                "old_text": "hello",
                "new_text": "hi",
            }
        )


def test_gated_tools_refresh(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path, allow_edits=False, allow_tests=False)
    registry = ToolRegistry()
    tools.register(registry)
    assert "edit_file" not in registry.names
    tools.set_permissions(allow_edits=True, allow_tests=True)
    tools.refresh_registry(registry)
    assert "edit_file" in registry.names
    assert "run_python_tests" in registry.names


def test_scrub_subprocess_env_drops_openrouter_key() -> None:
    cleaned = scrub_subprocess_env(
        {
            "PATH": "/usr/bin",
            "OPENROUTER_API_KEY": "sk-secret",
            "AIE_API_KEY": "sk-aie",
            "GITHUB_TOKEN": "ghp_x",
            "SAFE_FLAG": "1",
        }
    )
    assert "OPENROUTER_API_KEY" not in cleaned
    assert "AIE_API_KEY" not in cleaned
    assert "GITHUB_TOKEN" not in cleaned
    assert cleaned["PATH"] == "/usr/bin"
    assert cleaned["SAFE_FLAG"] == "1"
    assert cleaned["GIT_TERMINAL_PROMPT"] == "0"


def test_run_python_tests_invokes_pytest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tools = WorkspaceTools(tmp_path, allow_tests=True)
    captured: dict[str, Any] = {}

    def fake_command(command: list[str]) -> dict[str, Any]:
        captured["command"] = command
        return {"exit_code": 0, "output": "ok", "timed_out": False, "truncated": False}

    monkeypatch.setattr(tools, "_command", fake_command)
    result = tools.run_python_tests({})
    assert captured["command"][:4] == ["python", "-B", "-m", "pytest"]
    assert "-q" in captured["command"]
    assert result["exit_code"] == 0
