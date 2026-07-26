# Progress — Task 6 fix pass (Cline → Cursor)

**Date:** 2026-07-26
**Commit:** `fix: harden chat SSE busy cleanup and error handling`
**Scope:** All Task 6 adversarial findings (P1 + P2). No Task 7 (README closeout) in this commit — Cursor dispatches next.

## Fixes

### P1 — Busy stuck / yield-in-finally
- `app/channels/web.py` `stream_turn_sse`: the `try/finally` now **only** does
  `store.set_busy(coordinator_id, False)` (no yield in `finally`). The trailing
  busy-false `state` is yielded **after** the try/finally completes normally, so
  on a clean drain the UI still sees busy go false; on cancel/disconnect the
  `finally` still clears busy even though the trailing SSE is skipped. Yielding
  in `finally` on GeneratorExit was unsafe (ignored / raises).
- `app/services/chat.py`: `run_session_turn` / `run_turn` now wrap
  `stream_turn_sse(...)` in `contextlib.aclosing`, so closing the outer
  generator cascades into `stream_turn_sse`'s `finally` (synchronous busy clear)
  instead of leaving it suspended until GC.
- `app/api/stream.py`: both SSE routes wrap the turn generator **and** the
  heartbeat stream in `contextlib.aclosing`, so a client disconnect (early
  `return` on `is_disconnected`, or Starlette closing `event_source`) closes the
  generators promptly. This is the route-level half of the cascade; the chat.py
  half closes `stream_turn_sse` itself.

### P1 — SSE `error` vs EventSource transport handler
- `app/static/js/chat.js`: the `error` listener now distinguishes a **named SSE
  `error` event** (`e.data` present → MessageEvent) from a **transport failure**
  (no `e.data` → Event, `readyState === CLOSED`). Named errors parse
  `data.message` and render it as an agent error bubble (danger text), reset the
  badge to online, and close cleanly — no longer mislabeled "Stream interrupted".
  Only true transport errors show "Stream interrupted". The `closed` guard still
  suppresses the spurious `error` event fired by `es.close()`. Server wire event
  name stays `error` (plan contract). A documenting comment explains the split.

### P2 — Restore `turn_id` on `done`
- `app/channels/web.py`: `_map_event` now takes `turn_id` and the `done` event
  data includes `turn_id` matching the leading `turn.start`.

### P2 — Persist before yield
- `app/channels/web.py`: each loop event appends its history entry **before**
  yielding the SSE chunk (and the agent-not-found error branch was reordered the
  same way), so a disconnect the instant a chunk is seen still leaves durable
  history (notably `tool_call` / `final`).

## Tests added (`tests/integration/test_chat_mock.py`)
- `test_loop_error_clears_busy_after_stream_drains` — forces a setup failure
  (monkeypatch `get_llm`), drains the stream, asserts the `error` SSE event
  carries the message, the trailing busy-false `state` is emitted, the `error`
  history entry is durable, and `store.get_agent("main")["busy"] is False`.
- `test_early_close_persists_seen_event_and_clears_busy` — mirrors the route
  `aclosing` wiring, breaks right after the `tool` event, asserts `tool_call` is
  durable (persist-before-yield) and busy is cleared via the aclose cascade
  without the trailing state.
- `test_plain_turn_emits_done_and_persists_user_final` extended: `turn.start` and
  `done` share the same `turn_id`.

## Verify
```
uv run pytest tests/integration/test_chat_mock.py tests/unit/runtime/agent/ -v
uv run pytest -q
```

## Notes for Cursor
- `aclosing` is also applied to the heartbeat streams (clean shutdown of the
  infinite sleep loop on disconnect) — harmless and correct.
- The busy-clear cascade is `stream.py (aclosing) → chat.py (aclosing) →
  web.py (finally: set_busy False)`. Both `aclosing` layers are needed: closing
  the route generator alone does not close `stream_turn_sse` (`async for` does
  not close its iterator on exception/early exit), so the chat.py `aclosing` is
  what makes the `finally` run synchronously.
- No Task 7 changes (README/progress-doc closeout) in this commit, per brief.
