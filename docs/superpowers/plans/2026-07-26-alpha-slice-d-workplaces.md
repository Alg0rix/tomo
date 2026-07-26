# Alpha Slice D — Workplaces Implementation Plan

> **For agentic workers:** Use subagent-driven-development or executing-plans. Checkbox steps.

**Goal:** Local + SSH workplaces in SQLite; Connect works; tools use agent workplace cwd/host.

**Architecture:** `workplaces` table; backends `local` + `ssh`; tunnel type allowed but labeled “connector later” (not fake-connected). Secrets (SSH password/key) encrypted via Slice 0 secrets helpers.

**Do not** start server. **Do not** implement E–H.

## Tasks
1. Schema `workplaces` + mixin CRUD + Connect/test (local path exists; SSH mocked in tests)
2. Assign workplace to agent; tool backends resolve root from assignment (fallback: agent `work/` dir)
3. UI: list/create/edit/Connect; honest tunnel label
4. Tests + progress + commit `feat: local and SSH workplaces with Connect`

## Out of scope
Telegram, memory, scheduler runner (G), tunnel product.
