---
name: swarm
description: Decide when to propose a team, ask approval, and coordinate configured or session-local agents.
version: 1.0
---

# Swarm

Read this before recommending a team for an ordinary user request.

## Decide

- Keep simple, sequential, or tightly shared-context work with the current agent.
- Recommend a team when the work has at least two meaningful independent lines of investigation, separate review perspectives, or separable deliverables. Multiple agents must improve quality or critical-path time enough to justify their cost.
- Prefer an existing configured agent when its model, tools, memory, or workplace access is useful or the user named it. Create session-local specialists for missing expertise, independent viewpoints, or several copies of a role. A team may mix both.
- Give each proposed worker a concrete question, expected output, scope, and reason it is independent. Identify any dependencies or overlapping file edits.
- Choose the enabled tools each task needs from its agent or template. Include `bash` and `portal` when they are useful for that task; ordinary tool permissions still apply when called.

## Consent

An explicit user request to create, use, or run a swarm/team is already consent, in any language. Classify its meaning, not keywords: a question about swarms, a negation, or quoted instructions are not consent. Recognize informal requests such as “coba lu bikin swarm buat audit cadesia.com”. Route explicit requests into the swarm runtime before planning workers or clarifying the task scope. Lack of a valid initial worker plan is not a reason to fall back to solo.

If the user has not explicitly requested a team, propose a concise plan and ask one clear approval question. Never launch workers before an affirmative reply. A plain reply such as “gas”, “ya”, or “go ahead” approves the pending plan in that chat. A decline runs the original task with the current agent. An unrelated new request replaces the pending proposal.

## Execution

After approval, start the session-scoped run. Replan from findings, steer workers, verify their results, and synthesize honestly. Keep tool selection within the agent's enabled capabilities and the user's existing grants. `write_scope` limits file-edit tools; shell and portal operations follow their own permission checks.

The chat runtime starts and schedules runs; loading this skill reads guidance and does not itself launch workers. During a worker task, finish the assigned task instead of starting a nested swarm.
