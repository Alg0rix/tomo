# Cline Brief — Alpha Slice D: Workplaces

**Repo:** `/home/dev-serv/Project/py-proj/tomo`  
**Plan:** `docs/superpowers/plans/2026-07-26-alpha-slice-d-workplaces.md`  
**Slice D only.** No E–H. Do not start the server.

## Goal
SQLite workplaces (local + SSH); Connect/test; assign to agents; bash/file tools use workplace root.

## Requirements
1. Follow plan. Encrypt SSH secrets. Tunnel type = honest “later” label only.
2. Tests with local temp dirs + mocked SSH.
3. Mark progress D done; commit:

```bash
git add -A && git commit -m "$(cat <<'EOF'
feat: local and SSH workplaces with Connect

EOF
)"
```

## Verify
```bash
uv run pytest tests/unit/models/ tests/unit/runtime/tools/ -q
```
