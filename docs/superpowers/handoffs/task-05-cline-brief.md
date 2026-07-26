# Cline Brief — Foundation Task 5

**Repo:** `/home/dev-serv/Project/py-proj/tomo`  
**Plan:** `docs/superpowers/plans/2026-07-26-foundation-thin-vertical.md` — **Task 5 only**  
**Do not** implement Tasks 6–7 (no chat.py/SSE rewrite, no README).

## Goal

Agent context builder + turn loop that uses `get_llm()` + tool registry, yields internal events, respects `LLM_MAX_TOOL_ITERATIONS`. Tests with mock LLM. Commit.

## Hard constraints

- Modular: `context.py` builds messages; `loop.py` orchestrates only — **no HTTP, no SSE formatting**.
- Event kinds (exact `kind` strings for Task 6 mapping):

```python
{"kind": "thinking", "content": str}          # optional
{"kind": "tool", "tool": str, "args": dict}
{"kind": "tool_result", "tool": str, "result": str, "error": bool}
{"kind": "final", "content": str}
{"kind": "error", "message": str}
```

- Prefer async generator: `async def run_turn(...) -> AsyncIterator[dict]`.
- Coordinator turn only (no multi-agent delegation).
- Use `LLM_MAX_TOOL_ITERATIONS` from `app.core.config` to stop tool loops.
- Persist **not** required in Task 5 (store wiring is Task 6) — loop may accept history messages / agent_id / session_id as inputs for context building; do not rewrite `chat.py`.
- System prompt: short constant **or** `defaults/coordinator_system.md` if present / create a small default file.

## Implement

1. `app/runtime/agent/context.py` — build OpenAI-style messages from session history entries (`user` / `final` / `tool_call` / `tool_output` / …) + system prompt.
2. `app/runtime/agent/loop.py` — call LLM with `get_openai_tools()`; on tool_calls → emit tool → `execute` → emit tool_result → append tool messages → repeat until final content or max iterations → emit final or error.
3. Tests: `tests/unit/runtime/agent/test_loop.py`
   - text-only path → `final`
   - calculator path (mock) → `tool` → `tool_result` → `final`
   - max iterations stops cleanly (emit `error` or `final` with clear stop — pick one and test it)
4. Commit: `feat: agent turn loop with tool iterations`

## Verify

```bash
uv run pytest tests/unit/runtime/agent/ -v
```

Also ensure tools/llm tests still pass if you touch shared imports:

```bash
uv run pytest tests/unit/runtime/ -v
```

## Reference

- Plan Task 5
- LLM: `app/runtime/llm/`
- Tools: `app/runtime/tools/registry.py` (`get_openai_tools`, `execute`)
- Config: `LLM_MAX_TOOL_ITERATIONS`
- Design: event kinds for SSE mapping in Task 6
