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
            "PYTHONPATH": "/evil",
            "LD_PRELOAD": "/evil.so",
            "GIT_SSH_COMMAND": "ssh -i /evil",
            "AWS_ACCESS_KEY_ID": "AKIAxxx",
            "NODE_OPTIONS": "--require /evil",
        }
    )
    assert "OPENROUTER_API_KEY" not in cleaned
    assert "AIE_API_KEY" not in cleaned
    assert "GITHUB_TOKEN" not in cleaned
    assert "PYTHONPATH" not in cleaned
    assert "LD_PRELOAD" not in cleaned
    assert "GIT_SSH_COMMAND" not in cleaned
    assert "AWS_ACCESS_KEY_ID" not in cleaned
    assert "NODE_OPTIONS" not in cleaned
    assert cleaned["PATH"] == "/usr/bin"
    assert cleaned["SAFE_FLAG"] == "1"
    assert cleaned["GIT_TERMINAL_PROMPT"] == "0"


def test_run_python_tests_invokes_pytest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    tools = WorkspaceTools(tmp_path, allow_tests=True)
    captured: dict[str, Any] = {}

    def fake_command(command: list[str]) -> dict[str, Any]:
        captured["command"] = command
        return {"exit_code": 0, "output": "ok", "timed_out": False, "truncated": False}

    monkeypatch.setattr(tools, "_command", fake_command)
    result = tools.run_python_tests({})
    assert captured["command"][:4] == [sys.executable, "-B", "-m", "pytest"]
    assert "-q" in captured["command"]
    assert result["exit_code"] == 0


def test_read_file_marks_char_truncation(tmp_path: Path) -> None:
    from ai_engineer.workspace import MAX_OUTPUT_CHARS

    line = "x" * 200
    (tmp_path / "wide.txt").write_text("\n".join([line] * 120) + "\n", encoding="utf-8")
    tools = WorkspaceTools(tmp_path)
    result = tools.read_file({"path": "wide.txt", "start_line": 1, "end_line": 120})
    assert result["truncated"] is True
    assert len(result["content"]) <= MAX_OUTPUT_CHARS


def test_env_example_is_readable(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text("AIE_MODEL=demo\n", encoding="utf-8")
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=secret\n", encoding="utf-8")
    tools = WorkspaceTools(tmp_path)
    listed = tools.list_files({})
    assert ".env.example" in listed["files"]
    assert ".env" not in listed["files"]
    read = tools.read_file({"path": ".env.example"})
    assert "AIE_MODEL=demo" in read["content"]


def test_git_status_filters_secret_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tools = WorkspaceTools(tmp_path)

    def fake_git(arguments: list[str]) -> dict[str, Any]:
        if arguments[:1] == ["rev-parse"] or "rev-parse" in arguments:
            return {
                "exit_code": 0,
                "timed_out": False,
                "truncated": False,
                "output": str(tmp_path),
            }
        assert "--porcelain" in arguments
        assert "-z" in arguments
        payload = (
            "?? .env\x00?? notes.txt\x00 M src/ok.py\x00?? id_rsa\x00?? .aie/sessions/abc.jsonl\x00"
        )
        return {
            "exit_code": 0,
            "timed_out": False,
            "truncated": False,
            "output": payload,
        }

    monkeypatch.setattr(tools, "_git_command", fake_git)
    result = tools.git_status({})
    assert ".env" not in result["output"]
    assert "id_rsa" not in result["output"]
    assert ".aie/" not in result["output"]
    assert "notes.txt" in result["output"]
    assert "src/ok.py" in result["output"]
    assert result["filtered"] is True


def test_git_status_maps_nested_workspace_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path
    nested = repo / "pkg" / "app"
    nested.mkdir(parents=True)
    (nested / "notes.txt").write_text("ok\n", encoding="utf-8")
    tools = WorkspaceTools(nested)

    def fake_git(arguments: list[str]) -> dict[str, Any]:
        if "rev-parse" in arguments:
            return {
                "exit_code": 0,
                "timed_out": False,
                "truncated": False,
                "output": str(repo),
            }
        payload = "?? pkg/app/notes.txt\x00?? pkg/app/.env\x00?? other/out.txt\x00"
        return {
            "exit_code": 0,
            "timed_out": False,
            "truncated": False,
            "output": payload,
        }

    monkeypatch.setattr(tools, "_git_command", fake_git)
    result = tools.git_status({})
    assert "notes.txt" in result["output"]
    assert ".env" not in result["output"]
    assert "other/out.txt" not in result["output"]
    assert "pkg/app/" not in result["output"]


def test_text_rejects_symlink_target(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    linked = tmp_path / "linked.txt"
    linked.symlink_to(outside)
    tools = WorkspaceTools(tmp_path)
    with pytest.raises(ValueError):
        tools._text("linked.txt")


def test_deny_unregisters_gated_tools(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path, allow_edits=True, allow_tests=True)
    registry = ToolRegistry()
    tools.register(registry)
    assert "edit_file" in registry.names
    assert "run_python_tests" in registry.names
    tools.set_permissions(allow_edits=False, allow_tests=False)
    tools.refresh_registry(registry)
    assert "edit_file" not in registry.names
    assert "create_file" not in registry.names
    assert "run_python_tests" not in registry.names
