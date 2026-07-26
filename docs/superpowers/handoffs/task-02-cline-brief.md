# Cline Brief — Foundation Task 2

**Repo:** `/home/dev-serv/Project/py-proj/tomo`  
**Plan:** `docs/superpowers/plans/2026-07-26-foundation-thin-vertical.md` — **Task 2 only**  
**Do not** implement Tasks 3–7 (no LLM, no chat rewrite, no calculator).

## Goal

SQLite-backed hybrid store: agents/sessions/messages/settings in DB; platform lists stay on `platform_data`. Thin `store.py` facade. Seed on empty DB. Tests green. Commit.

## Hard constraints

- Modular: each mixin file focused; `store.py` ≤ ~250 lines as facade.
- **No** `store.json` read/write for agents/sessions/messages after cutover.
- **No** migrate-from-JSON; empty DB → `seed_if_empty`.
- Busy: process-local in-memory (`app/models/mixins/busy.py`), not SQLite.
- Keep **all existing public Store method names** used by API/UI (grep `store.` in `app/`).
- Hybrid: SQLite for agents, sessions, session_agents, messages, settings; `platform_data` for tools/skills/plugins/workplaces/schedules/models/providers/safety/users/shared_channels/eval_*.
- Use `TOMO_DB_PATH` / temp DB in tests via monkeypatch — reset/rebind store carefully so tests don't hit production `var/tomo.db`.

## Implement

1. `app/models/seed.py` — move demo agents/sessions seed from current store; `seed_if_empty(conn)`.
2. Mixins: `agents.py`, `sessions.py`, `messages.py` (new), `settings.py`, `busy.py`.
3. Rewrite `app/services/store.py` as thin facade: migrate+seed on init; delegate CRUD; platform methods call `platform_data`.
4. Tests:
   - `tests/unit/models/test_agents.py`
   - `tests/unit/models/test_sessions_messages.py`
   - `tests/integration/test_store_sqlite.py` (create session, append user+final, list history, list agents)
5. Ensure existing Task 1 schema tests still pass.
6. Commit: `feat: SQLite-backed store facade for agents and sessions`

## Verify

```bash
uv run pytest tests/unit/models/ tests/integration/test_store_sqlite.py -v
```

Expected: all PASS.

Optional smoke: import store and `list_agents()` returns seeded agents against a temp DB.

## Reference

- Schema: `app/models/schema.py`
- Connection: `app/models/db.py`
- Design: `docs/superpowers/specs/2026-07-26-foundation-thin-vertical-design.md` §4–5
- Current API surface: `app/services/store.py` (replace body, keep method names)
