# Cline Brief — Foundation fix pass (Task 1–2 debt)

**Repo:** `/home/dev-serv/Project/py-proj/tomo`  
**Scope:** Fix adversarial review findings from Task 1–2 only.  
**Do not** implement Task 3+ (no LLM clients, calculator, agent loop, chat rewrite).

## Must fix (P1 + P2)

### P1 — `seed_if_empty` FK crash on reopen
**File:** `app/models/seed.py`

**Bug:** If `agents` is non-empty but does not contain demo ids (`main`/`ops`/`research`), and `sessions` is empty, `_seed_sessions` inserts rows that FK-fail → `Store._open` / `rebind` / process startup crash.

**Repro:** delete all seeded agents → `create_agent({id: custom})` → `rebind` same DB → `IntegrityError FOREIGN KEY`.

**Fix:** Only seed demo sessions when required agent ids exist (e.g. all of `main`, `ops`, `research`), otherwise skip session seed. Prefer wrapping seed in try/rollback so a failed seed never leaves the connection/transaction dirty. Agents+settings seeding stays as-is when their tables are empty.

**Test:** `tests/unit/models/test_seed.py` (or extend existing) — custom-only agents + empty sessions + `seed_if_empty` / `rebind` must succeed (no demo sessions, no crash).

### P2 — dashboard `recent_agents` sort regression
**File:** `app/services/store.py` (`dashboard_data`)

**Bug:** Old store sorted agents by `created_at DESC` then `[:5]`. New code uses `list_agents()` order (`is_super DESC, created_at ASC`) → wrong “recent” panel.

**Fix:** In `dashboard_data`, sort agents by `created_at` descending before slicing `[:5]`. Keep `list_agents()` order unchanged for the agents list API.

**Test:** assert newest `created_at` appears first in `dashboard_data()["recent_agents"]`.

### P2 — stats / dashboard not atomic
**File:** `app/services/store.py`

**Bug:** `stats` / `dashboard_data` call `list_*` under separate lock acquisitions → torn snapshot under concurrency.

**Fix:** Hold `self._lock` for the entire stats and dashboard snapshot (read agents/sessions once inside one `with self._lock:`). Avoid nested lock deadlocks if helpers also take the lock — either call mixin functions directly under the outer lock, or use a private unlocked helper.

### P2 — `get_or_create_session` missing agent validation
**File:** `app/models/mixins/sessions.py`

**Bug:** Unlike `create_swarm_session`, missing `agent_id` → raw `IntegrityError`.

**Fix:** If agent does not exist, raise `ValueError` (same spirit as swarm create). Add a unit test.

## Should fix (P3, small)

### P3 — `clear_session(agent, user)` must not create a session
**File:** `app/services/store.py` (+ sessions mixin helper if needed)

**Bug:** Always `get_or_create_session` then clear → invents empty session when none existed.

**Fix:** Look up existing single-agent session for `(agent_id, user_id)`; if none, no-op. If present, clear messages only. Add test.

### Optional hygiene (if cheap)
- Break `seed` → `platform_data` circular import risk: have `seed_settings` defaults live where seed can import without pulling `app.services` package `__init__` (e.g. import `seed_settings` from a leaf module, or lazy-import inside the function). Only if straightforward.
- Do **not** change busy-as-in-memory design; no need to re-seed `ops` busy unless trivial.

## Verify

```bash
uv run pytest tests/unit/models/ tests/integration/test_store_sqlite.py -v
```

All must PASS, including new regression tests for P1/P2/P3.

## Commit

```bash
git commit -m "fix: harden seed and store snapshot contracts"
```

Leave `docs/superpowers/progress/foundation.md` for Cursor.
