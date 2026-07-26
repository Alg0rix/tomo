# Alpha Slice G — Platform → SQLite + Scheduler Plan

> Agentic workers: subagent-driven-development or executing-plans.

**Goal:** Move workplaces/schedules/skills/plugins/agent-tool links fully to SQLite; scheduler UI creates interval/cron that fires a session turn; remove fake platform_data catalogs for those domains.

**Do not** start server. No H polish beyond what’s needed. Eval stays gated.

## Tasks
1. Tables + mixins for schedules (+ run log), skills metadata, plugins, any remaining platform_data entities
2. Store facade keeps call-site names; seed once into SQLite
3. Scheduler runner (in-process interval OK for Alpha) + UI create/list/enable
4. Tests: schedule fires turn (MockLLM); data survives rebind
5. Progress + commit `feat: platform entities in SQLite with scheduler`

## Out of scope
Eval UI, tunnel connector product.
