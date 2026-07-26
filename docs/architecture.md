# Architecture

- **Surface vs runtime** — `app/api` and `app/web` stay thin; agent logic lives in `app/runtime`.
- **Schemas vs models** — Pydantic at the edge; SQL mixins in `app/models` for persistence.
- **Extensions at the root** — `skills/` and `plugins/` are installable trees; `app/extensions` loads them.
- **Tools in two places** — JSON contracts in `app/tools/`; Python backends in `app/runtime/tools/`.
- **Foundation thin vertical (live)** — the smallest end-to-end real path is wired: SQLite store (`app/models/`) → LLM (`app/runtime/llm/`, mock or OpenAI-compatible) → `calculator` tool (`app/runtime/tools/`) → coordinator-only agent turn loop (`app/runtime/agent/`) → web chat over SSE (`app/channels/web.py`, `app/services/chat.py`). Design spec: `docs/superpowers/specs/2026-07-26-foundation-thin-vertical-design.md`; plan: `docs/superpowers/plans/2026-07-26-foundation-thin-vertical.md`; progress: `docs/superpowers/progress/foundation.md`.
- **Eval / evaluator deferred** — nav + `/evaluate` + `/history` + `/api/eval/*` are off by default (`TOMO_EVAL_UI=1` to re-enable). Seed/stubs remain.
