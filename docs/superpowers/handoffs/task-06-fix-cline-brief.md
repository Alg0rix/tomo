# Cline Brief — Foundation fix pass (Task 6 debt)

**Repo:** `/home/dev-serv/Project/py-proj/tomo`  
**Scope:** Fix **all** Task 6 adversarial findings below.  
**Do not** implement Task 7 yet (README closeout) in this commit — Cursor will dispatch Task 7 next.

## Must fix

### P1 — Busy stuck / yield-in-finally
**Files:** `app/channels/web.py`, `app/api/stream.py`

**Bug:** `stream_turn_sse` clears busy then **yields** busy-false inside `finally`. Early `return` on disconnect in `stream.py` may skip `aclose`; yielding in `finally` on GeneratorExit is unsafe.

**Fix:**
1. In `stream_turn_sse`: `try`/`finally` that **only** `store.set_busy(coordinator_id, False)` (no yield in finally). After the try/finally completes normally, yield the trailing busy-false `state`. On cancel/disconnect, busy is still cleared even if the trailing SSE is skipped.
2. In `stream.py`: wrap turn iteration with `contextlib.aclosing` (or explicit `agen.aclose()` in a `finally`) so disconnect always closes the generator.

**Test:** force a loop `error` (or mock) and assert agent not busy after stream drains; optional test that GeneratorExit / early close still clears busy.

### P1 — SSE `error` vs EventSource transport handler
**Files:** `app/static/js/chat.js` (and keep wire name `error` per plan)

**Bug:** `es.addEventListener('error', …)` treats named SSE `event: error` like a transport failure (“Stream interrupted”), ignores `data.message`, closes stream — busy badge can stick.

**Fix:** In the `error` listener, if `e.data` is present (MessageEvent from named SSE), parse JSON and show `message` as an agent error bubble (danger text), then close cleanly / reset sending — do **not** label it “Stream interrupted”. Only use “Stream interrupted” for true transport errors (no `e.data`).

Keep server wire event name `error` (plan contract).

**Test:** if JS is hard to unit-test, add a short comment in chat.js documenting the distinction; prefer a minimal node-free assertion or document in integration that error SSE includes message field (already true). Optional: small pure helper if you extract parse logic — don’t over-engineer.

### P2 — Restore `turn_id` on `done`
**File:** `app/channels/web.py`

Pass `turn_id` into `_map_event` (or close over it) so `done` data includes `turn_id` matching `turn.start`.

### P2 — Persist before yield (or before next event)
**File:** `app/channels/web.py`

For each loop event: **append_session_history first**, then yield SSE chunks — so disconnect after seeing an event still has durable history (especially `tool_call` / `final`).

## Verify

```bash
uv run pytest tests/integration/test_chat_mock.py tests/unit/runtime/agent/ -v
uv run pytest -q
```

## Commit

```bash
git commit -m "fix: harden chat SSE busy cleanup and error handling"
```

Leave progress.md for Cursor.
