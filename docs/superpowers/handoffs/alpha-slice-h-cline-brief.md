# Cline Brief — Alpha Slice H: Polish

**Repo:** `/home/dev-serv/Project/py-proj/tomo`  
**Plan:** `docs/superpowers/plans/2026-07-26-alpha-slice-h-polish.md`  
**Slice H only.** Do not start the server. No new major features.

## Goal
Nav/honesty audit, empty states, README Alpha section, progress closeout that Alpha kitchen-sink is complete.

## Requirements
1. Follow plan. Eval stays gated.
2. Mark all slices / H done in progress; commit `docs: Alpha polish and kitchen-sink closeout`.

## Verify
```bash
uv run pytest -q --tb=no
```
Expected: PASS (no regressions).
