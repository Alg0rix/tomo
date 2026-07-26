# Cline Brief — Foundation Task 7 (docs closeout)

**Repo:** `/home/dev-serv/Project/py-proj/tomo`  
**Plan:** `docs/superpowers/plans/2026-07-26-foundation-thin-vertical.md` — **Task 7 only**  
**No production code** unless a one-line pointer in architecture is needed.

## Goal

Mark the foundation thin vertical complete in docs. Commit.

## Implement

1. **`docs/superpowers/progress/foundation.md`**
   - Status: Task 1–6 (+ fix passes) done; foundation vertical complete.
   - Task reviews table: include commits through Task 6 fix (`cb4b804` latest fix; feat commits as already logged).
   - Note autonomous Review→Adversarial→Fix→Next loop.
   - Do not invent commits — use `git log --oneline` for accuracy.

2. **`README.md`**
   - Getting started: run with mock (default) vs `TOMO_LLM_PROVIDER=openai_compat` + `TOMO_LLM_API_KEY` / `TOMO_LLM_BASE_URL` / `TOMO_LLM_MODEL`.
   - Mention `TOMO_DB_PATH` / `var/tomo.db`.
   - Short note that foundation thin vertical is live: SQLite store + mock/openai LLM + calculator + agent loop + web SSE.

3. **`docs/architecture.md`**
   - Point at the thin vertical (SQLite → LLM → calculator → loop → web SSE); link to design spec / plan under `docs/superpowers/` if sensible.

## Verify

```bash
# docs only — no new tests required
git diff --stat
```

Skim that README run instructions match `app/core/config.py` env names.

## Commit

```bash
git commit -m "docs: mark foundation thin vertical complete"
```
