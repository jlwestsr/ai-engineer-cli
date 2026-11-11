# AI Engineer CLI (`aie`)

`aie` is a local harness for AI-assisted software engineering in the terminal.
The model selects tools. The harness owns permissions, budgets, and session metadata.

**Reader outcome:** install `aie`, run a mock smoke prompt, then call a live provider with clear grants for edits and tests.

## Install and first run

```bash
uv sync --all-extras
uv run aie --provider mock --no-session "read AGENTS.md"
uv run aie --provider mock   # interactive REPL
```

Full steps: [docs/quickstart.md](docs/quickstart.md).

## Live providers

| Provider | API key | Notes |
|----------|---------|-------|
| `openrouter` | `OPENROUTER_API_KEY` or `AIE_API_KEY` | Default model `deepseek/deepseek-v4-pro` |
| `openai` | `AIE_API_KEY` or `OPENAI_API_KEY` | Any OpenAI-compatible base URL |
| `mock` | none | Offline demos and tests |

```bash
cp .env.example .env   # set OPENROUTER_API_KEY
uv run aie --provider openrouter "Summarize AGENTS.md in one sentence."
```

## Trust boundary

`AGENTS.md`, workspace files, and `.aie/skills` are untrusted text in the system
prompt. Do not enable `--allow-edits` or `--allow-tests` on a tree you do not trust.
Details: [docs/security.md](docs/security.md).

## Permissions

Inspection tools are always on. Edits and tests need an explicit grant:

```bash
uv run aie --provider openrouter --allow-edits --allow-tests "fix the failing test"
```

`--allow-tests` runs `python -m pytest -q`. Install the suite with `uv sync --all-extras` first.

REPL grants: `/allow edits`, `/allow tests`. Help: `/help`.

## Budgets

Each user message has turn and tool budgets (`AIE_MAX_STEPS`, `AIE_MAX_TOOL_CALLS`).
At most `AIE_MAX_TOOLS_PER_TURN` tools run per model turn. The harness defers extra parallel requests. The last budgeted turn is answer-only.

## Sessions

Unless you pass `--no-session`, metadata JSONL lands under `.aie/sessions/` (gitignored). Sessions record tool names and timings. They do not record prompts, answers, file contents, or API keys.

## Documentation map

| Doc | Purpose |
|-----|---------|
| [docs/quickstart.md](docs/quickstart.md) | Install to first success |
| [docs/cli.md](docs/cli.md) | Flags, env, tools, exit codes |
| [docs/architecture.md](docs/architecture.md) | Control loop and modules |
| [docs/security.md](docs/security.md) | Trust model and limits |
| [docs/terminology.md](docs/terminology.md) | Approved terms |
| [docs/llms.txt](docs/llms.txt) | Agent-oriented index |

## Non-goals (v1)

No web UI, Neo4j memory, Copilot/Cursor wrapping, or cloud/Terraform apply tools.
The human owns git commit and push.

## Development

```bash
uv sync --all-extras
uv run pytest -q
```

Requires Python 3.12+. License: MIT (see [LICENSE](LICENSE)).
