# Cline Brief — Foundation Task 4

**Repo:** `/home/dev-serv/Project/py-proj/tomo`  
**Plan:** `docs/superpowers/plans/2026-07-26-foundation-thin-vertical.md` — **Task 4 only**  
**Do not** implement Tasks 5–7 (no agent loop, no chat/SSE rewrite).

## Goal

Calculator tool (safe `ast` eval) + registry that loads `tools/*.json` and dispatches `execute(name, arguments) -> str`. Tests green. Commit.

## Hard constraints

- Modular files (~150–250 lines; smell at ~400+).
- **No** `eval()` / `exec()` of arbitrary code — use `ast` with a whitelist of safe nodes (literals + arithmetic ops).
- Invalid expression → return an **error string**, do not raise to the caller.
- Unknown tool name → return an **error string**, do not raise.
- Success for `2+2` → `"4"` (string).
- Do **not** wire into chat/agent loop yet.
- Keep OpenAI tool schema shape compatible with `LLMClient.complete(..., tools=...)`.

## Implement

1. Ensure `tools/calculator.json` has a valid OpenAI function-tool schema (`expression` argument).
2. `app/runtime/tools/calculator.py` — `run(arguments: dict) -> str` (or similar) using safe ast evaluation.
3. `app/runtime/tools/registry.py` — load `tools/*.json`; `get_openai_tools() -> list[dict]`; `execute(name, arguments) -> str`. Foundation may hardcode calculator backend mapping.
4. Tests:
   - `tests/unit/runtime/tools/test_calculator.py`
   - `tests/unit/runtime/tools/test_registry.py`
5. Commit: `feat: calculator tool and registry dispatch`

## Verify

```bash
uv run pytest tests/unit/runtime/tools/ -v
```

Expected: all PASS. Existing LLM/store tests should still pass if untouched.

## Reference

- Plan Task 4
- Design spec tools section
- Mock LLM already emits `ToolCall(name="calculator", arguments={"expression": ...})` — registry must accept that shape
- Existing stubs: `app/runtime/tools/`, `tools/calculator.json`
