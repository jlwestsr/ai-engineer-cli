"""Interactive REPL with Rich output and slash commands."""

from __future__ import annotations

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from ai_engineer.budget import Budget
from ai_engineer.loop import AgentLoop
from ai_engineer.workspace import WorkspaceTools

HELP_TEXT = """\
[bold]Slash commands[/bold]
  /help              Show this help
  /status            Show workspace, permissions, and budget
  /plan              Show the current task plan
  /allow edits       Enable edit_file and create_file
  /allow tests       Enable run_python_tests
  /workspace         Show workspace root
  /quit              Exit the REPL

Anything else is sent to the model as a user turn.
"""


def run_repl(
    loop: AgentLoop,
    tools: WorkspaceTools,
    *,
    console: Console,
    out: Console,
) -> int:
    console.print(
        Panel.fit(
            f"[bold]aie[/bold]  workspace={tools.root}\n"
            f"edits={'on' if tools.allow_edits else 'off'}  "
            f"tests={'on' if tools.allow_tests else 'off'}  "
            f"turns={loop.budget.max_steps}  "
            f"tools={loop.budget.max_tool_calls}  "
            f"per-turn={loop.max_tools_per_turn}",
            title="AI Engineer CLI",
        )
    )
    console.print("Type /help for commands. Empty line is ignored.\n")

    while True:
        try:
            line = console.input("[bold cyan]aie>[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/dim]")
            return 0
        if not line:
            continue
        if line.startswith("/"):
            if _handle_slash(line, loop, tools, console=console, out=out):
                return 0
            continue

        # Fresh budget per user message in the REPL.
        loop.budget = Budget(
            max_steps=loop.budget.max_steps,
            max_tool_calls=loop.budget.max_tool_calls,
        )
        result = loop.run_turn(line)
        if result.status == "done":
            out.print(Markdown(result.answer))
        else:
            console.print(f"[yellow]{result.status}:[/yellow] {result.answer}")


def _handle_slash(
    line: str,
    loop: AgentLoop,
    tools: WorkspaceTools,
    *,
    console: Console,
    out: Console,
) -> bool:
    """Return True when the REPL should exit."""
    parts = line.split()
    command = parts[0].lower()
    if command in {"/quit", "/exit", "/q"}:
        console.print("[dim]bye[/dim]")
        return True
    if command == "/help":
        console.print(HELP_TEXT)
        return False
    if command == "/workspace":
        console.print(str(tools.root))
        return False
    if command == "/status":
        table = Table(title="Status")
        table.add_column("Key")
        table.add_column("Value")
        table.add_row("workspace", str(tools.root))
        table.add_row("allow_edits", str(tools.allow_edits))
        table.add_row("allow_tests", str(tools.allow_tests))
        for key, value in loop.budget.snapshot().items():
            table.add_row(key, str(value))
        if loop.session is not None:
            table.add_row("session", str(loop.session.path))
        console.print(table)
        return False
    if command == "/plan":
        plan = tools.get_plan({})
        steps = plan.get("steps") or []
        if not steps:
            console.print("[dim](empty plan)[/dim]")
            return False
        table = Table(title="Plan")
        table.add_column("Status")
        table.add_column("Title")
        for step in steps:
            table.add_row(step["status"], step["title"])
        console.print(table)
        return False
    if command == "/allow":
        if len(parts) < 2:
            console.print("Usage: /allow edits | /allow tests")
            return False
        target = parts[1].lower()
        if target == "edits":
            tools.set_permissions(allow_edits=True)
            loop.refresh_tools()
            console.print("[green]edits enabled[/green]")
        elif target == "tests":
            tools.set_permissions(allow_tests=True)
            loop.refresh_tools()
            console.print("[green]tests enabled[/green]")
        else:
            console.print("Usage: /allow edits | /allow tests")
        return False
    console.print(f"Unknown command: {command}. Try /help.")
    return False
