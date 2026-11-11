# CLI reference

`aie` is the AI Engineer CLI entry point. Omit the prompt to open the REPL. Pass a prompt for one-shot mode.

## Synopsis

```bash
uv run aie [OPTIONS] [PROMPT]
```

## Options

| Flag | Env default | Meaning |
|------|-------------|---------|
| `--workspace`, `-w` | current directory | Root for tools and `AGENTS.md` |
| `--provider`, `-p` | `mock` | `mock`, `openai`, or `openrouter` |
| `--allow-edits` | off | Register `edit_file` and `create_file` |
| `--allow-tests` | off | Register `run_python_tests` (runs pytest) |
| `--max-steps` | `AIE_MAX_STEPS` or `5` | Max model turns per user message |
| `--max-tool-calls` | `AIE_MAX_TOOL_CALLS` or `10` | Max tool calls per user message |
| `--max-tools-per-turn` | `AIE_MAX_TOOLS_PER_TURN` or `6` | Max tools executed per model turn |
| `--command-timeout` | `30` | Seconds for git/pytest child processes (1–300) |
| `--no-session` | off | Skip `.aie/sessions/` metadata |
| `--version` | — | Print version and exit |
| `--help` | — | Print help and exit |

## Environment variables

Load order: workspace `.env`, then cwd `.env`. For `openrouter` only, if still no key, load `~/.hermes/.env`.

| Variable | Used by | Default |
|----------|---------|---------|
| `OPENROUTER_API_KEY` | `openrouter` provider | required unless `AIE_API_KEY` set |
| `AIE_API_KEY` | `openai` / `openrouter` | — |
| `OPENAI_API_KEY` | `openai` fallback | — |
| `AIE_BASE_URL` | HTTP providers | OpenAI or OpenRouter host by provider |
| `AIE_MODEL` | HTTP providers | `gpt-4o-mini` or `deepseek/deepseek-v4-pro` |
| `AIE_MAX_STEPS` | budgets | `5` |
| `AIE_MAX_TOOL_CALLS` | budgets | `10` |
| `AIE_MAX_TOOLS_PER_TURN` | budgets | `6` |
| `AIE_HTTP_REFERER` | OpenRouter headers | project GitHub URL |
| `AIE_APP_TITLE` | OpenRouter headers | `AI Engineer CLI` |

## Providers

| Name | Network | Notes |
|------|---------|-------|
| `mock` | no | Scripted demos for offline tests |
| `openrouter` | yes | OpenAI-compatible; sends `HTTP-Referer` and `X-Title` |
| `openai` | yes | Any OpenAI-compatible `/chat/completions` host |

## Tools

### Always available

| Tool | Purpose |
|------|---------|
| `list_files` | List workspace files (excludes secrets/generated paths) |
| `read_file` | Read numbered UTF-8 lines + SHA-256 |
| `search_text` | Literal text search |
| `python_symbols` | Outline Python defs without importing |
| `git_status` | Read-only `git status` |
| `git_diff` | Read-only `git diff` |
| `update_plan` / `get_plan` | In-memory task plan |
| `get_current_time` | Local timestamp |

### Gated

| Tool | Grant | Purpose |
|------|-------|---------|
| `edit_file` | `--allow-edits` or `/allow edits` | Exact one-match replace after SHA check |
| `create_file` | `--allow-edits` or `/allow edits` | Create a new file only |
| `run_python_tests` | `--allow-tests` or `/allow tests` | `python -m pytest -q -p no:cacheprovider` |

There is no arbitrary shell, package install, delete, commit, push, or deploy tool.

## REPL slash commands

| Command | Action |
|---------|--------|
| `/help` | Show slash help |
| `/status` | Workspace, permissions, budget |
| `/plan` | Show task plan |
| `/allow edits` | Enable edit tools |
| `/allow tests` | Enable pytest tool |
| `/clear` | Clear conversation history and plan (`/reset` alias) |
| `/workspace` | Print workspace root |
| `/quit` | Exit |

Each user message resets the turn/tool budgets. Conversation history stays in memory for that REPL process until `/clear`.

## Budgets and wrap-up

- Extra tools beyond the per-turn cap are deferred with an error result (not a hard crash).
- The last budgeted model turn is answer-only (no tools).
- If tool rounds use every step, or context grows near the request limit, `aie` forces a wrap-up answer.

## Sessions

Unless `--no-session`, `aie` writes metadata JSONL under `<workspace>/.aie/sessions/<id>.jsonl` (mode `0600`). Events include session start/end, model turns, tool names, durations, and error flags. They do not include prompts, answers, file contents, tool arguments, or API keys.

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | The harness produced an answer (`status=done`) |
| `1` | Runtime failure or blocked budget/context outcome |
| `2` | Bad flags / missing credentials / config error |

Exit `0` does not mean tests passed. Read the tool output when you use `--allow-tests`.

## One-shot as a sub-agent

Other agents can call `aie` non-interactively:

```bash
uv run aie --provider openrouter --workspace /path/to/repo --no-session \
  "Report root cause of the failing test with file:line evidence."
```

Progress: stderr. Answer: stdout.
