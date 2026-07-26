# Cline Brief — Alpha Slice A: Agent Identity + Multi-Model

**Repo:** `/home/dev-serv/Project/py-proj/tomo`  
**Master spec:** `docs/superpowers/specs/2026-07-26-alpha-kitchen-sink-design.md` — **§2.2 + Slice A only**  
**Plan:** `docs/superpowers/plans/2026-07-26-alpha-slice-a-identity-models.md` — tasks 1–4 (TDD)  
**Do not** implement Slices B–H.

## Goal

Ship **Name + Role** on agents, **LLM profiles** (CRUD + default + per-agent assign), runtime `get_llm(agent_id=…)`, System → Models multi-profile UI, and honest coordinator-only chat copy until Slice B.

## Requirements

1. Read and follow the plan tasks 1–4 exactly (failing tests first where specified).
2. `llm_profiles` table; encrypt `api_key` with Slice 0 `secrets.py`; masked public GET; blank PUT keeps key.
3. Settings `default_model_id`; agents `role`; `model_id` = profile id or `""` (use default).
4. `get_llm(agent_id=None)` resolves agent profile → default → first enabled → else `LLMConfigError`. Wire loop + session title.
5. System → Models: list/add/edit/set-default profiles (not the old single global form as the runtime source).
6. Setup creates first default profile from the wizard fields.
7. Agent create + Config: Name, Role, model dropdown; save persists.
8. Harden dashboard/sessions copy: no false “routes / handoff” claims.
9. Do **not** start or restart the Tomo server.
10. Keep files modular (~150–250 lines; smell at 400+).
11. When tests pass, mark Slice A done in `docs/superpowers/progress/alpha.md`, then commit:

```bash
git add -A && git commit -m "$(cat <<'EOF'
feat: agent roles and multi-model LLM profiles

EOF
)"
```

## Verify

```bash
uv run pytest tests/unit/models/test_llm_profiles.py tests/unit/runtime/llm/ -q
uv run pytest tests/unit/models/ tests/unit/runtime/llm/ tests/unit/runtime/agent/ -q
```

Expected: PASS.

## Done when

- [ ] Plan tasks 1–4 complete  
- [ ] ≥2 profiles + default configurable; agents can use different profiles  
- [ ] Role + model assignment persist in UI  
- [ ] Chat home copy honest (no fake routing)  
- [ ] Progress Slice A marked done  
- [ ] Commit created  
