# Cline Brief — Alpha Slice E: Memory / KB

**Repo:** `/home/dev-serv/Project/py-proj/tomo`  
**Plan:** `docs/superpowers/plans/2026-07-26-alpha-slice-e-memory.md`  
**Slice E only.** No F–H. Do not start the server.

## Goal
SQLite knowledge entries + `recall` tool + minimal CRUD UI + seed.

## Requirements
1. Follow plan. Keyword search OK (no vector DB).
2. Agent can recall seeded fact in tests.
3. Mark progress E done; commit `feat: knowledge base with recall tool`.

## Verify
```bash
uv run pytest tests/unit/models/ tests/unit/runtime/tools/ -q
```
