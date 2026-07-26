# Cline Brief — Alpha Slice G: Platform SQLite + Scheduler

**Repo:** `/home/dev-serv/Project/py-proj/tomo`  
**Plan:** `docs/superpowers/plans/2026-07-26-alpha-slice-g-platform-scheduler.md`  
**Slice G only.** No H except progress. Do not start the server. Eval stays gated.

## Goal
Platform entities in SQLite; working scheduler that triggers a turn; no fake seed wipe on restart.

## Requirements
1. Follow plan. Keep store facade stable. Scheduler must actually fire (interval OK).
2. Mark progress G done; commit `feat: platform entities in SQLite with scheduler`.

## Verify
```bash
uv run pytest tests/unit/models/ tests/unit/ -q
```
