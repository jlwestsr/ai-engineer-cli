# Quickstart

Install `aie`, run a mock smoke prompt, then talk to a live provider.

## Requirements

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)
- A git workspace you trust (the harness runs tools in that tree)

## Install

```bash
cd /path/to/ai-engineer-cli
uv sync --all-extras
```

## Mock smoke test

The `mock` provider needs no API key. It scripts a few tool demos.

```bash
uv run aie --provider mock --no-session "read AGENTS.md"
```

Expected: progress lines on stderr, then a JSON-like tool result or short answer on stdout, exit code `0`.

## Interactive REPL

```bash
uv run aie --provider mock
```

Type `/help` for slash commands. Type `/quit` to exit.

## OpenRouter

1. Copy `.env.example` to `.env`.
2. Set `OPENROUTER_API_KEY` in `.env`.
3. Run:

```bash
uv run aie --provider openrouter "Summarize AGENTS.md in one sentence."
```

Default model: `deepseek/deepseek-v4-pro` (`AIE_MODEL` overrides it).
Default base URL: `https://openrouter.ai/api/v1`.

If the project `.env` has no key, `aie` also reads `OPENROUTER_API_KEY` from `~/.hermes/.env` when present.

## Watch progress

Progress goes to stderr. The final answer goes to stdout.

```bash
uv run aie --provider openrouter "review this project" \
  2> >(tee /tmp/aie-stderr.txt >&2)
```

## Next reading

- [CLI reference](cli.md) — flags, env vars, tools, exit codes
- [Security](security.md) — permissions and threat model
- [Architecture](architecture.md) — control loop and package layout
