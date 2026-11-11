# Security

`aie` reduces accidental damage and credential leakage. It is not a full sandbox against malicious repository code.

## Trust model

| Actor | Trust |
|-------|-------|
| Human operator | Chooses workspace, grants edits/tests, owns git commit/push |
| Model | Untrusted; may only call registered tools |
| Workspace files / `AGENTS.md` / `.aie/skills` | Untrusted text spliced into the system prompt — can steer the model. Skill files load only via symlink-safe reads under `.aie/skills/<name>/SKILL.md` |
| Pytest under `--allow-tests` | Executes repository code with the operator's OS user |

Do not point `aie` at an untrusted tree with `--allow-edits` or `--allow-tests`.
Running with grants enabled in a cloned third-party repo is an operator decision:
repo text can steer the model into editing that same repo.

## Permissions

Inspection tools register by default. `edit_file` / `create_file` require `--allow-edits` (or `/allow edits`). `run_python_tests` requires `--allow-tests` (or `/allow tests`).

When a grant is off, the harness omits gated tools from the tool list. Handlers also reject calls if the grant is missing.

## Path and content limits

- Paths must stay inside the workspace.
- Symlinks are rejected at walk time; file reads use `O_NOFOLLOW` so a planted final-component symlink cannot race past the check.
- Hidden/generated/credential-like names are excluded from discovery, reads, and filtered git status/diff paths.
- File size, line window, search hits, and tool output length are capped.

## Subprocess environment scrub

Before git or pytest starts, `scrub_subprocess_env` (shared policy in `envpolicy.py`) removes:

- Credential-shaped names (`API_KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `KEY_ID`, `CREDENTIAL`, `AUTHORIZATION`, `SESSION_*`, `*_KEY*`)
- Process-control variables (`PYTHONPATH`, `LD_PRELOAD`, `NODE_OPTIONS`, `JAVA_TOOL_OPTIONS`, and related)
- `GIT_*`, `PIP_*`, and `UV_*` prefixes, then sets `GIT_TERMINAL_PROMPT=0`

The `.env` loader uses the same policy and also refuses to inject `PATH`.

Scrubbing does not redact tool output. A test that prints its own environment can still leak values into the chat transcript.

## Sessions

Session files are metadata-only and created with mode `0600`. Do not commit `.aie/`. Keep API keys in `.env` (gitignored), not in the repo.

## What the harness does not do

- No OS container or network jail for tools
- No commit, push, or cloud apply tools
- No guarantee against prompt injection from repo text
- No secret scanning of ordinary source files
