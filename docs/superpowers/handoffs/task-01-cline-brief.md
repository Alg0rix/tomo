# Cline Brief — Foundation Task 1

**Repo:** `/home/dev-serv/Project/py-proj/tomo`  
**Plan:** `docs/superpowers/plans/2026-07-26-foundation-thin-vertical.md` — Task 1 only  
**Do not** implement Tasks 2–7.

## Goal

Add DB/LLM config, `httpx` + pytest deps, SQLite `migrate()` creating foundation tables, and a unit test.

## Requirements

1. `pyproject.toml`: add `httpx>=0.27`; add `[dependency-groups] dev = ["pytest>=8", "pytest-asyncio>=0.24"]`. Run `uv sync --group dev`.
2. `app/core/config.py`: add `VAR_DIR`, `DB_PATH`, `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_MAX_TOOL_ITERATIONS` as specified in the plan.
3. `app/models/db.py`: `get_connection()` — create parent dirs, `sqlite3.Row`, `PRAGMA foreign_keys=ON`.
4. `app/models/schema.py`: `migrate(conn)` creating tables: `agents`, `sessions`, `session_agents`, `messages`, `settings` (columns per design spec).
5. `tests/unit/models/test_schema.py`: assert those tables exist after migrate.
6. Keep files small; do not rewrite `store.py` yet.
7. Commit when tests pass: `feat: add SQLite schema and LLM/DB config`

## Verify

```bash
uv sync --group dev
uv run pytest tests/unit/models/test_schema.py -v
```

Expected: PASS.
