# Alpha Slice B — Swarm Delegation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mid-turn handoff from coordinator to session member agents via `delegate` tool and `@mention`, with SSE `delegate` events and member replies in the transcript.

**Architecture:** Fill `app/runtime/coordinator/router.py` for membership-safe target pick. Add `delegate` tool. Web channel / loop: on force-mention or successful delegate tool, emit SSE `delegate`, then nested `run_turn` for the target agent. Persist handoff + member finals with correct `agent_id`.

**Tech Stack:** Existing agent loop, MockLLM tests, SSE web channel, tool registry.

**Master spec:** `docs/superpowers/specs/2026-07-26-alpha-kitchen-sink-design.md` Slice B.

## Global Constraints

- Do **not** start/restart the Tomo server.
- Do **not** implement Slices C–H.
- Non-members cannot be delegated to (return tool error string).
- Prefer ~150–250 lines/file; smell at ~400+.
- Restore honest-but-true “routes / handoff” copy now that delegation works.
- Commit at end per brief.

---

## File map

| Path | Responsibility |
|------|----------------|
| Fill: `app/runtime/coordinator/router.py` | `resolve_delegate_target(session, mention_or_id) -> agent_id \| None` |
| Create: `app/tools/delegate.json` | OpenAI tool schema |
| Create: `app/runtime/tools/delegate.py` | Validate args; membership check via store; return OK/error string |
| Modify: `app/runtime/tools/registry.py` | Register backend |
| Modify: `app/runtime/agent/loop.py` and/or `app/channels/web.py` | Handoff orchestration + SSE |
| Modify: `app/services/chat.py` | Optional @mention pre-parse |
| Modify: chat UI copy (`chat_home.html`, `sessions.js`, modals) | Restore routing language |
| Tests: extend `test_loop.py`, `test_chat_mock.py`, add `tests/unit/runtime/coordinator/test_router.py` | |

---

### Task 1: Router + delegate tool

**Interfaces:**
- `resolve_target(*, agent_ids: list[str], agents: list[dict], query: str) -> str | None` — match by id, name (casefold), or `@name`
- `parse_leading_mention(text: str) -> tuple[str | None, str]` — (`ops`, rest) or `(None, text)`
- Tool `delegate(agent_id|name, reason?)` → `"Delegated to {id}"` or `"Error: …"` (never raise)

- [ ] **Step 1:** Failing unit tests for router + tool membership
- [ ] **Step 2:** Implement router + tool + registry
- [ ] **Step 3:** pytest → PASS

---

### Task 2: Loop / web handoff + SSE + persistence

**Behavior (locked):**

1. **@mention force:** If user message starts with `@member` and member ∈ session → emit `delegate` `{from, to, reason:"mention"}` → `run_turn` as that agent (skip coordinator tool loop for this turn, or run coordinator only if mention invalid).
2. **Tool path:** Coordinator `run_turn` may call `delegate` tool → on success, after tool_result, emit `delegate` SSE → nested `run_turn(agent_id=target)` yielding that agent’s thinking/tool/delta/done with `agent_id` stamped to target. Coordinator may also produce a short final or empty; prefer member final as primary reply in transcript.
3. Set busy state for the active agent; clear when turn fully ends.
4. Persist entries with correct `agent_id` (including a lightweight `delegate` history row if schema supports it — type already exists).
5. `turn.start` should set `"delegate": true` when a handoff will/did occur (or emit separate events — keep client compatible; `sessions.js` already handles `delegate`).

- [ ] **Step 1:** Integration test with MockLLM: coordinator returns delegate tool call → member final appears
- [ ] **Step 2:** @mention test forces ops without coordinator tools
- [ ] **Step 3:** Implement web/loop wiring
- [ ] **Step 4:** pytest loop + chat_mock → PASS

---

### Task 3: UI copy + optional @ autocomplete + progress

- Restore dashboard/sessions copy: coordinator routes / @mention works
- Optional: simple @autocomplete in `sessions.js` from session agent list (nice-to-have)
- Mark Slice B done in `docs/superpowers/progress/alpha.md`
- Commit:

```bash
git add -A && git commit -m "$(cat <<'EOF'
feat: swarm mid-turn delegation via delegate tool and @mention

EOF
)"
```

---

## Out of scope

- New file/bash tools (C), workplaces (D), memory (E), Telegram (F)
