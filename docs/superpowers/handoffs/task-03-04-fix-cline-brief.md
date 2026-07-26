# Cline Brief — Foundation fix pass (Task 3–4 debt)

**Repo:** `/home/dev-serv/Project/py-proj/tomo`  
**Scope:** Fix **all** adversarial findings from Tasks 3–4 below.  
**Do not** implement Task 5+ (no agent loop, no chat/SSE).

## Must fix (P1 + P2)

### P1 — Calculator size bomb + “never raises” contract
**File:** `app/runtime/tools/calculator.py`

**Bugs:**
1. Nested `**` like `(10**200)**200` bypasses per-op exponent cap, builds a huge int, then `str()` in `_format_result` raises `ValueError` — **`evaluate`/`run` leak exceptions** despite “never raises”.
2. Float exponents (`2**1000.0`) skip the int-only exponent cap.
3. `(-2)**0.5` returns a complex string.

**Fix:**
- Cap result size before/after pow (e.g. reject if either operand’s `bit_length()` or estimated result digits exceed a small limit; also cap float exponents).
- Reject non-real results (complex) with an error string.
- Wrap `_format_result` / final `str` so `evaluate`/`run` **never raise** (return `Error: ...`).
- Keep registry `execute` as a second line of defense.

**Tests:** huge nested pow → error string, no exception; float huge exponent → error; `(-2)**0.5` → error string; `2+2` still `"4"`.

### P1 — Mock multi-turn: any historical tool ends calc forever
**File:** `app/runtime/llm/mock.py`

**Bug:** `_has_tool_result = any(role == "tool")` over full history. After one calculator turn in a session, later `calculate 5+5` returns `_CALC_FINAL` with no tool call.

**Fix:** Treat tool results as “continue this turn” only when they appear **after the latest user message** (typical: messages after the last `user` include a `tool` role). A new user message must be able to trigger calculator again even if older turns had tools.

**Tests:**
- In-turn: user calc → (simulated) tool result after that user → final text (no new tool).
- Multi-turn: user calc + tool + assistant final, then **new** user `calculate 3+3` → **new** calculator tool call.
- Update/remove `test_tool_result_takes_precedence_over_calc_keyword` if it encoded the buggy behavior.

### P2 — Whitespace API key
**File:** `app/runtime/llm/openai_compat.py`

**Fix:** `resolved_key = (resolved_key or "").strip()`; empty after strip → `LLMConfigError`.

**Test:** `"   "` raises `LLMConfigError`.

### P2 — Non-dict tool arguments from JSON
**File:** `app/runtime/llm/openai_compat.py` (`_parse_tool_calls`)

**Fix:** After `json.loads`, if not a `dict`, coerce to `{"_raw": args_raw}` (or `{}` + keep raw string). Never leave a list/number on `ToolCall.arguments`.

**Test:** arguments JSON `[1,2]` → `arguments` is a `dict`.

### P2 — Malformed `choices[0]` leaks AttributeError
**File:** `app/runtime/llm/openai_compat.py`

**Fix:** If `choices[0]` is not a `dict` (e.g. `null`), raise `LLMRequestError`, not AttributeError. Validate tool_calls entries similarly where cheap.

**Test:** `choices: [null]` → `LLMRequestError`.

### P2 — Complex calculator results
Covered under P1 calculator fix.

## Should fix (P3)

### P3 — Mock should respect `tools=`
**File:** `app/runtime/llm/mock.py`

Only emit calculator tool calls when `tools` is non-empty **and** includes a function named `calculator`. If `tools is None` or empty, return text-only (default reply) even for calc-looking prompts. When tools include calculator, keep current calc behavior.

**Test:** calc prompt with `tools=None` → no tool_calls; with calculator schema → tool_calls.

### P3 — Base URL double `/chat/completions`
**File:** `app/runtime/llm/openai_compat.py`

If `base_url` already ends with `/chat/completions`, use it as the endpoint as-is (don’t append again).

**Test:** base `http://x/v1/chat/completions` → endpoint unchanged.

### P3 — httpx client per request (optional if cheap)
Prefer holding a client on the instance or documenting deferral. If easy: create client in `__init__` with optional `transport`, reuse in `complete`, add `async def aclose()`. Don’t block the commit on a large refactor — reuse via `AsyncClient` as context once is OK if you add a note; **preferred** is instance-level client for tests with MockTransport.

If instance client is awkward with current tests, skip and leave a one-line comment — but still fix P1/P2/P3 mock+URL above.

## Out of scope
- Dynamic import of JSON `backend` (hardcoded `_BACKENDS` is correct).
- Agent loop / chat.py.

## Verify

```bash
uv run pytest tests/unit/runtime/ -v
```

All must PASS including new regression tests.

## Commit

```bash
git commit -m "fix: harden LLM mock, openai_compat, and calculator bounds"
```

Leave `docs/superpowers/progress/foundation.md` for Cursor.
