# AI Engineer CLI (`aie`)

Interactive terminal REPL for AI-assisted software engineering. The model
reasons; the harness owns tools, permissions, budgets, and session metadata.

```bash
uv sync --all-extras
uv run aie --provider mock "read AGENTS.md"
uv run aie --provider mock          # interactive REPL
```

## Providers

| Provider | Use |
|----------|-----|
| `mock` | Offline scripted demos and smoke tests (default) |
| `openrouter` | [OpenRouter](https://openrouter.ai) (`OPENROUTER_API_KEY`, default model `deepseek/deepseek-v4-pro`) |
| `openai` | OpenAI-compatible chat completions (`AIE_API_KEY`, optional `AIE_BASE_URL`, `AIE_MODEL`) |

```bash
cp .env.example .env   # set OPENROUTER_API_KEY
uv run aie --provider openrouter "Summarize AGENTS.md in one sentence."

# Or OpenAI / any compatible proxy:
export AIE_API_KEY=sk-...
export AIE_BASE_URL=https://api.openai.com/v1
export AIE_MODEL=gpt-4o-mini
uv run aie --provider openai --allow-edits --allow-tests
```

If `--provider openrouter` is used and no project `.env` key is set, `aie` will
also try `~/.hermes/.env` for `OPENROUTER_API_KEY`.

## Budgets

Each user message has turn and tool budgets (`AIE_MAX_STEPS`, `AIE_MAX_TOOL_CALLS`).
At most `AIE_MAX_TOOLS_PER_TURN` (default 6) tools run per model turn; extra
parallel requests are deferred with an error so the model can continue instead of
hard-blocking.

## Permissions

Inspection tools are always available. Edits and tests are explicit grants:

```bash
uv run aie --allow-edits --allow-tests "fix the failing test"
```

`--allow-tests` runs `python -m pytest -q` (uses `pyproject.toml` `testpaths` /
`pythonpath`). Install the suite with `uv sync --all-extras` first.

In the REPL: `/allow edits`, `/allow tests`, `/status`, `/plan`, `/help`, `/quit`.

## Sessions

Metadata-only JSONL sessions land under `.aie/sessions/` (gitignored). Traces
record turn counts, tool names, durations, and errors — not prompts, answers,
source contents, or API keys.

## Non-goals (v1)

No web UI, Neo4j memory, Copilot/Cursor wrapping, or cloud/Terraform apply tools.
Human owns git commit and push.
