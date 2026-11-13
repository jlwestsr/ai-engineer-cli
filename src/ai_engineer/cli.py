"""Typer entrypoint for one-shot and interactive REPL usage."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from ai_engineer import __version__
from ai_engineer.budget import Budget
from ai_engineer.envfile import load_env_file
from ai_engineer.loop import AgentLoop
from ai_engineer.provider import build_provider
from ai_engineer.session import SessionStore
from ai_engineer.tools.registry import ToolRegistry
from ai_engineer.workspace import WorkspaceTools

app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    invoke_without_command=True,
    help="AI Engineer CLI — interactive REPL with guarded workspace tools.",
)
console = Console(stderr=True)
out = Console()


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from error
    if value < 1:
        raise ValueError(f"{name} must be at least 1, got {value}")
    return value


def build_runtime(
    workspace: Path,
    *,
    provider_name: str,
    allow_edits: bool,
    allow_tests: bool,
    max_steps: int,
    max_tool_calls: int,
    max_tools_per_turn: int,
    command_timeout: int,
    session: bool,
) -> tuple[AgentLoop, WorkspaceTools, SessionStore | None]:
    tools = WorkspaceTools(
        workspace,
        allow_edits=allow_edits,
        allow_tests=allow_tests,
        command_timeout=command_timeout,
    )
    registry = ToolRegistry()
    tools.register(registry)
    budget = Budget(max_steps=max_steps, max_tool_calls=max_tool_calls)
    store = SessionStore(workspace) if session else None
    provider = build_provider(provider_name)
    loop = AgentLoop(
        provider,
        tools,
        registry,
        budget,
        session=store,
        progress=lambda msg: console.print(f"[dim]{msg}[/dim]"),
        max_tools_per_turn=max_tools_per_turn,
    )
    return loop, tools, store


@app.callback(invoke_without_command=True)
def main(
    prompt: Optional[str] = typer.Argument(
        None,
        help="One-shot prompt. Omit to enter the interactive REPL.",
    ),
    workspace: Path = typer.Option(
        Path.cwd(),
        "--workspace",
        "-w",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Workspace root for tools and AGENTS.md.",
    ),
    provider: str = typer.Option(
        "mock",
        "--provider",
        "-p",
        help="Provider: mock | openai | openrouter",
    ),
    allow_edits: bool = typer.Option(False, "--allow-edits", help="Permit edit_file/create_file."),
    allow_tests: bool = typer.Option(False, "--allow-tests", help="Permit run_python_tests."),
    max_steps: int = typer.Option(
        None,
        "--max-steps",
        help="Max model turns per user message (default env AIE_MAX_STEPS or 5).",
    ),
    max_tool_calls: int = typer.Option(
        None,
        "--max-tool-calls",
        help="Max tool calls per user message (default env AIE_MAX_TOOL_CALLS or 10).",
    ),
    max_tools_per_turn: int = typer.Option(
        None,
        "--max-tools-per-turn",
        help="Max tools executed per model turn (default env AIE_MAX_TOOLS_PER_TURN or 6).",
    ),
    command_timeout: int = typer.Option(
        30, "--command-timeout", min=1, max=300, help="Seconds for git/test commands."
    ),
    no_session: bool = typer.Option(
        False, "--no-session", help="Do not write .aie/sessions metadata."
    ),
    version: bool = typer.Option(False, "--version", help="Show version and exit."),
) -> None:
    if version:
        out.print(__version__)
        raise typer.Exit(0)

    # Project .env first, then optional Hermes OpenRouter key as fallback.
    load_env_file(workspace / ".env")
    load_env_file(Path.cwd() / ".env")
    if provider == "openrouter" and not (
        os.environ.get("AIE_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
    ):
        load_env_file(Path.home() / ".hermes" / ".env")

    try:
        steps = max_steps if max_steps is not None else _env_int("AIE_MAX_STEPS", 5)
        tool_budget = (
            max_tool_calls if max_tool_calls is not None else _env_int("AIE_MAX_TOOL_CALLS", 10)
        )
        tools_per_turn = (
            max_tools_per_turn
            if max_tools_per_turn is not None
            else _env_int("AIE_MAX_TOOLS_PER_TURN", 6)
        )
        if max_steps is not None and max_steps < 1:
            raise ValueError("--max-steps must be at least 1")
        if max_tool_calls is not None and max_tool_calls < 1:
            raise ValueError("--max-tool-calls must be at least 1")
        if max_tools_per_turn is not None and max_tools_per_turn < 1:
            raise ValueError("--max-tools-per-turn must be at least 1")
        loop, tools, store = build_runtime(
            workspace,
            provider_name=provider,
            allow_edits=allow_edits,
            allow_tests=allow_tests,
            max_steps=steps,
            max_tool_calls=tool_budget,
            max_tools_per_turn=tools_per_turn,
            command_timeout=command_timeout,
            session=not no_session,
        )
    except (RuntimeError, ValueError, OSError) as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(2) from error

    try:
        if prompt is not None:
            result = loop.run_turn(prompt)
            out.print(result.answer)
            code = 0 if result.status == "done" else 1
            raise typer.Exit(code)

        from ai_engineer.repl import run_repl

        code = run_repl(loop, tools, console=console, out=out)
        raise typer.Exit(code)
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    app()
