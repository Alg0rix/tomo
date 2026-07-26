# Cline Brief — Foundation fix pass (Task 5 debt)

**Repo:** `/home/dev-serv/Project/py-proj/tomo`  
**Scope:** Fix **all** adversarial findings from Task 5 below.  
**Do not** implement Task 6+ (no chat.py/SSE rewrite).

## Must fix (P1 + P2)

### P1 — Unpaired `tool_call` history
**File:** `app/runtime/agent/context.py`

After grouping consecutive `tool_call` entries, if fewer `tool_output` rows follow, emit synthetic `role: tool` messages for every unpaired call (content like `"Error: missing tool result"`). Never leave an assistant `tool_calls` message without matching tool results.

**Test:** history with tool_call then user (no tool_output) → messages include synthetic tool result before any later user.

### P1 — Empty tool-call ids collide across iterations
**File:** `app/runtime/agent/loop.py`

`_with_ids` must use a **turn-scoped monotonic counter** (or UUID), not `call_{i}` reset per response. Same id must be used in the assistant tool_calls message and tool results.

**Test:** two tool rounds with empty ids → distinct ids in the messages fed to the second `complete`.

### P1 — Setup failures must yield `error` events
**File:** `app/runtime/agent/loop.py`

Wrap client/schema/message setup (`get_llm()`, `get_openai_tools()`, `build_messages`) so `LLMConfigError` / `LLMProviderError` / unexpected setup errors yield `{"kind":"error","message":...}` and return — never raise out of `run_turn`.

**Test:** inject failing `get_llm` or pass a broken path; consumer gets error event, no exception.

### P2 — Extra `tool_output` must not reuse last id
**File:** `app/runtime/agent/context.py`

Drop surplus `tool_output` rows beyond the number of calls (do not map extras onto `calls[-1]["id"]`).

### P2 — `user_message: str | None`
**File:** `app/runtime/agent/loop.py` (+ context already supports None)

Allow `user_message=None` so Task 6 can persist user first then pass history that already includes it without duplicating.

**Test:** history ending with user + `user_message=None` → only one trailing user in messages.

### P2 — Error flag
**File:** `app/runtime/agent/loop.py`

Use `startswith("Error:")` (not bare `"Error"`). Coerce result with `str(...)`.

### P3 — System prompt Unicode
**File:** `app/runtime/agent/context.py`

`except (OSError, UnicodeError):` → fallback prompt.

## Verify

```bash
uv run pytest tests/unit/runtime/agent/ -v
uv run pytest tests/unit/runtime/ -q
```

## Commit

```bash
git commit -m "fix: harden agent context pairing and turn error surfacing"
```

Leave progress.md for Cursor.
