# Architecture

`aie` is a harness: the model chooses among registered tools; the harness owns permissions, path rules, budgets, and session metadata.

## Control flow

```text
user message
    → system prompt (AGENTS.md + permissions + optional skills)
    → model turn (may request tools)
        → execute up to per-turn tool cap
        → defer extras with error results
    → repeat until text answer, answer-only last step, or wrap-up
```

The last budgeted turn withholds tools so the model must answer. If steps are already spent after tools, a free wrap-up completion still requests a text answer.

## Package map

| Module | Role |
|--------|------|
| `cli.py` | Typer entry: flags, env load, one-shot or REPL |
| `repl.py` | Interactive loop and slash commands |
| `loop.py` | Message history, budgets, compaction, wrap-up |
| `provider.py` | `mock`, OpenAI-compatible HTTP, `openrouter` headers |
| `workspace.py` | Path sandbox, tools, scrubbed subprocess env |
| `tools/registry.py` | Schema validation and dispatch |
| `budget.py` | Turn and tool counters |
| `session.py` | Metadata-only JSONL writer |
| `instructions.py` | System prompt and `.aie/skills` snippets |
| `envfile.py` | Minimal `.env` loader |

## Providers

Providers implement `complete(messages, tools) → ModelResponse`. They never execute tools. The loop validates arguments and calls handlers.

## Workspace boundary

Relative paths only. No `..`. No symlink traversal. Excluded trees include `.git`, virtualenvs, `.aie`, and common credential filenames. File reads and edits are UTF-8 text with a 256 KiB cap.

## Child processes

Git and pytest run through `_command` with a fixed argv (no shell). The child environment is a scrubbed copy of `os.environ` (see [Security](security.md)). Timeouts use process-group kill on POSIX.

## Compaction

When the serialized transcript exceeds the request character budget, older large tool payloads shrink to short previews so wrap-up can still run.
