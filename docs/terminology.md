# Terminology

Use one approved term per concept in docs, CLI help, and error text.

| Concept | Approved term | Do not use |
|---------|---------------|------------|
| This product / binary | `aie` or AI Engineer CLI | bot, agent app, chatbot |
| Software around the model | harness | wrapper, framework, orchestrator (in user docs) |
| Model backend | provider | LLM vendor, backend, engine |
| One user prompt cycle | turn | step (except budget flag names), round |
| Tool invocation limit per message | tool budget | tool quota, call limit |
| Cap on tools in one model response | per-turn tool cap | batch limit |
| Workspace file/git/test actions | tools | functions, plugins |
| Explicit edit/test flags | permission / grant | capability, privilege |
| Metadata JSONL under `.aie/sessions/` | session | trace log, transcript (sessions are metadata-only) |
| OpenAI-style HTTP chat API | OpenAI-compatible endpoint | OpenAI API (unless that host) |
| Credential for HTTP providers | API key | token, secret (in docs; scrub markers still match those strings in env names) |
