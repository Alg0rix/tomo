# Cline Brief — Alpha Slice C: More Tools

**Repo:** `/home/dev-serv/Project/py-proj/tomo`  
**Plan:** `docs/superpowers/plans/2026-07-26-alpha-slice-c-tools.md`  
**Spec:** master Alpha Slice C only. **Do not** implement D–H. **Do not** start the server.

## Goal
Add `bash`, `read_file`, `write_file` (or `str_replace`) with cwd jail + timeouts; registry-backed System → Tools; persist agent tool enablement.

## Requirements
1. Follow the plan (TDD). Keep calculator + delegate green.
2. Default sandbox cwd under `$TOMO_HOME/agents/<id>/work` or documented `var/workspaces` — no path escape.
3. Tool errors return strings, never raise to the loop.
4. Modular files; mark progress Slice C done; commit:

```bash
git add -A && git commit -m "$(cat <<'EOF'
feat: add bash and file tools with cwd jail

EOF
)"
```

## Verify
```bash
uv run pytest tests/unit/runtime/tools/ tests/unit/runtime/agent/test_loop.py -q
```
Expected: PASS.
