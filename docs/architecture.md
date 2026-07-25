# Architecture

- **Surface vs runtime** — `app/api` and `app/web` stay thin; agent logic lives in `app/runtime`.
- **Schemas vs models** — Pydantic at the edge; SQL mixins in `app/models` for persistence.
- **Extensions at the root** — `skills/` and `plugins/` are installable trees; `app/extensions` loads them.
- **Tools in two places** — JSON contracts in `tools/`; Python backends in `app/runtime/tools/`.
