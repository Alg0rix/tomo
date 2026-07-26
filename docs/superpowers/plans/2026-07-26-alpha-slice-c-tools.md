# Alpha Slice C — More Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Ship `bash`, `read_file`, `write_file` (or `str_replace`) tools with cwd jail + timeouts; System → Tools from registry; agent Tools panel persists enable/disable.

**Architecture:** JSON defs in `app/tools/`; backends in `app/runtime/tools/`; registry `_BACKENDS`. Default cwd = agent `work/` under `$TOMO_HOME` or `var/workspaces/<id>` until Slice D. No arbitrary `eval`.

**Master spec:** Slice C. **Do not** start server. **Do not** implement D–H (workplace SQLite can wait; local sandbox OK).

## Tasks

### Task 1: bash + read_file + write_file backends
- Create tool JSON + Python backends; register; timeouts; reject `..` path escape; relative paths only under root
- Tests: each tool success + jail + timeout/error strings (never raise to caller)
- [ ] TDD → implement → pytest PASS

### Task 2: System Tools list + agent enablement
- System → Tools sourced from registry (not fake platform_data only)
- Persist per-agent enabled tools (SQLite table `agent_tools` or settings JSON — document choice)
- Agent Tools panel: real save (remove “Not wired yet” for Tools only)
- [ ] Tests + UI wiring

### Task 3: Progress + commit
```bash
git add -A && git commit -m "$(cat <<'EOF'
feat: add bash and file tools with cwd jail

EOF
)"
```
Mark Slice C done in `docs/superpowers/progress/alpha.md`.

## Out of scope
Workplaces SQLite/SSH (D), memory (E), Telegram (F). `delegate` already from B — do not regress.
