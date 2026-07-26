# Cline Brief — Alpha Slice 0: Tomo Home

**Repo:** `/home/dev-serv/Project/py-proj/tomo`  
**Master spec:** `docs/superpowers/specs/2026-07-26-alpha-kitchen-sink-design.md` — **§2.1 + Slice 0 only**  
**Plan:** `docs/superpowers/plans/2026-07-26-alpha-slice-0-tomo-home.md` — follow tasks 1–3 (TDD)  
**Do not** implement Slices A–H (no multi-model profiles, no Name/Role UI, no swarm).

## Goal

Ship `$TOMO_HOME` (default `~/.tomo`) with the locked tree, bootstrap from `defaults/`, encrypt UI secrets at rest (`.secret_key` / `TOMO_SECRET_KEY`), and load coordinator/agent system prompts from `SOUL.md` + `SYSTEM.md`. Optional hidden `.env` for bootstrap only — **never** `secrets.env`. **Never store API keys as plaintext in SQLite.**

## Requirements

1. Read and follow the plan file above, tasks 1–3 exactly (write failing tests first where the plan says so).
2. Layout must match §2.1: `tomo.yaml`, `SOUL.md`, `.secret_key`, `library/{skills,memory}`, `agents/<id>/{SYSTEM.md,SOUL.md,knowledge,work}`, `workplaces/`, `state/tomo.db`.
3. Allowed names: `SOUL.md`, `SYSTEM.md`, `.env`, `.secret_key`. Do not invent `secrets.env` / `identity.md` / `prompt.md` / `kb/`.
4. Master key: `TOMO_SECRET_KEY` env **or** auto-create `$TOMO_HOME/.secret_key` (chmod 600). Never put it in `tomo.yaml`. Never log plaintext secrets. SQLite secret writes are always ciphertext (Fernet/`cryptography` OK).
5. Encrypt `llm_api_key` on settings write; decrypt for runtime `get_settings`; keep masked GET / blank PUT keep.
6. `tests/conftest.py` must set `TOMO_HOME` to a temp dir before app imports.
7. Do **not** start or restart the Tomo server.
8. Keep files modular (~150–250 lines; smell at 400+).
9. When tests pass, update `docs/superpowers/progress/alpha.md` Slice 0 → done, then commit:

```bash
git add -A && git commit -m "$(cat <<'EOF'
feat: add Tomo Home ($TOMO_HOME) with SOUL/SYSTEM prompt loading

EOF
)"
```

## Verify

```bash
uv run pytest tests/unit/core/test_home.py tests/unit/runtime/agent/test_context_home.py tests/unit/runtime/agent/test_loop.py -q
uv run pytest tests/unit/core/ tests/unit/runtime/agent/ -q
```

Expected: PASS.

## Done when

- [ ] Plan tasks 1–3 complete  
- [ ] README documents Tomo Home + secrets / `.secret_key` policy  
- [ ] SQLite `llm_api_key` is ciphertext after save; runtime still works  
- [ ] `docs/superpowers/progress/alpha.md` Slice 0 marked done  
- [ ] Commit created  
