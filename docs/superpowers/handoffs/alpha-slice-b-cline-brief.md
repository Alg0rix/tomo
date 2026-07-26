# Cline Brief — Alpha Slice B: Swarm Delegation

**Repo:** `/home/dev-serv/Project/py-proj/tomo`  
**Master spec:** `docs/superpowers/specs/2026-07-26-alpha-kitchen-sink-design.md` — **Slice B only**  
**Plan:** `docs/superpowers/plans/2026-07-26-alpha-slice-b-delegation.md` — tasks 1–3  
**Do not** implement Slices C–H.

## Goal

Real mid-turn handoff: `delegate` tool + `@mention` → SSE `delegate` → member agent `run_turn` → transcript with correct `agent_id`. Non-members rejected.

## Requirements

1. Follow the plan (TDD where specified).
2. Implement `coordinator/router.py` membership-safe target resolution.
3. Add `app/tools/delegate.json` + `app/runtime/tools/delegate.py`; register backend.
4. Wire loop and/or `web.py`: emit `delegate` then nested member turn; persist with correct agent ids.
5. `@ops …` (or agent name/id) forces handoff when member of session.
6. Restore chat copy that routing/handoff now works.
7. Do **not** start/restart the Tomo server.
8. Modular files (~150–250 lines; smell at 400+).
9. Mark Slice B done in `docs/superpowers/progress/alpha.md`, then commit:

```bash
git add -A && git commit -m "$(cat <<'EOF'
feat: swarm mid-turn delegation via delegate tool and @mention

EOF
)"
```

## Verify

```bash
uv run pytest tests/unit/runtime/coordinator/ tests/unit/runtime/agent/test_loop.py tests/unit/runtime/tools/ -q
uv run pytest tests/unit/runtime/ tests/integration/test_chat_mock.py -q
```

Expected: PASS.

## Done when

- [ ] Delegate tool + router work  
- [ ] @mention force-handoff works for session members  
- [ ] SSE `delegate` + member finals in transcript  
- [ ] Progress Slice B marked done  
- [ ] Commit created  
