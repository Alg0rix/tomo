# Cline Brief — Foundation Task 3

**Repo:** `/home/dev-serv/Project/py-proj/tomo`  
**Plan:** `docs/superpowers/plans/2026-07-26-foundation-thin-vertical.md` — **Task 3 only**  
**Do not** implement Tasks 4–7 (no calculator registry, no agent loop, no chat/SSE rewrite).

## Goal

LLM client layer: Protocol + mock + OpenAI-compatible HTTP client + `get_llm()` factory. Tests green. Commit.

## Hard constraints

- Modular: keep each file focused (~150–250 lines; smell at ~400+).
- Config already exists in `app/core/config.py` (`LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, …).
- Use **httpx** (already a dependency). Full completion first — **no** streaming SSE from the LLM client in this task.
- Mock must support the calculator path used later: if user content contains `calculate` or `=`, first `complete` returns a `calculator` tool_call; when messages include a tool result, second call returns final text content.
- Raise a clear error if `LLM_PROVIDER=openai_compat` and API key is missing.
- Do **not** wire into `chat.py` / agent loop yet.

## Implement

1. `app/runtime/llm/base.py` — `ToolCall`, `LLMResponse`, `LLMClient` Protocol (`async def complete(...)`).
2. `app/runtime/llm/mock.py` — deterministic mock as above.
3. `app/runtime/llm/openai_compat.py` — `POST {base}/chat/completions` via `httpx.AsyncClient`; map OpenAI `tool_calls` → `ToolCall` list.
4. `app/runtime/llm/__init__.py` — `get_llm()` reads `LLM_PROVIDER` (`mock` | `openai_compat`).
5. Tests:
   - `tests/unit/runtime/llm/test_mock.py`
   - `tests/unit/runtime/llm/test_factory.py` (and httpx mock/respx or `httpx.MockTransport` for openai_compat if useful)
6. Commit: `feat: add mock and OpenAI-compatible LLM clients`

## Verify

```bash
uv run pytest tests/unit/runtime/llm/ -v
```

Expected: all PASS. Existing store/schema tests should still pass if you touch nothing there.

## Reference

- Plan Task 3 section
- Design: `docs/superpowers/specs/2026-07-26-foundation-thin-vertical-design.md` (LLM section)
- Config: `app/core/config.py`
