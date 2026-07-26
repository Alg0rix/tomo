# Cline Brief — Foundation Task 6

**Repo:** `/home/dev-serv/Project/py-proj/tomo`  
**Plan:** `docs/superpowers/plans/2026-07-26-foundation-thin-vertical.md` — **Task 6 only**  
**Do not** implement Task 7 (README/architecture closeout) unless needed for a one-line pointer.

## Goal

Wire web chat SSE to the real agent loop (`run_turn`). Coordinator-only execution. Persist history. Integration test with mock LLM + temp DB. Commit.

## Hard constraints

- Keep `_fmt_sse` and existing SSE **wire event names** the UI expects.
- Loop → SSE mapping (document in code comments):

| Loop kind | SSE event |
|-----------|-----------|
| thinking | `thinking` |
| tool | `tool` |
| tool_result | `tool_result` |
| final | optional `delta` chunks then `done` |
| error | `error` |

Always emit `state` (busy true/false) and `turn.start`.

- **Coordinator-only:** for a session, run only `coordinator_id` (ignore multi-agent `_pick_responders` execution). Keep `agent_ids` membership unchanged in DB.
- Persist: user message **before** loop; on events persist `tool_call` / `tool_output` / `final` / `error` via `append_session_history` (match existing ChatEntry shapes).
- Use `TOMO_DB_PATH` / store `rebind` in tests — never touch production `var/tomo.db`.
- Default LLM is mock — tests must pass without API keys.
- Modular: prefer thin chat wiring; put channel helpers in `app/channels/web.py` if it stays small.

## Implement

1. Rewrite `app/services/chat.py` to consume `run_turn` events → SSE (remove stub thinking/tool theater for the real path).
2. Touch `app/channels/web.py` if needed for the web channel entrypoint.
3. Integration test: `tests/integration/test_chat_mock.py`
   - Create session, stream a calc (or plain) turn with mock LLM
   - Collect SSE until `done` (or `error`)
   - Assert history has user + final (and tool entries for calc path)
4. Commit: `feat: wire web chat to real agent loop over SSE`

## Verify

```bash
uv run pytest tests/integration/test_chat_mock.py tests/unit/runtime/agent/ -v
```

Full suite should still pass:

```bash
uv run pytest -q
```

## Reference

- Loop: `app/runtime/agent/loop.py` (`run_turn`)
- Context: `app/runtime/agent/context.py`
- Current stub stream: `app/services/chat.py`
- Store: `append_session_history`, `get_session`, `set_busy`
- Design + plan Task 6 SSE table
